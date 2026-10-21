"""Root-local plan/apply for existing management transport and isolated host-NAT."""
import argparse
import fcntl
import hashlib
import importlib.util
import ipaddress as ip
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_geneve_guard as guard
import openstack_initial_probe as probe
import openstack_network_runtime as runtime
from openstack_firewall_managers import require_unmanaged

RUNTIME='/usr/local/libexec/spvm-host-network.py'


def inputs(args,backend):
    cfg=backend.deployment_config(args.config);request=json.loads(read(args.network_request))
    if (set(request)!={'schema_version','hosts','vip_owner','guest_cidr','nat'}
            or type(request['schema_version']) is not int or request['schema_version']!=1
            or set(request['hosts'])!=set(cfg['hosts']) or args.host not in cfg['hosts']):
        raise ValueError('complete explicit host network request required')
    controls=[n for n,h in cfg['hosts'].items() if 'control' in h['roles']]
    networks=[n for n,h in cfg['hosts'].items() if 'network' in h['roles']]
    if len(controls)!=1 or request['vip_owner']!=controls[0] or len(networks)!=1 or cfg['network']['mode']!='host-nat':
        raise ValueError('initial profile supports one static-VIP controller and one host-NAT network node')
    nat=request['nat']
    if set(nat)!={'host','egress_interface','transit_address','router_ip','probe_ports'} or nat['host']!=networks[0]:
        raise ValueError('explicit host-NAT configuration required')
    guest=ip.IPv4Network(request['guest_cidr'],strict=True);runtime.private(cfg['network']['api_vip'])
    transit=ip.IPv4Interface(nat['transit_address'])
    if str(transit.network)!=cfg['network']['transit_cidr']:raise ValueError('transit differs from deployment config')
    machines=set();addresses=set()
    for name,node in request['hosts'].items():
        if (not {'machine_sha256','management_address','route_interface'}<=set(node)
                or set(node)-{'machine_sha256','management_address','route_interface','transport_unit'}
                or not re.fullmatch('[a-f0-9]{64}',str(node['machine_sha256'])) or node['machine_sha256'] in machines):
            raise ValueError('unique audited host identities required')
        address=ip.IPv4Interface(node['management_address']);runtime.private(str(address.ip));runtime.interface(node['route_interface'])
        runtime.interface(cfg['hosts'][name]['management_interface'])
        if node.get('transport_unit') not in (None,'wg-quick@'+node['route_interface']+'.service'):
            raise ValueError('invalid existing transport unit')
        if (str(address)!=node['management_address'] or address.ip in addresses or address.ip in guest
                or address.network.overlaps(transit.network) or address.network.overlaps(guest)
                or str(address.ip)==cfg['network']['api_vip']):
            raise ValueError('management addresses collide or overlap guest/transit/VIP')
        machines.add(node['machine_sha256']);addresses.add(address.ip)
    if ip.IPv4Address(cfg['network']['api_vip']) in guest or ip.IPv4Address(cfg['network']['api_vip']) in transit.network:
        raise ValueError('VIP overlaps guest/transit')
    def make_policy(name,phase):
        host=cfg['hosts'][name];node=request['hosts'][name]
        p={'schema_version':1,'deployment_id':cfg['deployment_id'],'machine_sha256':node['machine_sha256'],
           'phase':phase,'management_interface':host['management_interface'],'route_interface':node['route_interface'],
           'management_address':node['management_address'],'guest_cidr':str(guest),'guest_route':None,
           'transport_unit':node.get('transport_unit')}
        if phase=='management':
            p['vip']=cfg['network']['api_vip'] if name==request['vip_owner'] else None
            if name!=nat['host'] and 'compute' in host['roles']:
                p['guest_route']={'gateway':str(ip.IPv4Interface(request['hosts'][nat['host']]['management_address']).ip),
                                  'interface':node['route_interface'],'source':str(ip.IPv4Interface(node['management_address']).ip)}
        else:
            p.update(transit_address=nat['transit_address'],router_ip=nat['router_ip'],egress_interface=nat['egress_interface'],
                external_interface='spvm-ext0',host_interface='spvm-nat0',probe_ports=nat['probe_ports'],
                probe_sources=sorted(str(ip.IPv4Interface(info['management_address']).ip) for peer,info in request['hosts'].items()
                                     if peer!=name and 'compute' in cfg['hosts'][peer]['roles']),
                guest_route={'gateway':nat['router_ip'],'interface':'spvm-nat0','source':str(transit.ip)})
        return runtime.validate(p)
    # Reject an impossible second phase before authorizing changes in the first.
    policies={name:make_policy(name,'management') for name in request['hosts']}
    nat_policy=make_policy(nat['host'],'nat')
    phase=args.action.split('-',1)[1];own=request['hosts'][args.host]
    if phase=='nat' and args.host!=nat['host']:raise ValueError('NAT preparation runs only on the selected network node')
    p=nat_policy if phase=='nat' else policies[args.host]
    binding={'config_sha256':digest(cfg),'request_sha256':file_hash(args.network_request),'host':args.host,
        'machine_sha256':own['machine_sha256'],'allow_ubuntu26':args.allow_experimental_ubuntu26,
        'sources':{q.name:file_hash(q) for q in Path(__file__).parent.glob('*.py')},
        'backend_sources':{q.relative_to(Path(backend.__file__).parent).as_posix():file_hash(q)
                           for q in Path(backend.__file__).parent.rglob('*.py')},
        'peer_routes':sorted(str(a) for a in addresses if a!=ip.IPv4Interface(own['management_address']).ip)}
    if args.host!=request['vip_owner']:binding['peer_routes'].append(cfg['network']['api_vip'])
    return cfg,p,binding


