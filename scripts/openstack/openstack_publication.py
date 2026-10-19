"""Root-authorized publication through installed SPVM admission/readiness tools.

No guest create, reboot, DNS writes or credentials are implemented here. The
fixed installed helpers validate Nova/Neutron/Barbican identity and exact domains.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import time

from openstack_container_engine import digest,file_hash,write
from openstack_runtime_enrollment import protected_output

ENROLL=Path('/usr/local/sbin/spvm-ingress-enroll')
UI=Path('/usr/local/sbin/spvm-ui-ready')
POLICY=Path('/etc/spvm/ingress-policy.json')
PORTS=Path('/etc/spvm/ingress-http-ports.json')
TOOLS=(ENROLL,UI,Path('/usr/local/sbin/spvm-ingress-refresh'),
       Path('/usr/local/sbin/spvm-ingress-reconcile'),Path('/usr/local/sbin/spvm-http-reconcile'),
       Path('/usr/local/libexec/spvm/release_service_smoke.py'))
LOCK=Path('/run/lock/spvm-publication-workflow.lock')


def stable(before,after):
    mutable={str(POLICY),str(PORTS),'/etc/spvm/ingress-registry.json'}
    return (all(after.get(k)==before[k] for k in ('cluster_uuid','swarm_domain','machine_sha256'))
            and {k:v for k,v in after['files'].items() if k not in mutable}
                =={k:v for k,v in before['files'].items() if k not in mutable})


def open_lock():
    parent=LOCK.parent.lstat()
    if parent.st_uid!=0 or LOCK.parent.is_symlink() or (parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX):
        raise ValueError('protected/sticky root lock directory required')
    fd=os.open(LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if st.st_uid!=0 or st.st_nlink!=1 or not stat.S_ISREG(st.st_mode) or st.st_mode & 0o777!=0o600:
            raise ValueError('protected publication lock required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd);raise


class Host:
    def inspect(self,request):
        # Check trust BEFORE importing any root helper code.
        for path in (*TOOLS,POLICY,PORTS,Path(request)):
            if not path.is_absolute() or path.resolve()!=path:raise ValueError('canonical publication input required')
            for item in (path,*path.parents):
                st=item.lstat()
                if item.is_symlink() or st.st_uid!=0 or st.st_mode & 0o022:
                    raise ValueError('root-protected publisher inputs required')
            if not path.is_file() or path.stat().st_nlink!=1 or path.stat().st_size>4194304:
                raise ValueError('bounded regular publication input required')
        for path in (POLICY,PORTS,Path(request)):
            if path.stat().st_mode & 0o077:raise ValueError('private publication policy/permit required')
        helpers=runpy.run_path(str(ENROLL));value=json.loads(Path(request).read_text())
        helpers['selection'](value)
        ui=runpy.run_path(str(UI));ui['trusted'](POLICY,private=True)
        policy=json.loads(POLICY.read_text());registry=Path(policy['registry'])
        if registry!=Path('/etc/spvm/ingress-registry.json'):raise ValueError('fixed installed registry required')
        ui['trusted'](registry,private=True)
        machine=Path('/etc/machine-id')
        ui['trusted'](machine)
        return {'cluster_uuid':value['cluster'],'swarm_domain':value['swarm_domain'],
                'machine_sha256':hashlib.sha256(machine.read_bytes().strip()).hexdigest(),
                'files':{str(p):file_hash(p) for p in (*TOOLS,POLICY,PORTS,Path(request),registry)}}

    def run(self,stage,request,cluster,timeout):
        if stage=='validate':args=[str(ENROLL),'--policy',str(POLICY),'--request',str(request)]
        elif stage=='enroll':args=[str(ENROLL),'--policy',str(POLICY),'--request',str(request),'--apply']
        elif stage=='publish':args=[str(UI),'--policy',str(POLICY),'--cluster',cluster,
                                     '--timeout',str(max(0.001,timeout-2)),'--apply']
        else:raise ValueError('unknown fixed publication stage')
        result=subprocess.run(['/usr/bin/python3',*args],capture_output=True,text=True,timeout=timeout,
            env={'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
        if len(result.stdout)>1048576:raise ValueError('publication result limit exceeded')
        value=json.loads(result.stdout.splitlines()[-1])
        if value.get('cluster_uuid')!=cluster:raise ValueError('publication result cluster differs')
        expected={'validate':{'ENROLLMENT_VALIDATED','WAITING_GATEWAY'},
                  'enroll':{'ENROLLED','ENROLLMENT_UNCHANGED','WAITING_GATEWAY'},'publish':{'UI_READY'}}[stage]
        if value.get('status') not in expected or result.returncode!=(2 if value['status']=='WAITING_GATEWAY' else 0):
            raise ValueError('publication stage failed; no automatic rollback/retry')
        return {k:value[k] for k in ('status','cluster_uuid')}


def execute(args,backend,host=None):
    host=host or Host();output=Path(args.output);protected_output(output)
    if type(args.timeout) is not int or not 10<=args.timeout<=3600:raise ValueError('bounded timeout required')
    identity={'backend_sha256':file_hash(backend),'runner_sha256':file_hash(__file__),
              'output_guard_sha256':file_hash(Path(__file__).with_name('openstack_runtime_enrollment.py')),
              'request':str(Path(args.request)),'timeout':args.timeout}
    before=host.inspect(args.request)
    if args.action=='plan-spvm-publication':
        result=host.run('validate',args.request,before['cluster_uuid'],min(180,args.timeout))
        if host.inspect(args.request)!=before:raise ValueError('publication changed during validation')
        output.mkdir(mode=0o700,exist_ok=False)
        plan={'schema_version':1,'kind':'spvm-publication-v1','identity':identity,
              'before':before,'created_at':int(time.time())}
        plan['plan_sha256']=digest(plan);write(output/'publication-plan.json',plan)
        return {'status':'SPVM_PUBLICATION_PLAN_READY','plan_sha256':plan['plan_sha256'],
                'gateway_status':result['status'],'cluster_uuid':before['cluster_uuid']}
    if output.stat().st_mode & 0o077:raise ValueError('private root publication transaction required')
    # Global workflow lock is distinct from the retirement/reconciler locks
    # acquired internally by each installed helper (no recursive flock).
    fd=open_lock()
    try:
        plan=json.loads((output/'publication-plan.json').read_text())
        if (plan.get('kind')!='spvm-publication-v1' or plan.get('plan_sha256')!=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
                or args.plan_sha256!=plan['plan_sha256'] or plan['identity']!=identity or plan['before']!=before
                or host.inspect(args.request)!=before or type(plan['created_at']) is not int
                or not 0<=time.time()-plan['created_at']<=900):raise ValueError('fresh unchanged publication approval required')
        journal=output/'publication-transaction.json'
        if journal.exists() or journal.is_symlink():raise ValueError('previous publication requires review, not blind replay')
        record={'status':'RUNNING','cluster_uuid':before['cluster_uuid'],'stage':'enroll',
                'plan_sha256':args.plan_sha256,'guests_restarted':False};write(journal,record)
        deadline=time.monotonic()+args.timeout
        try:
            while True:
                if deadline-time.monotonic()<=2:raise TimeoutError('gateway deadline reached')
                result=host.run('enroll',args.request,before['cluster_uuid'],min(180,deadline-time.monotonic()))
                if result['status']!='WAITING_GATEWAY':break
                # WAITING_GATEWAY makes no enrollment changes. Never retry a
                # failed or ambiguously acknowledged mutating helper call.
                if host.inspect(args.request)!=before:raise ValueError('permit/policy changed while waiting')
                print(json.dumps(result),flush=True)
                time.sleep(min(5,max(0,deadline-time.monotonic())))
            record['stage']='publish';write(journal,record)
            if not stable(before,host.inspect(args.request)):raise ValueError('publisher source/permit drift after enrollment')
            if deadline-time.monotonic()<=2:raise TimeoutError('publication deadline reached')
            result=host.run('publish',args.request,before['cluster_uuid'],deadline-time.monotonic())
            if not stable(before,host.inspect(args.request)):raise ValueError('publisher source/permit drift during publication')
            record.update(status='UI_READY',swarm_domain=before['swarm_domain'],stage='complete')
        except BaseException:
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record);return {k:record[k] for k in ('status','cluster_uuid','swarm_domain','guests_restarted')}
    finally:os.close(fd)


def main(backend,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan-spvm-publication','publish-spvm-ui'))
    for name in ('request','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--timeout',type=int,default=900);parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        if os.geteuid()!=0:raise ValueError('root-local publisher host required')
        print(json.dumps(execute(args,backend)));return 0
    except Exception:
        print('ERROR: publication incomplete; review private journal and current ingress state. No guest lifecycle action requested.',file=sys.stderr)
        return 2
