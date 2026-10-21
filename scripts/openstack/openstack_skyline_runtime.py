"""Fresh-only, approved installation of staged Skyline web/capacity services.

Never overwrite occupied units, upgrade a running API, reconfigure Kolla or
perform guest lifecycle. Existing installations require a separate scoped rollout.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import sys
import time
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

from openstack_container_engine import digest, write

LOCK = Path('/run/lock/spvm-skyline-runtime.lock')


def lock_install():
    info = LOCK.parent.lstat()
    if LOCK.parent.is_symlink() or info.st_uid != 0 or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
        raise ValueError('protected/sticky root lock directory required')
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o777 != 0o600:
            raise ValueError('protected installer lock required')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd); raise


def trusted(path):
    path = Path(path)
    for item in (path, *path.parents):
        info = item.lstat()
        if item.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('root-protected canonical installer input/parent required')
    return path


def inputs(backend, args):
    root = trusted(args.runtime_dir)
    receipt = trusted(root/'runtime-stage.json')
    if receipt.stat().st_size > 262144 or hashlib.sha256(receipt.read_bytes()).hexdigest() != args.runtime_sha256:
        raise ValueError('approved staged runtime receipt required')
    value = json.loads(receipt.read_text())
    if value.get('schema_version') != 1 or value.get('status') != 'SKYLINE_RUNTIME_STAGED_NOT_INSTALLED':
        raise ValueError('complete staged runtime required')
    files = value.get('files_sha256')
    if not isinstance(files, dict) or len(files) not in (5, 9):
        raise ValueError('recognized staged files required')
    for name, expected in files.items():
        item = root/name
        if not item.is_relative_to(root) or '..' in Path(name).parts or Path(name).is_absolute():
            raise ValueError('staged file outside runtime')
        trusted(item)
        if not item.is_file() or hashlib.sha256(item.read_bytes()).hexdigest() != expected:
            raise ValueError('staged runtime file drift')
    account = pwd.getpwnam(value['service_user'])
    if account.pw_uid == 0: raise ValueError('non-root web account required')
    config = json.loads((root/'config.json').read_text())
    state = Path(config['state_dir'])
    trusted(state.parent)
    if not state.is_absolute() or state.resolve() != state:
        raise ValueError('canonical private web state required')
    if state.exists() and (state.is_symlink() or state.stat().st_uid != account.pw_uid
                           or state.stat().st_mode & 0o777 != 0o700 or any(state.iterdir())):
        raise ValueError('fresh empty owned state required; never replay existing operations')
    unit_names = ['spvm-web-nodes.service', 'spvm-web-capacity.service', 'spvm-web-capacity.timer']
    publication = value.get('publication_enabled_in_staged_config') is True
    if publication: unit_names += ['spvm-web-publish.service', 'spvm-web-publish.timer']
    targets = {Path('/etc/systemd/system')/name: root/'units'/name for name in unit_names}
    targets[Path('/usr/local/libexec/spvm-web-capacity/collector.py')] = root/'helpers/web_capacity_collector.py'
    if publication:
        targets[Path('/usr/local/sbin/spvm-web-publish')] = root/'helpers/spvm-web-publish'
        targets[Path('/etc/spvm/web-publication-policy.json')] = root/'web-publication-policy.json'
        for item in ('/usr/local/sbin/spvm-ingress-enroll', '/usr/local/sbin/spvm-ui-ready', '/etc/spvm/ingress-policy.json'):
            trusted(item)
    for target, source in targets.items():
        if target.exists() or target.is_symlink(): raise ValueError('occupied runtime target; fresh-only install refused')
        if str(source.relative_to(root)) not in files: raise ValueError('target source missing from receipt')
        parent = target.parent
        while not parent.exists(): parent = parent.parent
        trusted(parent)
    for name in unit_names:
        check = subprocess.run(['systemctl', 'show', name, '--property=LoadState', '--value'],
            capture_output=True, timeout=10)
        if check.returncode not in (0, 1) or check.stdout.strip() != b'not-found':
            raise ValueError('existing/cached systemd unit; fresh-only install refused')
    # Never kill or replace a loopback listener to make an installation fit.
    with socket.socket() as probe: probe.bind(('127.0.0.1', 18776))
    helper = Path(backend).parent/'skyline/stage_runtime.py'
    definition = importlib.util.spec_from_file_location('approved_runtime_stage', helper)
    module = importlib.util.module_from_spec(definition); definition.loader.exec_module(module)
    if module.tree_digest(root/'venv') != value.get('venv_sha256'):
        raise ValueError('matching client/SDK environment drift')
    identity = {'runtime_sha256': args.runtime_sha256, 'runtime_dir': str(root),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'backend_sha256': hashlib.sha256(Path(backend).read_bytes()).hexdigest(),
        'stager_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
        'machine_sha256': hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()}
    return root, value, account, state, targets, identity


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=120,
        env={'PATH': '/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode: raise ValueError('scoped runtime action failed; output suppressed')
    return result


def execute(backend, args):
    root, value, account, state, targets, identity = inputs(backend, args)
    output = Path(args.output); trusted(output.parent)
    if args.action == 'plan-skyline-runtime':
        output.mkdir(mode=0o700, exist_ok=False)
        plan = {'schema_version': 1, 'identity': identity, 'created_at': int(time.time()),
                'targets': sorted(map(str, targets)), 'state_dir': str(state)}
        plan['plan_sha256'] = digest(plan); write(output/'runtime-plan.json', plan)
        return {'status': 'FRESH_SKYLINE_RUNTIME_PLAN_READY', 'plan_sha256': plan['plan_sha256']}
    trusted(output)
    plan = json.loads((output/'runtime-plan.json').read_text())
    if (plan.get('identity') != identity or plan.get('targets') != sorted(map(str, targets))
            or plan.get('state_dir') != str(state) or plan.get('plan_sha256') != args.plan_sha256
            or digest({k:v for k,v in plan.items() if k != 'plan_sha256'}) != args.plan_sha256
            or not 0 <= time.time()-plan['created_at'] <= 900):
        raise ValueError('fresh unchanged runtime installation approval required')
    journal = output/'runtime-install.json'
    if journal.exists() or journal.is_symlink(): raise ValueError('previous installation needs review, not replay')
    record = {'status': 'INSTALLING_FRESH_SKYLINE_RUNTIME', 'services_started': [], 'guests_changed': False}
    with journal.open('x') as stream:
        json.dump(record, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
    try:
        if not state.exists():
            state.mkdir(mode=0o700); state.chmod(0o700); os.chown(state, account.pw_uid, account.pw_gid)
        for target, source in targets.items():
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            trusted(target.parent)
            with target.open('xb') as stream:
                stream.write(source.read_bytes())
                os.fchmod(stream.fileno(), 0o600 if target.name.endswith('-policy.json') else 0o644)
        run(['systemctl', 'daemon-reload'])
        if value.get('publication_enabled_in_staged_config') is True:
            # Empty fresh state only: validate the installed worker/delegation
            # before enabling a timer or accepting any cluster creation.
            run(['/usr/bin/python3', '/usr/local/sbin/spvm-web-publish',
                 '--policy', '/etc/spvm/web-publication-policy.json'])
        for name in ('spvm-web-capacity.service', 'spvm-web-nodes.service'):
            # Record intent before invoking a possibly ambiguously acknowledged start.
            record['services_started'].append(name); write(journal, record)
            run(['systemctl', 'start', name])
        deadline = time.monotonic()+30
        while True:
            try:
                with urlopen('http://127.0.0.1:18776/v1/cluster-catalog', timeout=3):
                    raise ValueError('API unexpectedly anonymous')
            except HTTPError as error:
                if error.code != 401: raise ValueError('unexpected fresh API status') from None
                break
            except (URLError, OSError):
                if time.monotonic() >= deadline: raise TimeoutError('fresh API liveness deadline reached')
                time.sleep(1)
    except BaseException:
        for name in reversed(record['services_started']):
            subprocess.run(['systemctl', 'stop', name], capture_output=True, timeout=60)
        record['status'] = 'NEEDS_REVIEW'; write(journal, record); raise
    try:
        timers = ['spvm-web-capacity.timer']
        if value.get('publication_enabled_in_staged_config') is True: timers.append('spvm-web-publish.timer')
        for name in ['spvm-web-nodes.service', *timers]:
            record['services_started'].append(name); write(journal, record)
            run(['systemctl', 'enable', '--now', name])
        record['status'] = 'FRESH_SKYLINE_RUNTIME_INSTALLED'
        write(journal, record); return record
    except BaseException:
        for name in reversed(record['services_started']):
            subprocess.run(['systemctl', 'disable', '--now', name], capture_output=True, timeout=60)
        record['status'] = 'NEEDS_REVIEW'; write(journal, record); raise


def main(backend, argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan-skyline-runtime', 'install-skyline-runtime'))
    for name in ('runtime-dir', 'runtime-sha256', 'output'): parser.add_argument('--'+name, required=True)
    parser.add_argument('--plan-sha256')
    args = parser.parse_args(argv)
    lock = None
    try:
        if os.geteuid() != 0: raise ValueError('root-local runtime installer required')
        if args.action == 'install-skyline-runtime': lock = lock_install()
        print(json.dumps(execute(backend, args), sort_keys=True)); return 0
    except Exception:
        print('ERROR: runtime install refused/incomplete; inspect private journal. No VM lifecycle requested.', file=sys.stderr)
        return 2
    finally:
        if lock is not None: os.close(lock)
