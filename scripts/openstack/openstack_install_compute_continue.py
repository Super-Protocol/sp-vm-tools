"""Reviewed first-compute enrollment continuation; never a fresh deploy/recovery daemon.

The previous runtime must still be bootstrap, the target stopped/quarantined and
all projects empty. New evidence owns the remaining operations; no old WAL is reset.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace as R

from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError
from openstack_install_continue import document, private
import openstack_install_platform_continue as platform_resume


def activation_digest(value):
    # Driver accounting/activation plans use compact JSON, unlike parent WALs.
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def validate_delta(old, new):
    changed = {n for n in old['source_sha256'] if old['source_sha256'][n] != new['source_sha256'].get(n)}
    required = {'deployment/initial_compute.py', 'deployment/compute_workflow.py'}
    if (old['wheel_sha256'] != new['wheel_sha256'] or old['version'] != new['version']
            or old.get('capabilities') != new.get('capabilities')
            or set(old['source_sha256']) != set(new['source_sha256'])
            or not required <= changed or not changed <= required | {'deployment/README.md'}
            or old.get('source_tree_dirty') is not False or new.get('source_tree_dirty') is not False):
        raise InstallerError('Compute continuation requires the reviewed host-dependency fix and unchanged wheel/other sources.')


def reviewed(origin):
    from openstack_install import PHASES, protected_parent
    origin = Path(origin); protected_parent(origin)
    parent = document(origin/'installation.json')
    if (parent.get('schema_version')!=1 or origin.stat().st_mode & 0o077 or (origin/'continued-by.json').exists()
            or parent.get('status') != 'NEEDS_REVIEW' or parent.get('active_phase') != 'platform'
            or parent.get('completed') != list(PHASES[:-1]) or parent.get('guests_created') is not False):
        raise InstallerError('Unclaimed failed platform-compute transaction required.')
    platform_origin = Path(parent.get('continued_from', '')); protected_parent(platform_origin)
    previous = document(platform_origin/'installation.json')
    base = Path(previous.get('continued_from', '')); protected_parent(base)
    for before, after in ((base, platform_origin), (platform_origin, origin)):
        marker = document(before/'continued-by.json'); plan = document(after/'plan.json')
        child = document(after/'installation.json')
        if (marker.get('output') != str(after) or child.get('continued_from') != str(before)
                or marker.get('plan_sha256') != digest(plan) or child.get('plan_sha256') != digest(plan)):
            raise InstallerError('Compute continuation lineage differs.')
    core = document(platform_origin/'core/install-transaction.json')
    if (document(base/'installation.json').get('completed') != list(PHASES[:-2])
            or core.get('status') != 'KOLLA_DEPLOYED_NOT_SPVM_ENROLLED'
            or core.get('completed') != ['prechecks','deploy','post-deploy']
            or core.get('guests_launched') is not False):
        raise InstallerError('Successful inherited Kolla completion is required; Kolla will not be repeated.')
    requests = document(base/'requests.json')['hosts']
    if not isinstance(requests,dict) or not requests:
        raise InstallerError('Explicit prepared compute fleet required.')
    host = sorted(requests)[0]
    if {p.name for p in origin.glob('activate-*')} != {'activate-'+host}:
        raise InstallerError('Only the first compute enrollment failure is supported.')
    plan = document(origin/('activate-'+host)/'plan.json')
    record = document(origin/('activate-'+host)/'transaction/transaction.json')
    if (record.get('schema_version')!=1 or plan.get('schema_version')!=1
            or plan.get('mode')!='ACTIVATE' or record.get('status') != 'NEEDS_REVIEW' or record.get('host') != host
            or record.get('mode') != 'ACTIVATE' or record.get('done') != ['quarantine','stop']
            or record.get('mutations') != ['quarantine','stop'] or record.get('inflight') != 'enroll'
            or record.get('guests_restarted') is not False or record.get('quarantine_confirmed_after_failure') is not True
            or plan.get('kind') != 'initial-runtime-compute-v1' or plan.get('host') != host
            or plan.get('plan_sha256') != activation_digest({k:v for k,v in plan.items() if k!='plan_sha256'})
            or record.get('plan_sha256') != plan.get('plan_sha256')):
        raise InstallerError('Only stopped/quarantined first-compute enrollment may be reconciled; no stop/start replay.')
    commit = document(origin/'plan.json')['controller_review']['driver_commit']
    matches = []
    for name, wanted in parent['inputs'].items():
        path = Path(name)
        if path.name == 'manifest.json' and not path.is_symlink() and file_hash(path) == wanted:
            value = json.loads(path.read_bytes())
            if value.get('source_commit') == commit: matches.append(value)
    if len(matches) != 1:
        raise InstallerError('Previous verified driver manifest is missing or ambiguous.')
    expected = {Path(n).name:s for n,s in matches[0]['source_sha256'].items()
                if Path(n).parent == Path('deployment') and n.endswith('.py')}
    if plan.get('source_sha256') != expected:
        raise InstallerError('Failed activation source binding differs from its verified bundle.')
    return R(parent=parent, base=base, platform_origin=platform_origin, host=host,
             plan=plan, record=record, manifest=matches[0])


def idle_services_equal(before, after):
    if set(before) != set(after): return False
    for name, value in before.items():
        if value == after[name]: continue
        if name != 'libvirtd.service': return False
        try:
            a = dict(line.split('=',1) for line in value.splitlines())
            b = dict(line.split('=',1) for line in after[name].splitlines())
            if set(a) != {'NRestarts','MainPID','ActiveEnterTimestampMonotonic'} or set(a) != set(b): return False
            if a['NRestarts'] != '0' or b['NRestarts'] != '0': return False
            if any(int(x[k]) <= 0 for x in (a,b) for k in ('MainPID','ActiveEnterTimestampMonotonic')): return False
        except (ValueError, TypeError): return False
    return True


def validate_current(old, current, wanted):
    """Read-only plans independently proved empty cloud/domain/state/PCI inventory."""
    if (current.get('enrollment') != wanted or old.get('enrollment') != wanted
            or old.get('config_sha256') != current.get('config_sha256')
            or old.get('transport') != current.get('transport')
            or old['service_before']['id'] != current['service_before']['id']
            or current['service_before']['status'] != 'disabled'
            or set(old['local_before']) != set(current['local_before'])):
        raise InstallerError('Activation identity, config, enrollment or quarantine changed.')
    for name, previous in old['local_before'].items():
        if any(previous[k] != current['local_before'][name][k]
               for k in ('compute_uuid','host_identity_sha256','domains')):
            raise InstallerError('Compute fleet identity/domains changed.')
    a,b = old['host_before'],current['host_before']
    if (b['running'] is not False or b['state'] not in ('created','exited')
            or b['runtime'] != wanted['bootstrap_runtime']
            or any(a[k] != b[k] for k in ('container_id','image','protected','containers','runtime'))
            or not idle_services_equal(a['services'],b['services'])):
        raise InstallerError('Failed compute is no longer the unchanged empty stopped bootstrap host; no enrollment replay.')


def native_checkpoint(old_root, source_root, activation, *, claimed=False):
    from openstack_installer import backend
    from openstack_platform_checkpoint import inspect
    previous = Path(activation['previous_root'])
    if previous.parent != Path('/opt/spvm-openstack') or previous.resolve() != previous:
        raise ValueError('canonical previous controller source root required')
    backend(previous/'driver',verified_bundle=True)
    continuation = document(previous/'data/continuation.json')
    if continuation.get('old_root') != str(old_root): raise ValueError('controller continuation lineage differs')
    marker = document(old_root/'data/platform-install/continued-by.json')
    if (marker.get('controller') != str(previous)
            or marker.get('review_sha256') != digest(continuation['review'])):
        raise ValueError('controller claim differs')
    if any(file_hash(Path(n)) != s for n,s in continuation['review']['files'].items()):
        raise ValueError('previous reviewed controller inputs changed')
    if claimed:
        own=document(source_root/'data/compute-continuation.json')
        marker=document(previous/'data/platform-install/continued-by.json')
        if (own['old_root']!=str(old_root) or own['activation']!=activation
                or marker.get('review_sha256')!=digest(own['review'])):
            raise ValueError('native continuation claim binding differs')
    checkpoint = inspect(previous/'data/platform-install',activation['requests'],
                         claim_root=source_root if claimed else None)
    for name, wanted in activation['requests'].items():
        if checkpoint['files']['runtime-requests/'+name+'.json'] != wanted:
            raise ValueError('controller and coordinator runtime requests differ')
    if activation['host'] != sorted(activation['requests'])[0]: raise ValueError('first compute required')
    return checkpoint


def remote_probe(i):
    """Current reviewed code travels over stdin; no remote files are written."""
    modules = ('openstack_platform_checkpoint','openstack_platform',
               'openstack_install_platform_continue','openstack_install_compute_continue')
    code = 'import sys,types,json\nsys.path.insert(0,'+repr(i.compute_context['previous_root']+'/tools')+')\n'
    for name in modules:
        path = Path(__file__).with_name(name+'.py')
        code += 'm=types.ModuleType('+repr(name)+');m.__file__='+repr(str(path))+';sys.modules['+repr(name)+']=m\n'
        code += 'exec(compile('+repr(path.read_text())+','+repr(name)+',"exec"),m.__dict__)\n'
    code += 'print(json.dumps(sys.modules["openstack_install_platform_continue"].probe('+repr(i.controller_root)+','+repr(i.compute_context['previous_root'])+',activation='+repr(i.compute_context)+')))\n'
    argv = i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+[
        'sudo -n '+shlex.quote(i.controller_root+'/sdk/bin/python')+' -I -B -']
    with (i.output/'private-compute-review.log').open('xb') as log:
        os.fchmod(log.fileno(),0o600)
        result = subprocess.run(argv,input=code.encode(),stdout=subprocess.PIPE,stderr=log,timeout=300)
    if result.returncode or len(result.stdout)>4*1024*1024:
        raise InstallerError('Compute continuation review failed; inspect '+str(i.output/'private-compute-review.log')+'. No deployment was run.')
    return json.loads(result.stdout)


def prepare(i, tx):
    from openstack_install import PHASES
    from openstack_install_phases import BASES
    import openstack_kolla_workspace as workspace
    import yaml
    state = reviewed(i.args.continue_compute_from)
    origin = Path(i.args.continue_compute_from)
    manifest = json.loads((i.path.parent.parent/'manifest.json').read_bytes())
    validate_delta(state.manifest,manifest)
    if not i.args.use_testing_images: raise InstallerError('Explicit --use-test-images acknowledgement remains required.')
    raw = yaml.safe_load(private(state.base/'deployment.input.yaml').read_bytes())
    current = json.loads(json.dumps(i.raw))
    raw['driver'].pop('directory'); current['driver'].pop('directory')
    if raw != current: raise InstallerError('Only the verified driver.directory may change for this continuation.')
    tools = Path(__file__).parent.resolve()
    for filename,wanted in state.parent['inputs'].items():
        path = Path(filename)
        if path == Path(i.args.config) or path.parent == tools and path.suffix == '.py': continue
        if file_hash(path) != wanted: raise InstallerError('Previously approved input changed: '+str(path))
        tx.inputs[filename] = i.inputs[filename] = wanted
    i.verify_environment()
    i.config = str(private(state.base/'deployment.artifacts.yaml')); i.refresh()
    i.workspace = str(state.base/'kolla-bound'); workspace.check(i.workspace)
    i.requests = document(state.base/'requests.json')['hosts']
    binding = document(Path(i.workspace)/'compute-binding.json')
    if (str(i.guest) != document(state.base/'plan.json')['guest_cidr']
            or i.region != binding['bootstrap_runtime']['region_name']
            or any(document(p)['allow_debug'] != i.options.get('allow_debug',False) for p in i.requests.values())
            or any(i.options.get(k+'_base_image',BASES[k]) != document(state.base/'artifacts-result.json')['bases'][k]
                   for k in ('compute','console'))):
        raise InstallerError('Prepared region/guest/debug/base-image policy changed.')
    hosts = document(state.base/'plan.json')['hosts']
    if set(hosts)!=set(i.cfg['hosts']) or any(a['host_identity_sha256'] != hosts[n]['machine_sha256'] for n,a in i.audits.items()):
        raise InstallerError('Prepared host identities changed.')
    controls = [n for n,h in i.cfg['hosts'].items() if 'control' in h['roles']]
    if len(controls)!=1: raise InstallerError('Single-controller reviewed profile required.')
    i.controller = controls[0]
    i.controller_root = '/opt/spvm-openstack/'+i.cfg['deployment_id']+'-'+digest(str(state.base))[:12]
    i.compute_origin = origin; i.compute_host = state.host
    i.compute_context = {'previous_root':'/opt/spvm-openstack/'+i.cfg['deployment_id']+'-platform-'+digest(str(origin))[:12],
                         'host':state.host,'manifest':manifest,
                         'requests':{n:file_hash(private(origin/'runtime-requests'/(n+'.json'))) for n in i.requests}}
    i.platform_target = '/opt/spvm-openstack/'+i.cfg['deployment_id']+'-compute-'+digest(str(i.output))[:12]
    i.compute_review = remote_probe(i)
    if i.compute_review.get('status')!='REVIEWED_COMPUTE_BOUNDARY_READY': raise InstallerError('Controller did not confirm the compute boundary.')
    from initial_compute import Backend
    wanted = document(origin/'runtime-requests'/(state.host+'.json'))
    runner = Backend(i.cfg,i.controller,i.controller_root+'/sdk/bin/python','/etc/spvm/auth/admin-openrc.sh',
                     i.output/'compute-observations',enrollment=wanted)
    i.compute_plan = runner.plan(state.host)
    validate_current(state.plan,i.compute_plan,wanted)
    write(i.output/'compute-plan.json',i.compute_plan)
    i.compute_runner = runner
    for parent in (origin,state.base,state.platform_origin):
        for p in parent.iterdir():
            if p.is_file() and p.suffix in ('.json','.yaml'):
                tx.inputs[str(p)] = i.inputs[str(p)] = file_hash(private(p))
    for p in (origin/('activate-'+state.host)/'plan.json',
              origin/('activate-'+state.host)/'transaction/transaction.json',
              *(origin/'runtime-requests'/(n+'.json') for n in i.requests),
              state.platform_origin/'core/install-transaction.json',state.platform_origin/'core/install-plan.json'):
        tx.inputs[str(p)] = i.inputs[str(p)] = file_hash(private(p))
    tx.inputs[str(i.output/'compute-plan.json')] = file_hash(i.output/'compute-plan.json')
    tx.record.update(completed=list(PHASES[:-1]),continued_from=str(origin))
    return {'schema_version':1,'deployment_id':i.cfg['deployment_id'],'hosts':hosts,
            'storage':document(state.base/'plan.json').get('storage',{}),'blockers':[],
            'continued_from':str(origin),'remaining_phases':['platform'],'remaining_compute':state.host,
            'use_testing_images':True,'controller_review':i.compute_review,
            'management_mode':i.cfg['network'].get('management_mode')}


def claim(i, tx, plan):
    reviewed(i.compute_origin); tx.unchanged()
    marker = i.compute_origin/'continued-by.json'
    with marker.open('x') as stream:
        os.fchmod(stream.fileno(),0o600)
        json.dump({'output':str(i.output),'plan_sha256':digest(plan)},stream)
        stream.flush(); os.fsync(stream.fileno())
    fd = os.open(marker.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


class StoppedBackend:
    """Do not stop the already stopped empty target a second time."""
    def __init__(self, runner): self.runner=runner
    def __getattr__(self, name): return getattr(self.runner,name)
    def stop(self, plan):
        host = self.runner.host_facts(plan['host'],plan['compute_uuid'])
        if host != plan['host_before'] or host['running'] is not False:
            raise InstallerError('Previously stopped compute changed; no second stop is allowed.')


def setup_controller(root, payload):
    c = payload['compute_continuation']; old = Path(c['old_root'])
    if old.parent!=Path('/opt/spvm-openstack') or old.resolve()!=old: raise ValueError('canonical SDK root required')
    code = 'import sys,json;sys.path.insert(0,'+repr(str(root/'tools'))+');from openstack_install_compute_continue import finalize_controller;print(json.dumps(finalize_controller('+repr(str(root))+',json.load(sys.stdin))))'
    result = subprocess.run([str(old/'sdk/bin/python'),'-I','-B','-c',code],input=json.dumps(c),capture_output=True,text=True,timeout=300)
    if result.returncode:
        log=root/'private-compute-prepare.log'
        with log.open('x') as stream:
            os.fchmod(stream.fileno(),0o600); stream.write(result.stderr)
        raise ValueError('compute continuation preparation failed; inspect '+str(log))
    return json.loads(result.stdout)


def finalize_controller(root, c):
    root=Path(root)
    if platform_resume.probe(c['old_root'],root,activation=c['activation'])!=c['review']:
        raise ValueError('reviewed controller state changed')
    data=root/'data'; data.mkdir(mode=0o700)
    write(data/'compute-continuation.json',c)
    # Cross-coordinator exclusion must precede enrollment/start, not merely
    # controller platform completion after compute activation.
    marker=Path(c['activation']['previous_root'])/'data/platform-install/continued-by.json'
    with marker.open('x') as stream:
        os.fchmod(stream.fileno(),0o600)
        json.dump({'controller':str(root),'review_sha256':digest(c['review'])},stream)
        stream.flush();os.fsync(stream.fileno())
    fd=os.open(marker.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    return {'status':'CONTROLLER_COMPUTE_CONTINUATION_PREPARED'}


def complete(i):
    from openstack_install_completion import BOOTSTRAP,snapshot,rpc_process,Operations
    from compute_transaction import execute
    c={'old_root':i.controller_root,'activation':i.compute_context,'review':i.compute_review,
       'plan_sha256':i.compute_plan['plan_sha256']}
    payload={'root':i.platform_target,'machine':i.compute_review['machine'],'files':snapshot(i),'compute_continuation':c}
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n /usr/bin/python3 -I -B -u -c '+shlex.quote(BOOTSTRAP)]
    if rpc_process(i,argv,payload).get('status')!='CONTROLLER_COMPUTE_CONTINUATION_PREPARED':
        raise InstallerError('Controller compute continuation not prepared.')
    result=execute(i.compute_plan,i.compute_plan['plan_sha256'],i.output/'reconciled-compute',StoppedBackend(i.compute_runner))
    write(i.output/'reconciled-compute.json',result)
    if result.get('status')!='COMPUTE_READY' or result.get('guests_restarted') is not False:
        raise InstallerError('Reconciled compute activation did not complete; inspect '+str(i.output/'reconciled-compute/transaction.json')+'. No automatic retry.')
    operations=Operations(i)
    operations.enrollment=str(i.compute_origin/'enrollment/enrollment-plan.json')
    operations.requests={n:document(i.compute_origin/'runtime-requests'/(n+'.json')) for n in i.requests}
    operations.project=i.compute_review['project']; operations.activated.add(i.compute_host)
    code='import sys;sys.path.insert(0,'+repr(i.platform_target+'/tools')+');from openstack_install_compute_continue import serve;serve('+repr(i.platform_target)+')'
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n '+shlex.quote(i.controller_root+'/sdk/bin/python')+' -I -B -u -c '+shlex.quote(code)]
    result=rpc_process(i,argv,{'host':i.compute_host,'plan_sha256':i.compute_plan['plan_sha256']},operations)
    if operations.activated!=set(i.requests) or operations.installed!=set(i.requests) or not operations.audited:
        raise InstallerError('Controller did not complete all remaining compute operations.')
    return result


def serve(directory):
    from openstack_install_phases import load_module
    import openstack_platform as platform
    root=Path(directory); data=root/'data'; c=document(data/'compute-continuation.json')
    acknowledgement=json.loads(sys.stdin.buffer.readline(4096))
    if acknowledgement!={'host':c['activation']['host'],'plan_sha256':c['plan_sha256']}:
        raise ValueError('reviewed compute completion acknowledgement required')
    review=platform_resume.probe(c['old_root'],root,activation=c['activation'],activated=True)
    if review!=c['review']: raise ValueError('controller state changed after compute reconciliation')
    path=root/'driver/deployment/installer.py'
    sys.path[:0]=[str(path.parent),str(root/'driver/nova-spvm')]
    backend=load_module(path,'installer'); sys.modules['installer']=backend
    original=backend.deployment_config; backend.deployment_config=lambda filename,**kw:original(filename,transport=False)
    def forbidden(*a,**kw): raise ValueError('no operator SSH or keys on controller')
    backend.ssh_arguments=forbidden; backend.host_audits=forbidden
    sequence=0
    def operation(name,value):
        nonlocal sequence
        print(json.dumps({'operation':name,'data':value,'sequence':sequence}),flush=True)
        line=sys.stdin.buffer.readline(64*1024*1024+1)
        if len(line)>64*1024*1024: raise ValueError('oversized acknowledgement')
        reply=json.loads(line)
        if set(reply)!={'sequence','result'} or reply['sequence']!=sequence: raise ValueError('invalid acknowledgement')
        sequence+=1; return reply['result']
    old=Path(c['old_root'])
    value={'config':str(old/'data/deployment.yaml'),'workspace':str(old/'data'),'platform':str(old/'data/platform.json')}
    result=platform.post_install(value,path,backend,data/'platform-install',old/'data/core',operations=operation,
        verify_existing_auth=True,checkpoint=review['checkpoint'],reconciled_host=c['activation']['host'])
    print(json.dumps({'result':result}),flush=True)
