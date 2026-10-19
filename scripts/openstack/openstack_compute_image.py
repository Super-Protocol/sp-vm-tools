"""Build a local Nova driver image offline; never deploy or restart compute."""
import argparse
import email.parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

from openstack_container_engine import digest, file_hash, write

MAX_WHEEL=32*1024*1024
BASE_RE=re.compile(r'[a-z0-9][a-z0-9.:/_-]*@sha256:[a-f0-9]{64}')
TAG_RE=re.compile(r'[a-z0-9][a-z0-9.:/_-]*:[a-z0-9][a-z0-9_.-]{0,127}')
ENV={'PATH':'/usr/local/bin:/usr/bin:/bin','LC_ALL':'C','DOCKER_HOST':'unix:///var/run/docker.sock',
     'DOCKER_BUILDKIT':'1','DOCKER_CONFIG':'/nonexistent/spvm-offline-docker'}


def read(path,limit=4194304):
    path=Path(path)
    if path.resolve()!=path or not path.is_file() or path.stat().st_size>limit:
        raise ValueError('bounded regular non-symlink image input required')
    return path.read_bytes()


def wheel_bytes(backend,wheel,approved):
    body=read(wheel,MAX_WHEEL)
    if not re.fullmatch('[a-f0-9]{64}',str(approved)) or hashlib.sha256(body).hexdigest()!=approved:
        raise ValueError('explicit trusted wheel SHA256 required')
    base=Path(backend).parent.parent
    manifest_path=base/'manifest.json'
    if not manifest_path.exists():manifest_path=base/'nova-spvm/manifest.json'
    manifest=json.loads(read(manifest_path))
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


def stage(backend,args):
    if not BASE_RE.fullmatch(args.base_image):raise ValueError('registry image pinned by SHA256 digest required')
    wheel,version,manifest_sha=wheel_bytes(backend,args.wheel,args.wheel_sha256)
    deployment=Path(backend).parent
    template=read(deployment/'compute-image.Dockerfile.in').decode()
    if template.count('@WHEEL_SHA256@')!=1 or template.count('@DRIVER_VERSION@')!=4:
        raise ValueError('reviewed compute Dockerfile template required')
    dockerfile=template.replace('@WHEEL_SHA256@',args.wheel_sha256).replace('@DRIVER_VERSION@',version).encode()
    files={'Dockerfile':dockerfile,'driver.whl':wheel,'smoke.py':read(deployment/'kolla-driver-smoke.py')}
    output=Path(args.output)
    if not output.is_absolute() or output.parent.resolve()!=output.parent:
        raise ValueError('new absolute context with existing non-symlink parent required')
    output.mkdir(mode=0o700,exist_ok=False)
    for name,data in files.items():
        with (output/name).open('xb') as stream:stream.write(data)
        (output/name).chmod(0o600)
    receipt={'schema_version':1,'status':'COMPUTE_IMAGE_CONTEXT_STAGED_NOT_BUILT',
             'base_image':args.base_image,'driver_version':version,'manifest_sha256':manifest_sha,
             'files':{n:hashlib.sha256(b).hexdigest() for n,b in files.items()}}
    receipt['context_sha256']=digest(receipt)
    write(output/'image-context.json',receipt)
    return receipt


def context(output,approved):
    output=Path(output)
    if (not output.is_absolute() or output.resolve()!=output or not output.is_dir()
            or output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077):
        raise ValueError('private owned local image context required')
    receipt=json.loads(read(output/'image-context.json'))
    body={k:v for k,v in receipt.items() if k!='context_sha256'}
    if (set(receipt)!={'schema_version','status','base_image','driver_version','manifest_sha256','files','context_sha256'}
            or receipt['schema_version']!=1 or receipt['status']!='COMPUTE_IMAGE_CONTEXT_STAGED_NOT_BUILT'
            or not BASE_RE.fullmatch(receipt['base_image']) or approved!=digest(body)
            or receipt['context_sha256']!=approved or set(receipt['files'])!={'Dockerfile','driver.whl','smoke.py'}
            or {p.name for p in output.iterdir()}!={'Dockerfile','driver.whl','smoke.py','image-context.json'}):
        raise ValueError('approved immutable image context required')
    for name,expected in receipt['files'].items():
        if hashlib.sha256(read(output/name,MAX_WHEEL if name=='driver.whl' else 4194304)).hexdigest()!=expected:
            raise ValueError('image context drift')
    return receipt


