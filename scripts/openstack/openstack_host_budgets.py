"""Root-delegated budget worker. Only explicit administrator requests mutate hosts."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

import yaml
from openstack_container_engine import write
import openstack_workflow as workflow
from openstack_host_pools import PoolWorker


def policy(filename):
    path=Path(filename)
    for p in (path,*path.parents):
        s=p.lstat()
        if p.is_symlink() or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('root-owned budget policy required')
    value=json.loads(path.read_text())
    fields={'schema_version','config','controller','controller_python','openrc','known_hosts',
            'project_id','admin_user_ids','reader_user_ids','automatic'}
    if type(value) is not dict or set(value)!=fields or type(value['schema_version']) is not int or value['schema_version']!=1:
        raise ValueError('exact budget worker delegation required')
    for key in ('config','controller_python','openrc','known_hosts'):
        workflow.literal_path(value[key],canonical=False)
    from nova_spvm.authority import user_ids
    user_ids(value['admin_user_ids']);user_ids(value['reader_user_ids'])
    if type(value['automatic']) is not dict or not 1<=len(value['automatic'])<=256:
        raise ValueError('explicit per-host automatic baselines required')
    for defaults in value['automatic'].values():
        if type(defaults) is not dict or set(defaults)!={'reserved_cpus','reserved_memory_mb','pools'}:
            raise ValueError('exact automatic host defaults required')
        if any(type(defaults[k]) is not int or defaults[k]<1 for k in ('reserved_cpus','reserved_memory_mb')):
            raise ValueError('positive host reserves required')
        from openstack_spvm.host_budgets import normalize
        normalize({'request_id':'11111111-1111-4111-8111-111111111111','host':'baseline',
            'generation':'0'*64,'budget':{'mode':'manual','vcpus':1,'memory_mb':1,'pools':defaults['pools']}})
    return value


class Worker(PoolWorker):
    def __init__(self,config,path,module,connection,root=Path('/var/lib/spvm-host-budget-worker'),policy_path=None):
        from openstack_spvm import host_budgets
        self.model=host_budgets;self.config=config;self.path=Path(path);self.module=module;self.c=connection
        self.policy_path=Path(policy_path) if policy_path else None
        self.root=root;root.mkdir(mode=0o700,parents=True,exist_ok=True)
        from openstack_skyline_runtime import trusted
        trusted(root)
        if root.stat().st_mode&0o077:raise ValueError('private worker journal directory required')
        self.cfg=module.deployment_config(config['config'])
        if connection.session.get_project_id()!=config['project_id']:raise ValueError('worker auth project differs')
        host_budgets.admin(connection)
        self.publisher=connection.session.get_user_id()
        if self.publisher not in config['reader_user_ids']:
            raise ValueError('worker identity is not an approved request reader')
        if not set(config['automatic'])<=set(self.cfg['hosts']):raise ValueError('unknown delegated host')
        from compute_workflow import Backend
        self.runner=Backend(self.cfg,config['controller'],config['controller_python'],config['openrc'],root/'observations')

    def local(self,name,action,**data):
        source=(self.path.parent/'host_budget_host.py').read_text()
        body='ACTION='+repr(action)+'\nDATA='+repr(data)+'\n'+source+'''
host=Host()
result=host.observe() if ACTION=='observe' else host.apply(DATA['request_id'],DATA['wanted'],DATA['before'])
print(json.dumps(result))
'''
        return self.runner.remote(name,body)

    def cloud_empty(self,name,snapshot,*,allow_down=False):
        hostname=self.cfg['hosts'][name].get('inventory_name',name);blockers=[]
        from openstack_spvm.cluster_flow import bounded
        services=[s for s in bounded(self.c.compute.services(binary='nova-compute')) if s.host==hostname]
        if len(services)!=1 or not allow_down and services[0].state!='up':blockers.append('COMPUTE_NOT_UP')
        for server in bounded(self.c.compute.servers(all_projects=True,details=True)):
            if getattr(server,'compute_host',None)==hostname:blockers.append('NOVA_INSTANCES_PRESENT')
            if server.status=='BUILD' or getattr(server,'task_state',None):blockers.append('INFLIGHT_NOVA_REQUEST')
        root=snapshot['compute_uuid']
        response=self.c.placement.get('/resource_providers?in_tree='+root,microversion='1.39');response.raise_for_status()
        tree=response.json()['resource_providers']
        if type(tree) is not list or len(tree)>256:raise ValueError('bounded provider tree required')
        if not any(p['uuid']==root for p in tree):blockers.append('PLACEMENT_PROVIDER_MISSING')
        for provider in tree:
            response=self.c.placement.get('/resource_providers/'+provider['uuid']+'/allocations',microversion='1.39');response.raise_for_status()
            if response.json()['allocations']:blockers.append('PLACEMENT_ALLOCATIONS_PRESENT')
        from accounting_cloud import PCI_CODE
        code='CODE='+repr(PCI_CODE)+'\nHOST='+repr(hostname)+'\n'+'''import subprocess,json
p=subprocess.run(['docker','exec','-i','nova_conductor','/var/lib/kolla/venv/bin/python','-c',CODE],
 input=json.dumps([HOST]),capture_output=True,text=True,timeout=90)
if p.returncode:raise ValueError('PCI observation unavailable')
print(p.stdout)
'''
        pci=self.runner.remote(self.config['controller'],code)[hostname]
        if pci['compute_uuid']!=root:raise ValueError('PCI compute identity differs')
        if any(p['status'] not in ('available','deleted') or p.get('instance_uuid') for p in pci['devices']):
            blockers.append('PCI_RESERVATIONS_PRESENT')
        return sorted(set(blockers)),services[0] if len(services)==1 else None

    def catalog(self,name,*,request_id=None):
        before=self.local(name,'observe');extra,service=self.cloud_empty(name,before)
        defaults=self.config['automatic'][name];hardware=before['hardware']
        automatic={'mode':'auto','vcpus':hardware['logical_cpus']-defaults['reserved_cpus'],
            'memory_mb':hardware['memory_mb']-defaults['reserved_memory_mb'],'pools':[]}
        baseline={p['id']:p for p in defaults['pools']}
        for current in before['current']['pools']:
            p=baseline[current['id']]
            actual=next(v for v in hardware['pools'] if v['id']==p['id'])
            automatic['pools'].append({**p,'budget_gib':min(p['budget_gib'],max(0,actual['available_gib']-p['reserve_gib']))})
        current=before['current']
        if {**current,'mode':'auto'}==automatic:current={**current,'mode':'auto'}
        host=self.cfg['hosts'][name].get('inventory_name',name)
        generation=self.model.digest({'host':host,'compute_uuid':before['compute_uuid'],
            'machine':before['machine_sha256'],'current':current,'config_files':before['files']})
        unresolved=[]
        for journal in self.root.glob('*/transaction.json'):
            if journal.parent.name==request_id:continue
            transaction=json.loads(journal.read_text())
            if transaction.get('host') in (None,host) and transaction.get('status') not in ('SUCCEEDED','REFUSED'):
                unresolved.append('BUDGET_TRANSACTION_REVIEW_REQUIRED')
        return {'schema_version':1,'project_id':self.config['project_id'],'host':host,'generation':generation,
            'current':current,'automatic':automatic,'hardware':hardware,
            'blockers':sorted(set(before['blockers']+extra+unresolved))},before,service

    def publish(self,name,payload):
        from openstack_spvm import cluster_flow,secrets
        old=cluster_flow.record(self.c.key_manager,name,[self.publisher])
        if old and cluster_flow.parse_json(old[1])==payload:return
        if old:self.c.key_manager.delete_secret(old[0],ignore_missing=False)
        secrets.put_private(self.c.key_manager,name,json.dumps(payload,sort_keys=True),
            list(set(self.config['admin_user_ids']+self.config['reader_user_ids']+[self.publisher])))

    def sync(self):
        for name in self.config['automatic']:
            catalog,before,_=self.catalog(name)
            self.publish(self.model.PREFIX+before['compute_uuid']+'-catalog',catalog)

    def persist(self,name,wanted,before):
        # Canonical intent changes as well. Old bound workspaces now fail their
        # digest check instead of silently overwriting budgets on reconfigure.
        filename=Path(self.config['config']);old=filename.read_bytes()
        (self.active/'deployment.before.yaml').write_bytes(old)
        cfg=yaml.safe_load(old)
        cfg['hosts'][name]['compute_resources']={'reserved_cpus':before['hardware']['logical_cpus']-wanted['vcpus'],
            'reserved_memory_mb':before['hardware']['memory_mb']-wanted['memory_mb']}
        by_id={p['id']:p for p in wanted['pools']}
        for p in cfg['hosts'][name]['storage_pools']:
            if not p.get('enabled',True):continue
            p.update({k:by_id[p['id']][k] for k in ('budget_gib','reserve_gib')})
        # Validate the complete candidate before replacing canonical intent.
        candidate=self.active/'deployment.after.yaml'
        candidate.write_text(yaml.safe_dump(cfg,sort_keys=False))
        self.module.deployment_config(candidate)
        if filename.read_bytes()!=old:raise ValueError('canonical deployment changed during budget application')
        from runtime_maintenance import atomic
        atomic(filename,yaml.safe_dump(cfg,sort_keys=False).encode(),filename.stat().st_mode&0o777)

    def verify(self,before,wanted):
        from nova_spvm.storage import pool_uuid,RESOURCE
        root=before['compute_uuid'];deadline=time.monotonic()+180
        while True:
            response=self.c.placement.get('/resource_providers/'+root+'/inventories',microversion='1.39');response.raise_for_status()
            inv=response.json()['inventories']
            matched=all(k in inv and inv[k]['total']-inv[k].get('reserved',0)==wanted[field] and inv[k]['allocation_ratio']==1.0
                for k,field in (('VCPU','vcpus'),('MEMORY_MB','memory_mb')))
            for pool in wanted['pools']:
                response=self.c.placement.get('/resource_providers/'+pool_uuid(root,pool['id'])+'/inventories',microversion='1.39')
                # A restarted compute publishes newly configured child providers
                # asynchronously. Only their absence is a convergence condition;
                # authentication, transport and other API failures remain errors.
                if response.status_code==404:
                    matched=False;continue
                response.raise_for_status()
                inventory=response.json()['inventories'].get(RESOURCE)
                if inventory is None:
                    matched=False;continue
                matched=matched and inventory['total']-inventory.get('reserved',0)==pool['budget_gib'] and inventory['allocation_ratio']==1.0
            if matched:return
            if time.monotonic()>deadline:raise ValueError('live inventories did not converge; scheduling remains disabled')
            time.sleep(5)

    def process(self,entry):
        from openstack_spvm import cluster_flow,secret_reader
        metadata=secret_reader.read_json(self.c.key_manager,'/secrets/'+entry)
        creator=metadata.get('creator_id')
        if creator not in self.config['admin_user_ids']:return
        name=metadata['name'];request_id=name.removeprefix(self.model.PREFIX+'request-')
        secret_reader.canonical_uuid(request_id)
        saved=cluster_flow.record(self.c.key_manager,name,[creator]);body=cluster_flow.parse_json(saved[1])
        if (type(body) is not dict or set(body)!={'schema_version','project_id','user_id','request_id','host','generation','budget'}
                or type(body['schema_version']) is not int or body['schema_version']!=1
                or body['request_id']!=request_id or body['project_id']!=self.config['project_id']
                or body['user_id']!=creator):raise ValueError('request creator/project/identity differs')
        self.model.normalize({k:body[k] for k in ('request_id','host','generation','budget')})
        result_name=self.model.PREFIX+'result-'+request_id
        if cluster_flow.record(self.c.key_manager,result_name,[self.publisher]):return
        self.active=self.root/request_id
        if self.active.exists():
            self.publish(result_name,{'request_id':request_id,'project_id':self.config['project_id'],'status':'REVIEW_REQUIRED',
                'message':'A previous attempt was interrupted. No automatic replay; inspect the private journal.'});return
        self.active.mkdir(mode=0o700)
        record={'request_id':request_id,'host':body['host'],'status':'PROCESSING','phase':'PREFLIGHT'}
        journal=self.active/'transaction.json';write(journal,record)
        disabled=False
        try:
            matches=[n for n in self.config['automatic'] if self.cfg['hosts'][n].get('inventory_name',n)==body['host']]
            if len(matches)!=1:raise ValueError('host not delegated')
            host=matches[0];catalog,before,service=self.catalog(host,request_id=request_id)
            wanted=self.model.resolve({k:body[k] for k in ('request_id','host','generation','budget')},catalog)
            if catalog['blockers']:raise ValueError('Budgets can only be changed on an empty host. '+', '.join(catalog['blockers']))
            record['phase']='DISABLING_SCHEDULING';write(journal,record)
            was_enabled=service.status=='enabled'
            # A lost API acknowledgement is ambiguous: never report a safe
            # preflight refusal after scheduling quarantine was attempted.
            disabled=True
            if was_enabled:self.c.compute.disable_service(service,disabled_reason='SPVM host budget '+request_id)
            # Recheck after quarantine; no cached project-local view is sufficient.
            again=self.local(host,'observe');blockers,quarantined=self.cloud_empty(host,again)
            if again!=before or blockers or quarantined is None or quarantined.status!='disabled':
                raise ValueError('Host changed or scheduling quarantine was not confirmed')
            record['phase']='APPLYING';write(journal,record)
            self.local(host,'apply',request_id=request_id,wanted=wanted,before=before)
            record['phase']='VERIFYING';write(journal,record)
            self.verify(before,wanted)
            self.persist(host,wanted,before)
            record['phase']='RESTORING_SCHEDULING';write(journal,record)
            final=self.local(host,'observe');blockers,current_service=self.cloud_empty(host,final)
            if blockers or current_service is None or current_service.status!='disabled':
                raise ValueError('Host changed before scheduling restore')
            if final['current']!={**wanted,'mode':'manual'}:
                raise ValueError('host configuration does not match verified budget')
            if was_enabled:self.c.compute.enable_service(current_service)
            result={'request_id':request_id,'project_id':self.config['project_id'],'status':'SUCCEEDED',
                'message':'Budget applied and inventories verified. No VM lifecycle action. Rebind installer workspace before maintenance.'}
        except Exception:
            result={'request_id':request_id,'project_id':self.config['project_id'],
                'status':'REVIEW_REQUIRED' if disabled else 'REFUSED',
                'message':('Scheduling quarantine was attempted; verify it remains disabled and inspect the private transaction. No automatic rollback or retry.'
                           if disabled else 'Budgets can only be changed on an empty host. Host may also be stale, unavailable or not delegated; refresh and inspect resources.')}
        record.update(result);write(journal,record);self.publish(result_name,result)


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['sync-host-budgets','process-host-budgets'])
    parser.add_argument('--policy',required=True)
    parser.add_argument('--expected-worker-user-id')
    args=parser.parse_args(argv)
    if os.geteuid()!=0:raise ValueError('root budget worker required')
    workflow.existing.backend(path)
    config=policy(args.policy)
    with workflow.context(path,config) as module:
        from openstack_spvm import cluster_cleanup
        import openstack
        connection=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=30)
        try:
            if args.expected_worker_user_id and connection.session.get_user_id()!=args.expected_worker_user_id:
                raise ValueError('authenticated worker differs from activated publisher identity')
            worker=Worker(config,path,module,connection,policy_path=args.policy)
            fd=os.open(worker.root/'worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                if args.action=='process-host-budgets':
                    entries=cluster_cleanup.listing(connection.key_manager,worker.model.PREFIX+'request-')
                    for entry in entries:worker.process(entry)
                    from openstack_spvm import host_pools
                    for entry in cluster_cleanup.listing(connection.key_manager,host_pools.PREFIX+'request-'):
                        worker.process_pool(entry)
                worker.sync()
                worker.sync_pools()
            finally:os.close(fd)
        finally:connection.close()
    print(json.dumps({'status':'HOST_BUDGET_WORKER_COMPLETE'}));return 0
