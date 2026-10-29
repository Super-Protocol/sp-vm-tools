"""Approved initial-cloud Kolla prechecks, never bootstrap/deploy/reconfigure."""
import argparse
import configparser
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import traceback

import yaml
from openstack_compute_image import read
from openstack_container_engine import collection_hash, digest, file_hash, write
from openstack_host_helper import request, sources, source_manifest
from openstack_launcher import recipe
import openstack_kolla_workspace as workspace


def testing_images(args):
    value = getattr(args, 'use_testing_images', False)
    if type(value) is not bool:
        raise ValueError('testing-image acknowledgement must be explicit boolean')
    return value


def require_system_interpreter(inventory,cfg):
    expected={h.get('inventory_name',n) for n,h in cfg['hosts'].items()}
    found=set();section=None
    for line in inventory.splitlines():
        line=line.strip()
        if line.startswith('['):section=line;continue
        if not line or line.startswith(('#',';')):continue
        parts=shlex.split(line)
        if section=='[all]' and parts[0] in expected:
            declaration=[p for p in parts[1:] if p.startswith('ansible_python_interpreter=')]
            if declaration!=['ansible_python_interpreter=/usr/bin/python3'] or parts[0] in found:
                raise ValueError('one explicit system Python declaration required per target')
            found.add(parts[0])
    if found!=expected:raise ValueError('re-render inventory with explicit host system Python')


def inputs(args,backend_path,backend):
    cfg=backend.deployment_config(args.config)
    root=Path(args.workspace)
    receipt=workspace.check(root)
    binding=json.loads(read(root/'compute-binding.json'))
    body={k:v for k,v in binding.items() if k!='binding_sha256'}
    if (binding.get('status')!='COMPUTE_INTENT_BOUND_NOT_DEPLOYABLE'
            or binding.get('binding_sha256')!=digest(body) or receipt.get('compute_binding')!=digest(body)
            or receipt['config_sha256']!=digest(cfg) or binding['config_sha256']!=digest(cfg)
            or set(binding.get('machines',{}))!=set(cfg['hosts'])
            or len(set(binding['machines'].values()))!=len(cfg['hosts'])
            or any(not re.fullmatch('[a-f0-9]{64}',str(m)) for m in binding['machines'].values())):
        raise ValueError('bound compute workspace with every physical host identity required')
    trust=Path(args.known_hosts)
    if any(ord(c)<32 for c in str(trust)) or not read(trust):
        raise ValueError('explicit trusted SSH known-hosts file required')
    source=workspace.pinned(receipt['kolla_source'],receipt['kolla_revision'])
    collection_hash(receipt['collections'],receipt['collections_sha256'])
    if file_hash(receipt['kolla_python'])!=receipt['kolla_python_sha256']:
        raise ValueError('Kolla Python changed')
    profile=yaml.safe_load(read(root/'host-profile.yml'))
    globals_=yaml.safe_load(read(root/'kolla/globals.yml'))
    require_system_interpreter(read(root/'hosts.ini').decode(),cfg)
    if any(v.get('ansible_python_interpreter','/usr/bin/python3')!='/usr/bin/python3' for v in (profile,globals_)):
        raise ValueError('system Python must not be overridden by Kolla variables')
    if (profile.get('disable_firewall') is not False or profile.get('apparmor_remove_libvirt_profile') is not False
            or profile.get('ubuntu_pkg_removals')!=[] or profile.get('enable_nova_libvirt_container') is not False
            or globals_.get('enable_nova_libvirt_container') is not False
            or globals_.get('nova_compute_image_full')!=binding['image_reference']):
        raise ValueError('preserving measured runtime profile and bound image required')
    code=read(Path(__file__).with_name('openstack_initial_probe.py')).decode()
    source_hashes={p:hashlib.sha256(body).hexdigest() for p,(body,mode) in sources(backend_path).items()}
    launcher,_,_=recipe(backend_path)
    requests={}
    for name,host in cfg['hosts'].items():
        identity=host.get('identity_file')
        if not identity or not read(identity):raise ValueError('explicit readable SSH identity required per host')
        required={host['management_interface'],host['tunnel_interface']}
        if 'network' in host['roles']:
            required.add('spvm-ext0' if cfg['network']['mode']=='host-nat' else cfg['network']['external_interface'])
        wanted={'machine_sha256':binding['machines'][name],'roles':host['roles'],'interfaces':sorted(required),
                'allow_ubuntu26':args.allow_experimental_ubuntu26,
                'api_vip':cfg['network']['api_vip'] if 'control' in host['roles'] and globals_.get('enable_keepalived') is False else None}
        if 'compute' in host['roles']:
            member=binding['hosts'][name]
            if file_hash(member['request_file'])!=member['request_sha256']:raise ValueError('helper request drift')
            spec=request(member['request_file']);source_manifest(backend_path,spec,bootstrap=True)
            if spec['machine_sha256']!=wanted['machine_sha256']:raise ValueError('compute identity differs')
            nova=configparser.ConfigParser(interpolation=None)
            alias=host.get('inventory_name',name)
            nova.read_string(read(root/'kolla/config/nova'/alias/'nova-compute.conf').decode())
            devices=json.loads(nova.get('pci','device_spec'))
            if {d['address'] for d in devices}!=set(spec['gpus']):raise ValueError('Nova and helper GPU selections differ')
            wanted.update(helper_request=spec,bootstrap_runtime=binding['bootstrap_runtime'],
                gpu_devices=devices,
                reserved_cpus=host['compute_resources']['reserved_cpus'],
                reserved_memory_mb=host['compute_resources']['reserved_memory_mb'],
                host_source_sha256={**source_hashes,**{str(Path(spec['tools'])/'scripts'/n):h
                    for n,h in launcher['result_sha256'].items()}},launcher_sha256=launcher['result_sha256'],
                image_reference=binding['image_reference'],image_id=binding['image_id'],
                image_context_sha256=binding['image_context_sha256'])
        requests[name]=wanted
    fingerprint={'workspace_sha256':digest(receipt),'binding_sha256':binding['binding_sha256'],
        'use_testing_images':testing_images(args),
        'config_sha256':digest(cfg),'known_hosts_sha256':file_hash(trust),
        'identity_files_sha256':{n:file_hash(h['identity_file']) for n,h in cfg['hosts'].items()},
        'installer_sha256':{n:file_hash(Path(__file__).with_name(n)) for n in
            ('openstack_kolla_prechecks.py','openstack_initial_probe.py','openstack_kolla_workspace.py')},
        'requests':requests}
    return cfg,receipt,source,code,fingerprint


