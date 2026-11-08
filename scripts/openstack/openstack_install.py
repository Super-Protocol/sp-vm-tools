"""One fresh-install transaction, driven from the administrator's management host.

No upgrade, guest creation, automatic recovery, SSH credential distribution or
mutable command recipes in configuration. Individual phases remain diagnostic
tools; the public installer owns their order and their internal approvals.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import traceback
from types import SimpleNamespace as R

from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError
import openstack_progress as progress

SCHEMA = 1
PHASES = ('artifacts', 'workspace', 'compute-binding', 'docker',
          'network', 'helpers', 'images', 'dependencies', 'kolla', 'platform')
RESULTS = dict(zip(PHASES, (
    'MATCHED_PREBUILT_COMPONENTS_VERIFIED',
    'KOLLA_INPUTS_INITIALIZED_NOT_DEPLOYED','COMPUTE_INTENT_BOUND_NOT_DEPLOYABLE','ALL_HOST_DOCKER_READY',
    'ALL_HOST_NETWORK_READY','ALL_HOST_HELPERS_READY_DENY_ALL','ALL_HOST_IMAGES_DELIVERED_NOT_STARTED',
    'ALL_HOST_DEPENDENCIES_READY','KOLLA_DEPLOYED_NOT_SPVM_ENROLLED','PLATFORM_READY_NO_SWARM_CREATED')))


def protected_parent(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise InstallerError('Use an absolute, canonical installation directory without symlinks.')
    for parent in (path, *path.parents):
        if parent.exists():
            value = parent.lstat()
            if not stat.S_ISDIR(value.st_mode) or value.st_uid != 0 or value.st_mode & 0o022:
                raise InstallerError('Installation state must have root-owned, non-writable ancestors: ' + str(parent))
    return path


@contextmanager
def deployment_lock(directory=Path('/run/lock')):
    """Serialize fresh installs on this coordinator, including overlapping inventories."""
    value = directory.lstat()
    if directory.is_symlink() or value.st_uid != 0 or value.st_mode & 0o022 and not value.st_mode & stat.S_ISVTX:
        raise InstallerError('Unsafe installation lock directory.')
    name = directory / 'spvm-openstack-fresh-install.lock'
    fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        value = os.fstat(fd)
        if not stat.S_ISREG(value.st_mode) or value.st_uid != 0 or value.st_nlink != 1 or value.st_mode & 0o777 != 0o600:
            raise InstallerError('Unsafe installation lock file.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallerError('Another fresh installation is running on this management machine.') from None
        yield
    finally:
        os.close(fd)


def fingerprint(paths):
    return {str(p): file_hash(p) for p in paths}


class Transaction:
    """Durable parent WAL. Neither interrupted nor failed children are replayed."""
    def __init__(self, output, inputs, *, emit=print):
        self.output = Path(output)
        self.emit = emit
        self.inputs = dict(inputs)
        if self.output.exists() or self.output.is_symlink():
            raise InstallerError('Installation directory already exists: ' + str(self.output)
                                 + '. Inspect its journal; no overwrite, resume or automatic repair is performed.')
        self.output.mkdir(mode=0o700)
        self.record = {'schema_version': SCHEMA, 'status': 'SCANNING', 'inputs': self.inputs,
                       'completed': [], 'active_phase': 'scan', 'guests_created': False,
                       'created_at': int(time.time())}
        self.save()

    def save(self):
        write(self.output / 'installation.json', self.record)

    def unchanged(self):
        if fingerprint(self.inputs) != self.inputs:
            raise InstallerError('An approved configuration, source or environment receipt changed. Stop and inspect the journal.')

    def fail(self, error):
        self.record.update(status='NEEDS_REVIEW', error_type=type(error).__name__,
            error_frames=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
                          for f in traceback.extract_tb(error.__traceback__)[-6:]])
        self.save()

    def scan(self, callback):
        try:
            self.unchanged()
            with progress.step('scan'):
                plan = callback()
            if not isinstance(plan, dict) or plan.get('blockers'):
                blockers = plan.get('blockers', []) if isinstance(plan, dict) else []
                raise InstallerError('Installation checks failed: ' + ', '.join(blockers))
            self.unchanged()
            write(self.output / 'plan.json', plan)
            self.record.update(status='AWAITING_APPROVAL', active_phase=None, plan_sha256=digest(plan))
            self.save()
            return plan
        except BaseException as error:
            self.fail(error)
            raise

    def apply(self, plan, phases):
        if (self.record['status'] != 'AWAITING_APPROVAL'
                or self.record['plan_sha256'] != digest(plan)
                or json.loads((self.output / 'plan.json').read_bytes()) != plan
                or tuple(phases) != PHASES
                or self.record['completed'] != list(PHASES[:len(self.record['completed'])])):
            raise InstallerError('Complete, unchanged built-in installation plan required.')
        try:
            for name in PHASES[len(self.record['completed']):]:
                self.unchanged()
                self.record.update(status='RUNNING', active_phase=name, mutation_started=True)
                self.save()  # Write intent BEFORE allowing any child mutation.
                with progress.step('install-' + name):
                    result = phases[name]()
                    if not isinstance(result, dict) or result.get('blockers'):
                        raise InstallerError('Phase ' + name + ' did not confirm completion.')
                    write(self.output / (name + '-result.json'), result)
                    if name == 'platform' and (result.get('status') != 'PLATFORM_READY_NO_SWARM_CREATED'
                                               or result.get('guests_launched') is not False):
                        raise InstallerError('Full authenticated API/GUI acceptance is not complete; the installation is NOT ready.')
                    if result.get('status')!=RESULTS[name]:
                        raise InstallerError('Phase '+name+' returned an unexpected/incomplete status; the installation is NOT ready.')
                    self.record['completed'].append(name)
                    self.record.update(status='PHASE_BOUNDARY', active_phase=None)
                    self.save()
            self.unchanged()
            self.record.update(status='OPENSTACK_READY_NO_SWARM_CREATED')
            self.save()
            return self.record
        except BaseException as error:
            self.fail(error)
            raise


@progress.pause()
def approve(deployment, plan, *, yes=False, stream=None, continuation=None):
    """Never infer permission from a noninteractive session or a stale receipt."""
    stream = stream or sys.stdin
    if not yes and not stream.isatty():
        raise InstallerError('Installation requires confirmation. Run interactively, or explicitly supply --yes after reviewing --check.')
    if continuation is not None:
        print('Installation directory already exists: ' + str(continuation))
        print('Installation will continue from the previous verified point; completed phases will not be repeated.')
    if plan.get('continued_from'):
        print('Reviewed continuation. Remaining phases: '+', '.join(plan.get('remaining_phases',['kolla','platform']))+'.')
        print('Previous preparation is retained; the failed transaction is not reset or replayed.')
        if plan.get('compute_activation_replayed') is False:
            print('All compute activations are already complete and have passed read-only acceptance; no compute stop/start/enrollment is repeated.')
            print('The exact old controller reader, if still blocked waiting for its lost acknowledgement, will be terminated after approval.')
    else:
        print('All configured hosts will be prepared and a NEW OpenStack cloud installed.')
    if plan.get('use_testing_images'):
        print('WARNING: upstream quay.io/openstack.kolla images are acknowledged as TEST images, not a production-certified kit.')
    print('Disks are not formatted; existing state is never removed automatically. No Swarm or guest is created.')
    for name, host in plan['hosts'].items():
        print('  ' + name + ': ' + ', '.join(host['roles']))
        if 'compute' in host['roles']:
            print('    No resources assigned: UNCONFIGURED; new VM admissions remain disabled.')
    if plan.get('management_mode')=='shared-interface':
        print('WARNING: shared-interface management is not an encrypted inter-host network. Review exposure before approval.')
    if yes:
        if continuation is not None:
            print('Continuation approved by --yes.')
        return
    if continuation is not None:
        print('Continue the previous installation? [y/N]: ', end='', flush=True)
        if stream.readline().strip().lower() not in ('y', 'yes'):
            raise InstallerError('Continuation not approved; no installation phase was started.')
        return
    print('Type the deployment name to approve (' + deployment + '): ', end='', flush=True)
    if stream.readline().strip() != deployment:
        raise InstallerError('Installation not approved; no installation phase was started.')


def parser():
    p = argparse.ArgumentParser(prog='spvm-openstack.sh install',description=__doc__)
    p.add_argument('--config', required=True, help='Infrastructure-only deployment YAML; no provider config or guest build')
    p.add_argument('--environment', required=True, help='READY prepare-environment directory')
    p.add_argument('--output', required=True, help='Root-private installation directory; an existing installation is checked for safe continuation')
    p.add_argument('--identity-file', help='Local operator SSH private key; never copied to a server')
    p.add_argument('--known-hosts', help='Local SSH trust file; otherwise use invoking-user defaults')
    p.add_argument('--check', action='store_true', help='Read-only host scan and local plan only; no host mutation/download')
    p.add_argument('--yes', action='store_true', help='Approve installation or continuation without prompting, after successful all-host checks')
    p.add_argument('--allow-experimental-ubuntu26', action='store_true')
    p.add_argument('--use-test-images', dest='use_testing_images', action='store_true',
                   help='Explicitly acknowledge upstream quay.io/openstack.kolla test images; not implied by --yes')
    return p


@progress.command('install')
def main(argv=None):
    args = parser().parse_args(argv)
    args.continue_from = args.continue_platform_from = args.continue_compute_from = args.continue_channel_from = None
    transaction=None
    try:
        if os.geteuid() != 0:
            raise InstallerError('Run the installation coordinator with sudo; bootstrap remains a separate operation.')
        protected_parent(Path(args.output).parent)
        # Import only after parsing --help; this module has no third-party dependency.
        from openstack_install_phases import Installation
        with deployment_lock():
            root = Path(args.output)
            origin = mode = None
            if root.exists() or root.is_symlink():
                from openstack_install_resume import select, attempt
                origin, mode = select(root)
                if mode == 'ready':
                    recorded = json.loads((origin / 'installation.json').read_bytes())['inputs']
                    verified = Installation(args)
                    if any(recorded.get(name) != value for name, value in verified.inputs.items()):
                        raise InstallerError('The supplied configuration, environment, identity or sources differ from the completed installation.')
                    print('Installation already recorded as OPENSTACK_READY_NO_SWARM_CREATED: ' + str(origin))
                    print('No phases were repeated. This is not a new live health check.')
                    return 0
                args.output = str(attempt(root))
                if mode == 'kolla': args.continue_from = str(origin)
                if mode == 'platform': args.continue_platform_from = str(origin)
                if mode == 'compute': args.continue_compute_from = str(origin)
                if mode == 'channel': args.continue_channel_from = str(origin)
            installation = Installation(args)
            transaction = Transaction(args.output, installation.inputs)
            if origin is not None:
                from openstack_install_resume import remember
                remember(root, origin, mode, transaction)
                print('Installation history: ' + str(transaction.output), flush=True)
            installation.transaction = transaction
            if args.continue_channel_from:
                from openstack_install_channel_continue import prepare
                plan = transaction.scan(lambda: prepare(installation, transaction))
            elif args.continue_compute_from:
                from openstack_install_compute_continue import prepare
                plan = transaction.scan(lambda: prepare(installation, transaction))
            elif args.continue_platform_from:
                from openstack_install_platform_continue import prepare
                plan = transaction.scan(lambda: prepare(installation, transaction))
            elif args.continue_from:
                from openstack_install_continue import prepare
                plan = transaction.scan(lambda: prepare(installation, transaction))
            else:
                plan = transaction.scan(installation.scan)
            print('All-host installation checks passed. Plan: ' + str(transaction.output / 'plan.json'), flush=True)
            if args.check:
                transaction.record.update(status='CHECKED_NOT_INSTALLED')
                transaction.save()
                return 0
            try:
                approve(installation.cfg['deployment_id'], plan, yes=args.yes,
                        continuation=root if origin is not None else None)
                if args.continue_channel_from:
                    from openstack_install_channel_continue import claim
                    claim(installation, transaction, plan)
                elif args.continue_compute_from:
                    from openstack_install_compute_continue import claim
                    claim(installation, transaction, plan)
                elif args.continue_platform_from:
                    from openstack_install_platform_continue import claim
                    claim(installation, transaction, plan)
                elif args.continue_from:
                    from openstack_install_continue import claim
                    claim(installation, transaction, plan)
                elif origin is not None:
                    from openstack_install_resume import claim_scan
                    claim_scan(origin, transaction, plan)
                result = transaction.apply(plan, installation.phases())
            except BaseException as error:
                if transaction.record['status'] != 'NEEDS_REVIEW':
                    transaction.fail(error)
                raise
            print(result['status'])
            print('OpenStack UI: https://' + installation.cfg['network']['api_vip'] + ':9999')
            print('No images, flavors, provider templates or Swarm clusters were created.')
            print('New computes are enrolled UNCONFIGURED/disabled. Set CPU/RAM, add an enabled state pool and optional GPUs in Host resource budgets or CLI, then explicitly Enable each host.')
            return 0
    except InstallerError as error:
        print('ERROR: ' + str(error), file=sys.stderr)
    except KeyboardInterrupt:
        print('Installation interrupted. Inspect the transaction before any new attempt; no automatic replay.',file=sys.stderr)
        return 130
    except Exception as error:
        # Do not stringify exceptions from network/pip/API clients: they may contain credentials.
        detail=('Inspect '+str(Path(args.output)/'installation.json')+' and the active phase log.'
                if transaction is not None else
                'Local input validation failed before a transaction was created; check the selected config, bundle and READY environment.')
        print('ERROR: installation stopped (' + type(error).__name__ + '). '+detail+
              ' No automatic retry or cleanup was performed.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
