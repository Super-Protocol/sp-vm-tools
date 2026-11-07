"""Controller-backed management inputs; local directories are disposable copies.

No installation receipt is synthesized, no remote executable is trusted as a
transport recipe, and no operator SSH credential is included in the snapshot.
"""
import copy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid

import yaml
import openstack_cloud_store as store
from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError

WORKSPACE = '@SPVM_WORKSPACE@'
DRIVER = '@SPVM_DRIVER@'
KOLLA_SOURCE = '@SPVM_KOLLA_SOURCE@'
KOLLA_PYTHON = '@SPVM_KOLLA_PYTHON@'


def relocate(value, mapping):
    if isinstance(value, dict): return {k: relocate(v, mapping) for k, v in value.items()}
    if isinstance(value, list): return [relocate(v, mapping) for v in value]
    if isinstance(value, str):
        for before, after in sorted(mapping.items(), key=lambda v: -len(v[0])):
            if value == before or value.startswith(before + '/'):
                return after + value[len(before):]
    return value


def ssh_arguments(args):
    """Bootstrap transport before a driver can be fetched; never root defaults/TOFU."""
    user, separator, address = args.controller.partition('@')
    if not separator or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user):
        raise InstallerError('--controller must be USER@literal-IP.')
    try: ipaddress.ip_address(address)
    except ValueError: raise InstallerError('--controller must use a literal controller IP address.') from None
    uid = int(os.environ.get('SUDO_UID', os.getuid())) if os.geteuid() == 0 else os.getuid()
    home = Path(pwd.getpwuid(uid).pw_dir)
    def local(value):
        path = home / str(value)[2:] if str(value).startswith('~/') else Path(value).absolute()
        try: path = path.resolve(strict=True)
        except OSError:
            raise InstallerError('SSH identity/trust file is missing or unreadable: ' + str(path) + '. Prepare operator keys and known-host trust explicitly.') from None
        if not path.is_file() or any(c.isspace() or ord(c) < 32 for c in str(path)):
            raise InstallerError('SSH inputs must be regular local files with whitespace-free paths.')
        return str(path)
    identities = ([local(args.identity_file)] if args.identity_file else
                  [local(home / '.ssh' / n) for n in ('id_ed25519', 'id_ed25519_sk', 'id_ecdsa', 'id_ecdsa_sk', 'id_rsa')
                   if (home / '.ssh' / n).is_file()])
    trust = local(args.known_hosts or (home / '.ssh/known_hosts' if (home / '.ssh/known_hosts').exists()
                                      else '/etc/ssh/ssh_known_hosts'))
    agent = 'none'
    if not args.identity_file and os.environ.get('SSH_AUTH_SOCK'):
        path = Path(os.environ['SSH_AUTH_SOCK']); info = path.stat()
        if not path.is_absolute() or info.st_uid != uid or not stat.S_ISSOCK(info.st_mode) or any(c.isspace() for c in str(path)):
            raise InstallerError('SSH agent must be a local socket owned by the invoking operator.')
        agent = str(path)
    command = ['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=15', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
               '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=12',
               '-o', 'UserKnownHostsFile=' + trust, '-o', 'IdentityAgent=' + agent,
               '-o', 'IdentitiesOnly=' + ('yes' if args.identity_file else 'no')]
    for path in identities: command.extend(['-i', path])
    if not identities: command.extend(['-o', 'IdentityFile=none'])
    args.known_hosts = trust
    return command + [args.controller]


def remote_source(code):
    # Execute only the local operator-selected stdlib implementation.
    return Path(store.__file__).read_text() + '\n' + code


def validate_identity(identity, pointer):
    if (not isinstance(identity, dict) or identity != pointer['identity']
            or not re.fullmatch('[a-f0-9]{64}', str(identity.get('controller_machine')))
            or not re.fullmatch('/opt/spvm-openstack/[a-zA-Z0-9_-]+', str(identity.get('native_root')))):
        raise InstallerError('Invalid controller cloud identity or installed component location.')


