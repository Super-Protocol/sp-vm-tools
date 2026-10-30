"""Root-local controller command to retire one reachable empty compute-only host."""
import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback

import yaml
from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_kolla_workspace as workspace


def context(path,args):
    from openstack_kolla_existing import backend
    from openstack_host_budgets import policy
    module=backend(path)
    from compute_removal import Backend
    delegation=policy(args.policy)
    original=module.ssh_arguments
    def ssh(host):
        values=original(host)
        values[1:1]=['-F','/dev/null','-o','ForwardAgent=no','-o','UserKnownHostsFile='+delegation['known_hosts']]
        return values
    module.ssh_arguments=ssh
    cfg=module.deployment_config(delegation['config'])
    if (os.geteuid()!=0 or cfg['hosts'][delegation['controller']].get('inventory_name',delegation['controller'])!=socket.gethostname()
            or args.host not in delegation['automatic'] or cfg['hosts'][args.host]['roles']!=['compute']):
        raise ValueError('root-local controller and delegated compute-only target required')
    receipt=workspace.check(args.workspace)
    binding=json.loads(read(Path(args.workspace)/'compute-binding.json'))
    body={k:v for k,v in binding.items() if k!='binding_sha256'}
    if (receipt['config_sha256']!=digest(cfg) or binding['config_sha256']!=digest(cfg)
            or binding['binding_sha256']!=digest(body) or receipt['compute_binding']!=digest(body)
            or set(binding['hosts'])!={n for n,h in cfg['hosts'].items() if 'compute' in h['roles']}):
        raise ValueError('current config and complete bound workspace required')
    runner=Backend(cfg,delegation['controller'],delegation['controller_python'],delegation['openrc'],
                   Path(args.output)/'observations',known_hosts=delegation['known_hosts'])
    return module,delegation,cfg,receipt,binding,runner


def network(runner,cfg,host,*,approved=None,output=None):
    """Remove exactly the target's Geneve and management probe addresses."""
    from openstack_compute_onboard import remote_tools
    deployment=cfg['deployment_id'];result={}
    for name,peer in cfg['hosts'].items():
        if name==host or not set(peer['roles']) & {'compute','network'}:continue
        data={'deployment':deployment,'target':cfg['hosts'][host].get('inventory_name',host),
              'expected':None if approved is None else approved[name],
              'output':None if output is None else '/var/lib/spvm-installer/'+deployment+'/remove-'+digest(str(output))[:24]}
        operation=r'''
from openstack_peer_network import RemovingPeer
from openstack_container_engine import file_hash
import json,socket
peer=RemovingPeer();entries=[]
# The target's addresses are bound from its owned management/Geneve policies,
# not guessed from the public SSH address.
for kind,field,folder,stem,runtime in (
 ('geneve','peers','/etc/spvm-network','spvm-geneve-','/usr/local/libexec/spvm-geneve-guard.py'),
 ('nat','probe_sources','/etc/spvm-host-network','spvm-nat-','/usr/local/libexec/spvm-host-network.py')):
 path=folder+'/'+stem+DATA['deployment']+'.json'
 if not peer.trusted(path).exists():continue
 old=json.loads(peer.read(path));address=DATA['addresses'][kind]
 new={**old,field:sorted(set(old[field])-{address})}
 entries.append({'policy':new,'path':path,'kind':kind,'runtime':runtime,
                 'new_address':address,'runtime_sha256':file_hash(runtime)})
machine=__import__('hashlib').sha256(peer.read('/etc/machine-id').strip()).hexdigest()
before=peer.read('/etc/hosts');lines=[]
for line in before.decode().splitlines(keepends=True):
 plain,mark,comment=line.partition('#');fields=plain.split()
 if DATA['target'] not in fields[1:]:lines.append(line);continue
 if fields[0]!=DATA['addresses']['nat']:raise ValueError('target hostname address mismatch')
 remaining=[n for n in fields[1:] if n!=DATA['target']]
 if remaining:lines.append(fields[0]+' '+' '.join(remaining)+(' #'+comment.rstrip() if mark else '')+'\n')
after=''.join(lines).encode()
hosts={'before':__import__('hashlib').sha256(before).hexdigest(),'after':__import__('hashlib').sha256(after).hexdigest()}
if DATA['expected'] is None:result={'network':peer.observe(entries,machine),'hosts':hosts,'machine':machine}
else:
 if hosts!=DATA['expected']['hosts'] or machine!=DATA['expected']['machine']:raise ValueError('peer identity or hostnames changed')
 result=peer.apply(entries,machine,DATA['expected']['network'],DATA['output'])
 if peer.read('/etc/hosts')!=before:raise ValueError('concurrent hosts edit')
 if after!=before:
  from pathlib import Path
  peer.trusted(DATA['output']+'/hosts.before').write_bytes(before)
  peer.put('/etc/hosts',after)
 result={'network':result,'hosts_removed':True,'machine':machine}
'''
        if approved is None:
            code='DEPLOYMENT='+repr(deployment)+'\n'+r'''
import json
from pathlib import Path
g=json.load(open('/etc/spvm-network/spvm-geneve-'+DEPLOYMENT+'.json'))
m=json.load(open('/etc/spvm-host-network/spvm-management-'+DEPLOYMENT+'.json'))
print(json.dumps({'geneve':g['local_ip'],'nat':m['management_address'].split('/')[0]}))
'''
            addresses=runner.remote(host,code)
        else:addresses=approved['_addresses']
        data['addresses']=addresses
        result[name]=remote_tools(runner,name,('openstack_peer_network.py','openstack_container_engine.py'),data,operation)
        result['_addresses']=addresses
    return result


