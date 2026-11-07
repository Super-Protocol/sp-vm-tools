"""Built-in fresh installation phases. Never load executable recipes from YAML."""
import base64
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace as R

import yaml
from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError

# The reviewed Noble bases used by this installer profile, not floating tags.
BASES = {
    'compute': 'quay.io/openstack.kolla/nova-compute@sha256:4f7778f2aa22bf5a07aa7dc4690edda945fa730c390be59873910573bfa5c836',
    'console': 'quay.io/openstack.kolla/skyline-console@sha256:f2f2c81685e656b2652691251d9dca38b68f67b61cc6ca58df273228209cd91b',
}


def check_base_images(options):
    """Inspect pinned registry metadata before any host mutation; never pull layers."""
    import openstack_compute_image as image
    bases = {key: options.get(key + '_base_image', value) for key, value in BASES.items()}
    for key, reference in bases.items():
        if not isinstance(reference, str) or not image.BASE_RE.fullmatch(reference):
            raise InstallerError(key + ' base image must be an immutable repository@sha256 reference.')
        try:
            manifest = json.loads(image.docker(['manifest', 'inspect', reference]).stdout)
            entries = manifest.get('manifests')
            if entries is not None and not any(
                    m.get('platform', {}).get('os') == 'linux'
                    and m.get('platform', {}).get('architecture') == 'amd64' for m in entries):
                raise ValueError('no linux/amd64 manifest')
            if entries is None and not manifest.get('config', {}).get('digest'):
                raise ValueError('invalid image manifest')
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
            raise InstallerError('Cannot verify ' + key + ' base image in its registry. '
                'The pinned manifest may be unavailable, access may be denied, or registry/network inspection failed. '
                'No installation phase was started. Check registry access and the pinned base-image configuration.') from None
    return bases


def document(path, value):
    path = Path(path)
    with path.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        yaml.safe_dump(value, stream, sort_keys=False)
    return str(path)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def address(audit, interface, expected=None):
    entries = json.loads(audit['addresses']['stdout'])
    candidates = [a for link in entries if link['ifname'] == interface
                  for a in link.get('addr_info', []) if a.get('family') == 'inet'
                  and a.get('scope') == 'global' and (expected is None or a['local'] == expected)]
    if len(candidates) != 1:
        raise InstallerError('Cannot unambiguously select an existing IPv4 address on ' + interface + '.')
    return str(ipaddress.IPv4Interface(candidates[0]['local'] + '/' + str(candidates[0]['prefixlen'])))


