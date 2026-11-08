"""Retire an empty compute using controller-owned state and operator SSH."""
from contextlib import contextmanager
import hashlib
import json
import openstack_progress as progress
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import time

import yaml
from openstack_container_engine import digest,file_hash,write
from openstack_errors import InstallerError
from openstack_install_continue import document,private
import openstack_compute_removal as removal
from openstack_remove_entry import parser

POLICY='/etc/spvm/host-budget-policy.json'
LOCK='/var/lib/spvm-host-budget-worker/worker.lock'
CLAIM='/var/lib/spvm-host-budget-worker/removal-coordinator.json'

# Sent as part of the fixed operation, not imported from manager-only installer
# modules. The remote interpreter runs under sudo; records must belong to it.
CONTROLLER_RECORD_SOURCE='''import os,stat
from pathlib import Path
def private(path):
 path=Path(path)
 if path.resolve()!=path or path.is_symlink():raise ValueError('canonical private retirement record required')
 value=path.stat()
 if not stat.S_ISREG(value.st_mode) or value.st_uid!=os.geteuid() or value.st_mode&0o077 or value.st_nlink!=1 or value.st_size>16*1024*1024:
  raise ValueError('bounded single-link private retirement record required')
 return path
'''


def configuration_identity(cfg):
    value=json.loads(json.dumps(cfg));value['driver'].pop('directory',None)
    for h in value['hosts'].values():h.pop('ssh_transport',None);h.pop('identity_file',None)
    value['kolla'].pop('installation_kit',None)
    return value


LOCK_SOURCE='''import fcntl,hashlib,json,os,select,sys,time
from pathlib import Path
p=Path('/var/lib/spvm-host-budget-worker/worker.lock')
for q in (p.parent,*p.parent.parents):
 st=q.lstat()
 if q.is_symlink() or st.st_uid!=0 or st.st_mode&0o022:raise ValueError('unsafe worker lock parent')
fd=os.open(p,os.O_RDWR|os.O_NOFOLLOW)
st=os.fstat(fd)
if st.st_uid!=0 or st.st_nlink!=1 or st.st_mode&0o777!=0o600:raise ValueError('unsafe worker lock')
deadline=time.monotonic()+120
while True:
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
 except BlockingIOError:
  if time.monotonic()>deadline:raise ValueError('host resource worker is busy')
  time.sleep(.25)
pid=os.getpid();ticks=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
print(json.dumps({'pid':pid,'ticks':ticks,'fd':fd,'inode':st.st_ino,'machine':hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()}),flush=True)
try:
 while select.select([sys.stdin.buffer],[],[],1800)[0]:
  if not os.read(sys.stdin.fileno(),1):break
finally:os.close(fd)
'''


def lock_guard(value):
    return 'LOCK='+repr(value)+'\n'+'''from pathlib import Path
import os
p=Path('/proc')/str(LOCK['pid'])
if p.stat().st_uid!=0 or p.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]!=LOCK['ticks']:
 raise ValueError('controller lock process changed')
if os.readlink(p/'fd'/str(LOCK['fd']))!='/var/lib/spvm-host-budget-worker/worker.lock' or (p/'fd'/str(LOCK['fd'])).stat().st_ino!=LOCK['inode']:
 raise ValueError('controller worker lock was lost')
'''


