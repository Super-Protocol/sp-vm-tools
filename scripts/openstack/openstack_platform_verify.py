"""Read-only API/static acceptance plus isolated login/logout; never launch a VM."""
import hashlib
import http.cookiejar
import importlib.util
import json
from pathlib import Path
import ssl
import time
from urllib.request import Request,HTTPSHandler,HTTPCookieProcessor,build_opener


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
        'units':['spvm-web-nodes.service','spvm-web-capacity.timer','spvm-web-publish.timer'],
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
        if (not creation['templates'] or not creation['images'] or not creation['profiles']
                or catalogs['clusters']['clusters'] or not catalogs['provider-templates']['templates']):
            raise ValueError('new platform catalog is incomplete or already contains a Swarm')
        capacity=json.loads(Path('/run/spvm-web-capacity/capacity.json').read_text())
        if (capacity.get('schema_version')!=1 or not 0<=time.time()-capacity['observed_at']<=120
                or set(capacity['hosts'])!={h['compute_uuid'] for h in binding['hosts'].values()}
                or any(not h['enabled'] for h in capacity['hosts'].values())
                or any(not capacity['profiles'].get(p['id'],{}).get('candidate_roots') for p in creation['profiles'])):
            raise ValueError('native capacity snapshot is stale/incomplete or an initial flavor has no eligible host')
        result.update(authenticated_gui=True,template_count=len(creation['templates']),
                      image_count=len(creation['images']),profile_count=len(creation['profiles']),native_capacity_verified=True)
    finally:
        if logged_in and call(base+'/logout',{})[0]!=200:raise ValueError('acceptance logout failed')
    if list(connection.compute.servers(all_projects=True)):raise ValueError('expected empty Nova cloud')
    services=list(connection.compute.services(binary='nova-compute'))
    if len(services)!=len(binding['hosts']) or any(s.state!='up' or s.status!='enabled' for s in services):
        raise ValueError('compute service acceptance failed')
    result.update(status='PLATFORM_API_AND_GUI_VERIFIED_NO_GUESTS',compute_hosts=len(services),guests_launched=False)
    return result
