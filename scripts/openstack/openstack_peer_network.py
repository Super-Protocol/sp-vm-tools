"""Add one registered compute to owned peer allowlists, without flushing rules.

No interfaces, routes, policies, guest processes or services are changed. A
partial operation is journalled for review; it is never automatically replayed.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def extension(before,after,field,address):
    if ({k:v for k,v in before.items() if k!=field}!=
            {k:v for k,v in after.items() if k!=field}):
        raise ValueError('only an additive peer address change is supported')
    old,new=before[field],after[field]
    if (type(old) is not list or type(new) is not list or old!=sorted(set(old)) or new!=sorted(set(new))
            or not set(old)<=set(new) or set(new)-set(old) not in (set(),{address})):
        raise ValueError('only the new compute may be added; no removals or other peers')


def insertions(before,after):
    """Return insert-only rules; existing order and chain identity must remain."""
    if not before or not after or before[0]!=after[0] or before[0][0]!='-N':
        raise ValueError('owned chain identity must be unchanged')
    chain=before[0][1];position=0;result=[]
    for index,row in enumerate(after):
        if position<len(before) and row==before[position]:position+=1;continue
        if index==0 or row[:2]!=['-A',chain]:raise ValueError('only owned-chain insertions permitted')
        result.append(['-I',chain,str(index),*row[2:]])
    if position!=len(before):raise ValueError('existing rules may not be removed or reordered')
    return result


def contraction(before,after,field,address):
    if ({k:v for k,v in before.items() if k!=field}!={k:v for k,v in after.items() if k!=field}
            or before[field]!=sorted(set(before[field]))
            or after[field]!=sorted(set(before[field])-{address})):
        raise ValueError('remove only the retired compute peer')


def deletions(before,after):
    # Reversing insert-only validation proves unchanged chain/order/jumps.
    insertions(after,before)
    return [['-D',*row[1:]] for row in before if row not in after]


class Peer:
    removing=False
    def __init__(self,root=Path('/'),runner=subprocess.run):
        self.root=Path(root);self.runner=runner;self.owner=os.getuid()

    def trusted(self,name):
        path=self.root/name.lstrip('/')
        for p in (path,*path.parents):
            if p.exists() or p.is_symlink():
                s=p.lstat()
                if p.is_symlink() or s.st_uid!=self.owner or s.st_mode&0o022:raise ValueError('untrusted peer path')
            if p==self.root:break
        return path

    def read(self,name,limit=1048576):
        path=self.trusted(name);s=path.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>limit:raise ValueError('bounded peer file required')
        return path.read_bytes()

    def load(self,entry):
        body=self.read(entry['runtime'])
        if hashlib.sha256(body).hexdigest()!=entry['runtime_sha256']:raise ValueError('installed network runtime differs')
        spec=importlib.util.spec_from_file_location('_spvm_owned_peer_runtime',self.trusted(entry['runtime']))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def state(self,module,policy,kind):
        if kind=='geneve':
            state=module.snapshot(policy,self.runner)
            if state['owned']!='READY':raise ValueError('owned Geneve guard is not ready')
        else:
            state=module.snapshot(policy,self.runner)
            if any(state.get(k) is not True for k in ('nat_ready','route_ready','vip_ready')):
                raise ValueError('owned NAT/route is not ready')
        return state

    def observe(self,entries,machine):
        if hashlib.sha256(self.read('/etc/machine-id').strip()).hexdigest()!=machine:raise ValueError('wrong peer machine')
        result={}
        for entry in entries:
            module=self.load(entry);old=module.validate(json.loads(self.read(entry['path'],65536)))
            new=module.validate(entry['policy']);kind=entry['kind']
            change=contraction if self.removing else extension
            change(old,new,'peers' if kind=='geneve' else 'probe_sources',entry['new_address'])
            result[kind]={'before':old,'after':new,'state':self.state(module,old,kind),
                'file_sha256':hashlib.sha256(self.read(entry['path'],65536)).hexdigest()}
        return result

    def put(self,name,body):
        path=self.trusted(name);s=path.stat()
        fd,temporary=tempfile.mkstemp(prefix='.spvm-peer-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                os.fchown(stream.fileno(),s.st_uid,s.st_gid);os.fchmod(stream.fileno(),stat.S_IMODE(s.st_mode))
                stream.write(body);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
            fd=os.open(path.parent,os.O_DIRECTORY|os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            if Path(temporary).exists():Path(temporary).unlink()

    def apply(self,entries,machine,before,output):
        # /run/lock is commonly world-writable; never rely on it as a trusted
        # ancestor. Use our own root-private directory directly under /run.
        directory=self.trusted('/run/spvm-installer-peer')
        directory.mkdir(mode=0o700,exist_ok=True)
        if stat.S_IMODE(directory.stat().st_mode)!=0o700:raise ValueError('private peer runtime directory required')
        fd=os.open(self.trusted('/run/spvm-installer-peer/network.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            s=os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid!=self.owner or s.st_nlink!=1 or stat.S_IMODE(s.st_mode)!=0o600:
                raise ValueError('untrusted peer network lock')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if self.observe(entries,machine)!=before:raise ValueError('peer changed since approval')
            folder=self.trusted(output);folder.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            folder.mkdir(mode=0o700,exist_ok=False)
            from openstack_container_engine import write
            journal=folder/'transaction.json';write(journal,{'status':'APPLYING','before':before})
            try:
                for entry in entries:
                    kind=entry['kind'];old=before[kind]['before'];new=before[kind]['after'];module=self.load(entry)
                    if old==new:continue
                    expected=self.state(module,old,kind)
                    (folder/(kind+'.before.json')).write_bytes(self.read(entry['path'],65536))
                    if kind=='geneve':
                        oldrows,oldjump=module.rules(old);newrows,newjump=module.rules(new)
                        if oldjump!=newjump:raise ValueError('Geneve jump must remain unchanged')
                        tables={'filter':(oldrows,newrows)}
                    else:
                        tables={}
                        for table,(oldrows,oldjumps) in module.firewall(old).items():
                            newrows,newjumps=module.firewall(new)[table]
                            if oldjumps!=newjumps:raise ValueError('NAT jumps must remain unchanged')
                            tables[table]=(oldrows,newrows)
                    for table,(oldrows,newrows) in tables.items():
                        changes=deletions if self.removing else insertions
                        for rule in changes(oldrows,newrows):
                            p=self.runner(['iptables','-w','5','-t',table,*rule],capture_output=True,text=True,timeout=45)
                            if p.returncode:raise ValueError('peer rule insertion failed; inspect journal')
                    after=self.state(module,new,kind)
                    # These snapshots exclude owned rule contents, so equality
                    # proves unrelated rules, NAT, links and routes stayed intact.
                    if after!=expected:raise ValueError('unrelated peer network state changed')
                    if hashlib.sha256(self.read(entry['path'],65536)).hexdigest()!=before[kind]['file_sha256']:
                        raise ValueError('peer policy changed concurrently; not overwriting it')
                    self.put(entry['path'],(json.dumps(new,sort_keys=True,indent=2)+'\n').encode())
                write(journal,{'status':'PEER_NETWORK_READY','services_restarted':False})
            except BaseException:
                write(journal,{'status':'NEEDS_REVIEW','services_restarted':False});raise
            return {'status':'PEER_NETWORK_READY','services_restarted':False}
        finally:os.close(fd)


class RemovingPeer(Peer):
    """Explicit remove-compute only; never selected by onboarding."""
    removing=True
