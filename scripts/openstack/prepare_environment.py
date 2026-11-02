"""Prepare local, isolated installer/Kolla environments; never configure hosts."""
import argparse
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

PYYAML = '6.0.3'


class PreparationError(Exception):
    pass


def safe_text(text):
    # Installer output must not disclose credentials embedded in download URLs.
    return re.sub(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\x1b]+', '[URL hidden]', text)


def run(command, env=None, log=None, cwd=None):
    try:
        result = subprocess.run(command, env=env, cwd=cwd, capture_output=True,
                                text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if log:
            log.write(type(exc).__name__ + '\n')
            log.flush()
        raise PreparationError('Command could not complete; inspect the private log.') from None
    if log:
        log.write(safe_text(result.stdout) + safe_text(result.stderr))
        log.flush()
    if result.returncode:
        raise PreparationError('Command failed; inspect the private log if available.')
    return result.stdout


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def python_matches(spec, version):
    """Fail closed for unsupported metadata, without requiring packaging upfront."""
    if not isinstance(spec, str) or not spec:
        raise PreparationError('Kolla must declare its supported Python versions.')
    for part in spec.split(','):
        match = re.fullmatch(r'\s*(>=|<=|!=|==|>|<|~=)\s*(\d+(?:\.\d+){0,2})(\.\*)?\s*', part)
        if not match:
            raise PreparationError('Unsupported requires-python expression in Kolla metadata.')
        op, number, wildcard = match.groups()
        digits = tuple(int(n) for n in number.split('.'))
        target = (digits + (0, 0, 0))[:3]
        if wildcard:
            if op not in ('==', '!='):
                raise PreparationError('Unsupported Python wildcard constraint.')
            ok = version[:len(digits)] == digits
            ok = ok if op == '==' else not ok
        elif op == '~=':
            if len(digits) < 2:
                raise PreparationError('Invalid compatible Python constraint.')
            prefix = digits[:-1]
            ok = version >= target and version[:len(prefix)] == prefix
        else:
            ok = {'>=': version >= target, '<=': version <= target,
                  '>': version > target, '<': version < target,
                  '==': version == target, '!=': version != target}[op]
        if not ok:
            return False
    return True


def inspect_inputs(args):
    errors, packages = [], set()
    if sys.platform != 'linux':
        errors.append('Linux is required.')
    if os.geteuid() == 0:
        errors.append('Run as an unprivileged operator, not root.')
    if sys.version_info < (3, 11):
        errors.append('Python 3.11 or newer is required by this preparer.')
        packages.add('python3')
    for command, package in [('git', 'git'), ('ssh', 'openssh-client'),
                             ('scp', 'openssh-client'), ('openssl', 'openssl')]:
        if not shutil.which(command):
            errors.append('Missing executable: ' + command)
            packages.add(package)
    for module in ('venv', 'ensurepip', 'tomllib'):
        if importlib.util.find_spec(module) is None:
            errors.append('Missing Python module: ' + module)
            packages.add('python3-venv' if module != 'tomllib' else 'python3')
    if not re.fullmatch('[0-9a-f]{40}', args.kolla_revision):
        errors.append('--kolla-revision must be a full lowercase Git commit SHA.')
    source = Path(args.kolla_source).expanduser().resolve()
    directory = Path(args.directory).expanduser().absolute()
    if directory.resolve() != directory:
        errors.append('Environment path must not traverse symlinks or contain .. components.')
    parent = directory
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        errors.append('Environment parent is not a writable directory.')
    if directory == source or directory.is_relative_to(source) or source.is_relative_to(directory):
        errors.append('Environment and Kolla source directories must not contain one another.')
    if shutil.which('git'):
        try:
            head = run(['git', '-C', str(source), 'rev-parse', 'HEAD']).strip()
            dirty = run(['git', '--no-optional-locks', '-C', str(source), 'status', '--porcelain', '--untracked-files=all'])
            if head != args.kolla_revision or dirty:
                errors.append('Kolla checkout must be clean and match the exact requested commit.')
        except PreparationError:
            errors.append('Kolla source must be a readable Git checkout.')
    requirements = [source / 'requirements.yml', source / 'requirements-core.yml']
    for path in requirements:
        if not path.is_file():
            errors.append('Missing Kolla collections requirements file: ' + path.name)
    if importlib.util.find_spec('tomllib'):
        try:
            import tomllib
            metadata = tomllib.loads((source / 'pyproject.toml').read_text())
            spec = metadata.get('project', {}).get('requires-python')
            if not spec and (source / 'setup.cfg').is_file():
                config = configparser.ConfigParser(interpolation=None)
                config.read(source / 'setup.cfg')
                spec = config.get('options', 'python_requires', fallback=None)
            if not python_matches(spec, tuple(sys.version_info[:3])):
                errors.append('Selected Kolla requires Python ' + spec + '; select a matching Python interpreter.')
        except (OSError, ValueError, configparser.Error, PreparationError) as exc:
            errors.append('Cannot validate Kolla Python metadata (' + type(exc).__name__ + ').')
    if errors:
        message = '\n'.join('- ' + error for error in errors)
        try:
            ubuntu = __import__('platform').freedesktop_os_release().get('ID') == 'ubuntu'
        except OSError:
            ubuntu = False
        if ubuntu and packages:
            message += '\nAdministrator action example (NOT executed): sudo apt install ' + ' '.join(sorted(packages))
        raise PreparationError(message)
    constraints = [source / name for name in ('constraints.txt', 'upper-constraints.txt')
                   if (source / name).is_file()]
    inputs = {'kolla_source': str(source), 'kolla_revision': args.kolla_revision,
              'python': str(Path(sys.executable).resolve()),
              'python_version': list(sys.version_info[:3]), 'pyyaml': PYYAML,
              'preparer_sha256': sha(Path(__file__)),
              'requirements_sha256': {p.name: sha(p) for p in requirements + constraints}}
    return source, directory, inputs, constraints


def isolated_env(root):
    env = os.environ.copy()
    # Do not inherit interpreter, pip target, or Ansible search-path overrides.
    for key in list(env):
        if key.startswith(('PYTHON', 'PIP_', 'ANSIBLE_', 'VIRTUAL_ENV')):
            env.pop(key)
    env.update(PATH=str(root / 'kolla-venv/bin') + os.pathsep + env.get('PATH', ''),
               PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
               PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_NO_INPUT='1', PIP_NO_CACHE_DIR='1',
               ANSIBLE_COLLECTIONS_PATH=str(root / 'collections'),
               ANSIBLE_COLLECTIONS_SCAN_SYS_PATH='False',
               ANSIBLE_HOME=str(root / 'ansible-home'),
               ANSIBLE_CONFIG=str(root / 'ansible.cfg'))
    return env


def collection_state(root):
    if not (root / 'ansible_collections/openstack/kolla').is_dir():
        raise PreparationError('Required openstack.kolla collection is missing.')
    files, versions = {}, {}
    for path in sorted(root.rglob('*')):
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_relative_to(root) or not target.is_file():
                raise PreparationError('Unsafe collection symlink.')
            files[path.relative_to(root).as_posix()] = {
                'symlink_target': str(target.relative_to(root)), 'sha256': sha(target)}
        elif path.is_file():
            files[path.relative_to(root).as_posix()] = sha(path)
            if path.name == 'MANIFEST.json':
                info = json.loads(path.read_text())['collection_info']
                versions[info['namespace'] + '.' + info['name']] = info['version']
    # Match the installer's generic tree-digest encoding; approval of any
    # deployment-specific collection patch is a separate check.
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {'sha256': digest, 'versions': versions}


def validate(root, env, log):
    installed = {}
    for name, modules in [('installer-venv', 'yaml'), ('kolla-venv', 'kolla_ansible, ansible')]:
        python = str(root / name / 'bin/python')
        run([python, '-I', '-B', '-m', 'pip', 'check'], env, log)
        run([python, '-I', '-B', '-c', 'import ' + modules], env, log)
        installed[name] = json.loads(run([python, '-I', '-B', '-m', 'pip', 'list', '--format=json'], env, log))
    for command, option in [('kolla-ansible', '--version'), ('ansible-playbook', '--version'),
                            ('ansible-galaxy', '--version')]:
        run([str(root / 'kolla-venv/bin' / command), option], env, log, cwd=root)
    # Validate declarations and transitive metadata without calling Galaxy install.
    check = r'''
import json, pathlib, sys, yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version
root = pathlib.Path(sys.argv[1])
installed = {}
for path in (root / 'collections/ansible_collections').glob('*/*/MANIFEST.json'):
    info = json.loads(path.read_text())['collection_info']
    installed[info['namespace'] + '.' + info['name']] = info
requirements = []
for filename in ('requirements.yml', 'requirements-core.yml'):
    value = yaml.safe_load((root / 'kolla-source' / filename).read_text())
    for item in value['collections']:
        if isinstance(item, str):
            name, version = item, '*'
        else:
            name, version = item['name'], item.get('version', '*')
            if item.get('type') == 'git':
                if name.rstrip('/').removesuffix('.git') != 'https://opendev.org/openstack/ansible-collection-kolla':
                    raise ValueError('unsupported Git collection identity')
                name, version = 'openstack.kolla', '*'
        requirements.append((name, version))
for info in installed.values():
    requirements.extend(info.get('dependencies', {}).items())
for name, version in requirements:
    if name not in installed:
        raise ValueError('required collection missing')
    if version != '*' and str(version)[0].isdigit():
        version = '==' + str(version)
    if version != '*' and Version(installed[name]['version']) not in SpecifierSet(str(version)):
        raise ValueError('collection version does not satisfy requirements')
'''
    run([str(root / 'kolla-venv/bin/python'), '-I', '-B', '-c', check, str(root)], env, log)
    return {'packages': installed, 'collections': collection_state(root / 'collections')}


def prepare(args):
    source, root, inputs, constraints = inspect_inputs(args)
    if root.exists():
        if root.stat().st_uid != os.geteuid() or root.stat().st_mode & 0o077:
            raise PreparationError('Existing environment must be operator-owned with mode 0700.')
        receipt = root / 'environment.json'
        if not receipt.is_file():
            raise PreparationError('Existing/incomplete directory: choose a new --directory; no automatic repair.')
        old = json.loads(receipt.read_text())
        if not isinstance(old, dict) or old.get('status') != 'READY' or old.get('inputs') != inputs:
            raise PreparationError('Environment inputs differ: choose a new --directory.')
        if args.check:
            print('Prerequisites and recorded inputs match; --check did not validate installed packages.')
            return
        actual = validate(root, isolated_env(root), None)
        if actual != old.get('installed'):
            raise PreparationError('Installed environment changed; choose a new --directory.')
    elif args.check:
        print('Prerequisites passed. No directories created or packages downloaded.')
        return
    else:
        root.mkdir(parents=True, mode=0o700)
        os.chmod(root, 0o700)
        env = isolated_env(root)
        with (root / 'prepare.log').open('x') as log:
            os.chmod(log.name, 0o600)
            (root / 'ansible.cfg').write_text('[defaults]\ncollections_path = ' + str(root / 'collections') + '\n')
            # Build tools may write into their input; never install from the operator checkout.
            run(['git', 'clone', '--no-hardlinks', '--no-checkout', str(source), str(root / 'kolla-source')], env, log)
            run(['git', '-C', str(root / 'kolla-source'), 'checkout', '--detach', args.kolla_revision], env, log)
            for name in ('installer-venv', 'kolla-venv'):
                run([sys.executable, '-I', '-B', '-m', 'venv', str(root / name)], env, log)
            run([str(root / 'installer-venv/bin/python'), '-I', '-B', '-m', 'pip',
                 'install', 'PyYAML==' + PYYAML], env, log)
            command = [str(root / 'kolla-venv/bin/python'), '-I', '-B', '-m', 'pip', 'install']
            for path in constraints:
                command += ['--constraint', str(root / 'kolla-source' / path.name)]
            run(command + [str(root / 'kolla-source')], env, log)
            for filename in ('requirements.yml', 'requirements-core.yml'):
                run([str(root / 'kolla-venv/bin/ansible-galaxy'), 'collection', 'install',
                     '-r', str(root / 'kolla-source' / filename),
                     '-p', str(root / 'collections')], env, log, cwd=root)
            installed = validate(root, env, log)
            # Catch source edits that happened during preparation.
            if inspect_inputs(args)[2] != inputs:
                raise PreparationError('Source inputs changed during preparation.')
            receipt = {'schema_version': 1, 'status': 'READY', 'inputs': inputs, 'installed': installed}
            (root / 'environment.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
            os.chmod(root / 'environment.json', 0o600)
    print('Local environments READY (OpenStack is NOT deployed).')
    print('export SPVM_INSTALLER_PYTHON=' + shlex.quote(str(root / 'installer-venv/bin/python')))
    print('export ANSIBLE_COLLECTIONS_PATH=' + shlex.quote(str(root / 'collections')))
    print('Kolla Python: ' + str(root / 'kolla-venv/bin/python'))
    print('Kolla source: ' + str(root / 'kolla-source'))
    print('Receipt: ' + str(root / 'environment.json'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--kolla-source', required=True)
    parser.add_argument('--kolla-revision', required=True)
    parser.add_argument('--check', action='store_true', help='Check inputs only; no writes or downloads')
    args = parser.parse_args(argv)
    try:
        prepare(args)
    except (PreparationError, OSError, ValueError, KeyError) as exc:
        # Unexpected OS/metadata errors may embed input content: expose type only.
        message = str(exc) if isinstance(exc, PreparationError) else type(exc).__name__
        print('ERROR: ' + safe_text(message), file=sys.stderr)
        print('No automatic cleanup/repair. If creation started, inspect prepare.log in --directory.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
