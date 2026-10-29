"""Scoped, journalled Kolla Docker preparation; no hardware bootstrap or deploy."""
import argparse
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
import tempfile
import time
import traceback


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def write(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


SERVICES=('docker.service','libvirtd.service','virtqemud.service','qgs.service',
          'qgsd.service','pccs.service','host-helper.socket','haproxy.service')
INSTALL_TIMEOUT=1800

PROBE = 'SERVICE_UNITS='+repr(SERVICES)+'\n'+'''import hashlib,json,os,re,shutil,subprocess
from pathlib import Path
ENV={'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C',
     'DOCKER_HOST':'unix:///var/run/docker.sock'}
def run(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=30,env=ENV)
  if len(p.stdout)>=65536:return {'code':-2,'value':''}
  return {'code':p.returncode,'value':p.stdout.strip()}
 except (OSError,subprocess.TimeoutExpired):return {'code':-1,'value':''}
def h(path):
 d=hashlib.sha256()
 with path.open('rb') as f:
  for c in iter(lambda:f.read(1048576),b''):d.update(c)
 return d.hexdigest()
release={}
for line in Path('/etc/os-release').read_text().splitlines():
 if '=' in line:
  k,v=line.split('=',1);release[k]=v.strip('"')
cli=shutil.which('docker',path=ENV['PATH']) is not None
unit=run(['systemctl','show','docker.service','--property=LoadState','--value'])
docker=['docker','--host','unix:///var/run/docker.sock']
info=run(docker+['info','--format','{{.ServerVersion}}']) if cli else {'code':-1,'value':''}
protected={}
for name in ['/usr/bin/qemu-system-x86_64','/usr/sbin/libvirtd','/usr/bin/virsh',
 '/etc/libvirt/qemu.conf','/etc/libvirt/libvirtd.conf','/etc/libvirt/qemu/tdx.xml']:
 p=Path(name)
 if p.is_file():protected[name]=h(p)
# A read-only libvirt connection can activate the idle socket-managed daemon.
# Observe domains first, then capture the settled service identity; otherwise
# the probe itself makes its plan immediately stale. Preserve strict lifecycle
# comparison across the subsequent package transaction.
domains=run(['virsh','-c','qemu:///system','list','--all','--uuid'])
qemu=run(['pgrep','-f','^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$)'])
services={}
for name in SERVICE_UNITS:
 value=run(['systemctl','show',name,'--property=LoadState,ActiveState,SubState,MainPID,ActiveEnterTimestampMonotonic,NRestarts'])
 if value['code'] not in (0,1,4) or 'LoadState=' not in value['value']:raise ValueError('service observation unavailable')
 services[name]=value
containers={}
if info['code']==0:
 listing=run(docker+['ps','--all','--quiet','--no-trunc'])
 ids=listing['value'].split()
 if listing['code']!=0 or len(ids)>256 or any(not re.fullmatch('[a-f0-9]{64}',i) for i in ids):
  raise ValueError('bounded local container observation required')
 if ids:
  template='{"id":{{json .Id}},"image":{{json .Image}},"running":{{json .State.Running}},"started_at":{{json .State.StartedAt}},"restart_count":{{json .RestartCount}}}'
  inspected=run(docker+['inspect','--format',template,*ids])
  if inspected['code']!=0:raise ValueError('container observation unavailable')
  for line in inspected['value'].splitlines():
   c=json.loads(line)
   if c['id'] not in ids or c['id'] in containers:raise ValueError('container identity changed')
   containers[c['id']]=c
  if set(containers)!=set(ids):raise ValueError('container set changed')
config=Path('/etc/docker/daemon.json')
print(json.dumps({'schema_version':1,'machine':h(Path('/etc/machine-id')),
 'os':release.get('ID'),'version':release.get('VERSION_ID'),
 'codename':release.get('VERSION_CODENAME'),'architecture':os.uname().machine,
 'cli':cli,'socket':Path('/var/run/docker.sock').exists(),'unit':unit,
 'docker':info,'domains':domains,'qemu':qemu,
 'protected':protected,'services':services,'containers':containers,
 'docker_config_sha256':h(config) if config.is_file() else None}))
'''


def observe(backend, host, known_hosts=None):
    command=backend.ssh_arguments(host)
    if known_hosts:
        command[1:1]=['-o','UserKnownHostsFile='+known_hosts]
    result = subprocess.run(command+['sudo -n /usr/bin/python3 -'],
                            input=PROBE, capture_output=True, text=True, timeout=180)
    if result.returncode or len(result.stdout)>1048576:
        raise ValueError('read-only host observation failed')
    value = json.loads(result.stdout)
    if value.get('schema_version') != 1 or not re.fullmatch('[a-f0-9]{64}', value.get('machine','')):
        raise ValueError('invalid host observation')
    return value


def policy(fact, allow26, *, compute):
    if type(compute) is not bool:
        raise ValueError('explicit compute role required')
    if (not isinstance(fact.get('services'),dict) or set(fact['services'])!=set(SERVICES)
            or not isinstance(fact.get('containers'),dict)
            or any(not isinstance(v,dict) or v.get('code') not in (0,1,4)
                   or not isinstance(v.get('value'),str) or 'LoadState=' not in v['value']
                   for v in fact['services'].values())):
        raise ValueError('complete service/container observations required')
    if (fact.get('os') != 'ubuntu' or fact.get('architecture') != 'x86_64'
            or (fact.get('version'),fact.get('codename')) not in
                [('24.04','noble'),('26.04','resolute')]):
        raise ValueError('unreviewed host OS/architecture')
    if fact['version']=='26.04' and not allow26:
        raise ValueError('Ubuntu 26 requires explicit experimental acknowledgement')
    if fact['docker']['code']==0 and fact['cli']:
        return 'PRESERVE'
    if (fact['cli'] or fact['socket'] or fact['unit']['code']!=0
            or fact['unit']['value']!='not-found'):
        raise ValueError('incomplete or unreachable Docker; repair manually')
    if fact['qemu']['code']!=1:
        raise ValueError('Docker install requires a host without active hypervisor processes')
    if compute and (fact['domains']['code']!=0 or fact['domains']['value']
            or not fact['protected'].get('/usr/bin/virsh')
            or not fact['protected'].get('/usr/bin/qemu-system-x86_64')):
        raise ValueError('compute Docker install requires prepared empty libvirt host')
    return 'INSTALL'


def preserved(before,after,mode):
    """Only a missing Docker daemon may acquire a new lifecycle/configuration."""
    if mode=='PRESERVE':return before==after
    immutable=('machine','os','version','codename','architecture','protected','domains','qemu','containers')
    return (all(before[k]==after[k] for k in immutable)
            and all(before['services'][s]==after['services'][s] for s in SERVICES if s!='docker.service'))


def collection_hash(root, expected=None):
    root = Path(root).resolve(strict=True)
    required = root/'ansible_collections/openstack/kolla'
    main = (required/'roles/docker/tasks/main.yml').read_text()
    # The exact reviewed patch is intentionally narrow, not any matching keyword.
    patched = ('---\n# Experimental SPVM profile: an operator installs and configures Docker.\n'
        '# Do not mask units, install packages, change repositories or daemon settings.\n'
        '- name: Verify externally managed Docker daemon\n  become: true\n'
        '  ansible.builtin.command: docker info\n  changed_when: false\n  check_mode: false\n'
        '  when: spvm_external_docker | default(false) | bool\n- name: Install/Uninstall\n'
        '  ansible.builtin.include_tasks: "{{ package_action }}.yml"\n'
        '  when: not (spvm_external_docker | default(false) | bool)\n')
    if main != patched:
        raise ValueError('reviewed Kolla Docker-preservation patch required')
    files = {}
    for item in sorted(root.rglob('*')):
        if '__pycache__' in item.parts or item.suffix in ('.pyc','.pyo'):
            continue
        if item.is_symlink():
            resolved=item.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError('collection symlink must point to a regular file inside the tree')
            files[item.relative_to(root).as_posix()]={
                'symlink_target':str(resolved.relative_to(root)), 'sha256':file_hash(resolved)}
        elif item.is_file():
            files[item.relative_to(root).as_posix()] = file_hash(item)
    actual = digest(files)
    if expected is not None and actual != expected:
        raise ValueError('reviewed collection tree SHA256 mismatch')
    return actual


def inputs(args, backend):
    cfg = backend.deployment_config(args.config)
    if args.host not in cfg['hosts']:
        raise ValueError('explicit configured OpenStack host required')
    if Path(args.kolla_source).resolve()!=Path(args.kolla_source):
        raise ValueError('absolute resolved Kolla source required')
    revision = subprocess.run(['git','-C',args.kolla_source,'rev-parse','HEAD'],
                             check=True,capture_output=True,text=True,timeout=30).stdout.strip()
    dirty = subprocess.run(['git','-C',args.kolla_source,'status','--porcelain'],
                          check=True,capture_output=True,text=True,timeout=30).stdout
    if revision != cfg['kolla']['revision'] or dirty:
        raise ValueError('clean pinned Kolla source required')
    # Hash dependencies too, not merely the openstack.kolla role itself.
    collections = collection_hash(args.collections, args.collections_sha256)
    executable = Path(args.ansible_playbook)
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError('absolute existing ansible-playbook required')
    trust=None
    if args.known_hosts:
        trust_path=Path(args.known_hosts)
        if not trust_path.is_absolute() or not trust_path.is_file():
            raise ValueError('absolute existing known-hosts file required')
        trust={'path':str(trust_path),'sha256':file_hash(trust_path)}
    backend_root=Path(backend.__file__).parent
    sources={p.relative_to(backend_root).as_posix():file_hash(p)
             for p in backend_root.rglob('*.py') if '__pycache__' not in p.parts}
    spec_root=backend_root.parent/'nova-spvm/openstack_spvm'
    sources.update({'openstack_spvm/'+p.name:file_hash(p)
                    for p in spec_root.glob('*.py')})
    return cfg, {'config':digest(cfg), 'kolla_revision':revision,
                 'collections':collections,'playbook_executable':file_hash(executable),
                 'backend_sources':digest(sources),'ssh_trust':trust,
                 'runner':file_hash(Path(__file__))}


def playbook(fact):
    # Only the container-engine role. No baremetal role, libvirt, reboot or deploy.
    return [{'name':'SPVM container engine preparation', 'hosts':'spvm_target',
        'gather_facts':True, 'become':True, 'any_errors_fatal':True,
        'vars':{'spvm_external_docker':False,'enable_docker_repo':True,
                'package_action':'install','container_engine':'docker'},
        'pre_tasks':[{'name':'Require real reviewed host facts', 'ansible.builtin.assert':{
            'that':["ansible_facts.distribution == 'Ubuntu'",
                    "ansible_facts.distribution_version == '"+fact['version']+"'",
                    "ansible_facts.distribution_release == '"+fact['codename']+"'"]}}],
        'roles':['openstack.kolla.docker']}]


def execute(args, backend):
    cfg, bindings = inputs(args,backend)
    host = cfg['hosts'][args.host]
    current = observe(backend,host,args.known_hosts)
    mode = policy(current,args.allow_experimental_ubuntu26,compute='compute' in host['roles'])
    output = Path(args.output).absolute()
    if output.resolve()!=output:
        raise ValueError('non-symlink output required')
    output.mkdir(mode=0o700,parents=False,exist_ok=True)
    if output.stat().st_uid != os.getuid() or output.stat().st_mode & 0o077:
        raise ValueError('private owned output directory required')
    lock = os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan_path = output/'container-engine-plan.json'
        journal_path = output/'container-engine-transaction.json'
        if plan_path.is_symlink() or journal_path.is_symlink():
            raise ValueError('symlink receipt refused')
        if args.action=='plan-container-engine':
            if plan_path.exists() or journal_path.exists():
                raise ValueError('plan output exists; choose new directory')
            plan = {'schema_version':1,'created_at':int(time.time()),'host':args.host,
                    'mode':mode,'bindings':bindings,'host_before':current,
                    'experimental_ubuntu26':args.allow_experimental_ubuntu26,
                    'ansible_playbook':args.ansible_playbook,'collections':str(Path(args.collections).resolve()),
                    'kolla_source':args.kolla_source}
            plan['plan_sha256']=digest(plan)
            write(plan_path,plan)
            return {'status':'CONTAINER_ENGINE_PLAN_READY', 'mode':mode,'plan_sha256':plan['plan_sha256']}
        plan = json.loads(plan_path.read_text())
        expected = plan.pop('plan_sha256')
        if (args.plan_sha256!=expected or digest(plan)!=expected or plan['host']!=args.host
                or plan['bindings']!=bindings
                or plan['experimental_ubuntu26']!=args.allow_experimental_ubuntu26
                or plan['ansible_playbook']!=args.ansible_playbook
                or plan['collections']!=str(Path(args.collections).resolve())
                or plan['kolla_source']!=args.kolla_source):
            raise ValueError('changed plan/input refused')
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else None
        if journal:
            if journal.get('plan_sha256')!=expected:
                raise ValueError('journal plan mismatch')
            # Do not blindly repeat package tasks after interruption or failure.
            if journal.get('status')!='CONTAINER_ENGINE_READY':
                raise ValueError('incomplete install needs operator review; package tasks not replayed')
            if current!=journal['host_after']:
                raise ValueError('completed host state changed; create a new plan')
            return journal
        if not 0<=time.time()-plan['created_at']<=900 or current!=plan['host_before'] or mode!=plan['mode']:
            raise ValueError('expired plan or host drift')
        journal={'schema_version':1,'plan_sha256':expected,'status':'PREPARED','mode':mode,
                 'host':args.host,'mutations':[],'host_after':None}
        write(journal_path,journal)
        if mode=='INSTALL':
            alias = host.get('inventory_name',args.host)
            common_args='-o IdentityAgent=none -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes'
            if args.known_hosts:
                common_args+=' -o UserKnownHostsFile='+shlex.quote(args.known_hosts)
            inventory={'all':{'children':{'spvm_target':{'hosts':{alias:{
                'ansible_host':host['address'],'ansible_user':host['user'],
                'ansible_connection':'ssh','ansible_python_interpreter':'/usr/bin/python3',
                'ansible_ssh_private_key_file':host.get('identity_file'),
                'ansible_ssh_common_args':common_args}}}}}}
            write(output/'inventory.json',inventory)
            write(output/'prepare-docker.json',playbook(current))
            environment={k:v for k,v in os.environ.items() if not k.startswith('ANSIBLE_')}
            environment.update(ANSIBLE_COLLECTIONS_PATH=plan['collections'],
                               ANSIBLE_CONFIG=str(output/'ansible.cfg'))
            with (output/'ansible.cfg').open('x') as config:
                os.chmod(config.name,0o600)
                config.write('[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n')
            journal['status']='INSTALLING'
            journal['mutations']=['kolla-docker-role-attempted']
            write(journal_path,journal)
            try:
                with (output/'ansible-private.log').open('x') as log:
                    os.chmod(log.name,0o600)
                    result=subprocess.run([args.ansible_playbook,'-i',str(output/'inventory.json'),
                        str(output/'prepare-docker.json')],cwd=output,env=environment,stdout=log,stderr=log,
                        check=False,timeout=INSTALL_TIMEOUT)
            except BaseException as error:
                journal.update(status='NEEDS_REVIEW',error_class=type(error).__name__,remote_state_unconfirmed=True)
                write(journal_path,journal)
                raise
            journal['returncode']=result.returncode
            write(journal_path,journal)
            if result.returncode:
                journal['status']='NEEDS_REVIEW'
                journal['remote_state_unconfirmed']=True
                write(journal_path,journal)
                return journal
        try:
            after=observe(backend,host,args.known_hosts)
            journal['host_after']=after
            journal['status']=('CONTAINER_ENGINE_READY'
                if policy(after,args.allow_experimental_ubuntu26,compute='compute' in host['roles'])=='PRESERVE' and preserved(current,after,mode)
                else 'NEEDS_REVIEW')
        except BaseException as error:
            journal.update(status='NEEDS_REVIEW',error_class=type(error).__name__)
            write(journal_path,journal)
            raise
        write(journal_path,journal)
        return journal
    finally:
        os.close(lock)


def main(backend_path, argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-container-engine','prepare-container-engine'])
    for name in ('config','host','kolla-source','collections','collections-sha256','ansible-playbook','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--allow-experimental-ubuntu26',action='store_true')
    parser.add_argument('--known-hosts',help='Existing independently trusted SSH host-key file; never populated automatically')
    parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    sys.path.insert(0,str(Path(backend_path).parent))
    spec=importlib.util.spec_from_file_location('spvm_installer_backend',backend_path)
    backend=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    try:
        result=execute(args,backend)
        print(json.dumps({k:v for k,v in result.items() if k in ('status','mode','plan_sha256')}))
        return 0 if result['status'] in ('CONTAINER_ENGINE_PLAN_READY','CONTAINER_ENGINE_READY') else 2
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,KeyError) as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: container-engine preparation refused or interrupted; inspect private plan/journal. '
              'No automatic retry or rollback was performed. '
              +type(exc).__name__+' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
