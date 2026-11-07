"""Read-only API/static acceptance plus isolated login/logout; never launch a VM."""
import hashlib
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import ssl
import time
from urllib.request import Request,HTTPSHandler,HTTPCookieProcessor,build_opener


def verify_initial_resources(connection,binding,capacity,lifecycle):
    """Fresh enrollment is technically ready but has zero workload admission."""
    roots={h['compute_uuid'] for h in binding['hosts'].values()}
    if (capacity.get('schema_version')!=1 or not 0<=time.time()-capacity['observed_at']<=120
            or set(capacity['hosts'])!=roots
            or any(h.get('enabled') is not False or h.get('gpu_free') for h in capacity['hosts'].values())
            or any(p.get('candidate_roots') for p in capacity['profiles'].values())):
        raise ValueError('initial capacity must be fresh, complete and disabled without workload candidates')
    if (len(lifecycle)!=len(roots) or {r['resources']['compute_uuid'] for r in lifecycle}!=roots):
        raise ValueError('initial lifecycle catalogs must cover the exact compute fleet')
    for row in lifecycle:
        value=row['resources']
        if (row['status']!='UNCONFIGURED' or row['nova_status']!='disabled' or not row['technical_ready']
                or value['enabled'] is not False or value['vcpus'] is not None or value['memory_mb'] is not None
                or value['pools'] or value['transaction'] is not None
                or set(row['blockers'])!={'CPU_RAM_NOT_CONFIGURED','STATE_POOL_NOT_CONFIGURED'}):
            raise ValueError('fresh compute must be healthy, UNCONFIGURED and disabled with no assigned resources')
        root=value['compute_uuid']
        response=connection.placement.get('/resource_providers/'+root+'/inventories',microversion='1.39');response.raise_for_status()
        inventories=response.json()['inventories']
        for name in ('VCPU','MEMORY_MB'):
            current=inventories.get(name,{})
            if (type(current.get('total')) is not int or current['total']<=0
                    or current.get('reserved')!=current['total'] or current.get('allocation_ratio')!=1.0):
                raise ValueError('initial CPU/RAM inventory requires real positive totals and zero available capacity')
        response=connection.placement.get('/resource_providers?in_tree='+root,microversion='1.39');response.raise_for_status()
        if len(response.json()['resource_providers'])!=1:
            raise ValueError('unconfigured compute must not publish workload pool or GPU providers')
        response=connection.placement.get('/resource_providers/'+root+'/traits',microversion='1.39');response.raise_for_status()
        if set(response.json()['traits'])&{'CUSTOM_SPVM_CC_READY','CUSTOM_SPVM_DEBUG_READY'}:
            raise ValueError('unconfigured compute must not advertise SPVM workload readiness')
    return {'resource_status':'UNCONFIGURED','scheduling_enabled':False,'resources_assigned':False}


def verify(spec,path,connection,auth,binding):
    root=Path(path).parent/'skyline'
    def load(name):
        definition=importlib.util.spec_from_file_location('platform_'+name,root/(name+'.py'))
        module=importlib.util.module_from_spec(definition);definition.loader.exec_module(module);return module
    assets=load('verify_assets');installed=load('verify_installation')
    from urllib.parse import urlsplit
    auth_url=urlsplit(binding['bootstrap_runtime']['auth_url'])
    origin='https://'+auth_url.hostname+':9999'
    context=ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')
    opener=build_opener(HTTPSHandler(context=context))
    def fetch(url,encoding):
        with opener.open(Request(url,headers={'Accept-Encoding':encoding}),timeout=20) as response:
            return response.headers.get('Content-Encoding'),response.read(assets.LIMIT+1)
    def probe(url):
        with opener.open(url,timeout=20) as response:return response.status
    runtime=Path(spec['runtime_dir']);stage=json.loads((runtime/'runtime-stage.json').read_text())
    files={str(runtime/'config.json'):hashlib.sha256((runtime/'config.json').read_bytes()).hexdigest()}
    for name,sha in stage['files_sha256'].items():
        if name.startswith('units/'):files['/etc/systemd/system/'+Path(name).name]=sha
    result=installed.verify({'image_id':spec['console_image_id'],'files_sha256':files,
        'units':['spvm-web-nodes.service','spvm-web-capacity.timer'],
        'manifest':spec['console_manifest'],'origin':origin},assets.verify,fetch,probe)
    jar=http.cookiejar.CookieJar();session=build_opener(HTTPSHandler(context=context),HTTPCookieProcessor(jar))
    base=origin+'/api/openstack/skyline/api/v1'
    def call(url,body=None):
        request=Request(url,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        with session.open(request,timeout=30) as response:
            data=response.read(2*1024*1024+1)
            if len(data)>2*1024*1024:raise ValueError('bounded GUI response required')
            return response.status,json.loads(data) if data else None
    logged_in=False
    try:
        status,config=call(base+'/config')
        if status!=200 or not config.get('default_domain'):raise ValueError('Skyline domain selection unavailable')
        status,_=call(base+'/login',{'username':auth['username'],'password':auth['password'],
            'domain':auth['user_domain_name'],'region':binding['bootstrap_runtime']['region_name']})
        logged_in=True
        if status!=200 or call(base+'/profile')[0]!=200 or call(base+'/contrib/domains')[0]!=200:
            raise ValueError('Skyline authenticated profile/domain check failed')
        catalogs={}
        for route in ('cluster-catalog','provider-templates','clusters'):
            status,document=call(origin+'/spvm-api/v1/'+route)
            if status!=200:raise ValueError('authenticated Swarm catalog unavailable')
            catalogs[route]=document
        creation=catalogs['cluster-catalog']
        if (creation['templates'] or creation['images'] or creation['profiles']
                or catalogs['clusters']['clusters'] or catalogs['provider-templates']['templates']):
            raise ValueError('installation must leave image, flavor, provider and Swarm catalogs empty')
        capacity=json.loads(Path('/run/spvm-web-capacity/capacity.json').read_text())
        from openstack_spvm import host_lifecycle,host_gpus,host_pools
        policy=json.loads(Path('/etc/spvm/host-budget-policy.json').read_text())
        publishers=policy['reader_user_ids']
        initial=verify_initial_resources(connection,binding,capacity,host_lifecycle.catalogs(connection,publishers))
        if (any(p['pools'] for p in host_pools.catalogs(connection,publishers))
                or any(d['enabled'] for h in host_gpus.catalogs(connection,publishers) for d in h['configuration']['devices'])):
            raise ValueError('initial pool and GPU configuration must remain unassigned')
        result.update(authenticated_gui=True,template_count=len(creation['templates']),
                      image_count=len(creation['images']),profile_count=len(creation['profiles']),native_capacity_verified=True,**initial)
    finally:
        if logged_in and call(base+'/logout',{})[0]!=200:raise ValueError('acceptance logout failed')
    if list(connection.compute.servers(all_projects=True)):raise ValueError('expected empty Nova cloud')
    services=list(connection.compute.services(binary='nova-compute'))
    if len(services)!=len(binding['hosts']) or any(s.state!='up' or s.status!='disabled' for s in services):
        raise ValueError('fresh compute services must be up and administratively disabled')
    result.update(status='PLATFORM_API_AND_GUI_VERIFIED_NO_GUESTS',compute_hosts=len(services),guests_launched=False)
    return result
