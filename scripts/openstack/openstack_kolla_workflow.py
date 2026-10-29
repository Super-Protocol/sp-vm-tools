"""Explicit fresh-cloud prechecks/deploy/post-deploy transaction; never upgrade a cloud."""
import argparse
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import select
import subprocess
import sys
import time

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_kolla_prechecks as prechecks
import openstack_kolla_workspace as workspace

PHASES = ('prechecks', 'deploy', 'post-deploy')

# An empty socket-activated libvirtd exits on its idle timeout. A live read-only
# client prevents an unrelated PID change during the protected install window.
# EOF on the controller-owned SSH stdin releases the remote client, also after
# a controller crash/disconnection. No service configuration or guest is changed.
LIBVIRT_HOLD = '''import json,subprocess,sys,time
child=subprocess.Popen(['virsh','-c','qemu:///system','event','--all','--loop'],
 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
 time.sleep(0.3)
 if child.poll() is not None:raise ValueError('read-only libvirt client failed')
 r=subprocess.run(['virsh','-c','qemu:///system','list','--all','--uuid'],
  check=True,capture_output=True,text=True,timeout=30)
 if r.stdout.strip():raise ValueError('fresh host acquired a guest')
 print('{"status":"READONLY_LIBVIRT_CONNECTION_HELD"}',flush=True)
 while sys.stdin.buffer.read(1):pass
finally:
 if child.poll() is None:
  child.terminate()
  try:child.wait(timeout=10)
  except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
'''


