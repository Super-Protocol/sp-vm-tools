"""Read-only fresh-cloud host probe, sent over verified SSH using stdlib only."""
import hashlib
import base64
import ipaddress
import json
import os
import pwd
from pathlib import Path
import re
import shutil
import stat
import subprocess


def probe(request,root=Path('/'),runner=subprocess.run):
    root=Path(root)
    owner=0 if root==Path('/') else os.getuid()
    blockers=[]
    def path(name):return root/name.lstrip('/')
    def run(argv,allowed=(0,)):
        result=runner(argv,capture_output=True,text=True,timeout=45,
            env={'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C','DOCKER_HOST':'unix:///var/run/docker.sock'})
        if result.returncode not in allowed or len(result.stdout)>4194304:raise ValueError('probe command refused')
        return result
    def trusted(name):
        p=path(name)
        for item in (p,*p.parents):
            if item.exists() or item.is_symlink():
                st=item.lstat()
                if item.is_symlink() or st.st_uid!=owner or st.st_mode & 0o022:
                    raise ValueError('untrusted system path')
            if item==root:break
        return p
    def digest(name,limit=4194304):
        p=trusted(name)
        if not p.is_file() or p.stat().st_size>limit:raise ValueError('bounded installed file required')
        value=hashlib.sha256()
        with p.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1048576),b''):value.update(chunk)
        return value.hexdigest()
    machine=hashlib.sha256(path('/etc/machine-id').read_text().strip().encode()).hexdigest()
    result={'schema_version':1,'machine_sha256':machine,'blockers':blockers}
    if machine!=request['machine_sha256']:
        blockers.append('WRONG_MACHINE');return result
    # Refuse a busy target BEFORE reading private helper config or inspecting images.
    if 'compute' in request['roles'] or shutil.which('virsh'):
        if run(['virsh','-c','qemu:///system','list','--all','--uuid']).stdout.strip():
            blockers.append('EXISTING_LIBVIRT_DOMAINS');return result
    processes=run(['pgrep','-f',r'(^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$))|(^|[ /])nova(-compute|[.]cmd[.]compute)( |$)'],(0,1))
    if processes.returncode!=1:blockers.append('EXISTING_GUEST_OR_NOVA_PROCESS');return result
    ids=run(['docker','ps','-aq','--no-trunc']).stdout.split()
    if any(not re.fullmatch('[a-f0-9]{64}',i) for i in ids):raise ValueError('invalid Docker container identity')
    containers=json.loads(run(['docker','inspect',*ids]).stdout) if ids else []
    protected={}
    cloud_names={'nova_compute','nova_api','nova_conductor','nova_scheduler','mariadb','rabbitmq',
        'keystone','kolla_toolbox','neutron_server','ovn_controller','ovn_northd','ovn_sb_db','ovn_nb_db',
        'barbican_api','barbican_worker','skyline_apiserver','skyline_console'}
    for c in containers:
        name=c['Name'].lstrip('/')
        if name in cloud_names or 'kolla_version' in (c.get('Config',{}).get('Labels') or {}):
            blockers.append('EXISTING_OPENSTACK_CONTAINERS')
        protected[c['Id']]={'name':name,'image':c['Image'],'state':c['State']['Status'],
                            'started_at':c['State']['StartedAt']}
    result['protected_containers']=protected
    if blockers:return result
    volumes=run(['docker','volume','ls','--format','{{.Name}}']).stdout.split()
    if any(v in {'mariadb','rabbitmq','nova_compute','nova_libvirt','keystone','barbican'} for v in volumes):
        blockers.append('EXISTING_OPENSTACK_VOLUMES')
    for name in ('/etc/kolla','/etc/nova','/opt/stack'):
        p=path(name)
        if p.is_symlink() or (p.exists() and (not p.is_dir() or any(p.iterdir()))):
            blockers.append('EXISTING_CLOUD_CONFIGURATION')
    if blockers:return result
    release={}
    for line in path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            k,v=line.split('=',1);release[k]=v.strip('"')
    version=release.get('VERSION_ID')
    if release.get('ID')!='ubuntu' or version not in ('24.04','26.04'):
        blockers.append('UNREVIEWED_OS')
    if os.uname().machine!='x86_64':blockers.append('UNREVIEWED_ARCHITECTURE')
    if version=='26.04' and not request['allow_ubuntu26']:blockers.append('UBUNTU26_ACK_REQUIRED')
    addresses=json.loads(run(['ip','-j','address']).stdout)
    interfaces={i['ifname'] for i in addresses}
    if not set(request['interfaces'])<=interfaces:blockers.append('REQUIRED_INTERFACE_MISSING')
    if request.get('api_vip') and not any(a.get('local')==request['api_vip'] for i in addresses for a in i.get('addr_info',[])):
        blockers.append('PERSISTENT_PRIVATE_API_VIP_MISSING')
    result.update(os_version=version,kernel=os.uname().release,
        docker_version=run(['docker','info','--format','{{.ServerVersion}}']).stdout.strip())
    if 'compute' in request['roles'] and not request.get('guard_only',False):
        spec=request['helper_request']
        memory=next(int(line.split()[1])//1024 for line in path('/proc/meminfo').read_text().splitlines()
                    if line.startswith('MemTotal:'))
        if (os.cpu_count()<=request['reserved_cpus'] or memory<=request['reserved_memory_mb']):
            blockers.append('HOST_RESERVES_LEAVE_NO_VM_CAPACITY')
        if not path('/dev/kvm').exists():blockers.append('KVM_NOT_PREPARED')
        tdx=path('/sys/module/kvm_intel/parameters/tdx')
        if not ((tdx.is_file() and tdx.read_text().strip().lower() in ('y','1')) or path('/dev/sev').exists()):
            blockers.append('TDX_SEV_NOT_PREPARED')
        account=pwd.getpwnam('kolla-nova')
        ipc=path('/run/spvm/host-helper.sock').stat()
        if ((account.pw_uid,account.pw_gid)!=(42436,42436)
                or not stat.S_ISSOCK(ipc.st_mode) or ipc.st_uid!=0 or ipc.st_gid!=42436
                or ipc.st_mode & 0o777!=0o660
                or run(['systemctl','is-active','host-helper.socket']).stdout.strip()!='active'):
            blockers.append('HELPER_PEER_OR_SOCKET_NOT_READY')
        for name in ('/var/lib/spvm/network','/var/lib/spvm/providers','/var/lib/spvm/runtime',
                     '/var/lib/spvm/execution','/var/lib/spvm/clusters'):
            p=trusted(name)
            if p.exists() and (not p.is_dir() or any(p.iterdir())):blockers.append('EXISTING_SPVM_STATE')
        if path('/var/lib/nova/compute_id').read_text().strip()!=spec['compute_uuid']:
            blockers.append('COMPUTE_UUID_MISMATCH')
        helper=json.loads(trusted('/etc/spvm/host-helper.json').read_text())
        runtime=json.loads(trusted('/etc/spvm/runtime-services.json').read_text())
        execution=helper['execution']
        if (helper['allowed_peer_uids']!=[42436] or helper['storage']['compute_uuid']!=spec['compute_uuid']
                or helper['runtime']['registered_gpus']!=spec['gpus']
                or execution['release']!=spec['release'] or execution['tools']!=spec['tools']
                or execution['cache']!=spec['cache'] or execution['allow_debug_launch'] is not spec['allow_debug']
                or execution['release_caches']!={spec['release']:spec['cache']}
                or execution['source_sha256']!=request['launcher_sha256']
                or execution['release_gate']!='guest-api-registry-presence-v1'):
            blockers.append('HELPER_CONFIG_MISMATCH')
        if runtime!=request['bootstrap_runtime']:blockers.append('RUNTIME_NOT_BOOTSTRAP_DENY_ALL')
        public=json.loads(trusted('/etc/spvm/compute-pools.json').read_text())
        if public!={'compute_uuid':spec['compute_uuid'],'pool_ids':[p['id'] for p in spec['pools']]}:
            blockers.append('PUBLIC_COMPUTE_POOL_BINDING_MISMATCH')
        result['helper_config_sha256']=digest('/etc/spvm/host-helper.json')
        result['runtime_config_sha256']=digest('/etc/spvm/runtime-services.json')
        result['public_pool_config_sha256']=digest('/etc/spvm/compute-pools.json')
        configured={p['id']:p for p in helper['storage']['pools']}
        if set(configured)!={p['id'] for p in spec['pools']}:blockers.append('HELPER_POOL_SET_MISMATCH')
        devices=set()
        for pool in spec['pools']:
            p=trusted(pool['root']);mount=trusted(pool['mount']);actual=configured[pool['id']]
            fs=json.loads(run(['findmnt','--json','--target',str(p),'-o','TARGET,FSTYPE']).stdout)['filesystems']
            if len(fs)!=1 or fs[0]['target']!=pool['mount'] or fs[0]['fstype'] not in ('ext4','xfs'):
                blockers.append('STATE_FILESYSTEM_NO_LONGER_LOCAL_SUPPORTED')
            if (not p.is_dir() or any(p.iterdir()) or not os.path.ismount(mount)
                    or p.stat().st_dev!=mount.stat().st_dev or p.stat().st_dev==root.stat().st_dev
                    or p.stat().st_dev in devices or any(actual[k]!=pool[k] for k in ('root','mount','budget_gib','reserve_gib'))):
                blockers.append('STATE_POOL_NOT_FRESH_OR_BOUND')
            devices.add(p.stat().st_dev)
            free=os.statvfs(p)
            if free.f_flag & os.ST_RDONLY:blockers.append('STATE_FILESYSTEM_READ_ONLY')
            if free.f_bavail*free.f_frsize<(pool['budget_gib']+pool['reserve_gib'])*1024**3:
                blockers.append('STATE_BUDGET_NO_LONGER_AVAILABLE')
        hashes={name:digest(name) for name in request['host_source_sha256']}
        if hashes!=request['host_source_sha256']:blockers.append('HOST_HELPER_SOURCE_DRIFT')
        result['host_source_sha256']=hashes
        devices={d['address']:d for d in request['gpu_devices']}
        for bdf in spec['gpus']:
            device=path('/sys/bus/pci/devices/'+bdf)
            if (device.joinpath('vendor').read_text().strip()!='0x'+devices[bdf]['vendor_id']
                    or device.joinpath('device').read_text().strip()!='0x'+devices[bdf]['product_id']
                    or device.joinpath('driver').resolve().name!='vfio-pci'
                    or sorted(p.name for p in device.joinpath('iommu_group/devices').iterdir())!=[bdf]):
                blockers.append('GPU_NO_LONGER_VFIO_ISOLATED')
        image=json.loads(run(['docker','image','inspect',request['image_reference']]).stdout)
        if (len(image)!=1 or image[0]['Id']!=request['image_id'] or image[0]['Config']['User']!='nova'
                or image[0]['Os']!='linux' or image[0]['Architecture']!='amd64'
                or image[0]['Config'].get('Labels',{}).get('io.spvm.context-sha256')!=request['image_context_sha256']):
            blockers.append('DELIVERED_IMAGE_MISMATCH')
        result['protected_runtime']={p:digest(p,limit=512*1024**2) for p in ('/usr/bin/qemu-system-x86_64','/usr/bin/virsh',
            '/usr/sbin/libvirtd','/etc/libvirt/qemu.conf','/etc/libvirt/libvirtd.conf') if path(p).is_file()}
        if not {'/usr/bin/qemu-system-x86_64','/usr/bin/virsh'}<=set(result['protected_runtime']):
            blockers.append('MEASURED_QEMU_LIBVIRT_PREREQUISITES_MISSING')
    if request.get('protect_package_runtime') is True:
        # Package preparation may change only the three explicitly selected packages.
        # Service timestamps expose an unexpected restart without logging configuration.
        services={}
        for service in ('docker.service','libvirtd.service','virtqemud.service','qgs.service',
                        'qgsd.service','pccs.service','host-helper.socket'):
            info=run(['systemctl','show',service,'--property=LoadState,ActiveState,SubState,MainPID,'
                      'ActiveEnterTimestampMonotonic,ExecMainStartTimestampMonotonic,NRestarts'],(0,1,4))
            if 'LoadState=' not in info.stdout:raise ValueError('service observation unavailable')
            services[service]=info.stdout.strip()
        result['protected_package_services']=services
        result['protected_package_files']={p:digest(p,limit=512*1024**2) for p in
            ('/usr/bin/qemu-system-x86_64','/usr/bin/virsh','/usr/sbin/libvirtd',
             '/etc/libvirt/qemu.conf','/etc/libvirt/libvirtd.conf','/etc/docker/daemon.json') if path(p).is_file()}
        profile=request.get('package_profile','base-packages')
        packages={'base-packages':('git','iputils-ping','tzdata'),
                  'host-sdk':('python3-docker','python3-dbus','python3-requests'),
                  'api-trust':('ca-certificates',),
                  'initial-readiness':('git','iputils-ping','tzdata','python3-docker',
                                       'python3-dbus','python3-requests','ca-certificates')}[profile]
        query=run(['dpkg-query','-W','-f=${binary:Package}\t${db:Status-Status}\t${Version}\n',*packages],(0,1))
        installed={}
        for line in query.stdout.splitlines():
            name,status,version=line.split('\t')
            if name not in packages or name in installed:raise ValueError('unexpected package status')
            if status=='installed':installed[name]=version
        result['base_packages']=installed
        if profile in ('host-sdk','initial-readiness'):
            script=('import docker,dbus,requests,json; '
                'v=docker.__version__; c=docker.DockerClient(base_url="unix:///var/run/docker.sock",timeout=10); '
                'print(json.dumps({"ready":int(v.split(".")[0])>=7 and c.ping() is True,"version":v})); c.close()')
            sdk=run(['/usr/bin/python3','-I','-B','-c',script],(0,1))
            value=json.loads(sdk.stdout) if sdk.returncode==0 else {'ready':False}
            if (not isinstance(value,dict) or type(value.get('ready')) is not bool
                    or set(value)-{'ready','version'}):raise ValueError('invalid SDK observation')
            result['host_sdk']=value
        if profile in ('api-trust','initial-readiness'):
            wanted=request['api_ca'];destination=wanted['destination']
            if not re.fullmatch('/usr/local/share/ca-certificates/spvm-openstack-[a-f0-9]{16}[.]crt',destination):
                raise ValueError('invalid managed CA destination')
            anchor=trusted(destination)
            if not anchor.parent.is_dir():blockers.append('PREPARED_CA_DIRECTORY_MISSING')
            bundle=trusted('/etc/ssl/certs/ca-certificates.crt')
            if not bundle.is_file() or bundle.stat().st_size>32*1024**2:raise ValueError('system CA bundle unavailable')
            ids=[]
            for cert in re.findall(rb'-----BEGIN CERTIFICATE-----\s+(.*?)\s+-----END CERTIFICATE-----',bundle.read_bytes(),re.S):
                ids.append(hashlib.sha256(base64.b64decode(re.sub(rb'\s+',b'',cert),validate=True)).hexdigest())
            anchor_sha=digest(destination) if anchor.exists() else None
            if anchor_sha not in (None,wanted['sha256']):blockers.append('MANAGED_API_CA_CONFLICT')
            if anchor.exists():
                st=anchor.stat()
                if st.st_gid!=(0 if root==Path('/') else os.getgid()) or st.st_mode & 0o777!=0o644:
                    blockers.append('MANAGED_API_CA_PERMISSIONS_MISMATCH')
            if 'ca-certificates' not in installed or not trusted('/usr/sbin/update-ca-certificates').is_file():
                blockers.append('CA_CERTIFICATES_PREREQUISITE_MISSING')
            result['api_trust']={'anchor_sha256':anchor_sha,'bundle_certificates':sorted(set(ids))}
        if profile=='initial-readiness':
            local={};network_blockers=[]
            for name in {request['management_interface'],request['tunnel_interface']}:
                matches=[a for a in addresses if a['ifname']==name]
                if len(matches)!=1:
                    network_blockers.append('LOCAL_INTERFACE_MISSING');continue
                device=matches[0];ipv4=[]
                for address in device.get('addr_info',[]):
                    if address.get('family')=='inet':
                        ip=ipaddress.IPv4Address(address['local'])
                        if not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast):
                            ipv4.append(str(ip))
                up='UP' in device.get('flags',[])
                if not up:network_blockers.append('LOCAL_INTERFACE_NOT_UP')
                if not ipv4:network_blockers.append('LOCAL_IPV4_ADDRESS_MISSING')
                local[name]={'up':up,'ipv4':sorted(ipv4),'mtu':device.get('mtu')}
            forwarding=None
            if request['needs_ipv4_forwarding']:
                forwarding=path('/proc/sys/net/ipv4/ip_forward').read_text().strip()=='1'
                if not forwarding:network_blockers.append('HOST_NAT_IPV4_FORWARDING_DISABLED')
            result['local_network']={'interfaces':local,'ipv4_forwarding':forwarding,
                'blockers':sorted(set(network_blockers)),'connectivity_accepted':False}
    return result


if __name__=='__main__':
    try:
        print(json.dumps(probe(REQUEST)))
    except Exception:
        # Private configuration/command output must never appear in diagnostics.
        print(json.dumps({'schema_version':1,'blockers':['HOST_PREREQUISITE_PROBE_FAILED']}))
