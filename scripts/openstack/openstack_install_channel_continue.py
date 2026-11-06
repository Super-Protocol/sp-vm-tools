"""Explicit completion after a lost final compute ACK, never activation replay.

Both the local durable activation receipts and live acceptance must succeed.
Only the exact old controller reader may be terminated, after approval, using
an identity-bound pidfd. No credentials/ACKs are injected into old processes.
"""
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import signal
import subprocess
import sys
from types import SimpleNamespace as R

from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError
from openstack_install_continue import document, private
import openstack_install_compute_continue as compute
import openstack_install_platform_continue as platform_resume


def ready_receipt(record, plan, host):
    steps=['quarantine','stop','enroll','start','accept','enable']
    if (record.get('schema_version')!=1 or plan.get('schema_version')!=1
            or record.get('host')!=host or plan.get('host')!=host
            or record.get('mode')!='ACTIVATE' or plan.get('mode')!='ACTIVATE'
            or plan.get('kind')!='initial-runtime-compute-v1'
            or record.get('status')!='COMPUTE_READY' or record.get('done')!=steps
            or record.get('mutations')!=[s for s in steps if s!='accept']
            or record.get('inflight') is not None or record.get('errors')!=[]
            or record.get('guests_restarted') is not False
            or plan.get('plan_sha256')!=compute.activation_digest({k:v for k,v in plan.items() if k!='plan_sha256'})
            or record.get('plan_sha256')!=plan.get('plan_sha256')):
        raise InstallerError('Every compute needs an exact successful activation receipt; no activation will be replayed.')


def reviewed(origin):
    from openstack_install import PHASES, protected_parent
    origin=Path(origin);protected_parent(origin)
    parent=document(origin/'installation.json')
    if (origin.stat().st_mode&0o077 or (origin/'continued-by.json').exists()
            or parent.get('schema_version')!=1 or parent.get('status')!='NEEDS_REVIEW'
            or parent.get('active_phase')!='platform' or parent.get('completed')!=list(PHASES[:-1])
            or parent.get('guests_created') is not False):
        raise InstallerError('Unclaimed failed controller completion required.')
    chain=[origin]
    while True:
        current=document(chain[-1]/'installation.json')
        previous=Path(current.get('continued_from',''));protected_parent(previous)
        if previous in chain or len(chain)>16:raise InstallerError('Invalid completion lineage.')
        marker=document(previous/'continued-by.json');plan=document(chain[-1]/'plan.json')
        if (marker.get('output')!=str(chain[-1]) or marker.get('plan_sha256')!=digest(plan)
                or set(marker)-{'output','plan_sha256','previous_journal_sha256'}
                or ('previous_journal_sha256' in marker and marker['previous_journal_sha256']!=file_hash(previous/'installation.json'))
                or current.get('plan_sha256')!=digest(plan)):
            raise InstallerError('Completion lineage or plan binding differs.')
        chain.append(previous)
        if document(previous/'installation.json').get('completed')==list(PHASES[:-2]):break
    if len(chain)!=4:raise InstallerError('Only the reviewed compute-continuation channel boundary is supported.')
    prior,platform_origin,base=chain[1:]
    core=document(platform_origin/'core/install-transaction.json')
    if (core.get('status')!='KOLLA_DEPLOYED_NOT_SPVM_ENROLLED'
            or core.get('completed')!=['prechecks','deploy','post-deploy'] or core.get('guests_launched') is not False):
        raise InstallerError('Inherited Kolla deployment is not confirmed.')
    requests=document(base/'requests.json')['hosts'];names=sorted(requests)
    if len(names)<2:raise InstallerError('Explicit multi-host completion evidence required.')
    if {p.name for p in origin.glob('activate-*')}!={'activate-'+n for n in names[1:]}:
        raise InstallerError('Unexpected or incomplete compute activation evidence.')
    plans={};files=[]
    for n in names:
        pp=origin/('compute-plan.json' if n==names[0] else 'activate-'+n+'/plan.json')
        rp=origin/('reconciled-compute/transaction.json' if n==names[0] else 'activate-'+n+'/transaction/transaction.json')
        plan,record=document(pp),document(rp);ready_receipt(record,plan,n)
        if n==names[0] and document(origin/'reconciled-compute.json')!=record:
            raise InstallerError('Reconciled compute summary and durable receipt differ.')
        if plan.get('enrollment')!=document(prior/'runtime-requests'/(n+'.json')):
            raise InstallerError('Activation runtime differs from the approved enrollment.')
        plans[n]=plan;files.extend((pp,rp,prior/'runtime-requests'/(n+'.json')))
    return R(parent=parent,base=base,prior=prior,platform_origin=platform_origin,
             requests=requests,plans=plans,chain=chain,files=files)