class Coordinator:
    def __init__(self,args,path,module,cfg,base):
        self.args=args;self.path=path;self.module=module;self.cfg=cfg;self.base=base
        self.output=Path(args.output);self.child=None
        controls=[n for n,h in cfg['hosts'].items() if 'control' in h['roles']]
        if len(controls)!=1:raise InstallerError('This workflow currently requires one configured controller.')
        self.controller=controls[0]
        cloud=getattr(args,'cloud_context',None)
        self.argv=list(cloud.argv) if cloud is not None else module.ssh_arguments(cfg['hosts'][self.controller])
        self.argv[1:1]=['-o','ServerAliveInterval=15','-o','ServerAliveCountMax=12']
        self.native_root=cloud.native_root if cloud is not None else args.native_root
        self.python=self.native_root+'/sdk/bin/python'

    def alive(self):
        if self.child is None or self.child.poll() is not None:raise InstallerError('Controller lock channel ended; no automatic retry.')
        self.child.stdin.write(b'.');self.child.stdin.flush()

    @contextmanager
    def locked(self):
        with (self.output/'private-controller-lock.log').open('ab') as errors:
            os.fchmod(errors.fileno(),0o600)
            self.child=subprocess.Popen(self.argv+['sudo -n /usr/bin/python3 -I -B -u -c '+shlex.quote(LOCK_SOURCE)],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=errors)
            try:
                if not select.select([self.child.stdout],[],[],140)[0]:raise InstallerError('Timed out acquiring controller resource lock.')
                line=self.child.stdout.readline(4096)
                self.lock=json.loads(line)
                if set(self.lock)!={'pid','ticks','fd','inode','machine'}:raise InstallerError('Invalid controller lock acknowledgement.')
                yield
            finally:
                if self.child.stdin:self.child.stdin.close()
                try:self.child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.child.terminate()
                    try:self.child.wait(timeout=10)
                    except subprocess.TimeoutExpired:self.child.kill();self.child.wait()
                if self.child.stdout:self.child.stdout.close()
                self.child=None

    def remote(self,code):
        self.alive()
        with (self.output/'private-controller-operations.log').open('ab') as errors:
            os.fchmod(errors.fileno(),0o600)
            p=subprocess.run(self.argv+['sudo -n '+shlex.quote(self.python)+' -I -B -'],
                input=(lock_guard(self.lock)+code).encode(),stdout=subprocess.PIPE,stderr=errors,timeout=300)
        self.alive()
        if p.returncode or len(p.stdout)>8*1024*1024:
            raise InstallerError('Controller operation failed; inspect private-controller-operations.log. Do not replay an ambiguous removal.')
        return json.loads(p.stdout)

    def snapshot(self):
        cloud=getattr(self.args,'cloud_context',None)
        if cloud is not None:cloud.verify(self)
        code='ROOT='+repr(self.native_root)+'\n'+CONTROLLER_RECORD_SOURCE+'''import sys,json,hashlib
from pathlib import Path
root=Path(ROOT)
for p in (root,*root.parents):
 st=p.lstat()
 if p.is_symlink() or st.st_uid!=0 or st.st_mode&0o022:raise ValueError('untrusted controller source')
sys.path[:0]=[str(root/'tools'),str(root/'driver/deployment')]
from openstack_host_budgets import policy
from openstack_installer import backend
from openstack_kolla_existing import backend as load_backend
from openstack_container_engine import file_hash
path=backend(root/'driver',verified_bundle=True);module=load_backend(path)
p=Path('/etc/spvm/host-budget-policy.json');v=policy(p)
cfg=module.deployment_config(v['config'],transport=False)
claim=Path('/var/lib/spvm-host-budget-worker/removal-coordinator.json')
if claim.exists():
 previous=json.loads(private(claim).read_bytes())
 if previous.get('status')!='COMPUTE_REMOVED':raise ValueError('previous controller removal needs manual review')
print(json.dumps({'policy':v,'config':cfg,'policy_sha256':file_hash(p),'config_sha256':file_hash(Path(v['config'])),
 'native_sources':{str(p):file_hash(p) for p in (root/'tools').glob('*.py')},'backend':str(path)}))
'''
        self.native=self.remote(code)
        if cloud is not None and (self.native['policy']['project_id']!=cloud.identity['project_id']
                or self.lock['machine']!=cloud.identity['controller_machine']):
            raise InstallerError('Controller project or physical identity differs from the cloud-management identity.')
        if (self.native['policy']['controller']!=self.controller
                or self.native['policy']['controller_machine_sha256']!=self.lock['machine']
                or configuration_identity(self.native['config'])!=configuration_identity(self.cfg)):
            raise InstallerError('Active controller configuration differs from the local bound workspace; reconcile it before retirement.')
        # Exercise the same imports and input validation as claim, without
        # creating a claim or changing controller files, including in --check.
        self.native_operation('check')
        snapshot=self.output/'controller-snapshot.json'
        if snapshot.exists():
            if document(snapshot)!=self.native:raise InstallerError('Controller inputs changed after planning.')
        else:write(snapshot,self.native)
        return self.native['policy']

    def inputs(self):
        paths=[self.args.config,self.output/'controller-snapshot.json',self.args.known_hosts,
               Path(self.args.environment)/'environment.json']
        paths+=self.cfg['hosts'][self.controller].get('ssh_transport',{}).get('identity_files',[])
        paths += list(Path(__file__).parent.glob('*.py'))
        if getattr(self.args,'active_config',None):paths.append(self.args.active_config)
        return {str(p):file_hash(p) for p in paths}

    def native_operation(self,action,plan=None):
        request={'action':action,'host':self.args.host,'operation':digest(str(self.output)),
                 'plan_sha256':plan['plan_sha256'] if plan else None,'snapshot':self.native}
        code='REQUEST='+repr(request)+'\n'+CONTROLLER_RECORD_SOURCE+'''import sys,json,os
from pathlib import Path
root=Path('''+repr(self.native_root)+''')
sys.path.insert(0,str(root/'tools'))
from openstack_container_engine import file_hash,write
from openstack_host_budgets import policy
request=REQUEST;before=request['snapshot'];host=request['host'];action=request['action']
for filename,wanted in before['native_sources'].items():
 p=Path(filename);st=p.lstat()
 if p.resolve()!=p or not p.is_file() or st.st_uid!=0 or st.st_mode&0o022 or st.st_nlink!=1 or st.st_size>4194304 or file_hash(p)!=wanted:
  raise ValueError('installed controller source changed')
policy_path=Path('/etc/spvm/host-budget-policy.json');config_path=Path(before['policy']['config'])
claim=Path('/var/lib/spvm-host-budget-worker/removal-coordinator.json')
if action in ('check','claim'):
 if claim.exists() and json.loads(claim.read_bytes()).get('status')!='COMPUTE_REMOVED':raise ValueError('unfinished controller retirement')
 if file_hash(policy_path)!=before['policy_sha256'] or file_hash(config_path)!=before['config_sha256']:
  raise ValueError('controller inputs changed before retirement')
 record={'status':'CONTROLLER_RETIREMENT_CHECKED' if action=='check' else 'REMOVING_COMPUTE',
         'host':host,'operation':request['operation'],'plan_sha256':request['plan_sha256']}
 if action=='claim':write(claim,record)
else:
 record=json.loads(private(claim).read_bytes())
 if record.get('status')!='REMOVING_COMPUTE' or record.get('host')!=host or record.get('operation')!=request['operation'] or record.get('plan_sha256')!=request['plan_sha256']:
  raise ValueError('controller retirement claim differs')
 if action=='configure':
  if file_hash(policy_path)!=before['policy_sha256'] or file_hash(config_path)!=before['config_sha256']:raise ValueError('controller configuration drift')
  cfg=before['config'];v=policy(policy_path)
  if host not in cfg['hosts'] or cfg['hosts'][host]['roles']!=['compute']:raise ValueError('compute-only target required')
  directory=claim.parent/('retirement-'+request['operation']);directory.mkdir(mode=0o700)
  write(directory/'configuration-before.json',{'config':cfg,'policy':v})
  del cfg['hosts'][host];del v['executors'][host]
  write(claim,{**record,'configuration_inflight':True})
  write(policy_path,v);write(config_path,cfg)
  record.update(policy_sha256=file_hash(policy_path),config_sha256=file_hash(config_path),configuration_inflight=False)
  write(claim,record)
 elif action=='finish':
  if record.get('configuration_inflight') or file_hash(policy_path)!=record.get('policy_sha256') or file_hash(config_path)!=record.get('config_sha256'):
   raise ValueError('controller successor configuration differs')
  record['status']='COMPUTE_REMOVED';write(claim,record)
 else:raise ValueError('unknown fixed retirement operation')
print(json.dumps({'status':record['status'],'host':host,'operation':request['operation']}))
'''
        return self.remote(code)

    def start(self,plan):
        self.plan=plan
        cloud=getattr(self.args,'cloud_context',None)
        if cloud is not None:cloud.begin(self,'remove-compute',self.args.host,digest(str(self.output)),plan['plan_sha256'])
        return self.native_operation('claim',plan)

    def configure(self,new,successor,plan):
        self.native_operation('configure',plan)
        write(self.output/'remaining-deployment.yaml',new)
        self.successor=successor;self.successor_config=self.output/'remaining-deployment.yaml'
        return {'workspace':str(successor),'config':str(self.output/'remaining-deployment.yaml'),'host_removed':self.args.host}

    def refresh(self):
        # Execute the installed worker with its native paths and credentials;
        # never copy them to management or ask it to use management-local paths.
        native_path=Path(self.native['backend'])
        code='TOOLS='+repr(self.native_root+'/tools')+'\nBACKEND='+repr(str(native_path))+'\n'+'''
import sys,json,subprocess,openstack
from pathlib import Path
sys.path[:0]=[TOOLS,str(Path(BACKEND).parent)]
from openstack_kolla_existing import backend
from openstack_host_budgets import Worker,policy
config=policy('/etc/spvm/host-budget-policy.json');module=backend(BACKEND)
c=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=30)
try:
 worker=Worker(config,BACKEND,module,c,policy_path=Path('/etc/spvm/host-budget-policy.json'))
 worker.sync_catalogs()
finally:c.close()
p=subprocess.run(['systemctl','start','spvm-web-capacity.service'],capture_output=True,timeout=120)
if p.returncode:raise ValueError('capacity refresh failed')
print(json.dumps({'status':'REMAINING_HOST_CATALOGS_REFRESHED','hosts':sorted(config['executors'])}))
'''
        return self.runner.remote(self.controller,lock_guard(self.lock)+code,controller=True,auth=True)

    def finish(self,plan):
        result=self.native_operation('finish',plan)
        cloud=getattr(self.args,'cloud_context',None)
        if cloud is not None:
            network=document(cloud.base/'network.json');network['hosts'].pop(self.args.host,None)
            cloud.publish(self,self.successor,self.successor_config,digest(str(self.output)),network=network,retirement=plan)
        return result


