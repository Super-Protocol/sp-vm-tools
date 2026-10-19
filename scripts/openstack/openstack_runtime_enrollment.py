"""Stage and install initial runtime enrollment on an already stopped empty compute."""
import argparse
import fcntl
import importlib.util
import json
import hashlib
import os
from pathlib import Path
import sys
import time

from openstack_compute_image import read
from openstack_container_engine import digest,file_hash,write
import openstack_kolla_workspace as workspace
from openstack_host_helper import request as helper_request


def load_backend(path):
    loader=importlib.util.spec_from_file_location('offline_runtime_enrollment',Path(path).with_name('runtime_enrollment.py'))
    module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module);return module


def stage(args,path):
    backend=load_backend(path);root=Path(args.workspace);receipt=workspace.check(root)
    binding=json.loads(read(root/'compute-binding.json'));enrollment=json.loads(read(args.enrollment_plan))
    body={k:v for k,v in binding.items() if k!='binding_sha256'}
    staged={k:v for k,v in enrollment.items() if k!='enrollment_sha256'}
    if (binding.get('binding_sha256')!=digest(body) or receipt.get('compute_binding')!=digest(body)
            or enrollment.get('schema_version')!=1 or enrollment.get('status')!='SPVM_ENROLLMENT_STAGED_NOT_APPLIED'
            or enrollment.get('enrollment_sha256')!=digest(staged)
            or enrollment.get('compute_binding_sha256')!=binding['binding_sha256'] or not binding.get('hosts')):
        raise ValueError('matching hash-bound compute/enrollment inputs required')
    requests={}
    for name,host in binding['hosts'].items():
        if not name or Path(name).name!=name or name in ('.','..'):raise ValueError('safe bound host name required')
        if file_hash(host['request_file'])!=host['request_sha256']:raise ValueError('prepared helper request drift')
        spec=helper_request(host['request_file'])
        if spec['machine_sha256']!=host['machine_sha256'] or spec['compute_uuid']!=host['compute_uuid']:
            raise ValueError('helper identity differs from bound compute')
        requests[name]=backend.request({'schema_version':1,'helper_request':spec,
            'bootstrap_runtime':binding['bootstrap_runtime'],'runtime':enrollment['runtime'],
            'image_id':binding['image_id'],'image_context_sha256':binding['image_context_sha256'],
            'enrollment_sha256':enrollment['enrollment_sha256']})
    if workspace.check(root)!=receipt:raise ValueError('workspace changed during staging')
    for host in binding['hosts'].values():
        if file_hash(host['request_file'])!=host['request_sha256']:raise ValueError('helper request changed during staging')
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('new separate private staging directory required')
    output.mkdir(mode=0o700,exist_ok=False)
    for name,value in requests.items():
        write(output/(name+'.json'),value)
    result={'status':'OFFLINE_RUNTIME_REQUESTS_STAGED_NOT_INSTALLED','hosts':sorted(requests),
            'enrollment_sha256':enrollment['enrollment_sha256'],'services_restarted':False,'guests_launched':False}
    write(output/'staging.json',result);return result


def protected_output(path):
    if not path.is_absolute() or path.resolve()!=path:raise ValueError('canonical root-protected transaction required')
    for parent in (path,*path.parents):
        if parent.exists():
            st=parent.lstat()
            if parent.is_symlink() or st.st_uid!=0 or st.st_mode & 0o022:
                raise ValueError('transaction ancestors must be root-owned and not writable by others')
    if path==Path('/') or any(path.is_relative_to(Path(p)) for p in ('/etc','/run','/usr','/boot','/proc','/sys','/dev')):
        raise ValueError('separate persistent root transaction directory required')