def waiting_readers(root, proc=Path('/proc')):
    """Read only process identity; never expose command strings or secrets."""
    root=str(root)
    code='import sys;sys.path.insert(0,'+repr(root+'/tools')+');from openstack_install_compute_continue import serve;serve('+repr(root)+')'
    found=[]
    for entry in proc.iterdir():
        if not entry.name.isdecimal():continue
        try:
            args=(entry/'cmdline').read_bytes().split(b'\0')[:-1]
            if not args or args[-1]!=code.encode():continue
            # sudo keeps the same final -c argument while waiting for Python.
            # It is not the reader, and must never be selected or signalled.
            if Path(os.fsdecode(args[0])).name=='sudo':continue
            if len(args)!=6 or args[1:5]!=[b'-I',b'-B',b'-u',b'-c']:
                raise ValueError('unknown controller reader invocation')
            status=dict(line.split(':',1) for line in (entry/'status').read_text().splitlines() if ':' in line)
            state=(entry/'stat').read_text().rsplit(')',1)[1].split()
            syscall=(entry/'syscall').read_text().split()
            if (status['Uid'].split()!=['0']*4 or status['Threads'].strip()!='1'
                    or (entry/'wchan').read_text().strip()!='anon_pipe_read'
                    or not os.readlink(entry/'fd/0').startswith('pipe:[')
                    or syscall[:2]!=['0','0x0']):
                raise ValueError('controller reader is not exclusively waiting for stdin; inspect manually')
            found.append({'pid':int(entry.name),'start_ticks':state[19],
                          'command_sha256':hashlib.sha256((entry/'cmdline').read_bytes()).hexdigest()})
        except (FileNotFoundError,ProcessLookupError):continue
    if len(found)>1:raise ValueError('multiple controller readers; no process may be adopted')
    return found


def stop_reader(root, expected):
    """Only an approved, still-identical blocked reader; no process-group kill."""
    if waiting_readers(root)!=expected:raise ValueError('controller reader identity changed after approval')
    if not expected:return
    fd=os.pidfd_open(expected[0]['pid'])
    try:
        if waiting_readers(root)!=expected:raise ValueError('controller reader changed before termination')
        signal.pidfd_send_signal(fd,signal.SIGTERM)
        if not select.select([fd],[],[],30)[0]:raise ValueError('controller reader did not exit; no forced kill')
    finally:os.close(fd)


def native_review(c, source_root, *, claimed=False):
    old=Path(c['old_root']);previous=Path(c['source_root'])
    if (old.parent!=Path('/opt/spvm-openstack') or previous.parent!=old.parent
            or old.resolve()!=old or previous.resolve()!=previous):
        raise ValueError('canonical reviewed controller roots required')
    original=document(previous/'data/compute-continuation.json')
    if original!=c['original']:raise ValueError('original controller continuation changed')
    review=platform_resume.probe(old,source_root,activation=original['activation'],activated=True,
        channel={'source_root':str(previous),'claimed':claimed})
    if any(review['checkpoint']['files']['runtime-requests/'+n+'.json']!=h
           for n,h in original['activation']['requests'].items()):
        raise ValueError('controller runtime requests changed')
    # These inputs were verified before the interrupted channel was opened.
    if any(file_hash(Path(n))!=h for n,h in original['review']['files'].items()):
        raise ValueError('original reviewed controller inputs changed')
    review['channel_process']=waiting_readers(previous)
    return review


