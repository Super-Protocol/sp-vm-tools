"""Read-only shared-interface admission; concatenated into remote probes."""
import hashlib
import ipaddress
import json
import os
import re
from pathlib import Path
import subprocess


def check_shared_network(request,root=Path('/'),runner=subprocess.run):
    expected=request.get('shared_network')
    if expected is None:return None
    owner=0 if root==Path('/') else os.getuid()
    def protected(name):
        path=root/name.lstrip('/')
        for item in (path,*path.parents):
            st=item.lstat()
            if item.is_symlink() or st.st_uid!=owner or st.st_mode & 0o022:
                raise ValueError('shared network policy/source is not protected')
            if item==root:break
        if not path.is_file() or path.stat().st_size>1048576:raise ValueError('bounded shared network file required')
        return path
    runtime=protected('/usr/local/libexec/spvm-host-network.py')
    if hashlib.sha256(runtime.read_bytes()).hexdigest()!=expected['runtime_sha256']:
        raise ValueError('shared network runtime differs')
    policy=protected('/etc/spvm-host-network/spvm-management-'+expected['deployment_id']+'.json')
    if policy.stat().st_mode & 0o777!=0o600:raise ValueError('private shared network policy required')
    value=json.loads(policy.read_text())
    if (value.get('machine_sha256')!=request['machine_sha256'] or value.get('phase')!='management'
            or value.get('deployment_id')!=expected['deployment_id'] or value.get('management_mode')!='shared-interface'
            or value.get('management_interface')!=expected['interface'] or value.get('route_interface')!=expected['interface']
            or str(ipaddress.IPv4Interface(value['management_address']).ip)!=expected['address']
            or value.get('peers')!=expected['peers'] or value.get('vip')!=expected['vip']):
        raise ValueError('shared network policy does not match deployment')
    result=runner(['/usr/bin/python3','-I','-B',str(runtime),'--config',str(policy),'--check'],
        capture_output=True,text=True,timeout=90,
        env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    if result.returncode or len(result.stdout)>65536:
        raise ValueError('shared network/firewall is not ready')
    checked=json.loads(result.stdout)
    if checked.get('status')!='HOST_NETWORK_CHECK_PASSED' or not re.fullmatch('[a-f0-9]{64}',str(checked.get('shared_host_network'))):
        raise ValueError('shared network/firewall is not ready')
    return {'policy_sha256':hashlib.sha256(policy.read_bytes()).hexdigest(),
            'runtime_sha256':expected['runtime_sha256'],'shared_host_network':checked['shared_host_network'],'ready':True}