def execute(args,path,host=None):
    started=time.time()
    backend=load_backend(path);wanted=backend.request(backend.parse(read(args.request)))
    host=host or backend.Host()
    identity={'request_sha256':digest(wanted),'backend_sha256':file_hash(Path(path).with_name('runtime_enrollment.py')),
              'runner_sha256':file_hash(Path(__file__))}
    if args.action=='check-runtime-enrollment':
        if hashlib.sha256(host.read('/etc/machine-id').strip()).hexdigest()!=wanted['helper_request']['machine_sha256']:
            raise ValueError('wrong schema-validation machine')
        output=Path(args.output);protected_output(output);output.mkdir(mode=0o700,exist_ok=False)
        journal=output/'runtime-image-check.json';record={'status':'RUNNING_IMAGE_SCHEMA_CHECK','identity':identity}
        write(journal,record)
        try:
            host.validate_image(wanted)
            if backend.parse(read(args.request))!=wanted:raise ValueError('request changed during schema check')
            record.update(status='RUNTIME_IMAGE_SCHEMA_COMPATIBLE_NOT_INSTALLED',services_restarted=False,guests_launched=False)
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record)
        return {k:record[k] for k in ('status','services_restarted','guests_launched')}
    before=host.observe(wanted)
    if time.time()-started>900:raise ValueError('host observation exceeded approval freshness window')
    output=Path(args.output);protected_output(output)
    if args.action=='plan-runtime-enrollment':
        output.mkdir(mode=0o700,exist_ok=False)
        plan={'schema_version':1,'kind':'offline-runtime-enrollment-v1','identity':identity,'host':before,'created_at':int(time.time())}
        plan['plan_sha256']=digest(plan);write(output/'runtime-plan.json',plan)
        return {'status':'OFFLINE_RUNTIME_PLAN_READY','plan_sha256':plan['plan_sha256'],'services_restarted':False}
    if output.stat().st_mode & 0o077:raise ValueError('private root transaction required')
    global_lock=host.trusted('/run/spvm')/'runtime-enrollment.lock'
    fd=os.open(global_lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if st.st_uid!=host.owner or st.st_mode & 0o777!=0o600 or st.st_nlink!=1:raise ValueError('protected host-wide lock required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=backend.parse(read(output/'runtime-plan.json'));body={k:v for k,v in plan.items() if k!='plan_sha256'}
        if (not args.ack_compute_quarantined or plan.get('kind')!='offline-runtime-enrollment-v1'
                or plan.get('plan_sha256')!=digest(body) or args.plan_sha256!=plan['plan_sha256']
                or plan['identity']!=identity or plan['host']!=before or host.observe(wanted)!=before
                or type(plan['created_at']) is not int or not 0<=time.time()-plan['created_at']<=900):
            raise ValueError('fresh unchanged offline approval/quarantine acknowledgement required')
        journal=output/'runtime-transaction.json'
        if journal.exists() or journal.is_symlink():raise ValueError('previous install needs review, not replay')
        record={'status':'RUNNING','plan_sha256':args.plan_sha256,'services_restarted':False,'guests_launched':False}
        write(journal,record)
        try:
            result=host.install(wanted,before,output/'bootstrap-runtime.backup.json')
            if backend.parse(read(args.request))!=wanted:raise ValueError('request changed during installation')
            record.update(status=result['status'],result=result)
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record);return result
    finally:os.close(fd)


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('stage-runtime-enrollment')
    for name in ('workspace','enrollment-plan','output'):p.add_argument('--'+name,required=True)
    for action in ('check-runtime-enrollment','plan-runtime-enrollment','install-runtime-enrollment'):
        p=sub.add_parser(action)
        for name in ('request','output'):p.add_argument('--'+name,required=True)
        p.add_argument('--plan-sha256');p.add_argument('--ack-compute-quarantined',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action=='stage-runtime-enrollment':result=stage(args,path)
        else:
            if os.geteuid()!=0:raise ValueError('root-local offline compute required')
            result=execute(args,path)
        print(json.dumps(result));return 0
    except Exception:
        print('ERROR: offline runtime enrollment refused/failed; preserve journal/backup and leave compute stopped. No service restart requested.',file=sys.stderr)
        return 2
