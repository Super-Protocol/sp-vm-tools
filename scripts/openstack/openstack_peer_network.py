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


def policy_change(old,new,kind,address,removing=False):
    fields={'geneve':{'peers'},'management':{'peers'},'nat':{'probe_sources'}}
    if kind not in fields:raise ValueError('unknown owned peer policy kind')
    allowed=fields[kind].copy()
    if kind!='geneve':
        if old.get('phase')!=kind:raise ValueError('peer policy phase differs')
        if kind=='management' and old.get('management_mode')!='shared-interface':
            raise ValueError('peer management changes require shared-interface mode')
        if kind=='nat' and old.get('management_mode')=='shared-interface':allowed.add('peers')
    if {k:v for k,v in old.items() if k not in allowed}!={k:v for k,v in new.items() if k not in allowed}:
        raise ValueError('only the selected compute peer addresses may change')
    change=contraction if removing else extension
    for field in allowed:change({field:old[field]},{field:new[field]},field,address)


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
            if kind in result or any(entry['path']==e['path'] for e in entries if e is not entry):
                raise ValueError('duplicate owned peer policy')
            policy_change(old,new,kind,entry['new_address'],self.removing)
            result[kind]={'before':old,'after':new,'state':self.state(module,old,kind),
                'file_sha256':hashlib.sha256(self.read(entry['path'],65536)).hexdigest()}
        nat=result.get('nat');management=result.get('management')
        if nat and nat['before'].get('management_mode')=='shared-interface':
            if not management:raise ValueError('shared NAT requires coordinated management policy')
            for stage in ('before','after'):
                for key in ('peers','deployment_id','machine_sha256','management_address','management_interface'):
                    if nat[stage][key]!=management[stage][key]:raise ValueError('shared peer policies disagree')
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
                # NAT probe rules are changed while the old management guard is
                # still active. Then update that guard, and finally synchronize
                # the NAT policy's copy of its peers. Each step validates its
                # own live rules and protects all other chains. No permissive
                # intermediate guard, flush, service restart or route change.
                ordered=sorted(entries,key=lambda e:('geneve','nat','management').index(e['kind']))
                for entry in ordered:
                    kind=entry['kind'];old=before[kind]['before'];new=before[kind]['after'];module=self.load(entry)
                    if old==new:continue
                    if kind=='nat' and old.get('management_mode')=='shared-interface':
                        new={**new,'peers':old['peers']}
                    expected=self.state(module,old,kind)
                    (folder/(kind+'.before.json')).write_bytes(self.read(entry['path'],65536))
                    if kind=='geneve':
                        oldrows,oldjump=module.rules(old);newrows,newjump=module.rules(new)
                        if oldjump!=newjump:raise ValueError('Geneve jump must remain unchanged')
                        tables={'filter':(oldrows,newrows)}
                    elif kind=='management':
                        oldrows,oldjumps=module.management_rules(old);newrows,newjumps=module.management_rules(new)
                        if (oldjumps!=newjumps or module.management_rules(old,True)!=module.management_rules(new,True)):
                            raise ValueError('management jumps and IPv6 rules must remain unchanged')
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
                for entry in ordered:
                    if entry['kind']!='nat' or before['nat']['before'].get('management_mode')!='shared-interface':continue
                    new=before['nat']['after'];old=before['nat']['before']
                    intermediate={**new,'peers':old['peers']}
                    if json.loads(self.read(entry['path'],65536))!=intermediate:
                        raise ValueError('NAT policy changed during coordinated peer update')
                    self.state(self.load(entry),new,'nat')
                    self.put(entry['path'],(json.dumps(new,sort_keys=True,indent=2)+'\n').encode())
                for entry in ordered:
                    if json.loads(self.read(entry['path'],65536))!=before[entry['kind']]['after']:
                        raise ValueError('final peer policy differs')
                    self.state(self.load(entry),entry['policy'],entry['kind'])
                write(journal,{'status':'PEER_NETWORK_READY','services_restarted':False})
            except BaseException:
                write(journal,{'status':'NEEDS_REVIEW','services_restarted':False});raise
            return {'status':'PEER_NETWORK_READY','services_restarted':False}
        finally:os.close(fd)


class RemovingPeer(Peer):
    """Explicit remove-compute only; never selected by onboarding."""
    removing=True