def stage_workspace(args,cfg,receipt,binding):
    origin=Path(args.workspace);output=Path(args.output)/'remaining-workspace'
    new=copy.deepcopy(cfg);new['hosts'].pop(args.host)
    output.mkdir(mode=0o700)
    alias=cfg['hosts'][args.host].get('inventory_name',args.host)
    for name in receipt['files']:
        if name in ('hosts.ini','compute-binding.json') or name.startswith('kolla/config/nova/'+alias+'/'):
            continue
        workspace.private_file(output/name,read(origin/name))
    # Remove only exact inventory membership/declaration, retaining upstream groups.
    lines=read(origin/'hosts.ini').decode().splitlines(keepends=True)
    inventory=''.join(line for line in lines if not line.strip() or line.strip().startswith(('#','['))
                      or line.split()[0]!=alias)
    workspace.private_file(output/'hosts.ini',inventory)
    body={k:v for k,v in binding.items() if k!='binding_sha256'}
    body['hosts']={k:v for k,v in body['hosts'].items() if k!=args.host}
    body['machines']={k:v for k,v in body['machines'].items() if k!=args.host}
    body['config_sha256']=digest(new);body['source_workspace_sha256']=digest(receipt)
    body['binding_sha256']=digest(body);write(output/'compute-binding.json',body)
    globals_=yaml.safe_load(read(output/'kolla/globals.yml'))
    for key in ('node_custom_config','kolla_certificates_dir','kolla_internal_fqdn_cert','kolla_external_fqdn_cert'):
        globals_[key]=globals_[key].replace(str(origin),str(output))
    write(output/'kolla/globals.yml',globals_)
    write(output/'journal.json',{'status':receipt['status']})
    write(output/'workspace.json',{**receipt,'config_sha256':digest(new),
          'compute_binding':body['binding_sha256'],'files':workspace.file_set(output)})
    workspace.check(output)
    return new,output


def catalogs(runner,delegation,plan,remove=False):
    code=r'''
import json,openstack
from openstack_spvm import cluster_flow as flow
c=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=30)
try:
 if c.session.get_project_id()!=PROJECT:raise ValueError('catalog project differs')
 result={}
 for prefix in ('spvm-host-budget-','spvm-host-pool-','spvm-host-gpu-'):
  name=prefix+ROOT+'-catalog';saved=flow.record(c.key_manager,name,PUBLISHERS)
  if saved is None:continue
  payload=flow.parse_json(saved[1])
  if payload['host']!=HOST or payload['project_id']!=PROJECT:raise ValueError('catalog identity mismatch')
  result[name]=saved[0]
 if REMOVE:
  if result!=EXPECTED:raise ValueError('host catalogs changed during removal')
  for identifier in result.values():
   response=c.key_manager.delete('/secrets/'+identifier);response.raise_for_status()
 print(json.dumps(result))
finally:c.close()
'''
    values={'PROJECT':delegation['project_id'],'PUBLISHERS':delegation['reader_user_ids'],
            'ROOT':plan['compute_uuid'],'HOST':plan['hostname'],'REMOVE':remove,'EXPECTED':plan.get('catalogs')}
    return runner.remote(delegation['controller'],''.join(k+'='+repr(v)+'\n' for k,v in values.items())+code,controller=True,auth=True)


