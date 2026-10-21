"""Plan/install a scoped persistent Geneve guard on one fresh root-local host."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_geneve_runtime as runtime
import openstack_initial_probe as initial_probe
from openstack_firewall_managers import require_unmanaged

RUNTIME='/usr/local/libexec/spvm-geneve-guard.py'


def inputs(args,backend):
    cfg=backend.deployment_config(args.config)
    request=json.loads(read(args.network_request))
    participants={n for n,h in cfg['hosts'].items() if set(h['roles']) & {'network','compute'}}
    if (not isinstance(request,dict) or set(request)!={'schema_version','hosts'}
            or type(request['schema_version']) is not int or request['schema_version']!=1
            or not isinstance(request['hosts'],dict) or set(request['hosts'])!=participants
            or args.host not in participants):raise ValueError('explicit complete Geneve host map required')
    identities=set();addresses=set()
    for node in request['hosts'].values():
        if (not isinstance(node,dict) or set(node)!={'machine_sha256','tunnel_ip'}
                or not re.fullmatch('[a-f0-9]{64}',str(node['machine_sha256']))
                or node['machine_sha256'] in identities):raise ValueError('unique audited machine identities required')
        runtime.validate({'schema_version':1,'chain':'SPVM_GNV_'+'A'*16,'interface':'test0',
                          'local_ip':node['tunnel_ip'],'peers':[]})
        if node['tunnel_ip'] in addresses:raise ValueError('duplicate tunnel IP')
        identities.add(node['machine_sha256']);addresses.add(node['tunnel_ip'])
    own=request['hosts'][args.host]
    policy=runtime.validate({'schema_version':1,'chain':'SPVM_GNV_'+digest(cfg['deployment_id'])[:16].upper(),
        'interface':cfg['hosts'][args.host]['tunnel_interface'],'local_ip':own['tunnel_ip'],
        'peers':sorted(addresses-{own['tunnel_ip']})})
    binding={'config_sha256':digest(cfg),'network_request_sha256':file_hash(args.network_request),
        'machine_sha256':own['machine_sha256'],'host':args.host,
        'allow_ubuntu26':args.allow_experimental_ubuntu26,
        'sources':{n:file_hash(Path(__file__).with_name(n)) for n in
            ('openstack_geneve_guard.py','openstack_geneve_runtime.py','openstack_initial_probe.py',
             'openstack_container_engine.py','openstack_compute_image.py','openstack_firewall_managers.py')}}
    backend_root=Path(backend.__file__).parent
    binding['backend_sources_sha256']=digest({p.relative_to(backend_root).as_posix():file_hash(p)
        for p in backend_root.rglob('*.py') if '__pycache__' not in p.parts})
    return cfg,policy,binding


def artifacts(cfg,policy):
    name='spvm-geneve-'+cfg['deployment_id']
    config='/etc/spvm-network/'+name+'.json'
    unit=('[Unit]\nDescription=SPVM registered-host IPv4 Geneve guard\n'
          'Wants=network-online.target\nAfter=network-online.target\nBefore=docker.service\n\n'
          '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
          'ExecStart=/usr/bin/python3 -I -B '+RUNTIME+' --config '+config+'\n'
          'NoNewPrivileges=yes\nProtectHome=yes\nProtectSystem=full\n'
          'CapabilityBoundingSet=CAP_NET_ADMIN\n\n[Install]\nWantedBy=multi-user.target\n')
    return name+'.service',{
        RUNTIME:(read(Path(__file__).with_name('openstack_geneve_runtime.py')),0o644),
        config:((json.dumps(policy,sort_keys=True,indent=2)+'\n').encode(),0o600),
        '/etc/systemd/system/'+name+'.service':(unit.encode(),0o644)}


class Host:
    def __init__(self,root=Path('/'),runner=subprocess.run):
        self.root=Path(root);self.runner=runner
        self.owner=0 if self.root==Path('/') else os.getuid()

    def path(self,name):return self.root/name.lstrip('/')

    def trusted(self,name):
        p=self.path(name)
        for item in (p,*p.parents):
            if item.exists() or item.is_symlink():
                st=item.lstat()
                if item.is_symlink() or st.st_uid!=self.owner or st.st_mode & 0o022:
                    raise ValueError('unprotected network artifact path')
            if item==self.root:break
        return p

    def inspect(self,cfg,policy,binding,files,unit,*,installed=False):
        wanted={'machine_sha256':binding['machine_sha256'],'roles':cfg['hosts'][binding['host']]['roles'],
            'interfaces':[policy['interface']],'api_vip':None,'allow_ubuntu26':binding['allow_ubuntu26'],
            'guard_only':True,'protect_package_runtime':True}
        facts=initial_probe.probe(wanted,self.root,self.runner)
        if facts['blockers']:raise ValueError('fresh-host guard blocked network preparation')
        if 'compute' in wanted['roles'] and not {'/usr/bin/virsh','/usr/bin/qemu-system-x86_64'}<=set(facts['protected_package_files']):
            raise ValueError('prepare host QEMU/libvirt separately first')
        require_unmanaged(lambda argv:runtime.command(argv,self.runner))
        for name,(body,mode) in files.items():
            path=self.trusted(name)
            if not installed:
                if path.exists():raise ValueError('existing network artifacts require review')
            elif (not path.is_file() or path.stat().st_mode & 0o777!=mode
                    or path.read_bytes()!=body):raise ValueError('installed network artifact differs')
        directory=self.trusted('/etc/spvm-network')
        if not installed and directory.exists() and any(directory.iterdir()):
            raise ValueError('existing network namespace requires review')
        devices=json.loads(runtime.command(['ip','-j','address','show','dev',policy['interface']],self.runner))
        if len(devices)!=1 or 'UP' not in devices[0].get('flags',[]):raise ValueError('existing UP tunnel interface required')
        ips=sorted([a['local'],a['prefixlen']] for a in devices[0].get('addr_info',[]) if a.get('family')=='inet')
        if len(ips)!=1 or ips[0][0]!=policy['local_ip']:
            raise ValueError('selected interface must have exactly the explicit tunnel IPv4')
        firewall=runtime.snapshot(policy,self.runner)
        if firewall['owned']!=('READY' if installed else 'ABSENT'):raise ValueError('unexpected Geneve firewall ownership')
        if installed:
            for verb,expected in (('is-active','active'),('is-enabled','enabled')):
                if runtime.command(['systemctl',verb,unit],self.runner).strip()!=expected:raise ValueError('guard unit not enabled/active')
        elif runtime.command(['systemctl','show',unit,'--property=LoadState','--value'],self.runner).strip()!='not-found':
            raise ValueError('existing systemd guard requires review')
        enabled=self.path('/etc/systemd/system/multi-user.target.wants/'+unit)
        self.trusted('/etc/systemd/system/multi-user.target.wants')
        if installed:
            if (not enabled.is_symlink() or enabled.lstat().st_uid!=self.owner
                    or enabled.resolve()!=self.path('/etc/systemd/system/'+unit)):
                raise ValueError('unexpected guard enablement link')
        elif enabled.exists() or enabled.is_symlink():raise ValueError('existing enablement link requires review')
        return {'host':facts,'firewall':firewall,'interface':{'ips':ips,'mtu':devices[0].get('mtu'),
            'flags':sorted(devices[0].get('flags',[]))},
            'routes_sha256':digest(runtime.command(['ip','-j','route','show','table','all'],self.runner)),
            'routing_policy_sha256':digest(runtime.command(['ip','-j','rule','show'],self.runner)),
            'iptables_version':runtime.command(['iptables','--version'],self.runner).strip()}

    def install(self,files,unit):
        for name,(body,mode) in files.items():
            path=self.trusted(name)
            missing=[]
            for parent in path.parents:
                if parent.exists():break
                missing.append(parent)
            for parent in reversed(missing):parent.mkdir(mode=0o755)
            self.trusted(name)
            with path.open('xb') as output:
                os.chmod(path,mode);output.write(body);output.flush();os.fsync(output.fileno())
        runtime.command(['systemctl','daemon-reload'],self.runner)
        runtime.command(['systemctl','enable','--now',unit],self.runner)


def execute(args,backend,host=None):
    started=time.time()
    cfg,policy,binding=inputs(args,backend);host=Host() if host is None else host
    unit,files=artifacts(cfg,policy)
    before=host.inspect(cfg,policy,binding,files,unit)
    if time.time()-started>900:raise ValueError('network observations exceeded freshness window')
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('absolute private output required')
    if any(output.is_relative_to(Path(p)) for p in
           ('/etc/spvm-network','/etc/systemd/system','/usr/local/libexec')):
        raise ValueError('keep transaction reports outside owned system namespaces')
    for name in files:
        target=Path(name)
        if output.is_relative_to(target) or target.is_relative_to(output):
            raise ValueError('transaction must not overlap installed artifacts')
    plan={'schema_version':1,'kind':'fresh-geneve-guard-v1','binding':binding,'policy':policy,'before':before,
          'artifacts':{p:{'sha256':hashlib.sha256(body).hexdigest(),'mode':mode}
                       for p,(body,mode) in files.items()},
          'effects':['CREATE_OWNED_GENEVE_INPUT_ALLOWLIST','INSTALL_AND_ENABLE_OWNED_SYSTEMD_UNIT'],
          'deploy_authorized':False,'guests_launched':False}
    if args.action=='plan-geneve-guard':
        output.mkdir(mode=0o700,exist_ok=False);plan['created_at']=int(time.time());plan['expires_at']=plan['created_at']+900
        plan['plan_sha256']=digest(plan);write(output/'geneve-plan.json',plan)
        return {'status':'GENEVE_GUARD_PLAN_READY',**plan}
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:raise ValueError('owned private transaction required')
    previous=json.loads(read(output/'geneve-plan.json'));body={k:v for k,v in previous.items() if k!='plan_sha256'}
    if (type(previous.get('created_at')) is not int or previous.get('expires_at')!=previous['created_at']+900
            or not 0<=time.time()-previous['created_at']<=900 or previous.get('plan_sha256')!=digest(body)
            or args.plan_sha256!=previous['plan_sha256']):raise ValueError('fresh approved Geneve plan required')
    plan.update(created_at=previous['created_at'],expires_at=previous['expires_at'])
    if plan!=body:raise ValueError('network inputs/host drift since approval')
    lock=os.open(output/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        journal=output/'geneve-transaction.json'
        if journal.exists() or journal.is_symlink():raise ValueError('already attempted; review before retry')
        record={'schema_version':1,'status':'INSTALLING_GENEVE_GUARD','plan_sha256':args.plan_sha256,
                'deploy_authorized':False,'guests_launched':False}
        write(journal,record)
        try:
            host.install(files,unit)
            if inputs(args,backend)[2]!=binding:raise ValueError('network source/input drift during installation')
            after=host.inspect(cfg,policy,binding,files,unit,installed=True)
            expected={**before,'firewall':{**before['firewall'],'owned':'READY'}}
            if after!=expected:raise ValueError('protected host/firewall/routing state changed')
            record.update(status='GENEVE_GUARD_INSTALLED_NOT_CONNECTIVITY_ACCEPTED',after=after)
        except BaseException:
            record['status']='NEEDS_REVIEW_GENEVE_GUARD';write(journal,record);raise
        write(journal,record);return record
    finally:os.close(lock)


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan-geneve-guard','prepare-geneve-guard'])
    for name in ('config','network-request','host','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256');parser.add_argument('--allow-experimental-ubuntu26',action='store_true')
    args=parser.parse_args(argv)
    try:
        if os.geteuid()!=0:raise ValueError('run root-locally on the selected host')
        sys.path.insert(0,str(Path(backend_path).parent))
        spec=importlib.util.spec_from_file_location('spvm_geneve_backend',backend_path)
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        result=execute(args,backend)
        print(json.dumps({k:v for k,v in result.items() if k in ('status','plan_sha256','deploy_authorized','guests_launched')}))
        return 0
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: Geneve preparation refused; evidence preserved; no automatic rollback. '
            +type(exc).__name__+' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
