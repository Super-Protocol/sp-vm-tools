"""Stage the driver-owned web API runtime; console images are CI-owned."""
import argparse
from pathlib import Path
import subprocess
import sys

from openstack_artifact_inputs import wheel_bytes


def main(backend, argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stage-skyline-runtime',))
    parser.add_argument('--output', required=True)
    for name in ('sdk-venv','web-user','web-config','wheel','wheel-sha256'):
        parser.add_argument('--'+name, required=True)
    for name in ('publication-policy','retirement-policy'):
        parser.add_argument('--'+name)
    args = parser.parse_args(argv)
    wheel_bytes(backend, args.wheel, args.wheel_sha256)
    command = [sys.executable, str(Path(backend).parent/'skyline/stage_runtime.py'),
        '--output', args.output, '--sdk-venv', args.sdk_venv, '--web-user', args.web_user,
        '--web-config', args.web_config, '--wheel', args.wheel, '--wheel-sha256', args.wheel_sha256]
    for name in ('publication_policy','retirement_policy'):
        value = getattr(args, name)
        if value: command += ['--'+name.replace('_','-'), value]
    subprocess.run(command, check=True)
    return 0
