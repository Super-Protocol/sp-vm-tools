"""Explicit, fresh-only activation of the administrator-request budget worker.

This does not change budgets during staging. Installation enables processing of
explicit Barbican requests only; it never performs VM lifecycle operations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

from openstack_container_engine import digest, file_hash, write
from openstack_skyline_runtime import trusted

UNITS=('spvm-host-budgets.service','spvm-host-budgets.timer')
SYSTEMD_DIRECTORY=Path('/etc/systemd/system')


def stage(backend,args):
    from openstack_workflow import context
    from openstack_host_budgets import policy
    with context(backend,{'known_hosts':args.known_hosts}):
        cfg=policy(args.policy)
    if cfg['known_hosts']!=args.known_hosts:raise ValueError('known-hosts binding differs')
    for key in ('config','openrc','known_hosts'):
        trusted(cfg[key])
    interpreter=Path(args.python)
    if not interpreter.is_absolute() or not interpreter.is_file():raise ValueError('existing worker Python required')
    trusted(interpreter.resolve())
    web_python=Path(args.web_python)
    if not web_python.is_absolute() or not web_python.is_file():raise ValueError('existing web Python required')
    trusted(web_python.resolve())
    probe=subprocess.run([str(web_python),'-c',
        'import json; from pathlib import Path; import openstack_spvm.web_nodes as w; '
        'import openstack_spvm.host_budgets; '
        'assert all(callable(getattr(w.NodeService,n,None)) for n in ("budgets","budget_submit","budget_status")); '
        'print(json.dumps(str(Path(w.__file__).parent)))'],capture_output=True,text=True,timeout=30)
    if probe.returncode:raise ValueError('web backend lacks budget support; deploy matching client before activation')
    web_modules=trusted(json.loads(probe.stdout))
    web=trusted(args.web_config)
    before=json.loads(web.read_text())
    if 'host_budget_policy' in before:raise ValueError('existing budget delegation requires manual review')
    if args.worker_user_id not in cfg['reader_user_ids']:
        raise ValueError('worker identity must be an explicit request reader')
    after={**before,'host_budget_policy':{'project_id':cfg['project_id'],
        'admin_user_ids':cfg['admin_user_ids'],'publisher_user_ids':[args.worker_user_id],
        'worker_user_ids':[args.worker_user_id]}}
    from nova_spvm.authority import user_ids
    user_ids([args.worker_user_id])
    # Validate full web configuration without creating its runtime state.
    # NodeService performs final validation on service restart.
    root=Path(args.output)
    if not root.is_absolute() or root.resolve()!=root:raise ValueError('canonical absolute activation output required')
    trusted(root.parent);root.mkdir(mode=0o700)
    runner=root/'worker.sh'
    command=[str(interpreter),str(Path(__file__).with_name('openstack_installer.py')),
        '--driver-dir',str(Path(backend).parent),'--','process-host-budgets','--policy',str(Path(args.policy)),
        '--expected-worker-user-id',args.worker_user_id]
    runner.write_text('#!/bin/bash\nset -euo pipefail\numask 077\nset -a\n. '+shlex.quote(cfg['openrc'])+
        '\nset +a\nexec '+shlex.join(command)+'\n');runner.chmod(0o700)
    service='''[Unit]
Description=Apply explicit administrator SPVM host budget requests
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
TimeoutStartSec=1800
ExecStart=/bin/bash '''+str(runner)+'''
'''
    timer='''[Unit]
Description=Inspect explicit SPVM host budget requests

[Timer]
OnActiveSec=60
OnUnitInactiveSec=30
Unit=spvm-host-budgets.service

[Install]
WantedBy=timers.target
'''
    # systemd directives must never interpret whitespace/specifiers in paths.
    if any(c.isspace() or c in '%\\' for c in str(runner)):raise ValueError('literal service path required')
    for name,body in zip(UNITS,(service,timer)):(root/name).write_text(body)
    (root/'web-config.json').write_text(json.dumps(after,indent=2)+'\n')
    inputs={str(p):file_hash(p) for p in
        [trusted(args.policy),trusted(cfg['config']),trusted(cfg['known_hosts']),web,
         *[trusted(p) for p in Path(__file__).parent.glob('*.py')],
         *[trusted(p) for p in Path(backend).parent.glob('*.py')],
         *[trusted(p) for p in web_modules.glob('*.py')],
         *[trusted(p) for p in (Path(backend).parent.parent/'nova-spvm').rglob('*.py')]]}
    receipt={'schema_version':1,'machine_sha256':file_hash('/etc/machine-id'),
        'inputs':inputs,'python':str(interpreter),'python_sha256':file_hash(interpreter),
        'web_python':str(web_python),'web_python_sha256':file_hash(web_python),
        'web_config':str(web),'files':{p.name:file_hash(p) for p in root.iterdir() if p.is_file()},
        'status':'BUDGET_WORKER_STAGED_NOT_INSTALLED'}
    write(root/'activation.json',receipt)
    return {'status':receipt['status'],'activation_sha256':file_hash(root/'activation.json')}


def install(args):
    root=trusted(args.output);receipt=trusted(root/'activation.json')
    if file_hash(receipt)!=args.activation_sha256:raise ValueError('explicit activation digest required')
    value=json.loads(receipt.read_text())
    if value['machine_sha256']!=file_hash('/etc/machine-id'):raise ValueError('activation host differs')
    if file_hash(value['python'])!=value['python_sha256']:raise ValueError('worker interpreter changed')
    if file_hash(value['web_python'])!=value['web_python_sha256']:raise ValueError('web interpreter changed')
    for filename,expected in value['inputs'].items():
        if file_hash(trusted(filename))!=expected:raise ValueError('activation input changed; stage again')
    if set(value['files'])!={'worker.sh','web-config.json',*UNITS}:raise ValueError('exact activation files required')
    for name,expected in value['files'].items():
        if file_hash(trusted(root/name))!=expected:raise ValueError('activation files changed')
    targets=[SYSTEMD_DIRECTORY/n for n in UNITS]
    for target in targets:
        trusted(target.parent)
        if target.exists() or target.is_symlink():raise ValueError('existing budget worker unit; fresh-only install refused')
        result=subprocess.run(['systemctl','show',target.name,'--property=LoadState','--value'],
            capture_output=True,text=True,timeout=15)
        if result.returncode not in (0,1) or result.stdout.strip()!='not-found':
            raise ValueError('cached/existing worker unit refused')
    journal=root/'activation-transaction.json'
    if journal.exists():raise ValueError('activation already attempted; inspect private journal, never replay')
    write(journal,{'status':'APPLYING','phase':'BACKUP'})
    web=Path(value['web_config']);original=web.stat()
    (root/'web-config.before.json').write_bytes(web.read_bytes())
    try:
        from runtime_maintenance import atomic
        atomic(web,(root/'web-config.json').read_bytes(),original.st_mode&0o777)
        os.chown(web,original.st_uid,original.st_gid)
        for target in targets:
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
            with os.fdopen(fd,'wb') as stream:stream.write((root/target.name).read_bytes())
        for command in (['systemctl','daemon-reload'],['systemctl','restart','spvm-web-nodes.service'],
                        ['systemctl','enable','--now','spvm-host-budgets.timer']):
            subprocess.run(command,check=True,capture_output=True,timeout=120)
        write(journal,{'status':'INSTALLED','phase':'REQUEST_PROCESSING_ENABLED',
            'note':'Explicit requests only; no budget or guest change performed by activation.'})
    except BaseException:
        write(journal,{'status':'REVIEW_REQUIRED','phase':'ACTIVATION_FAILED'});raise
    return {'status':'BUDGET_WORKER_INSTALLED','guests_restarted':False}


def main(backend,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage-host-budget-worker','install-host-budget-worker'])
    parser.add_argument('--output',required=True);parser.add_argument('--policy')
    parser.add_argument('--known-hosts');parser.add_argument('--python');parser.add_argument('--web-python')
    parser.add_argument('--web-config');parser.add_argument('--worker-user-id')
    parser.add_argument('--activation-sha256')
    args=parser.parse_args(argv)
    if os.geteuid()!=0:raise ValueError('root activation required')
    from openstack_kolla_existing import backend as load
    load(backend)
    if args.action=='stage-host-budget-worker':
        if not all(getattr(args,k) for k in ('policy','known_hosts','python','web_python','web_config','worker_user_id')):
            raise ValueError('explicit worker inputs required')
        result=stage(backend,args)
    else:result=install(args)
    print(json.dumps(result));return 0
