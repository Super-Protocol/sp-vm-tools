"""Pool lifecycle extension of the explicitly delegated empty-host worker."""
import copy
import json
from pathlib import Path
import time
import traceback

from openstack_container_engine import write


class PoolWorker:
    def pool_local(self,name,action,**data):
        if action=='observe':return self.observation(name,'pools','pool-observe',**data)
        return self.channel.call(name,'pool-'+action,**data)

    def pool_catalog(self,name,*,request_id=None):
        from openstack_spvm import host_pools as model
        before=self.pool_local(name,'observe',managed=[])
        blockers,service=self.cloud_empty(name,before['state'])
        host=self.cfg['hosts'][name].get('inventory_name',name)
        for journal in self.root.glob('*/transaction.json'):
            if journal.parent.name==request_id:continue
            record=json.loads(journal.read_text())
            if record.get('host') in (None,host) and record.get('status') not in ('SUCCEEDED','REFUSED'):
                blockers.append('HOST_TRANSACTION_REVIEW_REQUIRED')
        generation=self.model.digest({'host':host,'state_files':before['state']['files'],
            'machine':before['state']['machine_sha256'],'compute_uuid':before['state']['compute_uuid'],
            'public':before['public_sha256'],'managed':before['managed'],
            'mounts':[{k:f[k] for k in ('filesystem_id','device_id','mount','fstype','eligible')} for f in before['filesystems']]})
        catalog={'schema_version':1,'project_id':self.config['project_id'],'host':host,'generation':generation,
            'pools':before['managed'],'filesystems':before['filesystems'],
            'blockers':sorted(set(before['blockers']+blockers))}
        return catalog,before,service

    def sync_pools(self):
        from openstack_spvm import host_pools as model
        for name in self.config['executors']:
            catalog,before,_=self.pool_catalog(name)
            self.publish(model.PREFIX+before['state']['compute_uuid']+'-catalog',catalog)

    def retire_pool_providers(self,before,wanted):
        from nova_spvm.storage import pool_uuid,OWNER_TRAIT,RESOURCE
        root=before['state']['compute_uuid'];keep={p['id'] for p in wanted if p['enabled']}
        for name in set(before['public']['pool_ids'])-keep:
            provider=pool_uuid(root,name);base='/resource_providers/'+provider
            reply=self.c.placement.get(base,microversion='1.39')
            if reply.status_code==404:continue
            reply.raise_for_status()
            if reply.json()['parent_provider_uuid']!=root:raise ValueError('pool provider parent differs')
            traits=self.c.placement.get(base+'/traits',microversion='1.39');traits.raise_for_status()
            if OWNER_TRAIT not in traits.json()['traits']:raise ValueError('pool provider is not SPVM-owned')
            inventories=self.c.placement.get(base+'/inventories',microversion='1.39');inventories.raise_for_status()
            if set(inventories.json()['inventories'])-{RESOURCE}:raise ValueError('pool provider contains foreign resources')
            allocations=self.c.placement.get(base+'/allocations',microversion='1.39');allocations.raise_for_status()
            if allocations.json()['allocations']:raise ValueError('pool provider acquired allocations')
            response=self.c.placement.delete(base,microversion='1.39');response.raise_for_status()

    def process_pool(self,entry):
        from openstack_spvm import host_pools as model,cluster_flow,secret_reader
        metadata=secret_reader.read_json(self.c.key_manager,'/secrets/'+entry);creator=metadata.get('creator_id')
        if not self.authorized_creator(creator):return
        name=metadata['name'];request_id=name.removeprefix(model.PREFIX+'request-');secret_reader.canonical_uuid(request_id)
        saved=cluster_flow.record(self.c.key_manager,name,[creator]);body=cluster_flow.parse_json(saved[1])
        if (type(body) is not dict or set(body)!={'schema_version','project_id','user_id','request_id','host','generation','pools'}
                or type(body['schema_version']) is not int or body['schema_version']!=1
                or body['project_id']!=self.config['project_id'] or body['user_id']!=creator or body['request_id']!=request_id):
            raise ValueError('pool request creator/project/identity differs')
        intent={k:body[k] for k in ('request_id','host','generation','pools')};model.normalize(intent)
        result_name=model.PREFIX+'result-'+request_id
        if cluster_flow.record(self.c.key_manager,result_name,[self.publisher]):return
        self.active=self.root/request_id
        if self.active.exists():
            self.publish(result_name,{'request_id':request_id,'project_id':self.config['project_id'],
                'status':'REVIEW_REQUIRED','message':'Previous pool attempt requires review; no automatic replay.'});return
        self.active.mkdir(mode=0o700)
        record={'request_id':request_id,'host':body['host'],'kind':'pools','status':'PROCESSING','phase':'PREFLIGHT'}
        journal=self.active/'transaction.json';write(journal,record);quarantine=False
        try:
            matches=[n for n in self.config['executors'] if self.cfg['hosts'][n].get('inventory_name',n)==body['host']]
            if len(matches)!=1:raise ValueError('pool host not delegated')
            host=matches[0];catalog,before,service=self.pool_catalog(host,request_id=request_id)
            if catalog['blockers']:raise ValueError('host not empty/compatible')
            wanted=model.resolve(intent,catalog);was_enabled=service.status=='enabled'
            record['was_enabled']=was_enabled
            record['phase']='DISABLING_SCHEDULING';write(journal,record);quarantine=True
            if was_enabled:self.c.compute.disable_service(service,disabled_reason='SPVM host pools '+request_id)
            again=self.pool_local(host,'observe',managed=[])
            blockers,current=self.cloud_empty(host,again['state'])
            if again!=before or blockers or current is None or current.status!='disabled':raise ValueError('host changed after quarantine')
            record['phase']='PREPARING_POOLS';write(journal,record)
            self.pool_local(host,'prepare',request_id=request_id,wanted=wanted,before=before)
            blockers,current=self.cloud_empty(host,before['state'],allow_down=True)
            if blockers or current is None or current.status!='disabled':raise ValueError('host acquired work while stopped')
            record['phase']='RETIRING_EMPTY_PROVIDERS';write(journal,record)
            self.retire_pool_providers(before,wanted)
            self.pool_local(host,'restart',request_id=request_id)
            record['phase']='VERIFYING_INVENTORIES';write(journal,record)
            desired={**before['state']['current'],'pools':[{k:p[k] for k in ('id','budget_gib','reserve_gib')} for p in wanted if p['enabled']]}
            self.verify(before['state'],desired)
            deadline=time.monotonic()+180
            while True:
                final=self.pool_local(host,'observe',managed=wanted);blockers,current=self.cloud_empty(host,final['state'])
                if not final['blockers'] and not blockers and current is not None and current.status=='disabled':break
                if time.monotonic()>deadline:raise ValueError('final pool state did not converge')
                time.sleep(5)
            result={'request_id':request_id,'project_id':self.config['project_id'],'status':'SUCCEEDED',
                'message':'Pools and inventories verified. Host remains disabled until explicit Enable. Directories and data were retained.'}
        except Exception as error:
            record['error_type']=type(error).__name__
            response=getattr(error,'response',None)
            record['error_http_status']=getattr(response,'status_code',None)
            record['error_frames']=[(Path(f.filename).name,f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)[-6:]]
            result={'request_id':request_id,'project_id':self.config['project_id'],
                'status':'REVIEW_REQUIRED' if quarantine else 'REFUSED',
                'message':('Scheduling quarantine was attempted. Inspect the private pool journal; no automatic rollback/replay or data deletion.'
                    if quarantine else 'Pools can only be changed on an empty, compatible host. Refresh mounts, configuration and generation.')}
        record.update(result);write(journal,record);self.publish(result_name,result)
