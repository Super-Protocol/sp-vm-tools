"""Fresh controller completion: real API IDs -> enrollment -> ingress -> Swarm UI.

No guest creation, hardware bootstrap, live adoption or ambiguous operation replay.
Inputs are explicit; credentials are read only from this transaction's Kolla output.
"""
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import grp
import shlex
import socket
import ssl
import subprocess
from types import SimpleNamespace as R
import uuid

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_kolla_workspace as workspace
from openstack_compute_identity import identities,verify_rule

FIELDS={'schema_version','controller','sdk_venv','network','profiles','provider_directory',
        'template_name','web_user','operator_user','runtime_dir','state_dir','origins','host_sources',
        'ingress_address','base_haproxy_config','quota_from_host_budgets','console_manifest'}
INGRESS_USER='spvm-ingress'


def account_policy(value):
    import re
    if not isinstance(value['web_user'],str) or not re.fullmatch('[a-z_][a-z0-9_-]{0,30}',value['web_user']):
        raise ValueError('explicit non-root service account name required')
    operator=pwd.getpwnam(value['operator_user'])
    try:web=pwd.getpwnam(value['web_user'])
    except KeyError:
        try:grp.getgrnam(value['web_user'])
        except KeyError:return {'action':'CREATE_SYSTEM_ACCOUNT','name':value['web_user']}
        raise ValueError('service group exists without its account; refuse adoption')
    if (web.pw_uid==0 or web.pw_uid==operator.pw_uid
            or operator.pw_gid in os.getgrouplist(value['web_user'],web.pw_gid)):
        raise ValueError('distinct non-root web and operator accounts required; no admin credentials for the web service')
    return {'action':'REUSE_DISTINCT_ACCOUNT','name':value['web_user'],'uid':web.pw_uid,'gid':web.pw_gid}


def ingress_account_policy(value):
    """A fixed non-login identity, never the administrator's SSH account."""
    if INGRESS_USER in (value['operator_user'],value['web_user']):
        raise ValueError('ingress, web and SSH identities must be distinct')
    try:account=pwd.getpwnam(INGRESS_USER)
    except KeyError:
        try:grp.getgrnam(INGRESS_USER)
        except KeyError:return {'action':'CREATE_SYSTEM_ACCOUNT','name':INGRESS_USER}
        raise ValueError('orphan ingress group requires review')
    if (account.pw_uid==0 or account.pw_shell not in ('/usr/sbin/nologin','/sbin/nologin')
            or account.pw_dir!='/nonexistent'):
        raise ValueError('dedicated non-login ingress system account required')
    ingress_groups=set(os.getgrouplist(INGRESS_USER,account.pw_gid))
    if ingress_groups!={account.pw_gid}:raise ValueError('ingress account has unexpected supplemental groups')
    for name in (value['operator_user'],value['web_user']):
        try:other=pwd.getpwnam(name)
        except KeyError:continue
        if (other.pw_uid==account.pw_uid or other.pw_gid==account.pw_gid
                or account.pw_gid in os.getgrouplist(name,other.pw_gid)):
            raise ValueError('ingress credentials must not be accessible to SSH/web identities')
    return {'action':'REUSE_DISTINCT_ACCOUNT','name':INGRESS_USER,'uid':account.pw_uid,'gid':account.pw_gid}


def prepare_account(value,output):
    result={}
    for role,policy in (('web',account_policy),('ingress',ingress_account_policy)):
        journal=Path(output)/(role+'-account.json')
        if journal.exists():raise ValueError('previous account transaction needs review')
        wanted=policy(value)
        if wanted['action']=='REUSE_DISTINCT_ACCOUNT':result[role]=wanted;continue
        write(journal,{'status':'CREATING_SYSTEM_ACCOUNT','intent':wanted})
        p=subprocess.run(['useradd','--system','--user-group','--no-create-home','--home-dir','/nonexistent',
                          '--shell','/usr/sbin/nologin',wanted['name']],capture_output=True,timeout=30)
        if p.returncode:
            write(journal,{'status':'NEEDS_REVIEW','intent':wanted});raise ValueError('service account creation failed; no adoption/retry')
        result[role]=policy(value)
        write(journal,{'status':'SERVICE_ACCOUNT_READY','account':result[role]})
    return result