def docker(argv,*,allowed=(0,)):
    result=subprocess.run(['docker',*argv],env=ENV,capture_output=True,timeout=60)
    if result.returncode not in allowed or len(result.stdout)>1048576:
        raise ValueError('local Docker inspection failed; output suppressed')
    return result


def build(args):
    receipt=context(args.output,args.context_sha256)
    if not TAG_RE.fullmatch(args.tag) or args.tag.rsplit(':',1)[-1] in ('latest','main','master'):
        raise ValueError('explicit non-floating local image tag required')
    # A missing image is established by listing tags, not by interpreting any
    # failed daemon connection as evidence that overwriting the tag is safe.
    tags=docker(['image','ls','--format','{{.Repository}}:{{.Tag}}']).stdout.decode().splitlines()
    if args.tag in tags:raise ValueError('image tag already exists; never replace a reviewed image')
    base=json.loads(docker(['image','inspect',receipt['base_image']]).stdout)
    if (len(base)!=1 or base[0].get('Os')!='linux' or base[0].get('Architecture')!='amd64'
            or not re.fullmatch('sha256:[a-f0-9]{64}',str(base[0].get('Id','')))
            or receipt['base_image'] not in base[0].get('RepoDigests',[])):
        raise ValueError('exact Linux amd64 base must already be present locally; no implicit pull')
    journal=Path(args.journal)
    if not journal.is_absolute() or journal.parent.resolve()!=journal.parent:
        raise ValueError('absolute new private journal directory required')
    output=Path(args.output)
    if journal.is_relative_to(output) or output.is_relative_to(journal):
        raise ValueError('image context and build journal must not overlap')
    journal.mkdir(mode=0o700,exist_ok=False)
    record={'schema_version':1,'status':'BUILDING_COMPUTE_IMAGE','context_sha256':args.context_sha256,
            'base_image':receipt['base_image'],'base_image_id':base[0]['Id'],'tag':args.tag,
            'containers_deployed':False,'guests_changed':False}
    write(journal/'image-build.json',record)
    try:
        with (journal/'private-build.log').open('xb') as log:
            os.chmod(log.name,0o600)
            result=subprocess.run(['docker','build','--pull=false','--network=none',
                '--build-arg','BASE_IMAGE='+receipt['base_image'],
                '--label','io.spvm.context-sha256='+args.context_sha256,
                '--tag',args.tag,str(Path(args.output))],env=ENV,
                stdout=log,stderr=log,timeout=1800)
        if result.returncode:raise ValueError('offline build failed; inspect private build log')
        context(args.output,args.context_sha256)
        image=json.loads(docker(['image','inspect',args.tag]).stdout)
        if (len(image)!=1 or image[0].get('Config',{}).get('User')!='nova'
                or not re.fullmatch('sha256:[a-f0-9]{64}',str(image[0].get('Id','')))
                or image[0].get('Config',{}).get('Labels',{}).get('io.spvm.context-sha256')!=args.context_sha256):
            raise ValueError('built image identity/runtime user failed acceptance')
        record.update(status='COMPUTE_IMAGE_BUILT_NOT_DEPLOYED',image_id=image[0]['Id'])
    except BaseException:
        record['status']='NEEDS_REVIEW_IMAGE_BUILD';write(journal/'image-build.json',record)
        raise
    write(journal/'image-build.json',record)
    return record


def main(backend,argsv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage-compute-image','build-compute-image'])
    parser.add_argument('--output',required=True,help='New context for staging, reviewed existing context for build')
    for name in ('base-image','wheel','wheel-sha256','context-sha256','tag','journal'):
        parser.add_argument('--'+name)
    args=parser.parse_args(argsv)
    try:
        if args.action=='stage-compute-image':
            if not all((args.base_image,args.wheel,args.wheel_sha256)) or any((args.context_sha256,args.tag,args.journal)):
                raise ValueError('staging requires only explicit base image and checksum-pinned wheel')
            result=stage(backend,args)
        else:
            if not all((args.context_sha256,args.tag,args.journal)) or any((args.base_image,args.wheel,args.wheel_sha256)):
                raise ValueError('build requires approved context, new tag and journal')
            result=build(args)
        print(json.dumps({k:v for k,v in result.items() if k in
            ('status','context_sha256','image_id','tag','containers_deployed','guests_changed')}))
        return 0
    except (OSError,ValueError,KeyError,zipfile.BadZipFile,subprocess.SubprocessError):
        print('ERROR: compute image preparation refused; private artifacts preserved. No compute was deployed.',file=sys.stderr)
        return 2
