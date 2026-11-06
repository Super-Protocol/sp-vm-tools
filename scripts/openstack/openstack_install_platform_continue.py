"""Reviewed platform-only continuation of the exact OVN ha=False refusal.

No generic failed-workspace adoption, Kolla replay or credential recreation.
Original journals stay intact; a new controller source/data directory owns all
remaining work and uses the already verified, unchanged SDK/driver wheel.
"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace as R

from openstack_container_engine import digest,file_hash,write
from openstack_errors import InstallerError
from openstack_install_continue import document,private,protected_equal


def reviewed(origin):
    from openstack_install import PHASES,protected_parent
    origin=Path(origin);protected_parent(origin)
    if origin.stat().st_mode&0o077 or (origin/'continued-by.json').exists():
        raise InstallerError('Private, not-yet-continued platform transaction required.')
    parent=document(origin/'installation.json')
    if (parent.get('schema_version')!=1 or parent.get('status')!='NEEDS_REVIEW'
            or parent.get('active_phase')!='platform' or parent.get('completed')!=list(PHASES[:-1])
            or parent.get('guests_created') is not False):
        raise InstallerError('Only a platform failure after successful Kolla is eligible.')
    result=document(origin/'kolla-result.json');core=document(origin/'core/install-transaction.json')
    if (result.get('status')!='KOLLA_DEPLOYED_NOT_SPVM_ENROLLED' or result.get('guests_launched') is not False
            or core.get('status')!='KOLLA_DEPLOYED_NOT_SPVM_ENROLLED'
            or core.get('completed')!=['prechecks','deploy','post-deploy']):
        raise InstallerError('Kolla must have completed all phases; it will not be repeated.')
    log=private(origin/'private-controller-completion.log').read_bytes()
    if b'Provider ovn does not support ha=False' not in log:
        raise InstallerError('Only the reviewed OVN router HA refusal is eligible.')
    base=Path(parent.get('continued_from',''))
    protected_parent(base)
    marker=document(base/'continued-by.json')
    if marker.get('output')!=str(origin):raise InstallerError('Prepared-run lineage differs.')
    if document(base/'installation.json').get('completed')!=list(PHASES[:-2]):
        raise InstallerError('Unrecognized prepared-run lineage.')
    return parent,base,core


def validate_bundle_delta(old,new):
    allowed={'deployment/spvm_network.py','deployment/README.md'}
    a,b=old['source_sha256'],new['source_sha256']
    changed={name for name in a if a.get(name)!=b.get(name)}
    if (old['version']!=new['version'] or old['wheel_sha256']!=new['wheel_sha256']
            or old.get('capabilities')!=new.get('capabilities') or set(a)!=set(b)
            or not changed<=allowed or 'deployment/spvm_network.py' not in changed):
        raise InstallerError('Platform continuation permits only the reviewed network-provisioning source update with an unchanged wheel.')


def probe(old_root,source_root):
    """Controller-local GET/authentication only. Never print secret documents."""
    import hashlib,pwd,shlex,yaml
    from keystoneauth1.identity.v3 import Password,ApplicationCredential
    from keystoneauth1.session import Session
    from openstack.connection import Connection
    from openstack_installer import backend
    import openstack_platform as platform
    old_root,source_root=Path(old_root),Path(source_root)
    if (old_root.parent!=Path('/opt/spvm-openstack') or source_root.parent!=old_root.parent
            or old_root.resolve()!=old_root or source_root.resolve()!=source_root):
        raise ValueError('canonical versioned controller directories required')
    data=old_root/'data';prepared=document(data/'prepared.json')
    if (prepared['machine']!=hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()
            or prepared['config_sha256']!=file_hash(data/'deployment.yaml')
            or prepared['binding_sha256']!=file_hash(data/'compute-binding.json')
            or prepared['sdk_packages_sha256']!=file_hash(data/'sdk-packages.txt')):
        raise ValueError('prepared controller input drift')
    backend(source_root/'driver',verified_bundle=True)
    old_manifest=json.loads((old_root/'driver/manifest.json').read_bytes())
    new_manifest=json.loads((source_root/'driver/manifest.json').read_bytes())
    validate_bundle_delta(old_manifest,new_manifest)
    frozen=subprocess.run([str(old_root/'sdk/bin/python'),'-m','pip','freeze','--all'],check=True,
        capture_output=True,text=True,timeout=60).stdout
    if frozen!=(data/'sdk-packages.txt').read_text():raise ValueError('installed SDK dependency versions changed')
    for package in ('nova_spvm','openstack_spvm'):
        location=Path(importlib.util.find_spec(package).submodule_search_locations[0])
        prefix='nova-spvm/'+package+'/'
        for name,wanted in new_manifest['source_sha256'].items():
            if name.startswith(prefix) and name.endswith('.py'):
                target=location/name.removeprefix(prefix)
                if target.is_symlink() or file_hash(target)!=wanted:raise ValueError('installed driver SDK code changed')
    previous=data/'platform-install';journal=document(previous/'platform-transaction.json')
    if (journal.get('status')!='NEEDS_REVIEW' or journal.get('completed')!=['operator-auth']
            or journal.get('active_phase')!='shared-network' or journal.get('guests_launched') is not False
            or (previous/'continued-by.json').exists()):
        raise ValueError('unreviewed platform boundary or existing continuation')
    network_journal=document(previous/'network/network-transaction.json')
    network_plan=document(previous/'network/network-plan.json')
    if (network_journal.get('status')!='NEEDS_REVIEW'
            or network_journal.get('plan_sha256')!=network_plan.get('plan_sha256')
            or network_plan['plan_sha256']!=digest({k:v for k,v in network_plan.items() if k!='plan_sha256'})):
        raise ValueError('failed network journal differs')
    request=document(previous/'network-request.json')
    if 'is_ha' in request['router'] or network_plan['identity']['request_sha256']!=digest(request):
        raise ValueError('only omitted router HA intent is supported')
    clouds=data/'core/kolla-run/clouds.yaml'
    cloud=yaml.safe_load(private(clouds).read_bytes())['clouds']['kolla-admin-internal']
    binding=document(data/'compute-binding.json');region=binding['bootstrap_runtime']['region_name']
    ca='/etc/ssl/certs/ca-certificates.crt'
    session=Session(auth=Password(**cloud['auth']),verify=ca,timeout=30)
    connection=Connection(session=session,region_name=region,interface='internal',api_timeout=30)
    try:
        access=session.auth.get_access(session)
        if 'admin' not in access.role_names or list(connection.compute.servers(all_projects=True)):
            raise ValueError('empty administrator-scoped cloud required')
        services=list(connection.compute.services(binary='nova-compute'))
        if len(services)!=len(binding['hosts']) or any(s.state!='up' for s in services):
            raise ValueError('prepared compute service inventory is not healthy')
        platform.verify_operator_auth('/etc/spvm/auth',cloud['auth'],0,region,ca)
        spec=platform.specification(data/'platform.json')
        if Path(spec['runtime_dir']).exists() or Path(spec['state_dir']).exists():
            raise ValueError('platform runtime may already exist; no adoption')
        path=source_root/'driver/deployment/spvm_network.py'
        loader=importlib.util.spec_from_file_location('reviewed_spvm_network',path)
        network=importlib.util.module_from_spec(loader);loader.loader.exec_module(network)
        state=network.inspect(connection,request)
        if (state['router']['action']!='CREATE' or state['router']['interface_action']!='ATTACH'
                or any(v['network_action']!='REUSE' or v['subnet_action']!='REUSE' for v in state['networks'].values())):
            raise ValueError('only the inspected existing network/subnet pair and absent router are supported')
        # Verify existing delegated secrets and their live metadata, without
        # invoking provision/rotation or a negative-write canary during --check.
        path=source_root/'driver/deployment/service_credentials.py'
        loader=importlib.util.spec_from_file_location('reviewed_service_credentials',path)
        creds=importlib.util.module_from_spec(loader);loader.loader.exec_module(creds)
        paths=[data/name for name in ('prepared.json','deployment.yaml','compute-binding.json','platform.json','network.json','base-haproxy.cfg','console-context.json','sdk-packages.txt')]
        paths += [clouds,previous/'platform-transaction.json',previous/'network-request.json',
                  previous/'network/network-plan.json',previous/'network/network-transaction.json',Path('/etc/spvm/auth/admin-openrc.sh'),Path(ca)]
        gid=pwd.getpwnam(platform.INGRESS_USER).pw_gid
        for purpose,folder in [('ingress','ingress-auth'),('retirement','retirement-auth')]:
            rc=Path('/etc/spvm')/folder/'openrc.sh';info=rc.lstat()
            directory=rc.parent.lstat()
            if rc.parent.is_symlink() or directory.st_uid!=0 or directory.st_gid!=gid or directory.st_mode&0o777!=0o750:
                raise ValueError('protected existing credential directory required')
            if rc.is_symlink() or not rc.is_file() or info.st_uid!=0 or info.st_gid!=gid or info.st_mode&0o777!=0o640 or info.st_nlink!=1 or info.st_size>32768:
                raise ValueError('protected existing service credential required')
            body=rc.read_text()
            if not body.startswith(creds.CLEAN_ENV):raise ValueError('unknown service credential format')
            tokens=shlex.split(body[len(creds.CLEAN_ENV):])
            if len(tokens)%2 or any(t!='export' for t in tokens[::2]):raise ValueError('invalid credential format')
            values=dict(t.split('=',1) for t in tokens[1::2])
            credential=R(id=values['OS_APPLICATION_CREDENTIAL_ID'],secret=values['OS_APPLICATION_CREDENTIAL_SECRET'])
            if body!=creds.openrc(cloud['auth']['auth_url'],credential,region,ca):raise ValueError('credential scope differs')
            metadata=connection.identity.get_application_credential(access.user_id,credential.id)
            canonical=lambda rules:sorted(json.dumps(v,sort_keys=True) for v in rules)
            observed=[{k:v[k] for k in ('service','method','path')} for v in metadata.access_rules]
            if metadata.unrestricted is not False or canonical(observed)!=canonical(creds.rules(purpose)):
                raise ValueError('existing API delegation differs')
            delegated=Session(auth=ApplicationCredential(auth_url=cloud['auth']['auth_url'],application_credential_id=credential.id,
                application_credential_secret=credential.secret),verify=ca,timeout=20)
            try:
                identity=delegated.auth.get_access(delegated)
                if (identity.project_id!=access.project_id or identity.user_id!=access.user_id
                        or 'member' not in identity.role_names or set(identity.role_names)-{'member','reader'}):
                    raise ValueError('service credential roles/scope differ')
            finally:delegated.close()
            paths.append(rc)
        return {'status':'REVIEWED_PLATFORM_BOUNDARY_READY','machine':prepared['machine'],
            'project':access.project_id,'user':access.user_id,'network':state,'driver_commit':new_manifest['source_commit'],
            'files':{str(p):file_hash(p) for p in paths},'guests':0}
    finally:connection.close();session.close()


def remote_probe(i):
    # Execute reviewed current probe code in memory; no remote source changes
    # or arbitrary uploaded command are requested by a web/API client.
    platform_path=Path(__file__).with_name('openstack_platform.py')
    code=('import sys,types,json; p=types.ModuleType("openstack_platform");'
        'p.__file__='+repr(i.platform_source_root+'/tools/openstack_platform.py')+';'
        'sys.modules["openstack_platform"]=p;'
        'exec(compile('+repr(platform_path.read_text())+',"platform-review","exec"),p.__dict__);'
        'm=types.ModuleType("platform_probe");'
        'exec(compile('+repr(Path(__file__).read_text())+',"platform-probe","exec"),m.__dict__);'
        'print(json.dumps(m.probe('+repr(i.controller_root)+','+repr(i.platform_source_root)+')))')
    bootstrap='import sys;sys.path.insert(0,'+repr(i.platform_source_root+'/tools')+');'+code
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n '+shlex.quote(i.controller_root+'/sdk/bin/python')+' -I -B -c '+shlex.quote(bootstrap)]
    with (i.output/'private-platform-review.log').open('xb') as log:
        os.fchmod(log.fileno(),0o600)
        result=subprocess.run(argv,stdout=subprocess.PIPE,stderr=log,timeout=300)
    if result.returncode or len(result.stdout)>4*1024*1024:
        raise InstallerError('Controller platform review failed; inspect '+str(i.output/'private-platform-review.log')+'. No deployment was run.')
    return json.loads(result.stdout)


def prepare(i,tx):
    from openstack_install import PHASES
    from openstack_install_artifacts import console_builder
    from openstack_install_phases import BASES
    import openstack_kolla_workspace as workspace
    import openstack_kolla_workflow as kolla
    if not i.args.use_testing_images:raise InstallerError('Explicit --use-test-images acknowledgement remains required.')
    origin=Path(i.args.continue_platform_from)
    if i.output==origin or origin.is_relative_to(i.output):raise InstallerError('Continuation evidence must be separate from the original transaction.')
    parent,base,core=reviewed(origin)
    raw=__import__('yaml').safe_load(private(base/'deployment.input.yaml').read_bytes())
    original_manifest=json.loads((Path(raw['driver']['directory'])/'manifest.json').read_bytes())
    current_manifest=json.loads((i.path.parent.parent/'manifest.json').read_bytes())
    validate_bundle_delta(original_manifest,current_manifest)
    original_raw=json.loads(json.dumps(raw));current_raw=json.loads(json.dumps(i.raw))
    original_raw['driver'].pop('directory');current_raw['driver'].pop('directory')
    if original_raw!=current_raw:raise InstallerError('Only driver.directory may change for this reviewed platform fix.')
    tools=Path(__file__).parent.resolve()
    for filename,wanted in parent['inputs'].items():
        path=Path(filename)
        if path==Path(i.args.config) or path.parent==tools and path.suffix=='.py':continue
        if file_hash(path)!=wanted:raise InstallerError('Previous approved input changed: '+str(path))
        i.inputs[filename]=wanted;tx.inputs[filename]=wanted
    i.verify_environment()
    console_builder(i,['--check-base-image',i.options.get('console_base_image',BASES['console'])])
    i.config=str(private(base/'deployment.artifacts.yaml'));i.refresh()
    i.workspace=str(base/'kolla-bound');workspace.check(i.workspace)
    i.requests=document(base/'requests.json')['hosts']
    binding=document(Path(i.workspace)/'compute-binding.json')
    previous_plan=document(base/'plan.json')
    previous_bases=document(base/'artifacts-result.json')['bases']
    if (str(i.guest)!=previous_plan['guest_cidr'] or i.region!=binding['bootstrap_runtime']['region_name']
            or any(document(filename)['allow_debug']!=i.options.get('allow_debug',False) for filename in i.requests.values())
            or any(i.options.get(key+'_base_image',BASES[key])!=previous_bases[key] for key in ('compute','console'))):
        raise InstallerError('Prepared guest/debug/region/base-image policy changed; continuation refused.')
    i.controller=next(n for n,h in i.cfg['hosts'].items() if 'control' in h['roles'])
    i.run_id=i.cfg['deployment_id']+'-'+digest(str(base))[:12]
    i.controller_root='/opt/spvm-openstack/'+i.run_id
    i.platform_source_root='/opt/spvm-openstack/'+i.cfg['deployment_id']+'-update-'+current_manifest['source_commit'][:8]
    i.platform_target='/opt/spvm-openstack/'+i.cfg['deployment_id']+'-platform-'+digest(str(i.output))[:12]
    machines=document(base/'plan.json')['hosts']
    if set(machines)!=set(i.cfg['hosts']):raise InstallerError('Prepared host inventory differs.')
    for name,audit in i.audits.items():
        if audit['host_identity_sha256']!=machines[name]['machine_sha256']:raise InstallerError('Host identity differs: '+name)
    args=i.phase_args(workspace=i.workspace)
    cfg,receipt,source,code,intent=kolla.fingerprint(args,i.path,i.backend)
    with kolla.hold_libvirt(args,i.backend,i.cfg):
        observed=kolla.guard(args,i.backend,i.cfg,intent,document(origin/'core/install-plan.json')['hosts'])
    if not protected_equal(core['protected_hosts'],observed):raise InstallerError('Protected host state changed after Kolla; manual review required.')
    i.platform_review=remote_probe(i)
    if i.platform_review.get('status')!='REVIEWED_PLATFORM_BOUNDARY_READY':
        raise InstallerError('Controller review did not confirm the supported boundary.')
    for root in (origin,base):
        for path in root.iterdir():
            if path.is_file() and path.suffix in ('.json','.yaml'):
                private(path);i.inputs[str(path)]=file_hash(path);tx.inputs[str(path)]=i.inputs[str(path)]
    for name in ('install-transaction.json','install-plan.json','kolla-run/clouds.yaml'):
        path=private(origin/'core'/name);i.inputs[str(path)]=file_hash(path);tx.inputs[str(path)]=i.inputs[str(path)]
    i.inputs[str(origin/'private-controller-completion.log')]=file_hash(origin/'private-controller-completion.log')
    tx.inputs.update(i.inputs)
    tx.record.update(completed=list(PHASES[:-1]),continued_from=str(origin),use_testing_images=True)
    return {'schema_version':1,'deployment_id':i.cfg['deployment_id'],'hosts':machines,'storage':document(base/'plan.json').get('storage',{}),
        'blockers':[],'continued_from':str(origin),'remaining_phases':['platform'],'use_testing_images':True,
        'previous_journal_sha256':file_hash(origin/'installation.json'),'controller_review':i.platform_review,
        'management_mode':i.cfg['network'].get('management_mode')}


def claim(i,tx,plan):
    # Same durable one-shot reservation, but validate the platform boundary.
    origin=Path(plan['continued_from']);reviewed(origin);tx.unchanged()
    with (origin/'continued-by.json').open('x') as stream:
        os.fchmod(stream.fileno(),0o600);json.dump({'output':str(i.output),'plan_sha256':digest(plan)},stream)
        stream.flush();os.fsync(stream.fileno())
    fd=os.open(origin,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def setup_controller(root,payload):
    c=payload['platform_continuation'];old=Path(c['old_root'])
    if old.parent!=Path('/opt/spvm-openstack') or old.resolve()!=old:raise ValueError('invalid prepared controller root')
    code='import sys,json;sys.path.insert(0,'+repr(str(root/'tools'))+');from openstack_install_platform_continue import finalize_controller;print(json.dumps(finalize_controller('+repr(str(root))+',json.load(sys.stdin))))'
    result=subprocess.run([str(old/'sdk/bin/python'),'-I','-B','-c',code],input=json.dumps(c),capture_output=True,text=True,timeout=300)
    if result.returncode:
        log=root/'private-continuation-prepare.log'
        with log.open('x') as stream:
            os.fchmod(stream.fileno(),0o600);stream.write(result.stderr)
        raise ValueError('controller continuation preparation failed; inspect '+str(log))
    return json.loads(result.stdout)


def finalize_controller(root,continuation):
    root=Path(root);old=Path(continuation['old_root'])
    if probe(old,root)!=continuation['review']:
        raise ValueError('controller state changed after explicit approval')
    data=root/'data';data.mkdir(mode=0o700)
    write(data/'continuation.json',continuation)
    return {'status':'CONTROLLER_PLATFORM_CONTINUATION_PREPARED'}


def complete(i):
    from openstack_install_completion import BOOTSTRAP,snapshot,rpc_process,Operations
    payload={'root':i.platform_target,'machine':i.platform_review['machine'],'files':snapshot(i),
        'platform_continuation':{'old_root':i.controller_root,'review':i.platform_review}}
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n /usr/bin/python3 -I -B -u -c '+shlex.quote(BOOTSTRAP)]
    result=rpc_process(i,argv,payload)
    if result.get('status')!='CONTROLLER_PLATFORM_CONTINUATION_PREPARED':raise InstallerError('Controller preparation incomplete.')
    code='import sys;sys.path.insert(0,'+repr(i.platform_target+'/tools')+');from openstack_install_platform_continue import serve;serve('+repr(i.platform_target)+')'
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n '+shlex.quote(i.controller_root+'/sdk/bin/python')+' -I -B -u -c '+shlex.quote(code)]
    operations=Operations(i)
    result=rpc_process(i,argv,{},operations)
    if operations.activated!=set(i.requests) or operations.installed!=set(i.requests) or not operations.audited:
        raise InstallerError('Controller did not complete all reviewed compute operations.')
    return result


def serve(directory):
    from openstack_install_phases import load_module
    import openstack_platform as platform
    root=Path(directory);data=root/'data';c=document(data/'continuation.json');old=Path(c['old_root'])
    if json.loads(sys.stdin.buffer.readline(1024))!={}:raise ValueError('empty continuation acknowledgement required')
    if probe(old,root)!=c['review']:raise ValueError('controller state drift before platform continuation')
    marker=old/'data/platform-install/continued-by.json'
    with marker.open('x') as stream:
        os.fchmod(stream.fileno(),0o600);json.dump({'controller':str(root),'review_sha256':digest(c['review'])},stream)
        stream.flush();os.fsync(stream.fileno())
    fd=os.open(marker.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    path=root/'driver/deployment/installer.py'
    sys.path[:0]=[str(path.parent),str(root/'driver/nova-spvm')]
    backend=load_module(path,'installer');sys.modules['installer']=backend
    original=backend.deployment_config;backend.deployment_config=lambda filename,**kw:original(filename,transport=False)
    def forbidden(*a,**kw):raise ValueError('no operator SSH or keys on controller')
    backend.ssh_arguments=forbidden;backend.host_audits=forbidden
    sequence=0
    def operation(name,value):
        nonlocal sequence
        print(json.dumps({'operation':name,'data':value,'sequence':sequence}),flush=True)
        line=sys.stdin.buffer.readline(64*1024*1024+1)
        if len(line)>64*1024*1024:raise ValueError('oversized acknowledgement')
        reply=json.loads(line)
        if set(reply)!={'sequence','result'} or reply['sequence']!=sequence:raise ValueError('invalid acknowledgement')
        sequence+=1;return reply['result']
    value={'config':str(old/'data/deployment.yaml'),'workspace':str(old/'data'),'platform':str(old/'data/platform.json')}
    result=platform.post_install(value,path,backend,data/'platform-install',old/'data/core',operations=operation,verify_existing_auth=True)
    print(json.dumps({'result':result}),flush=True)