def install_operator_auth(authdir, auth, operator_group, region, ca):
    """Install root-owned auth readable only by its explicitly selected group.

    Administrative auth uses gid 0; ingress auth uses the dedicated service gid.
    The SSH user's group must never be passed here.
    """
    authdir=Path(authdir)
    authdir.mkdir(mode=0o750,exist_ok=False)
    os.chown(authdir,0,operator_group);authdir.chmod(0o750)
    names={'auth_url':'OS_AUTH_URL','username':'OS_USERNAME','password':'OS_PASSWORD','project_name':'OS_PROJECT_NAME',
           'user_domain_name':'OS_USER_DOMAIN_NAME','project_domain_name':'OS_PROJECT_DOMAIN_NAME'}
    env={target:auth[source] for source,target in names.items()}
    env.update(OS_IDENTITY_API_VERSION='3',OS_INTERFACE='internal',OS_REGION_NAME=region,OS_CACERT=ca)
    target=authdir/'admin-openrc.sh'
    with target.open('x') as stream:
        stream.write('for spvm_auth_var in "${!OS_@}"; do unset "$spvm_auth_var"; done\nunset spvm_auth_var\n')
        stream.write(''.join('export '+k+'='+shlex.quote(str(v))+'\n' for k,v in env.items()))
    target.chmod(0o640);os.chown(target,0,operator_group)


def specification(filename):
    value=json.loads(read(filename))
    if (not isinstance(value,dict) or set(value)-{'release_registration'}!=FIELDS or type(value['schema_version']) is not int
            or value['schema_version']!=1 or value['quota_from_host_budgets'] is not True):
        raise ValueError('exact fresh platform completion specification required')
    if 'release_registration' in value and type(value['release_registration']) is not bool:
        raise ValueError('explicit boolean release registration opt-in required')
    for key in ('sdk_venv','network','profiles','provider_directory','runtime_dir','state_dir','base_haproxy_config','console_manifest'):
        p=Path(value[key])
        if not p.is_absolute() or p.resolve()!=p or any(c.isspace() for c in str(p)):
            raise ValueError('canonical whitespace-free platform paths required')
    if not value['host_sources'] or not isinstance(value['host_sources'],list):raise ValueError('exact host /32 sources required')
    for source in value['host_sources']:
        net=ipaddress.IPv4Network(source,strict=True)
        if net.prefixlen!=32 or str(net)!=source:raise ValueError('exact host /32 sources required')
    if len(set(value['host_sources']))!=len(value['host_sources']):raise ValueError('duplicate host source')
    address=ipaddress.IPv4Address(value['ingress_address'])
    if str(address)!=value['ingress_address'] or address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError('assigned canonical ingress address required')
    from urllib.parse import urlsplit
    if not isinstance(value['origins'],list) or not value['origins']:raise ValueError('explicit browser origins required')
    for origin in value['origins']:
        u=urlsplit(origin)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
            raise ValueError('credential-free HTTPS browser origins required')
    import re
    if not re.fullmatch('[a-z][a-z0-9-]{0,62}',value['template_name']):raise ValueError('DNS-safe template name required')
    account_policy(value)
    ingress_account_policy(value)
    return value


def input_files(filename):
    value=specification(filename)
    if value['base_haproxy_config']=='/etc/haproxy/haproxy.cfg':
        raise ValueError('use an immutable operator copy of the initial HAProxy baseline')
    paths=[Path(filename),Path(value['network']),Path(value['profiles']),Path(value['base_haproxy_config']),Path(value['console_manifest'])]
    root=Path(value['provider_directory'])
    if not root.is_dir() or root.is_symlink():raise ValueError('existing approved provider directory required')
    # Do not leak the values, including nested credentials, into the public plan.
    from openstack_spvm import secrets,provider_templates
    provider_templates.validate_files(secrets.provider_files(str(root)))
    for p in root.rglob('*'):
        if p.is_symlink():raise ValueError('provider symlinks refused')
        if p.is_file():paths.append(p)
    if not (Path(value['sdk_venv'])/'pyvenv.cfg').is_file():raise ValueError('prepared SDK environment required')
    return {str(p):file_hash(p) for p in paths}