@contextmanager
def hold_libvirt(args,backend,cfg):
    connections=[]
    try:
        for host in cfg['hosts'].values():
            if 'compute' not in host.get('roles',[]):continue
            argv=backend.ssh_arguments(host)
            argv[1:1]=['-F','/dev/null','-o','UserKnownHostsFile='+str(args.known_hosts),
                       '-o','ForwardAgent=no','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3']
            command=shlex.join(['sudo','-n','/usr/bin/python3','-I','-c',LIBVIRT_HOLD])
            child=subprocess.Popen(argv+[command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
            connections.append(child)
            ready,_,_=select.select([child.stdout],[],[],45)
            if (not ready or child.stdout.readline(4096)!=b'{"status":"READONLY_LIBVIRT_CONNECTION_HELD"}\n'
                    or child.poll() is not None):
                raise ValueError('read-only libvirt guard connection failed')
        yield
    finally:
        # Close every stdin before waiting, so all remote clients release in parallel.
        for child in connections:
            if child.stdin is not None:child.stdin.close()
        for child in connections:
            try:child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
            finally:
                if child.stdout is not None:child.stdout.close()

# This post-deploy guard intentionally does not use the fresh-cloud probe: cloud
# containers/configuration now exist. No container environment or secret is read.
GUARD = '''
import hashlib,json,subprocess
from pathlib import Path
def run(argv,allowed=(0,)):
 r=subprocess.run(argv,capture_output=True,text=True,timeout=45,
  env={'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C','DOCKER_HOST':'unix:///var/run/docker.sock'})
 if r.returncode not in allowed or len(r.stdout)>4194304:raise ValueError('protected host observation failed')
 return r.stdout.strip()
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for part in iter(lambda:f.read(1048576),b''):h.update(part)
 return h.hexdigest()
machine=hashlib.sha256(Path('/etc/machine-id').read_text().strip().encode()).hexdigest()
if machine!=REQUEST['machine_sha256']:raise ValueError('wrong host')
domains=run(['virsh','-c','qemu:///system','list','--all','--uuid']) if 'compute' in REQUEST['roles'] else ''
if domains:raise ValueError('guests appeared during deployment')
guests=subprocess.run(['pgrep','-f',r'(^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$))'],capture_output=True,timeout=45)
if guests.returncode!=1:raise ValueError('direct guest process appeared during deployment')
files={str(p):sha(p) for p in [Path('/usr/bin/qemu-system-x86_64'),Path('/usr/bin/virsh'),
 Path('/usr/sbin/libvirtd'),Path('/etc/libvirt/qemu.conf'),Path('/etc/libvirt/libvirtd.conf'),
 Path('/etc/docker/daemon.json'),Path('/etc/spvm/host-helper.json'),Path('/etc/spvm/runtime-services.json')] if p.is_file()}
for name in REQUEST.get('host_source_sha256',{}):files[name]=sha(Path(name))
containers={}
for identity in REQUEST.get('protected_container_ids',[]):
 c=json.loads(run(['docker','inspect',identity]))[0]
 containers[identity]={'name':c['Name'].lstrip('/'),'image':c['Image'],'state':c['State']['Status'],'started_at':c['State']['StartedAt']}
services={s:run(['systemctl','show',s,'--property=LoadState,ActiveState,SubState,MainPID,ActiveEnterTimestampMonotonic,ExecMainStartTimestampMonotonic,NRestarts'],(0,1,4))
 for s in ['docker.service','libvirtd.service','virtqemud.service','qgs.service','qgsd.service','pccs.service','host-helper.socket']}
if any('LoadState=' not in value for value in services.values()):raise ValueError('service observation unavailable')
print(json.dumps({'schema_version':1,'blockers':[],'machine':machine,'files':files,'services':services,'containers':containers}))
'''


def fingerprint(args, backend_path, backend):
    cfg, receipt, source, code, intent = prechecks.inputs(args, backend_path, backend)
    intent = {**intent, 'use_testing_images':prechecks.testing_images(args),
              'workflow_sha256': file_hash(Path(__file__)),
              'backend_sources': {str(p.relative_to(Path(backend_path).parent)): file_hash(p)
                                  for p in sorted(Path(backend_path).parent.glob('*.py'))}}
    return cfg, receipt, source, code, intent


def guard(args, backend, cfg, intent, hosts):
    return {name: prechecks.observe(backend, host, GUARD,
                {**intent['requests'][name], 'protected_container_ids':sorted(hosts[name].get('protected_containers',{}))}, args.known_hosts)
            for name, host in cfg['hosts'].items()}


def invoke(args, receipt, source, output, phase):
    config = output/'ansible.cfg'
    env = {k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_', 'KOLLA_', 'PYTHON', 'OS_'))}
    env.update(PYTHONDONTWRITEBYTECODE='1', ANSIBLE_CONFIG=str(config),
        ANSIBLE_COLLECTIONS_PATH=receipt['collections'], KOLLA_ANSIBLE_DATA_FILES_PATH=str(source),
        PATH=str(Path(receipt['kolla_python']).parent)+':/usr/local/bin:/usr/bin:/bin')
    command = 'import sys;sys.path.insert(0,sys.argv.pop(1));from kolla_ansible.cmd.kolla_ansible import main;sys.exit(main())'
    if phase not in PHASES: raise ValueError('unsupported workflow phase')
    argv = [receipt['kolla_python'], '-c', command, str(source), phase, '--configdir',
            str(output/'kolla-run'), '-i', str(Path(args.workspace)/'hosts.ini'),
            '-e', '@'+str(Path(args.workspace)/'host-profile.yml')]
    # Pinned upstream accepts this acknowledgement only on its prechecks action.
    if prechecks.testing_images(args) and phase=='prechecks':argv.append('--use-test-images')
    with (output/('private-'+phase+'.log')).open('xb') as log:
        os.chmod(log.name, 0o600)
        if subprocess.run(argv, cwd=output, env=env, stdout=log, stderr=log, timeout=7200, umask=0o077).returncode:
            raise ValueError('Kolla phase failed; inspect private log')


def execute(args, backend_path, backend):
    cfg, receipt, source, code, intent = fingerprint(args, backend_path, backend)
    output = Path(args.output)
    origin = Path(args.workspace)
    if (not output.is_absolute() or output.resolve()!=output or output.is_relative_to(origin)
            or origin.is_relative_to(output)):
        raise ValueError('separate canonical private transaction required')
    if args.action == 'plan-kolla-install':
        start = time.time()
        hosts = {n: prechecks.observe(backend,h,code,intent['requests'][n],args.known_hosts)
                 for n,h in cfg['hosts'].items()}
        blockers = [n+':'+b for n,f in hosts.items() for b in f['blockers']]
        if time.time()-start>900: raise ValueError('host observations expired')
        plan = {'schema_version':1, 'kind':'fresh-kolla-install-v1', 'intent':intent,
                'hosts':hosts, 'blockers':blockers, 'phases':list(PHASES),
                'created_at':int(time.time()), 'guests_launched':False}
        plan['plan_sha256'] = digest(plan)
        output.mkdir(mode=0o700, exist_ok=False)
        write(output/'install-plan.json',plan)
        return {'status':'KOLLA_INSTALL_PLAN_BLOCKED' if blockers else 'KOLLA_INSTALL_PLAN_READY',
                'plan_sha256':plan['plan_sha256'], 'blockers':blockers}
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:
        raise ValueError('private owned transaction required')
    lock = os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    held=None
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan = json.loads(read(output/'install-plan.json'))
        body = {k:v for k,v in plan.items() if k!='plan_sha256'}
        if (plan.get('kind')!='fresh-kolla-install-v1' or plan.get('phases')!=list(PHASES)
                or plan.get('plan_sha256')!=digest(body) or args.plan_sha256!=plan['plan_sha256']
                or plan['intent']!=intent or plan['blockers'] or not args.ack_new_cloud):
            raise ValueError('explicit matching fresh-cloud deployment approval required')
        connection=hold_libvirt(args,backend,cfg)
        connection.__enter__()
        held=connection
        journal = output/'install-transaction.json'
        if journal.exists():
            record = json.loads(read(journal))
            if (record.get('plan_sha256')!=args.plan_sha256 or record.get('intent_sha256')!=digest(intent)
                    or record.get('status') not in ('PHASE_BOUNDARY','KOLLA_DEPLOYED_NOT_SPVM_ENROLLED')):
                raise ValueError('interrupted/failed deployment needs manual review, not replay')
            if not args.resume: raise ValueError('explicit --resume required at a completed boundary')
            if record.get('completed')!=list(PHASES[:len(record.get('completed',[]))]):
                raise ValueError('invalid phase journal')
            if guard(args,backend,cfg,intent,plan['hosts'])!=record['protected_hosts']:
                raise ValueError('protected host state changed since completed phase')
        else:
            if args.resume or type(plan['created_at']) is not int or not 0<=time.time()-plan['created_at']<=900:
                raise ValueError('fresh plan required for first execution')
            hosts = {n: prechecks.observe(backend,h,code,intent['requests'][n],args.known_hosts)
                     for n,h in cfg['hosts'].items()}
            if hosts!=plan['hosts']: raise ValueError('fresh host facts changed since approval')
            record = {'schema_version':1, 'plan_sha256':args.plan_sha256, 'intent_sha256':digest(intent),
                      'completed':[], 'protected_hosts':guard(args,backend,cfg,intent,plan['hosts']), 'guests_launched':False}
            (output/'kolla-run').mkdir(mode=0o700)
            for name in receipt['files']:
                if name.startswith('kolla/'):
                    target=output/'kolla-run'/name.removeprefix('kolla/')
                    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    workspace.private_file(target,read(origin/name))
            workspace.private_file(output/'ansible.cfg', '[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n'
                '[ssh_connection]\nusetty = False\nssh_args = -F /dev/null -o IdentityAgent=none -o IdentitiesOnly=yes '
                '-o StrictHostKeyChecking=yes -o UserKnownHostsFile='+shlex.quote(str(args.known_hosts))+'\n')
        for phase in PHASES[len(record['completed']):]:
            for name, approved in receipt['files'].items():
                if name.startswith('kolla/') and file_hash(output/'kolla-run'/name.removeprefix('kolla/'))!=approved:
                    raise ValueError('prepared Kolla run inputs changed before phase')
            record.update(status='RUNNING', active_phase=phase)
            write(journal,record)  # durable intent BEFORE any remote Kolla action
            try:
                invoke(args,receipt,source,output,phase)
                if fingerprint(args,backend_path,backend)[4]!=intent: raise ValueError('deployment input drift')
                for name, approved in receipt['files'].items():
                    if name.startswith('kolla/') and file_hash(output/'kolla-run'/name.removeprefix('kolla/'))!=approved:
                        raise ValueError('Kolla changed an approved configuration input')
                if guard(args,backend,cfg,intent,plan['hosts'])!=record['protected_hosts']: raise ValueError('protected runtime changed')
            except BaseException:
                record['status']='NEEDS_REVIEW';write(journal,record);raise
            record['completed'].append(phase)
            record.update(status='PHASE_BOUNDARY', active_phase=None)
            write(journal,record)
        record['status']='KOLLA_DEPLOYED_NOT_SPVM_ENROLLED';write(journal,record)
        return {'status':record['status'], 'completed':record['completed'], 'guests_launched':False}
    finally:
        try:
            if held is not None:held.__exit__(None,None,None)
        finally:os.close(lock)


def main(backend_path, argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-kolla-install','run-kolla-install'])
    for name in ('config','workspace','known-hosts','output'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--allow-experimental-ubuntu26',action='store_true')
    parser.add_argument('--use-test-images',dest='use_testing_images',action='store_true',
                        help='Explicit upstream testing-image opt-in; use identically on plan/apply')
    parser.add_argument('--ack-new-cloud',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        sys.path.insert(0,str(Path(backend_path).parent))
        spec=importlib.util.spec_from_file_location('spvm_workflow_backend',backend_path)
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        result=execute(args,backend_path,backend);print(json.dumps(result))
        return 2 if result.get('blockers') else 0
    except Exception:
        print('ERROR: Kolla install refused/failed; private transaction requires review.',file=sys.stderr)
        return 2
