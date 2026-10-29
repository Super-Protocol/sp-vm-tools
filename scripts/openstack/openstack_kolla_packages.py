"""Scoped host packages, Docker SDK and public API trust on prepared empty hosts."""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
import time
import traceback

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_kolla_prechecks as prechecks
import openstack_kolla_workspace as workspace

PACKAGES=['git','iputils-ping','tzdata']
SDK_PACKAGES=['python3-docker','python3-dbus','python3-requests']
PROFILES={'base-packages':(PACKAGES,'BASE_PACKAGES'),
          'host-sdk':(SDK_PACKAGES,'HOST_SDK'),
          'api-trust':(['ca-certificates'],'API_TRUST')}
REVIEWED_ROLE={
    'tasks/main.yml':'48b7540ff915359894ed10f86874a9da6a48df9d752615a7c2e129238d3dcd3e',
    'defaults/main.yml':'b09a72f34ea5344fa2fe3fda3508ec17c66153f15a8cd789e601e06f5ca2bf33'}


def profile(action):
    for name in PROFILES:
        if action in ('plan-'+name,'prepare-'+name):return name
    raise ValueError('unknown preparation profile')


def inputs(args,backend_path,backend):
    cfg,receipt,source,code,intent=prechecks.inputs(args,backend_path,backend)
    role=Path(receipt['collections'])/'ansible_collections/openstack/kolla/roles/packages'
    for name,expected in REVIEWED_ROLE.items():
        if file_hash(role/name)!=expected:raise ValueError('unreviewed Kolla package role')
    intent['package_role_sha256']=REVIEWED_ROLE
    intent['package_installer_sha256']=file_hash(Path(__file__))
    selected=profile(args.action)
    intent['preparation_profile']=selected
    if selected=='api-trust':
        import yaml
        globals_=yaml.safe_load(read(Path(args.workspace)/'kolla/globals.yml'))
        if (globals_.get('kolla_copy_ca_into_containers') is not True
                or globals_.get('kolla_certificates_dir')!=str(Path(args.workspace)/'kolla/certificates')):
            raise ValueError('matching Kolla container CA-copy configuration required')
        pem=read(Path(args.workspace)/'kolla/certificates/ca/ca.crt').decode('ascii')
        match=re.fullmatch(r'-----BEGIN CERTIFICATE-----\s+([A-Za-z0-9+/=\s]+)\s+-----END CERTIFICATE-----\s*',pem)
        if not match:raise ValueError('one public API CA certificate required; no private keys or chain')
        der=base64.b64decode(re.sub(r'\s+','',match[1]),validate=True)
        check=subprocess.run(['openssl','x509','-noout','-ext','basicConstraints'],input=pem,text=True,capture_output=True,timeout=30)
        if check.returncode or 'CA:TRUE' not in check.stdout:raise ValueError('CA certificate required')
        intent['api_ca']={'pem':pem,'sha256':hashlib.sha256(pem.encode()).hexdigest(),
            'der_sha256':hashlib.sha256(der).hexdigest(),
            'destination':'/usr/local/share/ca-certificates/spvm-openstack-'+digest(cfg['deployment_id'])[:16]+'.crt'}
    for wanted in intent['requests'].values():
        wanted.update(protect_package_runtime=True,package_profile=selected)
        if selected=='api-trust':wanted['api_ca']={k:v for k,v in intent['api_ca'].items() if k!='pem'}
    return cfg,receipt,source,code,intent


def playbook(selected='base-packages',ca=None):
    if selected=='api-trust':
        return [{'name':'SPVM public OpenStack API CA only','hosts':'all','gather_facts':False,
            'become':True,'any_errors_fatal':True,'serial':1,'tasks':[
                {'name':'Install only the public API CA without overwriting an existing anchor',
                 'ansible.builtin.copy':{'content':ca['pem'],'dest':ca['destination'],
                    'owner':'root','group':'root','mode':'0644','force':False},'register':'spvm_ca_copy'},
                {'name':'Register the approved API CA in host trust',
                 'ansible.builtin.command':{'argv':['/usr/sbin/update-ca-certificates']},
                 'when':'spvm_ca_copy.changed or not spvm_api_ca_trusted','changed_when':True}]}]
    packages=PROFILES[selected][0]
    return [{'name':'SPVM '+selected+' only','hosts':'all','gather_facts':True,
        'become':True,'any_errors_fatal':True,'serial':1,
        'environment':{'NEEDRESTART_MODE':'l','DEBIAN_FRONTEND':'noninteractive'},
        'module_defaults':{'ansible.builtin.apt':{'policy_rc_d':101,'install_recommends':False,
            'auto_install_module_deps':False,'dpkg_options':'force-confdef,force-confold'}},
        'vars':{'ubuntu_pkg_install':packages,'redhat_pkg_install':[],
            'ubuntu_pkg_removals':[],'redhat_pkg_removals':[],'apt_cache_valid_time':3600},
        'pre_tasks':[{'name':'Require actual supported host facts','ansible.builtin.assert':{'that':[
            "ansible_facts.distribution == 'Ubuntu'",
            "ansible_facts.distribution_version in ['24.04', '26.04']",
            "ansible_facts.architecture == 'x86_64'"]}}],
        'roles':['openstack.kolla.packages']}]


