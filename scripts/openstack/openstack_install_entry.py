"""Stdlib entry point: select the explicitly prepared installer Python, never pip/system apt."""
import argparse
import json
import os
from pathlib import Path
import sys


def main(argv=None, *, operation='install'):
    # A caller may already select installer-venv, bypassing the -B re-exec.
    # Never add bytecode caches to downloaded, hash-pinned controller inputs.
    sys.dont_write_bytecode=True
    argv=list(sys.argv[1:] if argv is None else argv)
    folder=Path(__file__).resolve().parent
    sys.path.insert(0,str(folder))
    # Help must work without a pre-existing environment or third-party packages.
    if '--help' in argv or '-h' in argv:
        if operation=='add-compute':
            from openstack_add_entry import parser
        elif operation=='remove-compute':
            from openstack_remove_entry import parser
        else:
            from openstack_install import parser
        parser().print_help()
        return 0
    p=argparse.ArgumentParser(add_help=False)
    p.add_argument('--environment',required=True)
    args,_=p.parse_known_args(argv)
    try:
        root=Path(args.environment)
        if not root.is_absolute() or root.resolve()!=root:
            raise ValueError()
        receipt=json.loads((root/'environment.json').read_bytes())
        python=root/'installer-venv/bin/python'
        if receipt.get('status')!='READY' or not python.is_file():raise ValueError()
        if Path(sys.prefix).resolve()!=root/'installer-venv':
            entry=folder/({'remove-compute':'openstack_remove_entry.py','add-compute':'openstack_add_entry.py'}.get(operation,'openstack_install_entry.py'))
            os.execv(str(python),[str(python),'-I','-B',str(entry),*argv])
    except (OSError,ValueError):
        print('ERROR: --environment must select the existing READY prepare-environment directory. '
              'No packages or environments were installed.',file=sys.stderr)
        return 2
    if operation=='add-compute':
        from openstack_add_manager import main as add
        return add(argv)
    if operation=='remove-compute':
        from openstack_remove_manager import main as remove
        return remove(argv)
    from openstack_install import main as install
    return install(argv)


if __name__=='__main__':
    raise SystemExit(main())
