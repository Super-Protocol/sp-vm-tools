"""GPU discovery/label extension of the explicit administrator host worker."""
import json
import time
from pathlib import Path
from openstack_container_engine import write

PUBLIC=Path('/var/lib/spvm/gpu-catalog.json')


class GpuWorker:
    def gpu_local(self,name,action,**data):
        modules={n:(self.path.parent/(n+'.py')).read_text() for n in
                 ('host_budget_host','runtime_maintenance','gpu_discovery','host_gpu_host','host_gpu_native')}
        profiles=(self.path.parent.parent/'nova-spvm/nova_spvm/gpu_profiles.py').read_text()
        source='MODULES='+repr(modules)+'\nPROFILES='+repr(profiles)+'\nACTION='+repr(action)+'\nDATA='+repr(data)+'\n'+'''
import sys,types,json
package=types.ModuleType('nova_spvm');package.__path__=[];sys.modules['nova_spvm']=package
model=types.ModuleType('nova_spvm.gpu_profiles');sys.modules[model.__name__]=model
exec(PROFILES,model.__dict__);package.gpu_profiles=model
for name,body in MODULES.items():
 m=types.ModuleType(name);sys.modules[name]=m;exec(body,m.__dict__)
host=sys.modules['host_gpu_host'].Gpus()
if ACTION=='observe':result=host.observe_gpus()
elif ACTION=='change':result=host.change(DATA['before'],DATA['change'],idle=DATA['idle'])
else:
 actor=sys.modules['host_gpu_native'].Native()
 if ACTION=='plan-compute':
  changes=actor.compute_plan(DATA['before'],DATA['wanted'],DATA['aliases'])
  digest=sys.modules['host_budget_host'].digest
  result={'before':{p:digest(actor.read(p)) for p in changes},'after':{p:digest(b) for p,b in changes.items()}}
 elif ACTION=='stage-compute':result=actor.stage_compute(DATA['request_id'],DATA['before'],DATA['wanted'],DATA['aliases'])
 elif ACTION=='start-compute':result=actor.start_compute(DATA['request_id'],DATA['staged'])
 elif ACTION=='plan-controller':result=actor.plan_controller(DATA['target'],DATA['selected'],DATA['aliases'])
 elif ACTION=='apply-controller':result=actor.apply_controller(DATA['request_id'],DATA['target'],DATA['selected'],DATA['aliases'],DATA['plan'])
 else:raise ValueError('unsupported GPU transaction operation')
print(json.dumps(result))
'''
        return self.runner.remote(name,source,isolated=True)

    def gpu_names(self):
        path=self.root/'gpu-profile-names.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def gpu_catalog(self,name,*,request_id=None):
        from nova_spvm import gpu_profiles as gpu
        observed=self.gpu_local(name,'observe');names=self.gpu_names()
        state=self.local(name,'observe');blockers,service=self.cloud_empty(name,state)
        host=self.cfg['hosts'][name].get('inventory_name',name)
        unresolved=[]
        for path in self.root.glob('*/transaction.json'):
            if path.parent.name==request_id:continue
            transaction=json.loads(path.read_bytes())
            if transaction.get('host') in (None,host) and transaction.get('status') not in ('SUCCEEDED','REFUSED'):
                unresolved.append('HOST_TRANSACTION_REVIEW_REQUIRED')
        generation=gpu.digest({'host':host,'inventory':observed,'files':state['files'],'names':names})
        return {'schema_version':1,'project_id':self.config['project_id'],'host':host,'generation':generation,
                'inventory':gpu.catalog(observed['inventory'],names),
                'configuration':gpu.settings(observed['inventory']),
                'blockers':sorted(set(blockers+state['blockers']+observed['blockers']+unresolved))},observed,state,service

    def sync_gpus(self):
        from openstack_spvm import host_gpus as model
        from nova_spvm import gpu_profiles as gpu
        entries={};profiles={};names=self.gpu_names()
        observations=[self.gpu_catalog(name) for name in sorted(self.config['automatic'])]
        # Automatic names are recomputed across all hosts, not saved as manual
        # overrides or chosen from whichever host happens to be visited first.
        shared=gpu.profile_names([c['inventory'] for c,_,_,_ in observations],names)
        for catalog,observed,state,_ in observations:
            catalog['inventory']=gpu.catalog(observed['inventory'],shared)
            catalog['generation']=gpu.digest({'host':catalog['host'],'inventory':observed,'files':state['files'],'names':names})
            self.publish(model.PREFIX+state['compute_uuid']+'-catalog',catalog)
            inventory_name=next(n for n in self.config['automatic']
                if self.cfg['hosts'][n].get('inventory_name',n)==catalog['host'])
            launch=self.runner.remote(inventory_name,
                "import sys,json;sys.path[:0]=['/usr/local/libexec/spvm','/usr/local/libexec/nova-spvm'];from host_launch import report;print(json.dumps(report()))",isolated=True)
            entries[catalog['host']]={'inventory':observed['inventory'],'assignments':catalog['inventory']['assignments'],'launch':launch,
                'blockers':sorted(set(observed['blockers']+[v for v in catalog['blockers'] if 'TRANSACTION_REVIEW' in v]))}
            for key,p in catalog['inventory']['profiles'].items():
                if key in profiles and profiles[key]['identity']!=p['identity']:raise ValueError('GPU profile identity collision')
                profiles[key]=p
        from runtime_maintenance import atomic
        PUBLIC.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
        from openstack_skyline_runtime import trusted
        trusted(PUBLIC.parent)
        if PUBLIC.is_symlink():raise ValueError('unsafe GPU catalog destination')
        atomic(PUBLIC,json.dumps({'schema_version':1,'observed_at':int(time.time()),'hosts':entries,'profiles':profiles},sort_keys=True).encode(),0o644)

    def process_gpu(self,entry):
        from openstack_spvm import host_gpus as model,cluster_flow,secret_reader
        from nova_spvm import gpu_profiles as gpu
        metadata=secret_reader.read_json(self.c.key_manager,'/secrets/'+entry);creator=metadata.get('creator_id')
        if creator not in self.config['admin_user_ids']:return
        name=metadata['name'];request_id=name.removeprefix(model.PREFIX+'request-');secret_reader.canonical_uuid(request_id)
        saved=cluster_flow.record(self.c.key_manager,name,[creator]);body=cluster_flow.parse_json(saved[1])
        if (type(body) is not dict or set(body)!={'schema_version','project_id','user_id','request_id','host','generation','change'}
                or type(body['schema_version']) is not int or body['schema_version']!=1
                or body['project_id']!=self.config['project_id'] or body['user_id']!=creator or body['request_id']!=request_id):
            raise ValueError('GPU request creator/project differs')
        intent={k:body[k] for k in ('request_id','host','generation','change')};model.normalize(intent)
        result_name=model.PREFIX+'result-'+request_id
        if cluster_flow.record(self.c.key_manager,result_name,[self.publisher]):return
        folder=self.root/request_id
        if folder.exists():
            previous=json.loads((folder/'transaction.json').read_bytes())
            if (previous.get('intent_digest')==gpu.digest(intent) and previous.get('project_id')==self.config['project_id']
                    and previous.get('request_id')==request_id and previous.get('status') in ('SUCCEEDED','REFUSED','REVIEW_REQUIRED')):
                # Publish an already durable outcome, never replay its mutation.
                self.publish(result_name,{k:previous[k] for k in ('request_id','project_id','status','message')});return
            self.publish(result_name,{'request_id':request_id,'project_id':self.config['project_id'],
                'status':'REVIEW_REQUIRED','message':'Earlier GPU operation requires review; no automatic replay.'});return
        folder.mkdir(mode=0o700);journal=folder/'transaction.json';quarantine=False;service=None;was_enabled=False
        record={'request_id':request_id,'project_id':self.config['project_id'],'intent_digest':gpu.digest(intent),
                'host':body['host'],'kind':'gpu','status':'PROCESSING'};write(journal,record)
        try:
            matches=[n for n in self.config['automatic'] if self.cfg['hosts'][n].get('inventory_name',n)==body['host']]
            if len(matches)!=1:raise ValueError('GPU host not delegated')
            host=matches[0];current,observed,state,service=self.gpu_catalog(host,request_id=request_id)
            if current['generation']!=body['generation']:raise ValueError('GPU catalog changed')
            change=body['change']
            if change['kind'] in ('discover','configure'):
                if current['blockers']:raise ValueError('hardware discovery requires an empty host')
                if change['kind']=='configure':
                    wanted=gpu.desired(observed['inventory'],change['settings'])
                    prepared=self.prepare_gpu_configuration(host,observed,state,wanted,folder)
                was_enabled=service.status=='enabled';record['was_enabled']=was_enabled
                quarantine=True;record['phase']='DISABLING_SCHEDULING';write(journal,record)
                if was_enabled:self.c.compute.disable_service(service,disabled_reason='SPVM GPU configuration '+request_id)
                again=self.local(host,'observe');blockers,selected=self.cloud_empty(host,again)
                if blockers or again!=state or selected is None or selected.status!='disabled':raise ValueError('host changed after quarantine')
                if change['kind']=='discover':
                    after=self.gpu_local(host,'change',before=observed,change=change,idle=True)
                    if gpu.nova_configuration(after['inventory'])!=gpu.nova_configuration(observed['inventory']):
                        raise ValueError('technical GPU identity changed; native PCI configuration must be reviewed before scheduling')
                else:
                    record['phase']='APPLYING_NATIVE_PCI';write(journal,record)
                    self.apply_gpu_configuration(host,request_id,prepared,folder)
                    record['phase']='VERIFYING_NATIVE_PCI';write(journal,record)
                    self.verify_gpus(host,wanted)
                final=self.local(host,'observe');blockers,selected=self.cloud_empty(host,final)
                if blockers or selected is None or selected.status!='disabled':raise ValueError('host changed during discovery')
                if was_enabled:self.c.compute.enable_service(selected)
            elif change['kind']=='device-label':
                self.gpu_local(host,'change',before=observed,change=change,idle=False)
            else:
                if change['profile'] not in current['inventory']['profiles']:raise ValueError('unknown shared profile')
                names=self.gpu_names();names[change['profile']]=gpu.label(change['label'])
                write(self.root/'gpu-profile-names.json',names)
            result={'request_id':request_id,'project_id':self.config['project_id'],'status':'SUCCEEDED',
                'message':('GPU configuration applied and native PCI inventory verified. No VM was stopped or restarted.'
                    if change['kind']=='configure' else 'GPU metadata updated. Nova allocations and VM lifecycle were not changed.')}
        except Exception as error:
            record['error_type']=type(error).__name__
            if quarantine and service is not None:
                # A restore acknowledgement can be lost after Nova enabled the
                # service. Reassert the safety fence, never restore guests or
                # blindly repeat the configuration transaction.
                try:
                    self.c.compute.disable_service(service,disabled_reason='SPVM GPU review required '+request_id)
                    record['quarantine_reasserted']=True
                except Exception:
                    record['quarantine_reasserted']=False
            result={'request_id':request_id,'project_id':self.config['project_id'],
                    'status':'REVIEW_REQUIRED' if quarantine else 'REFUSED',
                    'message':('GPU transaction requires review. Scheduling quarantine was attempted; verify the service is disabled. No VM was stopped or restarted; no automatic replay or rollback.'
                        if quarantine else 'GPU request refused before scheduling/configuration changes. Refresh the catalog and inspect the host prerequisites.')}
        record.update(result);write(journal,record);self.publish(result_name,result)

    def prepare_gpu_configuration(self,host,observed,state,wanted,folder):
        """Render and validate every destination before disabling scheduling."""
        from nova_spvm import gpu_profiles as gpu
        import yaml
        controls=[n for n,h in self.cfg['hosts'].items() if 'control' in h['roles']]
        if controls!=[self.config['controller']]:raise ValueError('GPU reconfiguration currently supports one delegated controller')
        filename=Path(self.config['config']);old=filename.read_bytes();resolved=yaml.safe_load(old)
        resolved['hosts'][host]['gpu_inventory']=wanted
        candidate=folder/'deployment.after.yaml';candidate.write_text(yaml.safe_dump(resolved,sort_keys=False))
        (folder/'deployment.before.yaml').write_bytes(old)
        self.module.deployment_config(candidate)
        all_aliases={}
        for h in resolved['hosts'].values():
            if 'compute' not in h['roles']:continue
            for a in gpu.nova_configuration(h['gpu_inventory'])['aliases']:
                if a['name'] in all_aliases and all_aliases[a['name']]!=a:raise ValueError('conflicting native GPU aliases')
                all_aliases[a['name']]=a
        profile_aliases=sorted(all_aliases.values(),key=lambda a:a['name'])
        before={'gpu':observed,'host':state};target=resolved['hosts'][host].get('inventory_name',host)
        selected=gpu.nova_configuration(wanted)
        compute_plan=self.gpu_local(host,'plan-compute',before=before,wanted=wanted,aliases=profile_aliases)
        plan=self.gpu_local(self.config['controller'],'plan-controller',target=target,selected=selected,aliases=profile_aliases)
        noop=(compute_plan['before']==compute_plan['after'] and plan['before']==plan['after']
            and self.cfg['hosts'][host]['gpu_inventory']==wanted)
        prepared={'before':before,'wanted':wanted,'aliases':profile_aliases,'target':target,
                  'selected':selected,'controller_plan':plan,'compute_plan':compute_plan,'noop':noop}
        write(folder/'native-plan.json',prepared)
        return prepared

    def apply_gpu_configuration(self,host,request_id,prepared,folder):
        from nova_spvm import gpu_profiles as gpu
        from runtime_maintenance import atomic
        filename=Path(self.config['config']);old=(folder/'deployment.before.yaml').read_bytes()
        if filename.read_bytes()!=old:raise ValueError('canonical deployment changed since GPU preflight')
        if prepared['noop']:return
        staged=self.gpu_local(host,'stage-compute',request_id=request_id,before=prepared['before'],
            wanted=prepared['wanted'],aliases=prepared['aliases']);write(folder/'compute-staged.json',staged)
        self.confirm_gpu_quarantine(host)
        self.gpu_local(self.config['controller'],'apply-controller',request_id=request_id,target=prepared['target'],
            selected=prepared['selected'],aliases=prepared['aliases'],plan=prepared['controller_plan'])
        if filename.read_bytes()!=old:raise ValueError('canonical deployment changed during GPU transaction')
        candidate=folder/'deployment.after.yaml';resolved=self.module.deployment_config(candidate)
        if gpu.digest(resolved['hosts'][host]['gpu_inventory'])!=gpu.digest(prepared['wanted']):raise ValueError('staged GPU intent changed')
        atomic(filename,candidate.read_bytes(),filename.stat().st_mode&0o777)
        self.cfg=resolved
        self.confirm_gpu_quarantine(host)
        self.gpu_local(host,'start-compute',request_id=request_id,staged=staged)

    def confirm_gpu_quarantine(self,host):
        state=self.local(host,'observe');blockers,service=self.cloud_empty(host,state,allow_down=True)
        if state['blockers'] or blockers or service is None or service.status!='disabled':
            raise ValueError('host acquired instances/reservations or lost scheduling quarantine')

    def gpu_pci(self,host):
        from accounting_cloud import PCI_CODE
        hostname=self.cfg['hosts'][host].get('inventory_name',host)
        source='CODE='+repr(PCI_CODE)+'\nHOST='+repr(hostname)+'\n'+'''
import subprocess,json
p=subprocess.run(['docker','exec','-i','nova_conductor','/var/lib/kolla/venv/bin/python','-c',CODE],
 input=json.dumps([HOST]),capture_output=True,text=True,timeout=90)
if p.returncode or len(p.stdout)>4194304:raise ValueError('native PCI query failed')
print(p.stdout)
'''
        return self.runner.remote(self.config['controller'],source,isolated=True)[hostname]

    def verify_gpus(self,host,wanted):
        from nova_spvm import gpu_profiles as gpu
        native=gpu.nova_configuration(wanted);desired={};by_address={d['address']:d for d in wanted['devices']}
        for d in native['device_spec']:
            rc=d['resource_class'];desired[rc]=desired.get(rc,0)+1
        expected={d['address'] for d in native['device_spec']}
        state=self.local(host,'observe');root=state['compute_uuid'];deadline=time.monotonic()+180
        while True:
            response=self.c.placement.get('/resource_providers?in_tree='+root,microversion='1.39');response.raise_for_status()
            providers=response.json()['resource_providers']
            if type(providers) is not list or len(providers)>1024:raise ValueError('bounded native PCI provider tree required')
            totals={}
            for p in providers:
                response=self.c.placement.get('/resource_providers/'+p['uuid']+'/inventories',microversion='1.39');response.raise_for_status()
                for rc,v in response.json()['inventories'].items():
                    if not rc.startswith(('CUSTOM_SPVM_GPU_','CUSTOM_SPVM_FG_')):continue
                    if v.get('allocation_ratio')!=1.0:raise ValueError('GPU overcommit is forbidden')
                    total=v['total']-v.get('reserved',0)
                    if total:totals[rc]=totals.get(rc,0)+total
            pci=self.gpu_pci(host);devices=[d for d in pci['devices'] if d['status']!='deleted' and d['address'] in by_address]
            matches=(pci['compute_uuid']==root and {d['address'] for d in devices}==expected
                and len(devices)==len(expected) and all(d['status']=='available' and not d.get('instance_uuid')
                    and all(d[k]==by_address[d['address']]['pci'][k] for k in ('vendor_id','product_id'))
                    and d['dev_type']==by_address[d['address']]['pci']['device_type'] for d in devices))
            blockers,service=self.cloud_empty(host,self.local(host,'observe'))
            if totals==desired and matches and not blockers and service is not None and service.status=='disabled':return
            if time.monotonic()>deadline:raise ValueError('native GPU ownership/inventory did not converge; scheduling stays disabled')
            time.sleep(5)