def protected(fact,selected='base-packages'):
    allowed=({'api_trust'} if selected=='api-trust' else {'base_packages'})|({'host_sdk'} if selected=='host-sdk' else set())
    return {k:v for k,v in fact.items() if k not in allowed}


def ready(fact,selected,intent):
    if selected=='api-trust':
        value=fact.get('api_trust',{});ca=intent['api_ca']
        return value.get('anchor_sha256')==ca['sha256'] and ca['der_sha256'] in value.get('bundle_certificates',[])
    return (set(fact.get('base_packages',{}))==set(PROFILES[selected][0])
            and (selected!='host-sdk' or fact.get('host_sdk',{}).get('ready') is True))


def execute(args,backend_path,backend):
    started=time.time()
    selected=profile(args.action);packages,label=PROFILES[selected]
    cfg,receipt,source,code,intent=inputs(args,backend_path,backend)
    def observe():
        return {n:prechecks.observe(backend,h,code,intent['requests'][n],args.known_hosts)
                for n,h in cfg['hosts'].items()}
    before=observe()
    blockers=[n+':'+b for n,f in before.items() for b in f['blockers']]
    for n,f in before.items():
        if not f['blockers'] and (not isinstance(f.get('base_packages'),dict)
                or not f.get('protected_package_services') or 'protected_package_files' not in f):
            blockers.append(n+':PACKAGE_PROTECTION_OBSERVATION_MISSING')
        if not f['blockers'] and selected=='host-sdk':
            if not isinstance(f.get('host_sdk',{}).get('ready'),bool):blockers.append(n+':SDK_PROBE_MISSING')
            elif set(f['base_packages'])==set(packages) and not f['host_sdk']['ready']:
                blockers.append(n+':INSTALLED_SDK_UNUSABLE_REVIEW_REQUIRED')
        if not f['blockers'] and selected=='api-trust' and not isinstance(f.get('api_trust'),dict):
            blockers.append(n+':API_TRUST_PROBE_MISSING')
    if time.time()-started>900:raise ValueError('host observation expired')
    plan={'schema_version':1,'kind':'kolla-'+selected+'-v1','intent':intent,'hosts':before,
          'blockers':blockers,'packages':[] if selected=='api-trust' else packages,'playbook':playbook(selected,intent.get('api_ca')),
          'status':label+'_PLAN_BLOCKED' if blockers else label+'_PLAN_READY',
          'deploy_authorized':False}
    output=Path(args.output);root=Path(args.workspace)
    if (not output.is_absolute() or output.resolve()!=output or output.is_relative_to(root)
            or root.is_relative_to(output)):raise ValueError('separate absolute transaction required')
    if args.action=='plan-'+selected:
        output.mkdir(mode=0o700,exist_ok=False)
        plan['created_at']=int(time.time());plan['expires_at']=plan['created_at']+900
        plan['plan_sha256']=digest(plan);write(output/(selected+'-plan.json'),plan)
        return plan
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:
        raise ValueError('private owned transaction required')
    previous=json.loads(read(output/(selected+'-plan.json')))
    body={k:v for k,v in previous.items() if k!='plan_sha256'}
    if (type(previous.get('created_at')) is not int or previous.get('expires_at')!=previous['created_at']+900
            or not 0<=time.time()-previous['created_at']<=900 or previous.get('plan_sha256')!=digest(body)
            or args.plan_sha256!=previous['plan_sha256'] or previous.get('blockers') or blockers):
        raise ValueError('fresh passing approved preparation plan required')
    plan.update(created_at=previous['created_at'],expires_at=previous['expires_at'])
    if plan!=body:raise ValueError('host or input changed since approval')
    lock=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        journal=output/(selected+'-transaction.json')
        if journal.exists() or journal.is_symlink():raise ValueError('already attempted; review before a new plan')
        record={'schema_version':1,'status':'PREPARING_'+label,'plan_sha256':args.plan_sha256,
            'package_role_attempted':False,'deploy_executed':False,'guests_launched':False}
        record['api_trust_update_attempted']=False
        write(journal,record)
        try:
            if any(not ready(f,selected,intent) for f in before.values()):
                workspace.private_file(output/'ansible.cfg','[defaults]\nhost_key_checking = True\n'
                    'retry_files_enabled = False\n[ssh_connection]\nusetty = False\nssh_args = -F /dev/null '
                    '-o IdentityAgent=none -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes '
                    '-o UserKnownHostsFile='+shlex.quote(str(args.known_hosts))+'\n')
                inventory={'all':{'hosts':{n:{'ansible_host':h['address'],'ansible_user':h['user'],
                    'ansible_connection':'ssh','ansible_python_interpreter':'/usr/bin/python3',
                    'ansible_ssh_private_key_file':h['identity_file']} for n,h in cfg['hosts'].items()}}}
                if selected=='api-trust':
                    for n,values in inventory['all']['hosts'].items():
                        values['spvm_api_ca_trusted']=intent['api_ca']['der_sha256'] in before[n]['api_trust']['bundle_certificates']
                write(output/'inventory.json',inventory);write(output/'packages.json',playbook(selected,intent.get('api_ca')))
                env={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','PYTHON','OS_'))}
                env.update(PYTHONDONTWRITEBYTECODE='1',ANSIBLE_CONFIG=str(output/'ansible.cfg'),
                    ANSIBLE_COLLECTIONS_PATH=receipt['collections'],
                    PATH=str(Path(receipt['kolla_python']).parent)+':/usr/local/bin:/usr/bin:/bin')
                record['package_role_attempted']=selected!='api-trust'
                record['api_trust_update_attempted']=selected=='api-trust';write(journal,record)
                argv=[receipt['kolla_python'],'-m','ansible.cli.playbook','-i',str(output/'inventory.json'),
                      str(output/'packages.json')]
                with (output/'private-packages.log').open('xb') as log:
                    os.chmod(log.name,0o600)
                    status=subprocess.run(argv,cwd=output,env=env,stdout=log,stderr=log,timeout=1800).returncode
            else:status=0
            if inputs(args,backend_path,backend)[4]!=intent:raise ValueError('inputs changed during preparation')
            after=observe();record['hosts_after']=after
            if not record['package_role_attempted'] and not record['api_trust_update_attempted'] and after!=before:
                raise ValueError('host changed during verified NOOP')
            if any(protected(before[n],selected)!=protected(f,selected) or not ready(f,selected,intent)
                   for n,f in after.items()):raise ValueError('host protection or readiness check failed')
            if selected=='api-trust':
                for n,f in after.items():
                    if set(f['api_trust']['bundle_certificates'])!=set(before[n]['api_trust']['bundle_certificates'])|{intent['api_ca']['der_sha256']}:
                        raise ValueError('unrelated system trust changed; review required')
            if status:raise ValueError('preparation failed; private log preserved')
            record['status']=label+'_READY_NOT_BOOTSTRAPPED'
        except BaseException:
            record['status']='NEEDS_REVIEW_'+label;write(journal,record);raise
        write(journal,record);return record
    finally:os.close(lock)


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=[prefix+name for name in PROFILES for prefix in ('plan-','prepare-')])
    for name in ('config','workspace','known-hosts','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--allow-experimental-ubuntu26',action='store_true')
    parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        sys.path.insert(0,str(Path(backend_path).parent))
        spec=importlib.util.spec_from_file_location('spvm_packages_backend',backend_path)
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        result=execute(args,backend_path,backend)
        print(json.dumps({k:v for k,v in result.items() if k in
            ('status','plan_sha256','blockers','package_role_attempted','api_trust_update_attempted','deploy_executed','guests_launched')}))
        return 2 if result.get('blockers') else 0
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: scoped host preparation refused; private evidence preserved. '
            +type(exc).__name__+' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
