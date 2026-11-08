"""Bounded installation-time inspection/reconciliation of one empty compute.

No guest lifecycle, disks formatting, bootstrap, package installation or cloud
API operations. Reconciliation archives proven owned metadata; never migrates it.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

CONTAINERS=('neutron_ovn_metadata_agent','nova_compute','nova_ssh','ovn_controller',
            'openvswitch_vswitchd','openvswitch_db','fluentd','cron','kolla_toolbox')
UNITS=('host-helper.socket','host-helper.service','spvm-host-control.service',
       'spvm-host-budgets.timer','spvm-host-budgets.service')
FIXED=('/etc/spvm','/etc/kolla','/var/lib/spvm','/var/lib/nova','/run/spvm','/run/openvswitch',
       '/usr/local/libexec/spvm','/usr/local/libexec/nova-spvm',
       '/usr/local/libexec/spvm-host-control',
       '/etc/tmpfiles.d/spvm.conf','/etc/polkit-1/rules.d/49-spvm-kolla-nova.rules')
KOLLA_DIRECTORIES={'cron','kolla-toolbox','openvswitch-vswitchd','openvswitch-db-server',
                   'nova-compute','nova-ssh','neutron-ovn-metadata-agent','fluentd','ovn-controller'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def classify(facts,cloud,identity,proof=None):
    """Decide by current facts, not an administrator-selected fresh/returning mode."""
    if facts['machine_sha256']!=identity['machine_sha256'] or facts['hostname']!=identity['hostname']:
        raise ValueError('target machine or hostname differs from audited identity')
    if cloud['services']:
        if len(cloud['services'])!=1:raise ValueError('ambiguous compute registration')
        return 'registered'
    if facts['domains'] or facts['guest_processes'] or facts['live_claims'] or cloud['assigned_servers'] or cloud['allocations'] or cloud['migrations']:
        raise ValueError('host is occupied or has outstanding workload reservations')
    if cloud['providers'] or cloud['cell'] or cloud['chassis']:
        raise ValueError('partial native registration requires explicit transaction review')
    if not facts['managed'] and not facts['containers']:return 'new'
    runtime=facts.get('runtime') or {};helper=facts.get('helper') or {}
    # A historical receipt cannot override newer contradictory enrollment.
    if runtime and runtime.get('auth_url','').rstrip('/').removesuffix('/v3')!=identity['auth_url'].rstrip('/').removesuffix('/v3'):
        raise ValueError('installed runtime belongs to another cloud')
    if runtime and identity['project_id'] not in runtime.get('projects',{}):
        raise ValueError('installed runtime belongs to another project')
    if helper and helper.get('storage',{}).get('compute_uuid')!=identity['compute_uuid']:
        raise ValueError('installed helper has a different or unsupported compute identity')
    authenticated=(runtime.get('auth_url','').rstrip('/').removesuffix('/v3')==identity['auth_url'].rstrip('/').removesuffix('/v3')
        and identity['project_id'] in runtime.get('projects',{})
        and helper.get('storage',{}).get('compute_uuid')==identity['compute_uuid'])
    historical=(proof is not None and proof.get('machine')==identity['machine_sha256']
        and proof.get('compute_uuid')==identity['compute_uuid'] and proof.get('hostname')==identity['hostname'])
    if not authenticated and not historical:
        raise ValueError('existing configuration is not proven to belong to this cloud/physical host')
    if set(facts['containers'])-set(CONTAINERS):raise ValueError('unowned containers on target')
    for name,item in facts['containers'].items():
        if historical and proof.get('target',{}).get('containers',{}).get(name,{}).get('id')==item['id']:continue
        if not item.get('kolla_owned'):raise ValueError('container ownership is not proven')
    if facts['live_claims']:raise ValueError('state disks, active claims or unknown workload history require review')
    return 'returning'


class Host:
    def __init__(self,root=Path('/'),runner=subprocess.run):
        self.root=Path(root);self.runner=runner

    def path(self,name):return self.root/name.lstrip('/')

    def trusted(self,name):
        p=self.path(name)
        for parent in (p,*p.parents):
            if parent.exists() or parent.is_symlink():
                info=parent.lstat()
                nova=parent==self.path('/var/lib/nova') or parent.is_relative_to(self.path('/var/lib/nova'))
                # Kolla creates its root-only configuration namespace 0770,
                # root:root. This exception never accepts another group or
                # relaxes write permissions on ancestors/arbitrary metadata.
                kolla=(parent==self.path('/etc/kolla') and stat.S_ISDIR(info.st_mode)
                       and info.st_uid==0 and info.st_gid==0 and not info.st_mode&0o007)
                if parent.is_symlink() or info.st_uid not in ({os.geteuid(),42436} if nova else {os.geteuid()}) or info.st_mode&0o022 and not kolla:
                    raise ValueError('untrusted managed host path')
            if parent==self.root:break
        return p

    def command(self,argv,allowed=(0,)):
        result=self.runner(argv,capture_output=True,text=True,timeout=90)
        if result.returncode not in allowed or len(result.stdout)>4194304:raise ValueError('host inspection failed')
        return result.stdout

    def tree(self,name):
        p=self.trusted(name)
        if not p.exists():return None
        if name=='/etc/kolla' and (not p.is_dir() or any(
                child.name not in KOLLA_DIRECTORIES or not child.is_dir() or child.is_symlink()
                for child in p.iterdir())):
            raise ValueError('unreviewed Kolla configuration outside compute-only namespace')
        if name=='/run/openvswitch' and (not p.is_dir() or any(
                child.is_dir() or child.is_symlink() or not re.fullmatch(r'(?:br-int\.(?:mgmt|snoop)|db\.sock|(?:ovsdb-server|ovs-vswitchd)(?:\.[0-9]+\.ctl|\.pid))',child.name)
                for child in p.iterdir())):
            raise ValueError('unreviewed OVS runtime entry')
        mountinfo=self.path('/proc/self/mountinfo')
        if mountinfo.exists():
            for row in mountinfo.read_text().splitlines():
                fields=row.split()
                if len(fields)<6:raise ValueError('incomplete mount inventory')
                token=fields[4]
                for escape,char in (('\\040',' '),('\\011','\t'),('\\012','\n'),('\\134','\\')):token=token.replace(escape,char)
                mount=Path(token)
                logical=Path(name)
                if mount==logical or mount.is_relative_to(logical):raise ValueError('mounted or bind-mounted managed state requires explicit review')
        result={}
        paths=[p,*sorted(p.rglob('*'))] if p.is_dir() else [p]
        if len(paths)>20000:raise ValueError('managed metadata exceeds bounded inspection')
        for item in paths:
            info=item.lstat()
            if item.is_symlink() or info.st_dev!=p.stat().st_dev or os.path.ismount(item) and item!=p:
                raise ValueError('symlinks and nested mounts are not archived automatically')
            if stat.S_ISDIR(info.st_mode):entry={'kind':'directory'}
            elif stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=16*1024*1024:
                entry={'kind':'file','sha256':hashlib.sha256(item.read_bytes()).hexdigest()}
            elif stat.S_ISSOCK(info.st_mode) and ((name=='/run/spvm' and item.name=='host-helper.sock')
                    or name=='/run/openvswitch'):entry={'kind':'socket'}
            else:raise ValueError('unsupported managed metadata file')
            result[str(item.relative_to(p))]={'inode':info.st_ino,'mode':stat.S_IMODE(info.st_mode),**entry}
        return result

    def ovs_quiet(self):
        # Socket files survive stopped container removal. Never mistake a
        # live native/foreign OVS instance for disposable runtime metadata.
        proc=self.runner(['pgrep','-x','ovsdb-server|ovs-vswitchd'],capture_output=True,text=True,timeout=20)
        if proc.returncode!=1 or proc.stdout.strip():raise ValueError('OVS process still active or unknown')
        # /proc/net itself is a kernel symlink on Linux; read the kernel-owned
        # table directly rather than treating it as mutable managed metadata.
        table=Path('/proc/net/unix') if self.root==Path('/') else self.trusted('/proc/net/unix')
        for row in table.read_text().splitlines()[1:]:
            fields=row.split()
            if len(fields)>=8 and (fields[7]=='/run/openvswitch' or fields[7].startswith('/run/openvswitch/')):
                raise ValueError('OVS UNIX endpoint still live')

    def observe(self,identity,pools):
        domains=self.command(['virsh','-c','qemu:///system','list','--all','--uuid']).split()
        guests=self.command(['pgrep','-f',r'^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$)'],(0,1)).split()
        try:docker=self.runner(['docker','ps','--all','--no-trunc','--format','{{.ID}}'],capture_output=True,text=True,timeout=30)
        except FileNotFoundError:
            if self.path('/var/lib/docker').exists():raise ValueError('installed Docker is not healthy')
            docker=None
        if docker is not None and docker.returncode:raise ValueError('Docker inspection is not healthy')
        ids=docker.stdout.split() if docker is not None else []
        raw=json.loads(self.command(['docker','inspect',*ids])) if ids else []
        containers={}
        for c in raw:
            name=c['Name'].removeprefix('/')
            owned=any(m.get('Destination')=='/var/lib/kolla/config_files' and m.get('Source')=='/etc/kolla/'+name for m in c.get('Mounts',[]))
            containers[name]={'id':c['Id'],'image':c['Image'],'running':c['State']['Running'],'kolla_owned':owned}
        managed={name:self.tree(name) for name in FIXED if self.path(name).exists() or self.path(name).is_symlink()}
        for name in (*UNITS,*('kolla-'+n+'-container.service' for n in CONTAINERS)):
            path='/etc/systemd/system/'+name
            if self.path(path).exists() or self.path(path).is_symlink():managed[path]=self.tree(path)
        runtime={};helper={}
        for name,target in (('/etc/spvm/runtime-services.json',runtime),('/etc/spvm/host-helper.json',helper)):
            if self.path(name).exists():target.update(json.loads(self.trusted(name).read_bytes()))
        claims=[];state={}
        for name in ('/var/lib/spvm/network','/var/lib/spvm/providers','/var/lib/spvm/runtime','/var/lib/spvm/execution','/var/lib/spvm/clusters'):
            p=self.path(name)
            if p.exists():
                for child in p.rglob('*'):
                    if child.is_file() and child.suffix=='.json' and json.loads(child.read_bytes()):claims.append(str(child.relative_to(self.root)))
        old=helper.get('storage',{}).get('pools',[])
        if not isinstance(old,list):raise ValueError('unsupported installed storage metadata')
        all_pools={p['path']:p for p in pools}
        for pool in old:
            root=Path(pool['root']);mount=Path(pool['mount'])
            if (not root.is_absolute() or root.resolve()!=root or root==mount or not root.is_relative_to(mount)
                    or any(root==Path(n) or root.is_relative_to(Path(n)) for n in ('/etc','/usr','/run','/var/lib/docker','/home','/root','/boot','/dev','/proc','/sys'))):
                raise ValueError('unsafe installed pool path')
            all_pools[str(root)]={'path':str(root)}
        for pool in all_pools.values():
            p=self.trusted(pool['path'])
            if not p.exists():continue
            if os.path.ismount(p):raise ValueError('state root must not be a mount point')
            state[pool['path']]=self.tree(pool['path'])
            for child in p.iterdir():
                if child.name not in ('.spvm-state.lock','.spvm-claims.json'):claims.append(str(child))
                elif child.name=='.spvm-claims.json' and json.loads(child.read_bytes()):claims.append(str(child))
        return {'machine_sha256':hashlib.sha256(self.trusted('/etc/machine-id').read_bytes().strip()).hexdigest(),
            'hostname':self.command(['hostname']).strip(),'domains':domains,'guest_processes':guests,
            'managed':managed,'containers':containers,'runtime':runtime,'helper':helper,
            'live_claims':sorted(claims),'state':state}

    def reconcile(self,identity,pools,approved,cloud,proof,operation):
        if not isinstance(operation,str) or len(operation)!=64 or any(c not in '0123456789abcdef' for c in operation):raise ValueError('bound operation identity required')
        before=self.observe(identity,pools)
        if before!=approved or classify(before,cloud,identity,proof)!='returning':raise ValueError('returning target changed since approval')
        for name in (*before['managed'],*before['state']):
            p=self.trusted(name);destination=p.with_name(p.name+'.spvm-retired-'+operation[:24])
            if destination.exists() or destination.is_symlink():raise ValueError('archive destination already exists')
        # Containers are removed only after stopping their owning units, and
        # without -f. There are no volume/image removal or VM lifecycle commands.
        for name in (*UNITS,*('kolla-'+n+'-container.service' for n in before['containers'])):
            if '/etc/systemd/system/'+name in before['managed']:
                self.command(['systemctl','disable','--now',name])
        after=self.observe(identity,pools)
        if after['domains'] or after['guest_processes'] or after['live_claims']:raise ValueError('host became occupied')
        if set(after['containers'])!=set(before['containers']):raise ValueError('container set changed')
        for name,item in after['containers'].items():
            if item['running'] or item['id']!=before['containers'][name]['id'] or item['image']!=before['containers'][name]['image']:
                raise ValueError('container did not stop unchanged')
        archive=self.trusted('/var/lib/spvm-compute-reconcile')/operation
        archive.parent.mkdir(mode=0o700,exist_ok=True);archive.mkdir(mode=0o700)
        journal=archive/'transaction.json'
        def record(status,moves):
            with journal.open('w') as stream:
                os.fchmod(stream.fileno(),0o600);json.dump({'status':status,'moves':moves,'guests_changed':False},stream)
                stream.flush();os.fsync(stream.fileno())
        moves=[];record('RECONCILING',moves)
        for name,item in after['containers'].items():self.command(['docker','rm',item['id']])
        if '/run/openvswitch' in after['managed']:self.ovs_quiet()
        for name in after['managed']:
            # Fixed roots do not overlap; keep complete metadata for inspection.
            p=self.trusted(name)
            if self.tree(name)!=after['managed'][name]:raise ValueError('managed metadata changed before archival')
            target=p.with_name(p.name+'.spvm-retired-'+operation[:24])
            if target.exists() or target.is_symlink():raise ValueError('archive destination already exists')
            moves.append({'source':name,'backup':str(target.relative_to(self.root))});record('ARCHIVING',moves)
            os.rename(p,target)
            if target.is_dir():os.chmod(target,0o700)
        for name,expected in after['state'].items():
            p=self.trusted(name)
            if self.tree(name)!=expected:raise ValueError('state metadata changed')
            target=p.with_name(p.name+'.spvm-retired-'+operation[:24])
            if target.exists() or target.is_symlink():raise ValueError('state archive exists')
            moves.append({'source':name,'backup':str(target.relative_to(self.root))});record('ARCHIVING',moves)
            os.rename(p,target)
        self.command(['systemctl','daemon-reload'])
        record('OWNED_METADATA_ARCHIVED',moves)
        return {'status':'OWNED_METADATA_ARCHIVED','archive':str(archive.relative_to(self.root)),
                'moves':moves,'guests_changed':False,'bootstrap_changed':False}
