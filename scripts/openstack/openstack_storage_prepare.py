"""Discover/prepare state directories on fresh compute hosts before Kolla init.

Outputs a new resolved deployment config. Never overwrite manual pool settings,
source config, filesystems, occupied hosts or previously attempted transactions.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid

import yaml
from openstack_container_engine import digest,file_hash,write
from openstack_skyline_runtime import trusted
from openstack_workflow import context
from openstack_errors import InstallerError

STORAGE_ERRORS={
    'STATE_NOT_EMPTY':'state directory is not empty. To replace it, rerun plan-storage with --replace-existing-state and a NEW --output directory, then review and approve the plan. Old data will be renamed to a sibling backup, not deleted.',
    'STATE_NOT_DIRECTORY':'the selected state path exists but is not a directory. Select a different path.',
    'UNSAFE_EXISTING_STATE':'existing state contains a symlink, mounted filesystem or unsupported file type; automatic replacement is refused.',
    'EXISTING_STATE_TOO_LARGE':'existing state has too many entries for bounded replacement inspection; select another state directory.',
    'STATE_REPLACEMENT_CHANGED':'existing state or backup destination changed after planning. Preserve the transaction and investigate before making a new plan.'}

VALIDATION_ERRORS={
    'fresh storage observation unavailable':'cannot inspect host storage. Verify virsh/libvirt, findmnt, pgrep and noninteractive sudo.',
    'selected mount unavailable or ineligible':'selected filesystem is absent or ineligible. Review mounted local ext4/XFS filesystems and the storage plan.',
    'explicit root permission required':'using the root filesystem requires explicit root opt-in in the storage configuration.',
    'overlapping state directories':'selected state directories overlap; choose separate directories.',
    'one pool per filesystem':'multiple pools reference one filesystem; select only one to avoid counting its capacity twice.',
    'positive limits required':'storage budget and reserve must be positive integer GiB values.',
    'insufficient physical space':'storage budget plus host reserve exceeds available filesystem space. Reduce the budget or choose another filesystem.',
    'protected root-owned state ancestors required':'state path or an ancestor is a symlink, not root-owned or writable by group/others. Correct ownership/permissions or choose a safe directory.',
    'state directory crosses a mount boundary':'state directory resolves to a different filesystem than the selected mount.',
    'state directory must be below an eligible filesystem, outside protected system trees':'state path must be a subdirectory of the selected eligible filesystem, outside protected system directories.',
    'fresh storage observation changed or host occupied':'host state changed or guests/enrollment appeared. No automatic retry: inspect the transaction and make a new plan only after review.'}


def remote(module,host,backend,action,**data):
    root=Path(backend).parent
    modules={n:(root/(n+'.py')).read_text() for n in ('storage_discovery','storage_prepare_host')}
    source='MODULES='+repr(modules)+'\nACTION='+repr(action)+'\nDATA='+repr(data)+'\nSAFE_ERRORS='+repr(set(VALIDATION_ERRORS))+'\n'+'''
import sys,types,json,os
os.umask(0o077)
for name,body in MODULES.items():
 module=types.ModuleType(name);sys.modules[name]=module;exec(body,module.__dict__)
actor=sys.modules['storage_prepare_host']
try:
 if ACTION=='observe':value=actor.observe()
 elif ACTION=='validate':value={'existing':actor.validate(DATA['pools'],DATA['before'],**DATA.get('options',{}))}
 elif ACTION=='prepare':value=actor.prepare(DATA['pools'],DATA['before'],replacements=DATA['replacements'])
 else:raise ValueError('unsupported storage action')
 print(json.dumps({'result':value}))
except actor.StorageError as error:
 print(json.dumps({'error':{'code':error.code,'path':error.path}}))
except ValueError as error:
 print(json.dumps({'error':{'code':'VALIDATION_ERROR','reason':str(error) if str(error) in SAFE_ERRORS else None}}))
except Exception:
 print(json.dumps({'error':{'code':'STORAGE_VALIDATION_FAILED'}}))
'''
    label=host.get('inventory_name',host.get('address','configured host'))
    try:
        p=subprocess.run(module.ssh_arguments(host)+['sudo -n /usr/bin/python3 -'],input=source,
                         capture_output=True,text=True,timeout=180)
    except subprocess.TimeoutExpired:
        raise InstallerError(f'{label}: storage {action} timed out. Check SSH, sudo and host responsiveness.') from None
    if p.returncode or len(p.stdout)>4194304:
        raise InstallerError(f'{label}: storage {action} could not run. Check SSH authentication/known_hosts, noninteractive sudo, Python and required storage tools. Remote stderr is suppressed.')
    try:reply=json.loads(p.stdout)
    except ValueError:raise InstallerError(f'{label}: invalid storage response; check SSH startup output and matching components.') from None
    if 'error' in reply:
        error=reply['error'];code=error.get('code');path=error.get('path')
        if code in STORAGE_ERRORS and path in {p['path'] for p in data.get('pools',[])}:
            raise InstallerError(f'{label}: {path!r}: '+STORAGE_ERRORS[code])
        if code=='VALIDATION_ERROR' and error.get('reason') in VALIDATION_ERRORS:
            raise InstallerError(f'{label}: '+VALIDATION_ERRORS[error['reason']])
        raise InstallerError(f'{label}: storage {action} validation failed. Check filesystem eligibility, permissions, free space, reserve/budget and empty-host prerequisites.')
    return reply['result']


def execute(backend,args):
    manifest=Path(backend).parent.parent/'manifest.json'
    if not manifest.exists():manifest=Path(backend).parent.parent/'nova-spvm/manifest.json'
    if 'storage-replacement-diagnostics-v1' not in json.loads(manifest.read_text()).get('capabilities',[]):
        raise InstallerError('Selected driver lacks storage-replacement-diagnostics-v1. Build/select the updated driver bundle; do not modify a verified archive.')
    value={'known_hosts':args.known_hosts}
    with context(backend,value) as module:
        from storage_discovery import automatic
        cfg=module.deployment_config(args.config)
        selected={n for n,h in cfg['hosts'].items() if 'compute' in h['roles']}
        host=getattr(args,'host',None)
        if host is not None:
            if host not in selected:raise ValueError('select one configured compute host')
            selected={host}
        sources={str(p):file_hash(p) for p in [Path(__file__),Path(backend),
            Path(backend).parent/'storage_discovery.py',Path(backend).parent/'storage_prepare_host.py',
            Path(args.config),Path(args.known_hosts)]}
        output=Path(args.output);trusted(output.parent)
        if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical storage output required')
        if args.action=='plan-storage':
            if output.exists():raise InstallerError(f'Storage plan directory {str(output)!r} already exists. Preserve it and choose a new --output directory.')
            output.mkdir(mode=0o700);resolved=yaml.safe_load(Path(args.config).read_text());hosts={};replacement_id=uuid.uuid4().hex
            for name,h in cfg['hosts'].items():
                if name not in selected:continue
                before=remote(module,h,backend,'observe')
                if before['blockers']:raise InstallerError(f'{name}: host has guests or is already enrolled. Storage preparation requires an empty, unenrolled compute; use host pool management for an existing cloud.')
                pools=automatic(before,**h.get('storage_discovery',{})) if h['storage_pools']=='auto' else [dict(p) for p in h['storage_pools']]
                fs={v['mount']:v for v in before['filesystems'] if v['eligible']}
                for p in pools:
                    if 'budget_gib' not in p:
                        if p['mount'] not in fs:raise ValueError('manual pool mount unavailable')
                        p['budget_gib']=fs[p['mount']]['available_gib']-p['reserve_gib']
                checked=remote(module,h,backend,'validate',pools=pools,before=before,
                    options={'replace_existing':bool(getattr(args,'replace_existing_state',False))})
                replacements={path:{'snapshot':snapshot,'backup':path+'.spvm-backup-'+replacement_id}
                              for path,snapshot in checked['existing'].items()}
                remote(module,h,backend,'validate',pools=pools,before=before,options={'replacements':replacements})
                resolved['hosts'][name]['storage_pools']=pools;hosts[name]={'before':before,'pools':pools,'replacements':replacements}
            if not hosts:raise ValueError('compute hosts required for storage preparation')
            candidate=output/'deployment.resolved.yaml';candidate.write_text(yaml.safe_dump(resolved,sort_keys=False))
            module.deployment_config(candidate)
            plan={'schema_version':1,'source_sha256':sources,'resolved_sha256':file_hash(candidate),
                'selected_hosts':sorted(selected),'hosts':hosts}
            plan['plan_sha256']=digest(plan);write(output/'storage-plan.json',plan)
            return {'status':'STORAGE_DISCOVERED_NOT_PREPARED','plan_sha256':plan['plan_sha256'],
                'resolved_config':str(candidate),'hosts':{n:p['pools'] for n,p in hosts.items()},
                'replacements':{n:p['replacements'] for n,p in hosts.items() if p['replacements']}}
        trusted(output)
        plan=json.loads((output/'storage-plan.json').read_text());candidate=output/'deployment.resolved.yaml'
        if (plan.get('selected_hosts')!=sorted(selected)
                or plan['source_sha256']!=sources or plan['resolved_sha256']!=file_hash(candidate)
                or plan['plan_sha256']!=args.plan_sha256
                or digest({k:v for k,v in plan.items() if k!='plan_sha256'})!=args.plan_sha256):
            raise InstallerError('Storage plan approval does not match the current config, sources, selected hosts or resolved YAML. Preserve the old plan and create/review a new plan; do not edit its hashes.')
        journal=output/'storage-transaction.json'
        if journal.exists():raise InstallerError(f'Storage preparation was already attempted: inspect {str(journal)!r} and any recorded backups; never replay the transaction.')
        # Validate EVERY host before the first directory mutation.
        for name,h in plan['hosts'].items():
            before=remote(module,cfg['hosts'][name],backend,'observe')
            if before!=h['before'] or before['blockers']:
                raise InstallerError(f'{name}: mount, identity, free-space or occupancy observation changed since planning. No preparation was started; inspect the host and create a new plan.')
            remote(module,cfg['hosts'][name],backend,'validate',pools=h['pools'],before=before,options={'replacements':h['replacements']})
        state={'status':'PREPARING','completed':{}};write(journal,state)
        try:
            for name,h in plan['hosts'].items():
                state['completed'][name]=remote(module,cfg['hosts'][name],backend,'prepare',pools=h['pools'],before=h['before'],replacements=h['replacements'])
                write(journal,state)
            state['status']='COMPLETE';write(journal,state)
        except BaseException:
            state['status']='REVIEW_REQUIRED';write(journal,state);raise
        return {'status':'STORAGE_PREPARED','resolved_config':str(candidate),'hosts':list(plan['hosts']),
                'data_deleted':False,'disks_formatted':False}


def main(backend,argv):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan-storage','prepare-storage'])
    for name in ('config','known-hosts','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--host',help='Prepare only this fresh compute; do not inspect enrolled peers')
    p.add_argument('--replace-existing-state',action='store_true',help='Plan explicit replacement of nonempty state directories by renaming them to sibling backups; no deletion')
    p.add_argument('--plan-sha256');args=p.parse_args(argv)
    if os.geteuid()!=0:raise InstallerError('Storage coordination requires root on the management host. Rerun this command with sudo and the explicit installer Python path.')
    if args.action=='prepare-storage' and args.replace_existing_state:
        raise InstallerError('--replace-existing-state belongs to plan-storage only. Preparation uses the reviewed plan and its --plan-sha256 approval.')
    print(json.dumps(execute(backend,args)));return 0