def refresh_catalogs(runner,delegation,path,policy_path):
    # Caller holds the existing worker lock. Only observation/publication: never
    # process queued administrator mutations while retiring a compute.
    code='TOOLS='+repr(str(Path(__file__).parent))+'\nBACKEND='+repr(str(path))+'\nPOLICY='+repr(str(policy_path))+'\n'+r'''
import sys,json,subprocess,openstack
sys.path.insert(0,TOOLS)
import openstack_workflow as workflow
from openstack_host_budgets import Worker,policy
config=policy(POLICY)
with workflow.context(BACKEND,config) as module:
 c=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=30)
 try:
  worker=Worker(config,BACKEND,module,c,policy_path=POLICY)
  worker.sync();worker.sync_pools();worker.sync_gpus()
 finally:c.close()
p=subprocess.run(['systemctl','start','spvm-web-capacity.service'],capture_output=True,timeout=120)
if p.returncode:raise ValueError('capacity collection failed')
print(json.dumps({'status':'REMAINING_HOST_CATALOGS_REFRESHED','hosts':sorted(config['automatic'])}))
'''
    return runner.remote(delegation['controller'],code,controller=True,auth=True)


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan-remove-compute','remove-compute'))
    for name in ('policy','workspace','host','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256');parser.add_argument('--confirm-host')
    args=parser.parse_args(argv);output=Path(args.output)
    try:
        from openstack_skyline_runtime import trusted
        trusted(output.parent)
        if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical private output required')
        if args.action=='plan-remove-compute':output.mkdir(mode=0o700)
        trusted(output)
        if output.stat().st_mode&0o077:raise ValueError('private output required')
        if args.action=='remove-compute' and (output/'transaction.json').exists():
            record=json.loads(read(output/'transaction.json'))
            plan=json.loads(read(output/'remove-compute-plan.json'))
            if (record.get('status')=='COMPUTE_REMOVED' and record.get('host')==args.host
                    and args.confirm_host==args.host and record.get('plan_sha256')==args.plan_sha256
                    and plan.get('plan_sha256')==args.plan_sha256
                    and digest({k:v for k,v in plan.items() if k!='plan_sha256'})==args.plan_sha256):
                print(json.dumps({**record,'recorded_completion':True}));return 0
        module,delegation,cfg,receipt,binding,runner=context(path,args)
        # Same root-local lock used by budget/pool/GPU processing. No timer stop.
        fd=os.open('/var/lib/spvm-host-budget-worker/worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            st=os.fstat(fd)
            if st.st_uid!=0 or st.st_nlink!=1 or st.st_mode&0o777!=0o600:raise ValueError('untrusted worker lock')
            deadline=time.monotonic()+120
            while True:
                try:
                    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise ValueError('host resource worker is busy; retry later')
                    time.sleep(0.25)
            return execute(args,output,module,delegation,cfg,receipt,binding,runner)
        finally:os.close(fd)
    except Exception as error:
        print('ERROR: compute removal refused/incomplete ('+type(error).__name__+'). Review private journal; do not re-enable or replay ambiguous steps.',file=sys.stderr)
        if output.exists() and not output.is_symlink() and output.stat().st_uid==os.getuid() and not output.stat().st_mode&0o077:
            write(output/'failure.json',{'type':type(error).__name__,'sites':[
                (Path(f.filename).name,f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)[-6:]]})
        return 2


def execute(args,output,module,delegation,cfg,receipt,binding,runner):
    planfile=output/'remove-compute-plan.json';journal=output/'transaction.json'
    inputs={str(Path(args.policy)):file_hash(args.policy),str(Path(delegation['config'])):file_hash(delegation['config']),
            str(Path(delegation['known_hosts'])):file_hash(delegation['known_hosts'])}
    source={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')}
    if args.action=='plan-remove-compute':
        plan=runner.plan(args.host)
        plan.update(inputs=inputs,installer_sources=source,workspace=str(Path(args.workspace)),workspace_receipt=digest(receipt))
        plan['network']=network(runner,cfg,args.host)
        plan['catalogs']=catalogs(runner,delegation,plan)
        plan.pop('plan_sha256');plan['plan_sha256']=digest(plan)
        write(planfile,plan)
        print(json.dumps({'status':'REMOVE_COMPUTE_PLAN_READY','host':args.host,
              'plan_sha256':plan['plan_sha256'],'hostname':plan['hostname'],
              'containers':list(plan['target']['containers']),'guests_changed':False}))
        return 0
    plan=json.loads(read(planfile));body={k:v for k,v in plan.items() if k!='plan_sha256'}
    if (args.confirm_host!=args.host or plan['host']!=args.host or plan['plan_sha256']!=args.plan_sha256
            or digest(body)!=args.plan_sha256 or plan['kind']!='remove-compute-v1'):
        raise ValueError('exact host and approved plan hash required')
    if journal.exists():
        record=json.loads(read(journal))
        if record.get('plan_sha256')==args.plan_sha256 and record.get('status')=='COMPUTE_REMOVED':
            runner.guard(plan,stopped=True,removed=True)
            print(json.dumps(record));return 0
        raise ValueError('unfinished transaction needs review; no automatic replay')
    if (not 0<=time.time()-plan['created_at']<=900 or inputs!=plan['inputs'] or source!=plan['installer_sources']
            or digest(receipt)!=plan['workspace_receipt'] or str(Path(args.workspace))!=plan['workspace']):
        raise ValueError('expired or changed removal inputs')
    record={'status':'REMOVING_COMPUTE','host':args.host,'plan_sha256':args.plan_sha256,'done':[],
            'inflight':None,'guests_changed':False}
    def step(name,fn):
        record['inflight']=name;write(journal,record)
        result=fn();write(output/(name+'-result.json'),result or {})
        record['done'].append(name);record['inflight']=None;write(journal,record)
        return result
    try:
        # Build and validate successor inputs before the first remote mutation.
        new,successor=stage_workspace(args,cfg,receipt,binding)
        write(output/'configuration-before.json',{'config':cfg,'policy':delegation})
        step('quarantine',lambda:runner.cloud(plan,'quarantine'))
        runner.guard(plan)
        step('stop',lambda:runner.target(args.host,'stop',plan['target']))
        runner.guard(plan,stopped=True)
        step('native-removal',lambda:runner.cloud(plan,'remove'))
        runner.guard(plan,stopped=True,removed=True)
        step('peer-network',lambda:network(runner,cfg,args.host,approved=plan['network'],output=output))
        # Retire delegation first: the worker must not re-publish removed catalogs.
        def configurations():
            policy={**delegation,'automatic':{n:v for n,v in delegation['automatic'].items() if n!=args.host}}
            write(Path(args.policy),policy);write(Path(delegation['config']),new)
            write(output/'remaining-deployment.yaml',new)
            write(Path(args.workspace)/'journal.json',{'status':'SUPERSEDED_COMPUTE_REMOVED',
                  'successor':str(successor),'plan_sha256':plan['plan_sha256']})
            return {'workspace':str(successor),'config':delegation['config'],'host_removed':args.host}
        step('configuration',configurations)
        step('catalogs',lambda:catalogs(runner,delegation,plan,True))
        step('refresh-catalogs',lambda:refresh_catalogs(runner,delegation,module.__file__,args.policy))
        if catalogs(runner,delegation,plan):raise ValueError('retired catalogs still visible')
        runner.guard(plan,stopped=True,removed=True)
        record.update(status='COMPUTE_REMOVED',workspace=str(successor),completed_at=int(time.time()))
        write(journal,record);print(json.dumps(record));return 0
    except BaseException:
        record['status']='NEEDS_REVIEW';write(journal,record);raise
