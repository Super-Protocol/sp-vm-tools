"""Prepare private Kolla collections without modifying the operator environment."""
import hashlib
import os
from pathlib import Path
import stat

from openstack_container_engine import collection_hash, digest
from openstack_errors import InstallerError

ROLE='ansible_collections/openstack/kolla/roles/docker/tasks/main.yml'
PATCH_SHA256='310421d0c12dbf579612aa40b361feaef8ba89939491abe8a8ffd5885d7bab2b'
ORIGINAL=b'---\n- name: Install/Uninstall\n  ansible.builtin.include_tasks: "{{ package_action }}.yml"\n'


def regular(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>32*1024*1024:
            raise InstallerError(f'Collections contain an unsupported or oversized file: {str(path)!r}.')
        data=stream.read(32*1024*1024+1);after=os.fstat(stream.fileno())
    if (len(data)>32*1024*1024 or (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)
            !=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
        raise InstallerError('Collections changed while reading a file. Use an unchanged source environment and a new workspace.')
    return data,bool(before.st_mode & stat.S_IXUSR)


def scan(root,*,private=False):
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root or not root.is_dir():
        raise InstallerError('Collections source must be an existing canonical absolute directory.')
    if private and (root.stat().st_uid!=os.getuid() or stat.S_IMODE(root.stat().st_mode)!=0o700):
        raise InstallerError('Prepared collections root must remain privately owned with mode 0700.')
    entries={};size=0
    def failed(error):raise InstallerError('Collections cannot be fully read; check local permissions.') from None
    for parent,dirs,files in os.walk(root,followlinks=False,onerror=failed):
        for name in dirs:
            if (Path(parent)/name).is_symlink():
                raise InstallerError(f'Collections directory symlink is not supported: {str(Path(parent)/name)!r}.')
        dirs[:]=sorted(n for n in dirs if n!='__pycache__')
        for path in [*(Path(parent)/n for n in dirs),*(Path(parent)/n for n in sorted(files))]:
            if path.suffix in ('.pyc','.pyo'):continue
            relative=path.relative_to(root).as_posix();info=path.lstat()
            if private and (info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode) not in (0o600,0o700)):
                raise InstallerError('Prepared collections ownership or private permissions changed.')
            if stat.S_ISDIR(info.st_mode):entries[relative]={'kind':'directory'}
            else:
                target=path
                link=None
                if stat.S_ISLNK(info.st_mode):
                    try:target=path.resolve(strict=True)
                    except (OSError,RuntimeError):raise InstallerError(f'Invalid or cyclic collection symlink: {str(path)!r}.') from None
                    if not target.is_relative_to(root) or not target.is_file():
                        raise InstallerError(f'Collection symlink must point to a regular file inside its source tree: {str(path)!r}.')
                    link=os.readlink(path)
                elif not stat.S_ISREG(info.st_mode):
                    raise InstallerError(f'Collections contain a special file: {str(path)!r}.')
                data,executable=regular(target);size+=len(data)
                entries[relative]={'kind':'file','sha256':hashlib.sha256(data).hexdigest(),'executable':executable}
                if link is not None:entries[relative]['symlink']=link
            if len(entries)>100000 or size>1024*1024*1024:
                raise InstallerError('Collections exceed the bounded preparation limit (100,000 entries / 1 GiB).')
    return entries


def write_private(path,data,executable=False):
    missing=[];parent=path.parent
    while not parent.exists():missing.append(parent);parent=parent.parent
    for directory in reversed(missing):directory.mkdir(mode=0o700)
    with path.open('xb') as output:
        os.fchmod(output.fileno(),0o700 if executable else 0o600)
        output.write(data)


def prepare(source,workspace,patch):
    source=Path(source);workspace=Path(workspace);target=workspace/'collections';patch=Path(patch)
    if source.is_relative_to(workspace) or workspace.is_relative_to(source):
        raise InstallerError('Source collections and the new workspace must not overlap.')
    before=scan(source)
    if ROLE not in before or regular((source/ROLE).resolve(strict=True))[0]!=ORIGINAL:
        raise InstallerError(f'Unsupported or already modified Kolla Docker role: {str(source/ROLE)!r}. '
            'Use the original unmodified collections from prepare-environment in a new workspace.')
    patch_body=regular(patch)[0]
    if hashlib.sha256(patch_body).hexdigest()!=PATCH_SHA256:
        raise InstallerError('Selected driver contains an unsupported Docker-preservation patch. Select matching reviewed components; no fuzzy patching is allowed.')
    lines=patch_body.split(b'@@ -1,3 +1,12 @@\n',1)[1].splitlines(keepends=True)
    original=b''.join(line[1:] for line in lines if line[:1] in (b' ',b'-'))
    modified=b''.join(line[1:] for line in lines if line[:1] in (b' ',b'+'))
    if original!=ORIGINAL:raise InstallerError('Pinned Docker patch does not match the supported original role.')
    target.mkdir(mode=0o700,exist_ok=False)
    for relative,entry in sorted(before.items()):
        destination=target/relative
        if entry['kind']=='directory':destination.mkdir(mode=0o700,parents=True,exist_ok=True)
        else:
            data,executable=regular((source/relative).resolve(strict=True))
            if hashlib.sha256(data).hexdigest()!=entry['sha256'] or executable!=entry['executable']:
                raise InstallerError('Collections changed during copying; partial workspace preserved. Do not retry into it.')
            write_private(destination,data,executable)
    if scan(source)!=before:
        raise InstallerError('Source collections changed during preparation; partial workspace preserved.')
    role=target/ROLE
    with role.open('wb') as output:output.write(modified)
    prepared=collection_hash(target)
    if regular(patch)[0]!=patch_body or scan(source)!=before:
        raise InstallerError('Source collections or driver patch changed during preparation; partial workspace preserved.')
    provenance={'schema_version':1,'source':str(source),'source_tree_sha256':digest(before),
        'patch_sha256':PATCH_SHA256,'original_role_sha256':hashlib.sha256(ORIGINAL).hexdigest(),
        'prepared_tree_sha256':digest(scan(target,private=True))}
    return str(target),prepared,provenance


def verify(workspace,receipt):
    target=Path(workspace)/'collections';provenance=receipt.get('collections_preparation')
    if (receipt.get('collections')!=str(target) or not isinstance(provenance,dict)
            or provenance.get('schema_version')!=1 or provenance.get('patch_sha256')!=PATCH_SHA256
            or provenance.get('prepared_tree_sha256')!=digest(scan(target,private=True))):
        raise InstallerError('Workspace lacks matching automatically prepared collections or they changed. Create a new workspace from the original environment; no migration or repair is performed.')
    collection_hash(target,receipt['collections_sha256'])


def copy_workspace_file(origin,output,name):
    """Keep prepared collection executables when extending a private workspace."""
    data,executable=regular(Path(origin)/name)
    write_private(Path(output)/name,data,executable if name.startswith('collections/') else False)
