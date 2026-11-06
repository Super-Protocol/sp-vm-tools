"""Explicit continuation ONLY after the reviewed test-image prechecks refusal.

Not a general resume, migration, adoption or failed-deploy replay. The original
transaction and failed child journal stay intact. A new transaction owns only
Kolla and platform completion and requires fresh protected-host observations.
"""
import json
import os
from pathlib import Path
import stat
from copy import deepcopy

from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError


def private(path):
    path=Path(path)
    if path.resolve()!=path or path.is_symlink():
        raise InstallerError('Continuation requires canonical regular private records.')
    value=path.stat()
    if not stat.S_ISREG(value.st_mode) or value.st_uid!=0 or value.st_mode&0o077 or value.st_size>16*1024*1024:
        raise InstallerError('Continuation record is not a bounded root-private file: '+str(path))
    return path


def document(path):
    return json.loads(private(path).read_bytes())


def protected_equal(before,after):
    """Allow only PID/start-time refresh of an otherwise unchanged active libvirt.

    observe/hold_libvirt have independently required zero domains/direct guests.
    Every file/container/network observation and other service stays exact.
    The new child workflow still protects its complete fresh PID baseline.
    """
    left,right=deepcopy(before),deepcopy(after)
    if set(left)!=set(right):return False
    for name in left:
        for service in ('libvirtd.service','virtqemud.service'):
            a=left[name].get('services',{}).get(service)
            b=right[name].get('services',{}).get(service)
            if a==b:continue
            if not isinstance(a,str) or not isinstance(b,str):return False
            x=dict(line.split('=',1) for line in a.splitlines())
            y=dict(line.split('=',1) for line in b.splitlines())
            required={'LoadState':'loaded','ActiveState':'active','SubState':'running','NRestarts':'0'}
            if any(x.get(k)!=v or y.get(k)!=v for k,v in required.items()):return False
            for key in ('MainPID','ExecMainStartTimestampMonotonic','ActiveEnterTimestampMonotonic'):
                x.pop(key,None);y.pop(key,None)
            if x!=y:return False
            left[name]['services'][service]=x;right[name]['services'][service]=y
    return left==right


def reviewed(origin):
    from openstack_install import PHASES, RESULTS, protected_parent
    origin=Path(origin)
    protected_parent(origin)
    if origin.stat().st_mode&0o077:
        raise InstallerError('Previous transaction must be root-private.')
    if (origin/'continued-by.json').exists():
        raise InstallerError('This transaction already has an explicit continuation. Inspect its journal; do not replay it.')
    parent=document(origin/'installation.json')
    completed=list(PHASES[:PHASES.index('kolla')])
    if (parent.get('schema_version')!=1 or parent.get('status')!='NEEDS_REVIEW'
            or parent.get('active_phase')!='kolla' or parent.get('completed')!=completed
            or parent.get('guests_created') is not False):
        raise InstallerError('Continuation is limited to a fully prepared fresh installation stopped at Kolla prechecks.')
    core=document(origin/'core/install-transaction.json')
    plan=document(origin/'core/install-plan.json')
    if (core.get('status')!='NEEDS_REVIEW' or core.get('active_phase')!='prechecks'
            or core.get('completed')!=[] or core.get('guests_launched') is not False
            or core.get('plan_sha256')!=plan.get('plan_sha256')
            or plan.get('plan_sha256')!=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
            or core.get('intent_sha256')!=digest(plan.get('intent'))
            or plan.get('intent',{}).get('use_testing_images') is not False):
        raise InstallerError('Failed/interrupted deploy or unknown Kolla history cannot be continued by this command.')
    for phase in ('deploy','post-deploy'):
        if (origin/'core'/('private-'+phase+'.log')).exists():
            raise InstallerError('Kolla deployment may have started; manual review is required.')
    log=private(origin/'core/private-prechecks.log').read_bytes()
    if b'please use --use-test-images CLI argument' not in log:
        raise InstallerError('Only the reviewed upstream test-image prechecks refusal is eligible for continuation.')
    for phase in completed:
        if document(origin/(phase+'-result.json')).get('status')!=RESULTS[phase]:
            raise InstallerError('Previous preparation did not confirm every required phase.')
    return parent,core,plan,completed