def observe(backend,host,code,wanted,known_hosts):
    argv=backend.ssh_arguments(host)
    argv[1:1]=['-F','/dev/null','-o','UserKnownHostsFile='+str(known_hosts),'-o','ForwardAgent=no']
    result=subprocess.run(argv+['sudo -n /usr/bin/python3 -I -'],
        input='REQUEST='+repr(wanted)+'\n'+code,text=True,capture_output=True,timeout=300,
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    if result.returncode or len(result.stdout)>4194304:raise ValueError('initial host probe failed; diagnostics suppressed')
    value=json.loads(result.stdout)
    if value.get('schema_version')!=1 or not isinstance(value.get('blockers'),list):
        raise ValueError('invalid host probe result')
    return value


def execute(args,backend_path,backend):
    started=time.time()
    cfg,receipt,source,code,intent=inputs(args,backend_path,backend)
    facts={n:observe(backend,h,code,intent['requests'][n],args.known_hosts) for n,h in cfg['hosts'].items()}
    blockers=[n+':'+b for n,f in facts.items() for b in f['blockers']]
    if time.time()-started>900:raise ValueError('host observation collection exceeded freshness window')
    plan={'schema_version':1,'kind':'initial-kolla-prechecks-v1','intent':intent,'hosts':facts,
          'status':'INITIAL_PRECHECKS_PLAN_BLOCKED' if blockers else 'INITIAL_PRECHECKS_PLAN_READY',
          'blockers':blockers,'kolla_action':'prechecks','deploy_authorized':False}
    output=Path(args.output)
    if (not output.is_absolute() or output.resolve()!=output or output.is_relative_to(Path(args.workspace))
            or Path(args.workspace).is_relative_to(output)):
        raise ValueError('absolute separate private transaction output required')
    if args.action=='plan-kolla-prechecks':
        output.mkdir(mode=0o700,exist_ok=False)
        plan['created_at']=int(time.time());plan['expires_at']=plan['created_at']+900
        plan['plan_sha256']=digest(plan);write(output/'prechecks-plan.json',plan)
        return plan
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:
        raise ValueError('private owned transaction required')
    previous=json.loads(read(output/'prechecks-plan.json'))
    body={k:v for k,v in previous.items() if k!='plan_sha256'}
    if (type(previous.get('created_at')) is not int or previous.get('expires_at')!=previous['created_at']+900
            or not 0<=time.time()-previous['created_at']<=900 or previous.get('plan_sha256')!=digest(body)
            or args.plan_sha256!=previous['plan_sha256'] or previous.get('blockers') or blockers):
        raise ValueError('fresh passing explicitly approved prechecks plan required')
    plan.update(created_at=previous['created_at'],expires_at=previous['expires_at'])
    if plan!=body:raise ValueError('host or input drift since prechecks approval')
    lock=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        journal=output/'prechecks-transaction.json'
        if journal.exists() or journal.is_symlink():raise ValueError('prechecks already attempted; review, do not replay blindly')
        record={'schema_version':1,'status':'RUNNING_INITIAL_KOLLA_PRECHECKS','plan_sha256':args.plan_sha256,
                'remote_tasks_executed':True,'deploy_executed':False,'guests_launched':False}
        write(journal,record)
        try:
            config=output/'ansible.cfg'
            workspace.private_file(config,'[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n'
                '[ssh_connection]\nusetty = False\nssh_args = -F /dev/null -o IdentityAgent=none -o IdentitiesOnly=yes '
                '-o StrictHostKeyChecking=yes -o UserKnownHostsFile='+shlex.quote(str(args.known_hosts))+'\n')
            env={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','PYTHON','OS_'))}
            env.update(PYTHONDONTWRITEBYTECODE='1',ANSIBLE_CONFIG=str(config),
                ANSIBLE_COLLECTIONS_PATH=receipt['collections'],KOLLA_ANSIBLE_DATA_FILES_PATH=str(source),
                PATH=str(Path(receipt['kolla_python']).parent)+':/usr/local/bin:/usr/bin:/bin')
            command='import sys;sys.path.insert(0,sys.argv.pop(1));from kolla_ansible.cmd.kolla_ansible import main;sys.exit(main())'
            argv=[receipt['kolla_python'],'-c',command,str(source),'prechecks','--configdir',
                str(Path(args.workspace)/'kolla'),'-i',str(Path(args.workspace)/'hosts.ini'),
                '-e','@'+str(Path(args.workspace)/'host-profile.yml')]
            if testing_images(args):argv.append('--use-test-images')
            with (output/'private-prechecks.log').open('xb') as log:
                os.chmod(log.name,0o600)
                status=subprocess.run(argv,cwd=output,env=env,stdout=log,stderr=log,timeout=1800).returncode
            if inputs(args,backend_path,backend)[4]!=intent:raise ValueError('inputs changed during prechecks')
            after={n:observe(backend,h,code,intent['requests'][n],args.known_hosts) for n,h in cfg['hosts'].items()}
            if after!=facts:raise ValueError('protected host state changed during prechecks')
            if status:raise ValueError('Kolla prechecks failed; private log preserved')
            record['status']='INITIAL_KOLLA_PRECHECKS_PASSED_NOT_DEPLOYED'
        except BaseException:
            record['status']='NEEDS_REVIEW_INITIAL_KOLLA_PRECHECKS';write(journal,record)
            raise
        write(journal,record)
        return record
    finally:os.close(lock)


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-kolla-prechecks','run-kolla-prechecks'])
    for name in ('config','workspace','known-hosts','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--allow-experimental-ubuntu26',action='store_true')
    parser.add_argument('--use-test-images',dest='use_testing_images',action='store_true',
                        help='Explicit upstream testing-image opt-in, bound into plan SHA')
    parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        sys.path.insert(0,str(Path(backend_path).parent))
        spec=importlib.util.spec_from_file_location('spvm_prechecks_backend',backend_path)
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        result=execute(args,backend_path,backend)
        print(json.dumps({k:v for k,v in result.items() if k in
            ('status','plan_sha256','blockers','deploy_executed','guests_launched')}))
        return 2 if result.get('blockers') else 0
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: initial Kolla prechecks refused; private evidence preserved. '+type(exc).__name__
            +' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
