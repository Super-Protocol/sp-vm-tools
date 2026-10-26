"""Explicit compute-only Kolla deploy into an existing cloud.

Hardware, trusted management transport and matching image/helper inputs remain
owned prerequisite phases. Never invoke a control-plane deploy or post-deploy.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import yaml
from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write, collection_hash
import openstack_kolla_workspace as workspace


def specification(filename):
    value = json.loads(read(filename))
    if (type(value) is not dict or set(value)-{'preparation'} != {'schema_version','configdir','allow_experimental_ubuntu26'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or type(value['allow_experimental_ubuntu26']) is not bool):
        raise ValueError('exact new-compute preparation request required')
    root = Path(value['configdir'])
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError('existing protected Kolla config directory required')
    for parent in (root,*root.parents):
        if parent.is_symlink() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o022:
            raise ValueError('untrusted existing Kolla config directory')
    if not (root/'passwords.yml').is_file():
        raise ValueError('existing passwords required; never generate new credentials')
    if 'preparation' in value:
        import openstack_compute_onboard
        openstack_compute_onboard.specification(value['preparation'])
    return value


def config_hashes(filename):
    root = Path(specification(filename)['configdir'])
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise ValueError('symlink in existing service configuration')
        if path.is_file():
            if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
                raise ValueError('untrusted service configuration')
            files[str(path.relative_to(root))] = file_hash(path)
    return files


def inputs(value, module):
    cfg = module.deployment_config(value['config'])
    name = value['host'];host = cfg['hosts'].get(name)
    if not host or host['roles'] != ['compute'] or name == value['controller']:
        raise ValueError('one compute-only target required')
    alias = host.get('inventory_name',name)
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',alias) or alias in ('all','compute','control','network','ungrouped'):
        raise ValueError('one literal inventory host required')
    receipt = workspace.check(value['workspace'])
    if receipt['config_sha256'] != digest(cfg):raise ValueError('workspace/config mismatch')
    workspace.pinned(receipt['kolla_source'],cfg['kolla']['revision'])
    collection_hash(receipt['collections'],receipt['collections_sha256'])
    if file_hash(receipt['kolla_python']) != receipt['kolla_python_sha256']:
        raise ValueError('Kolla interpreter changed')
    if not value.get('enrollment_request'):raise ValueError('explicit initial enrollment required')
    enrollment = json.loads(read(value['enrollment_request']))
    binding = json.loads(read(Path(value['workspace'])/'compute-binding.json'))
    if (enrollment['helper_request']['machine_sha256'] != binding['machines'][name]
            or enrollment['image_id'] != cfg['kolla']['compute_image_id']
            or enrollment['bootstrap_runtime'] != binding['bootstrap_runtime']):
        raise ValueError('enrollment/image/workspace mismatch')
    spec = specification(value['new_compute'])
    globals_ = yaml.safe_load(read(Path(spec['configdir'])/'globals.yml'))
    profile = yaml.safe_load(read(Path(value['workspace'])/'host-profile.yml'))
    for config in (globals_,profile):
        workspace.runtime_mounts(config)
    if profile.get('enable_nova_libvirt_container') is not False:
        raise ValueError('external host libvirt is mandatory')
    code='''import json,sys
from ansible.inventory.data import InventoryData
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.inventory.ini import InventoryModule
v=json.load(sys.stdin);data=InventoryData();InventoryModule().parse(data,DataLoader(),v['inventory'])
h=data.hosts[v['alias']]
if v['alias'] in data.groups or h not in data.groups['compute'].get_hosts() or any(h in data.groups[g].get_hosts() for g in ('control','network')):
 raise ValueError('compute-only literal target required')
if h.get_vars().get('ansible_host')!=v['address'] or h.get_vars().get('ansible_user')!=v['user']:
 raise ValueError('inventory address/user changed')
'''
    subprocess.run([receipt['kolla_python'],'-c',code],input=json.dumps({'inventory':str(Path(value['workspace'])/'hosts.ini'),
        'alias':alias,'address':host['address'],'user':host['user']}),check=True,capture_output=True,text=True,timeout=30)
    return cfg,receipt,enrollment,spec,alias


HOST = r'''
import hashlib,json,subprocess,platform
from pathlib import Path
def run(args,allowed=(0,)):
 p=subprocess.run(args,capture_output=True,text=True,timeout=45)
 if p.returncode not in allowed or len(p.stdout)>4194304:raise ValueError('host observation failed')
 return p.stdout
if hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()!=WANT['helper_request']['machine_sha256']:
 raise ValueError('wrong target machine')
if run(['virsh','-c','qemu:///system','list','--all','--uuid']).strip():raise ValueError('target has domains')
if run(['pgrep','-f',r'^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$)'],(0,1)).strip():raise ValueError('direct guest present')
if Path('/var/lib/nova/compute_id').read_text().strip()!=WANT['helper_request']['compute_uuid']:
 raise ValueError('compute UUID differs')
runtime=json.loads(Path('/etc/spvm/runtime-services.json').read_text())
if runtime!=WANT['bootstrap_runtime']:raise ValueError('explicit deny-all bootstrap runtime required')
helper=json.loads(Path('/etc/spvm/host-helper.json').read_text())
if helper['storage']['compute_uuid']!=WANT['helper_request']['compute_uuid']:raise ValueError('helper UUID differs')
for name in ('network','providers','runtime','execution','clusters'):
 directory=Path('/var/lib/spvm')/name
 if directory.exists() and any(directory.iterdir()):
  raise ValueError('old SPVM bookkeeping/history requires explicit cleanup before new-compute preparation')
for pool in WANT['helper_request']['pools']:
 root=Path(pool['root'])
 if {p.name for p in root.iterdir()}- {'.spvm-state.lock','.spvm-claims.json'}:raise ValueError('state disks remain')
 claims=root/'.spvm-claims.json'
 if claims.exists() and json.loads(claims.read_text()):raise ValueError('state claims remain')
ids=run(['docker','ps','--all','--no-trunc','--format','{{.ID}}']).split()
containers=json.loads(run(['docker','inspect',*ids])) if ids else []
if any(c['Name']=='/nova_compute' for c in containers):raise ValueError('target already has nova_compute; use prepared-host path')
image=json.loads(run(['docker','image','inspect',IMAGE]))
if len(image)!=1 or image[0]['Id']!=WANT['image_id']:raise ValueError('preloaded pinned driver image required')
files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('/usr/bin/qemu-system-x86_64'),
 Path('/usr/sbin/libvirtd'),Path('/etc/libvirt/qemu.conf'),Path('/etc/libvirt/libvirtd.conf'),Path('/etc/docker/daemon.json'),
 Path('/etc/spvm/host-helper.json'),Path('/etc/spvm/runtime-services.json'),Path('/var/lib/nova/compute_id')] if p.is_file()}
print(json.dumps({'hostname':run(['hostname']).strip(),'files':files,'domains':[],'os':platform.freedesktop_os_release(),
 'containers':{c['Name']:{'id':c['Id'],'image':c['Image'],'started_at':c['State']['StartedAt']} for c in containers}}))
'''


def facts(runner, value, cfg, wanted):
    return runner.remote(value['host'],'WANT='+repr(wanted)+'\nIMAGE='+repr(cfg['kolla']['compute_image'])+'\n'+HOST)


def controller(runner):
    return runner.remote(runner.controller,r'''
import json,subprocess
p=subprocess.run(['docker','ps','--all','--no-trunc','--format','{{.ID}}'],check=True,capture_output=True,text=True)
items=json.loads(subprocess.run(['docker','inspect',*p.stdout.split()],check=True,capture_output=True,text=True).stdout)
print(json.dumps({c['Name']:{'id':c['Id'],'image':c['Image'],'running':c['State']['Running'],
 'started':c['State']['StartedAt'],'restarts':c['RestartCount']} for c in items}))
''')


def cloud(runner):
    return runner.remote(runner.controller,r'''
import json,openstack
c=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=30)
try:
 if 'admin' not in c.session.auth.get_access(c.session).role_names:raise ValueError('admin required')
 print(json.dumps({'services':[{'host':s.host,'id':s.id,'status':s.status,'state':s.state} for s in c.compute.services(binary='nova-compute')],
  'servers':[{'id':s.id,'host':s.compute_host,'status':s.status,'task':s.task_state} for s in c.compute.servers(all_projects=True,details=True)]}))
finally:c.close()
''',controller=True,auth=True)


def plan(value,path,module,output,runner):
    spec=specification(value['new_compute'])
    if 'preparation' in spec:
        import openstack_compute_onboard as onboard
        with onboard.hold_target(value,module):
            observed=onboard.observe(value,path,module,output,runner)
        result={'kind':'new-compute-with-preparation-v1','created_at':int(time.time()),
                'preparation':observed,'automatic_recovery':False}
        result['plan_sha256']=digest(result);write(output/'new-compute-plan.json',result)
        return {'kind':'new-compute','changes':True,'plan_sha256':result['plan_sha256']}
    return plan_prepared(value,path,module,output,runner)


def plan_prepared(value,path,module,output,runner):
    cfg,receipt,wanted,spec,alias=inputs(value,module)
    target=facts(runner,value,cfg,wanted);current=cloud(runner)
    os_release=target['os']
    if os_release.get('ID')!='ubuntu' or os_release.get('VERSION_ID') not in ('24.04','26.04'):
        raise ValueError('supported Ubuntu host required')
    if os_release['VERSION_ID']=='26.04' and not spec['allow_experimental_ubuntu26']:
        raise ValueError('experimental Ubuntu 26 requires explicit acknowledgement')
    if any(s['host']==target['hostname'] for s in current['services']):
        raise ValueError('target already registered; use existing prepared-host workflow')
    if any(s['host']==target['hostname'] or s['status']=='BUILD' or s['task'] for s in current['servers']):
        raise ValueError('assigned or inflight server requires review')
    result={'kind':'new-compute-deploy-v1','created_at':int(time.time()),'target':target,
        'controller':controller(runner),'cloud':current,'config_sha256':config_hashes(value['new_compute']),
        'alias':alias,'stages':['deploy','map','enroll'],'automatic_recovery':False}
    result['plan_sha256']=digest(result);write(output/'new-compute-plan.json',result)
    return {'kind':'new-compute','changes':True,'plan_sha256':result['plan_sha256']}


def deploy(value,receipt,spec,alias,output):
    # No post-deploy/password generation, bootstrap-servers or controller targets.
    # Hardware/Docker/helper preparation must already have passed their owned phases.
    root=output/'kolla-config'
    shutil.copytree(spec['configdir'],root)
    alias_dir=root/'config/nova'/alias;alias_dir.mkdir(parents=True,exist_ok=True)
    override=alias_dir/'nova-compute.conf'
    with override.open('a') as stream:stream.write('\n[DEFAULT]\nenable_new_services = false\n')
    profile=yaml.safe_load(read(Path(value['workspace'])/'host-profile.yml'))
    # Existing globals often contain an absolute path to the old custom config.
    # Extra vars must point at the isolated copy, or the disabled-service guard
    # would be ignored despite writing the correct per-host override file.
    profile['node_custom_config']=str(root/'config')
    profile['ansible_ssh_common_args']=('-F /dev/null -o IdentityAgent=none -o IdentitiesOnly=yes '
        '-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile='+value['known_hosts'])
    write(output/'profile.json',profile)
    workspace.private_file(output/'ansible.cfg','[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n')
    env={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','PYTHON','OS_'))}
    env.update(ANSIBLE_CONFIG=str(output/'ansible.cfg'),ANSIBLE_COLLECTIONS_PATH=receipt['collections'],
        KOLLA_ANSIBLE_DATA_FILES_PATH=receipt['kolla_source'],PYTHONDONTWRITEBYTECODE='1',
        PATH=str(Path(receipt['kolla_python']).parent)+':/usr/local/bin:/usr/bin:/bin')
    code='import sys;sys.path.insert(0,sys.argv.pop(1));from kolla_ansible.cmd.kolla_ansible import main;sys.exit(main())'
    argv=[receipt['kolla_python'],'-c',code,receipt['kolla_source'],'deploy','--configdir',str(root),
          '-i',str(Path(value['workspace'])/'hosts.ini'),'-e','@'+str(output/'profile.json'),'--limit',alias]
    with (output/'private-deploy.log').open('xb') as log:
        os.chmod(log.name,0o600)
        if subprocess.run(argv,cwd=output,env=env,stdout=log,stderr=log,timeout=7200,umask=0o077).returncode:
            raise ValueError('scoped Kolla deploy failed; inspect private log; do not replay')


def preregister(runner, hostname):
    # Service.create is executed by conductor in current Nova. A compute-local
    # enable_new_services setting alone cannot quarantine remote object creation.
    # Use the same versioned object API as Nova, with one explicit disabled host.
    script='HOST='+repr(hostname)+'\n'+'''from nova import config,context,objects,exception
config.parse_args(['spvm-new-compute','--config-file','/etc/nova/nova.conf'])
objects.register_all()
ctx=context.get_admin_context()
try:
 existing=objects.Service.get_by_host_and_binary(ctx,HOST,'nova-compute')
except exception.ServiceNotFound:
 existing=None
if existing is not None:raise ValueError('service already exists; registration is not replayable')
service=objects.Service(ctx,host=HOST,binary='nova-compute',topic='compute',report_count=0,
                        disabled=True,disabled_reason='SPVM initial enrollment pending')
service.create()
'''
    code='SCRIPT='+repr(script)+'\n'+'''import subprocess,json
p=subprocess.run(['docker','exec','nova_conductor','/var/lib/kolla/venv/bin/python','-c',SCRIPT],
 capture_output=True,text=True,timeout=90)
if p.returncode:raise ValueError('disabled Nova preregistration failed; inspect original transaction')
print(json.dumps({'registered_disabled':True}))
'''
    runner.remote(runner.controller,code)


def apply(value,path,module,output,runner):
    approved=json.loads(read(output/'new-compute-plan.json'))
    if approved.get('kind')=='new-compute-with-preparation-v1':
        import openstack_compute_onboard as onboard
        with onboard.hold_target(value,module):
            onboard.execute(value,path,module,output,runner,approved['preparation'])
            # Prepared-host checks can only run after the prerequisites exist.
            # Parent approval binds their code, policies and artifact inputs.
            child=output/'prepared-compute';child.mkdir(mode=0o700,exist_ok=False)
            plan_prepared(value,path,module,child,runner)
            return apply_prepared(value,path,module,child,runner)
    return apply_prepared(value,path,module,output,runner)


def apply_prepared(value,path,module,output,runner):
    cfg,receipt,wanted,spec,alias=inputs(value,module)
    approved=json.loads(read(output/'new-compute-plan.json'))
    journal=output/'new-compute-transaction.json'
    if journal.exists():raise ValueError('new-compute apply already attempted; explicit operator review required')
    if (config_hashes(value['new_compute'])!=approved['config_sha256'] or controller(runner)!=approved['controller']
            or facts(runner,value,cfg,wanted)!=approved['target'] or cloud(runner)!=approved['cloud']):
        raise ValueError('new-compute observations changed since approval')
    record={'status':'RUNNING','phase':'REGISTERING_DISABLED','plan_sha256':approved['plan_sha256']}
    write(journal,record)
    try:
        preregister(runner,approved['target']['hostname'])
        record['phase']='DEPLOYING';write(journal,record)
        deploy(value,receipt,spec,alias,output)
        if controller(runner)!=approved['controller']:raise ValueError('controller lifecycle drift')
        record['phase']='WAITING_DISABLED_SERVICE';write(journal,record)
        deadline=time.monotonic()+180
        while True:
            current=cloud(runner)
            services=[s for s in current['services'] if s['host']==approved['target']['hostname']]
            if len(services)==1 and services[0]['state']=='up':break
            if time.monotonic()>deadline:raise ValueError('new compute registration timed out')
            time.sleep(5)
        if services[0]['status']!='disabled':raise ValueError('new compute did not register disabled')
        if sorted((s for s in current['services'] if s['host']!=approved['target']['hostname']),key=lambda s:s['host'])!=sorted(approved['cloud']['services'],key=lambda s:s['host']):
            raise ValueError('unrelated compute registration changed before mapping')
        if current['servers']!=approved['cloud']['servers']:raise ValueError('cloud workloads changed before mapping')
        record['phase']='MAPPING';write(journal,record)
        runner.remote(runner.controller,"import subprocess,json\np=subprocess.run(['docker','exec','nova_conductor','nova-manage','cell_v2','discover_hosts','--by-service'],capture_output=True,text=True,timeout=120)\nif p.returncode:raise ValueError('cell mapping failed')\nprint(json.dumps({'mapped':True}))\n")
        record['phase']='ENROLLING';write(journal,record)
        from initial_compute import Backend
        from compute_transaction import execute
        enrolled=Backend(cfg,value['controller'],value['controller_python'],value['openrc'],
                         output/'enrollment-observations',enrollment=wanted)
        inner=enrolled.plan(value['host']);write(output/'enrollment-plan.json',inner)
        result=execute(inner,inner['plan_sha256'],output/'enrollment-transaction',enrolled)
        if result['status']!='COMPUTE_READY':raise ValueError('SPVM enrollment incomplete')
        if controller(runner)!=approved['controller']:raise ValueError('controller lifecycle drift')
        record.update(status='COMPLETE',phase='COMPUTE_READY',guests_restarted=False)
    except BaseException:
        record['status']='NEEDS_REVIEW';write(journal,record);raise
    write(journal,record)
    return {'status':'OPENSTACK_COMPUTE_READY','host':value['host'],'guests_restarted':False}
