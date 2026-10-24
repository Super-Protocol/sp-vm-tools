"""Persistent private management/VIP and host-NAT primitives; stdlib only."""
import argparse
import hashlib
import ipaddress as ip
import json
import os
from pathlib import Path
import re
import subprocess

PRIVATE=[ip.IPv4Network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]


def private(value):
    address=ip.IPv4Address(value)
    if not isinstance(value,str) or str(address)!=value or not any(address in n for n in PRIVATE):
        raise ValueError('canonical RFC1918 IPv4 required')
    return address


def interface(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}',value) or value=='lo':
        raise ValueError('explicit kernel interface required')
    return value


def validate(p):
    common={'schema_version','deployment_id','machine_sha256','phase','management_interface',
            'route_interface','management_address','guest_cidr','guest_route','transport_unit'}
    extra=({'vip'} if p.get('phase')=='management' else
           {'transit_address','router_ip','egress_interface','external_interface','host_interface','probe_sources','probe_ports'})
    if (set(p)!=common|extra or type(p['schema_version']) is not int or p['schema_version']!=1
            or p['phase'] not in ('management','nat') or not re.fullmatch('[a-z][a-z0-9-]{0,39}',p['deployment_id'])
            or not re.fullmatch('[a-f0-9]{64}',p['machine_sha256'])):raise ValueError('invalid host network policy')
    interface(p['management_interface']);interface(p['route_interface'])
    if p['transport_unit'] not in (None,'wg-quick@'+p['route_interface']+'.service'):
        raise ValueError('only an explicitly existing WireGuard transport unit is supported')
    address=ip.IPv4Interface(p['management_address']);private(str(address.ip))
    if str(address)!=p['management_address']:raise ValueError('canonical management address required')
    if address.network.prefixlen<31 and address.ip in (address.network.network_address,address.network.broadcast_address):
        raise ValueError('management network/broadcast address is not a host')
    guest=ip.IPv4Network(p['guest_cidr'],strict=True)
    if not 16<=guest.prefixlen<=30 or not any(guest.subnet_of(n) for n in PRIVATE) or address.network.overlaps(guest):
        raise ValueError('explicit nonoverlapping private guest /16../30 required')
    route=p['guest_route']
    if route is not None:
        if set(route)!={'gateway','interface','source'}:raise ValueError('invalid guest route')
        private(route['gateway']);private(route['source']);interface(route['interface'])
        if p['phase']=='management' and (route['source']!=str(address.ip) or route['interface']!=p['route_interface']):
            raise ValueError('guest probe route source/interface differs')
    if p['phase']=='management':
        if p['vip'] is not None:
            private(p['vip'])
            if ip.IPv4Address(p['vip']) in guest or p['vip']==str(address.ip):raise ValueError('distinct API VIP required')
            if address.network.prefixlen<31 and ip.IPv4Address(p['vip']) in (address.network.network_address,address.network.broadcast_address):
                raise ValueError('VIP cannot be a management network/broadcast address')
    else:
        transit=ip.IPv4Interface(p['transit_address']);private(str(transit.ip));private(p['router_ip'])
        if (str(transit)!=p['transit_address'] or not 16<=transit.network.prefixlen<=30
                or transit.network.overlaps(guest) or address.network.overlaps(transit.network)
                or ip.IPv4Address(p['router_ip']) not in transit.network
                or ip.IPv4Address(p['router_ip']) in (transit.ip,transit.network.network_address,transit.network.broadcast_address)
                or transit.ip in (transit.network.network_address,transit.network.broadcast_address)):
            raise ValueError('invalid nonoverlapping transit/router address')
        for key in ('egress_interface','external_interface','host_interface'):interface(p[key])
        if (p['external_interface']!='spvm-ext0' or p['host_interface']!='spvm-nat0'
                or len({p[k] for k in ('external_interface','host_interface','egress_interface')})!=3
                or {p['external_interface'],p['host_interface'],p['egress_interface']} & {p['management_interface'],p['route_interface']}
                or route!={'gateway':p['router_ip'],'interface':p['host_interface'],'source':str(address.ip)}):
            raise ValueError('NAT interface or probe route conflict')
        if (not isinstance(p['probe_sources'],list) or p['probe_sources']!=sorted(set(p['probe_sources']))
                or len(p['probe_sources'])>255 or not isinstance(p['probe_ports'],list)
                or not 1<=len(p['probe_ports'])<=15 or p['probe_ports']!=sorted(set(p['probe_ports']))
                or any(type(v) is not int or not 1<=v<=65535 for v in p['probe_ports'])):
            raise ValueError('explicit bounded probe sources/ports required')
        for source in p['probe_sources']:
            private(source)
            if ip.IPv4Address(source) in guest or ip.IPv4Address(source) in transit.network:
                raise ValueError('probe source overlaps guest/transit')
    return p