@progress.command('remove')
def main(argv=None):
    args=parser().parse_args(argv);output=Path(args.output);owned=False
    try:
        from openstack_install import protected_parent,deployment_lock
        if os.geteuid()!=0:raise InstallerError('Run the management-host coordinator with sudo.')
        protected_parent(output.parent)
        if output.resolve()!=output:raise InstallerError('Canonical private output required.')
        if output.exists() or output.is_symlink():
            raise InstallerError('Removal output already exists. Inspect its journal and use a new directory; no automatic replay.')
        output.mkdir(mode=0o700);owned=True
        protected_parent(output)
        if output.stat().st_mode&0o077:raise InstallerError('Private output required.')
        with deployment_lock():
            from openstack_cloud_context import Cloud
            cloud=Cloud(args,output).receive();args.cloud_context=cloud
            base=cloud.base;path=cloud.backend_path;module=cloud.module
            active_workspace,active_config=cloud.workspace,cloud.config
            from openstack_active_deployment import portable
            from openstack_install_phases import document as yaml_document
            args.workspace=str(active_workspace)
            args.active_config=yaml_document(output/'active-deployment.yaml',portable(yaml.safe_load(active_config.read_bytes())))
            args.config=args.active_config
            cfg=module.deployment_config(args.active_config)
            if args.host not in cfg['hosts'] or cfg['hosts'][args.host]['roles']!=['compute']:
                raise InstallerError('Only an explicit compute-only target is supported; controller/network/ingress removal is refused.')
            import openstack_kolla_workspace as workspace
            receipt=workspace.check(args.workspace);binding=document(Path(args.workspace)/'compute-binding.json')
            body={k:v for k,v in binding.items() if k!='binding_sha256'}
            if (receipt['config_sha256']!=digest(cfg) or binding['config_sha256']!=digest(cfg)
                    or binding['binding_sha256']!=digest(body) or receipt['compute_binding']!=digest(body)):
                raise InstallerError('Local bound workspace/config identity differs.')
            coordinator=Coordinator(args,path,module,cfg,base)
            from compute_removal import Backend
            runner=Backend(cfg,coordinator.controller,coordinator.python,'/etc/spvm/auth/admin-openrc.sh',
                           output/'observations',known_hosts=args.known_hosts)
            original=runner.remote
            def guarded_remote(host,source,**kwargs):
                coordinator.alive()
                source='import sys;sys.dont_write_bytecode=True\n'+source
                if host==coordinator.controller:source=lock_guard(coordinator.lock)+source
                result=original(host,source,**kwargs);coordinator.alive();return result
            runner.remote=guarded_remote;coordinator.runner=runner
            args.policy=POLICY
            with coordinator.locked():
                delegation=coordinator.snapshot()
                print('Cloud: '+cloud.pointer['cloud_id']+' · '+cfg['deployment_id']+' · '+cloud.identity['api_endpoint'],flush=True)
                if args.host not in delegation['executors']:raise InstallerError('Target has no enrolled resource executor.')
                args.action='plan-remove-compute'
                planfile=output/'remove-compute-plan.json'
                if not planfile.exists():
                    removal.execute(args,output,module,delegation,cfg,receipt,binding,runner,coordinator=coordinator)
                plan=document(planfile)
                if args.check:
                    print('CHECKED_NOT_REMOVED: no host services, registrations or configuration were changed.');return 0
                print('Retire '+args.host+' ('+plan['hostname']+'). No VM is stopped or deleted; bootstrap/libvirt/QEMU are retained.',flush=True)
                if not args.yes:
                    with progress.pause():
                        print('Type the host name to approve: ',end='',flush=True)
                        if sys.stdin.readline().strip()!=args.host:raise InstallerError('Retirement not approved; no removal phase started.')
                args.action='remove-compute';args.confirm_host=args.host;args.plan_sha256=plan['plan_sha256']
                return removal.execute(args,output,module,delegation,cfg,receipt,binding,runner,coordinator=coordinator)
    except Exception as error:
        if owned and output.exists() and output.is_dir() and not output.is_symlink() and output.stat().st_uid==0 and not output.stat().st_mode&0o077:
            import traceback
            write(output/'failure.json',{'type':type(error).__name__,'sites':[(Path(f.filename).name,f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)[-6:]]})
        if isinstance(error,InstallerError):print('ERROR: '+str(error),file=sys.stderr)
        else:print('ERROR: retirement failed ('+type(error).__name__+'); inspect '+str(output/'failure.json')+' and private logs. No automatic retry.',file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
