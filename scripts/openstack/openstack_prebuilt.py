"""Consume a coordinated CI kit. No Docker on the management machine."""
import importlib.util
import inspect
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

from openstack_container_engine import file_hash
from openstack_errors import InstallerError
import openstack_progress as progress
from openstack_image_identity import pinned_image_id


def backend_module(backend):
    path = Path(backend).with_name('prebuilt_images.py')
    spec = importlib.util.spec_from_file_location('selected_prebuilt_images', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selected(backend, *, online=False):
    root = Path(backend).parent.parent
    try:
        module = backend_module(backend)
        kit = module.validate(root, module.document(root / 'manifest.json'))
        if online:
            for component, image in kit['images'].items():
                with progress.step('verify-image-' + component):
                    labels = ({'io.spvm.source-commit': kit['sources']['spvm-openstack'],
                               'io.spvm.wheel-sha256': kit['driver']['wheel_sha256']}
                              if component == 'compute' else
                              {'org.opencontainers.image.revision': kit['sources']['skyline-console']})
                    module.inspect(image, labels)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise InstallerError('Prebuilt installation kit verification failed: ' + str(error)
            + '. Select a complete matching prebuilt-openstack-images-v1 CI kit; no migration or local build is performed.') from None
    return kit


def file(backend, path, expected):
    root = Path(backend).parent.parent
    path = Path(path)
    if path != root / 'installation-kit.json' or file_hash(path) != expected:
        raise InstallerError('Installation kit path or SHA256 differs from the selected driver.')
    return selected(backend)


def verify_installer(kit):
    """Check actual manager source bytes, not just a claimed Git HEAD."""
    root = Path(__file__).resolve().parents[2]
    revision = kit['sources']['sp-vm-tools']
    git = ['git', '-c', 'safe.directory=' + str(root), '-C', str(root)]
    try:
        head = subprocess.check_output([*git, 'rev-parse', 'HEAD'], timeout=30).decode().strip()
        if head != revision:
            raise ValueError('Installer source revision differs')
        tree = subprocess.check_output([*git, 'ls-tree', '-rz', revision, '--',
                                        'scripts/openstack', 'scripts/spvm-openstack.sh'], timeout=30)
        wanted = {}
        for item in tree.split(b'\0'):
            if not item: continue
            meta, name = item.split(b'\t', 1)
            mode, kind, oid = meta.split()
            name = name.decode()
            if name.endswith('.py') or name == 'scripts/spvm-openstack.sh':
                if mode not in (b'100644', b'100755') or kind != b'blob':
                    raise ValueError('Regular installer sources required')
                wanted[name] = oid.decode()
        actual = {'scripts/openstack/' + p.name for p in (root/'scripts/openstack').glob('*.py')}
        actual.add('scripts/spvm-openstack.sh')
        if head != revision or actual != set(wanted):
            raise ValueError('Installer source revision/member set differs')
        for name, expected in wanted.items():
            path = root / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Regular installer source required')
            body = path.read_bytes()
            if hashlib.sha1(b'blob ' + str(len(body)).encode() + b'\0' + body).hexdigest() != expected:
                raise ValueError('Installer source bytes differ')
    except (OSError, ValueError, subprocess.SubprocessError):
        raise InstallerError('Use the unchanged sp-vm-tools commit ' + revision
            + ' recorded in the selected CI kit; mixed installer components are not supported.') from None


def pull(backend, host, kit, component, machine, output, *, reserve_gib=10):
    """Use the existing operator SSH transport to pull only on this target."""
    output = Path(output)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    image = kit['images'][component]
    expected = {'machine': machine, 'image': image, 'reserve_gib': reserve_gib}
    code = inspect.getsource(pinned_image_id) + '\nWANTED=' + repr(expected) + '\n' + '''
import hashlib,json,shutil,subprocess
from pathlib import Path
if hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()!=WANTED['machine']:
 raise ValueError('image target machine changed')
if type(WANTED['reserve_gib']) is not int or WANTED['reserve_gib']<1:
 raise ValueError('positive Docker storage reserve required')
info=json.loads(subprocess.check_output(['docker','info','--format','{{json .DockerRootDir}}'],timeout=30))
if shutil.disk_usage(info).free<WANTED['reserve_gib']*1024**3:
 raise ValueError('insufficient Docker storage reserve')
image=WANTED['image'];reference=image['reference']
result=subprocess.run(['docker','pull','--platform=linux/amd64',reference],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=1800)
if result.returncode:raise ValueError('pinned image download failed')
actual=json.loads(subprocess.check_output(['docker','image','inspect',reference],timeout=30))
if len(actual)!=1:
 raise ValueError('downloaded image identity/platform differs from the kit')
runtime_id=pinned_image_id(actual[0],reference,image['config_id'])
if shutil.disk_usage(info).free<WANTED['reserve_gib']*1024**3:
 raise ValueError('Docker storage reserve exhausted; image retained for review')
print(json.dumps({'status':'PREBUILT_IMAGE_PULLED_NOT_DEPLOYED','reference':reference,'image_id':image['config_id'],'runtime_id':runtime_id}))
'''
    spec = importlib.util.spec_from_file_location('image_pull_transport', backend)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    argv = module.ssh_arguments(host) + ['sudo -n /usr/bin/python3 -I -B -c ' + shlex.quote(code)]
    log = output / 'private-image-pull.log'
    with progress.step('pull-image-' + component, log=log), log.open('xb') as errors:
        os.fchmod(errors.fileno(), 0o600)
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=errors, timeout=2100)
    if result.returncode or len(result.stdout) > 65536:
        raise InstallerError('Pinned ' + component + ' image retrieval failed; inspect ' + str(log) + '. No automatic retry.')
    receipt = json.loads(result.stdout)
    if (receipt.get('status') != 'PREBUILT_IMAGE_PULLED_NOT_DEPLOYED'
            or receipt.get('image_id') != image['config_id'] or receipt.get('reference') != image['reference']
            or receipt.get('runtime_id') not in (image['config_id'],image['reference'].split('@')[1])):
        raise InstallerError('Target did not confirm the exact prebuilt image identity.')
    return receipt
