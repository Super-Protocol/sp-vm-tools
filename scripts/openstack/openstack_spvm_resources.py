"""Plan/apply SPVM private API resources using an explicit operator cloud profile."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write


def load_backend(path):
    root=Path(path).parent
    package=root.parent/'nova-spvm'
    if not package.is_dir():package=root.parent
    sys.path.insert(0,str(package))
    loader=importlib.util.spec_from_file_location('portable_spvm_resources',root/'spvm_resources.py')
    backend=importlib.util.module_from_spec(loader);loader.loader.exec_module(backend)
    return backend


def execute(args,path,connection):
    backend=load_backend(path)
    enrollment=json.loads(read(args.enrollment_plan))
    identity={'enrollment_file_sha256':file_hash(args.enrollment_plan),
              'backend_sha256':{str(p.relative_to(Path(path).parent.parent)):file_hash(p)
                  for folder in (Path(path).parent,Path(path).parent.parent/'nova-spvm',
                                 Path(path).parent.parent/'nova_spvm',Path(path).parent.parent/'openstack_spvm')
                  for p in sorted(folder.rglob('*.py')) if '__pycache__' not in p.parts},
              'runner_sha256':file_hash(Path(__file__)),'ca_sha256':file_hash(args.ca_file),
              'cloud_profile':args.cloud,'project_id':args.project_id}
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical absolute output required')
    if args.action=='check-spvm-barbican':
        output.mkdir(mode=0o700,exist_ok=False)
        record={'status':'RUNNING_BARBICAN_PROBE','identity':identity}
        write(output/'probe-transaction.json',record)
        try:
            result=backend.barbican_probe(connection,enrollment,args.project_id)
            record.update(status='BARBICAN_PROBE_PASSED' if result['passed'] else 'NEEDS_REVIEW',result=result)
        except BaseException:
            record['status']='NEEDS_REVIEW';write(output/'probe-transaction.json',record);raise
        write(output/'probe-transaction.json',record)
        return {'status':record['status'],**result}
    state=backend.inspect(connection,enrollment,args.project_id)
    if args.action=='plan-spvm-resources':
        plan={'schema_version':1,'kind':'spvm-resources-approval-v1','identity':identity,'state':state,
              'created_at':int(time.time())}
        plan['plan_sha256']=digest(plan)
        output.mkdir(mode=0o700,exist_ok=False);write(output/'resources-plan.json',plan)
        return {'status':'SPVM_RESOURCES_PLAN_READY','plan_sha256':plan['plan_sha256'],
                'flavors':[i['action'] for i in state['flavors']], 'images':[i['action'] for i in state['images']],
                'guests_launched':False}
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:raise ValueError('private owned transaction required')
    lock=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=json.loads(read(output/'resources-plan.json'));body={k:v for k,v in plan.items() if k!='plan_sha256'}
        if (plan.get('kind')!='spvm-resources-approval-v1' or plan.get('plan_sha256')!=digest(body)
                or args.plan_sha256!=plan['plan_sha256'] or plan['identity']!=identity or plan['state']!=state
                or type(plan['created_at']) is not int or not 0<=time.time()-plan['created_at']<=900):
            raise ValueError('fresh unchanged approved API resource plan required')
        journal=output/'resources-transaction.json'
        if journal.exists() or journal.is_symlink():raise ValueError('previous apply needs review, never replay blindly')
        record={'status':'RUNNING','plan_sha256':args.plan_sha256,'guests_launched':False};write(journal,record)
        try:
            result=backend.apply(connection,enrollment,args.project_id,state)
            if file_hash(args.enrollment_plan)!=identity['enrollment_file_sha256']:raise ValueError('enrollment input drift')
            record.update(status=result['status'],result=result)
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record)
        return {k:result[k] for k in ('status','guests_launched','network_created')}
    finally:os.close(lock)


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-spvm-resources','apply-spvm-resources','check-spvm-barbican'])
    for name in ('enrollment-plan','project-id','cloud','ca-file','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        # CA must be an explicit regular, non-symlink operator-trusted file.
        read(args.ca_file)
        import openstack
        connection=openstack.connect(cloud=args.cloud,verify=args.ca_file,api_timeout=20)
        try:result=execute(args,path,connection)
        finally:connection.close()
        print(json.dumps(result));return 2 if result.get('passed') is False else 0
    except Exception:
        print('ERROR: SPVM API resource operation refused/failed; inspect private transaction. No VM action requested.',file=sys.stderr)
        return 2