def sdk_digest(filename):
    root=Path(specification(filename)['sdk_venv']);members={}
    for p in sorted(root.rglob('*')):
        if '__pycache__' in p.parts:continue
        if p.is_symlink():members[str(p.relative_to(root))]=['link',os.readlink(p)]
        elif p.is_file():members[str(p.relative_to(root))]=[p.stat().st_mode & 0o777,file_hash(p)]
        elif not p.is_dir():raise ValueError('unexpected SDK environment member')
    members['selected-python-sha256']=file_hash(root/'bin/python')
    return digest(members)


def require_web_separation(path):
    base=Path(path).parent.parent
    manifest=base/'manifest.json'
    if not manifest.is_file():manifest=base/'nova-spvm/manifest.json'
    value=json.loads(read(manifest))
    if 'separate-web-ingress-identities' not in value.get('capabilities',[]):
        raise ValueError('selected driver bundle must support separate-web-ingress-identities; build/select an updated release before fresh deployment')
    if 'private-publication-input-handoff' not in value.get('capabilities',[]):
        raise ValueError('selected driver bundle must support private-publication-input-handoff; select an updated release')


def initial_probe_request(wanted, value, host):
    """Defer only interfaces/VIP created by the validated owned network recipe."""
    result={**wanted}
    if 'preparation' not in value:return result
    recipe=json.loads(read(value['preparation']))
    if 'network_request' not in recipe:return result
    network=json.loads(read(recipe['network_request']))
    result['api_vip']=None
    if network['nat']['host']==host:
        result['interfaces']=[name for name in wanted['interfaces'] if name!='spvm-ext0']
    return result


