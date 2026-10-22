"""Root-local fresh Kolla helper provisioning. No Nova, libvirt or VM restarts."""
import argparse
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import traceback
import time
import uuid
from urllib.parse import urlsplit

from openstack_container_engine import digest, file_hash, write

MODULES=('host_helper.py','host_storage.py','host_network.py','host_provider.py',
         'host_runtime.py','host_execution.py','host_vif_cleanup.py','state_store.py',
         'seed_reconcile_worker.py','release_service_smoke.py')
SCRIPTS=('start_super_protocol.sh','start_super_protocol_libvirt.sh','libvirt_launcher.py')
ACCOUNT='kolla-nova'
UID=42436
ROOTS={k:'/var/lib/spvm/'+v for k,v in
       [('network','network'),('provider','providers'),('runtime','runtime'),('execution','execution')]}
FIXED=('/usr/local/libexec/spvm','/usr/local/libexec/nova-spvm',
       '/usr/local/bin/spvm_cloud.py','/etc/spvm/host-helper.json','/etc/spvm/compute-pools.json',
       '/etc/spvm/runtime-services.json','/etc/systemd/system/host-helper.service',
       '/etc/systemd/system/host-helper.socket','/etc/tmpfiles.d/spvm.conf',
       '/etc/polkit-1/rules.d/49-spvm-kolla-nova.rules','/var/lib/nova/compute_id')


