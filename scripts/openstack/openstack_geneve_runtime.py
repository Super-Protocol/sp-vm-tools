"""Root-owned persistent IPv4 Geneve allowlist. No routes, NAT or policy changes."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess


def validate(value):
    if (not isinstance(value,dict) or set(value)!={'schema_version','chain','interface','local_ip','peers'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or not all(isinstance(value[k],str) for k in ('chain','interface','local_ip'))
            or not re.fullmatch(r'SPVM_GNV_[A-F0-9]{16}',str(value['chain']))
            or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',str(value['interface']))
            or value['interface']=='lo' or not isinstance(value['peers'],list) or len(value['peers'])>255):
        raise ValueError('invalid Geneve policy')
    addresses=[value['local_ip'],*value['peers']]
    for address in addresses:
        if not isinstance(address,str):raise ValueError('IPv4 string required')
        ip=ipaddress.IPv4Address(address)
        if (str(ip)!=address or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_unspecified or int(ip)==2**32-1):raise ValueError('explicit usable IPv4 required')
    if len(set(addresses))!=len(addresses) or value['peers']!=sorted(value['peers']):
        raise ValueError('unique sorted peers excluding local IP required')
    return value


def command(args,runner=subprocess.run):
    result=runner(args,capture_output=True,text=True,timeout=45,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    if result.returncode or len(result.stdout)>4194304:raise ValueError('network command failed; output suppressed')
    return result.stdout


def rules(policy):
    chain=policy['chain']
    members=[['-A',chain,'-s',ip+'/32','-j','ACCEPT'] for ip in policy['peers']]
    members.append(['-A',chain,'-j','DROP'])
    jump=['-A','INPUT','-i',policy['interface'],'-d',policy['local_ip']+'/32',
          '-p','udp','-m','udp','--dport','6081','-j',chain]
    return [['-N',chain],*members],jump


def snapshot(policy,runner=subprocess.run):
    validate(policy)
    rows=[shlex.split(line) for line in command(['iptables','-w','5','-S'],runner).splitlines() if line.strip()]
    policies=[r for r in rows if r[0]=='-P']
    if (len(policies)!=3 or any(len(r)!=3 or r[2] not in ('ACCEPT','DROP') for r in policies)
            or {r[1] for r in policies}!={'INPUT','FORWARD','OUTPUT'}):
        raise ValueError('complete filter policy observation required')
    expected,jump=rules(policy);owned=[];unowned=[];jumps=[]
    def same_jump(row):
        # iptables prints addresses/interfaces in canonical order, not argv order.
        return (row[:2]==jump[:2] and len(row)==len(jump)
            and len(set(row[2::2]))==len(row[2::2])
            and dict(zip(row[2::2],row[3::2]))==dict(zip(jump[2::2],jump[3::2])))
    for row in rows:
        if same_jump(row):jumps.append(row)
        elif row[:2] in (['-N',policy['chain']],['-A',policy['chain']]):owned.append(row)
        elif policy['chain'] in row:raise ValueError('unreviewed reference to owned chain')
        else:unowned.append(row)
    state='ABSENT'
    if owned or jumps:
        if owned!=expected or len(jumps)!=1:raise ValueError('existing Geneve chain differs or is incomplete')
        if next((row for row in rows if row[:2]==['-A','INPUT']),None)!=jumps[0]:
            raise ValueError('Geneve jump must precede other INPUT rules')
        state='READY'
    nat=command(['iptables','-w','5','-t','nat','-S'],runner)
    if not nat.strip():raise ValueError('NAT observation unavailable')
    return {'owned':state,'unowned_filter_sha256':hashlib.sha256(json.dumps(unowned).encode()).hexdigest(),
            'nat_sha256':hashlib.sha256(nat.encode()).hexdigest()}


def reconcile(policy,runner=subprocess.run):
    before=snapshot(policy,runner)
    if before['owned']=='READY':return False
    members,jump=rules(policy)
    for row in members:command(['iptables','-w','5',*row],runner)
    command(['iptables','-w','5','-I','INPUT','1',*jump[2:]],runner)
    after=snapshot(policy,runner)
    if after!={**before,'owned':'READY'}:raise ValueError('unrelated firewall state changed')
    return True


def load(path):
    path=Path(path)
    if not path.is_absolute() or path.resolve()!=path or not path.is_file() or path.stat().st_size>65536:
        raise ValueError('bounded root-owned policy required')
    for item in (path,*path.parents):
        st=item.stat()
        if st.st_uid!=0 or st.st_mode & 0o022:raise ValueError('protected policy path required')
    if path.stat().st_mode & 0o777!=0o600:raise ValueError('root0600 policy required')
    return validate(json.loads(path.read_text()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    try:
        if os.geteuid()!=0:raise ValueError('root required')
        changed=reconcile(load(args.config))
        print(json.dumps({'status':'GENEVE_GUARD_READY','changed':changed}))
        return 0
    except Exception:
        print('ERROR: Geneve guard refused; existing firewall was not flushed.',flush=True)
        return 2


if __name__=='__main__':raise SystemExit(main())
