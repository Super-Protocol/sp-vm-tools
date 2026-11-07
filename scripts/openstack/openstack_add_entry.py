"""Standard-library entry point for the explicit add-compute operation."""
import argparse
from pathlib import Path
import sys


def parser():
    p=argparse.ArgumentParser(prog='spvm-openstack.sh add-compute',description='Add new or retired computes sequentially. Hosts start UNCONFIGURED and disabled; assign resources and Enable separately.')
    for name in ('config','environment','installation','output'):
        help_text={'config':'Host-only YAML (hosts mapping); no cloud configuration',
                   'installation':'Original completed installation; supplies cloud and pinned kit settings',
                   'output':'New private batch directory; per-host journals are retained',
                   'environment':'Existing READY installer environment'}
        p.add_argument('--'+name,required=True,help=help_text[name])
    p.add_argument('--host',help='Select one host from the host-only YAML; otherwise process all in YAML order')
    p.add_argument('--identity-file');p.add_argument('--known-hosts')
    p.add_argument('--allow-experimental-ubuntu26',action='store_true')
    p.add_argument('--check',action='store_true',help='Inspect only; do not stop services, reconcile data or enroll the host')
    p.add_argument('--yes',action='store_true',help='Approve the displayed bounded operation without interactive input')
    return p


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from openstack_install_entry import main
    raise SystemExit(main(operation='add-compute'))
