"""Controller-local completion with a finite, installation-only SSH dialogue.

There is no listener, new SSH account/key, agent forwarding or remote shell RPC.
The controller requests named installer operations; the management host selects
the code and the target from its already approved transaction, not from a URL,
browser, callback command string or uploaded executable.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import select
import shlex
import subprocess
import sys
import time
from types import SimpleNamespace as R

from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError

LIMIT = 64 * 1024 * 1024


def snapshot(i):
    root = i.path.parent.parent
    manifest = json.loads((root / 'manifest.json').read_bytes())
    names = {**manifest['source_sha256'], **{'wheels/'+n:s for n,s in manifest.get('wheel_sha256', {}).items()}}
    files = {}
    for name in ['manifest.json', *names]:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise InstallerError('Only regular, manifest-bound driver files may be sent to the controller.')
        data = path.read_bytes()
        if name in names and hashlib.sha256(data).hexdigest() != names[name]:
            raise InstallerError('Selected driver bundle changed before controller preparation.')
        files['driver/' + name] = base64.b64encode(data).decode()
    for path in Path(__file__).parent.glob('*.py'):
        if path.is_symlink():
            raise InstallerError('Installer source symlink refused.')
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=i.inputs.get(str(path)):
            raise InstallerError('Installer sources changed during controller snapshot preparation.')
        files['tools/' + path.name] = base64.b64encode(body).decode()
    return files


def rpc_process(i, argv, payload, handler=None, *, timeout=1800):
    """Bounded frames; callback data (including credentials) is never logged."""
    log = i.output / ('private-controller-' + ('completion' if handler else 'preparation') + '.log')
    body = json.dumps(payload, separators=(',', ':')).encode() + b'\n'
    if len(body) > LIMIT:
        raise InstallerError('Controller preparation payload exceeds its size limit.')
    with log.open('xb') as errors:
        os.fchmod(errors.fileno(), 0o600)
        # Keep the outer control channel alive while a local callback performs
        # lengthy compute activation over separate SSH connections. OpenSSH
        # sends encrypted liveness probes independently of our stdin dialogue.
        # These options precede transport options: OpenSSH uses the first value.
        command = [argv[0], '-o', 'ServerAliveInterval=15',
                   '-o', 'ServerAliveCountMax=12', *argv[1:]]
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
        try:
            child.stdin.write(body); child.stdin.flush()
            sequence = 0
            while True:
                # A controller may be installing dependencies/doing API acceptance.
                # This wait is in the user's installer, not an agent polling loop.
                ready, _, _ = select.select([child.stdout], [], [], timeout)
                if not ready:
                    raise InstallerError('Controller installation operation timed out; inspect both journals.')
                line = child.stdout.readline(LIMIT + 1)
                if not line or len(line) > LIMIT or not line.endswith(b'\n'):
                    raise InstallerError('Controller installation channel ended without a valid acknowledgement. '
                        'Inspect private controller log '+str(log)+'. Do not replay the failed platform transaction.')
                frame = json.loads(line)
                if set(frame) == {'result'}:
                    child.stdin.close()
                    if child.wait(timeout=30) != 0:
                        raise InstallerError('Controller process failed after its acknowledgement.')
                    return frame['result']
                if handler is None or set(frame) != {'operation', 'data', 'sequence'} or frame['sequence'] != sequence:
                    raise InstallerError('Unexpected controller installation operation or sequence.')
                result = handler(frame['operation'], frame['data'])
                reply = json.dumps({'sequence': sequence, 'result': result}, separators=(',', ':')).encode() + b'\n'
                if len(reply) > LIMIT:
                    raise InstallerError('Installation acknowledgement exceeds its size limit.')
                child.stdin.write(reply); child.stdin.flush()
                sequence += 1
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()


BOOTSTRAP = '''
import base64,hashlib,json,os,sys
from pathlib import Path,PurePosixPath
os.umask(0o077)
line=sys.stdin.buffer.readline(64*1024*1024+1)
if len(line)>64*1024*1024:raise ValueError('oversized source snapshot')
payload=json.loads(line);root=Path(payload['root'])
if root.parent!=Path('/opt/spvm-openstack') or not root.name or root.name in ('.','..'):
 raise ValueError('invalid installer source target')
if hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()!=payload['machine']:
 raise ValueError('wrong controller')
for parent in (root.parent,*root.parent.parents):
 if parent.exists() and (parent.is_symlink() or parent.stat().st_uid!=0 or parent.stat().st_mode&0o022):
  raise ValueError('unprotected controller source parent')
if not root.parent.exists():
 root.parent.mkdir(mode=0o755,parents=True);root.parent.chmod(0o755)
if root.parent.stat().st_mode&0o005!=0o005:
 raise ValueError('controller source parent must be service-traversable; do not widen existing private directories')
root.mkdir(mode=0o755);root.chmod(0o755)
total=0
for name,encoded in payload['files'].items():
 part=PurePosixPath(name)
 if part.is_absolute() or '..' in part.parts or str(part)!=name or part.parts[0] not in ('driver','tools'):
  raise ValueError('unsafe snapshot member')
 body=base64.b64decode(encoded,validate=True);total+=len(body)
 if total>48*1024*1024:raise ValueError('snapshot exceeds limit')
 p=root/name;p.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
 with p.open('xb') as stream:stream.write(body)
 p.chmod(0o644)
for p in root.rglob('*'):
 if p.is_dir():p.chmod(0o755)
sys.path.insert(0,str(root/'tools'))
if 'channel_continuation' in payload:
 from openstack_install_channel_continue import setup_controller
 result=setup_controller(root,payload)
elif 'compute_continuation' in payload:
 from openstack_install_compute_continue import setup_controller
 result=setup_controller(root,payload)
elif 'platform_continuation' in payload:
 from openstack_install_platform_continue import setup_controller
 result=setup_controller(root,payload)
else:
 from openstack_install_completion import setup
 result=setup(root,payload)
print(json.dumps({'result':result}),flush=True)
'''


def prepare(i):
    import yaml
    i.controller_root = '/opt/spvm-openstack/' + i.run_id
    raw = yaml.safe_load(Path(i.config).read_bytes())
    raw['driver']['directory'] = i.controller_root + '/driver'
    for host in raw['hosts'].values():
        host.pop('identity_file', None)
    # Local build context paths/transport policy have no meaning for the runtime.
    raw['kolla'].pop('skyline_console_context', None)
    raw['kolla'].pop('skyline_console_context_sha256', None)
    binding = json.loads((Path(i.workspace) / 'compute-binding.json').read_bytes())
    requests = {}
    for name, host in binding['hosts'].items():
        requests[name] = json.loads(Path(host['request_file']).read_bytes())
        host['request_file'] = i.controller_root + '/data/helper-' + name + '.json'
    binding['binding_sha256'] = digest({k: v for k, v in binding.items() if k != 'binding_sha256'})
    payload = {'root': i.controller_root, 'machine': i.audits[i.controller]['host_identity_sha256'],
        'controller': i.controller, 'config': raw, 'binding': binding, 'requests': requests,
        'files': snapshot(i), 'console_manifest': json.loads((i.output / 'console-context/console-context.json').read_bytes()),
        'host_sources': [str(__import__('ipaddress').IPv4Interface(v['management_address']).ip) + '/32'
                         for v in i.host_network['hosts'].values()],
        'guest_cidr': str(i.guest), 'region': i.region,
        'network': neutron_request(i)}
    argv = i.backend.ssh_arguments(i.cfg['hosts'][i.controller]) + [
        'sudo -n /usr/bin/python3 -I -B -u -c ' + shlex.quote(BOOTSTRAP)]
    result = rpc_process(i, argv, payload)
    if result.get('status') != 'CONTROLLER_COMPLETION_PREPARED':
        raise InstallerError('Controller completion prerequisites are not ready.')
    return result


def neutron_request(i):
    import ipaddress
    transit = ipaddress.IPv4Network(i.cfg['network']['transit_cidr'])
    return {'schema_version': 1, 'auth_url': 'https://' + i.cfg['network']['api_vip'] + ':5000/v3',
        'region_name': i.region,
        'external': {'network_name': 'spvm-external', 'physical_network': 'physnet1', 'subnet_name': 'spvm-external-subnet', 'cidr': str(transit),
                     'gateway_ip': str(transit[1]), 'allocation_pool': {'start': str(transit[10]), 'end': str(transit[10])}},
        'guest': {'network_name': 'spvm-guests', 'subnet_name': 'spvm-guests-subnet', 'cidr': str(i.guest),
                  'gateway_ip': str(i.guest[1]), 'allocation_pool': {'start': str(i.guest[2]), 'end': str(i.guest[-2])},
                  'dns_nameservers': ['1.1.1.1', '8.8.8.8']},
        'router': {'name': 'spvm-router', 'external_ip': str(transit[10])}}


def setup(root, payload):
    """Root on the controller; no SSH calls or operator transport credentials."""
    import venv
    data = root / 'data'; data.mkdir(mode=0o700)
    sdk = root / 'sdk'
    # The standard-library bootstrap only writes manifest-bound source bytes.
    # Verify them again before executing the SDK package or privileged actors.
    from openstack_installer import backend
    backend(root / 'driver', verified_bundle=True)
    mask = os.umask(0o022)
    try:
        venv.EnvBuilder(with_pip=True).create(sdk)
        wheels = list((root / 'driver/wheels').glob('nova_spvm-*.whl'))
        if len(wheels) != 1: raise ValueError('one source-matched wheel required')
        with (data/'private-sdk.log').open('xb') as log:
            os.fchmod(log.fileno(),0o600)
            for args in (['install','--disable-pip-version-check','--no-input','python-openstackclient',str(wheels[0])], ['check']):
                result=subprocess.run([str(sdk/'bin/python'),'-m','pip','--isolated',*args],
                    stdout=log,stderr=log,timeout=1800)
                if result.returncode:raise ValueError('controller SDK installation failed; inspect private log')
    finally:
        os.umask(mask)
    code='import sys,json;from pathlib import Path;sys.path.insert(0,'+repr(str(root/'tools'))+');'
    code+='from openstack_install_completion import finalize_setup;print(json.dumps(finalize_setup(Path('+repr(str(root))+'),json.load(sys.stdin))))'
    result=subprocess.run([str(sdk/'bin/python'),'-I','-B','-c',code],input=json.dumps(payload),
        text=True,capture_output=True,timeout=300)
    if result.returncode:raise ValueError('controller completion setup refused')
    return json.loads(result.stdout)


def finalize_setup(root,payload):
    import yaml
    import openstack_platform as platform
    data=root/'data';sdk=root/'sdk'
    for directory in (Path('/opt/spvm-web'),Path('/var/lib/spvm-web'),Path('/etc/spvm')):
        for parent in (directory,*directory.parents):
            if parent.exists() and (parent.is_symlink() or not parent.is_dir()
                    or parent.stat().st_uid!=0 or parent.stat().st_mode&0o022):
                raise ValueError('unprotected service parent')
        if not directory.exists():directory.mkdir(mode=0o755);directory.chmod(0o755)
        if directory.stat().st_mode&0o005!=0o005:
            raise ValueError('existing service parent is not traversable; no implicit permission widening')
    config = data / 'deployment.yaml'
    config.write_text(yaml.safe_dump(payload['config'], sort_keys=False)); config.chmod(0o600)
    write(data / 'compute-binding.json', payload['binding'])
    for name, request in payload['requests'].items():
        if not name or Path(name).name != name or name in ('.', '..'):
            raise ValueError('invalid helper host name')
        write(data / ('helper-' + name + '.json'), request)
    write(data / 'network.json', payload['network'])
    write(data / 'console-context.json', payload['console_manifest'])
    base = Path('/etc/haproxy/haproxy.cfg')
    if not base.is_file(): raise ValueError('install controller HAProxy prerequisites before completion preparation')
    (data / 'base-haproxy.cfg').write_bytes(base.read_bytes())
    spec = {'schema_version': 1, 'controller': payload['controller'], 'sdk_venv': str(sdk),
        'network': str(data / 'network.json'), 'web_user': 'spvm-web',
        'operator_user': payload['config']['hosts'][payload['controller']]['user'],
        'runtime_dir': '/opt/spvm-web/' + root.name, 'state_dir': '/var/lib/spvm-web/' + root.name,
        'origins': ['https://' + payload['config']['network']['api_vip'] + ':9999'],
        'host_sources': payload['host_sources'], 'ingress_address': payload['config']['network']['public_ip'],
        'base_haproxy_config': str(data / 'base-haproxy.cfg'), 'quota_from_host_budgets': True,
        'console_manifest': str(data / 'console-context.json'), 'release_registration': True}
    write(data / 'platform.json', spec)
    platform.prepare_account(spec, data)
    platform.check_sdk_access(spec)
    frozen = subprocess.run([str(sdk / 'bin/python'), '-m', 'pip', 'freeze', '--all'], check=True,
                            capture_output=True, text=True, timeout=60).stdout
    (data / 'sdk-packages.txt').write_text(frozen); (data / 'sdk-packages.txt').chmod(0o600)
    write(data / 'prepared.json', {'status': 'CONTROLLER_COMPLETION_PREPARED',
        'machine': payload['machine'], 'config_sha256': file_hash(config),
        'binding_sha256': file_hash(data / 'compute-binding.json'),
        'sdk_packages_sha256': file_hash(data / 'sdk-packages.txt')})
    return {'status': 'CONTROLLER_COMPLETION_PREPARED', 'root': str(root)}


class Operations:
    def __init__(self, installation):
        self.i = installation
        self.enrollment = None
        self.requests = None
        self.activated = set(); self.probed = set(); self.installed = set()
        self.audited = False

    def __call__(self, operation, data):
        i = self.i
        if getattr(i,'transaction',None) is not None:i.transaction.unchanged()
        if not isinstance(data, dict): raise InstallerError('Invalid installation operation data.')
        if operation == 'stage-enrollment':
            import re
            import openstack_spvm_enrollment as enrollment
            if self.enrollment is not None or set(data) != {'project', 'user', 'network', 'release_registration'}:
                raise InstallerError('Unexpected enrollment installation request.')
            if any(not isinstance(data[k], str) or not re.fullmatch('[a-f0-9-]{1,64}', data[k]) for k in ('project', 'user', 'network')) or data['release_registration'] is not True:
                raise InstallerError('Invalid new-cloud enrollment identity.')
            self.project = data['project']
            request = {'schema_version': 1, 'projects': {data['project']: {
                'publisher_user_ids': [data['user']], 'reader_user_ids': [data['user']],
                'network_ids': [data['network']], 'releases': [], 'release_registry_user_ids': [data['user']]}}}
            write(i.output / 'enrollment-request.json', request)
            self.enrollment = str(i.output / 'enrollment/enrollment-plan.json')
            enrollment.stage(R(workspace=i.workspace, request=str(i.output / 'enrollment-request.json'),
                profiles=None, output=str(i.output / 'enrollment')), i.path)
            return {'plan': json.loads(Path(self.enrollment).read_bytes()),
                    'runtime': json.loads((i.output / 'enrollment/runtime-services.json').read_bytes())}
        if operation == 'stage-runtime':
            import openstack_runtime_enrollment as runtime
            if data or self.enrollment is None or self.requests is not None:
                raise InstallerError('Unexpected runtime installation request.')
            runtime.stage(R(workspace=i.workspace, enrollment_plan=self.enrollment,
                output=str(i.output / 'runtime-requests')), i.path)
            self.requests = {n: json.loads((i.output / 'runtime-requests' / (n + '.json')).read_bytes()) for n in i.requests}
            return self.requests
        if operation == 'activate-compute':
            if set(data) != {'host', 'request'} or self.requests is None:
                raise InstallerError('Unexpected compute activation request.')
            name = data['host']
            if name not in self.requests or name in self.activated or data['request'] != self.requests[name]:
                raise InstallerError('Compute activation differs from the approved host/runtime binding.')
            from initial_compute import Backend
            from compute_transaction import execute
            target = i.output / ('activate-' + name); target.mkdir(mode=0o700)
            runner = Backend(i.cfg, i.controller, i.controller_root + '/sdk/bin/python',
                '/etc/spvm/auth/admin-openrc.sh', target / 'observations', enrollment=self.requests[name])
            plan = runner.plan(name); write(target / 'plan.json', plan)
            result = execute(plan, plan['plan_sha256'], target / 'transaction', runner)
            if result['status'] != 'COMPUTE_READY' or result['guests_restarted'] is not False:
                step=result.get('inflight')
                if step not in ('quarantine','stop','enroll','start','accept','enable',None):step='unknown'
                raise InstallerError('Compute activation failed on '+name+' at '+str(step)+'. Inspect '
                    +str(target/'transaction/transaction.json')+' and private-remote-*.json under '
                    +str(target/'observations')+'. Scheduling remains quarantined when confirmed by the journal; no automatic retry.')
            self.activated.add(name)
            return {'status': result['status'], 'guests_restarted': result['guests_restarted']}
        if operation == 'host-audits':
            if data or self.activated != set(i.requests) or self.audited:
                raise InstallerError('Unexpected final host audit request.')
            self.audited = True
            return i.backend.host_audits(i.cfg)
        if operation not in ('executor-probe', 'executor-install') or self.activated != set(i.requests):
            raise InstallerError('Unapproved controller installation operation.')
        name = data.get('host')
        if name not in i.requests: raise InstallerError('Unapproved executor target.')
        if operation == 'executor-probe':
            if set(data) != {'host'} or name in self.probed or self.installed:
                raise InstallerError('Unexpected executor probe.')
            source = (i.path.parent / 'host_control_install.py').read_text()
            result = i.remote(name, source + '\nfresh()\nprint(json.dumps({"status":"FRESH"}))\n')
            self.probed.add(name)
            return result
        if set(data) != {'host', 'policy', 'credentials'} or self.probed != set(i.requests) or name in self.installed:
            raise InstallerError('Unexpected executor installation request.')
        request = json.loads(Path(i.requests[name]).read_bytes())
        policy, private = data['policy'], data['credentials']
        if not isinstance(policy,dict) or not isinstance(private,dict):
            raise InstallerError('Exact structured executor enrollment required.')
        expected = {'host': i.cfg['hosts'][name].get('inventory_name', name),
                    'machine_sha256': request['machine_sha256'], 'compute_uuid': request['compute_uuid'],
                    'user_id': private.get('user_id')}
        if (policy.get('target') != expected or policy.get('project_id') != self.project
                or private.get('project_id') != self.project
                or private.get('auth_url') != 'https://' + i.cfg['network']['api_vip'] + ':5000/v3'):
            raise InstallerError('Executor enrollment differs from the approved deployment.')
        from host_control_install import source_files
        source = (i.path.parent / 'host_control_install.py').read_text()
        # Reconstruct the operation from LOCAL verified source; no executable text is accepted from the peer.
        code = source + '\nSPVM_INSTALL_POLICY=' + repr(policy) + '\nSPVM_INSTALL_CREDENTIALS=' + repr(private)
        code += '\nSPVM_INSTALL_SOURCES=' + repr(source_files(i.path.parent))
        code += '\nprint(json.dumps(install(SPVM_INSTALL_POLICY,SPVM_INSTALL_CREDENTIALS,SPVM_INSTALL_SOURCES)))\n'
        result = i.remote(name, code)
        self.installed.add(name)
        return result


def complete(i):
    import yaml
    payload = {'clouds': yaml.safe_load((i.output / 'core/kolla-run/clouds.yaml').read_bytes())}
    code = 'import sys;sys.path.insert(0,' + repr(i.controller_root + '/tools') + ');'
    code += 'from openstack_install_completion import serve;serve(' + repr(i.controller_root) + ')'
    argv = i.backend.ssh_arguments(i.cfg['hosts'][i.controller]) + [
        'sudo -n ' + shlex.quote(i.controller_root + '/sdk/bin/python') + ' -I -B -u -c ' + shlex.quote(code)]
    operations=Operations(i)
    result=rpc_process(i, argv, payload, operations)
    if (operations.activated!=set(i.requests) or operations.installed!=set(i.requests)
            or not operations.audited):
        raise InstallerError('Controller did not complete every approved compute and catalog operation.')
    return result


def serve(directory):
    """No network listener: stdin/stdout exist only for the current operator SSH session."""
    import yaml
    import openstack_platform as platform
    from openstack_install_phases import load_module
    root = Path(directory); data = root / 'data'
    prepared = json.loads((data / 'prepared.json').read_bytes())
    if (prepared['machine'] != hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()
            or prepared['config_sha256'] != file_hash(data / 'deployment.yaml')
            or prepared['binding_sha256'] != file_hash(data / 'compute-binding.json')):
        raise ValueError('controller completion input drift')
    line = sys.stdin.buffer.readline(LIMIT + 1)
    if len(line) > LIMIT: raise ValueError('oversized completion request')
    payload = json.loads(line)
    if set(payload) != {'clouds'}: raise ValueError('exact fresh Kolla auth document required')
    core = data / 'core'; core.mkdir(mode=0o700)
    (core / 'kolla-run').mkdir(mode=0o700)
    clouds = core / 'kolla-run/clouds.yaml'
    with clouds.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600); yaml.safe_dump(payload['clouds'], stream)
    path = root / 'driver/deployment/installer.py'
    sys.path[:0] = [str(path.parent), str(root / 'driver/nova-spvm')]
    backend = load_module(path, 'installer'); sys.modules['installer'] = backend
    original_config = backend.deployment_config
    backend.deployment_config = lambda filename, **kw: original_config(filename, transport=False)
    def forbidden(*args, **kwargs):
        raise ValueError('controller must not resolve or execute operator SSH during remote completion')
    backend.ssh_arguments = forbidden; backend.host_audits = forbidden
    sequence = 0
    def operation(name, value):
        nonlocal sequence
        print(json.dumps({'operation': name, 'data': value, 'sequence': sequence}), flush=True)
        line = sys.stdin.buffer.readline(LIMIT + 1)
        if not line or len(line) > LIMIT: raise ValueError('installation channel interrupted')
        reply = json.loads(line)
        if set(reply) != {'sequence', 'result'} or reply['sequence'] != sequence:
            raise ValueError('invalid operation acknowledgement')
        sequence += 1
        return reply['result']
    value = {'config': str(data / 'deployment.yaml'), 'workspace': str(data), 'platform': str(data / 'platform.json')}
    result = platform.post_install(value, path, backend, data / 'platform-install', core, operations=operation)
    print(json.dumps({'result': result}), flush=True)
