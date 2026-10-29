"""Owned preparation of one absent compute before the existing deploy workflow.

Never bootstrap hardware, prepare controllers, generate credentials or replay a
partially completed operation. Every remote action is selected by this module.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import tempfile

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_container_engine as engine
import openstack_host_helper as helper
import openstack_image_transfer as transfer
import openstack_launcher as launcher

FIELDS={'schema_version','network_request','geneve_request','launcher_stage',
        'launcher_sha256','image_package','image_package_sha256','image_reserve_gib'}


@contextmanager
def hold_target(value,module):
    from openstack_kolla_workflow import hold_libvirt
    cfg=module.deployment_config(value['config'])
    name=value['host']
    if name not in cfg['hosts'] or cfg['hosts'][name]['roles']!=['compute']:
        raise ValueError('only an explicit compute-only target can be guarded')
    # Never apply the fresh-cloud all-compute guard to occupied existing peers.
    with hold_libvirt(argparse.Namespace(known_hosts=value['known_hosts']),module,
                      {'hosts':{name:cfg['hosts'][name]}}):
        yield


def specification(filename):
    value=json.loads(read(filename))
    if (type(value) is not dict or set(value)!=FIELDS or type(value['schema_version']) is not int
            or value['schema_version']!=1):raise ValueError('exact compute preparation recipe required')
    for key in ('network_request','geneve_request','launcher_stage','image_package'):
        p=Path(value[key])
        if not p.is_absolute() or p.resolve()!=p:raise ValueError('canonical preparation inputs required')
    for key in ('launcher_sha256','image_package_sha256'):
        if not isinstance(value[key],str) or not re.fullmatch('[a-f0-9]{64}',value[key]):
            raise ValueError('explicit preparation artifact approval required')
    if type(value['image_reserve_gib']) is not int or value['image_reserve_gib']<1:
        raise ValueError('positive image staging reserve required')
    return value


def inputs(value,path,module):
    import openstack_compute_prepare as deploy
    from openstack_geneve_guard import inputs as geneve_inputs
    from openstack_network_prepare import inputs as network_inputs
    cfg,receipt,wanted,outer,alias=deploy.inputs(value,module)
    spec=specification(outer['preparation'])
    target=cfg['hosts'][value['host']]
    if (not re.fullmatch(r'[a-z_][a-z0-9_-]*@[a-z0-9][a-z0-9.-]*',target['user']+'@'+target['address'])
            or not target.get('identity_file') or not read(target['identity_file'])):
        raise ValueError('explicit supported SSH image-delivery identity required')
    # Reuse the already reviewed enrollment identity, pools and GPU donation.
    # No separate configuration may silently replace those values.
    request=wanted['helper_request']
    with tempfile.TemporaryDirectory(prefix='spvm-helper-validation-') as directory:
        filename=Path(directory)/'request.json';write(filename,request)
        helper.request(filename)
    helper.source_manifest(path,request,bootstrap=True)
    import openstack_compute_dependencies as dependencies
    dependencies.role(receipt)
    dependencies.ca(outer['configdir'],cfg['deployment_id'])
    assembled,files=launcher.verify(path,spec['launcher_stage'],spec['launcher_sha256'])
    package=transfer.package(spec['image_package'],spec['image_package_sha256'])
    if package['image_id']!=wanted['image_id'] or package['tag']!=cfg['kolla']['compute_image']:
        raise ValueError('preparation image differs from the existing cloud driver')
    runtime=helper.bootstrap_policy(wanted['bootstrap_runtime']['auth_url'],wanted['bootstrap_runtime']['region_name'])
    if runtime!=wanted['bootstrap_runtime']:raise ValueError('deny-all bootstrap runtime required')
    args=argparse.Namespace(config=value['config'],host=value['host'],
        allow_experimental_ubuntu26=outer['allow_experimental_ubuntu26'])
    args.action='plan-management';args.network_request=spec['network_request']
    _,management,binding=network_inputs(args,module)
    args.action='plan-geneve-guard';args.network_request=spec['geneve_request']
    _,geneve,geneve_binding=geneve_inputs(args,module)
    if binding['machine_sha256']!=request['machine_sha256'] or geneve_binding['machine_sha256']!=request['machine_sha256']:
        raise ValueError('network and enrollment target identities differ')
    snapshots=helper.sources(path)
    snapshots['/etc/spvm/runtime-services.json']=(json.dumps(runtime,indent=2).encode(),0o644)
    snapshots.update({str(Path(request['tools'])/'scripts'/name):(body,0o755) for name,body in files.items()})
    return cfg,receipt,wanted,outer,spec,assembled,snapshots


def peer_network(value,module,runner,cfg,spec,outer,*,approved=None,output=None):
    """Inspect or add exactly the new compute to existing owned allowlists."""
    import openstack_geneve_guard as geneve
    import openstack_network_prepare as network
    results={}
    network_request=json.loads(read(spec['network_request']))
    geneve_request=json.loads(read(spec['geneve_request']))
    import ipaddress
    management_ip=str(ipaddress.IPv4Interface(network_request['hosts'][value['host']]['management_address']).ip)
    for name,host in cfg['hosts'].items():
        if name==value['host'] or not set(host['roles']) & {'compute','network'}:continue
        args=argparse.Namespace(config=value['config'],host=name,
            network_request=spec['geneve_request'],action='plan-geneve-guard',
            allow_experimental_ubuntu26=outer['allow_experimental_ubuntu26'])
        _,policy,binding=geneve.inputs(args,module)
        entries=[{'policy':policy,'path':'/etc/spvm-network/spvm-geneve-'+cfg['deployment_id']+'.json',
                  'kind':'geneve','runtime':geneve.RUNTIME,
                  'new_address':geneve_request['hosts'][value['host']]['tunnel_ip'],
                  'runtime_sha256':file_hash(Path(__file__).with_name('openstack_geneve_runtime.py'))}]
        if name==network_request['nat']['host']:
            args.network_request=spec['network_request'];args.action='plan-nat'
            _,nat,_=network.inputs(args,module)
            entries.append({'policy':nat,'path':'/etc/spvm-host-network/spvm-nat-'+cfg['deployment_id']+'.json',
                'kind':'nat','runtime':network.RUNTIME,'new_address':management_ip,
                'runtime_sha256':file_hash(Path(__file__).with_name('openstack_network_runtime.py'))})
        data={'entries':entries,'machine':binding['machine_sha256'],'apply':approved is not None,
            'before':None if approved is None else approved[name],
            'output':None if output is None else '/var/lib/spvm-installer/'+cfg['deployment_id']+'/peer-'+digest(str(output))[:24]}
        results[name]=remote_tools(runner,name,('openstack_peer_network.py','openstack_container_engine.py'),data,'''
from openstack_peer_network import Peer
peer=Peer()
result=(peer.apply(DATA['entries'],DATA['machine'],DATA['before'],DATA['output']) if DATA['apply']
        else peer.observe(DATA['entries'],DATA['machine']))
''')
    return results


def remote_tools(runner,name,modules,data,operation):
    sources={n:read(Path(__file__).with_name(n)).decode() for n in modules}
    source='SOURCES='+repr(sources)+'\nDATA='+repr(data)+'\n'+'''
import tempfile,sys,json,os
from pathlib import Path
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='spvm-onboard-') as directory:
 root=Path(directory)
 for name,text in SOURCES.items():(root/name).write_text(text)
 sys.path.insert(0,str(root))
 exec(OPERATION)
 print(json.dumps(result))
'''.replace('exec(OPERATION)','exec('+repr(operation)+')')
    return runner.remote(name,source)


def hostnames(value,module,runner,cfg,spec,*,approved=None,output=None):
    import ipaddress
    from openstack_compute_identity import native_uuid
    network=json.loads(read(spec['network_request']));identities={};mappings={}
    for name in cfg['hosts']:
        source="import json,socket,hashlib;from pathlib import Path;print(json.dumps({'hostname':socket.gethostname(),'machine':hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()}))"
        identity=runner.remote(name,source);nodename=identity['hostname'];native_uuid(nodename)
        if identity['machine']!=network['hosts'][name]['machine_sha256'] or nodename in mappings:
            raise ValueError('hostname/machine mapping differs or is duplicated')
        mappings[nodename]=str(ipaddress.IPv4Interface(network['hosts'][name]['management_address']).ip)
        identities[name]=identity
    results={}
    modules=('openstack_hostnames.py','openstack_compute_identity.py','openstack_host_helper.py',
             'openstack_compute_image.py','openstack_container_engine.py')
    for name in cfg['hosts']:
        wanted={'schema_version':1,'machine_sha256':identities[name]['machine'],'mappings':mappings}
        data={'request':wanted,'apply':approved is not None,'before':None if approved is None else approved[name],
            'output':None if output is None else '/var/lib/spvm-installer/'+cfg['deployment_id']+'/hostnames-'+digest(str(output))[:24]}
        results[name]=remote_tools(runner,name,modules,data,'''
from openstack_hostnames import Host,specification,execute
from openstack_container_engine import write
from types import SimpleNamespace as R
wanted=specification(DATA['request']);host=Host();observed=host.observe(wanted)[0]
if not DATA['apply']:result={'identity':wanted,'observed':observed}
else:
 if {'identity':wanted,'observed':observed}!=DATA['before']:raise ValueError('hostname facts drift')
 target=Path(DATA['output'])
 for p in (target.parent,*target.parent.parents):
  if p.exists() and (p.is_symlink() or p.stat().st_uid!=0 or p.stat().st_mode&0o022):raise ValueError('untrusted hostname transaction')
 target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 request=root/'request.json';write(request,wanted)
 args=R(request=str(request),output=str(target),action='plan-hostnames',plan_sha256=None)
 plan=execute(args);args.action='prepare-hostnames';args.plan_sha256=plan['plan_sha256'];result=execute(args)
''')
    return results


def input_hashes(filename,path):
    """Bind transitive artifacts, including a streamed hash of the image archive."""
    spec=specification(filename)
    result={str(filename):file_hash(filename)}
    for key in ('network_request','geneve_request'):
        result[spec[key]]=file_hash(spec[key])
    launcher.verify(path,spec['launcher_stage'],spec['launcher_sha256'])
    for p in sorted(Path(spec['launcher_stage']).rglob('*')):
        if p.is_file():result[str(p)]=file_hash(p)
    package=transfer.package(spec['image_package'],spec['image_package_sha256'])
    result[str(Path(spec['image_package'])/'image-package.json')]=file_hash(Path(spec['image_package'])/'image-package.json')
    result[str(Path(spec['image_package'])/'image.tar')]=package['archive_sha256']
    result['helper-source-snapshot-sha256']=digest({name:{'mode':mode,
        'sha256':hashlib.sha256(body).hexdigest()} for name,(body,mode) in helper.sources(path).items()})
    manifest=Path(path).parent.parent/'manifest.json'
    if not manifest.is_file():manifest=Path(path).parent.parent/'nova-spvm/manifest.json'
    result[str(manifest)]=file_hash(manifest)
    return result


def engine_args(value,receipt,outer,output,action):
    return argparse.Namespace(config=value['config'],host=value['host'],
        known_hosts=value['known_hosts'],kolla_source=receipt['kolla_source'],
        collections=receipt['collections'],collections_sha256=receipt['collections_sha256'],
        ansible_playbook=str(Path(receipt['kolla_python']).parent/'ansible-playbook'),
        allow_experimental_ubuntu26=outer['allow_experimental_ubuntu26'],
        output=str(output),action=action,plan_sha256=None)


def helper_phase(runner,host,request,assembled,files,output,*,apply=False,before=None):
    # Ship only selected installer modules and approved helper/launcher bytes.
    # No provider configuration, SSH key, cloud password or API token is shipped.
    sources={name:read(Path(__file__).with_name(name)).decode() for name in
        ('openstack_host_helper.py','openstack_container_engine.py')}
    # A genuinely fresh host does not yet have discovery or driver modules.
    # Inspect using the same approved bytes that will subsequently be installed,
    # not imports accidentally supplied by a previous helper installation.
    sources['gpu_discovery.py']=files['/usr/local/libexec/spvm/gpu_discovery.py'][0].decode()
    sources['nova_spvm/__init__.py']=files['/usr/local/libexec/nova-spvm/nova_spvm/__init__.py'][0].decode()
    sources['nova_spvm/gpu_profiles.py']=files['/usr/local/libexec/nova-spvm/nova_spvm/gpu_profiles.py'][0].decode()
    data={'request':request,'launcher':assembled['result_sha256'],'files':files,
          'output':output,'apply':apply,'before':before}
    source='SOURCES='+repr(sources)+'\nDATA='+repr(data)+'\n'+'''
import sys,json,tempfile,os,fcntl,traceback
from pathlib import Path
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='spvm-compute-helper-') as directory:
 root=Path(directory)
 for name,text in SOURCES.items():
  path=root/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);path.write_text(text)
 sys.path.insert(0,str(root))
 import openstack_host_helper as phase
 from openstack_container_engine import write
 host=phase.Host();observed=host.inspect(DATA['request'],launcher=DATA['launcher'])
 if not DATA['apply']:print(json.dumps(observed))
 else:
  if observed!=DATA['before']:raise ValueError('helper preparation facts changed')
  target=host.trusted(DATA['output']);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  target.mkdir(mode=0o700,exist_ok=False)
  write(target/'transaction.json',{'status':'INSTALLING','request':DATA['request']})
  try:
   result=host.install(DATA['request'],observed,DATA['files'])
  except BaseException as error:
   write(target/'transaction.json',{'status':'NEEDS_REVIEW','error_type':type(error).__name__,
    'frames':[(frame.name,frame.lineno) for frame in traceback.extract_tb(error.__traceback__)[-5:]]});raise
  write(target/'transaction.json',result);print(json.dumps(result))
'''
    return runner.remote(host,source)


def observe(value,path,module,output,runner):
    """Read-only preparation plan; absent Docker is not a bootstrap failure."""
    import openstack_compute_prepare as deploy
    cfg,receipt,wanted,outer,spec,assembled,files=inputs(value,path,module)
    mtu = deploy.verify_network_mtu(runner, cfg)
    engine.inputs(engine_args(value,receipt,outer,output/'container-engine','plan-container-engine'),module)
    before=engine.observe(module,cfg['hosts'][value['host']],value['known_hosts'])
    mode=engine.policy(before,outer['allow_experimental_ubuntu26'],compute=True)
    helper_before=helper_phase(runner,value['host'],wanted['helper_request'],assembled,files,
        '/var/lib/spvm-installer/'+cfg['deployment_id']+'/helper-'+digest(str(output))[:24])
    # Container-engine receipts hash the raw file (including its newline);
    # helper/enrollment identities hash stripped bytes. Keep both established
    # ABIs and verify the canonical identity through the helper's own probe.
    if helper_before.get('machine')!=wanted['helper_request']['machine_sha256']:
        raise ValueError('wrong canonical target machine')
    # Native identity is kernel-nodename derived, never an inventory alias.
    from openstack_compute_identity import native_uuid,verify_rule
    nodename=runner.remote(value['host'],"import json,socket;print(json.dumps({'hostname':socket.gethostname()}))")['hostname']
    if native_uuid(nodename)!=wanted['helper_request']['compute_uuid']:raise ValueError('native compute identity mismatch')
    verify_rule(receipt,{value['host']:{'nodename':nodename}})
    current=deploy.cloud(runner)
    if any(s['host']==nodename for s in current['services']):raise ValueError('new compute already registered')
    if any(s['host']==nodename or s['status']=='BUILD' or s['task'] for s in current['servers']):
        raise ValueError('assigned or in-flight cloud workload blocks preparation')
    peers=peer_network(value,module,runner,cfg,spec,outer)
    names=hostnames(value,module,runner,cfg,spec)
    import openstack_compute_dependencies as dependencies
    public_ca=dependencies.ca(outer['configdir'],cfg['deployment_id'])
    dependency_state=dependencies.observe(runner,value['host'],public_ca)
    return {'kind':'compute-host-preparation-v1','network_mtu':mtu,'engine':before,'engine_mode':mode,'peers':peers,
            'hostnames':names,'dependencies':dependency_state,
            'helper':helper_before,'hostname':nodename,'controller':deploy.controller(runner),
            'cloud':current,'inputs':input_hashes(outer['preparation'],path),
            'stages':['peer-network','hostnames','dependencies','container-engine','network',
                      'helper','driver-image','compute-deploy'],
            'automatic_recovery':False}


def execute(value,path,module,output,runner,approved):
    import openstack_compute_prepare as deploy
    import openstack_platform_network as network
    import openstack_compute_dependencies as dependencies
    cfg,receipt,wanted,outer,spec,assembled,files=inputs(value,path,module)
    if observe(value,path,module,output,runner)!=approved:raise ValueError('preparation facts drift')
    journal=output/'host-preparation-transaction.json'
    if journal.exists():raise ValueError('preparation attempted; inspect journal, no automatic replay')
    record={'status':'PREPARING','phase':'PEER_NETWORK','completed':[]}
    write(journal,record)
    try:
        peer_network(value,module,runner,cfg,spec,outer,approved=approved['peers'],output=output)
        record['completed'].append('peer-network');record['phase']='HOSTNAMES';write(journal,record)
        hostnames(value,module,runner,cfg,spec,approved=approved['hostnames'],output=output)
        record['completed'].append('hostnames');record['phase']='DEPENDENCIES';write(journal,record)
        public_ca=dependencies.ca(outer['configdir'],cfg['deployment_id'])
        protected=engine.observe(module,cfg['hosts'][value['host']],value['known_hosts'])
        dependencies.prepare(value,module,receipt,public_ca,output/'dependencies',runner)
        if engine.observe(module,cfg['hosts'][value['host']],value['known_hosts'])!=protected:
            raise ValueError('dependency preparation changed protected runtime state')
        record['completed'].append('dependencies');record['phase']='CONTAINER_ENGINE';write(journal,record)
        args=engine_args(value,receipt,outer,output/'container-engine','plan-container-engine')
        plan=engine.execute(args,module)
        args.action='prepare-container-engine';args.plan_sha256=plan['plan_sha256']
        engine.execute(args,module)
        record['completed'].append('container-engine');record['phase']='NETWORK';write(journal,record)
        # Docker must exist before the existing fresh-network probe uses it.
        # Only the target is provisioned; existing peers received additive rules.
        prep=output/'network-recipe.json'
        write(prep,{'network_request':spec['network_request'],'geneve_request':spec['geneve_request']})
        network.prepare({**value,'preparation':str(prep),
            'allow_experimental_ubuntu26':outer['allow_experimental_ubuntu26']},path,module,output,
            targets=[value['host']])
        record['completed'].append('network');record['phase']='HELPER';write(journal,record)
        helper_before=helper_phase(runner,value['host'],wanted['helper_request'],assembled,files,
            '/var/lib/spvm-installer/'+cfg['deployment_id']+'/helper-'+digest(str(output))[:24])
        if helper_before!=approved['helper']:raise ValueError('protected helper facts drift after prerequisites')
        helper_phase(runner,value['host'],wanted['helper_request'],assembled,files,
            '/var/lib/spvm-installer/'+cfg['deployment_id']+'/helper-'+digest(str(output))[:24],apply=True,before=helper_before)
        record['completed'].append('helper');record['phase']='DRIVER_IMAGE';write(journal,record)
        host=cfg['hosts'][value['host']]
        args=argparse.Namespace(package=spec['image_package'],package_sha256=spec['image_package_sha256'],
            host=host['user']+'@'+host['address'],identity_file=host['identity_file'],
            known_hosts=value['known_hosts'],ssh_port=host.get('port',22),
            machine_sha256=wanted['helper_request']['machine_sha256'],
            reserve_gib=spec['image_reserve_gib'],output=str(output/'image-delivery'))
        result=transfer.deliver(args)
        if result['status']!='COMPUTE_IMAGE_DELIVERED_NOT_DEPLOYED':raise ValueError('image delivery incomplete')
        if deploy.controller(runner)!=approved['controller'] or deploy.cloud(runner)!=approved['cloud']:
            raise ValueError('existing controller or cloud workloads changed')
        record.update(status='HOST_PREPARED_NOT_DEPLOYED',phase='COMPUTE_DEPLOY')
        record['completed'].append('driver-image');write(journal,record)
        return record
    except BaseException:
        record['status']='NEEDS_REVIEW';write(journal,record);raise
