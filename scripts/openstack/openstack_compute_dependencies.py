"""Compute-only native Kolla package/SDK and public API-CA preparation."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess

from openstack_compute_image import read
from openstack_container_engine import digest,file_hash,write
import openstack_kolla_packages as packages


def ca(configdir,deployment_id):
    filename=Path(configdir)/'certificates/ca/ca.crt'
    pem=read(filename).decode('ascii')
    match=re.fullmatch(r'-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\s]+)\s+-----END CERTIFICATE-----\s*',pem)
    if not match:raise ValueError('exactly one public API CA certificate required; no private key')
    der=base64.b64decode(re.sub(r'\s+','',match[1]),validate=True)
    p=subprocess.run(['openssl','x509','-noout','-ext','basicConstraints'],input=pem,text=True,capture_output=True,timeout=30)
    if p.returncode or 'CA:TRUE' not in p.stdout:raise ValueError('API certificate must be a CA')
    return {'pem':pem,'sha256':__import__('hashlib').sha256(pem.encode()).hexdigest(),
            'der_sha256':__import__('hashlib').sha256(der).hexdigest(),
            'destination':'/usr/local/share/ca-certificates/spvm-openstack-'+digest(deployment_id)[:16]+'.crt'}


def role(receipt):
    path=Path(receipt['collections'])/'ansible_collections/openstack/kolla/roles/packages'
    for name,expected in packages.REVIEWED_ROLE.items():
        if file_hash(path/name)!=expected:raise ValueError('unreviewed native Kolla packages role')


def observe(runner,name,public_ca):
    data={k:v for k,v in public_ca.items() if k!='pem'}
    source='CA='+repr(data)+'\nPACKAGES='+repr(packages.PACKAGES+packages.SDK_PACKAGES+['ca-certificates'])+'\n'+'''
import json,subprocess,hashlib,sys,os
from pathlib import Path
result={'packages':{},'sdk':False,'ca':False}
for package in PACKAGES:
 p=subprocess.run(['dpkg-query','-W','-f=${db:Status-Status}',package],capture_output=True,text=True,timeout=30)
 if p.returncode not in (0,1):raise ValueError('package observation failed')
 result['packages'][package]=p.returncode==0 and p.stdout=='installed'
try:
 import docker,dbus,requests
 result['sdk']=True
except ImportError:pass
anchor=Path(CA['destination'])
for parent in (anchor,*anchor.parents):
 if parent.exists() or parent.is_symlink():
  s=parent.lstat()
  if parent.is_symlink() or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('untrusted CA path')
if anchor.exists():
 if not anchor.is_file() or anchor.stat().st_size>65536 or hashlib.sha256(anchor.read_bytes()).hexdigest()!=CA['sha256']:
  raise ValueError('conflicting existing CA anchor')
 p=subprocess.run(['openssl','verify','-CAfile','/etc/ssl/certs/ca-certificates.crt',str(anchor)],capture_output=True,text=True,timeout=30)
 result['ca']=p.returncode==0
print(json.dumps(result))
'''
    return runner.remote(name,source)


def prepare(value,module,receipt,public_ca,output,runner):
    role(receipt)
    host=module.deployment_config(value['config'])['hosts'][value['host']]
    alias=host.get('inventory_name',value['host']);output.mkdir(mode=0o700,exist_ok=False)
    # Both one-host inventory and --limit are used; no all-cloud packages phase.
    import shlex
    common='-F /dev/null -o IdentityAgent=none -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o UserKnownHostsFile='+shlex.quote(value['known_hosts'])
    inventory={'all':{'hosts':{alias:{'ansible_host':host['address'],'ansible_user':host['user'],
        'ansible_ssh_private_key_file':host['identity_file'],'ansible_python_interpreter':'/usr/bin/python3',
        'ansible_ssh_common_args':common}}}}
    write(output/'inventory.json',inventory)
    plays=packages.playbook('base-packages')+packages.playbook('host-sdk')+packages.playbook('api-trust',public_ca)
    # The trust playbook normally receives this fact from the fresh-host probe.
    # Here the one-host read-only observation supplies it explicitly.
    observed=observe(runner,value['host'],public_ca)
    if all(observed['packages'].values()) and not observed['sdk']:
        raise ValueError('installed SDK is unusable; do not repair it implicitly')
    plays[-1]['vars']={'spvm_api_ca_trusted':observed['ca']}
    write(output/'playbook.json',plays)
    (output/'ansible.cfg').write_text('[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n')
    env={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','OS_','PYTHON'))}
    env.update(ANSIBLE_CONFIG=str(output/'ansible.cfg'),ANSIBLE_COLLECTIONS_PATH=receipt['collections'],
        PYTHONDONTWRITEBYTECODE='1',PATH=str(Path(receipt['kolla_python']).parent)+':/usr/bin:/bin')
    argv=[str(Path(receipt['kolla_python']).parent/'ansible-playbook'),'-i',str(output/'inventory.json'),
          str(output/'playbook.json'),'--limit',alias]
    with (output/'private-prepare.log').open('xb') as log:
        os.chmod(log.name,0o600)
        p=subprocess.run(argv,env=env,stdout=log,stderr=log,timeout=1800,umask=0o077)
    if p.returncode:raise ValueError('compute dependency preparation failed; inspect private log')
    after=observe(runner,value['host'],public_ca)
    if not all(after['packages'].values()) or not after['sdk'] or not after['ca']:
        raise ValueError('compute dependencies or public CA not ready')
    write(output/'result.json',{'status':'COMPUTE_DEPENDENCIES_READY'})
    return after
