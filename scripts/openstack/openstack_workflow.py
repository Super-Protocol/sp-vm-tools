"""Explicit install/add-compute/existing plan/apply coordinator.

Hardware preparation remains separate. Existing clouds are never inferred to be
fresh, and generic reconfigure/upgrade is deliberately not an available action.
"""
import argparse
from contextlib import contextmanager
import fcntl
import importlib
import json
import os
from pathlib import Path
import time
import traceback
import uuid

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_kolla_existing as existing
import openstack_kolla_workspace as workspace
import openstack_kolla_workflow as fresh
import openstack_workflow_skyline as skyline
import openstack_platform as platform


def request(filename):
    value=json.loads(read(filename))
    common={'schema_version','mode','config','workspace','known_hosts'}
    modes={
        'fresh':{'ack_new_cloud','allow_experimental_ubuntu26','use_testing_images'},
        'existing':{'controller','controller_python','openrc','compute_requests'},
        'add-compute':{'controller','controller_python','openrc','host','enrollment_request'}}
    if (not isinstance(value,dict) or type(value.get('schema_version')) is not int
            or value['schema_version']!=1 or value.get('mode') not in modes
            or set(value)-{'skyline','platform','preparation','new_compute'}!=common|modes[value['mode']]):
        raise ValueError('exact explicit workflow request required')
    if 'new_compute' in value:
        if value['mode']!='add-compute' or 'skyline' in value:
            raise ValueError('new-compute preparation is exclusive to add-compute')
        literal_path(value['new_compute'])
        import openstack_compute_prepare
        openstack_compute_prepare.specification(value['new_compute'])
    if 'platform' in value or 'preparation' in value:
        if value['mode']!='fresh' or 'skyline' in value or 'preparation' in value and 'platform' not in value:
            raise ValueError('platform completion/preparation require fresh mode; not staged Skyline adoption')
        literal_path(value['platform']);platform.specification(value['platform'])
        if 'preparation' in value:literal_path(value['preparation'])
    if 'skyline' in value:
        literal_path(value['skyline'])
        skyline.specification(value['skyline'], value['mode'])
    paths=['config','workspace','known_hosts']
    if value['mode']=='fresh':
        if any(type(value[k]) is not bool for k in modes['fresh']) or not value['ack_new_cloud']:
            raise ValueError('explicit fresh-cloud acknowledgements required')
    else:
        # These are interpreted on the configured controller. A normal venv
        # Python is a symlink, and remote paths need not exist on this machine.
        for key in ('controller_python','openrc'):literal_path(value[key],canonical=False)
        if not isinstance(value['controller'],str) or not value['controller']:
            raise ValueError('explicit controller required')
        if value['mode']=='existing':
            if not isinstance(value['compute_requests'],list) or not value['compute_requests']:
                raise ValueError('complete compute requests required')
            for path in value['compute_requests']:literal_path(path)
        else:
            if not isinstance(value['host'],str) or not value['host']:
                raise ValueError('explicit target compute required')
            if value['enrollment_request'] is not None:paths+=['enrollment_request']
    for key in paths:literal_path(value[key])
    return value


def literal_path(value, *, canonical=True):
    if (not isinstance(value,str) or not Path(value).is_absolute()
            or canonical and Path(value).resolve()!=Path(value) or any(ord(c)<32 for c in value)):
        raise ValueError('canonical absolute path required')


@contextmanager
def context(path, value):
    module=existing.backend(path);original=module.ssh_arguments
    def ssh(host):
        args=original(host)
        args[1:1]=['-F','/dev/null','-o','UserKnownHostsFile='+value['known_hosts'],'-o','ForwardAgent=no']
        return args
    module.ssh_arguments=ssh
    try:yield module
    finally:module.ssh_arguments=original


