"""Use the matching driver's SSH transport policy for local installer phases."""
import importlib.util
import os
from pathlib import Path


def transport():
    source=Path(os.environ['SPVM_INSTALLER_SSH_BACKEND'])/'ssh_transport.py'
    spec=importlib.util.spec_from_file_location('spvm_installer_ssh_transport',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def common_args(known_hosts):
    return transport().common_args(known_hosts=known_hosts)
