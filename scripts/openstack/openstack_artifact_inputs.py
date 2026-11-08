"""Bounded local artifact and wheel checks; no image-building functions."""
import email.parser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile
import importlib.util

MAX_WHEEL=32*1024*1024
BASE_RE=re.compile(r'ghcr\.io/super-protocol/[a-z0-9][a-z0-9._-]*@sha256:[a-f0-9]{64}')
TAG_RE=re.compile(r'[a-z0-9][a-z0-9.:/_-]*:[a-z0-9][a-z0-9_.-]{0,127}')


def read(path,limit=4194304):
    path=Path(path)
    if path.resolve()!=path or not path.is_file() or path.stat().st_size>limit:
        raise ValueError('bounded regular non-symlink image input required')
    return path.read_bytes()


def driver_manifest(backend):
    """Use the selected kit's canonical source/generated metadata reader."""
    path = Path(backend).with_name('driver_metadata.py')
    read(path)
    definition = importlib.util.spec_from_file_location('selected_driver_metadata', path)
    module = importlib.util.module_from_spec(definition)
    definition.loader.exec_module(module)
    return module.manifest(path.parent.parent)


def wheel_bytes(backend,wheel,approved):
    body=read(wheel,MAX_WHEEL)
    if not re.fullmatch('[a-f0-9]{64}',str(approved)) or hashlib.sha256(body).hexdigest()!=approved:
        raise ValueError('explicit trusted wheel SHA256 required')
    base=Path(backend).parent.parent
    manifest_path=base/'manifest.json'
    if not manifest_path.exists():manifest_path=base/'nova-spvm/manifest.json'
    manifest=driver_manifest(backend)
    version=manifest.get('version','')
    if (manifest.get('schema_version')!=1 or manifest.get('name')!='nova-spvm'
            or not re.fullmatch(r'[0-9]+[.][0-9]+[.][0-9]+',version)):
        raise ValueError('versioned Nova driver manifest required')
    if 'wheel_sha256' in manifest and approved not in manifest['wheel_sha256'].values():
        raise ValueError('wheel not declared in selected driver bundle')
    expected={}
    for package in ('nova_spvm','openstack_spvm'):
        folder=base/'nova-spvm'/package
        for path in folder.rglob('*.py'):
            expected[path.relative_to(base/'nova-spvm').as_posix()]=read(path)
    if not any(p.startswith('nova_spvm/') for p in expected):raise ValueError('driver source missing')
    import io
    actual={};seen=set();total=0;metadata=None
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for item in archive.infolist():
            name=item.filename;part=PurePosixPath(name);total+=item.file_size
            if (len(seen)>=4096 or total>64*1024*1024 or item.file_size>4194304
                    or part.is_absolute() or '..' in part.parts or '\\' in name
                    or str(part)!=name or name in seen or item.is_dir()
                    or (item.external_attr>>16)&0o170000==0o120000):
                raise ValueError('unsafe driver wheel layout')
            seen.add(name)
            data=archive.read(item)
            if name in expected:actual[name]=data
            elif not name.startswith('nova_spvm-'+version+'.dist-info/'):
                raise ValueError('unexpected wheel payload')
            if name=='nova_spvm-'+version+'.dist-info/METADATA':metadata=data
    if actual!=expected or metadata is None:raise ValueError('wheel does not match selected driver sources')
    meta=email.parser.BytesParser().parsebytes(metadata)
    if meta['Name']!='nova-spvm' or meta['Version']!=version:raise ValueError('wheel package/version mismatch')
    return body,version,hashlib.sha256(read(manifest_path)).hexdigest()