def prepare(i,tx):
    from openstack_install import protected_parent
    from openstack_compute_image import read
    import openstack_kolla_workflow as kolla
    import openstack_kolla_workspace as workspace
    from openstack_install_artifacts import console_builder
    from openstack_install_phases import BASES
    if not i.args.use_testing_images:
        raise InstallerError('Continuation requires explicit --use-test-images acknowledgement; --yes is not sufficient.')
    origin=Path(i.args.continue_from)
    protected_parent(origin)
    if i.output==origin or origin.is_relative_to(i.output):
        raise InstallerError('Continuation evidence must be separate from the original transaction.')
    parent,core,old_core_plan,completed=reviewed(origin)
    tools=Path(__file__).resolve().parent
    # Explicitly approved updated coordinator code is allowed. Configuration,
    # SSH trust/keys, bundle and environment receipt must still match exactly.
    for filename,wanted in parent['inputs'].items():
        path=Path(filename)
        if path.parent==tools and path.suffix=='.py':
            continue
        if file_hash(path)!=wanted or i.inputs.get(filename)!=wanted:
            raise InstallerError('Original config, SSH trust/identity, driver or environment changed; continuation refused.')
    i.verify_environment()
    console_builder(i,['--check-base-image',i.options.get('console_base_image',BASES['console'])])
    i.config=str(private(origin/'deployment.artifacts.yaml'))
    i.refresh()
    i.workspace=str(origin/'kolla-bound')
    workspace.check(i.workspace)
    i.requests=document(origin/'requests.json')['hosts']
    controls=[n for n,h in i.cfg['hosts'].items() if 'control' in h['roles']]
    if len(controls)!=1:raise InstallerError('Single-controller prepared profile required.')
    i.controller=controls[0]
    i.run_id=i.cfg['deployment_id']+'-'+digest(str(origin))[:12]
    i.controller_root='/opt/spvm-openstack/'+i.run_id
    machines=document(origin/'plan.json')['hosts']
    if set(machines)!=set(i.cfg['hosts']) or set(i.requests)!={n for n,h in i.cfg['hosts'].items() if 'compute' in h['roles']}:
        raise InstallerError('Prepared host inventory changed.')
    for name,audit in i.audits.items():
        if audit['host_identity_sha256']!=machines[name]['machine_sha256']:
            raise InstallerError('Prepared host identity changed: '+name)
    for name,filename in i.requests.items():
        if Path(filename)!=origin/('helper-'+name+'.json'):
            raise InstallerError('Unexpected prepared helper request path.')
        private(filename)
    args=i.phase_args(workspace=i.workspace)
    cfg,receipt,source,code,intent=kolla.fingerprint(args,i.path,i.backend)
    # The flag and this reviewed workflow's diagnostics change. All underlying
    # prepared workspace, image, helper, config and host requests must match.
    old_intent=dict(old_core_plan['intent']); new_intent=dict(intent)
    for key in ('use_testing_images','workflow_sha256'):
        old_intent.pop(key,None);new_intent.pop(key,None)
    if new_intent!=old_intent:
        raise InstallerError('Prepared Kolla input drift; refusing to continue.')
    with kolla.hold_libvirt(args,i.backend,i.cfg):
        observed=kolla.guard(args,i.backend,i.cfg,intent,old_core_plan['hosts'])
    write(i.output/'continuation-observations.json',observed)
    if not protected_equal(core['protected_hosts'],observed):
        changed=[name for name in observed if observed[name]!=core['protected_hosts'].get(name)]
        raise InstallerError('Protected host state changed since failed prechecks: '+', '.join(changed)+'. Manual review is required.')
    # Bind all inherited local inputs. No journal/status is rewritten in origin.
    inherited=[p for p in origin.iterdir() if p.is_file() and p.suffix in ('.json','.yaml')]
    inherited += [origin/'core'/name for name in ('install-plan.json','install-transaction.json','private-prechecks.log')]
    for path in inherited:
        private(path)
        i.inputs[str(path)]=file_hash(path)
        tx.inputs[str(path)]=i.inputs[str(path)]
    tx.record['completed']=completed
    tx.record['continued_from']=str(origin)
    tx.record['use_testing_images']=True
    previous_plan=document(origin/'plan.json')
    return {'schema_version':1,'deployment_id':i.cfg['deployment_id'],'hosts':machines,
        'storage':previous_plan.get('storage',{}),'blockers':[], 'continued_from':str(origin),
        'previous_journal_sha256':file_hash(origin/'installation.json'),
        'remaining_phases':['kolla','platform'],'use_testing_images':True,
        'management_mode':i.cfg['network'].get('management_mode')}


def claim(i,tx,plan):
    """One explicit continuation, reserved BEFORE the first deployment action."""
    origin=Path(plan['continued_from'])
    reviewed(origin)
    tx.unchanged()
    marker=origin/'continued-by.json'
    with marker.open('x') as stream:
        os.fchmod(stream.fileno(),0o600)
        json.dump({'output':str(i.output),'plan_sha256':digest(plan),
                   'previous_journal_sha256':plan['previous_journal_sha256']},stream)
        stream.flush();os.fsync(stream.fileno())
    directory_fd=os.open(origin,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(directory_fd)
    finally:os.close(directory_fd)