def artifacts(cfg,p):
    name='spvm-'+p['phase']+'-'+cfg['deployment_id'];config='/etc/spvm-host-network/'+name+'.json';unit=name+'.service'
    dependencies=['network-online.target','spvm-geneve-'+cfg['deployment_id']+'.service']
    requires=[]
    if p['transport_unit']:dependencies.append(p['transport_unit']);requires.append(p['transport_unit'])
    if p['phase']=='nat':
        management='spvm-management-'+cfg['deployment_id']+'.service';dependencies.append(management);requires.append(management)
        transit='spvm-transit-'+cfg['deployment_id']+'.service'
        transit_dependencies=list(dependencies);transit_requires=list(requires)
        dependencies.extend(['docker.service',transit]);requires.extend(['docker.service',transit])
    content=('[Unit]\nDescription=SPVM '+p['phase']+' host network\nWants=network-online.target\n'
        'After='+' '.join(dependencies)+'\n'+('Requires='+' '.join(requires)+'\n' if requires else '')+
        ('Before=docker.service\n' if p['phase']=='management' else '')+'\n[Service]\nType=oneshot\nRemainAfterExit=yes\n'
        'ExecStart=/usr/bin/python3 -I -B '+RUNTIME+' --config '+config+'\n'
        'NoNewPrivileges=yes\nProtectHome=yes\nProtectSystem=full\nCapabilityBoundingSet=CAP_NET_ADMIN\n'
        '\n[Install]\nWantedBy=multi-user.target\n')
    files={RUNTIME:(read(Path(__file__).with_name('openstack_network_runtime.py')),0o644),
        config:((json.dumps(p,sort_keys=True,indent=2)+'\n').encode(),0o600),
        '/etc/systemd/system/'+unit:(content.encode(),0o644)}
    if p['phase']=='nat':
        files['/etc/sysctl.d/90-'+name+'.conf']=(b'net.ipv4.ip_forward=1\n',0o644)
        link_unit=('[Unit]\nDescription=SPVM pre-Docker isolated transit\nWants=network-online.target\n'
            'After='+' '.join(transit_dependencies)+'\nRequires='+' '.join(transit_requires)+'\n'
            'Before=docker.service\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\n'
            'ExecStart=/usr/bin/python3 -I -B '+RUNTIME+' --config '+config+' --links-only\n'
            'NoNewPrivileges=yes\nProtectHome=yes\nProtectSystem=full\nCapabilityBoundingSet=CAP_NET_ADMIN\n')
        files['/etc/systemd/system/'+transit]=(link_unit.encode(),0o644)
    return unit,files


