"""Export/import a checksum-approved local compute image. Never activate containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import shlex
import subprocess
import sys
import tarfile

from openstack_compute_image import ENV, TAG_RE, docker, read
from openstack_container_engine import digest, file_hash, write

MAX_ARCHIVE=64*1024**3
IMAGE_RE=re.compile('sha256:[a-f0-9]{64}')
SHA_RE=re.compile('[a-f0-9]{64}')


def private_directory(path,*,new=False):
    path=Path(path)
    if not path.is_absolute() or path.parent.resolve()!=path.parent:
        raise ValueError('absolute non-symlink directory required')
    if new:path.mkdir(mode=0o700,parents=False,exist_ok=False)
    if (path.resolve()!=path or not path.is_dir() or path.stat().st_uid!=os.getuid()
            or path.stat().st_mode & 0o077):
        raise ValueError('private owned directory required')
    return path


def inspect(tag):
    images=json.loads(docker(['image','inspect',tag]).stdout)
    if len(images)!=1:raise ValueError('one local image required')
    item=images[0];config=item.get('Config',{})
    context=config.get('Labels',{}).get('io.spvm.context-sha256','')
    if (not IMAGE_RE.fullmatch(str(item.get('Id',''))) or item.get('Os')!='linux'
            or item.get('Architecture')!='amd64' or config.get('User')!='nova'
            or not SHA_RE.fullmatch(context) or type(item.get('Size')) is not int or item['Size']<1):
        raise ValueError('accepted Linux amd64 Nova image identity required')
    return {'image_id':item['Id'],'context_sha256':context,'size':item['Size']}


def enough_space(path,size,reserve):
    if type(reserve) is not int or reserve<1 or shutil.disk_usage(path).free<size+reserve*1024**3:
        raise ValueError('insufficient disk space or invalid explicit reserve')


def archive_layout(path,tag):
    # docker load can assign all tags in an archive. Refuse unrelated images/tags
    # before handing it to the privileged daemon. Nested layer tar blobs are not
    # extracted by this installer; Docker verifies its own content-addressed data.
    seen=set();total=0;manifest=None
    with tarfile.open(path,'r:') as archive:
        for item in archive:
            name=PurePosixPath(item.name);total+=item.size
            if (len(seen)>=65536 or total>MAX_ARCHIVE or name.is_absolute()
                    or '..' in name.parts or '\\' in item.name or not name.parts
                    or name.as_posix()!=item.name.rstrip('/') or name.as_posix() in seen
                    or not (item.isfile() or item.isdir())):
                raise ValueError('unsafe or oversized Docker archive layout')
            seen.add(name.as_posix())
            if item.name=='manifest.json':
                if item.size>1048576:raise ValueError('bounded Docker manifest required')
                manifest=json.load(archive.extractfile(item))
            if item.name in ('index.json',):
                if item.size>1048576:raise ValueError('bounded OCI index required')
                index=json.load(archive.extractfile(item))
                for descriptor in index.get('manifests',[]):
                    annotations=descriptor.get('annotations',{})
                    for key in ('io.containerd.image.name','org.opencontainers.image.ref.name'):
                        value=annotations.get(key)
                        allowed=(tag,'docker.io/'+tag)
                        if key=='org.opencontainers.image.ref.name':allowed+= (tag.rsplit(':',1)[-1],)
                        if value and value not in allowed:
                            raise ValueError('unrelated OCI archive tag refused')
    if (not isinstance(manifest,list) or len(manifest)!=1
            or manifest[0].get('RepoTags') not in ([tag],['docker.io/'+tag])):
        raise ValueError('Docker archive must contain exactly the approved image tag')
    for member in [manifest[0].get('Config'),*manifest[0].get('Layers',[])]:
        if not isinstance(member,str) or member not in seen:
            raise ValueError('Docker archive manifest references missing content')


def export(args):
    raw=read(args.build_receipt)
    if not SHA_RE.fullmatch(str(args.build_sha256)) or hashlib.sha256(raw).hexdigest()!=args.build_sha256:
        raise ValueError('approved build receipt SHA256 required')
    record=json.loads(raw)
    tag=record.get('tag','')
    if (record.get('schema_version')!=1 or record.get('status')!='COMPUTE_IMAGE_BUILT_NOT_DEPLOYED'
            or not TAG_RE.fullmatch(tag) or not IMAGE_RE.fullmatch(str(args.image_id))
            or record.get('image_id')!=args.image_id or not SHA_RE.fullmatch(str(record.get('context_sha256')))):
        raise ValueError('successful approved build and image ID required')
    before=inspect(tag)
    if before['image_id']!=args.image_id or before['context_sha256']!=record['context_sha256']:
        raise ValueError('local image drift since build')
    parent=Path(args.output).parent
    enough_space(parent,before['size']*2,args.reserve_gib)
    output=private_directory(args.output,new=True)
    journal={'schema_version':1,'status':'EXPORTING_COMPUTE_IMAGE','tag':tag,
             'image_id':args.image_id,'containers_deployed':False,'guests_changed':False}
    write(output/'export-journal.json',journal)
    try:
        archive=output/'image.tar'
        with archive.open('xb') as stream,(output/'private-export.log').open('xb') as log:
            os.chmod(archive,0o600);os.chmod(log.name,0o600)
            result=subprocess.run(['docker','image','save',tag],env=ENV,stdout=stream,stderr=log,timeout=1800)
            stream.flush();os.fsync(stream.fileno())
        if result.returncode or not 0<archive.stat().st_size<=MAX_ARCHIVE:
            raise ValueError('Docker export failed or exceeds archive limit')
        archive_layout(archive,tag)
        if inspect(tag)!=before:raise ValueError('image changed during export')
        receipt={'schema_version':1,'status':'COMPUTE_IMAGE_EXPORTED_NOT_DEPLOYED',
            'tag':tag,'image_id':args.image_id,'context_sha256':before['context_sha256'],
            'image_size':before['size'],'archive_size':archive.stat().st_size,
            'archive_sha256':file_hash(archive),'build_receipt_sha256':args.build_sha256}
        receipt['package_sha256']=digest(receipt)
        write(output/'image-package.json',receipt)
        journal.update(status=receipt['status'],package_sha256=receipt['package_sha256'])
    except BaseException:
        journal['status']='NEEDS_REVIEW_IMAGE_EXPORT';write(output/'export-journal.json',journal)
        raise
    write(output/'export-journal.json',journal)
    return receipt


def package(path,approved):
    path=private_directory(path)
    receipt=json.loads(read(path/'image-package.json'))
    body={k:v for k,v in receipt.items() if k!='package_sha256'}
    if (set(receipt)!={'schema_version','status','tag','image_id','context_sha256','image_size',
            'archive_size','archive_sha256','build_receipt_sha256','package_sha256'}
            or receipt['schema_version']!=1 or receipt['status']!='COMPUTE_IMAGE_EXPORTED_NOT_DEPLOYED'
            or not TAG_RE.fullmatch(receipt['tag']) or not IMAGE_RE.fullmatch(receipt['image_id'])
            or any(not SHA_RE.fullmatch(receipt[k]) for k in ('context_sha256','archive_sha256','build_receipt_sha256'))
            or type(receipt['image_size']) is not int or not 0<receipt['image_size']<=MAX_ARCHIVE
            or type(receipt['archive_size']) is not int or not 0<receipt['archive_size']<=MAX_ARCHIVE
            or approved!=digest(body) or receipt['package_sha256']!=approved):
        raise ValueError('approved versioned image package required')
    archive=path/'image.tar'
    if (archive.resolve()!=archive or not archive.is_file() or archive.stat().st_size!=receipt['archive_size']
            or file_hash(archive)!=receipt['archive_sha256']):
        raise ValueError('image archive checksum/size mismatch')
    archive_layout(archive,receipt['tag'])
    return receipt


def load(args):
    identity=hashlib.sha256(Path('/etc/machine-id').read_text().strip().encode()).hexdigest()
    if not SHA_RE.fullmatch(str(args.machine_sha256)) or identity!=args.machine_sha256:
        raise ValueError('explicit matching target machine fingerprint required')
    receipt=package(args.package,args.package_sha256)
    tags=docker(['image','ls','--format','{{.Repository}}:{{.Tag}}']).stdout.decode().splitlines()
    if receipt['tag'] in tags:
        existing=inspect(receipt['tag'])
        if existing['image_id']!=receipt['image_id'] or existing['context_sha256']!=receipt['context_sha256']:
            raise ValueError('existing destination tag belongs to a different image')
        return {'status':'COMPUTE_IMAGE_ALREADY_PRESENT_NOT_DEPLOYED','image_id':existing['image_id'],
                'containers_deployed':False,'guests_changed':False}
    journal=Path(args.output)
    source=Path(args.package)
    if journal.is_relative_to(source) or source.is_relative_to(journal):
        raise ValueError('import journal must not overlap package')
    docker_root=json.loads(docker(['info','--format','{{json .DockerRootDir}}']).stdout)
    root=Path(docker_root)
    if not root.is_absolute() or root.resolve()!=root or not root.is_dir():
        raise ValueError('local Docker data filesystem required')
    enough_space(root,receipt['image_size'],args.reserve_gib)
    journal=private_directory(journal,new=True)
    record={'schema_version':1,'status':'IMPORTING_COMPUTE_IMAGE','machine_sha256':identity,
            'package_sha256':args.package_sha256,'tag':receipt['tag'],'image_id':receipt['image_id'],
            'containers_deployed':False,'guests_changed':False}
    write(journal/'import-journal.json',record)
    try:
        with (source/'image.tar').open('rb') as stream,(journal/'private-import.log').open('xb') as log:
            os.chmod(log.name,0o600)
            result=subprocess.run(['docker','image','load'],env=ENV,stdin=stream,stdout=log,stderr=log,timeout=1800)
        if result.returncode:raise ValueError('Docker load failed; review private journal')
        actual=inspect(receipt['tag'])
        if actual['image_id']!=receipt['image_id'] or actual['context_sha256']!=receipt['context_sha256']:
            raise ValueError('imported image ID/context does not match approved source')
        record['status']='COMPUTE_IMAGE_IMPORTED_NOT_DEPLOYED'
    except BaseException:
        record['status']='NEEDS_REVIEW_IMAGE_IMPORT';write(journal/'import-journal.json',record)
        raise
    write(journal/'import-journal.json',record)
    return record


def deliver(args):
    receipt=package(args.package,args.package_sha256)
    if (not re.fullmatch(r'[a-z_][a-z0-9_-]*@[a-z0-9][a-z0-9.-]*',str(args.host))
            or not SHA_RE.fullmatch(str(args.machine_sha256))
            or type(args.ssh_port) is not int or not 1<=args.ssh_port<=65535
            or type(args.reserve_gib) is not int or args.reserve_gib<1):
        raise ValueError('explicit SSH target, fingerprint and valid port/reserve required')
    for path in (args.identity_file,args.known_hosts):
        if not read(path):raise ValueError('explicit nonempty SSH identity and trusted known-hosts file required')
    target=args.host
    options=['-F','/dev/null','-i',args.identity_file,'-o','IdentityAgent=none','-o','IdentitiesOnly=yes',
             '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+args.known_hosts,
             '-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
             '-o','ForwardAgent=no','-o','ClearAllForwardings=yes']
    output=Path(args.output)
    if output.is_relative_to(Path(args.package)) or Path(args.package).is_relative_to(output):
        raise ValueError('delivery journal must be outside package')
    output=private_directory(output,new=True)
    record={'schema_version':1,'status':'DELIVERING_COMPUTE_IMAGE','host':target,
        'machine_sha256':args.machine_sha256,'package_sha256':args.package_sha256,
        'image_id':receipt['image_id'],'containers_deployed':False,'guests_changed':False}
    write(output/'delivery-journal.json',record)
    def run(argv):
        with (output/'private-transport.log').open('ab') as log:
            os.chmod(log.name,0o600)
            result=subprocess.run(argv,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'},
                stdout=subprocess.PIPE,stderr=log,timeout=1800)
        if result.returncode or len(result.stdout)>1048576:raise ValueError('SSH/SCP delivery failed; private log preserved')
        return result.stdout
    def ssh(argv):return run(['ssh',*options,'-p',str(args.ssh_port),target,shlex.join(argv)])
    try:
        # Fingerprint and capacity are verified BEFORE creating/uploading anything.
        probe="import hashlib,json,shutil;from pathlib import Path;print(json.dumps({'machine':hashlib.sha256(Path('/etc/machine-id').read_text().strip().encode()).hexdigest(),'free':shutil.disk_usage('/var/tmp').free}))"
        actual=json.loads(ssh(['python3','-I','-c',probe]))
        if (actual['machine']!=args.machine_sha256
                or actual['free']<receipt['archive_size']+args.reserve_gib*1024**3):
            raise ValueError('wrong remote machine or insufficient archive staging capacity')
        remote=ssh(['python3','-I','-c',"import tempfile;print(tempfile.mkdtemp(prefix='spvm-image-receive-',dir='/var/tmp'))"]).decode().strip()
        if not re.fullmatch('/var/tmp/spvm-image-receive-[a-zA-Z0-9_-]{6,32}',remote):
            raise ValueError('unexpected remote staging path')
        record['remote_staging']=remote;write(output/'delivery-journal.json',record)
        modules=[Path(__file__).with_name(n) for n in
                 ('openstack_image_transfer.py','openstack_compute_image.py','openstack_container_engine.py')]
        files=[Path(args.package)/'image.tar',Path(args.package)/'image-package.json',*modules]
        hashes={p.name:file_hash(p) for p in files}
        run(['scp',*options,'-P',str(args.ssh_port),*[str(p) for p in files],target+':'+remote+'/'])
        # Verify received bytes before importing staged Python as root. No keys,
        # provider config or OpenStack credentials are copied to the destination.
        prepare=("import hashlib,os;from pathlib import Path;p=Path("+repr(remote)+");expected="+repr(hashes)+
            ";assert p.is_dir() and not p.is_symlink();assert {x.name for x in p.iterdir()}==set(expected);"
            "assert all(x.is_file() and not x.is_symlink() and hashlib.file_digest(x.open('rb'),'sha256').hexdigest()==expected[x.name] for x in p.iterdir());"
            "[(os.chown(x,0,0),os.chmod(x,0o600)) for x in p.iterdir()];os.chown(p,0,0);os.chmod(p,0o700)")
        # file_digest streams the archive instead of allocating gigabytes.
        ssh(['sudo','-n','python3','-I','-c',prepare])
        code='import sys;sys.path.insert(0,'+repr(remote)+');import openstack_image_transfer as m;sys.exit(m.main(None,sys.argv[1:]))'
        result=json.loads(ssh(['sudo','-n','python3','-I','-B','-c',code,'import-compute-image',
            '--package',remote,'--package-sha256',args.package_sha256,
            '--machine-sha256',args.machine_sha256,'--output',remote+'-import',
            '--reserve-gib',str(args.reserve_gib)]))
        if (result.get('status') not in ('COMPUTE_IMAGE_IMPORTED_NOT_DEPLOYED','COMPUTE_IMAGE_ALREADY_PRESENT_NOT_DEPLOYED')
                or result.get('image_id')!=receipt['image_id'] or result.get('containers_deployed') is not False
                or result.get('guests_changed') is not False):
            raise ValueError('remote import did not confirm approved identity/non-activation')
        record.update(status='COMPUTE_IMAGE_DELIVERED_NOT_DEPLOYED',remote_import_status=result['status'])
    except BaseException:
        record['status']='NEEDS_REVIEW_IMAGE_DELIVERY';write(output/'delivery-journal.json',record)
        raise
    write(output/'delivery-journal.json',record)
    return record


def main(backend,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['export-compute-image','import-compute-image','deliver-compute-image'])
    parser.add_argument('--output',required=True,help='New private export directory or import journal')
    for name in ('build-receipt','build-sha256','image-id','package','package-sha256','machine-sha256'):
        parser.add_argument('--'+name)
    for name in ('host','identity-file','known-hosts'):parser.add_argument('--'+name)
    parser.add_argument('--ssh-port',type=int,default=22)
    parser.add_argument('--reserve-gib',type=int,default=10)
    args=parser.parse_args(argv)
    try:
        if args.action!='deliver-compute-image' and any((args.host,args.identity_file,args.known_hosts)):
            raise ValueError('SSH settings apply only to deliver-compute-image')
        if args.action=='export-compute-image':
            if not all((args.build_receipt,args.build_sha256,args.image_id)) or any((args.package,args.package_sha256,args.machine_sha256)):
                raise ValueError('export requires only build receipt approval and immutable image ID')
            result=export(args)
        elif args.action=='deliver-compute-image':
            if not all((args.package,args.package_sha256,args.machine_sha256,args.host,args.identity_file,args.known_hosts)) or any((args.build_receipt,args.build_sha256,args.image_id)):
                raise ValueError('delivery requires approved package, exact machine and explicit SSH trust/identity')
            result=deliver(args)
        else:
            if not all((args.package,args.package_sha256,args.machine_sha256)) or any((args.build_receipt,args.build_sha256,args.image_id)):
                raise ValueError('import requires approved package and target machine identity')
            result=load(args)
        print(json.dumps({k:v for k,v in result.items() if k in
            ('status','package_sha256','image_id','containers_deployed','guests_changed')}))
        return 0
    except (OSError,ValueError,KeyError,TypeError,tarfile.TarError,subprocess.SubprocessError):
        print('ERROR: compute image transfer refused; private evidence preserved. No compute was activated.',file=sys.stderr)
        return 2