class Installation:
    def __init__(self, args):
        from openstack_installer import backend
        self.args = args
        source = Path(args.config).resolve(strict=True)
        self.raw = yaml.safe_load(source.read_bytes())
        if not isinstance(self.raw, dict):
            raise InstallerError('Infrastructure deployment YAML must be a mapping.')
        hosts=self.raw.get('hosts',{})
        if isinstance(hosts,dict):
            for name,host in hosts.items():
                forbidden=set(host)&{'compute_resources','storage_pools','storage_discovery','gpu_inventory'} if isinstance(host,dict) else set()
                if forbidden:
                    raise InstallerError('Host '+str(name)+': installation resource fields are not accepted: '+', '.join(sorted(forbidden))+
                        '. Configure resources after installation through GUI or CLI/API, then explicitly Enable the host.')
        self.options = self.raw.pop('installation', {})
        allowed = {'guest_cidr', 'region', 'allow_debug', 'compute_base_image', 'console_base_image'}
        if not isinstance(self.options, dict) or set(self.options) - allowed:
            raise InstallerError('Unknown installation option; executable phase recipes are not supported.')
        if type(self.options.get('allow_debug', False)) is not bool:
            raise InstallerError('installation.allow_debug must be a boolean.')
        self.guest = ipaddress.IPv4Network(self.options.get('guest_cidr', '10.77.0.0/24'), strict=True)
        if not 16<=self.guest.prefixlen<=30:
            raise InstallerError('installation.guest_cidr must be a canonical RFC1918 IPv4 /16../30 network.')
        self.region = self.options.get('region', 'RegionOne')
        if not isinstance(self.region, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', self.region):
            raise InstallerError('Invalid region name.')
        try:
            driver = self.raw['driver']['directory']
        except (KeyError, TypeError):
            raise InstallerError('Set driver.directory to the verified, extracted driver bundle.') from None
        try:self.path = backend(Path(driver), verified_bundle=True)
        except (OSError,ValueError):
            raise InstallerError('Selected driver bundle validation failed. Check driver.directory, its manifest and source checksums; '
                'do not edit the verified archive or reuse partial staging.') from None
        sys.path[:0] = [str(self.path.parent), str(self.path.parent.parent / 'nova-spvm')]
        self.backend = load_module(self.path, 'installer')
        sys.modules['installer'] = self.backend
        transport = load_module(self.path.with_name('ssh_transport.py'), 'installation_ssh')
        self.ssh = transport.resolve(args.identity_file, args.known_hosts)
        os.environ['SPVM_INSTALLER_SSH_POLICY'] = json.dumps(self.ssh, sort_keys=True)
        os.environ['SPVM_INSTALLER_SSH_BACKEND'] = str(self.path.parent)
        self.known_hosts = self.ssh['known_hosts']
        self.environment = Path(args.environment).resolve(strict=True)
        self.environment_receipt = self.environment / 'environment.json'
        receipt = json.loads(self.environment_receipt.read_bytes())
        if receipt.get('status') != 'READY' or receipt.get('inputs', {}).get('kolla_revision') != self.raw.get('kolla', {}).get('revision'):
            raise InstallerError('Use a READY prepare-environment directory for the exact Kolla revision in deployment YAML.')
        self.environment_state=receipt['installed']
        from prepare_environment import collection_state
        if collection_state(self.environment/'collections')!=receipt.get('installed',{}).get('collections'):
            raise InstallerError('Prepared environment collections differ from environment.json. Select an unchanged READY environment; no repair is performed.')
        self.kolla_source = self.environment / 'kolla-source'
        self.kolla_python = self.environment / 'kolla-venv/bin/python'
        self.collections = self.environment / 'collections'
        import openstack_kolla_workspace as workspace
        workspace.pinned(str(self.kolla_source), self.raw['kolla']['revision'])
        if self.raw['kolla']['series'] != '2026.2' or self.raw['kolla']['base_distro'] != 'ubuntu':
            raise InstallerError('The built-in fresh profile supports reviewed Kolla 2026.2/Noble images only.')
        self.output = Path(args.output)
        self.config = str(self.output / 'deployment.input.yaml')
        # Validation may require resolved invoking-user transport; no server is contacted here.
        self.cfg = None
        self.inputs = {str(p): file_hash(p) for p in [source, self.environment_receipt,
            self.path.parent.parent / 'manifest.json', Path(self.known_hosts),
            *(Path(p) for p in self.ssh['identity_files']),
            *sorted(Path(__file__).parent.glob('*.py'))]}
        manifest=json.loads((self.path.parent.parent/'manifest.json').read_bytes())
        import openstack_platform as platform
        platform.require_web_separation(self.path)
        if 'host-control-api-v1' not in manifest.get('capabilities',[]):
            raise InstallerError('The selected driver must support host-control-api-v1; no runtime SSH fallback exists.')
        self.inputs.update({str(self.path.parent.parent/n):s for n,s in manifest['source_sha256'].items()})
        self.inputs.update({str(self.path.parent.parent/'wheels'/n):s for n,s in manifest.get('wheel_sha256',{}).items()})
        self.transaction = None

    def phase_args(self, **values):
        return R(config=self.config, known_hosts=self.known_hosts,
                 allow_experimental_ubuntu26=self.args.allow_experimental_ubuntu26,
                 use_testing_images=getattr(self.args, 'use_testing_images', False),
                 ack_new_cloud=True, resume=False, plan_sha256=None,
                 **values)

    def refresh(self):
        self.cfg = self.backend.deployment_config(self.config)
        self.audits = self.backend.host_audits(self.cfg)
        return self.audits

    def remote(self, name, source, *, timeout=300):
        command = self.backend.ssh_arguments(self.cfg['hosts'][name]) + ['sudo -n /usr/bin/python3 -I -B -']
        result = subprocess.run(command, input=source, text=True, capture_output=True, timeout=timeout)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise InstallerError(name + ': installation operation failed. Inspect the phase journal; remote stderr is suppressed.')
        return json.loads(result.stdout)

    def scan(self):
        if not self.args.use_testing_images:
            raise InstallerError('This fresh-install profile uses quay.io/openstack.kolla test images. '
                'Explicitly supply --use-test-images for an approved test installation. '
                '--yes and --allow-experimental-ubuntu26 do not imply this acknowledgement. No host preparation was started.')
        from basic_preflight import check
        self.verify_environment()
        from openstack_install_artifacts import console_builder
        console_builder(self, ['--check-base-image', self.options.get('console_base_image', BASES['console'])])
        check_base_images(self.options)
        document(self.config, self.raw)
        self.cfg=self.backend.deployment_config(self.config)
        controls = [n for n, h in self.cfg['hosts'].items() if 'control' in h['roles']]
        networks = [n for n, h in self.cfg['hosts'].items() if 'network' in h['roles']]
        if len(controls) != 1 or networks != controls or self.cfg['network']['ingress_host'] != controls[0]:
            raise InstallerError('Fresh profile requires one controller/network/ingress host; HA is a separate scenario.')
        if self.cfg['network']['mode'] != 'host-nat':
            raise InstallerError('The built-in fresh profile currently requires network.mode: host-nat.')
        self.controller = controls[0]
        self.audits=self.backend.host_audits(self.cfg)
        blockers = list(check(self.cfg, self.audits)['blockers'])
        for name, h in self.cfg['hosts'].items():
            fact = self.audits[name]
            if fact['os_version'] == '26.04' and not self.args.allow_experimental_ubuntu26:
                blockers.append(name + ': explicitly approve experimental Ubuntu26 with --allow-experimental-ubuntu26')
            if 'compute' in h['roles']:
                if h['launch_mode'] == 'untrusted' and self.options.get('allow_debug') is not True:
                    blockers.append(name + ': untrusted compute requires installation.allow_debug: true')
        self.network_documents()
        from openstack_install_completion import neutron_request
        validator=load_module(self.path.with_name('spvm_network.py'),'install_network_schema')
        validator.normalize({**neutron_request(self),'project_id':'0'*32})
        # Scan platform destinations and native services on every host, not just the controller.
        for name, h in self.cfg['hosts'].items():
            observed=self.remote(name, '''import json,subprocess,sys,importlib.util
from pathlib import Path
def run(argv):
 p=subprocess.run(argv,capture_output=True,text=True,timeout=30)
 if p.returncode not in (0,1,3,4):raise ValueError('inspection failed')
 return p.stdout.strip()
if Path('/etc/kolla/globals.yml').exists() or Path('/etc/spvm/host-helper.json').exists():
 raise ValueError('existing cloud/helper: not a fresh installation')
for path in ('/etc/kolla','/etc/nova','/opt/stack','/run/openvswitch','/etc/spvm/auth',
             '/etc/spvm/host-control-dispatcher-credentials.json'):
 p=Path(path)
 if p.is_symlink() or (p.exists() and (not p.is_dir() or any(p.iterdir()))):
  raise ValueError('existing cloud/platform state requires review')
for unit in ('openvswitch-switch.service','ovsdb-server.service','ovs-vswitchd.service'):
 if run(['systemctl','is-active',unit])=='active' or run(['systemctl','is-enabled',unit])=='enabled':
  raise ValueError('native OVS service requires administrator review')
ha={'load':run(['systemctl','show','haproxy.service','--property=LoadState','--value']),
    'active':run(['systemctl','is-active','haproxy.service']),
    'enabled':run(['systemctl','is-enabled','haproxy.service'])}
print(json.dumps({'status':'FRESH_HOST_CHECKED','haproxy':ha}))
''')
            if name==self.controller:
                self.haproxy_initial=observed['haproxy']
                if self.haproxy_initial['active']=='active' or self.haproxy_initial['enabled']=='enabled':
                    blockers.append(name+': existing enabled/active native HAProxy requires administrator review')
        import openstack_compute_image as image
        try:image.docker(['info', '--format', '{{.ServerVersion}}'])
        except (OSError,ValueError,subprocess.SubprocessError):
            raise InstallerError('A working local Docker engine is required to assemble the matched Nova/Skyline images. '
                'Check docker info on the management machine. The environment preparer installs Python environments only; '
                'this command does not install or repair Docker on the management machine.') from None
        import openstack_launcher as launcher
        recipe,_,_=launcher.recipe(self.path)
        launcher.stage(self.path,str(Path(__file__).resolve().parents[2]),recipe['tools_commit'],str(self.output/'checked-launcher'))
        import openstack_collections as collections
        if (self.collections/'ansible_collections/openstack/kolla/roles/docker/tasks/main.yml').read_bytes()!=collections.ORIGINAL:
            blockers.append('The prepared environment Docker role is not the supported original source')
        return {'schema_version': 1, 'deployment_id': self.cfg['deployment_id'],
                'use_testing_images': self.args.use_testing_images,
                'hosts': {n: {'roles': h['roles'], 'address': h['address'],
                              'machine_sha256': self.audits[n]['host_identity_sha256']}
                          for n, h in self.cfg['hosts'].items()},
                'hardware': {n:{k:f[k] for k in ('logical_cpus','memory_total_bytes','gpus','filesystems')} for n,f in self.audits.items()},
                'resources': 'UNCONFIGURED', 'blockers': blockers,
                'management_mode': self.cfg['network'].get('management_mode'),
                'guest_cidr': str(self.guest), 'provider_credentials_required': False,
                'phases': list(self.phases()), 'guests_created': False}

    def verify_environment(self):
        from prepare_environment import validate,isolated_env
        log=self.output/'private-environment-check.log'
        try:
            with log.open('x') as stream:
                os.fchmod(stream.fileno(),0o600)
                actual=validate(self.environment,isolated_env(self.environment),stream)
        except Exception:
            raise InstallerError('The prepared local Python/Kolla/Ansible environment failed verification. '
                'Inspect '+str(log)+'. No dependency installation or host preparation was started.') from None
        if actual!=self.environment_state:
            raise InstallerError('Prepared Python packages/collections differ from the READY receipt. '
                'Select an unchanged environment or prepare a new one; no automatic repair is performed.')

    def network_documents(self):
        transit = ipaddress.IPv4Network(self.cfg['network']['transit_cidr'], strict=True)
        if self.guest.overlaps(transit) or transit.num_addresses < 16:
            raise InstallerError('Guest/transit networks overlap or transit network is too small.')
        hosts, tunnels = {}, {}
        shared = self.cfg['network'].get('management_mode') == 'shared-interface'
        for name, h in self.cfg['hosts'].items():
            local = address(self.audits[name], h['management_interface'], h['address'] if shared else None)
            if ipaddress.IPv4Interface(local).network.overlaps(self.guest) or ipaddress.IPv4Interface(local).network.overlaps(transit):
                raise InstallerError(name + ': guest/transit CIDR overlaps management network.')
            hosts[name] = {'machine_sha256': self.audits[name]['host_identity_sha256'],
                           'management_address': local, 'route_interface': h['management_interface']}
            if set(h['roles']) & {'network', 'compute'}:
                tunnel = address(self.audits[name], h['tunnel_interface'])
                tunnels[name] = {'machine_sha256': hosts[name]['machine_sha256'],
                                 'tunnel_ip': str(ipaddress.IPv4Interface(tunnel).ip)}
        routes=json.loads(self.audits[self.controller]['routes']['stdout'])
        defaults=[r for r in routes if r.get('dst')=='default' and r.get('dev')]
        if not defaults:raise InstallerError('No IPv4 default egress route on the selected network host.')
        metric=min(r.get('metric',0) for r in defaults)
        devices={r['dev'] for r in defaults if r.get('metric',0)==metric}
        if len(devices)!=1:raise InstallerError('Ambiguous default egress interfaces; configure host routes before installation.')
        self.host_network = {'schema_version': 1, 'hosts': hosts, 'vip_owner': self.controller,
            'guest_cidr': str(self.guest), 'nat': {'host': self.controller,
            'egress_interface': devices.pop(),
            'transit_address': str(transit[1]) + '/' + str(transit.prefixlen), 'router_ip': str(transit[10]),
            'probe_ports': [80, 443, 7946, 9180, 9443]}}
        write(self.output / 'network.json', self.host_network)
        write(self.output / 'geneve.json', {'schema_version': 1, 'hosts': tunnels})
        write(self.output / 'preparation.json', {'schema_version': 1,
            'hosts': {n: str(ipaddress.IPv4Interface(h['management_address']).ip) for n, h in hosts.items()},
            'network_request': str(self.output / 'network.json'), 'geneve_request': str(self.output / 'geneve.json')})

    def phases(self):
        from openstack_install import PHASES
        def phase(name):
            from prepare_environment import collection_state
            receipt=json.loads(self.environment_receipt.read_bytes())
            if collection_state(self.collections)!=receipt['installed']['collections']:
                raise InstallerError('Source environment collections changed during installation. Stop and inspect the transaction.')
            return getattr(self,'install_'+name.replace('-','_'))()
        return {name:lambda n=name:phase(n) for name in PHASES}

    def install_artifacts(self):
        from openstack_install_artifacts import prepare
        return prepare(self)

    def install_workspace(self):
        import openstack_kolla_workspace as workspace
        from basic_preflight import check
        report = check(self.cfg, self.audits)
        if report['blockers']:
            raise InstallerError('Resolved-resource preflight failed: ' + ', '.join(report['blockers']))
        self.rendered = str(self.output / 'rendered')
        self.backend.render(self.cfg, self.rendered, self.audits,
                            kolla_source=str(self.kolla_source), preflight=report)
        self.workspace = str(self.output / 'kolla-inputs')
        receipt = workspace.init(R(config=self.config, rendered=self.rendered,
            kolla_source=str(self.kolla_source), kolla_python=str(self.kolla_python),
            collections=str(self.collections), output=self.workspace, ack_new_cloud=True), self.backend)
        return {'status': receipt['status']}

    def install_compute_binding(self):
        from nova_spvm.gpu_profiles import catalog
        from openstack_compute_identity import identities
        import openstack_host_helper as helper
        import openstack_kolla_compute as compute
        import kolla_inputs
        requests = {}
        for name, identity in identities(self.cfg, self.audits).items():
            h = self.cfg['hosts'][name]
            from openstack_gpu_prepare import detect
            detected=detect(self.backend,self.path,h)
            request = {'version': 1, 'machine_sha256': self.audits[name]['host_identity_sha256'],
                'compute_uuid': identity['compute_uuid'], 'launch_mode': h['launch_mode'],
                'allow_debug': self.options.get('allow_debug', False),
                'tools': '/opt/spvm-launcher/' + self.run_id,
                'registered_release_cache_root': '/var/lib/spvm/registered-releases',
                'pools': [], 'gpu_inventory': detected, 'gpus': []}
            helper.request(request)
            filename = self.output / ('helper-' + name + '.json')
            write(filename, request)
            requests[name] = str(filename)
        self.requests = requests
        write(self.output / 'requests.json', {'schema_version': 1, 'hosts': requests})
        result = compute.stage(R(config=self.config, rendered=self.rendered, workspace=self.workspace,
            requests=str(self.output / 'requests.json'), image_context=str(self.output / 'compute-context'),
            image_context_sha256=self.compute_context_sha, region=self.region,
            output=str(self.output / 'kolla-bound')), self.path, self.backend, kolla_inputs)
        self.workspace = str(self.output / 'kolla-bound')
        return result

    def install_docker(self):
        import openstack_container_engine as engine
        import openstack_kolla_workspace as workspace
        receipt = workspace.check(self.workspace)
        for name in self.cfg['hosts']:
            args = self.phase_args(action='plan-container-engine', host=name,
                kolla_source=receipt['kolla_source'], collections=receipt['collections'],
                collections_sha256=receipt['collections_sha256'],
                ansible_playbook=str(self.kolla_python.parent / 'ansible-playbook'),
                output=str(self.output / ('docker-' + name)))
            plan = engine.execute(args, self.backend)
            args.action = 'prepare-container-engine'; args.plan_sha256 = plan['plan_sha256']
            engine.execute(args, self.backend)
        return {'status': 'ALL_HOST_DOCKER_READY'}

    def install_network(self):
        import openstack_platform as platform
        import openstack_platform_network as network
        value = {'config': self.config, 'preparation': str(self.output / 'preparation.json'),
                 'allow_experimental_ubuntu26': self.args.allow_experimental_ubuntu26}
        target = self.output / 'host-network'; target.mkdir(mode=0o700)
        platform.prepare_hostnames(value, self.path, self.backend, target)
        network.prepare(value, self.path, self.backend, target)
        return {'status': 'ALL_HOST_NETWORK_READY'}

    def install_dependencies(self):
        import openstack_kolla_packages as packages
        import openstack_kolla_workflow as workflow
        with workflow.hold_libvirt(R(known_hosts=self.known_hosts),self.backend,self.cfg):
            return self._install_dependencies(packages)

    def _install_dependencies(self,packages):
        for profile in ('base-packages', 'host-sdk', 'api-trust'):
            args = self.phase_args(action='plan-' + profile, workspace=self.workspace,
                                   output=str(self.output / profile))
            plan = packages.execute(args, self.path, self.backend)
            if plan.get('blockers'):
                raise InstallerError('Dependency checks failed: ' + ', '.join(plan['blockers']))
            args.action = 'prepare-' + profile; args.plan_sha256 = plan['plan_sha256']
            packages.execute(args, self.path, self.backend)
        # Native controller-only prerequisites. Do not install packages on petr-dev.
        import openstack_kolla_workspace as workspace
        receipt=workspace.check(self.workspace)
        target=self.output/'controller-packages';target.mkdir(mode=0o700)
        h=self.cfg['hosts'][self.controller]
        inventory={'all':{'hosts':{self.controller:{'ansible_host':h['address'],'ansible_user':h['user'],
            'ansible_python_interpreter':'/usr/bin/python3',
            **({'ansible_ssh_private_key_file':h['identity_file']} if h.get('identity_file') else {})}}}}
        write(target/'inventory.json',inventory)
        play=packages.playbook('base-packages')
        play[0]['vars']['ubuntu_pkg_install']=['haproxy','python3-venv','acl']
        write(target/'packages.json',play)
        from openstack_ssh import common_args
        workspace.private_file(target/'ansible.cfg','[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n'
            '[ssh_connection]\nusetty = False\nssh_args = '+common_args(self.known_hosts)+'\n')
        env={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','OS_','PYTHON'))}
        env.update(ANSIBLE_CONFIG=str(target/'ansible.cfg'),ANSIBLE_COLLECTIONS_PATH=receipt['collections'],
            PATH=str(self.kolla_python.parent)+':/usr/sbin:/usr/bin:/sbin:/bin')
        with (target/'private-packages.log').open('xb') as log:
            os.fchmod(log.fileno(),0o600)
            result=subprocess.run([str(self.kolla_python),'-m','ansible.cli.playbook','-i',str(target/'inventory.json'),
                str(target/'packages.json')],env=env,stdout=log,stderr=log,timeout=1800)
        if result.returncode:raise InstallerError('Controller prerequisite packages failed; inspect private-packages.log.')
        if self.haproxy_initial['load']=='not-found':
            self.remote(self.controller,'''import subprocess,json
p=subprocess.run(['systemctl','is-active','haproxy.service'],capture_output=True,text=True)
if p.stdout.strip()=='active':raise ValueError('unexpectedly active new HAProxy; review required')
subprocess.run(['systemctl','disable','haproxy.service'],check=True,capture_output=True)
print(json.dumps({'status':'NEW_HAPROXY_DISABLED_UNTIL_INGRESS_APPROVAL'}))
''')
        from openstack_install_completion import prepare
        prepare(self)
        return {'status': 'ALL_HOST_DEPENDENCIES_READY'}

    def install_helpers(self):
        import openstack_host_helper as helper
        from openstack_compute_onboard import helper_phase
        assembled, files = self.launcher_files
        for name, filename in self.requests.items():
            request = json.loads(Path(filename).read_bytes())
            runtime = helper.bootstrap_policy('https://' + self.cfg['network']['api_vip'] + ':5000/v3', self.region)
            snapshot = helper.sources(self.path)
            snapshot['/etc/spvm/runtime-services.json'] = (json.dumps(runtime, indent=2).encode(), 0o644)
            snapshot.update({str(Path(request['tools']) / 'scripts' / n): (body, 0o755) for n, body in files.items()})
            output = '/var/lib/spvm-installer/' + self.run_id + '/helper-' + name
            before = helper_phase(self, name, request, assembled, snapshot, output)
            helper_phase(self, name, request, assembled, snapshot, output, apply=True, before=before)
        return {'status': 'ALL_HOST_HELPERS_READY_DENY_ALL'}

    def install_images(self):
        import openstack_image_transfer as transfer
        build = self.output / 'compute-build/image-build.json'
        package = self.output / 'compute-package'
        exported = transfer.export(R(build_receipt=str(build), build_sha256=file_hash(build),
            image_id=self.cfg['kolla']['compute_image_id'], reserve_gib=10, output=str(package)))
        for name, h in self.cfg['hosts'].items():
            if 'compute' not in h['roles']: continue
            transfer.deliver(R(package=str(package), package_sha256=exported['package_sha256'],
                host=h['user'] + '@' + h['address'], identity_file=None, known_hosts=self.known_hosts,
                ssh_port=h.get('port', 22), machine_sha256=self.audits[name]['host_identity_sha256'],
                reserve_gib=10, output=str(self.output / ('delivery-' + name))))
        # The console image is needed only on its single controller; no Docker TCP API is exposed.
        from openstack_install_artifacts import deliver_console
        deliver_console(self)
        return {'status': 'ALL_HOST_IMAGES_DELIVERED_NOT_STARTED'}

    def install_kolla(self):
        import openstack_kolla_workflow as kolla
        args = self.phase_args(action='plan-kolla-install', workspace=self.workspace,
                               output=str(self.output / 'core'))
        plan = kolla.execute(args, self.path, self.backend)
        if plan.get('blockers'):
            raise InstallerError('Kolla installation blocked: ' + ', '.join(plan['blockers']))
        args.action = 'run-kolla-install'; args.plan_sha256 = plan['plan_sha256']
        result = kolla.execute(args, self.path, self.backend)
        if result['status'] != 'KOLLA_DEPLOYED_NOT_SPVM_ENROLLED':
            raise InstallerError('Kolla deployment did not complete.')
        return result

    def install_platform(self):
        if getattr(self.args,'continue_channel_from',None):
            from openstack_install_channel_continue import complete
            return complete(self)
        if getattr(self.args,'continue_compute_from',None):
            from openstack_install_compute_continue import complete
            return complete(self)
        if getattr(self.args,'continue_platform_from',None):
            from openstack_install_platform_continue import complete
            return complete(self)
        from openstack_install_completion import complete
        return complete(self)
