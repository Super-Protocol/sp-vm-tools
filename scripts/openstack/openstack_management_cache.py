"""Explicit stale networkd-cache repair for a dedicated dummy, never a bond.

Called only by the approved fresh workflow with repair_stale_networkd_cache=true.
No active network file is overwritten. A manager reload is explicitly approved;
only the dedicated dummy receives a per-interface reconfigure command.
"""
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess

from openstack_container_engine import write


def run(argv,runner=subprocess.run):
    p=runner(argv,capture_output=True,text=True,timeout=45,
             env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    if p.returncode or len(p.stdout)>4194304:raise ValueError('management cache observation/action failed')
    return p.stdout


def observe(interface,runner=subprocess.run):
    links=json.loads(run(['ip','-j','-d','link','show'],runner))
    addresses=json.loads(run(['ip','-j','address','show'],runner))
    routes=json.loads(run(['ip','-j','route','show','table','all'],runner))
    defaults=json.loads(run(['ip','-j','-6','route','show','default'],runner))
    v6routes=json.loads(run(['ip','-j','-6','route','show','table','all'],runner))
    own=[v for v in links if v['ifname']==interface]
    if len(own)!=1 or any(r.get('dev')==interface and r.get('dst')=='default' for r in routes+defaults):
        raise ValueError('existing dedicated non-default interface required')
    return {'kind':own[0].get('linkinfo',{}).get('info_kind'),
            'addresses':[a for link in addresses if link['ifname']==interface for a in link.get('addr_info',[]) if a.get('family')=='inet'],
            'protected':{'links':[{k:v[k] for k in ('ifname','ifindex','flags','master','mtu') if k in v} for v in links if v['ifname']!=interface],
              'addresses':{v['ifname']:sorted((a.get('family'),a.get('local'),a.get('prefixlen')) for a in v.get('addr_info',[]))
                           for v in addresses if v['ifname']!=interface},
              'routes':sorted(json.dumps(r,sort_keys=True) for r in routes if r.get('dev')!=interface),
              'ipv6_defaults':defaults,'rules':json.loads(run(['ip','-j','rule','show'],runner)),
              'ipv6_routes':sorted(json.dumps(r,sort_keys=True) for r in v6routes if r.get('dev')!=interface),
              'ipv6_rules':json.loads(run(['ip','-j','-6','rule','show'],runner))}}


def needed(before,address,vip):
    expected=ipaddress.IPv4Interface(address)
    if not any(a['local']==str(expected.ip) and a['prefixlen']==expected.network.prefixlen for a in before['addresses']):return True
    return any(a['local']==vip and a['prefixlen']!=32 for a in before['addresses']) if vip else False


def prepare(policy,output,runner=subprocess.run):
    output=Path(output)
    if output.exists():raise ValueError('previous management cache transaction requires review')
    if set(policy)!={'machine_sha256','interface','address','vip','allow_reload'} or type(policy['allow_reload']) is not bool:
        raise ValueError('explicit management cache policy required')
    if hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()!=policy['machine_sha256']:
        raise ValueError('wrong management host')
    interface=policy['interface'];address=ipaddress.IPv4Interface(policy['address']);vip=policy['vip']
    before=observe(interface,runner)
    if not needed(before,str(address),vip):return {'status':'MANAGEMENT_ADDRESS_ALREADY_READY','changes':False}
    if before['kind']!='dummy' or not policy['allow_reload']:
        raise ValueError('management address conflict; no approved dummy-cache repair')
    allowed={str(address.ip)}|({vip} if vip else set())
    if any(a['local'] not in allowed for a in before['addresses']):raise ValueError('foreign dummy address; refuse adoption')
    metadata=json.loads(run(['networkctl','status',interface,'--json=short'],runner))
    cached=metadata.get('NetworkFile')
    if (not isinstance(cached,str) or not any(Path(cached).is_relative_to(Path(p)) for p in ('/etc/systemd/network','/run/systemd/network'))
            or Path(cached).exists() or Path(cached).is_symlink()):
        raise ValueError('not a positively identified deleted networkd file; refuse active policy rewrite')
    if run(['virsh','-c','qemu:///system','list','--all','--uuid'],runner).strip():raise ValueError('cache repair requires empty host')
    output.mkdir(mode=0o700)
    write(output/'plan.json',{'policy':policy,'before':before,'deleted_network_file':cached})
    record={'status':'RUNNING','changes':True,'physical_reconfigure_command_issued':False}
    write(output/'transaction.json',record)
    try:
        if observe(interface,runner)!=before:raise ValueError('network changed before approved repair')
        # Reload is explicit in the parent intent. Only the dummy gets a
        # reconfigure command; all unrelated addresses/routes are checked.
        run(['networkctl','reload'],runner);run(['networkctl','reconfigure',interface],runner)
        refreshed=json.loads(run(['networkctl','status',interface,'--json=short'],runner))
        if refreshed.get('NetworkFile'):raise ValueError('dummy acquired another active networkd policy; refuse address rewrite')
        current=observe(interface,runner)
        if current['protected']!=before['protected']:raise ValueError('unrelated network changed after networkd reload')
        if any(a['local'] not in allowed for a in current['addresses']):raise ValueError('foreign address appeared')
        for a in current['addresses']:
            desired=(a['local']==str(address.ip) and a['prefixlen']==address.network.prefixlen) or (a['local']==vip and a['prefixlen']==32)
            if not desired:run(['ip','address','del',a['local']+'/'+str(a['prefixlen']),'dev',interface],runner)
        current=observe(interface,runner)
        if not any(a['local']==str(address.ip) and a['prefixlen']==address.network.prefixlen for a in current['addresses']):
            run(['ip','address','add',str(address),'dev',interface],runner)
        after=observe(interface,runner)
        if after['protected']!=before['protected'] or needed(after,str(address),vip):raise ValueError('management cache repair postcheck failed')
        record['status']='STALE_MANAGEMENT_CACHE_RECONCILED'
    except BaseException:
        record['status']='NEEDS_REVIEW';write(output/'transaction.json',record);raise
    write(output/'transaction.json',record);return record
