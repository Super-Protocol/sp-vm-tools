"""Activate one already prepared empty compute; never bootstrap or drain guests."""
import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

from openstack_compute_image import read
from openstack_container_engine import write


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan-compute-activation','activate-compute'))
    for name in ('config','host','controller','controller-python','openrc','output'):
        parser.add_argument('--'+name,required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--enrollment-request');mode.add_argument('--preserve-enrollment',action='store_true')
    parser.add_argument('--plan-sha256')
    args=parser.parse_args(argv)
    try:
        root=Path(path).parent
        for directory in (root.parent/'nova-spvm',root.parent,root):sys.path.insert(0,str(directory))
        installer=importlib.import_module('installer')
        from initial_compute import Backend
        from compute_transaction import execute
        cfg=installer.deployment_config(args.config)
        output=Path(args.output)
        if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical private output required')
        if args.action=='plan-compute-activation':output.mkdir(mode=0o700,exist_ok=False)
        if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:raise ValueError('private owned output required')
        enrollment=json.loads(read(args.enrollment_request)) if args.enrollment_request else None
        backend=Backend(cfg,args.controller,args.controller_python,args.openrc,output/'observations',
                        enrollment=enrollment,preserve=args.preserve_enrollment)
        if args.action=='plan-compute-activation':
            result=backend.plan(args.host);write(output/'initial-compute-plan.json',result)
            public={k:result[k] for k in ('plan_sha256','host','mode')};public['status']='INITIAL_COMPUTE_PLAN_READY'
        else:
            plan=json.loads(read(output/'initial-compute-plan.json'))
            if plan.get('host')!=args.host:raise ValueError('selected host differs from plan')
            result=execute(plan,args.plan_sha256,output/'transaction',backend)
            public={k:result[k] for k in ('status','host','done','guests_restarted')}
        print(json.dumps(public));return 0 if public['status'] in ('INITIAL_COMPUTE_PLAN_READY','COMPUTE_READY') else 2
    except Exception as error:
        try:
            if output.is_dir() and output.stat().st_uid==os.getuid() and not output.stat().st_mode & 0o077:
                write(output/'activation-failure.json',{'error_class':type(error).__name__,
                    'location':[(Path(f.filename).name,f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)[-5:]]})
        except Exception:pass
        print('ERROR: compute activation refused/incomplete. Review private plan/journal and Nova quarantine; do not restart guests or replay ambiguous actions.',file=sys.stderr)
        return 2
