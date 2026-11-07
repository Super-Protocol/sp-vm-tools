"""Stdlib entry point for management-host compute retirement."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from openstack_install_entry import main


def parser():
    p=argparse.ArgumentParser(prog='spvm-openstack.sh remove-compute',description='Retire an empty compute using controller-owned state and operator SSH.')
    for key in ('controller','environment','host','output'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--cloud-id',help='Expected cloud UUID; a different cloud is refused')
    p.add_argument('--identity-file');p.add_argument('--known-hosts')
    p.add_argument('--check',action='store_true',help='Read-only check and private local copy; no retirement')
    p.add_argument('--yes',action='store_true',help='Approve the exact host retirement after successful checks')
    return p

if __name__=='__main__':
    raise SystemExit(main(operation='remove-compute'))
