"""Host-only batch frontend; reuse the guarded single-compute transaction.

Check every selected target before approval. Commit sequentially; stop on the
first failure without rollback, replay or repair of earlier transactions.
"""
import copy
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import traceback
from types import SimpleNamespace as R

import yaml
from openstack_active_deployment import portable
from openstack_add_entry import parser
from openstack_container_engine import digest,file_hash,write
from openstack_errors import InstallerError
from openstack_install import deployment_lock,protected_parent
from openstack_install_continue import document as read_document
from openstack_remove_manager import CONTROLLER_RECORD_SOURCE


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader,node,deep=False):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=deep)
        if not isinstance(key,str) or key in result:
            raise InstallerError('YAML mapping keys must be unique strings; duplicate hosts are not allowed.')
        result[key]=loader.construct_object(value_node,deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,unique_mapping)


def host_file(path,selected=None):
    path=Path(path).resolve(strict=True)
    with path.open('rb') as stream:body=stream.read(1048577)
    if len(body)>1048576:raise InstallerError('Host YAML exceeds 1 MiB.')
    try:value=yaml.load(body,Loader=UniqueLoader)
    except yaml.YAMLError:raise InstallerError('Invalid host YAML syntax.') from None
    if not isinstance(value,dict) or set(value)!={'hosts'} or not isinstance(value['hosts'],dict) or not 1<=len(value['hosts'])<=128:
        raise InstallerError('Use a host-only YAML with one hosts mapping (1..128 entries); cloud settings come from --controller.')
    for name,host in value['hosts'].items():
        if not re.fullmatch('[a-z][a-z0-9-]{0,39}',name) or not isinstance(host,dict):
            raise InstallerError('Each host needs a DNS-safe alias and a mapping of compute settings.')
        forbidden=set(host)&{'compute_resources','storage_pools','storage_discovery','gpu_inventory'}
        if forbidden:raise InstallerError(name+': resource fields are not accepted: '+', '.join(sorted(forbidden))+'. Configure resources after add-compute, then explicitly Enable the host.')
        if host.get('roles',['compute'])!=['compute']:raise InstallerError(name+': add-compute accepts compute-only hosts.')
        if 'allow_debug' in host and type(host['allow_debug']) is not bool:raise InstallerError(name+': allow_debug must be boolean.')
        if host.get('launch_mode')=='untrusted' and host.get('allow_debug') is not True:
            raise InstallerError(name+': untrusted compute requires explicit allow_debug: true.')
    if selected is not None:
        if selected not in value['hosts']:raise InstallerError('--host is absent from the host YAML.')
        value={'hosts':{selected:value['hosts'][selected]}}
    return path,value['hosts'],hashlib.sha256(body).hexdigest()


def intent(active,hosts):
    """Merge only selected compute intents; reject identity collisions early."""
    result=portable(active);addresses={};aliases={}
    for name,host in {**result['hosts'],**hosts}.items():
        for field,seen,value in (('address',addresses,host.get('address')),('inventory_name',aliases,host.get('inventory_name',name))):
            if field=='address':
                try:value=str(ipaddress.ip_address(value))
                except ValueError:raise InstallerError('A literal host IP address is required for '+name+'.') from None
            if not isinstance(value,str) or not value or value in seen:
                raise InstallerError('Duplicate or missing '+field+' for '+name+'.')
            seen[value]=name
    for name,raw in hosts.items():
        host=copy.deepcopy(raw);host.pop('allow_debug',None);host.setdefault('roles',['compute'])
        if name in result['hosts']:
            # Existing-host input is not a resource/configuration update.
            wanted=portable({'hosts':{name:host}})['hosts'][name]
            actual=portable({'hosts':{name:result['hosts'][name]}})['hosts'][name]
            for key in ('address','user','roles','inventory_name','management_interface','tunnel_interface','launch_mode'):
                if wanted.get(key)!=actual.get(key):raise InstallerError('Existing host differs: '+name+'.'+key)
        else:result['hosts'][name]=host
    return result


def target_identity(add,observation):
    name=add.args.host
    if observation['classification']=='registered':
        return (add.binding['machines'][name],add.binding['hosts'][name]['compute_uuid'])
    return (observation['facts']['machine_sha256'],add.identity['compute_uuid'])


