"""Owned management hostname repair; no DNS, interface or service reconfiguration."""
import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import stat
import time

from openstack_compute_identity import native_uuid
from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write


def specification(value):
    if (not isinstance(value,dict) or set(value)!={'schema_version','machine_sha256','mappings'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or not isinstance(value['machine_sha256'],str) or len(value['machine_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in value['machine_sha256'])
            or not isinstance(value['mappings'],dict) or not 1<=len(value['mappings'])<=255):
        raise ValueError('explicit machine and complete management hostname mapping required')
    addresses=set()
    for name,address in value['mappings'].items():
        native_uuid(name)
        ip=ipaddress.IPv4Address(address)
        if str(ip)!=address or ip.is_loopback or ip.is_unspecified or ip.is_multicast or address in addresses:
            raise ValueError('distinct canonical management addresses required')
        addresses.add(address)
    return value


def candidate(before,mappings):
    """Only replace approved names; preserve unrelated aliases and comments."""
    lines=[];found=set()
    for line in before.decode('utf-8').splitlines(keepends=True):
        plain,mark,comment=line.partition('#');fields=plain.split()
        selected=set(fields[1:]) & set(mappings) if fields else set()
        if not selected:
            lines.append(line);continue
        if len(fields)<2 or len(selected & found):raise ValueError('duplicate/ambiguous hostname mapping')
        found.update(selected)
        for name in selected:
            if fields[0] not in ('127.0.1.1',mappings[name]):raise ValueError('conflicting non-loopback hostname mapping')
        remaining=[name for name in fields[1:] if name not in selected]
        if remaining:lines.append(fields[0]+' '+ ' '.join(remaining)+(' #'+comment.rstrip('\n') if mark else '')+'\n')
        elif mark:lines.append('#'+comment.rstrip('\n')+'\n')
    if lines and not lines[-1].endswith('\n'):lines[-1]+='\n'
    lines.extend(address+' '+name+'\n' for name,address in sorted(mappings.items()))
    return ''.join(lines).encode()


class Host:
    def __init__(self,root=Path('/')):self.root=Path(root)
    def observe(self,wanted):
        path=self.root/'etc/hosts'
        for item in (path,*path.parents):
            st=item.lstat()
            if item.is_symlink() or st.st_uid!=os.getuid() or st.st_mode & 0o022:
                raise ValueError('protected hosts file and ancestors required')
            if item==self.root:break
        st=path.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>1048576:
            raise ValueError('bounded single-link hosts file required')
        if hashlib.sha256((self.root/'etc/machine-id').read_bytes().strip()).hexdigest()!=wanted['machine_sha256']:
            raise ValueError('wrong hostname-preparation machine')
        if self.root==Path('/') and socket.gethostname() not in wanted['mappings']:
            raise ValueError('actual kernel nodename is not approved')
        before=path.read_bytes();after=candidate(before,wanted['mappings'])
        return {'before_sha256':hashlib.sha256(before).hexdigest(),'after_sha256':hashlib.sha256(after).hexdigest(),
                'mode':stat.S_IMODE(st.st_mode),'uid':st.st_uid,'gid':st.st_gid},before,after
    def replace(self,data,observation):
        path=self.root/'etc/hosts';tmp=path.with_name('.spvm-hosts-'+str(os.getpid()))
        fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,observation['mode'])
        try:
            with os.fdopen(fd,'wb') as stream:
                os.fchown(stream.fileno(),observation['uid'],observation['gid'])
                # Installer processes intentionally use umask 077. Preserve the
                # approved original mode, or non-root NSS cannot read hosts.
                os.fchmod(stream.fileno(),observation['mode'])
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,path)
            directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if tmp.exists():tmp.unlink()
    def verify(self,wanted):
        for name,address in wanted['mappings'].items():
            actual={row[4][0] for row in socket.getaddrinfo(name,None,socket.AF_INET)}
            if actual!={address}:raise ValueError('management hostname does not resolve uniquely')


def execute(args,host=None):
    wanted=specification(json.loads(read(args.request)));host=host or Host()
    identity={'request':wanted,'source_sha256':file_hash(Path(__file__))}
    observed,before,after=host.observe(wanted);output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical private transaction required')
    if args.action=='plan-hostnames':
        output.mkdir(mode=0o700,exist_ok=False)
        plan={'schema_version':1,'identity':identity,'observed':observed,'created_at':int(time.time())}
        plan['plan_sha256']=digest(plan);write(output/'hostnames-plan.json',plan)
        return {'status':'HOSTNAMES_PLAN_READY','changes':before!=after,'plan_sha256':plan['plan_sha256']}
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:raise ValueError('private owned transaction required')
    fd=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600:
            raise ValueError('private single-link regular lock required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan=json.loads(read(output/'hostnames-plan.json'))
        if (plan.get('plan_sha256')!=args.plan_sha256 or digest({k:v for k,v in plan.items() if k!='plan_sha256'})!=args.plan_sha256
                or plan['identity']!=identity or plan['observed']!=observed
                or not 0<=time.time()-plan['created_at']<=900):raise ValueError('fresh unchanged hostname approval required')
        journal=output/'hostnames-transaction.json'
        if journal.exists():raise ValueError('previous hostname transaction requires review')
        write(output/'hosts.backup.json',{'content':before.decode(),'observation':observed})
        record={'status':'APPLYING_HOSTNAMES','plan_sha256':args.plan_sha256,'services_restarted':False}
        write(journal,record)
        try:
            if host.observe(wanted)[0]!=observed:raise ValueError('hosts changed concurrently')
            if before!=after:host.replace(after,observed)
            host.verify(wanted)
            if host.observe(wanted)[1]!=after:raise ValueError('hosts changed during verification')
            record.update(status='MANAGEMENT_HOSTNAMES_READY',changes=before!=after)
        except BaseException:
            # No unconditional rollback over somebody else's concurrent edits.
            try:
                if host.observe(wanted)[1]==after and before!=after:host.replace(before,observed)
            except Exception as error:record['rollback_error_type']=type(error).__name__
            record['status']='NEEDS_REVIEW';write(journal,record);raise
        write(journal,record);return record
    finally:os.close(fd)


def main(argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-hostnames','prepare-hostnames'])
    for name in ('request','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256');args=parser.parse_args(argv)
    try:
        if os.geteuid()!=0:raise ValueError('root-local hostname preparation required')
        print(json.dumps(execute(args)));return 0
    except Exception:
        print(json.dumps({'status':'HOSTNAMES_PREPARATION_REFUSED','services_restarted':False}));return 2