class Cloud:
    def __init__(self, args, output):
        self.args = args; self.output = Path(output); self.argv = ssh_arguments(args)
        self.base = self.output / 'cloud'; self.workspace = self.base / 'workspace'
        self.config = self.base / 'deployment.yaml'; self.driver = self.base / 'driver'
        self.pointer = None

    def receive(self):
        code = remote_source('''
import sys
pointer, generation = current()
machine = hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()
if pointer['identity']['controller_machine'] != machine: raise ValueError('wrong controller machine')
# Frame a bounded manifest before the archive, without creating remote files.
header = {'pointer': pointer, 'manifest': read(generation/'manifest.json')}
sys.stdout.buffer.write((json.dumps(header)+'\\n').encode()); sys.stdout.buffer.flush()
pack(generation/'payload', sys.stdout.buffer)
''')
        log = self.output / 'private-cloud-transfer.log'
        with log.open('xb') as errors:
            os.fchmod(errors.fileno(), 0o600)
            process = subprocess.Popen(self.argv + ['sudo -n /usr/bin/python3 -I -B -c ' + shlex.quote(code)],
                                       stdout=subprocess.PIPE, stderr=errors)
            try:
                header = process.stdout.readline(16 * 1024**2 + 1)
                if not header.endswith(b'\n') or len(header) > 16 * 1024**2:
                    raise InstallerError('Controller cloud-management state is missing, incomplete or unreadable. Inspect ' + str(log) + '; old installations are not adopted.')
                envelope = json.loads(header)
                self.pointer = envelope['pointer']; identity = self.pointer['identity']
                if self.pointer.get('capability') != store.CAPABILITY:
                    raise InstallerError('Controller lacks ' + store.CAPABILITY + '; a matching fresh installation is required.')
                uuid.UUID(self.pointer['cloud_id']); validate_identity(identity, self.pointer)
                if self.args.cloud_id and self.args.cloud_id != self.pointer['cloud_id']:
                    raise InstallerError('Controller belongs to a different cloud UUID.')
                self.manifest = envelope['manifest']
                manifest_sha = hashlib.sha256(json.dumps(self.manifest, sort_keys=True, indent=2).encode()).hexdigest()
                if manifest_sha != self.pointer['manifest_sha256']:
                    raise InstallerError('Cloud manifest hash differs from the authenticated pointer.')
                if digest(self.manifest['identity']) != digest(identity): raise InstallerError('Cloud identity differs from manifest.')
                # Remote files are not executed before the authenticated manifest is verified.
                store.unpack(process.stdout, self.base)
                if process.wait(timeout=30) != 0 or store.tree(self.base) != self.manifest['files']:
                    raise InstallerError('Cloud transfer failed integrity verification; partial files retained.')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                process.stdout.close()
        self.identity = identity; self.native_root = identity['native_root']
        self.metadata = store.read(self.base / 'cloud.json')
        if self.metadata.get('identity') != identity:
            raise InstallerError('Cloud payload identity differs from its authenticated generation.')
        local_tools = {p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')}
        if self.metadata['tools_sha256'] != local_tools:
            raise InstallerError('Installer source differs from the controller kit. Use sp-vm-tools commit '
                + self.metadata['installer_commit'] + ' and source-tree hash ' + self.metadata['installer_source_sha256']
                + '; no downloaded tools are executed. The authenticated source snapshot is in the private output cloud/tools directory.')
        for name, expected in self.metadata['tools_sha256'].items():
            path = Path(__file__).parent / store.name(name)
            if path.parent != Path(__file__).parent or not path.is_file() or file_hash(path) != expected:
                raise InstallerError('Installer source differs from the cloud kit. Use its recorded installer revision; no downloaded tools are executed.')
            if file_hash(self.base / 'tools' / name) != expected:
                raise InstallerError('Retained installer source snapshot differs from its recorded hashes.')
        from openstack_installer import backend
        try: self.backend_path = backend(self.driver, verified_bundle=True)
        except (OSError, ValueError):
            raise InstallerError('Controller driver kit is incomplete, corrupt or lacks the current capabilities. Inspect the private snapshot; no fallback or migration.') from None
        from openstack_install_phases import load_module
        sys.path[:0] = [str(self.backend_path.parent), str(self.driver / 'nova-spvm')]
        module = load_module(self.backend_path, 'installer'); sys.modules['installer'] = module
        self.module = module
        transport = load_module(self.backend_path.with_name('ssh_transport.py'), 'cloud_ssh')
        ssh = transport.resolve(self.args.identity_file, self.args.known_hosts)
        os.environ['SPVM_INSTALLER_SSH_POLICY'] = json.dumps(ssh)
        os.environ['SPVM_INSTALLER_SSH_BACKEND'] = str(self.backend_path.parent)
        self.hydrate()
        return self

    def hydrate(self):
        environment = Path(self.args.environment)
        receipt = json.loads((environment / 'environment.json').read_bytes())
        if (receipt.get('status') != 'READY' or receipt.get('inputs', {}).get('kolla_revision') != self.metadata['kolla_revision']
                or receipt.get('installed') != store.read(self.base / 'environment.json')['installed']):
            raise InstallerError('Prepare a matching READY environment for Kolla revision ' + self.metadata['kolla_revision'] + '; installed package/collection versions must match the cloud kit.')
        from prepare_environment import validate, isolated_env, PreparationError
        with (self.output / 'private-cloud-environment.log').open('x') as log:
            os.fchmod(log.fileno(), 0o600)
            try: actual = validate(environment, isolated_env(environment), log)
            except PreparationError:
                raise InstallerError('Local prepared environment failed import/CLI/dependency verification; inspect private-cloud-environment.log. No packages were installed or updated.') from None
            if actual != receipt['installed']:
                raise InstallerError('Actual environment differs from its READY receipt; inspect private-cloud-environment.log. No automatic dependency update.')
        mapping = {WORKSPACE: str(self.workspace), DRIVER: str(self.driver),
                   KOLLA_SOURCE: str(environment / 'kolla-source'), KOLLA_PYTHON: str(environment / 'kolla-venv/bin/python')}
        transform_documents(self.base, mapping)
        self.cfg = self.module.deployment_config(str(self.config))
        controls = [n for n, h in self.cfg['hosts'].items() if 'control' in h['roles']]
        if controls != [self.identity['controller']]: raise InstallerError('Single-controller cloud identity differs.')
        host = self.cfg['hosts'][controls[0]]
        if self.args.controller.partition('@')[2] != host['address']:
            raise InstallerError('SSH controller address must match the cloud inventory; no controller reassignment.')
        if (file_hash(self.workspace / 'kolla/certificates/ca/ca.crt') != self.identity['api_ca_sha256']
                or self.identity['api_endpoint'] != 'https://' + self.cfg['network']['api_vip'] + ':5000/v3'):
            raise InstallerError('Recovered API CA or endpoint differs from the cloud identity.')
        bind_controller(self.module, self)
        seal_workspace(self.workspace, self.cfg, environment)
        # Rebuild local inventory transport from this operator, never the old key/agent.
        import kolla_inputs
        template = kolla_inputs.upstream(str(environment / 'kolla-source'), self.metadata['kolla_revision'])
        (self.workspace / 'hosts.ini').write_text(kolla_inputs.inventory(self.cfg, template['content']))
        seal_workspace(self.workspace, self.cfg, environment)
        self.pins = store.tree(self.base)

    def unchanged(self):
        if store.tree(self.base) != self.pins:
            raise InstallerError('Controller-derived local inputs changed outside this operation.')

    def verify(self, coordinator):
        expected = self.pointer
        code = remote_source('''
pointer, generation = current()
if pointer != EXPECTED: raise ValueError('cloud generation changed; scan again')
print(json.dumps({'status':'CLOUD_GENERATION_VERIFIED'}))
''')
        return coordinator.remote('EXPECTED=' + repr(expected) + '\n' + code)

    def begin(self, coordinator, kind, host, operation, plan_sha256):
        code = remote_source('begin(ROOT, EXPECTED, OPERATION, KIND, HOST, PLAN)\nprint(json.dumps({"status":"CLOUD_OPERATION_CLAIMED"}))')
        return coordinator.remote('EXPECTED=' + repr(self.pointer) + '\nOPERATION=' + repr(operation) + '\nKIND=' + repr(kind) + '\nHOST=' + repr(host) + '\nPLAN=' + repr(plan_sha256) + '\n' + code)

    def publish(self, coordinator, workspace, config, operation, *, network=None, retirement=None):
        self.unchanged()
        retirements = copy.deepcopy(self.metadata['retirements'])
        if retirement is not None:
            retirements[retirement['host']] = retirement
        staged = coordinator.output / 'cloud-successor'
        snapshot(staged, workspace=workspace, config=config, base=self.base, driver=self.driver,
                 environment=self.args.environment, identity=self.identity, retirements=retirements,
                 network=network)
        self.unchanged()
        pointer = upload(coordinator, staged, self.identity, expected=self.pointer, operation=operation)
        self.pointer = pointer
        # Subsequent batch children use only the just-committed generation.
        self.workspace = Path(workspace); self.config = Path(config)
        self.metadata['retirements'] = retirements
        self.pins = store.tree(self.base)
        return {'status': 'CLOUD_GENERATION_COMMITTED', 'cloud_id': pointer['cloud_id'], 'generation': pointer['generation']}


def transform_documents(root, mapping):
    """Relocate structured path values, not substrings or executable recipes."""
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file() or not path.is_relative_to(Path(root) / 'workspace') and path != Path(root) / 'deployment.yaml':
            continue
        if path.suffix not in ('.json', '.yaml', '.yml'): continue
        if path == Path(root) / 'workspace/kolla/passwords.yml': continue
        # Collections and driver files are immutable artifacts, not our documents.
        if path.is_relative_to(Path(root) / 'workspace/collections'): continue
        if path.stat().st_size > 16 * 1024**2: raise ValueError('oversized structured workspace document')
        old = yaml.safe_load(path.read_bytes())
        new = relocate(old, mapping)
        if new != old:
            path.write_text(json.dumps(new, sort_keys=True, indent=2) if path.suffix == '.json' else yaml.safe_dump(new, sort_keys=True))


def seal_workspace(workspace, cfg, environment):
    import openstack_kolla_workspace as kolla
    binding = json.loads((workspace / 'compute-binding.json').read_bytes())
    for host in binding['hosts'].values(): host['request_sha256'] = file_hash(host['request_file'])
    binding['config_sha256'] = digest(cfg)
    binding['binding_sha256'] = digest({k: v for k, v in binding.items() if k != 'binding_sha256'})
    write(workspace / 'compute-binding.json', binding)
    receipt = json.loads((workspace / 'workspace.json').read_bytes())
    receipt.update(config_sha256=digest(cfg), compute_binding=binding['binding_sha256'],
                   collections=str(workspace / 'collections'), kolla_source=str(environment / 'kolla-source'),
                   kolla_python=str(environment / 'kolla-venv/bin/python'),
                   kolla_python_sha256=file_hash(environment / 'kolla-venv/bin/python'), files=kolla.file_set(workspace))
    write(workspace / 'workspace.json', receipt)
    kolla.check(workspace)


def snapshot(directory, *, workspace, config, base, driver, environment, identity, retirements=None, network=None):
    """Build a portable generation from verified current inputs, never a venv/run."""
    directory = Path(directory); directory.mkdir(mode=0o700)
    for source, destination in ((workspace, 'workspace'),
                                (Path(base) / 'launcher', 'launcher'), (Path(base) / 'compute-package', 'compute-package')):
        store.copy_tree(source, directory / destination)
    copy_driver(driver, directory / 'driver')
    # Preserve only sealed workspace members, not logs, historical successors or caches.
    import openstack_kolla_workspace as kolla
    before = kolla.check(workspace)
    raw = copy.deepcopy(yaml.safe_load(Path(config).read_bytes()))
    for host in raw['hosts'].values(): host.pop('ssh_transport', None); host.pop('identity_file', None)
    raw['driver'] = {'directory': DRIVER}
    for key in ('skyline_console_context', 'skyline_console_context_sha256'):
        raw['kolla'].pop(key, None)
    write(directory / 'deployment.yaml', raw)
    write(directory / 'network.json', network if network is not None else json.loads((Path(base) / 'network.json').read_bytes()))
    environment_record = json.loads((Path(environment) / 'environment.json').read_bytes())
    write(directory / 'environment.json', {'installed': environment_record['installed']})
    tools = {p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')}
    repository = Path(__file__).resolve().parents[2]
    git = ['git', '-c', 'safe.directory=' + str(repository), '-C', str(repository)]
    revision = subprocess.run(git + ['rev-parse', 'HEAD'], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    if not re.fullmatch('[a-f0-9]{40}', revision): raise InstallerError('Cannot record installer Git provenance.')
    dirty = subprocess.run(git + ['diff', '--quiet', 'HEAD', '--', 'scripts/openstack', 'scripts/spvm-openstack.sh'], timeout=30).returncode
    if dirty not in (0, 1): raise InstallerError('Cannot inspect installer source provenance.')
    # Retain the source snapshot for offline audit/backup, never auto-execute it.
    (directory / 'tools').mkdir(mode=0o700)
    for source in Path(__file__).parent.glob('*.py'):
        body = source.read_bytes()
        if hashlib.sha256(body).hexdigest() != tools[source.name]:
            raise InstallerError('Installer source changed during snapshot preparation.')
        with (directory / 'tools' / source.name).open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600); stream.write(body)
    write(directory / 'cloud.json', {'schema_version': 1, 'kolla_revision': raw['kolla']['revision'],
          'tools_sha256': tools, 'installer_commit': revision, 'installer_sources_dirty': bool(dirty),
          'installer_source_sha256': digest(tools), 'identity': identity, 'retirements': retirements or {}})
    mapping = {str(workspace): WORKSPACE, str(driver): DRIVER,
               str(Path(environment) / 'kolla-source'): KOLLA_SOURCE,
               str(Path(environment) / 'kolla-venv/bin/python'): KOLLA_PYTHON}
    # Existing helper request files can predate the bound workspace.
    binding = json.loads((Path(workspace) / 'compute-binding.json').read_bytes())
    for name, host in binding['hosts'].items():
        source = Path(host['request_file']); target = directory / 'workspace/helper-requests' / (name + '.json')
        target.parent.mkdir(mode=0o700, exist_ok=True)
        if not target.exists():
            with target.open('xb') as stream: os.fchmod(stream.fileno(), 0o600); stream.write(source.read_bytes())
        mapping[str(source)] = WORKSPACE + '/helper-requests/' + name + '.json'
    transform_documents(directory, mapping)
    # Inventory contains operator paths; reconstruct it on the receiving manager.
    (directory / 'workspace/hosts.ini').write_text('# Reconstructed from the authenticated infrastructure inventory.\n')
    if kolla.check(workspace) != before: raise InstallerError('Source workspace changed during snapshot preparation.')
    return directory


def bind_controller(module, cloud):
    original = module.ssh_arguments
    address = cloud.args.controller.partition('@')[2]
    def ssh(host):
        if 'control' in host['roles'] and host['address'] == address:
            return list(cloud.argv)
        return original(host)
    module.ssh_arguments = ssh


def copy_driver(source, target):
    """Only manifest-bound driver files; never incidental operator files/caches."""
    source = Path(source); target = Path(target); target.mkdir(mode=0o700)
    manifest_file = source/'manifest.json'; manifest = json.loads(manifest_file.read_bytes())
    files = {'manifest.json': file_hash(manifest_file), **manifest['source_sha256'],
             **{'wheels/'+name: sha for name, sha in manifest.get('wheel_sha256', {}).items()}}
    for relative, expected in files.items():
        store.name(relative); path = source/relative
        item = path.lstat()
        if path.resolve() != path or not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or file_hash(path) != expected:
            raise InstallerError('Driver snapshot source differs from the verified manifest.')
        destination = target/relative
        # Path.mkdir(parents=True) uses the process umask for intermediate
        # directories, not its explicit mode. Keep every private artifact
        # directory private even when the invoking shell uses umask 022.
        parent = target
        for part in destination.relative_to(target).parts[:-1]:
            parent = parent / part
            parent.mkdir(mode=0o700, exist_ok=True)
        with path.open('rb') as src, destination.open('xb') as dst:
            os.fchmod(dst.fileno(), 0o700 if item.st_mode & 0o100 else 0o600)
            shutil.copyfileobj(src, dst, 1024**2)
        if file_hash(path) != expected or file_hash(destination) != expected:
            raise InstallerError('Driver source changed during snapshot preparation.')
    if file_hash(manifest_file) != files['manifest.json']:
        raise InstallerError('Driver manifest changed during snapshot preparation.')


def upload(coordinator, staged, identity, *, expected=None, operation=None):
    """Stream a bounded private snapshot; commit under the existing held lock."""
    from openstack_remove_manager import lock_guard
    identifier = operation or uuid.uuid4().hex
    destination = '/var/lib/spvm/cloud-management-staging/' + identifier
    entries = store.tree(staged)
    header = (json.dumps(entries, sort_keys=True) + '\n').encode()
    if len(header) > 16 * 1024**2: raise InstallerError('Cloud snapshot manifest exceeds transfer limits.')
    code = remote_source('''
import sys
target = Path(TARGET)
parent = target.parent
for ancestor in parent.parents:
 st = ancestor.lstat()
 if ancestor.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022: raise ValueError('untrusted staging ancestor')
parent.mkdir(mode=0o700, exist_ok=True); private(parent, directory=True)
header = sys.stdin.buffer.readline(16*1024**2+1)
if not header.endswith(b'\\n') or len(header)>16*1024**2 or hashlib.sha256(header).hexdigest()!=HEADER_SHA: raise ValueError('invalid transfer manifest')
files = json.loads(header)
unpack(sys.stdin.buffer, target)
if tree(target) != files: raise ValueError('uploaded cloud snapshot differs')
exec(GUARD)
pointer = commit(ROOT, target, IDENTITY, expected=EXPECTED, operation=OPERATION, consume=True)
print(json.dumps(pointer))
''')
    code = (lock_guard(coordinator.lock) + 'TARGET=' + repr(destination) + '\nHEADER_SHA=' + repr(hashlib.sha256(header).hexdigest())
            + '\nIDENTITY=' + repr(identity) + '\nEXPECTED=' + repr(expected)
            + '\nOPERATION=' + repr(operation) + '\nGUARD=' + repr(lock_guard(coordinator.lock)) + '\n' + code)
    # The command line contains only the fixed program and non-secret hashes,
    # not passwords, keys or file contents. All snapshot bytes use SSH stdin.
    with (coordinator.output / 'private-cloud-publication.log').open('ab') as errors:
        os.fchmod(errors.fileno(), 0o600)
        process = subprocess.Popen(coordinator.argv + ['sudo -n /usr/bin/python3 -I -B -c ' + shlex.quote(code)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        try:
            coordinator.alive()
            process.stdin.write(header); process.stdin.flush()
            store.pack(staged, process.stdin); process.stdin.close()
            line = process.stdout.read(65537)
            if process.wait(timeout=60) != 0 or len(line) > 65536:
                raise InstallerError('Controller cloud snapshot was not committed; inspect private-cloud-publication.log. No replay.')
            pointer = json.loads(line); validate_identity(identity, pointer)
            coordinator.alive()
            return pointer
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            if process.stdin and not process.stdin.closed: process.stdin.close()
            process.stdout.close()


def publish_initial(i):
    """Fresh installs become READY only with a complete controller management kit."""
    from openstack_remove_manager import Coordinator, configuration_identity
    args = copy.copy(i.args); args.native_root = i.controller_root; args.host = i.controller
    coordinator = Coordinator(args, i.path, i.backend, i.cfg, i.output)
    identity = {'controller': i.controller, 'controller_machine': i.audits[i.controller]['host_identity_sha256'],
                'native_root': i.controller_root, 'api_endpoint': 'https://' + i.cfg['network']['api_vip'] + ':5000/v3',
                'api_ca_sha256': file_hash(Path(i.workspace) / 'kolla/certificates/ca/ca.crt'),
                'installation_plan_sha256': digest(json.loads((i.output / 'plan.json').read_bytes()))}
    with coordinator.locked():
        coordinator.snapshot()
        identity['project_id'] = coordinator.native['policy']['project_id']
        if configuration_identity(coordinator.native['config']) != configuration_identity(i.cfg):
            raise InstallerError('Controller configuration changed before initial cloud snapshot.')
        staged = snapshot(i.output / 'cloud-initial', workspace=Path(i.workspace), config=i.config,
                          base=Path(i.workspace).parent, driver=i.path.parent.parent, environment=i.environment, identity=identity)
        pointer = upload(coordinator, staged, identity)
    write(i.output / 'cloud-management.json', pointer)
    return pointer
