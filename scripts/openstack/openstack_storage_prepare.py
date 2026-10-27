"""Discover/prepare state directories on fresh compute hosts before Kolla init.

Outputs a new resolved deployment config. Never overwrite manual pool settings,
source config, filesystems, occupied hosts or previously attempted transactions.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

import yaml
from openstack_container_engine import digest,file_hash,write
from openstack_skyline_runtime import trusted
from openstack_workflow import context


def remote(module,host,backend,action,**data):
    root=Path(backend).parent
    modules={n:(root/(n+'.py')).read_text() for n in ('storage_discovery','storage_prepare_host')}
    source='MODULES='+repr(modules)+'\nACTION='+repr(action)+'\nDATA='+repr(data)+'\n'+'''
import sys,types,json,os
os.umask(0o077)
for name,body in MODULES.items():
 module=types.ModuleType(name);sys.modules[name]=module;exec(body,module.__dict__)
actor=sys.modules['storage_prepare_host']
if ACTION=='observe':value=actor.observe()
elif ACTION=='validate':actor.validate(DATA['pools'],DATA['before']);value={'validated':True}
elif ACTION=='prepare':value=actor.prepare(DATA['pools'],DATA['before'])
else:raise ValueError('unsupported storage action')
print(json.dumps(value))
'''
    p=subprocess.run(module.ssh_arguments(host)+['sudo -n /usr/bin/python3 -'],input=source,
                     capture_output=True,text=True,timeout=180)
    if p.returncode or len(p.stdout)>4194304:raise ValueError('storage host operation refused; remote diagnostics suppressed')
    return json.loads(p.stdout)


def execute(backend,args):
    value={'known_hosts':args.known_hosts}
    with context(backend,value) as module:
        from storage_discovery import automatic
        cfg=module.deployment_config(args.config)
        sources={str(p):file_hash(p) for p in [Path(__file__),Path(backend),
            Path(backend).parent/'storage_discovery.py',Path(backend).parent/'storage_prepare_host.py',
            Path(args.config),Path(args.known_hosts)]}
        output=Path(args.output);trusted(output.parent)
        if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical storage output required')
        if args.action=='plan-storage':
            output.mkdir(mode=0o700);resolved=yaml.safe_load(Path(args.config).read_text());hosts={}
            for name,h in cfg['hosts'].items():
                if 'compute' not in h['roles']:continue
                before=remote(module,h,backend,'observe')
                if before['blockers']:raise ValueError('fresh compute must be empty and not enrolled; use host pool CLI/GUI for existing hosts')
                pools=automatic(before,**h.get('storage_discovery',{})) if h['storage_pools']=='auto' else [dict(p) for p in h['storage_pools']]
                fs={v['mount']:v for v in before['filesystems'] if v['eligible']}
                for p in pools:
                    if 'budget_gib' not in p:
                        if p['mount'] not in fs:raise ValueError('manual pool mount unavailable')
                        p['budget_gib']=fs[p['mount']]['available_gib']-p['reserve_gib']
                remote(module,h,backend,'validate',pools=pools,before=before)
                resolved['hosts'][name]['storage_pools']=pools;hosts[name]={'before':before,'pools':pools}
            if not hosts:raise ValueError('compute hosts required for storage preparation')
            candidate=output/'deployment.resolved.yaml';candidate.write_text(yaml.safe_dump(resolved,sort_keys=False))
            module.deployment_config(candidate)
            plan={'schema_version':1,'source_sha256':sources,'resolved_sha256':file_hash(candidate),'hosts':hosts}
            plan['plan_sha256']=digest(plan);write(output/'storage-plan.json',plan)
            return {'status':'STORAGE_DISCOVERED_NOT_PREPARED','plan_sha256':plan['plan_sha256'],
                'resolved_config':str(candidate),'hosts':{n:p['pools'] for n,p in hosts.items()}}
        trusted(output)
        plan=json.loads((output/'storage-plan.json').read_text());candidate=output/'deployment.resolved.yaml'
        if (plan['source_sha256']!=sources or plan['resolved_sha256']!=file_hash(candidate)
                or plan['plan_sha256']!=args.plan_sha256
                or digest({k:v for k,v in plan.items() if k!='plan_sha256'})!=args.plan_sha256):
            raise ValueError('unchanged approved storage plan required')
        journal=output/'storage-transaction.json'
        if journal.exists():raise ValueError('storage preparation was attempted; inspect journal, never replay')
        # Validate EVERY host before the first directory mutation.
        for name,h in plan['hosts'].items():
            before=remote(module,cfg['hosts'][name],backend,'observe')
            if before!=h['before'] or before['blockers']:raise ValueError('host mount/identity/occupancy changed')
            remote(module,cfg['hosts'][name],backend,'validate',pools=h['pools'],before=before)
        state={'status':'PREPARING','completed':{}};write(journal,state)
        try:
            for name,h in plan['hosts'].items():
                state['completed'][name]=remote(module,cfg['hosts'][name],backend,'prepare',pools=h['pools'],before=h['before'])
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
    p.add_argument('--plan-sha256');args=p.parse_args(argv)
    if os.geteuid()!=0:raise ValueError('root storage coordination required')
    print(json.dumps(execute(backend,args)));return 0
