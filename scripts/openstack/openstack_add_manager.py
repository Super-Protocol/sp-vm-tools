"""Management-host add-compute: one entry for new, returning and ready hosts."""
import copy
import json
from pathlib import Path
import time
from types import SimpleNamespace as R

import yaml
from openstack_active_deployment import current,portable
from openstack_container_engine import digest,file_hash,write
from openstack_errors import InstallerError
from openstack_install_phases import Installation,document
from openstack_install_continue import document as read_document
from openstack_remove_manager import Coordinator,installed_context,configuration_identity,lock_guard,CONTROLLER_RECORD_SOURCE
from openstack_compute_onboard import remote_tools
import openstack_add_host as target


def desired_configuration(active,requested,name):
    if name not in requested['hosts'] or requested['hosts'][name]['roles']!=['compute']:
        raise InstallerError('Select exactly one configured compute-only host; controller/network/ingress changes are not supported.')
    result=portable(active)
    for key in ('version','deployment_id','network'):
        if requested[key]!=result[key]:raise InstallerError('Existing cloud '+key+' differs; add-compute never reconfigures the cloud.')
    for key in ('series','revision','base_distro'):
        if requested['kolla'][key]!=result['kolla'][key]:raise InstallerError('Use the active cloud Kolla kit, not an upgrade request.')
    for key,value in requested['kolla'].items():
        if key not in ('skyline_console_context','skyline_console_context_sha256') and key in result['kolla'] and value!=result['kolla'][key]:
            raise InstallerError('Requested image/version differs from the active cloud; update it separately.')
    for peer,host in result['hosts'].items():
        if peer not in requested['hosts']:raise InstallerError('Existing hosts must remain in deployment YAML.')
        for key in ('address','user','roles','inventory_name','management_interface','tunnel_interface','launch_mode'):
            if requested['hosts'][peer].get(key)!=host.get(key):raise InstallerError('Existing host intent differs: '+peer+'.'+key)
    if name not in result['hosts']:result['hosts'][name]=copy.deepcopy(requested['hosts'][name])
    # The management kit has already been independently verified by
    # Installation and the active-controller source pin. Its local staging
    # path is not an upgrade to the sealed deployment's driver intent.
    # Keep every existing global field byte-for-byte for workspace extension.
    return result


def retirement_proof(installation,name,machine,compute):
    matches=[]
    for path in Path(installation).parent.glob('*/transaction.json'):
        try:
            record=read_document(path)
            if record.get('status')!='COMPUTE_REMOVED' or record.get('host')!=name:continue
            plan=read_document(path.parent/'remove-compute-plan.json')
            if (plan.get('machine')==machine and plan.get('compute_uuid')==compute
                    and record.get('plan_sha256')==plan.get('plan_sha256')
                    and digest({k:v for k,v in plan.items() if k!='plan_sha256'})==plan['plan_sha256']):matches.append(plan)
        except (OSError,ValueError,KeyError):continue
    return max(matches,key=lambda p:p['created_at']) if matches else None


def cloud_state(runner,identity):
    from compute_removal import CELL_CODE
    code='IDENTITY='+repr(identity)+'\nCELL_CODE='+repr(CELL_CODE)+'\n'+'''
import json,subprocess,openstack
c=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=30)
try:
 if 'admin' not in c.session.auth.get_access(c.session).role_names:raise ValueError('all-project admin required')
 host=IDENTITY['hostname'];root=IDENTITY['compute_uuid']
 services=[{'id':s.id,'host':s.host,'status':s.status,'state':s.state} for s in c.compute.services(binary='nova-compute',host=host)]
 servers=[{'id':s.id,'host':s.compute_host,'status':s.status,'task':s.task_state} for s in c.compute.servers(all_projects=True,details=True)]
 migrations=[m.id for m in c.compute.migrations() if (m.source_compute==host or m.dest_compute==host) and m.status not in ('confirmed','reverted','completed','error','failed','cancelled')]
 response=c.placement.get('/resource_providers/'+root,microversion='1.39',raise_exc=False)
 if response.status_code==404:providers=[]
 else:
  response.raise_for_status();response=c.placement.get('/resource_providers',params={'in_tree':root},microversion='1.39');response.raise_for_status()
  providers=response.json()['resource_providers']
 for provider in providers:
  r=c.placement.get('/resource_providers/'+provider['uuid']+'/allocations',microversion='1.39');r.raise_for_status();provider['allocations']=r.json()['allocations']
 script='HOST='+repr(host)+'\\n'+CELL_CODE
 p=subprocess.run(['docker','exec','nova_conductor','/var/lib/kolla/venv/bin/python','-c',script],check=True,capture_output=True,text=True,timeout=90)
 cell=json.loads(p.stdout)['cell']
 p=subprocess.run(['docker','exec','ovn_sb_db','ovn-sbctl','--format=json','--columns=name,hostname','list','Chassis'],check=True,capture_output=True,text=True,timeout=60)
 table=json.loads(p.stdout);chassis=[dict(zip(table['headings'],r)) for r in table['data'] if r[1]==host]
 print(json.dumps({'services':services,'servers':servers,'assigned_servers':[s for s in servers if s['host']==host or s['status']=='BUILD' or s['task']],
  'providers':providers,'allocations':[p['uuid'] for p in providers if p['allocations']],
  'migrations':migrations,'cell':cell,'chassis':chassis}))
finally:c.close()
'''
    return runner.remote(runner.controller,code,controller=True,auth=True)