def remote_review(i):
    modules=('openstack_platform_checkpoint','openstack_platform','openstack_install_platform_continue',
             'openstack_install_compute_continue','openstack_install_channel_continue')
    code='import sys,types,json\nsys.path.insert(0,'+repr(i.channel_context['source_root']+'/tools')+')\n'
    for name in modules:
        body=Path(__file__).with_name(name+'.py').read_text()
        code+='m=types.ModuleType('+repr(name)+');sys.modules['+repr(name)+']=m\n'
        code+='exec(compile('+repr(body)+','+repr(name)+',"exec"),m.__dict__)\n'
    code+='print(json.dumps(sys.modules["openstack_install_channel_continue"].native_review('+repr(i.channel_context)+','+repr(i.channel_context['source_root'])+')))\n'
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n '+shlex.quote(i.controller_root+'/sdk/bin/python')+' -I -B -']
    with (i.output/'private-channel-review.log').open('xb') as log:
        os.fchmod(log.fileno(),0o600)
        result=subprocess.run(argv,input=code.encode(),stdout=subprocess.PIPE,stderr=log,timeout=300)
    if result.returncode or len(result.stdout)>4*1024*1024:
        raise InstallerError('Read-only completion review failed; inspect '+str(i.output/'private-channel-review.log')+'. No installation was run.')
    return json.loads(result.stdout)


def verify_computes(i):
    from initial_compute import Backend
    for name,plan in i.channel_state.plans.items():
        runner=Backend(i.cfg,i.controller,i.controller_root+'/sdk/bin/python','/etc/spvm/auth/admin-openrc.sh',
                       i.output/('channel-observations-'+name),enrollment=plan['enrollment'])
        # accept is GET/probe only. Do not invoke execute/guard/enable/stop/start.
        runner.accept(plan)


def prepare(i, tx):
    import yaml
    from openstack_install import PHASES
    from openstack_install_phases import BASES
    import openstack_kolla_workspace as workspace
    state=reviewed(i.args.continue_channel_from);i.channel_state=state
    if not i.args.use_testing_images:raise InstallerError('Explicit --use-test-images remains required.')
    raw=yaml.safe_load(private(state.base/'deployment.input.yaml').read_bytes());current=json.loads(json.dumps(i.raw))
    raw['driver'].pop('directory');current['driver'].pop('directory')
    if raw!=current:raise InstallerError('Infrastructure configuration changed; no channel continuation allowed.')
    manifest=json.loads((i.path.parent.parent/'manifest.json').read_bytes())
    expected={Path(n).name:h for n,h in manifest['source_sha256'].items()
              if Path(n).parent==Path('deployment') and n.endswith('.py')}
    if any(p.get('source_sha256')!=expected for p in state.plans.values()):
        raise InstallerError('Use the exact verified driver used for successful compute activation.')
    tools=Path(__file__).parent.resolve()
    for filename,wanted in state.parent['inputs'].items():
        p=Path(filename)
        if p==Path(i.args.config) or p.parent==tools and p.suffix=='.py':continue
        if file_hash(p)!=wanted:raise InstallerError('Previously approved input changed: '+str(p))
        tx.inputs[filename]=i.inputs[filename]=wanted
    i.verify_environment();i.config=str(private(state.base/'deployment.artifacts.yaml'));i.refresh()
    i.workspace=str(state.base/'kolla-bound');workspace.check(i.workspace);i.requests=state.requests
    binding=document(Path(i.workspace)/'compute-binding.json')
    if (str(i.guest)!=document(state.base/'plan.json')['guest_cidr']
            or i.region!=binding['bootstrap_runtime']['region_name']
            or any(document(p)['allow_debug']!=i.options.get('allow_debug',False) for p in i.requests.values())
            or any(i.options.get(k+'_base_image',BASES[k])!=document(state.base/'artifacts-result.json')['bases'][k] for k in ('compute','console'))):
        raise InstallerError('Prepared region/guest/debug/base-image policy changed.')
    hosts=document(state.base/'plan.json')['hosts']
    if set(hosts)!=set(i.cfg['hosts']) or any(a['host_identity_sha256']!=hosts[n]['machine_sha256'] for n,a in i.audits.items()):
        raise InstallerError('Prepared host identities changed.')
    controls=[n for n,h in i.cfg['hosts'].items() if 'control' in h['roles']]
    if len(controls)!=1:raise InstallerError('Single-controller reviewed profile required.')
    i.controller=controls[0]
    i.controller_root='/opt/spvm-openstack/'+i.cfg['deployment_id']+'-'+digest(str(state.base))[:12]
    previous='/opt/spvm-openstack/'+i.cfg['deployment_id']+'-compute-'+digest(str(Path(i.args.continue_channel_from)))[:12]
    original={'old_root':i.controller_root,'activation':{
        'previous_root':'/opt/spvm-openstack/'+i.cfg['deployment_id']+'-platform-'+digest(str(state.prior))[:12],
        'host':sorted(i.requests)[0],'manifest':manifest,
        'requests':{n:file_hash(state.prior/'runtime-requests'/(n+'.json')) for n in i.requests}},
        'review':document(Path(i.args.continue_channel_from)/'plan.json')['controller_review'],
        'plan_sha256':state.plans[sorted(i.requests)[0]]['plan_sha256']}
    i.channel_context={'old_root':i.controller_root,'source_root':previous,'original':original}
    i.platform_target='/opt/spvm-openstack/'+i.cfg['deployment_id']+'-channel-'+digest(str(i.output))[:12]
    i.channel_review=remote_review(i);verify_computes(i)
    for parent in state.chain:
        for p in parent.iterdir():
            if p.is_file() and p.suffix in ('.json','.yaml'):
                tx.inputs[str(p)]=i.inputs[str(p)]=file_hash(private(p))
    for p in state.files:tx.inputs[str(p)]=i.inputs[str(p)]=file_hash(private(p))
    tx.record.update(completed=list(PHASES[:-1]),continued_from=str(i.args.continue_channel_from))
    return {'schema_version':1,'deployment_id':i.cfg['deployment_id'],'hosts':hosts,
            'storage':document(state.base/'plan.json').get('storage',{}),'blockers':[],
            'continued_from':str(i.args.continue_channel_from),'remaining_phases':['platform'],
            'compute_activation_replayed':False,'controller_review':i.channel_review,
            'management_mode':i.cfg['network'].get('management_mode'),'use_testing_images':True}