def preflight(value,path,backend):
    """Read-only early identity and local-controller proof, before native deploy."""
    require_web_separation(path)
    spec=specification(value['platform']);cfg=backend.deployment_config(value['config'])
    if spec.get('release_registration'):
        manifest=Path(path).parent.parent/'manifest.json'
        if not manifest.is_file():manifest=Path(path).parent.parent/'nova-spvm/manifest.json'
        if 'administrator-release-registration' not in json.loads(read(manifest)).get('capabilities',[]):
            raise ValueError('release registration requires an updated driver bundle')
    controller=cfg['hosts'].get(spec['controller'])
    if (os.geteuid()!=0 or not controller or 'control' not in controller['roles']
            or sum('control' in h['roles'] for h in cfg['hosts'].values())!=1):
        raise ValueError('fresh platform completion is root-local on its single controller')
    if controller['user']!=spec['operator_user']:raise ValueError('operator must be the configured controller SSH user')
    audits=backend.host_audits(cfg)
    preparation_intent=None
    if 'preparation' in value:
        mappings=preparation_spec(value,backend,audits)
        if set(spec['host_sources'])!={ip+'/32' for ip in mappings.values()}:
            raise ValueError('web host-source policy must match approved management addresses')
        recipe=json.loads(read(value['preparation']))
        preparation_intent={'hostnames':mappings,'network_phases_enabled':'network_request' in recipe,
                            'repair_stale_networkd_cache':recipe.get('repair_stale_networkd_cache',False)}
    local=hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()
    if audits[spec['controller']]['host_identity_sha256']!=local:raise ValueError('not the selected controller')
    binding=json.loads(read(Path(value['workspace'])/'compute-binding.json'))
    if spec.get('release_registration') and not all(
            'registered_release_cache_root' in json.loads(read(h['request_file'])) for h in binding['hosts'].values()):
        raise ValueError('release registration requires explicit isolated cache roots on every compute before deployment')
    verify_rule(workspace.check(value['workspace']),identities(cfg,audits))
    for name,wanted in identities(cfg,audits).items():
        if binding['hosts'][name]['compute_uuid']!=wanted['compute_uuid']:
            raise ValueError('stage-kolla-identities must precede helper installation and compute binding')
    # Refuse a live cloud BEFORE hostname/network/auth changes. Missing planned
    # VIP is the only deferred fresh-host check; the owned network phase and
    # native Kolla plan must prove it ready before service deployment.
    import openstack_kolla_prechecks as checks
    args=R(config=value['config'],workspace=value['workspace'],known_hosts=value['known_hosts'],
        allow_experimental_ubuntu26=value['allow_experimental_ubuntu26'],use_testing_images=value['use_testing_images'])
    _,_,_,code,intent=checks.inputs(args,path,backend)
    for name,host in cfg['hosts'].items():
        wanted=initial_probe_request(intent['requests'][name],value,name)
        facts=checks.observe(backend,host,code,wanted,value['known_hosts'])
        if facts.get('blockers'):raise ValueError('fresh platform host blocked: '+name+':'+','.join(facts['blockers']))
    import openstack  # fail before deploy if the selected operator Python lacks SDK
    # Never accept project/network IDs copied from an old cloud.
    network=json.loads(read(spec['network']))
    if set(network)!={'schema_version','auth_url','region_name','external','guest','router'}:
        raise ValueError('network template must omit project_id; resolve it from the new API')
    if (network['auth_url']!=binding['bootstrap_runtime']['auth_url']
            or network['region_name']!=binding['bootstrap_runtime']['region_name']):
        raise ValueError('network template points at another cloud')
    if not cfg['kolla'].get('skyline_console_image_id'):raise ValueError('pinned custom console image required')
    if Path(spec['runtime_dir']).exists() or Path(spec['state_dir']).exists():
        raise ValueError('fresh web targets occupied; no adoption/overwrite')
    for name in ('/etc/spvm/auth','/etc/spvm/ingress-auth','/var/lib/spvm-ingress-inputs','/etc/spvm/web-project-enrollment.json','/etc/spvm/web-publication-policy.json','/etc/spvm/ingress-policy.json'):
        if Path(name).exists() or Path(name).is_symlink():raise ValueError('occupied platform target before initial deploy')
    for name in ('spvm-web-nodes.service','spvm-web-capacity.service','spvm-web-capacity.timer','spvm-web-publish.service','spvm-web-publish.timer'):
        p=subprocess.run(['systemctl','show',name,'--property=LoadState','--value'],capture_output=True,text=True,timeout=10)
        if p.returncode not in (0,1) or p.stdout.strip()!='not-found':raise ValueError('cached/occupied platform unit before deploy')
    for action,wanted in (('is-active','inactive'),('is-enabled','disabled')):
        p=subprocess.run(['systemctl',action,'haproxy.service'],capture_output=True,text=True,timeout=10)
        if p.stdout.strip()!=wanted:raise ValueError('fresh host ingress must be inactive/disabled')
    if read('/etc/haproxy/haproxy.cfg')!=read(spec['base_haproxy_config']):raise ValueError('immutable HAProxy baseline differs from host')
    with socket.socket() as probe:probe.bind(('127.0.0.1',18776))
    addresses=json.loads(subprocess.run(['ip','-j','address','show'],check=True,capture_output=True,text=True,timeout=10).stdout)
    if not any(a.get('local')==spec['ingress_address'] for link in addresses for a in link.get('addr_info',[])):
        raise ValueError('selected ingress address is not assigned to controller')
    return {'status':'PLATFORM_COMPLETION_INTENT_VALIDATED','native_identities':identities(cfg,audits),
            'input_sha256':input_files(value['platform']),'sdk_sha256':sdk_digest(value['platform']),
            'web_account':account_policy(spec),'ingress_account':ingress_account_policy(spec),
            'preparation':preparation_intent,'guests_launched':False}