def actor(runner,name,identity,pools,*,approved=None,cloud=None,proof=None,operation=None):
    data={'identity':identity,'pools':pools,'approved':approved,'cloud':cloud,'proof':proof,'operation':operation}
    return remote_tools(runner,name,('openstack_add_host.py',),data,'''
from openstack_add_host import Host
h=Host()
result=(h.observe(DATA['identity'],DATA['pools']) if DATA['approved'] is None else
 h.reconcile(DATA['identity'],DATA['pools'],DATA['approved'],DATA['cloud'],DATA['proof'],DATA['operation']))
''')


class Addition:
    def __init__(self,args):
        self.args=args;self.i=Installation(args);self.output=Path(args.output)
        self.base,self.latest=installed_context(args.installation)
        self.workspace,self.active_config=current(self.base)
        self.old_raw=portable(yaml.safe_load(self.active_config.read_bytes()))
        # Supersession files may contain resolved transport data. It is never
        # persisted as deployment intent in the new workspace/configuration.
        self.old_file=document(self.output/'active-deployment.yaml',self.old_raw)
        self.old=self.i.backend.deployment_config(self.old_file)
        self.receipt=read_document(self.workspace/'workspace.json')
        from openstack_kolla_workspace import check
        check(self.workspace)
        self.binding=read_document(self.workspace/'compute-binding.json')
        # The manifest is a verified public artifact, not a root-private
        # transaction/credential record. Installation already checked its hashes.
        manifest=json.loads((self.i.path.parent.parent/'manifest.json').read_bytes())
        wanted=read_document(self.latest/'plan.json').get('controller_review',{}).get('driver_commit')
        if wanted and manifest['source_commit']!=wanted:raise InstallerError('Select the verified active-cloud driver kit; add-compute does not upgrade the cloud.')
        raw=desired_configuration(self.old,self.i.raw,args.host)
        self.i.config=document(self.output/'deployment.desired.yaml',raw)
        self.i.cfg=self.i.backend.deployment_config(self.i.config)
        args.workspace=str(self.workspace);args.known_hosts=self.i.known_hosts
        self.c=Coordinator(args,self.i.path,self.i.backend,self.old,self.base)
        self.i.controller=self.c.controller
        from compute_workflow import Backend
        self.runner=Backend(self.i.cfg,self.c.controller,self.c.python,'/etc/spvm/auth/admin-openrc.sh',self.output/'observations')
        remote=self.runner.remote
        def guarded(host,source,**kwargs):
            self.c.alive()
            if host==self.c.controller:source=lock_guard(self.c.lock)+source
            result=remote(host,source,**kwargs);self.c.alive();return result
        self.runner.remote=guarded;self.c.runner=self.runner

    def observe(self):
        from openstack_compute_identity import native_uuid
        cfg=self.i.cfg;host=self.args.host
        # Automatic pools need read-only resolution before the hardware audit.
        # Unlike fresh storage preparation this discovery does not require
        # deleting existing enrollment first and does not create directories.
        audit_cfg=copy.deepcopy(cfg)
        if audit_cfg['hosts'][host]['storage_pools']=='auto':
            source=(self.i.path.parent/'storage_discovery.py').read_text()
            observed=self.runner.remote(host,'SOURCE='+repr(source)+'\nimport types,json\nm=types.ModuleType("storage_discovery");exec(SOURCE,m.__dict__)\nprint(json.dumps(m.discover()))\n')
            from storage_discovery import automatic
            audit_cfg['hosts'][host]['storage_pools']=automatic(observed,**audit_cfg['hosts'][host].get('storage_discovery',{}))
        audits=self.i.backend.host_audits(audit_cfg);self.i.audits=audits
        if audits[host]['os_version']=='26.04' and not self.args.allow_experimental_ubuntu26:
            raise InstallerError('Ubuntu 26 compute requires --allow-experimental-ubuntu26.')
        from basic_preflight import check
        screening=check(audit_cfg,audits)
        if screening['blockers']:raise InstallerError('Host screening failed: '+', '.join(screening['blockers']))
        self.identity={'hostname':audits[host]['hostname'],'compute_uuid':native_uuid(audits[host]['hostname']),
            'machine_sha256':audits[host]['host_identity_sha256'],
            'auth_url':self.binding['bootstrap_runtime']['auth_url'],'project_id':self.c.native['policy']['project_id']}
        if self.identity['machine_sha256'] in {v for n,v in self.binding['machines'].items() if n!=host}:
            raise InstallerError('Target duplicates an existing physical host.')
        if self.identity['compute_uuid'] in {v['compute_uuid'] for n,v in self.binding['hosts'].items() if n!=host}:
            raise InstallerError('Target hostname/compute UUID collides with an existing compute.')
        cloud=cloud_state(self.runner,self.identity)
        self.proof=retirement_proof(self.args.installation,host,self.identity['machine_sha256'],self.identity['compute_uuid'])
        if cloud['services']:
            if host not in self.old['hosts']:raise InstallerError('Host is registered but absent from the active installation; reconcile ownership explicitly.')
            plan=self.runner.plan(host)
            if plan['mode']!='NOOP' or plan['status']!='NOOP_READY':raise InstallerError('Registered host is not an exact ready match; add-compute does not repair occupied deployments.')
            return {'kind':'add-compute-v1','classification':'registered','cloud':cloud,'ready_plan':plan,'facts':None}
        if host in self.old['hosts']:raise InstallerError('Active inventory contains an absent compute; retire/review it first, never silently recreate a failed node.')
        self.pools=audit_cfg['hosts'][host]['storage_pools']
        facts=actor(self.runner,host,self.identity,self.pools)
        classification=target.classify(facts,cloud,self.identity,self.proof)
        import openstack_launcher as launcher
        import openstack_image_transfer as transfer
        self.launcher,self.launcher_files=launcher.verify(self.i.path,self.base/'launcher')
        package_sha=read_document(self.base/'compute-package/image-package.json')['package_sha256']
        self.package=transfer.package(self.base/'compute-package',package_sha)
        if self.package['image_id']!=cfg['kolla']['compute_image_id'] or self.package['tag']!=cfg['kolla']['compute_image']:
            raise InstallerError('The saved cloud compute image differs; select a complete matching installation kit.')
        from openstack_compute_prepare import controller,verify_network_mtu
        return {'kind':'add-compute-v1','classification':classification,'facts':facts,'cloud':cloud,
            'controller':controller(self.runner),'network_mtu':verify_network_mtu(self.runner,cfg),
            'launcher':self.launcher,'package':self.package}

    def native(self,action,plan,executor=None):
        # The fixed controller operation uses only installed, verified native
        # modules and stdlib. It never imports manager-only installer modules.
        request={'action':action,'host':self.args.host,'operation':digest(str(self.output)),
            'plan':plan['plan_sha256'],'before':self.c.native,'config':portable(self.i.cfg),
            'executor':executor}
        code='REQUEST='+repr(request)+'\n'+CONTROLLER_RECORD_SOURCE+'''import sys,json,os
from pathlib import Path
sys.path.insert(0,'''+repr(self.c.native_root+'/tools')+''')
from openstack_container_engine import file_hash,write
r=REQUEST;b=r['before'];claim=Path('/var/lib/spvm-host-budget-worker/addition-coordinator.json')
for name,wanted in b['native_sources'].items():
 p=Path(name);s=p.lstat()
 if p.resolve()!=p or not p.is_file() or s.st_uid!=0 or s.st_mode&0o022 or file_hash(p)!=wanted:raise ValueError('native source drift')
policy=Path('/etc/spvm/host-budget-policy.json');config=Path(b['policy']['config'])
if r['action']=='claim':
 if (claim.exists() or claim.is_symlink()) and json.loads(private(claim).read_bytes()).get('status')!='COMPUTE_ADDED':raise ValueError('unfinished host addition requires review')
 if file_hash(policy)!=b['policy_sha256'] or file_hash(config)!=b['config_sha256']:raise ValueError('active configuration changed')
 record={'status':'ADDING_COMPUTE','host':r['host'],'operation':r['operation'],'plan_sha256':r['plan']};write(claim,record)
else:
 record=json.loads(private(claim).read_bytes())
 if record.get('status')!='ADDING_COMPUTE' or record.get('operation')!=r['operation'] or record.get('plan_sha256')!=r['plan']:raise ValueError('addition claim differs')
 if r['action']=='configure':
  if file_hash(policy)!=b['policy_sha256'] or file_hash(config)!=b['config_sha256']:raise ValueError('active configuration drift')
  old=json.loads(config.read_bytes());v=json.loads(policy.read_bytes());name=r['host'];new=r['config']
  # Translate only the manager driver path; preserve every native existing host.
  new['driver']=old['driver']
  for key in ('skyline_console_context','skyline_console_context_sha256'):
   if key in old['kolla']:new['kolla'][key]=old['kolla'][key]
   else:new['kolla'].pop(key,None)
  if name in old['hosts'] or {k:x for k,x in new.items() if k!='hosts'}!={k:x for k,x in old.items() if k!='hosts'} or any(new['hosts'].get(n)!=h for n,h in old['hosts'].items()):raise ValueError('only an additive compute configuration is allowed')
  if set(new['hosts'])!=set(old['hosts'])|{name} or new['hosts'][name]['roles']!=['compute']:raise ValueError('exact compute addition required')
  directory=claim.parent/('addition-'+r['operation']);directory.mkdir(mode=0o700)
  write(directory/'configuration-before.json',{'config':old,'policy':v})
  h=new['hosts'][name];v['automatic'][name]={**h['compute_resources'],'pools':[{k:p[k] for k in ('id','budget_gib','reserve_gib')} for p in h['storage_pools'] if p.get('enabled',True)]}
  v['executors'][name]=r['executor'];record['configuration_inflight']=True;write(claim,record)
  write(policy,v);write(config,new)
  record.update(configuration_inflight=False,policy_sha256=file_hash(policy),config_sha256=file_hash(config));write(claim,record)
 elif r['action']=='finish':
  if record.get('configuration_inflight') or file_hash(policy)!=record.get('policy_sha256') or file_hash(config)!=record.get('config_sha256'):raise ValueError('successor configuration differs')
  record['status']='COMPUTE_ADDED';write(claim,record)
 else:raise ValueError('unsupported fixed action')
print(json.dumps({'status':record['status']}))
'''
        return self.c.remote(code)

    def prepare_inputs(self):
        import openstack_storage_prepare as storage
        import openstack_gpu_prepare as gpu
        i=self.i;host=self.args.host
        args=i.phase_args(action='plan-storage',host=host,output=str(self.output/'storage'),replace_existing_state=False)
        args.config=i.config
        result=storage.execute(i.path,args);args.action='prepare-storage';args.plan_sha256=result['plan_sha256']
        result=storage.execute(i.path,args);i.config=result['resolved_config'];i.refresh()
        result=gpu.execute(i.path,i.phase_args(host=host,output=str(self.output/'gpu')))
        i.config=result['resolved_config'];i.refresh()
        return self.finish_inputs()

    def finish_inputs(self):
        """Derive current immutable inputs after the explicit resource phases."""
        i=self.i;host=self.args.host
        i.network_documents()
        from nova_spvm.gpu_profiles import catalog
        import openstack_host_helper as helper
        h=i.cfg['hosts'][host];mount=max((p for p in h['storage_pools'] if p.get('enabled',True)),key=lambda p:p['budget_gib'])['mount']
        self.helper={'version':1,**{k:self.identity[k] for k in ('machine_sha256','compute_uuid')},
            'launch_mode':h['launch_mode'],'allow_debug':i.options.get('allow_debug',False),
            'tools':'/opt/spvm-launcher/'+i.cfg['deployment_id']+'-add-'+digest(str(self.output))[:12],
            'registered_release_cache_root':str(Path(mount)/('spvm-registered-releases-'+i.cfg['deployment_id'])),
            'pools':[{k:p[k] for k in ('id','mount','budget_gib','reserve_gib')}|{'root':p['path']}|({'allow_root':True} if p.get('allow_root') else {}) for p in h['storage_pools'] if p.get('enabled',True)],
            'gpu_inventory':h['gpu_inventory'],'gpus':sorted(a for unit in catalog(h['gpu_inventory'])['assignments'] for a in unit['members'])}
        helper.request(self.helper);request=self.output/'helper-request.json';write(request,self.helper)
        from basic_preflight import check
        report=check(i.cfg,i.audits)
        if report['blockers']:raise InstallerError('Resolved host preparation failed screening.')
        i.rendered=str(self.output/'rendered');i.backend.render(i.cfg,i.rendered,i.audits,kolla_source=str(i.kolla_source),preflight=report)
        audit=self.output/'audits.json';write(audit,i.audits)
        import openstack_compute_workspace as compute
        import kolla_inputs
        compute.stage(R(config=self.old_file,new_config=i.config,host=host,workspace=str(self.workspace),rendered=i.rendered,
            audit=str(audit),request=str(request),output=str(self.output/'added-workspace')),i.path,i.backend,kolla_inputs)
        self.new_workspace=self.output/'added-workspace'
        self.new_config=document(self.output/'deployment.resolved.yaml',portable(i.cfg))
        # Existing service configuration, password store and public CA are reused.
        write(self.output/'preparation-recipe.json',{'schema_version':1,'network_request':str(self.output/'network.json'),
            'geneve_request':str(self.output/'geneve.json'),'launcher_stage':str(self.base/'launcher'),
            'launcher_sha256':self.launcher['receipt_sha256'],'image_package':str(self.base/'compute-package'),
            'image_package_sha256':self.package['package_sha256'],'image_reserve_gib':10})
        runtime=self.runner.remote(self.c.controller,"import json;from pathlib import Path;print(json.dumps(json.loads(Path('/etc/spvm/runtime-services.json').read_bytes())))")
        if runtime['auth_url']!=self.binding['bootstrap_runtime']['auth_url']:raise InstallerError('Existing runtime belongs to a different cloud.')
        from nova_spvm.launch_policy import admit,PolicyError
        for project in runtime['projects'].values():
            allowed=[]
            for release in project['releases']:
                try:admit({'mode':h['launch_mode'],'allow_debug':self.helper['allow_debug']},release)
                except PolicyError:continue
                allowed.append(release)
            project['releases']=allowed
        wanted={'schema_version':1,'helper_request':self.helper,'bootstrap_runtime':self.binding['bootstrap_runtime'],
            'runtime':runtime,'image_id':self.binding['image_id'],'image_context_sha256':self.binding['image_context_sha256'],
            'enrollment_sha256':digest({'runtime':runtime,'helper':self.helper})}
        from openstack_runtime_enrollment import load_backend
        wanted=load_backend(i.path).request(wanted);write(self.output/'enrollment.json',wanted)
        write(self.output/'new-compute.json',{'schema_version':1,'configdir':str(self.new_workspace/'kolla'),
            'allow_experimental_ubuntu26':self.args.allow_experimental_ubuntu26,'preparation':str(self.output/'preparation-recipe.json')})
        self.value={'schema_version':1,'mode':'add-compute','config':self.new_config,'workspace':str(self.new_workspace),
            'known_hosts':i.known_hosts,'controller':self.c.controller,'controller_python':self.c.python,
            'openrc':'/etc/spvm/auth/admin-openrc.sh','host':host,'enrollment_request':str(self.output/'enrollment.json'),
            'new_compute':str(self.output/'new-compute.json')}
        # Use the resolved config (including automatically detected PCI identity).
        self.runner.cfg=i.cfg
        return {'status':'COMPUTE_INPUTS_PREPARED','workspace':str(self.new_workspace)}

    def install_executor(self,plan):
        request={'project':self.c.native['policy']['project_id'],'host':self.args.host,'identity':self.identity,
            'deployment':self.i.cfg['deployment_id'],'operation':digest(str(self.output)),
            'readers':self.c.native['policy']['reader_user_ids'],
            'dispatcher_path':self.c.native['policy']['dispatcher_credentials'],
            'auth_url':self.binding['bootstrap_runtime']['auth_url'],
            'region':self.binding['bootstrap_runtime']['region_name']}
        code='REQUEST='+repr(request)+'\nROOT='+repr(self.c.native_root)+'\n'+'''
import sys,json,hashlib,openstack
from pathlib import Path
sys.path[:0]=[str(Path(ROOT)/'tools'),str(Path(ROOT)/'driver/deployment')]
from host_control_credentials import provision
from openstack_container_engine import write
r=REQUEST;c=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=30)
try:
 if c.session.get_project_id()!=r['project']:raise ValueError('executor project differs')
 dispatcher_path=Path(r['dispatcher_path'])
 for parent in (dispatcher_path,*dispatcher_path.parents):
  st=parent.lstat()
  if parent.is_symlink() or st.st_uid!=0 or st.st_mode&0o022:raise ValueError('untrusted dispatcher identity file')
 st=dispatcher_path.stat()
 if st.st_nlink!=1 or st.st_mode&0o777!=0o600 or st.st_size>65536:raise ValueError('private bounded dispatcher identity required')
 publisher=json.loads(dispatcher_path.read_bytes())['user_id']
 if publisher not in r['readers'] or publisher==c.session.get_user_id():raise ValueError('enrolled dispatcher identity required')
 directory=Path('/var/lib/spvm-host-budget-worker')/('addition-executor-'+r['operation']);directory.mkdir(mode=0o700)
 label='spvm-host-'+hashlib.sha256((r['project']+r['deployment']+r['identity']['compute_uuid']+r['operation']).encode()).hexdigest()[:24]
 private=provision(c,label,r['auth_url'],r['region'],'/etc/ssl/certs/ca-certificates.crt',lambda state:write(directory/'transaction.json',state))
 print(json.dumps({'credentials':private,'dispatcher_user_id':publisher}))
finally:c.close()
'''
        # Restricted per-host credentials exist only in this process's memory
        # and verified SSH stdin/stdout. Never write or print the private result.
        enrolled=self.private_remote(self.c.controller,code,controller=True,auth=True)
        private=enrolled['credentials']
        from openstack_spvm.host_control import binding
        from host_control_install import source_files
        target_binding=binding({'host':self.identity['hostname'],'compute_uuid':self.identity['compute_uuid'],
            'machine_sha256':self.identity['machine_sha256'],'user_id':private['user_id']})
        policy={'schema_version':1,'project_id':request['project'],'target':target_binding,
            'dispatcher_user_ids':[enrolled['dispatcher_user_id']],'credentials':'/etc/spvm/host-control-credentials.json'}
        # Do not shadow the installer's POLICY Path constant with request data.
        body=(self.i.path.parent/'host_control_install.py').read_text()+'\nREQUEST_POLICY='+repr(policy)+'\nPRIVATE_CREDENTIALS='+repr(private)+'\nSOURCE_FILES='+repr(source_files(self.i.path.parent))+'\nprint(json.dumps(install(REQUEST_POLICY,PRIVATE_CREDENTIALS,SOURCE_FILES)))\n'
        result=self.private_remote(self.args.host,body)
        if result['status']!='HOST_EXECUTOR_INSTALLED' or result['user_id']!=target_binding['user_id']:raise InstallerError('Executor install did not acknowledge the expected identity.')
        return target_binding

    def private_remote(self,host,code,**kwargs):
        # SDK/installer exceptions must not copy secret-bearing HTTP responses
        # or input payloads into Backend.remote_failure's private diagnostics.
        wrapped='import sys,json,traceback\ntry:\n exec(compile('+repr(code)+',"spvm-private-host-install","exec"))\nexcept Exception as error:\n print(json.dumps({"private_operation_failure":type(error).__name__,"sites":[(frame.name,frame.lineno) for frame in traceback.extract_tb(error.__traceback__)[-6:]]}),file=sys.stderr)\n raise SystemExit(2)\n'
        return self.runner.remote(host,wrapped,**kwargs)

    def apply(self,plan):
        from openstack_compute_prepare import controller
        import openstack_compute_onboard as onboard
        import openstack_compute_prepare as deploy
        record={'status':'ADDING_COMPUTE','host':self.args.host,'plan_sha256':plan['plan_sha256'],
            'done':[],'inflight':None,'guests_changed':False}
        journal=self.output/'transaction.json'
        def step(name,callback):
            print('Adding compute: '+name,flush=True);record['inflight']=name;write(journal,record)
            result=callback();write(self.output/(name+'-result.json'),result or {})
            record['done'].append(name);record['inflight']=None;write(journal,record)
            return result
        # Reinspect all inputs before claiming or touching the target.
        for name,sha in plan['inputs'].items():
            if file_hash(name)!=sha:raise InstallerError('Approved input/source changed.')
        fresh=self.observe()
        if plan['classification']=='registered':
            if fresh['classification']!='registered':raise InstallerError('Registered host changed before verification.')
            write(journal,{**record,'status':'ALREADY_ADDED_VERIFIED'});return {'status':'ALREADY_ADDED_VERIFIED','host':self.args.host,'guests_changed':False}
        if fresh!=plan['observation']:raise InstallerError('Target/cloud changed after planning; no host changes started.')
        try:
            step('controller-claim',lambda:self.native('claim',plan))
            if plan['classification']=='returning':
                step('reconcile-owned-metadata',lambda:actor(self.runner,self.args.host,self.identity,self.pools,
                    approved=fresh['facts'],cloud=fresh['cloud'],proof=self.proof,operation=digest(str(self.output))))
            step('inputs',self.prepare_inputs)
            if controller(self.runner)!=fresh['controller']:raise InstallerError('Existing controller lifecycle changed.')
            prep=self.output/'host-preparation';prep.mkdir(mode=0o700)
            approved=onboard.observe(self.value,self.i.path,self.i.backend,prep,self.runner)
            write(prep/'approved.json',approved)
            with onboard.hold_target(self.value,self.i.backend):
                step('host-preparation',lambda:onboard.execute(self.value,self.i.path,self.i.backend,prep,self.runner,approved))
                executor=step('executor',lambda:self.install_executor(plan))
                # Prerequisites are complete. Use the existing prepared-only
                # deploy/enroll path; no repeated preparation or global deploy.
                write(self.output/'prepared-compute.json',{'schema_version':1,'configdir':str(self.new_workspace/'kolla'),
                    'allow_experimental_ubuntu26':self.args.allow_experimental_ubuntu26})
                value={**self.value,'new_compute':str(self.output/'prepared-compute.json')}
                child=self.output/'compute';child.mkdir(mode=0o700)
                deploy.plan_prepared(value,self.i.path,self.i.backend,child,self.runner)
                step('compute',lambda:deploy.apply_prepared(value,self.i.path,self.i.backend,child,self.runner))
            step('configuration',lambda:self.native('configure',plan,executor))
            # Native refresh authenticates only on the controller and uses the
            # newly committed native config/policy, never manager-local paths.
            step('catalogs',self.c.refresh)
            step('controller-completion',lambda:self.native('finish',plan))
            write(self.workspace/'journal.json',{'status':'SUPERSEDED_COMPUTE_ADDED','successor':str(self.new_workspace),
                'config':self.new_config,'plan_sha256':plan['plan_sha256']})
            record.update(status='COMPUTE_ADDED',workspace=str(self.new_workspace),config=self.new_config,completed_at=int(time.time()))
            write(journal,record);return record
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record)
            # Never stop/recover a VM. If registration was reached, fail closed
            # by disabling only this compute's future scheduling.
            if (self.output/'compute').exists():
                try:
                    self.runner.remote(self.c.controller,'HOST='+repr(self.identity['hostname'])+'\nimport openstack,json\nc=openstack.connect(verify="/etc/ssl/certs/ca-certificates.crt",api_timeout=30)\ntry:\n for s in c.compute.services(binary="nova-compute",host=HOST):c.compute.disable_service(s,disabled_reason="SPVM add-compute needs review")\n print(json.dumps({"scheduling":"disabled"}))\nfinally:c.close()\n',controller=True,auth=True)
                except Exception:write(self.output/'scheduling-review.json',{'status':'ACK_UNKNOWN_REVIEW_REQUIRED'})
            raise


def main(argv=None):
    from openstack_add_batch import main as batch_main
    return batch_main(argv)