class Host(guard.Host):
    def inspect(self,cfg,p,b,files,unit,*,installed=False):
        wanted={'machine_sha256':b['machine_sha256'],'roles':cfg['hosts'][b['host']]['roles'],
                'interfaces':[p['management_interface'],p['route_interface']],'api_vip':None,
                'allow_ubuntu26':b['allow_ubuntu26'],'guard_only':True,'protect_package_runtime':True}
        facts=probe.probe(wanted,self.root,self.runner)
        if facts['blockers']:raise ValueError('fresh-host guard blocked network changes')
        if 'compute' in wanted['roles'] and not {'/usr/bin/virsh','/usr/bin/qemu-system-x86_64'}<=set(facts['protected_package_files']):
            raise ValueError('hardware bootstrap required separately')
        require_unmanaged(lambda argv:runtime.run(argv,self.runner),
            ('ufw.service','firewalld.service','nftables.service','netfilter-persistent.service'))
        if p['transport_unit'] and runtime.run(['systemctl','is-active',p['transport_unit']],self.runner).strip()!='active':
            raise ValueError('explicit existing WireGuard transport must already be active')
        if p['phase']=='nat':
            if runtime.run(['systemctl','is-active','docker.service'],self.runner).strip()!='active':
                raise ValueError('existing systemd Docker daemon must already be active')
            management={k:p[k] for k in ('schema_version','deployment_id','machine_sha256','management_interface',
                        'route_interface','management_address','guest_cidr','transport_unit')}
            management.update(phase='management',guest_route=None,
                vip=cfg['network']['api_vip'] if 'control' in cfg['hosts'][b['host']]['roles'] else None)
            mu,mfiles=artifacts(cfg,management)
            for path,(body,mode) in mfiles.items():
                target=self.trusted(path)
                if not target.is_file() or target.read_bytes()!=body or target.stat().st_mode & 0o777!=mode:
                    raise ValueError('matching management phase must be installed before NAT')
            if runtime.run(['systemctl','is-active',mu],self.runner).strip()!='active':
                raise ValueError('management phase must be active before NAT')
            management_state=runtime.snapshot(management,self.runner)
            if not all(management_state[k] for k in ('vip_ready','route_ready','nat_ready')):
                raise ValueError('management kernel state drifted before NAT')
        for path,(body,mode) in files.items():
            target=self.trusted(path)
            if target.exists():
                if (not target.is_file() or target.stat().st_mode & 0o777!=mode or target.read_bytes()!=body):
                    raise ValueError('network artifact conflict')
                if not installed and path!=RUNTIME:raise ValueError('existing phase requires maintenance, not initial install')
            elif installed:raise ValueError('installed artifact missing')
        for peer in b['peer_routes']:
            route=json.loads(runtime.run(['ip','-j','route','get',peer],self.runner))
            if len(route)!=1 or route[0].get('dev')!=p['route_interface']:raise ValueError('existing private management peer/VIP route required')
        state=runtime.snapshot(p,self.runner)
        if not installed and p['phase']=='nat' and state['veth_present']:raise ValueError('refusing to adopt existing external veth')
        if not installed and p['phase']=='nat' and any(state['firewall_ready'].values()):
            raise ValueError('refusing to adopt existing NAT firewall ownership')
        link=self.path('/etc/systemd/system/multi-user.target.wants/'+unit)
        self.trusted('/etc/systemd/system/multi-user.target.wants')
        if installed:
            if not link.is_symlink() or link.lstat().st_uid!=self.owner or link.resolve()!=self.path('/etc/systemd/system/'+unit):
                raise ValueError('enablement link differs')
            for action,value in (('is-active','active'),('is-enabled','enabled')):
                if runtime.run(['systemctl',action,unit],self.runner).strip()!=value:raise ValueError('network unit not active/enabled')
            if not all(state[k] for k in ('vip_ready','route_ready','nat_ready')):raise ValueError('owned network not ready')
            if p['phase']=='nat' and runtime.run(['systemctl','is-active','spvm-transit-'+cfg['deployment_id']+'.service'],self.runner).strip()!='active':
                raise ValueError('transit dependency must be active')
        else:
            if link.exists() or link.is_symlink():raise ValueError('existing enablement link')
            if runtime.run(['systemctl','show',unit,'--property=LoadState','--value'],self.runner).strip()!='not-found':
                raise ValueError('existing network unit')
            if p['phase']=='nat' and runtime.run(['systemctl','show','spvm-transit-'+cfg['deployment_id']+'.service',
                   '--property=LoadState','--value'],self.runner).strip()!='not-found':raise ValueError('existing transit unit')
        # Management phase must not touch either firewall table.
        untouched={}
        if p['phase']=='management':
            untouched={t:digest(runtime.run(['iptables','-w','5','-t',t,'-S'],self.runner)) for t in ('filter','nat')}
        return {'host_guard':facts,'protected':state['protected'],'firewall_untouched':untouched,'state':state}

    def install(self,files,unit):
        missing={path:spec for path,spec in files.items() if not (path==RUNTIME and self.path(path).exists())}
        super().install(missing,unit)