def preparation_spec(value,backend,audits):
    network=json.loads(read(value['preparation']))
    cfg=backend.deployment_config(value['config'])
    if (set(network)-{'network_request','geneve_request','repair_stale_networkd_cache'}!={'schema_version','hosts'}
            or bool('network_request' in network)!=bool('geneve_request' in network)
            or type(network['schema_version']) is not int or network['schema_version']!=1
            or set(network['hosts'])!=set(cfg['hosts'])):
        raise ValueError('complete hostname preparation addresses required')
    if type(network.get('repair_stale_networkd_cache',False)) is not bool:raise ValueError('explicit cache repair boolean required')
    mappings={}
    for name,address in network['hosts'].items():
        address=str(ipaddress.IPv4Address(address));nodename=audits[name]['hostname']
        if nodename in mappings or address in mappings.values():raise ValueError('duplicate management identity')
        mappings[nodename]=address
    if 'network_request' in network:
        import openstack_network_prepare as host_network,openstack_geneve_guard as geneve
        for name in cfg['hosts']:
            args=R(config=value['config'],network_request=network['network_request'],host=name,
                allow_experimental_ubuntu26=value['allow_experimental_ubuntu26'],action='plan-management')
            _,policy,_=host_network.inputs(args,backend)
            if str(ipaddress.IPv4Interface(policy['management_address']).ip)!=network['hosts'][name]:
                raise ValueError('hostname and management phase addresses differ')
        for name,host in cfg['hosts'].items():
            if not set(host['roles']) & {'compute','network'}:continue
            args=R(config=value['config'],network_request=network['geneve_request'],host=name,
                allow_experimental_ubuntu26=value['allow_experimental_ubuntu26'],action='plan-geneve-guard')
            _,_,binding=geneve.inputs(args,backend)
            if binding['machine_sha256']!=audits[name]['host_identity_sha256']:raise ValueError('Geneve host identity mismatch')
    return mappings


def prepare_hostnames(value,path,backend,output):
    """Run the shipped owned phase over strict SSH, not a lab-specific script."""
    cfg=backend.deployment_config(value['config']);audits=backend.host_audits(cfg)
    mappings=preparation_spec(value,backend,audits)
    sources={p.name:read(p).decode() for p in Path(__file__).parent.glob('*.py')}
    for name,host in cfg['hosts'].items():
        request={'schema_version':1,'machine_sha256':audits[name]['host_identity_sha256'],'mappings':mappings}
        remote_output='/var/lib/spvm-installer/'+cfg['deployment_id']+'/hostnames-'+output.name+'-'+name
        # Source is selected by the approved installer tree; never browser input.
        source='SOURCES='+repr(sources)+'\nREQUEST='+repr(request)+'\nOUTPUT='+repr(remote_output)+'\n'+'''
import tempfile,sys,json,os
from pathlib import Path
from types import SimpleNamespace as R
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='spvm-hostnames-') as directory:
 root=Path(directory)
 for name,text in SOURCES.items():(root/name).write_text(text)
 sys.path.insert(0,str(root))
 import openstack_hostnames as phase
 from openstack_container_engine import write
 target=Path(OUTPUT);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 for parent in (target.parent,*target.parent.parents):
  if parent.is_symlink() or parent.stat().st_uid!=0 or parent.stat().st_mode & 0o022:raise ValueError('untrusted transaction parent')
 request=root/'request.json';write(request,REQUEST)
 args=R(action='plan-hostnames',request=str(request),output=str(target),plan_sha256=None)
 plan=phase.execute(args);args.action='prepare-hostnames';args.plan_sha256=plan['plan_sha256']
 result=phase.execute(args)
 print(json.dumps({'status':result['status'],'changes':result['changes']}))
'''
        process=subprocess.run(backend.ssh_arguments(host)+['sudo -n /usr/bin/python3 -'],input=source,
                               text=True,capture_output=True,timeout=180)
        if process.returncode:raise ValueError('owned hostname preparation refused; inspect remote journal')
        result=json.loads(process.stdout)
        if result['status']!='MANAGEMENT_HOSTNAMES_READY':raise ValueError('hostname preparation incomplete')
        write(output/('hostnames-'+name+'.json'),result)


