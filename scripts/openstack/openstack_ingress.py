"""Initial ingress scaffold and separately approved activation. No live adoption."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

from openstack_compute_image import read
from openstack_container_engine import digest,file_hash,write
from openstack_runtime_enrollment import protected_output


def load(path):
    loader=importlib.util.spec_from_file_location('portable_ingress_setup',Path(path).with_name('ingress_setup.py'))
    module=importlib.util.module_from_spec(loader);loader.loader.exec_module(module);return module


def stage(args,path):
    backend=load(path);wanted=backend.normalize(json.loads(read(args.request)))
    base=read(args.base_config);files=backend.assemble(path,wanted,base.decode())
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('new canonical private staging directory required')
    output.mkdir(mode=0o700,exist_ok=False);(output/'payload').mkdir(mode=0o700)
    manifest={'schema_version':1,'kind':'initial-spvm-ingress-v1','request':wanted,
              'backend_sha256':file_hash(Path(path).with_name('ingress_setup.py')),'files':{}}
    for index,(target,(body,mode)) in enumerate(sorted(files.items())):
        relative='payload/'+str(index)
        with (output/relative).open('xb') as stream:
            os.chmod(stream.name,0o600);stream.write(body)
        manifest['files'][target]={'path':relative,'sha256':backend.sha(body),'mode':mode}
    with (output/'base-haproxy.cfg').open('xb') as stream:
        os.chmod(stream.name,0o600);stream.write(base)
    manifest['package_sha256']=digest(manifest);write(output/'manifest.json',manifest)
    return {'status':'INGRESS_SCAFFOLD_STAGED_NOT_INSTALLED','package_sha256':manifest['package_sha256'],
            'clusters':0,'dns_changed':False,'services_restarted':False}


def package(args,path):
    backend=load(path);root=Path(args.package);manifest=json.loads(read(root/'manifest.json'))
    body={k:v for k,v in manifest.items() if k!='package_sha256'}
    if (set(manifest)!={'schema_version','kind','request','backend_sha256','files','package_sha256'}
            or manifest.get('kind')!='initial-spvm-ingress-v1' or type(manifest.get('schema_version')) is not int or manifest.get('schema_version')!=1
            or manifest.get('package_sha256')!=digest(body)
            or args.package_sha256!=manifest['package_sha256']
            or manifest.get('backend_sha256')!=file_hash(Path(path).with_name('ingress_setup.py'))):
        raise ValueError('reviewed matching ingress package/source SHA required')
    wanted=backend.normalize(manifest['request'])
    files=backend.assemble(path,wanted,read(root/'base-haproxy.cfg').decode())
    expected={target:{'path':'payload/'+str(index),'sha256':backend.sha(value[0]),'mode':value[1]}
              for index,(target,value) in enumerate(sorted(files.items()))}
    if manifest['files']!=expected:raise ValueError('package destination/source/mode allowlist differs')
    for target,value in expected.items():
        if read(root/value['path'])!=files[target][0]:raise ValueError('ingress payload drift')
    identity={'package_sha256':manifest['package_sha256'],'runner_sha256':file_hash(Path(__file__)),
              'output_guard_sha256':file_hash(Path(__file__).with_name('openstack_runtime_enrollment.py'))}
    return backend,wanted,files,identity


def execute(args,path,host=None):
    started=time.time();backend,wanted,files,identity=package(args,path);host=host or backend.Host()
    if args.action=='check-ingress':
        if backend.sha(host.read('/etc/machine-id').strip())!=wanted['machine_sha256']:raise ValueError('wrong ingress check machine')
        output=Path(args.output);protected_output(output);output.mkdir(mode=0o700,exist_ok=False)
        journal=output/'ingress-check.json';record={'status':'CHECKING_INGRESS_CANDIDATE','identity':identity}
        write(journal,record)
        try:
            host.validate(files[backend.CONFIG][0],output)
            record.update(status='INGRESS_SYNTAX_PASSED_NOT_INSTALLED',services_restarted=False,dns_changed=False)
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record);return {k:record[k] for k in ('status','services_restarted','dns_changed')}
    activation=args.action in ('plan-ingress-activation','activate-ingress')
    observe=host.activation_observe if activation else host.observe
    observed=observe(wanted,files)
    if time.time()-started>900:raise ValueError('ingress observations exceeded freshness window')
    output=Path(args.output);protected_output(output)
    plan_name='ingress-activation-plan.json' if activation else 'ingress-plan.json'
    kind='initial-ingress-activation-v1' if activation else 'fresh-ingress-install-v1'
    if args.action in ('plan-ingress','plan-ingress-activation'):
        output.mkdir(mode=0o700,exist_ok=False)
        plan={'schema_version':1,'kind':kind,'identity':identity,'host':observed,'created_at':int(time.time())}
        plan['plan_sha256']=digest(plan);write(output/plan_name,plan)
        return {'status':'INGRESS_ACTIVATION_PLAN_READY' if activation else 'INGRESS_INSTALL_PLAN_READY',
                'plan_sha256':plan['plan_sha256'],'services_restarted':False}
    if output.stat().st_mode & 0o077:raise ValueError('private root transaction required')
    directory=host.path('/run/lock');st=directory.lstat()
    if directory.is_symlink() or not stat.S_ISDIR(st.st_mode) or st.st_uid!=host.owner or (st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX):
        raise ValueError('root-owned protected/sticky lock directory required')
    fd=os.open(directory/'spvm-ingress-install.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if st.st_uid!=host.owner or not stat.S_ISREG(st.st_mode) or st.st_mode & 0o777!=0o600 or st.st_nlink!=1:
            raise ValueError('protected ingress install lock required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=json.loads(read(output/plan_name));body={k:v for k,v in plan.items() if k!='plan_sha256'}
        if (plan.get('kind')!=kind or plan.get('plan_sha256')!=digest(body)
                or args.plan_sha256!=plan['plan_sha256'] or plan['identity']!=identity or plan['host']!=observed
                or observe(wanted,files)!=observed or type(plan['created_at']) is not int
                or not 0<=time.time()-plan['created_at']<=900):raise ValueError('fresh unchanged approved ingress plan required')
        journal=output/('ingress-activation-transaction.json' if activation else 'ingress-transaction.json')
        if journal.exists() or journal.is_symlink():raise ValueError('previous install requires review, not replay')
        record={'status':'RUNNING','plan_sha256':args.plan_sha256,'services_restarted':False,'dns_changed':False};write(journal,record)
        try:
            result=host.activate(wanted,files,observed) if activation else host.install(wanted,files,observed,output)
            if package(args,path)[3]!=identity:raise ValueError('package changed during install')
            record.update(status=result['status'],result=result)
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record);return result
    finally:os.close(fd)


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('stage-ingress')
    for name in ('request','base-config','output'):p.add_argument('--'+name,required=True)
    for action in ('check-ingress','plan-ingress','install-ingress','plan-ingress-activation','activate-ingress'):
        p=sub.add_parser(action)
        for name in ('package','package-sha256','output'):p.add_argument('--'+name,required=True)
        p.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        if args.action=='stage-ingress':result=stage(args,path)
        else:
            if os.geteuid()!=0:raise ValueError('root-local ingress host required')
            result=execute(args,path)
        print(json.dumps(result));return 0
    except Exception:
        print('ERROR: ingress operation refused/failed; review private journal and actual HAProxy state before retry. No DNS/VM action requested.',file=sys.stderr)
        return 2