def execute(args,backend,host=None):
    started=time.time();cfg,p,b=inputs(args,backend);host=Host() if host is None else host
    phase=p['phase'];unit,files=artifacts(cfg,p);before=host.inspect(cfg,p,b,files,unit)
    if time.time()-started>900:raise ValueError('host observation expired')
    output=Path(args.output)
    if (not output.is_absolute() or output.resolve()!=output or any(output.is_relative_to(Path(n)) for n in
        ('/etc','/usr/local/libexec')) or any(Path(n).is_relative_to(output) for n in files)):
        raise ValueError('separate absolute private transaction directory required')
    plan={'schema_version':1,'kind':'host-network-'+phase+'-v1','binding':b,'policy':p,'before':before,
          'files':{n:{'sha256':hashlib.sha256(body).hexdigest(),'mode':mode} for n,(body,mode) in files.items()},
          'deploy_authorized':False,'guests_launched':False}
    if args.action=='plan-'+phase:
        output.mkdir(mode=0o700,exist_ok=False);plan.update(created_at=int(time.time()));plan['expires_at']=plan['created_at']+900
        plan['plan_sha256']=digest(plan);write(output/'network-plan.json',plan)
        return {'status':'HOST_NETWORK_PLAN_READY',**plan}
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:raise ValueError('private owned transaction required')
    old=json.loads(read(output/'network-plan.json'));body={k:v for k,v in old.items() if k!='plan_sha256'}
    if (type(old.get('created_at')) is not int or old.get('expires_at')!=old['created_at']+900
            or not 0<=time.time()-old['created_at']<=900 or old.get('plan_sha256')!=digest(body)
            or args.plan_sha256!=old['plan_sha256']):raise ValueError('fresh approved network plan required')
    plan.update(created_at=old['created_at'],expires_at=old['expires_at'])
    if plan!=body:raise ValueError('network/input drift since approval')
    lock=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);journal=output/'network-transaction.json'
        if journal.exists() or journal.is_symlink():raise ValueError('already attempted; review before retry')
        record={'schema_version':1,'phase':phase,'status':'INSTALLING_HOST_NETWORK','plan_sha256':args.plan_sha256,
                'deploy_authorized':False,'guests_launched':False};write(journal,record)
        try:
            host.install(files,unit)
            if inputs(args,backend)[2]!=b:raise ValueError('network source/input drift during apply')
            after=host.inspect(cfg,p,b,files,unit,installed=True)
            if any(before[k]!=after[k] for k in ('host_guard','protected','firewall_untouched')):
                raise ValueError('unowned network/runtime state changed')
            record.update(status='HOST_NETWORK_CONFIGURED_NOT_CONNECTIVITY_ACCEPTED',after=after)
        except BaseException:
            record['status']='NEEDS_REVIEW_HOST_NETWORK';write(journal,record);raise
        write(journal,record);return record
    finally:os.close(lock)


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-management','prepare-management','plan-nat','prepare-nat'])
    for name in ('config','network-request','host','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256');parser.add_argument('--allow-experimental-ubuntu26',action='store_true');args=parser.parse_args(argv)
    try:
        if os.geteuid()!=0:raise ValueError('run root-locally on the selected host')
        sys.path.insert(0,str(Path(backend_path).parent));spec=importlib.util.spec_from_file_location('spvm_network_backend',backend_path)
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        result=execute(args,backend);print(json.dumps({k:v for k,v in result.items() if k in
            ('status','phase','plan_sha256','deploy_authorized','guests_launched')}));return 0
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: host network preparation refused; no automatic rollback. '+type(exc).__name__+' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