@contextmanager
def locked(add):
    with add.c.locked():
        stop=threading.Event()
        def beat():
            while not stop.wait(20):
                try:add.c.alive()
                except Exception:return
        thread=threading.Thread(target=beat,daemon=True);thread.start()
        try:
            add.c.snapshot()
            add.c.remote(CONTROLLER_RECORD_SOURCE+'''import json
p=Path('/var/lib/spvm-host-budget-worker/addition-coordinator.json')
if p.exists() or p.is_symlink():
 if json.loads(private(p).read_bytes()).get('status')!='COMPUTE_ADDED':raise ValueError('unfinished host addition requires explicit review')
print(json.dumps({'status':'ADDITION_COORDINATOR_AVAILABLE'}))
''')
            yield
        finally:stop.set();thread.join(timeout=25)


class Batch:
    def __init__(self,args,output,*,addition=None):
        if addition is None:
            from openstack_add_manager import Addition
            addition=Addition
        self.addition=addition;self.args=args;self.output=Path(output)
        self.source,self.hosts,self.source_digest=host_file(args.config,args.host)
        from openstack_cloud_context import Cloud
        self.cloud=Cloud(args,self.output).receive();args.cloud_context=self.cloud
        self.base=self.cloud.base
        self.origin,self.active=self.cloud.workspace,self.cloud.config
        self.driver,self.backend_path=self.cloud.driver,self.cloud.backend_path
        self.manifest_sha=file_hash(self.driver/'manifest.json')
        self.expected=self.origin;self.scans={};self.identities={}
        self.binding=read_document(self.origin/'compute-binding.json')
        self.network=read_document(self.base/'network.json')
        self.initial=portable(yaml.safe_load(self.active.read_bytes()))
        # Validate the whole proposed set before any host is contacted.
        combined=intent(self.initial,self.hosts);combined['driver']={'directory':str(self.driver)}
        write(self.output/'validation.yaml',combined)
        from openstack_kolla_existing import backend
        backend(self.backend_path).deployment_config(str(self.output/'validation.yaml'),transport=False)
        self.pins={str(self.source):self.source_digest,str(self.driver/'manifest.json'):self.manifest_sha,
            str(Path(args.environment)/'environment.json'):file_hash(Path(args.environment)/'environment.json'),
            str(self.base/'network.json'):file_hash(self.base/'network.json'),
            str(self.origin/'compute-binding.json'):file_hash(self.origin/'compute-binding.json'),
            **{str(p):file_hash(p) for p in Path(__file__).parent.glob('*.py')}}
        self.record={'schema_version':1,'status':'SCANNING','hosts':{n:{'status':'PENDING'} for n in self.hosts},
            'done':[],'inflight':None,'guests_changed':False,'inputs':self.pins}
        self.save()

    def save(self):write(self.output/'batch.json',self.record)

    def unchanged(self):
        if any(file_hash(p)!=sha for p,sha in self.pins.items()):raise InstallerError('Approved host YAML, kit, environment or installer source changed.')
        self.cloud.unchanged();workspace=self.cloud.workspace
        if workspace!=self.expected:raise InstallerError('Active cloud lineage changed outside this batch; stop and review.')

    def candidate(self,name,phase):
        self.unchanged();workspace,config=self.cloud.workspace,self.cloud.config
        active=portable(yaml.safe_load(config.read_bytes()))
        raw=intent(active,{name:self.hosts[name]});raw['driver']={'directory':str(self.driver)}
        raw['installation']={'guest_cidr':self.network['guest_cidr'],
            'region':self.binding['bootstrap_runtime']['region_name'],'allow_debug':self.hosts[name].get('allow_debug',False)}
        directory=self.output/'hosts'/name/phase;directory.mkdir(mode=0o700,parents=True)
        path=directory/'requested.yaml';write_yaml(path,raw)
        args=R(**vars(self.args));args.host=name;args.config=str(path);args.output=str(directory)
        return self.addition(args)

    def scan(self):
        identities=set();computes=set()
        for name in self.hosts:
            self.record['inflight']=name;self.record['hosts'][name]['status']='SCANNING';self.save()
            add=self.candidate(name,'scan')
            with locked(add):observation=add.observe()
            for p,sha in add.i.inputs.items():
                if Path(p).is_relative_to(self.output):continue
                if p in self.pins and self.pins[p]!=sha:raise InstallerError('Common input changed between host checks.')
                self.pins[p]=sha
            identity=target_identity(add,observation)
            if identity[0] in identities or identity[1] in computes:raise InstallerError('Selected hosts duplicate a physical machine or native compute identity: '+name)
            identities.add(identity[0]);computes.add(identity[1]);self.identities[name]=identity
            self.scans[name]=observation;write(Path(add.output)/'observation.json',observation)
            self.record['hosts'][name]={'status':'CHECKED','classification':observation['classification'],'identity':identity}
            self.save()
        self.unchanged();self.record.update(status='AWAITING_APPROVAL',inflight=None,created_at=int(time.time()))
        self.record['plan_sha256']=digest({'hosts':self.record['hosts'],'inputs':self.pins,'workspace':str(self.origin)})
        self.save()

    def apply(self):
        self.unchanged()
        if not 0<=time.time()-self.record['created_at']<=900:raise InstallerError('Batch approval expired; no apply started.')
        self.record['status']='ADDING_COMPUTES';self.save()
        for name in self.hosts:
            self.record['inflight']=name;self.record['hosts'][name]['status']='APPLYING';self.save()
            add=self.candidate(name,'apply')
            with locked(add):
                fresh=add.observe();before=self.scans[name]
                if target_identity(add,fresh)!=self.identities[name] or fresh['classification']!=before['classification']:
                    raise InstallerError(name+': target identity/classification changed after approval.')
                if fresh['classification']!='registered':
                    if fresh['facts']!=before['facts'] or fresh['controller']!=before['controller'] or fresh['cloud']['servers']!=before['cloud']['servers']:
                        raise InstallerError(name+': target, controller lifecycle or cloud workload changed after approval.')
                plan={'kind':'add-compute-v1','host':name,'created_at':int(time.time()),'classification':fresh['classification'],
                    'observation':fresh,'inputs':{**add.i.inputs,str(add.old_file):file_hash(add.old_file)},
                    'no_global_deploy':True,'no_bootstrap':True,'guests_changed':False}
                plan['plan_sha256']=digest(plan);write(Path(add.output)/'add-compute-plan.json',plan)
                self.unchanged();result=add.apply(plan)
            if result['status']=='COMPUTE_ADDED':self.expected=Path(result['workspace'])
            elif result['status']!='ALREADY_ADDED_VERIFIED':raise InstallerError('Unexpected compute completion status.')
            self.unchanged();self.record['hosts'][name].update(status=result['status'],output=str(add.output))
            self.record['done'].append(name);self.record['inflight']=None;self.save()
        self.record.update(status='COMPUTES_ADDED',completed_at=int(time.time()));self.save()
        return self.record


