"""Approved add-only Neutron shared-network plan/apply. Never changes host networking."""
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
    source=Path(path).with_name('spvm_network.py')
    loader=importlib.util.spec_from_file_location('portable_spvm_network',source)
    module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module)
    return module


def execute(args,path,connection):
    started=time.time()
    backend=load_backend(path);request=backend.normalize(json.loads(read(args.request)))
    identity={'request_sha256':digest(request),'backend_sha256':file_hash(Path(path).with_name('spvm_network.py')),
              'runner_sha256':file_hash(Path(__file__)),'ca_sha256':file_hash(args.ca_file),'cloud_profile':args.cloud}
    state=backend.inspect(connection,request)
    if time.time()-started>900:raise ValueError('Neutron observations exceeded freshness window')
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical absolute transaction required')
    if args.action=='plan-spvm-network':
        plan={'schema_version':1,'kind':'spvm-network-approval-v1','identity':identity,'state':state,'created_at':int(time.time())}
        plan['plan_sha256']=digest(plan)
        output.mkdir(mode=0o700,exist_ok=False);write(output/'network-plan.json',plan)
        return {'status':'SPVM_NETWORK_PLAN_READY','plan_sha256':plan['plan_sha256'],'state':state}
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:raise ValueError('private owned transaction required')
    lock=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=json.loads(read(output/'network-plan.json'));body={k:v for k,v in plan.items() if k!='plan_sha256'}
        if (plan.get('kind')!='spvm-network-approval-v1' or plan.get('plan_sha256')!=digest(body)
                or args.plan_sha256!=plan['plan_sha256'] or plan['identity']!=identity or plan['state']!=state
                or type(plan['created_at']) is not int or not 0<=time.time()-plan['created_at']<=900):
            raise ValueError('fresh unchanged explicit network approval required')
        journal=output/'network-transaction.json'
        if journal.exists() or journal.is_symlink():raise ValueError('previous apply needs manual review')
        record={'status':'RUNNING','plan_sha256':args.plan_sha256,'host_network_changed':False,'guests_launched':False}
        write(journal,record)
        try:
            result=backend.apply(connection,request,state)
            if backend.normalize(json.loads(read(args.request)))!=request:raise ValueError('network request changed during apply')
            record.update(status=result['status'],result=result)
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record)
        return {k:result[k] for k in ('status','network_id','guests_launched','host_network_changed')}
    finally:os.close(lock)


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-spvm-network','apply-spvm-network'])
    for name in ('request','cloud','ca-file','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        read(args.ca_file)
        request=load_backend(path).normalize(json.loads(read(args.request)))
        import openstack
        connection=openstack.connect(cloud=args.cloud,region_name=request['region_name'],verify=args.ca_file,api_timeout=20)
        try:result=execute(args,path,connection)
        finally:connection.close()
        print(json.dumps(result));return 0
    except Exception:
        print('ERROR: SPVM network operation refused/failed; inspect private journal/partial resources. No host or VM action requested.',file=sys.stderr)
        return 2