def run(args,runner=subprocess.run):
    result=runner(args,capture_output=True,text=True,timeout=45,
                  env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    if result.returncode or len(result.stdout)>4194304:raise ValueError('network operation failed; output suppressed')
    return result.stdout


def h(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def firewall(p):
    tag=hashlib.sha256(p['deployment_id'].encode()).hexdigest()[:12].upper()
    f='SPVM_HNF_'+tag;n='SPVM_HNN_'+tag;host=p['host_interface'];egress=p['egress_interface']
    transit=str(ip.IPv4Interface(p['transit_address']).network);guest=p['guest_cidr'];mgmt=p['route_interface']
    fr=[['-N',f],['-A',f,'-i',host,'-o',egress,'-s',transit,'-j','ACCEPT'],
        ['-A',f,'-i',egress,'-o',host,'-d',transit,'-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT']]
    nr=[['-N',n]];ports=','.join(map(str,p['probe_ports']))
    for source in p['probe_sources']:
        fr.extend([['-A',f,'-i',mgmt,'-o',host,'-s',source+'/32','-d',guest,'-p','tcp','-m','multiport','--dports',ports,'-j','ACCEPT'],
            ['-A',f,'-i',host,'-o',mgmt,'-s',guest,'-d',source+'/32','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT']])
        nr.append(['-A',n,'-s',source+'/32','-d',guest,'-o',host,'-p','tcp','-m','multiport','--dports',ports,
                   # The OVN security group admits management clients only.
                   # Use the same approved source as controller-local probes;
                   # conntrack restores the original peer on the return path.
                   '-j','SNAT','--to-source',str(ip.IPv4Interface(p['management_address']).ip)])
    fr.append(['-A',f,'-j','DROP']);nr.extend([['-A',n,'-s',transit,'-o',egress,'-j','MASQUERADE'],['-A',n,'-j','RETURN']])
    return {'filter':(fr,[['-A','FORWARD','-i',host,'-j',f],['-A','FORWARD','-o',host,'-j',f]]),
            'nat':(nr,[['-A','POSTROUTING','-o',host,'-d',guest,'-j',n],['-A','POSTROUTING','-s',transit,'-o',egress,'-j',n]])}


def canonical(row):
    if row[0]!='-A':return row
    if len(row)%2:raise ValueError('unsupported owned firewall syntax')
    pairs=[]
    for key,value in zip(row[2::2],row[3::2]):
        if key in ('-s','-d'):value=str(ip.IPv4Network(value,strict=False))
        if key in ('--ctstate','--dports'):value=','.join(sorted(value.split(',')))
        pairs.append((key,value))
    return [*row[:2],*sorted(pairs)]


def table_state(table,expected,runner):
    import shlex
    rows=[shlex.split(line) for line in run(['iptables','-w','5','-t',table,'-S'],runner).splitlines() if line.strip()]
    if not rows or not any(r[:2]==['-P','OUTPUT'] for r in rows):raise ValueError('complete firewall read required')
    owned,jumps=expected;chain=owned[0][1];actual=[];found=[];unowned=[]
    for row in rows:
        if row[:2] in (['-N',chain],['-A',chain]):actual.append(canonical(row))
        elif chain in row:
            if canonical(row) not in [canonical(j) for j in jumps]:raise ValueError('unexpected owned-chain reference')
            found.append(row)
        else:unowned.append(row)
    present=bool(actual or found)
    if present:
        if actual!=[canonical(r) for r in owned] or [canonical(r) for r in found]!=[canonical(j) for j in jumps]:
            raise ValueError('partial or conflicting owned firewall')
        parent=jumps[0][1]
        first=[r for r in rows if r[:2]==['-A',parent]][:len(jumps)]
        if [canonical(r) for r in first]!=[canonical(j) for j in jumps]:raise ValueError('owned jumps not at chain head')
    return {'ready':present,'unowned_sha256':h(unowned)}


def snapshot(p,runner=subprocess.run):
    validate(p)
    links=json.loads(run(['ip','-j','-d','link','show'],runner));addresses=json.loads(run(['ip','-j','address'],runner))
    devices={d['ifname']:d for d in links};addr={a['ifname']:a.get('addr_info',[]) for a in addresses}
    for name in {p['management_interface'],p['route_interface']}:
        if name not in devices or 'UP' not in devices[name].get('flags',[]):raise ValueError('existing UP management transport required')
    management=ip.IPv4Interface(p['management_address'])
    if not any(a.get('local')==str(management.ip) and a.get('prefixlen')==management.network.prefixlen for a in addr.get(p['management_interface'],[])):
        raise ValueError('management address must already exist; network manager is not rewritten')
    routes=json.loads(run(['ip','-j','route','show','table','all'],runner))
    v6=json.loads(run(['ip','-j','-6','route','show','default'],runner))
    defaults={r.get('dev') for r in routes+v6 if r.get('dst')=='default'}
    if {p['management_interface'],p['route_interface']} & defaults:raise ValueError('dedicated non-default management network required')
    transport=None
    if devices[p['route_interface']].get('linkinfo',{}).get('info_kind')=='wireguard' and not p['transport_unit']:
        raise ValueError('explicit existing wg-quick unit required for startup ordering')
    if p['transport_unit']:
        allowed=run(['wg','show',p['route_interface'],'allowed-ips'],runner)
        networks=[]
        for line in allowed.splitlines():
            parts=line.split(None,1)
            if len(parts)==2 and parts[1]!='(none)':
                networks.extend(ip.ip_network(n,strict=True) for n in re.split(r'[\s,]+',parts[1].strip()))
        destinations=([ip.IPv4Network(p['guest_cidr'])] if p['phase']=='management' and p['guest_route'] else [])
        if p['phase']=='nat':destinations.extend(ip.IPv4Network(source+'/32') for source in p['probe_sources'])
        if any(not any(n.version==4 and d.subnet_of(n) for n in networks) for d in destinations):
            raise ValueError('existing WireGuard AllowedIPs must already cover guest probes/replies')
        transport=h(allowed)
    vip=p.get('vip');vip_ready=vip is None
    if vip:
        seen=[(name,a) for name,items in addr.items() for a in items if a.get('local')==vip]
        if seen and (len(seen)!=1 or seen[0][0]!=p['management_interface'] or seen[0][1].get('prefixlen')!=32):
            raise ValueError('VIP already exists elsewhere or with another prefix')
        vip_ready=bool(seen)
    guest_route=p['guest_route'];route_ready=guest_route is None
    matches=[r for r in routes if r.get('dst')==p['guest_cidr'] and r.get('table','main')=='main']
    if guest_route:
        if matches and (len(matches)!=1 or any(matches[0].get(k)!=v for k,v in
            {'dev':guest_route['interface'],'gateway':guest_route['gateway'],'prefsrc':guest_route['source']}.items())):
            raise ValueError('guest route conflict; replace is forbidden')
        route_ready=bool(matches)
    owned_links=set();nat_ready=True;fw={};forward=None
    if p['phase']=='nat':
        owned_links={p['external_interface'],p['host_interface']}
        existing=owned_links & set(devices)
        if existing and existing!=owned_links:raise ValueError('partial veth pair')
        if existing:
            a,b=(devices[p[k]] for k in ('external_interface','host_interface'))
            def peer_matches(left,right):
                return (left['link_index']==right['ifindex'] if 'link_index' in left
                        else left.get('link')==right['ifname'])
            if (a.get('linkinfo',{}).get('info_kind')!='veth' or b.get('linkinfo',{}).get('info_kind')!='veth'
                    or not peer_matches(a,b) or not peer_matches(b,a)
                    or any('UP' not in d.get('flags',[]) for d in (a,b))):raise ValueError('existing veth pair differs')
            expected=ip.IPv4Interface(p['transit_address'])
            actual=[x for x in addr.get(p['host_interface'],[]) if x.get('family')=='inet']
            if len(actual)!=1 or actual[0]['local']!=str(expected.ip) or actual[0]['prefixlen']!=expected.network.prefixlen:
                raise ValueError('transit address conflict')
            if any(x.get('family')=='inet' for x in addr.get(p['external_interface'],[])):raise ValueError('OVS-side interface must be unnumbered')
        egress=devices.get(p['egress_interface'])
        if not egress or 'UP' not in egress.get('flags',[]) or p['egress_interface'] not in defaults:
            raise ValueError('explicit existing default-route egress required')
        fw={table:table_state(table,rules,runner) for table,rules in firewall(p).items()}
        if len({s['ready'] for s in fw.values()})!=1:raise ValueError('partial NAT/filter ownership')
        forward=run(['sysctl','-n','net.ipv4.ip_forward'],runner).strip()
        if forward not in ('0','1'):raise ValueError('forwarding state unknown')
        nat_ready=bool(existing) and all(s['ready'] for s in fw.values()) and forward=='1'
    def owned_route(r):
        if guest_route and r in matches:return True
        if vip and r.get('dev')==p['management_interface'] and r.get('dst') in (vip,vip+'/32') and r.get('type')=='local':return True
        if p['phase']=='nat' and r.get('dev') in owned_links and r.get('protocol')=='kernel':
            destination=r.get('dst','')
            if destination in ('fe80::/64','ff00::/8'):return True
            if ':' in destination:
                network=ip.IPv6Network(destination,strict=False)
                if network.prefixlen==128 and network.network_address.is_link_local and r.get('type')=='local':return True
        if p['phase']=='nat' and r.get('dev')==p['host_interface']:
            n=ip.IPv4Interface(p['transit_address']);allowed={str(n.network),str(n.ip),str(n.network.network_address),str(n.network.broadcast_address)}
            return r.get('dst','').removesuffix('/32') in allowed
        return False
    clean_links=[{k:d[k] for k in ('ifname','ifindex','flags','mtu','master','address') if k in d}
                 for d in links if d['ifname'] not in owned_links]
    clean_addr={name:sorted((a.get('family'),a.get('local'),a.get('prefixlen')) for a in items if a.get('local')!=vip)
                for name,items in addr.items() if name not in owned_links}
    protected={'links':h(clean_links),'addresses':h(clean_addr),
        'routes':h(sorted(json.dumps(r,sort_keys=True) for r in routes if not owned_route(r))),
        'ipv6_defaults':h(v6),'routing_policy':h(run(['ip','-j','rule','show'],runner)),
        'firewall':{t:v['unowned_sha256'] for t,v in fw.items()},'transport':transport}
    return {'protected':protected,'vip_ready':vip_ready,'route_ready':route_ready,'nat_ready':nat_ready,
            'veth_present':bool(owned_links & set(devices)),'forwarding':forward,
            'firewall_ready':{t:v['ready'] for t,v in fw.items()}}


def reconcile(p,runner=subprocess.run,*,links_only=False):
    if links_only and p['phase']!='nat':raise ValueError('link preparation is NAT-only')
    before=snapshot(p,runner)
    if p['phase']=='nat':
        if not before['veth_present']:
            run(['ip','link','add',p['external_interface'],'type','veth','peer','name',p['host_interface']],runner)
            run(['ip','address','add',p['transit_address'],'dev',p['host_interface']],runner)
            for name in (p['external_interface'],p['host_interface']):run(['ip','link','set',name,'up'],runner)
        if before['forwarding']!='1':run(['sysctl','-w','net.ipv4.ip_forward=1'],runner)
        if not links_only:
            states={t:table_state(t,rules,runner) for t,rules in firewall(p).items()}
            for table,(rows,jumps) in firewall(p).items():
                if not states[table]['ready']:
                    for row in rows:run(['iptables','-w','5','-t',table,*row],runner)
                    for row in reversed(jumps):run(['iptables','-w','5','-t',table,'-I',row[1],'1',*row[2:]],runner)
    elif not before['vip_ready']:
        run(['ip','address','add',p['vip']+'/32','dev',p['management_interface']],runner)
    if not before['route_ready']:
        r=p['guest_route'];run(['ip','route','add',p['guest_cidr'],'via',r['gateway'],'dev',r['interface'],'src',r['source']],runner)
    after=snapshot(p,runner)
    ready=all(after[k] for k in ('vip_ready','route_ready'))
    ready=ready and ((after['veth_present'] and after['forwarding']=='1') if links_only else after['nat_ready'])
    if after['protected']!=before['protected'] or not ready:
        raise ValueError('network postcheck failed; manual review required')
    return after


def load(path):
    path=Path(path)
    if path.resolve()!=path or not path.is_file() or path.stat().st_size>65536:raise ValueError('bounded protected policy required')
    for item in (path,*path.parents):
        if item.stat().st_uid!=0 or item.stat().st_mode & 0o022:raise ValueError('root-owned protected policy required')
    if path.stat().st_mode & 0o777!=0o600:raise ValueError('root0600 policy required')
    p=validate(json.loads(path.read_text()))
    machine=hashlib.sha256(Path('/etc/machine-id').read_text().strip().encode()).hexdigest()
    if machine!=p['machine_sha256']:raise ValueError('wrong physical host')
    return p


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    parser.add_argument('--links-only',action='store_true');args=parser.parse_args()
    try:
        if os.geteuid()!=0:raise ValueError('root required')
        reconcile(load(args.config),links_only=args.links_only)
        print(json.dumps({'status':'HOST_NETWORK_CONFIGURED_NOT_CONNECTIVITY_ACCEPTED'}))
    except Exception:
        print('ERROR: host network action failed; no automatic rollback; inspect owned state.',flush=True);raise SystemExit(2)
