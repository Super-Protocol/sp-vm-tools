"""Private controller-owned cloud generations, not installation-history recovery.

This module is deliberately standard-library only. Its source is selected by the
operator's verified installer, never supplied by an unauthenticated API caller.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import uuid

ROOT = Path('/var/lib/spvm/cloud-management')
CAPABILITY = 'controller-cloud-management-v1'
MAX_FILES = 100000
MAX_BYTES = 32 * 1024**3


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            result.update(block)
    return result.hexdigest()


def private(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('canonical cloud-management path required')
    for parent in (path, *path.parents):
        item = parent.lstat()
        if stat.S_ISLNK(item.st_mode) or item.st_uid not in (0, os.geteuid()) or item.st_mode & 0o022:
            raise ValueError('untrusted cloud-management ownership or ancestor')
    item = path.stat()
    if item.st_uid != os.geteuid() or item.st_mode & 0o077 or (not directory and (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1)):
        raise ValueError('private cloud-management file/directory required')
    if directory and not stat.S_ISDIR(item.st_mode):
        raise ValueError('cloud-management directory required')
    return path


def read(path):
    path = private(path)
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError('cloud-management record exceeds size limit')
    return json.loads(path.read_bytes())


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    with temporary.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def name(value):
    part = PurePosixPath(value)
    if (not value or part.is_absolute() or str(part) != value or '..' in part.parts
            or '\\' in value or any(ord(c) < 32 for c in value)):
        raise ValueError('unsafe cloud-management archive member')
    return value


def tree(directory):
    """Hash regular files and empty directories; never follow links/devices."""
    directory = private(directory, directory=True)
    result = {}; total = 0
    for path in sorted(directory.rglob('*')):
        relative = name(path.relative_to(directory).as_posix())
        item = path.lstat()
        if item.st_uid != os.geteuid() or item.st_mode & 0o077:
            raise ValueError('non-private cloud-management tree')
        if stat.S_ISDIR(item.st_mode): result[relative] = {'kind': 'directory'}
        elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
            total += item.st_size
            result[relative] = {'kind': 'file', 'size': item.st_size,
                                'executable': bool(item.st_mode & stat.S_IXUSR), 'sha256': sha(path)}
        else: raise ValueError('links and special files are forbidden in cloud-management trees')
        if len(result) > MAX_FILES or total > MAX_BYTES:
            raise ValueError('cloud-management tree exceeds size limits')
    return result


def copy_tree(source, target):
    source = Path(source)
    if source.resolve() != source or not source.is_dir():
        raise ValueError('canonical source tree required')
    target = Path(target); target.mkdir(mode=0o700)
    members = sorted(source.rglob('*'))
    for path in members:
        destination = target / name(path.relative_to(source).as_posix())
        item = path.lstat()
        if stat.S_ISDIR(item.st_mode): destination.mkdir(mode=0o700)
        elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
            before = sha(path)
            with path.open('rb') as src, destination.open('xb') as dst:
                os.fchmod(dst.fileno(), 0o700 if item.st_mode & stat.S_IXUSR else 0o600)
                shutil.copyfileobj(src, dst, 1024**2)
                dst.flush(); os.fsync(dst.fileno())
            if sha(path) != before or sha(destination) != before:
                raise ValueError('source changed during cloud snapshot copying')
        else: raise ValueError('source symlink, hardlink or special file refused')
    if sorted(source.rglob('*')) != members:
        raise ValueError('source membership changed during cloud snapshot copying')


def pack(directory, stream):
    directory = Path(directory); before = tree(directory)
    with tarfile.open(fileobj=stream, mode='w|') as archive:
        for relative in before:
            archive.add(directory / relative, arcname=relative, recursive=False)
    if tree(directory) != before:
        raise ValueError('cloud-management source changed during transfer')


def unpack(stream, directory):
    """No extractall, links, ownership restoration, overwrite or partial cleanup."""
    directory = Path(directory); private(directory.parent, directory=True)
    directory.mkdir(mode=0o700)
    seen = set(); total = 0
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            relative = name(member.name)
            total += member.size
            if (relative in seen or len(seen) >= MAX_FILES or total > MAX_BYTES
                    or not (member.isfile() or member.isdir()) or member.size < 0):
                raise ValueError('invalid or oversized cloud-management transfer')
            seen.add(relative); destination = directory / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if member.isdir(): destination.mkdir(mode=0o700, exist_ok=True)
            else:
                if shutil.disk_usage(directory).free < member.size + 256 * 1024**2:
                    raise ValueError('insufficient space for cloud-management transfer')
                with archive.extractfile(member) as src, destination.open('xb') as dst:
                    os.fchmod(dst.fileno(), 0o700 if member.mode & 0o100 else 0o600)
                    shutil.copyfileobj(src, dst, 1024**2)
                    dst.flush(); os.fsync(dst.fileno())
    return directory


def verified_generation(root, pointer):
    if (pointer.get('schema_version') != 1 or pointer.get('capability') != CAPABILITY
            or type(pointer.get('generation')) is not int or pointer['generation'] < 1):
        raise ValueError('controller lacks a complete supported cloud-management contract')
    uuid.UUID(pointer['cloud_id'])
    generation = root / ('generation-%08d' % pointer['generation'])
    manifest = read(generation / 'manifest.json')
    if sha(generation / 'manifest.json') != pointer['manifest_sha256']:
        raise ValueError('cloud-management manifest changed')
    actual = tree(generation / 'payload')
    if actual != manifest['files'] or manifest['identity'] != pointer['identity']:
        raise ValueError('cloud-management generation is incomplete or corrupt')
    return generation


def current(root=ROOT):
    root = private(root, directory=True)
    pointer = read(root / 'current.json')
    generation = verified_generation(root, pointer)
    operation = read(root / 'operation.json')
    if operation.get('status') != 'COMMITTED' or operation.get('generation') != pointer['generation']:
        raise ValueError('unfinished cloud-management operation requires explicit review')
    return pointer, generation


def begin(root, expected, operation, kind, host, plan_sha256=None):
    pointer, _ = current(root)
    if pointer != expected: raise ValueError('cloud generation changed after approval')
    if not operation or kind not in ('add-compute', 'remove-compute'):
        raise ValueError('invalid cloud-management operation')
    write(Path(root) / 'operation.json', {'status': 'INFLIGHT', 'operation': operation,
          'kind': kind, 'host': host, 'plan_sha256': plan_sha256, 'before': pointer})


def commit(root, staged, identity, *, expected=None, operation=None, consume=False):
    """Caller holds the controller worker lock; partial commits fence maintenance."""
    root = Path(root); staged = private(staged, directory=True)
    if expected is None:
        if root.exists() or root.is_symlink(): raise ValueError('cloud-management state already exists; no adoption or migration')
        root.mkdir(mode=0o700)
        write(root / 'operation.json', {'status': 'INITIALIZING'})
        record = {'kind': 'install', 'host': None, 'operation': None,
                  'plan_sha256': identity.get('installation_plan_sha256')}
        cloud_id = str(uuid.uuid4()); number = 1
    else:
        private(root, directory=True)
        if read(root / 'current.json') != expected:
            raise ValueError('cloud-management generation changed')
        verified_generation(root, expected)
        record = read(root / 'operation.json')
        if record.get('status') != 'INFLIGHT' or record.get('operation') != operation or record.get('before') != expected:
            raise ValueError('cloud-management operation claim differs')
        cloud_id = expected['cloud_id']; number = expected['generation'] + 1
    destination = root / ('generation-%08d' % number)
    needed = sum(v.get('size', 0) for v in tree(staged).values())
    if shutil.disk_usage(root).free < (0 if consume else needed) + 256 * 1024**2:
        raise ValueError('insufficient controller space for durable cloud generation')
    destination.mkdir(mode=0o700)
    if consume: os.rename(staged, destination / 'payload')
    else: copy_tree(staged, destination / 'payload')
    # Make payload bytes and nested directory entries durable before publishing
    # a pointer. A partial disk failure leaves the operation fenced for review.
    payload = destination / 'payload'
    for path in sorted(payload.rglob('*')):
        if path.is_file():
            with path.open('rb') as stream: os.fsync(stream.fileno())
    for path in [*reversed([p for p in payload.rglob('*') if p.is_dir()]), payload]:
        fd = os.open(path, os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    # The generation retains the committed operation even after the global
    # operation/lock marker advances; this is not a local-history pointer.
    manifest = {'schema_version': 1, 'identity': identity, 'files': tree(destination / 'payload'),
                'operation': {**record, 'status': 'COMMITTED', 'generation': number}}
    write(destination / 'manifest.json', manifest)
    pointer = {'schema_version': 1, 'capability': CAPABILITY, 'cloud_id': cloud_id,
               'generation': number, 'identity': identity, 'manifest_sha256': sha(destination / 'manifest.json')}
    write(root / 'current.json', pointer)
    write(root / 'operation.json', {'status': 'COMMITTED', 'operation': operation, 'generation': number})
    current(root)
    return pointer