def fingerprint(value,path,module):
    cfg=module.deployment_config(value['config'])
    receipt=workspace.check(value['workspace'])
    if receipt['config_sha256']!=digest(cfg):raise ValueError('workspace/config mismatch')
    paths=[value['config'],value['known_hosts']]
    platform_inputs={}
    if 'platform' in value:
        platform_inputs=platform.input_files(value['platform'])
        platform_inputs['sdk-environment-sha256']=platform.sdk_digest(value['platform'])
        if 'preparation' in value:
            paths.append(value['preparation'])
            prep=json.loads(read(value['preparation']))
            paths.extend(prep[k] for k in ('network_request','geneve_request') if k in prep)
        for p in [Path(path).parent.parent/'manifest.json',*sorted((Path(path).parent.parent/'wheels').glob('*.whl'))]:
            if p.is_file():platform_inputs[str(p)]=file_hash(p)
    if 'skyline' in value:
        paths.append(value['skyline'])
        if value['mode']=='fresh':
            spec=skyline.specification(value['skyline'],'fresh')
            paths.append(str(Path(spec['runtime_dir'])/'runtime-stage.json'))
    if value['mode']=='existing':
        for filename in value['compute_requests']:
            paths.extend((filename,json.loads(read(filename))['profile']))
    elif value['mode']=='add-compute' and value['enrollment_request']:
        paths.append(value['enrollment_request'])
    if 'new_compute' in value:
        import openstack_compute_prepare
        paths.append(value['new_compute'])
        platform_inputs.update({'new-compute-config/'+k:v for k,v in
            openstack_compute_prepare.config_hashes(value['new_compute']).items()})
    source_root=Path(path).parent.parent
    sources=list(Path(path).parent.glob('*.py'))
    sources.extend(p for p in (Path(path).parent/'skyline').rglob('*')
                   if p.is_file() and '__pycache__' not in p.parts)
    for package_root in (source_root,source_root/'nova-spvm'):
        for package in ('nova_spvm','openstack_spvm'):
            sources.extend((package_root/package).rglob('*.py'))
    # Secrets are represented by hashes only, never copied into public output.
    return {'request':value,'workspace_sha256':digest(receipt),
            'files':{**{p:file_hash(p) for p in paths},**platform_inputs},
            'installer_sources':{p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')},
            'backend_sources':{str(p.relative_to(source_root)):file_hash(p) for p in sources}}


def verify_args(value,output):
    return argparse.Namespace(config=value['config'],workspace=value['workspace'],known_hosts=value['known_hosts'],
        controller=value['controller'],controller_python=value['controller_python'],openrc=value['openrc'],
        compute_request=value['compute_requests'],output=str(output))


def fresh_args(value,output,action,plan_sha256=None,resume=False):
    return argparse.Namespace(config=value['config'],workspace=value['workspace'],known_hosts=value['known_hosts'],
        ack_new_cloud=value['ack_new_cloud'],allow_experimental_ubuntu26=value['allow_experimental_ubuntu26'],
        use_testing_images=value['use_testing_images'],output=str(output),action=action,
        plan_sha256=plan_sha256,resume=resume)


def compute_backend(value,module,output, *, initial=False):
    cfg=module.deployment_config(value['config'])
    target=cfg['hosts'].get(value['host'])
    if not target or 'compute' not in target['roles']:raise ValueError('configured compute required')
    options={}
    if initial:
        if target['roles']!=['compute'] or not value['enrollment_request']:
            raise ValueError('empty compute-only target and explicit enrollment request required')
        cls=importlib.import_module('initial_compute').Backend
        options['enrollment']=json.loads(read(value['enrollment_request']))
    else:cls=importlib.import_module('compute_workflow').Backend
    return cls(cfg,value['controller'],value['controller_python'],value['openrc'],output,**options)


def plan_operation(value,path,module,output):
    if value['mode']=='existing':
        report=existing.execute(verify_args(value,output/'existing-plan-check'),path)
        if report['status']!='EXISTING_KOLLA_SCOPED_VERIFIED':raise ValueError('existing verification blocked')
        result={'kind':'existing-noop','changes':False,'report_sha256':report['report_sha256']}
        if 'skyline' in value:result['skyline']=skyline.verify(value,path,module)
        return result
    if value['mode']=='fresh':
        if 'platform' in value:
            reviewed=platform.preflight(value,path,module)
            return {'kind':'fresh-platform-install','changes':True,'platform_intent_sha256':digest(reviewed),'reviewed_platform':reviewed}
        if 'skyline' in value:
            # Validate the exact staged files, environment, vacant targets and
            # existing publication prerequisites without starting services.
            spec=skyline.specification(value['skyline'],'fresh')
            skyline.runtime.inputs(path,argparse.Namespace(**spec))
        result=fresh.execute(fresh_args(value,output/'fresh','plan-kolla-install'),path,module)
        if result.get('blockers'):raise ValueError('fresh host preparation required; inspect inner plan')
        return {'kind':'fresh-install','changes':True,'plan_sha256':result['plan_sha256']}
    backend=compute_backend(value,module,output/'compute-observations')
    if 'new_compute' in value:
        import openstack_compute_prepare
        return openstack_compute_prepare.plan(value,path,module,output,backend)
    plan=backend.plan(value['host'])
    if plan['mode']=='NOOP' and not plan['blockers']:kind='compute-noop'
    else:
        backend=compute_backend(value,module,output/'initial-observations',initial=True)
        plan=backend.plan(value['host']);kind='compute-enrollment'
    if plan['blockers']:raise ValueError('prepared compute prerequisites blocked')
    write(output/'compute-plan.json',plan)
    return {'kind':kind,'changes':plan['mode']!='NOOP','plan_sha256':plan['plan_sha256']}


def apply_operation(value,path,module,output,operation, *, resume=False):
    if operation['kind']=='new-compute':
        import openstack_compute_prepare
        return openstack_compute_prepare.apply(value,path,module,output,
            compute_backend(value,module,output/'compute-observations'))
    if operation['kind']=='fresh-platform-install':
        if resume:raise ValueError('platform resume is not an ambiguous child replay; review journals first')
        if digest(platform.preflight(value,path,module))!=operation['platform_intent_sha256']:
            raise ValueError('platform host/identity intent drift')
        platform.prepare_account(platform.specification(value['platform']),output)
        if 'preparation' in value:platform.prepare_hostnames(value,path,module,output)
        if 'preparation' in value:
            from openstack_platform_network import prepare
            prepare(value,path,module,output)
        # Child plans are produced only once their prerequisites exist. Their
        # policy/source/inputs are already bound by this approved parent plan.
        from openstack_kolla_packages import execute as packages
        cfg=module.deployment_config(value['config'])
        with fresh.hold_libvirt(argparse.Namespace(known_hosts=value['known_hosts']),module,cfg):
            for profile in ('base-packages','host-sdk','api-trust'):
                args=fresh_args(value,output/profile,'plan-'+profile)
                plan=packages(args,path,module)
                if plan.get('blockers'):raise ValueError('host dependencies blocked; inspect child plan')
                args.action='prepare-'+profile;args.plan_sha256=plan['plan_sha256'];packages(args,path,module)
            plan=fresh.execute(fresh_args(value,output/'fresh','plan-kolla-install'),path,module)
            if plan.get('blockers'):raise ValueError('fresh core preparation blocked')
            result=fresh.execute(fresh_args(value,output/'fresh','run-kolla-install',plan['plan_sha256']),path,module)
            if result['status']!='KOLLA_DEPLOYED_NOT_SPVM_ENROLLED':raise ValueError('core install incomplete')
            return platform.post_install(value,path,module,output/'platform',output/'fresh')
    if operation['kind']=='existing-noop':
        report=existing.execute(verify_args(value,output/('existing-apply-'+uuid.uuid4().hex)),path)
        if report['status']!='EXISTING_KOLLA_SCOPED_VERIFIED':raise ValueError('fresh existing verification blocked')
        result={'status':'OPENSTACK_NOOP_COMPLETE','verification_sha256':report['report_sha256']}
        if 'skyline' in value:result['skyline']=skyline.verify(value,path,module)
        return result
    if operation['kind']=='fresh-install':
        if 'skyline' in value:
            spec=skyline.specification(value['skyline'],'fresh')
            skyline.runtime.inputs(path,argparse.Namespace(**spec))
        result=fresh.execute(fresh_args(value,output/'fresh','run-kolla-install',operation['plan_sha256'],resume),path,module)
        if result['status']!='KOLLA_DEPLOYED_NOT_SPVM_ENROLLED':raise ValueError('fresh install incomplete')
        answer={'status':'OPENSTACK_INSTALLED_NOT_SPVM_ENROLLED','completed':result['completed']}
        if 'skyline' in value:
            answer['skyline']=skyline.install(value,path,output/'skyline-runtime')
            if answer['skyline']['status']!='FRESH_SKYLINE_RUNTIME_INSTALLED':
                raise ValueError('Skyline runtime installation incomplete')
        return answer
    backend=compute_backend(value,module,output/'apply-compute-observations',initial=operation['kind']=='compute-enrollment')
    plan=json.loads(read(output/'compute-plan.json'))
    result=importlib.import_module('compute_transaction').execute(plan,operation['plan_sha256'],output/'compute-transaction',backend)
    if result['status'] not in ('NOOP_COMPLETE','COMPUTE_READY'):raise ValueError('compute transaction incomplete')
    return {'status':'OPENSTACK_NOOP_COMPLETE' if result['status']=='NOOP_COMPLETE' else 'OPENSTACK_COMPUTE_READY',
            'host':value['host'],'guests_restarted':result['guests_restarted']}


def execute(args,path):
    value=request(args.request);output=Path(args.output);origin=Path(value['workspace'])
    literal_path(str(output))
    if output.is_relative_to(origin) or origin.is_relative_to(output):raise ValueError('separate private transaction required')
    if args.action=='plan-openstack':output.mkdir(mode=0o700,exist_ok=False)
    if not output.is_dir() or output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:
        raise ValueError('private owned transaction required')
    fd=os.open(output/'.workflow.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_mode & 0o777!=0o600:
            raise ValueError('untrusted transaction lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with context(path,value) as module:
            intent=fingerprint(value,path,module)
            if args.action=='plan-openstack':
                operation=plan_operation(value,path,module,output)
                plan={'schema_version':1,'kind':'openstack-workflow-v1','created_at':int(time.time()),
                      'intent':intent,'operation':operation}
                plan['plan_sha256']=digest(plan);write(output/'workflow-plan.json',plan)
                return {'status':'OPENSTACK_PLAN_READY','mode':value['mode'],'changes':operation['changes'],
                        'plan_sha256':plan['plan_sha256'],'operation':operation['kind']}
            plan=json.loads(read(output/'workflow-plan.json'));body={k:v for k,v in plan.items() if k!='plan_sha256'}
            if (plan.get('kind')!='openstack-workflow-v1' or plan.get('schema_version')!=1
                    or plan.get('plan_sha256')!=args.plan_sha256 or digest(body)!=args.plan_sha256
                    or plan['intent']!=intent):raise ValueError('changed request/source/inputs or incorrect approval')
            journal=output/'workflow-transaction.json'
            record=json.loads(read(journal)) if journal.exists() else None
            if record and record.get('plan_sha256')!=args.plan_sha256:raise ValueError('foreign transaction')
            if record and record.get('status') not in ('COMPLETE','RUNNING','NEEDS_REVIEW'):
                raise ValueError('invalid transaction state')
            kinds={'existing':{'existing-noop':False},'fresh':{'fresh-install':True,'fresh-platform-install':True},
                   'add-compute':{'compute-noop':False,'compute-enrollment':True,'new-compute':True}}
            if (plan['operation']['kind'] not in kinds[value['mode']]
                    or type(plan['operation']['changes']) is not bool
                    or plan['operation']['changes']!=kinds[value['mode']][plan['operation']['kind']]):
                raise ValueError('unsupported operation')
            # Bind the child approval bytes, not merely the parent's reference.
            if plan['operation']['kind'] not in ('existing-noop','fresh-platform-install'):
                child=output/('fresh/install-plan.json' if plan['operation']['kind']=='fresh-install' else
                    'new-compute-plan.json' if plan['operation']['kind']=='new-compute' else 'compute-plan.json')
                inner=json.loads(read(child))
                # Preserve each delegated workflow's published digest ABI:
                # compute plans use compact JSON, Kolla plans use spaced JSON.
                child_digest=(digest if plan['operation']['kind'] in ('fresh-install','new-compute')
                              else importlib.import_module('accounting_reconcile').digest)
                if (inner.get('plan_sha256')!=plan['operation']['plan_sha256']
                        or child_digest({k:v for k,v in inner.items() if k!='plan_sha256'})!=plan['operation']['plan_sha256']):
                    raise ValueError('child approval drift')
            if record and record.get('status')=='COMPLETE' and plan['operation']['kind']!='existing-noop':
                return {'status':'ALREADY_APPLIED_NOT_REVERIFIED','previous_result':record['result'],
                        'next_step':'plan mode=existing to verify current installed state; do not replay deployment'}
            if plan['operation']['changes'] and not args.allow_changes:raise ValueError('explicit --allow-changes required')
            if record and record['status']!='COMPLETE' and not args.resume:
                raise ValueError('explicit resume required; inspect child journal first')
            if args.resume and not record:raise ValueError('no transaction to resume')
            if not record and (type(plan['created_at']) is not int or not 0<=time.time()-plan['created_at']<=900):
                raise ValueError('first apply requires a fresh plan')
            record={'schema_version':1,'status':'RUNNING','plan_sha256':args.plan_sha256,'operation':plan['operation']['kind']}
            write(journal,record)
            try:
                result=apply_operation(value,path,module,output,plan['operation'],resume=args.resume)
                if fingerprint(value,path,module)!=intent:raise ValueError('inputs changed during operation')
            except BaseException as error:
                record.update(status='NEEDS_REVIEW',error_type=type(error).__name__);write(journal,record);raise
            record.update(status='COMPLETE',result=result,completed_at=int(time.time()));write(journal,record)
            return result
    finally:os.close(fd)


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-openstack','apply-openstack'])
    for name in ('request','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256');parser.add_argument('--allow-changes',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args(argv)
    try:print(json.dumps(execute(args,path)));return 0
    except Exception as error:
        frame=traceback.extract_tb(error.__traceback__)[-1]
        print(json.dumps({'status':'OPENSTACK_WORKFLOW_REFUSED','error_type':type(error).__name__,
                          'location':Path(frame.filename).name+':'+frame.name+':'+str(frame.lineno),
                          'next_step':'review request, workflow plan and private child journals; no automatic repair'}))
        return 2