def claim(i, tx, plan):
    reviewed(i.args.continue_channel_from);tx.unchanged()
    marker=Path(i.args.continue_channel_from)/'continued-by.json'
    with marker.open('x') as stream:
        os.fchmod(stream.fileno(),0o600);json.dump({'output':str(i.output),'plan_sha256':digest(plan)},stream)
        stream.flush();os.fsync(stream.fileno())
    fd=os.open(marker.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def setup_controller(root, payload):
    c=payload['channel_continuation'];old=Path(c['context']['old_root'])
    if old.parent!=Path('/opt/spvm-openstack') or old.resolve()!=old:raise ValueError('invalid controller SDK root')
    code='import sys,json;sys.path.insert(0,'+repr(str(root/'tools'))+');from openstack_install_channel_continue import finalize_controller;print(json.dumps(finalize_controller('+repr(str(root))+',json.load(sys.stdin))))'
    result=subprocess.run([str(old/'sdk/bin/python'),'-I','-B','-c',code],input=json.dumps(c),capture_output=True,text=True,timeout=360)
    if result.returncode:
        with (root/'private-channel-prepare.log').open('x') as log:
            os.fchmod(log.fileno(),0o600);log.write(result.stderr)
        raise ValueError('controller channel preparation failed; inspect private-channel-prepare.log')
    return json.loads(result.stdout)


def finalize_controller(root, c):
    root=Path(root);context=c['context']
    if native_review(context,root)!=c['review']:raise ValueError('controller changed after approval')
    data=root/'data';data.mkdir(mode=0o700);write(data/'channel-continuation.json',c)
    marker=Path(context['source_root'])/'data/platform-install/continued-by.json'
    with marker.open('x') as stream:
        os.fchmod(stream.fileno(),0o600);json.dump({'controller':str(root),'review_sha256':digest(c['review'])},stream)
        stream.flush();os.fsync(stream.fileno())
    fd=os.open(marker.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    write(data/'reader-stop-intent.json',{'source_root':context['source_root'],'process':c['review']['channel_process']})
    stop_reader(context['source_root'],c['review']['channel_process'])
    write(data/'reader-stopped.json',{'status':'READER_STOPPED_NO_COMPUTE_REPLAY'})
    return {'status':'CONTROLLER_CHANNEL_CONTINUATION_PREPARED'}


def complete(i):
    from openstack_install_completion import BOOTSTRAP,snapshot,rpc_process,Operations
    verify_computes(i)
    payload={'root':i.platform_target,'machine':i.channel_review['machine'],'files':snapshot(i),
             'channel_continuation':{'context':i.channel_context,'review':i.channel_review}}
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n /usr/bin/python3 -I -B -u -c '+shlex.quote(BOOTSTRAP)]
    if rpc_process(i,argv,payload).get('status')!='CONTROLLER_CHANNEL_CONTINUATION_PREPARED':
        raise InstallerError('Controller channel continuation was not prepared.')
    operations=Operations(i);operations.project=i.channel_review['project']
    operations.enrollment=str(i.channel_state.prior/'enrollment/enrollment-plan.json')
    operations.requests={n:p['enrollment'] for n,p in i.channel_state.plans.items()}
    operations.activated=set(i.requests)
    code='import sys;sys.path.insert(0,'+repr(i.platform_target+'/tools')+');from openstack_install_channel_continue import serve;serve('+repr(i.platform_target)+')'
    argv=i.backend.ssh_arguments(i.cfg['hosts'][i.controller])+['sudo -n '+shlex.quote(i.controller_root+'/sdk/bin/python')+' -I -B -u -c '+shlex.quote(code)]
    result=rpc_process(i,argv,{},operations)
    if operations.installed!=set(i.requests) or not operations.audited:
        raise InstallerError('Remaining platform operations did not complete.')
    return result


def serve(directory):
    from openstack_install_phases import load_module
    import openstack_platform as platform
    root=Path(directory);data=root/'data';c=document(data/'channel-continuation.json')
    if json.loads(sys.stdin.buffer.readline(1024))!={}:raise ValueError('empty continuation ACK required')
    review=native_review(c['context'],root,claimed=True)
    previous=dict(c['review']);previous['channel_process']=[]
    if review!=previous:raise ValueError('controller changed after reader termination')
    path=root/'driver/deployment/installer.py'
    sys.path[:0]=[str(path.parent),str(root/'driver/nova-spvm')]
    backend=load_module(path,'installer');sys.modules['installer']=backend
    original=backend.deployment_config;backend.deployment_config=lambda filename,**kw:original(filename,transport=False)
    def forbidden(*a,**kw):raise ValueError('no operator SSH or keys on controller')
    backend.ssh_arguments=forbidden;backend.host_audits=forbidden
    sequence=0
    def operation(name,value):
        nonlocal sequence
        if name=='activate-compute':raise ValueError('compute activation replay is forbidden')
        print(json.dumps({'operation':name,'data':value,'sequence':sequence}),flush=True)
        if not select.select([sys.stdin.buffer],[],[],1800)[0]:raise ValueError('installation ACK timed out')
        line=sys.stdin.buffer.readline(64*1024*1024+1)
        if not line or len(line)>64*1024*1024:raise ValueError('invalid installation ACK')
        reply=json.loads(line)
        if set(reply)!={'sequence','result'} or reply['sequence']!=sequence:raise ValueError('invalid ACK sequence')
        sequence+=1;return reply['result']
    old=Path(c['context']['old_root'])
    value={'config':str(old/'data/deployment.yaml'),'workspace':str(old/'data'),'platform':str(old/'data/platform.json')}
    result=platform.post_install(value,path,backend,data/'platform-install',old/'data/core',operations=operation,
        verify_existing_auth=True,checkpoint=review['checkpoint'],reconciled_host=sorted(c['context']['original']['activation']['requests']))
    print(json.dumps({'result':result}),flush=True)