def post_install(value,path,backend,output,core):
    """No resume: every mutating child owns a WAL; ambiguous failures require review."""
    from openstack.connection import Connection
    from keystoneauth1.identity.v3 import Password
    from keystoneauth1.session import Session
    import openstack_spvm_network as network
    import openstack_spvm_enrollment as enrollment
    import openstack_spvm_resources as resources
    import openstack_runtime_enrollment as runtime
    import openstack_ingress as ingress
    import openstack_skyline_runtime as web
    from openstack_spvm import secrets,provider_templates
    from initial_compute import Backend
    from compute_transaction import execute as activate
    import yaml
    spec=specification(value['platform']);cfg=backend.deployment_config(value['config'])
    output.mkdir(mode=0o700,exist_ok=False)
    journal=output/'platform-transaction.json';record={'status':'RUNNING','completed':[],'active_phase':None,'guests_launched':False}
    write(journal,record)
    def phase(name,callback):
        record['active_phase']=name;write(journal,record)
        try:result=callback()
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        record['completed'].append(name);record['active_phase']=None;write(journal,record)
        return result
    clouds=Path(core)/'kolla-run/clouds.yaml'
    config=yaml.safe_load(read(clouds));profile='kolla-admin-internal'
    auth=config['clouds'][profile]['auth'];ca='/etc/ssl/certs/ca-certificates.crt'
    binding=json.loads(read(Path(value['workspace'])/'compute-binding.json'))
    if auth['auth_url'].rstrip('/').removesuffix('/v3')!=binding['bootstrap_runtime']['auth_url'].rstrip('/').removesuffix('/v3'):
        raise ValueError('new Kolla auth origin differs from approved compute binding')
    # Do not modify the caller's auth environment or read an old named cloud.
    session=Session(auth=Password(**auth),verify=ca,timeout=30)
    connection=Connection(session=session,region_name=binding['bootstrap_runtime']['region_name'],
                          interface='internal',api_timeout=30)
    try:
        project=connection.session.get_project_id();user=connection.session.get_user_id()
        access=connection.session.auth.get_access(connection.session)
        if 'admin' not in access.role_names or list(connection.compute.servers(all_projects=True)):
            raise ValueError('initial enrollment requires explicit empty administrator scope')
        services=list(connection.compute.services(binary='nova-compute'))
        if len(services)!=len(binding['hosts']) or any(s.state!='up' for s in services):raise ValueError('computes not healthy')
        group=pwd.getpwnam(spec['web_user']).pw_gid
        ingress_group=pwd.getpwnam(INGRESS_USER).pw_gid
        authdir=Path('/etc/spvm/auth')
        ingress_authdir=Path('/etc/spvm/ingress-auth')
        def private_operator_auth():
            install_operator_auth(authdir,auth,0,binding['bootstrap_runtime']['region_name'],ca)
            install_operator_auth(ingress_authdir,auth,ingress_group,binding['bootstrap_runtime']['region_name'],ca)
        phase('operator-auth',private_operator_auth)
        def shared_network():
            request={**json.loads(read(spec['network'])),'project_id':project};request_path=output/'network-request.json';write(request_path,request)
            args=R(action='plan-spvm-network',request=str(request_path),cloud=profile,ca_file=ca,output=str(output/'network'),plan_sha256=None)
            plan=network.execute(args,path,connection);args.action='apply-spvm-network';args.plan_sha256=plan['plan_sha256']
            return network.execute(args,path,connection)
        net=phase('shared-network',shared_network)
        def stage_enrollment():
            request=output/'enrollment-request.json'
            write(request,{'schema_version':1,'projects':{project:{'publisher_user_ids':[user],'reader_user_ids':[user],
                'network_ids':[net['network_id']],'releases':[cfg['guest_release']],
                **({'release_registry_user_ids':[user]} if spec.get('release_registration') else {})}}})
            return enrollment.stage(R(workspace=value['workspace'],request=str(request),profiles=spec['profiles'],output=str(output/'enrollment')),path)
        phase('stage-enrollment',stage_enrollment)
        enrollment_path=str(output/'enrollment/enrollment-plan.json')
        def project_resources():
            args=R(action='plan-spvm-resources',enrollment_plan=enrollment_path,project_id=project,cloud=profile,ca_file=ca,output=str(output/'resources'),plan_sha256=None)
            plan=resources.execute(args,path,connection);args.action='apply-spvm-resources';args.plan_sha256=plan['plan_sha256']
            resources.execute(args,path,connection)
            args.action='check-spvm-barbican';args.output=str(output/'barbican')
            return resources.execute(args,path,connection)
        probe=phase('project-resources-and-barbican',project_resources)
        if not probe['passed'] or not probe['cleanup_complete']:raise ValueError('Barbican probe failed')
        phase('runtime-requests',lambda:runtime.stage(R(workspace=value['workspace'],enrollment_plan=enrollment_path,output=str(output/'runtime-requests')),path))
        for name in sorted(binding['hosts']):
            def activate_compute(name=name):
                target=output/('compute-'+name);target.mkdir(mode=0o700)
                wanted=json.loads(read(output/'runtime-requests'/(name+'.json')))
                runner=Backend(cfg,spec['controller'],str(Path(spec['sdk_venv'])/'bin/python'),str(authdir/'admin-openrc.sh'),target/'observations',enrollment=wanted)
                plan=runner.plan(name);write(target/'plan.json',plan)
                result=activate(plan,plan['plan_sha256'],target/'transaction',runner)
                if result['status']!='COMPUTE_READY':raise ValueError('initial compute activation incomplete')
                return {'status':result['status'],'guests_restarted':result['guests_restarted']}
            phase('compute-'+name,activate_compute)
        def quota():
            import resource_audit
            physical=resource_audit.validate(cfg,backend.host_audits(cfg))
            limits={'cores':sum(h['vm_capacity_ceiling']['vcpus'] for h in physical['hosts'].values()),
                    'ram':sum(h['vm_capacity_ceiling']['memory_mb'] for h in physical['hosts'].values())}
            limits['instances']=limits['cores'];before=connection.compute.get_quota_set(project)
            write(output/'quota-intent.json',{'project':project,'limits':limits,'before':{k:getattr(before,k) for k in limits}})
            connection.compute.update_quota_set(project,**limits);after=connection.compute.get_quota_set(project)
            if any(getattr(after,k)!=v for k,v in limits.items()):raise ValueError('quota validation failed')
            return limits
        phase('quota',quota)
        def ingress_scaffold():
            base=Path(spec['base_haproxy_config'])
            request={'schema_version':1,'machine_sha256':hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest(),
                'bind_address':spec['ingress_address'],'allowed_cidr':json.loads(read(spec['network']))['guest']['cidr'],
                'base_config_sha256':file_hash(base),'publisher':{'run_as':INGRESS_USER,'auth_script':str(ingress_authdir/'admin-openrc.sh'),
                    'auth_args':[],'ca_bundle':ca,'openstack_cli':str(Path(spec['sdk_venv'])/'bin/openstack')}}
            request_path=output/'ingress-request.json';write(request_path,request)
            staged=ingress.stage(R(request=str(request_path),base_config=str(base),output=str(output/'ingress-package')),path)
            args=R(action='plan-ingress',package=str(output/'ingress-package'),package_sha256=staged['package_sha256'],output=str(output/'ingress-install'),plan_sha256=None)
            plan=ingress.execute(args,path);args.action='install-ingress';args.plan_sha256=plan['plan_sha256'];ingress.execute(args,path)
            args.action='plan-ingress-activation';args.output=str(output/'ingress-activation');args.plan_sha256=None
            plan=ingress.execute(args,path);args.action='activate-ingress';args.plan_sha256=plan['plan_sha256']
            return ingress.execute(args,path)
        phase('ingress',ingress_scaffold)
        def web_stage():
            groups=list(connection.network.security_groups(project_id=project,name='default'))
            if len(groups)!=1:raise ValueError('one project default SG required for template policy')
            enrolled={'schema_version':1,'project_id':project,'publisher_user_ids':[user],'reader_user_ids':[user],
                'network_id':net['network_id'],'security_group_ids':[groups[0].id]}
            target=Path('/etc/spvm/web-project-enrollment.json')
            with target.open('x') as stream:json.dump(enrolled,stream)
            target.chmod(0o640);os.chown(target,0,group)
            files=secrets.provider_files(spec['provider_directory']);zone=provider_templates.validate_files(files)
            provider=yaml.safe_load(files['swarm/config.yaml']);gateway=provider.get('gateway_hostname')
            if not isinstance(gateway,str) or not gateway.endswith('.'+zone):raise ValueError('gateway must belong to provider zone')
            request_id=str(uuid.uuid4());write(output/'template-intent.json',{'name':spec['template_name'],'request_id':request_id})
            provider_templates.create(connection.key_manager,enrolled,spec['template_name'],files,request_id)
            config={'profile_url':'https://'+cfg['network']['api_vip']+':9998/api/v1/profile',
                'auth_url':binding['bootstrap_runtime']['auth_url'],'ca_file':ca,'origins':spec['origins'],'state_dir':spec['state_dir'],
                'capacity_file':'/run/spvm-web-capacity/capacity.json','clusters':{},
                'provider_template_policy':{'enrollment':str(target),'host_sources':spec['host_sources']}}
            if spec.get('release_registration'):
                prepared=[json.loads(read(h['request_file'])) for h in binding['hosts'].values()]
                if not all('registered_release_cache_root' in h for h in prepared):
                    raise ValueError('release registration requires prepared isolated cache roots on every compute')
                config['release_registration']={'project_id':project,'admin_user_ids':[user],
                    'reader_user_ids':[user],'allow_debug':all(h['allow_debug'] for h in prepared)}
            policy={'schema_version':1,'run_as':spec['web_user'],'python':str(Path(spec['sdk_venv'])/'bin/python'),
                'state_dir':spec['state_dir'],'project_id':project,'domain_suffix':zone,'gateway_hostname':gateway,
                'public_ip':spec['ingress_address'],'timeout':1800}
            write(output/'web-config.json',config);write(output/'publication-policy.json',policy)
            source=Path(path).parent/'skyline/stage_runtime.py'
            definition=importlib.util.spec_from_file_location('platform_web_stage',source)
            stager=importlib.util.module_from_spec(definition);definition.loader.exec_module(stager)
            wheels=list((Path(path).parent.parent/'wheels').glob('nova_spvm-*.whl'))
            if len(wheels)!=1:raise ValueError('one verified bundle client wheel required')
            return stager.stage(R(output=spec['runtime_dir'],sdk_venv=spec['sdk_venv'],web_user=spec['web_user'],
                web_config=str(output/'web-config.json'),wheel=str(wheels[0]),wheel_sha256=file_hash(wheels[0]),publication_policy=str(output/'publication-policy.json')))
        phase('web-stage',web_stage)
        def web_install():
            args=R(action='plan-skyline-runtime',runtime_dir=spec['runtime_dir'],runtime_sha256=file_hash(Path(spec['runtime_dir'])/'runtime-stage.json'),
                   output=str(output/'web-install'),plan_sha256=None)
            plan=web.execute(path,args);args.action='install-skyline-runtime';args.plan_sha256=plan['plan_sha256']
            lock=web.lock_install()
            try:return web.execute(path,args)
            finally:os.close(lock)
        phase('web-install',web_install)
        # Full authenticated GUI acceptance is implemented separately to keep
        # tokens/cookies strictly in memory and out of all phase receipts.
        from openstack_platform_verify import verify
        accepted=phase('verify-platform',lambda:verify({**spec,'console_image_id':cfg['kolla']['skyline_console_image_id']},path,connection,auth,binding))
        if list(connection.compute.servers(all_projects=True)):raise ValueError('unexpected guest appeared during installation')
        record.update(status='PLATFORM_READY_NO_SWARM_CREATED',acceptance=accepted)
        write(journal,record)
        return {'status':record['status'],'completed':record['completed'],'guests_launched':False}
    finally:connection.close()