def write_yaml(path,value):
    with Path(path).open('x') as stream:
        os.fchmod(stream.fileno(),0o600);yaml.safe_dump(value,stream,sort_keys=False)


def main(argv=None):
    args=parser().parse_args(argv);output=Path(args.output);batch=None;owned=False
    try:
        if os.geteuid()!=0:raise InstallerError('Run add-compute with sudo on the management host.')
        protected_parent(output.parent)
        if output.exists() or output.is_symlink():raise InstallerError('Batch output already exists; inspect its journals. No overwrite, resume or automatic replay.')
        output.mkdir(mode=0o700);owned=True
        with deployment_lock():
            batch=Batch(args,output);batch.scan()
            print('Cloud: '+batch.cloud.pointer['cloud_id']+' · '+batch.initial['deployment_id']+' · '+batch.cloud.identity['api_endpoint'],flush=True)
            print(json.dumps({'status':'ADD_COMPUTE_BATCH_PLAN_READY','hosts':batch.record['hosts'],'plan_sha256':batch.record['plan_sha256']}),flush=True)
            if args.check:batch.record['status']='CHECKED';batch.save();return 0
            print('All selected hosts passed checks. Add sequentially; stop at the first failure. Completed hosts are not rolled back.',flush=True)
            if not args.yes:
                if not sys.stdin.isatty():raise InstallerError('Explicit approval required: use an interactive terminal or --yes after checking the plan.')
                print('Type '+batch.initial['deployment_id']+' to approve: ',end='',flush=True)
                if sys.stdin.readline().strip()!=batch.initial['deployment_id']:raise InstallerError('Batch was not approved; no host apply started.')
            result=batch.apply();print(json.dumps({'status':result['status'],'hosts':result['hosts'],'guests_changed':False}));return 0
    except Exception as error:
        if owned:
            write(output/'failure.json',{'type':type(error).__name__,'sites':[(Path(f.filename).name,f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)[-8:]]})
            if batch is not None:
                phase='NEEDS_REVIEW' if batch.record['status']=='ADDING_COMPUTES' else 'CHECK_FAILED'
                batch.record['status']=phase
                if batch.record['inflight'] is not None:batch.record['hosts'][batch.record['inflight']]['status']='FAILED'
                batch.save()
        print('ERROR: '+(str(error) if isinstance(error,InstallerError) else 'add-compute stopped ('+type(error).__name__+'); inspect private batch/child journals. No automatic retry.'),file=sys.stderr)
        return 2