def request(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1048576:
        raise ValueError('bounded regular request file required')
    value=json.loads(path.read_text())
    if not isinstance(value,dict) or set(value)-{'registered_release_cache_root'}!={'version','machine_sha256','compute_uuid','release',
            'allow_debug','tools','cache','pools','gpus'} or type(value['version']) is not int or value['version']!=1:
        raise ValueError('explicit versioned helper request required')
    if (not re.fullmatch('[a-f0-9]{64}',str(value['machine_sha256']))
            or str(uuid.UUID(value['compute_uuid']))!=value['compute_uuid']
            or not re.fullmatch(r'build-[1-9][0-9]*-(debug|release)',str(value['release']))
            or type(value['allow_debug']) is not bool
            or (value['release'].endswith('-debug') and not value['allow_debug'])):
        raise ValueError('invalid machine/compute/release binding')
    for key in ('cache','tools', *(['registered_release_cache_root'] if 'registered_release_cache_root' in value else [])):
        p=Path(value[key])
        if (not p.is_absolute() or p==Path('/') or '..' in p.parts
                or p.parts[1] in ('home','root','tmp','run','proc','sys','dev')):
            raise ValueError('runtime/cache paths must be explicit non-home system paths')
        if key!='tools' and p.parts[1] in ('usr','etc','boot','efi'):
            raise ValueError('cache must remain writable under helper systemd sandbox')
    if not isinstance(value['pools'],list) or not value['pools']:
        raise ValueError('explicit state pools required')
    names=set()
    for pool in value['pools']:
        if (not isinstance(pool,dict) or set(pool)!={'id','root','mount','budget_gib','reserve_gib'}
                or not re.fullmatch('[a-z][a-z0-9-]{0,39}',str(pool['id'])) or pool['id'] in names):
            raise ValueError('invalid/duplicate state pool')
        names.add(pool['id'])
        root,mount=Path(pool['root']),Path(pool['mount'])
        if (not root.is_absolute() or not mount.is_absolute() or mount==Path('/') or root==mount
                or '..' in root.parts or '..' in mount.parts or not root.is_relative_to(mount)):
            raise ValueError('state root must be below an explicit non-root mount')
        if mount.parts[1] in ('home','root','tmp','run','proc','sys','dev','usr','etc','boot','efi'):
            raise ValueError('state mount cannot be hidden/read-only under helper systemd sandbox')
        for field in ('budget_gib','reserve_gib'):
            if type(pool[field]) is not int or pool[field]<1:
                raise ValueError('positive integer pool budget/reserve required')
    if (not isinstance(value['gpus'],list)
            or any(not isinstance(p,str) or not re.fullmatch(r'[a-f0-9]{4}:[a-f0-9]{2}:[01][a-f0-9]\.[0-7]',p) for p in value['gpus'])
            or len(value['gpus'])!=len(set(value['gpus']))):
        raise ValueError('explicit unique GPU BDF list required; [] means none')
    roots=[Path(p) for p in [*ROOTS.values(),value['cache'],value['tools'],*[p['root'] for p in value['pools']],
                          *([value['registered_release_cache_root']] if 'registered_release_cache_root' in value else [])]]
    if any(a.is_relative_to(b) or b.is_relative_to(a) for i,a in enumerate(roots) for b in roots[i+1:]):
        raise ValueError('storage/cache/tools/runtime roots must not overlap')
    return value


def sources(backend_path):
    deployment=Path(backend_path).parent
    base=deployment.parent
    result={}
    def read(path):
        if path.resolve()!=path or not path.is_file() or path.stat().st_size>4194304:
            raise ValueError('bounded regular source file required')
        return path.read_bytes()
    for name in MODULES:
        result['/usr/local/libexec/spvm/'+name]=(read(deployment/name),0o644)
    for p in (base/'nova-spvm/nova_spvm').glob('*.py'):
        if p.is_symlink():raise ValueError('symlink source refused')
        result['/usr/local/libexec/nova-spvm/nova_spvm/'+p.name]=(read(p),0o644)
    if '/usr/local/libexec/nova-spvm/nova_spvm/protocol.py' not in result:
        raise ValueError('driver protocol sources missing')
    for target,source,mode in [('/usr/local/bin/spvm_cloud.py',base/'spvm_cloud.py',0o755),
            ('/etc/systemd/system/host-helper.service',deployment/'host-helper.service',0o644),
            ('/etc/polkit-1/rules.d/49-spvm-kolla-nova.rules',deployment/'kolla-nova-libvirt.rules',0o644)]:
        result[target]=(read(source),mode)
    socket=read(deployment/'host-helper.socket').decode()
    if socket.count('SocketGroup=nova')!=1:raise ValueError('reviewed helper socket template required')
    result['/etc/systemd/system/host-helper.socket']=(socket.replace('SocketGroup=nova','SocketGroup=42436').encode(),0o644)
    if sum(len(body) for body,mode in result.values())>32*1024*1024:
        raise ValueError('helper source snapshot exceeds limit')
    return result


def source_manifest(backend_path,spec,*,bootstrap=False):
    base=Path(backend_path).parent.parent
    path=base/'manifest.json'
    if not path.is_file():path=base/'nova-spvm/manifest.json'
    if path.resolve()!=path or not path.is_file() or path.stat().st_size>1048576:
        raise ValueError('driver capability manifest required')
    value=json.loads(path.read_text())
    needed={'grant-bound-release-selection','guest-api-registry-presence'}
    if 'registered_release_cache_root' in spec:needed.add('administrator-release-registration')
    if spec['release'].endswith('-release'):needed.add('release-only-helper-default')
    if bootstrap:needed.add('deny-all-runtime-bootstrap')
    if value.get('schema_version')!=1 or not needed<=set(value.get('capabilities',[])):
        raise ValueError('selected driver bundle cannot configure this measured release')
    return file_hash(path)


def bootstrap_policy(auth_url,region):
    endpoint=urlsplit(auth_url)
    if (endpoint.scheme!='https' or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.query or endpoint.fragment or endpoint.path.rstrip('/')!='/v3'
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',str(region))):
        raise ValueError('explicit trusted HTTPS v3 endpoint and region required')
    endpoint.port
    return {'schema_version':1,'experimental_debug_only':False,'bootstrap_deny_all':True,
            'auth_url':auth_url,'region_name':region,'projects':{}}


class Host:
    # Alternate root/runner are an in-process test fixture only, not CLI options.
    def __init__(self,root=Path('/'),runner=subprocess.run):
        self.root=Path(root)
        self.runner=runner
        self.owner_uid=0 if self.root==Path('/') else os.getuid()

    def path(self,value):return self.root/str(value).lstrip('/')

    def command(self,args,allowed=(0,)):
        p=self.runner(args,capture_output=True,text=True,timeout=45,
            env={'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C','PYTHONNOUSERSITE':'1'})
        if p.returncode not in allowed or len(p.stdout)>1048576:
            raise ValueError('host prerequisite/action failed; output suppressed')
        return p

    def trusted(self,path,*,directory=False):
        p=self.path(path)
        for item in [p,*p.parents]:
            if item.exists() or item.is_symlink():
                st=item.lstat()
                if item.is_symlink() or st.st_uid!=self.owner_uid or st.st_mode & 0o022:
                    raise ValueError('system path must be root-owned, non-symlink and not group/other writable')
            if item==self.root:break
        if directory and not p.is_dir():raise ValueError('existing system directory required')
        return p

    def account(self):
        if any(p.pw_uid==UID and p.pw_name!=ACCOUNT for p in pwd.getpwall()):
            raise ValueError('Kolla Nova UID is shared with another user')
        try:
            p=pwd.getpwnam(ACCOUNT)
            if (p.pw_uid,p.pw_gid)!=(UID,UID):raise ValueError('Kolla Nova identity collision')
            status='exists'
        except KeyError:
            try:pwd.getpwuid(UID)
            except KeyError:pass
            else:raise ValueError('Kolla Nova UID already belongs to another account')
            status='create'
        try:
            group=grp.getgrgid(UID)
            if group.gr_name!=ACCOUNT:raise ValueError('Kolla Nova GID collision')
        except KeyError:
            try:grp.getgrnam(ACCOUNT)
            except KeyError:pass
            else:raise ValueError('Kolla Nova group name has another GID')
            if status=='exists':raise ValueError('existing Nova account has no matching group')
        return status

    def inspect(self,spec,launcher=None):
        identity=hashlib.sha256(self.path('/etc/machine-id').read_text().strip().encode()).hexdigest()
        if identity!=spec['machine_sha256']:
            raise ValueError('wrong physical host')
        domains=self.command(['virsh','-c','qemu:///system','list','--all','--uuid']).stdout.strip()
        qemu=self.command(['pgrep','-f',r'(^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$))|(^|[ /])nova(-compute|[.]cmd[.]compute)( |$)'],allowed=(0,1))
        if domains or qemu.returncode!=1:raise ValueError('fresh helper requires empty compute with no Nova process')
        for path in [*FIXED,*ROOTS.values(),'/run/spvm']:
            p=self.path(path)
            if p.exists() or p.is_symlink():raise ValueError('fresh helper target already exists; maintenance is a separate workflow')
            self.trusted(path)
        for path in ('/etc/spvm','/var/lib/nova','/var/lib/spvm/clusters'):
            directory=self.trusted(path)
            if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
                raise ValueError('old Nova/SPVM data requires a separate reviewed migration')
        for unit in ('host-helper.socket','host-helper.service'):
            p=self.command(['systemctl','show',unit,'--property=LoadState','--value'])
            if p.stdout.strip()!='not-found':raise ValueError('existing helper systemd unit refused')
        self.account()
        if launcher is not None:
            tools=self.trusted(spec['tools'])
            if tools.exists():raise ValueError('staged launcher requires an absent tools destination')
        else:
            tools=self.trusted(spec['tools'],directory=True)
            launcher={}
            for name in SCRIPTS:
                p=self.trusted(str(Path(spec['tools'])/'scripts'/name))
                if not p.is_file() or (name!='start_super_protocol.sh' and not p.stat().st_mode & 0o100):
                    raise ValueError('installed executable measured launcher missing')
                launcher[name]=file_hash(p)
            wrapper=(tools/'scripts/start_super_protocol_libvirt.sh').read_text()
            if not all(marker in wrapper for marker in ('--nova-state-disk)','--nova-interface-xml)','Nova requires measured TDX/SEV launch')):
                raise ValueError('Nova-compatible measured launcher patches are required')
        cache=self.trusted(spec['cache'])
        if cache.exists() and (not cache.is_dir() or cache.stat().st_mode & 0o067):
            raise ValueError('existing cache must be private except for traversal')
        if 'registered_release_cache_root' in spec:
            dynamic=self.trusted(spec['registered_release_cache_root'])
            if dynamic.exists() and (not dynamic.is_dir() or dynamic.stat().st_mode & 0o067):
                raise ValueError('registered release cache root must be private')
        pools,devices=[],set()
        for item in spec['pools']:
            mount=self.trusted(item['mount'],directory=True)
            state=self.trusted(item['root'])
            if (not os.path.ismount(mount) or mount.stat().st_dev==self.path('/').stat().st_dev
                    or (state.exists() and (not state.is_dir() or any(state.iterdir())))):
                raise ValueError('dedicated state mount and empty pool root required')
            ancestor=state
            while not ancestor.exists():ancestor=ancestor.parent
            if ancestor.stat().st_dev!=mount.stat().st_dev:
                raise ValueError('nested filesystem cannot masquerade as configured pool mount')
            if mount.stat().st_dev in devices:raise ValueError('filesystem cannot be counted twice')
            devices.add(mount.stat().st_dev)
            report=json.loads(self.command(['findmnt','--json','--target',item['mount'],'-o','TARGET,FSTYPE']).stdout)
            rows=report['filesystems']
            if len(rows)!=1 or rows[0]['target']!=item['mount'] or rows[0]['fstype'] not in ('ext4','xfs'):
                raise ValueError('local ext4/xfs state filesystem required')
            free=os.statvfs(mount)
            if free.f_flag & os.ST_RDONLY:
                raise ValueError('state filesystem is read-only')
            if (item['budget_gib']+item['reserve_gib'])*2**30>free.f_bavail*free.f_frsize:
                raise ValueError('explicit logical state budget plus reserve exceeds free space')
            pools.append({**item,'device_id':mount.stat().st_dev})
        for address in spec['gpus']:
            p=self.path('/sys/bus/pci/devices/'+address)
            if ((p/'vendor').read_text().strip()!='0x10de'
                    or not (p/'class').read_text().startswith('0x03')
                    or (p/'driver').resolve().name!='vfio-pci'
                    or sorted(x.name for x in (p/'iommu_group/devices').iterdir())!=[address]):
                raise ValueError('selected GPU must be isolated NVIDIA vfio-pci device')
        # Preserve the measured hypervisor identity across provisioning.
        protected={}
        for path in ('/usr/bin/qemu-system-x86_64','/usr/bin/virsh','/usr/sbin/libvirtd',
                     '/etc/libvirt/qemu.conf','/etc/libvirt/libvirtd.conf'):
            p=self.path(path)
            if p.is_file():protected[path]=file_hash(p)
        if not protected.get('/usr/bin/qemu-system-x86_64') or not protected.get('/usr/bin/virsh'):
            raise ValueError('host bootstrap QEMU/libvirt prerequisites missing')
        return {'machine':spec['machine_sha256'],'pools':pools,'launcher':launcher,'protected':protected,
                'account':self.account()}

    def put(self,path,body,mode):
        p=self.path(path)
        self.trusted(path)
        self.directory(str(Path(path).parent),0o755)
        with p.open('xb') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        os.chmod(p,mode)
        os.chown(p,0,0)

    def directory(self,path,mode):
        p=self.trusted(path)
        missing=[]
        ancestor=p
        while not ancestor.exists():
            missing.append(ancestor);ancestor=ancestor.parent
        for item in reversed(missing):
            item.mkdir(mode=0o755)
            os.chmod(item,mode if item==p else 0o755)
        return p

    def install(self,spec,observed,files):
        if self.account()=='create':
            try:grp.getgrgid(UID)
            except KeyError:self.command(['groupadd','--system','--gid',str(UID),ACCOUNT])
            self.command(['useradd','--system','--uid',str(UID),'--gid',str(UID),'--no-create-home',
                          '--home-dir','/nonexistent','--shell','/usr/sbin/nologin',ACCOUNT])
        for key,path in ROOTS.items():
            # Provider payloads and lease journals are helper-private. Their
            # backend validators require 0700, not QEMU traversal permissions.
            self.directory(path,0o700 if key in ('provider','runtime') else 0o710)
        for path in [spec['cache'],*[p['root'] for p in spec['pools']],
                     *([spec['registered_release_cache_root']] if 'registered_release_cache_root' in spec else [])]:
            self.directory(path,0o710)
        config={'allowed_peer_uids':[UID],'backend_timeout':1700,'request_timeout':30,
            'storage':{'compute_uuid':spec['compute_uuid'],'pools':observed['pools']},
            'network':{'root':ROOTS['network']},'provider':{'root':ROOTS['provider']},
            'runtime':{'root':ROOTS['runtime'],'cid_min':100,'cid_max':65535,'registered_gpus':spec['gpus']},
            'execution':{'root':ROOTS['execution'],'tools':spec['tools'],'source_sha256':observed['launcher'],
                'release':spec['release'],'cache':spec['cache'],'timeout':1200,
                'allow_debug_launch':spec['allow_debug'],'release_caches':{spec['release']:spec['cache']},
                'release_gate':'guest-api-registry-presence-v1'}}
        if 'registered_release_cache_root' in spec:
            config['execution']['registered_release_cache_root']=spec['registered_release_cache_root']
        files={**files,'/etc/spvm/host-helper.json':(json.dumps(config,indent=2).encode(),0o600),
            '/etc/spvm/compute-pools.json':(json.dumps({'compute_uuid':spec['compute_uuid'],
                'pool_ids':[p['id'] for p in spec['pools']]}).encode(),0o644),
            '/etc/tmpfiles.d/spvm.conf':(b'd /run/spvm 0750 root 42436 -\n',0o644),
            '/var/lib/nova/compute_id':((spec['compute_uuid']+'\n').encode(),0o600)}
        for path,(body,mode) in files.items():self.put(path,body,mode)
        os.chown(self.path('/var/lib/nova/compute_id'),UID,UID)
        os.chown(self.path('/var/lib/nova'),UID,UID)
        run=self.trusted('/run/spvm')
        if run.exists():raise ValueError('existing helper runtime directory refused')
        run.mkdir(mode=0o750);os.chown(run,0,UID)
        # Use installed trusted code; no source-directory import as root.
        code="import sys;sys.path[:0]=['/usr/local/libexec/spvm','/usr/local/libexec/nova-spvm'];from host_helper import load_config;from host_execution import validate_config;c=load_config('/etc/spvm/host-helper.json');validate_config(c['execution'])"
        self.command(['/usr/bin/python3','-I','-c',code])
        self.command(['sudo','-n','-u',ACCOUNT,'virsh','-c','qemu:///system','list','--uuid'])
        for name,expected in observed['protected'].items():
            if file_hash(self.path(name))!=expected:raise ValueError('measured runtime changed')
        self.command(['systemctl','daemon-reload'])
        self.command(['systemctl','enable','--now','host-helper.socket'])
        ping="import sys;sys.path.insert(0,'/usr/local/libexec/nova-spvm');from nova_spvm.protocol import call;r=call('/run/spvm/host-helper.sock','ping',timeout=10);assert {'host-state-v1','host-interface-stage-v1','host-provider-transfer-v1','host-runtime-prepare-v1','host-runtime-observe-v1'}<=set(r['capabilities']);assert r['deployment_ready'] is False"
        self.command(['sudo','-n','-u',ACCOUNT,'/usr/bin/python3','-I','-c',ping])
        return {'status':'HOST_HELPER_INSTALLED_NOT_ENROLLED','compute_uuid':spec['compute_uuid'],
                'release':spec['release'],'registered_gpu_count':len(spec['gpus']),
                'guests_launched':False,'nova_driver_activated':False}


def execute(args,backend_path,host=None):
    if os.geteuid()!=0:raise ValueError('root on the target compute required')
    host=Host() if host is None else host
    spec=request(args.request)
    launcher_stage=getattr(args,'launcher_stage',None)
    launcher_approval=getattr(args,'launcher_sha256',None)
    if bool(launcher_stage)!=bool(launcher_approval):
        raise ValueError('launcher staging path and explicit receipt digest must be supplied together')
    launcher_files={};receipt=None
    if launcher_stage:
        from openstack_launcher import verify
        receipt,launcher_files=verify(backend_path,launcher_stage,launcher_approval)
        observed=host.inspect(spec,launcher=receipt['result_sha256'])
    else:observed=host.inspect(spec)
    auth_url=getattr(args,'bootstrap_auth_url',None)
    region=getattr(args,'bootstrap_region',None)
    if bool(auth_url)!=bool(region):raise ValueError('bootstrap endpoint and region must be supplied together')
    policy=bootstrap_policy(auth_url,region) if auth_url else None
    manifest=source_manifest(backend_path,spec,bootstrap=policy is not None)
    files=sources(backend_path)
    if policy is not None:
        files['/etc/spvm/runtime-services.json']=(json.dumps(policy,indent=2).encode(),0o644)
    for name,body in launcher_files.items():
        files[str(Path(spec['tools'])/'scripts'/name)]=(body,0o755)
    plan={'schema_version':1,'request':spec,'observed':observed,'source_sha256':{
        k:hashlib.sha256(v[0]).hexdigest() for k,v in files.items()},'source_manifest_sha256':manifest,
        'launcher_receipt':receipt,
        'bootstrap_runtime':policy,
        'installer_sha256':{n:file_hash(Path(__file__).with_name(n)) for n in
            ('openstack_host_helper.py','openstack_container_engine.py','openstack_installer.py','openstack_launcher.py')}}
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('absolute non-symlink output required')
    output.mkdir(mode=0o700,parents=False,exist_ok=True)
    if output.stat().st_uid!=host.owner_uid or output.stat().st_mode & 0o077:raise ValueError('private root-owned output required')
    lock=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=output/'helper-plan.json'
        journal=output/'helper-transaction.json'
        if path.is_symlink() or journal.is_symlink():raise ValueError('symlink receipt refused')
        if args.action=='plan-host-helper':
            if path.exists() or journal.exists():raise ValueError('output already used')
            plan['created_at']=int(time.time())
            plan['expires_at']=plan['created_at']+900
            plan['plan_sha256']=digest(plan)
            write(path,plan)
            return {'status':'HOST_HELPER_PLAN_READY','plan_sha256':plan['plan_sha256']}
        previous=json.loads(path.read_text())
        approved=previous.get('plan_sha256')
        body={k:v for k,v in previous.items() if k!='plan_sha256'}
        if (type(previous.get('created_at')) is not int
                or previous.get('expires_at')!=previous['created_at']+900
                or not 0<=time.time()-previous['created_at']<=900):
            raise ValueError('fresh approved host plan required')
        plan.update(created_at=previous['created_at'],expires_at=previous['expires_at'])
        if (body!=plan or approved!=digest(body) or args.plan_sha256!=approved):
            raise ValueError('request, host or source changed since approved plan')
        if journal.exists():raise ValueError('installation was attempted; inspect journal, never replay blindly')
        record={'schema_version':1,'plan_sha256':approved,'status':'INSTALLING'}
        write(journal,record)
        try:
            result=host.install(spec,observed,files)
            record.update(result)
        except BaseException:
            record['status']='NEEDS_REVIEW_PARTIAL_HOST_INSTALL'
            write(journal,record)
            raise
        write(journal,record)
        return record
    finally:os.close(lock)


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-host-helper','prepare-host-helper'])
    parser.add_argument('--request',required=True,help='Explicit host identity, pools, GPU list and release JSON')
    parser.add_argument('--output',required=True,help='Private root-owned local plan/journal directory')
    parser.add_argument('--plan-sha256')
    parser.add_argument('--launcher-stage',help='Verified private launcher staging; tools destination must be absent')
    parser.add_argument('--launcher-sha256',help='Explicit approved launcher receipt digest')
    parser.add_argument('--bootstrap-auth-url',help='Trusted HTTPS Keystone /v3 URL for deny-all initial runtime')
    parser.add_argument('--bootstrap-region',help='Explicit region; required together with bootstrap auth URL')
    args=parser.parse_args(argv)
    try:
        result=execute(args,backend_path)
        print(json.dumps({k:v for k,v in result.items() if k in ('status','plan_sha256','compute_uuid','release','guests_launched','nova_driver_activated')}))
        return 0
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: fresh helper provisioning refused; artifacts preserved. '+type(exc).__name__
              +' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
