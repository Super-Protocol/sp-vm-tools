"""Stdlib entry point for management-host compute retirement."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from openstack_install_entry import main

if __name__=='__main__':
    raise SystemExit(main(operation='remove-compute'))
