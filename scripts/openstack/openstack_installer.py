"""Select a trusted spvm-openstack installer backend; never bootstrap hardware here."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from openstack_errors import InstallerError
from urllib.request import Request, urlopen

MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def release_url(release):
    match = re.fullmatch(r'([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)@([A-Za-z0-9_.-]+)', release)
    if not match or match[3] in ('main', 'master', 'latest'):
        raise ValueError('release must be owner/repository@explicit-tag')
    owner, repo, tag = match.groups()
    request = Request(f'https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}',
                      headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'spvm-tools'})
    with urlopen(request, timeout=30) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError('release metadata exceeds limit')
    assets = [a for a in json.loads(payload).get('assets', [])
              if a.get('name', '').startswith('nova-spvm-') and a.get('name', '').endswith('.tar.gz')]
    if len(assets) != 1:
        raise ValueError('release must contain exactly one nova-spvm-*.tar.gz asset')
    url = assets[0].get('browser_download_url', '')
    if not url.startswith(f'https://github.com/{owner}/{repo}/releases/download/{tag}/'):
        raise ValueError('unexpected release download origin')
    return url


def unpack(archive, destination):
    with tarfile.open(archive, 'r:*') as bundle:
        members, seen, total = [], set(), 0
        for member in bundle:
            path = PurePosixPath(member.name)
            total += member.size
            if (len(members) >= 10000 or total > MAX_EXPANDED or path.is_absolute()
                    or not path.parts or '..' in path.parts or '\\' in member.name
                    or path.suffix in ('.pyc', '.pyo')
                    or not (member.isfile() or member.isdir()) or path.as_posix() in seen):
                raise ValueError('unsafe or oversized driver archive')
            seen.add(path.as_posix())
            members.append(member)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with bundle.extractfile(member) as source, target.open('xb') as output:
                    os.chmod(target, 0o600)
                    shutil.copyfileobj(source, output)


def backend(directory, *, verified_bundle=False):
    root = Path(directory).resolve(strict=True)
    candidates = [root/'deployment/installer.py']
    found = [p for p in candidates if p.is_file()]
    if len(found) != 1:
        raise ValueError('select a spvm-openstack checkout or bundle containing deployment/installer.py; the old monorepo layout is not supported')
    path = found[0]
    if not verified_bundle:
        if (root/'manifest.json').exists() or (root/'manifest.json').is_symlink():
            return backend(root, verified_bundle=True)
        source_manifest = root/'nova-spvm/manifest.json'
        if (source_manifest.is_symlink() or not source_manifest.is_file()
                or source_manifest.stat().st_size > 4 * 1024 * 1024
                or 'installation-kit-selection-v1' not in json.loads(source_manifest.read_bytes()).get('capabilities', [])):
            raise ValueError('Select a new installation-kit-selection-v1 checkout; old source contracts are not converted.')
    if verified_bundle:
        base = path.parent.parent
        manifest = json.loads((base/'manifest.json').read_text())
        hashes = manifest.get('source_sha256')
        if 'installation-kit-selection-v1' not in manifest.get('capabilities',[]):
            raise ValueError('Select a new installation-kit-selection-v1 kit. The old driver YAML field/options and old kits are not converted or migrated.')
        if 'prebuilt-openstack-images-v1' not in manifest.get('capabilities',[]):
            raise ValueError('Driver kit lacks prebuilt-openstack-images-v1. Select a new CI kit; no local image build or old workspace migration is supported.')
        if 'manual-host-resource-lifecycle-v1' not in manifest.get('capabilities',[]):
            raise ValueError('Driver kit lacks manual-host-resource-lifecycle-v1. Use a matching new kit; old configurations and workspaces are not migrated.')
        if 'controller-cloud-management-v1' not in manifest.get('capabilities',[]):
            raise ValueError('Driver kit lacks controller-cloud-management-v1. Use the matching new kit; controller state is created only by a fresh installation, without migration.')
        if (manifest.get('schema_version') != 1 or manifest.get('source_tree_dirty') is not False
                or manifest.get('source_repository') != 'Super-Protocol/spvm-openstack'
                or not re.fullmatch('[a-f0-9]{40}', str(manifest.get('source_commit', '')))
                or not isinstance(hashes, dict) or 'deployment/installer.py' not in hashes):
            raise ValueError('clean commit bundle with source checksums required')
        artifacts = manifest.get('kit_files_sha256')
        if not isinstance(artifacts, dict) or set(artifacts) != {'installation-kit.json','console-component.json','console-manifest.json','nginx.conf.j2'}:
            raise ValueError('Complete prebuilt installation kit artifact hashes required')
        for name, expected in {**hashes, **artifacts}.items():
            part = PurePosixPath(name)
            if (part.is_absolute() or '..' in part.parts or '\\' in name
                    or part.as_posix() != name or not re.fullmatch('[a-f0-9]{64}', str(expected))):
                raise ValueError('invalid source checksum manifest')
            item = base/name
            if not item.is_file() or item.is_symlink() or sha256(item) != expected:
                raise ValueError('bundle source checksum mismatch')
        # Imported modules must not escape the checksummed set via injected files.
        for folder in ('deployment', 'nova-spvm'):
            for item in (base/folder).rglob('*.py'):
                if item.relative_to(base).as_posix() not in hashes:
                    raise ValueError('unchecksummed Python module in bundle')
    return path


def select(args):
    if args.installation_kit_dir:
        if args.sha256 or args.staging_dir:
            raise ValueError('--sha256/--staging-dir apply to archive or release sources only')
        return backend(args.installation_kit_dir)
    if not re.fullmatch('[a-f0-9]{64}', str(args.sha256)) or not args.staging_dir:
        raise ValueError('archive/release requires --sha256 and a new --staging-dir')
    directory = Path(args.staging_dir).absolute()
    # Refuse existing artifacts, including failed/partial extractions. Never delete evidence.
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    archive = Path(args.installation_kit_archive).resolve(strict=True) if args.installation_kit_archive else directory/'driver.tar.gz'
    if args.installation_kit_release:
        with urlopen(release_url(args.installation_kit_release), timeout=30) as response, archive.open('xb') as output:
            os.chmod(archive, 0o600)
            if not response.url.startswith('https://'):
                raise ValueError('insecure download redirect')
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ARCHIVE:
                    raise ValueError('archive exceeds size limit')
                output.write(chunk)
    if archive.stat().st_size > MAX_ARCHIVE or sha256(archive) != args.sha256:
        raise ValueError('archive size/checksum mismatch')
    contents = directory/'contents'
    contents.mkdir(mode=0o700)
    unpack(archive, contents)
    roots = [p.parent for p in [contents/'manifest.json', *contents.glob('*/manifest.json')] if p.is_file()]
    if len(roots) != 1:
        raise ValueError('one bundle manifest required at root or one directory below')
    path = backend(roots[0], verified_bundle=True)
    receipt = {'schema_version': 1, 'archive_sha256': args.sha256,
               'backend': str(path), 'backend_sha256': sha256(path)}
    with (directory/'source-receipt.json').open('x') as stream:
        os.chmod(stream.name, 0o600)
        json.dump(receipt, stream, indent=2)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False, epilog=(
        'Pass backend arguments after --. Example: --installation-kit-dir /srv/spvm-openstack -- '
        'preflight --config deployment.yaml --output /srv/check-01. '
        'Owned preparation commands: plan-container-engine, prepare-container-engine. '
        'New-cloud inputs: init-kolla, check-kolla-workspace, list-kolla-tasks. '
        'Fresh compute configuration: stage-kolla-computes. Existing inventory extension: stage-add-compute-workspace. '
        'Initial-cloud checks: plan-kolla-prechecks, run-kolla-prechecks. '
        'Fresh-cloud transaction: plan-kolla-install, run-kolla-install. '
        'Portable SPVM intent: stage-spvm-enrollment (staging only, no API writes). '
        'Project API resources: plan-spvm-resources, apply-spvm-resources, check-spvm-barbican. '
        'Shared Neutron topology: plan-spvm-network, apply-spvm-network. '
        'Offline runtime: stage-runtime-enrollment, check-runtime-enrollment, plan-runtime-enrollment, install-runtime-enrollment. '
        'Initial host ingress: stage-ingress, check-ingress, plan-ingress, install-ingress, plan-ingress-activation, activate-ingress. '
        'Root-approved UI publication: plan-spvm-publication, publish-spvm-ui. '
        'Driver-owned web API runtime: stage-skyline-runtime. Console images are supplied by the CI kit. '
        'Fresh web services: plan-skyline-runtime, install-skyline-runtime (never overwrite occupied units). '
        'Prepared empty compute: plan-compute-activation, activate-compute. '
        'Retire a reachable empty compute-only host: plan-remove-compute, remove-compute. '
        'Scoped compute image: plan-kolla-compute-container, reconcile-kolla-compute-container. '
        'Read-only aggregate and next stage: initial-host-status. '
        'Occupied Kolla cloud, scoped read-only verification: verify-kolla-existing. '
        'Explicit scenario coordinator: plan-openstack, apply-openstack (optional fresh platform completion). '
        'Empty-host budget requests: sync-host-budgets, process-host-budgets. '
        'Opt-in budget processing: stage-host-budget-worker, install-host-budget-worker. '
        'Native fresh identities: stage-kolla-identities. Management DNS names: plan-hostnames, prepare-hostnames. '
        'Root-local registered-host Geneve firewall: plan-geneve-guard, prepare-geneve-guard. '
        'Existing private management and isolated host-NAT: plan/prepare-management, plan/prepare-nat. '
        'Dependency-only preparation: plan-base-packages, prepare-base-packages. '
        'Host SDK / public API trust: plan-host-sdk, prepare-host-sdk, plan-api-trust, prepare-api-trust. '
        'Root-local fresh helper: plan-host-helper, prepare-host-helper. '
        'Measured launcher assembly: stage-launcher, check-launcher. '
        'Matched Nova and Skyline images are retrieved on target hosts by immutable CI-kit digest. '
        'General fresh deploy remains gated; this wrapper does not bypass it.'))
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--installation-kit-dir', help='Trusted spvm-openstack checkout or verified expanded installation kit')
    source.add_argument('--installation-kit-archive', help='Installation kit archive')
    source.add_argument('--installation-kit-release', help='Public GitHub owner/repository@tag')
    parser.add_argument('--sha256', help='Operator-trusted archive SHA256; mandatory for archive/release')
    parser.add_argument('--staging-dir', help='New private directory; parent must already exist')
    parser.add_argument('--identity-file',help='Optional local SSH private key; otherwise use invoking-user standard keys/agent')
    parser.add_argument('--known-hosts',help='Optional local SSH trust file; otherwise use invoking-user ~/.ssh/known_hosts')
    parser.add_argument('backend_args', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    remaining = args.backend_args
    if remaining[:1] == ['--']:
        remaining = remaining[1:]
    if not remaining:
        parser.error('backend command required after --; use -- --help to list commands')
    try:
        path = select(args)
    except (OSError, ValueError, tarfile.TarError) as error:
        reason = str(error) if isinstance(error, ValueError) and str(error).startswith('Select a new installation-kit-selection-v1') else 'check source, checksum, manifest and staging path'
        print('ERROR: installation kit selection failed: '+reason+'. '
              'Partial staging is preserved; no backend was started.', file=sys.stderr)
        return 2
    try:
        # Runtime host administration uses only API messages and local actors.
        # Do not inspect the invoking/root user's SSH keys or agent at all.
        if remaining[0] in ('sync-host-budgets','process-host-budgets',
                            'stage-host-budget-worker','install-host-budget-worker'):
            if args.identity_file or args.known_hosts:
                raise InstallerError('Runtime host administration does not accept SSH credentials.')
            if remaining[0] in ('sync-host-budgets','process-host-budgets'):
                from openstack_host_budgets import main as host_budgets
                return host_budgets(path,remaining)
            from openstack_budget_service import main as budget_service
            return budget_service(path,remaining)
        # Resolve sudo's invoking user before any phase/child sanitizes its env.
        # No private key data is serialized; only transport paths and policy.
        sys.path.insert(0,str(path.parent))
        if not (path.parent/'ssh_transport.py').is_file():
            raise InstallerError('Selected driver lacks optional-cli-ssh-settings-v1. Select a matching updated bundle; do not edit a verified archive.')
        import ssh_transport
        def override(name):
            flag='--'+name
            for index in reversed(range(len(remaining))):
                if remaining[index].startswith(flag+'='):return remaining[index].split('=',1)[1]
                if remaining[index]==flag and index+1<len(remaining):return remaining[index+1]
            return None
        for name in ('identity-file','known-hosts'):
            if getattr(args,name.replace('-','_')) and override(name):
                raise InstallerError('Specify --'+name+' only once: before OR after the -- separator.')
        try:
            transport=ssh_transport.resolve(args.identity_file or override('identity-file'),
                                            args.known_hosts or override('known-hosts'))
        except (OSError,ValueError):
            raise InstallerError('Cannot resolve local SSH settings. Check --identity-file / --known-hosts paths and permissions, or SSH_AUTH_SOCK ownership. Defaults belong to the invoking user, including under sudo.') from None
        os.environ['SPVM_INSTALLER_SSH_POLICY']=json.dumps(transport,sort_keys=True)
        os.environ['SPVM_INSTALLER_SSH_BACKEND']=str(path.parent)
        trust_commands={
            'plan-base-packages','prepare-base-packages','plan-host-sdk','prepare-host-sdk','plan-api-trust','prepare-api-trust',
            'initial-host-status','plan-kolla-prechecks','run-kolla-prechecks','plan-kolla-install','run-kolla-install',
            'verify-kolla-existing','plan-container-engine','prepare-container-engine'}
        if remaining[0] in trust_commands and override('known-hosts') is None:
            remaining+=['--known-hosts',transport['known_hosts']]
        if remaining[0]=='stage-kolla-identities':
            from openstack_compute_identity import main as compute_identity
            return compute_identity(path,remaining)
        if remaining[0] in ('plan-hostnames','prepare-hostnames'):
            from openstack_hostnames import main as hostnames
            return hostnames(remaining)
        if remaining[0] in ('plan-skyline-runtime', 'install-skyline-runtime'):
            from openstack_skyline_runtime import main as skyline_runtime
            return skyline_runtime(path, remaining)
        if remaining[0] == 'stage-skyline-runtime':
            from openstack_skyline import main as skyline
            return skyline(path, remaining)
        if remaining[0] in ('plan-openstack','apply-openstack'):
            from openstack_workflow import main as workflow_coordinator
            return workflow_coordinator(path,remaining)
        if remaining[0]=='verify-kolla-existing':
            from openstack_kolla_existing import main as existing_kolla
            return existing_kolla(path,remaining)
        if remaining[0] in ('plan-kolla-compute-container','reconcile-kolla-compute-container'):
            from openstack_kolla_compute_scope import main as compute_scope
            return compute_scope(remaining)
        if remaining[0] in ('plan-compute-activation','activate-compute'):
            from openstack_compute_activation import main as compute_activation
            return compute_activation(path,remaining)
        if remaining[0] in ('plan-remove-compute','remove-compute'):
            from openstack_compute_removal import main as compute_removal
            return compute_removal(path,remaining)
        if remaining[0] in ('plan-spvm-publication','publish-spvm-ui'):
            from openstack_publication import main as publication
            return publication(path,remaining)
        if remaining[0] in ('stage-ingress','check-ingress','plan-ingress','install-ingress','plan-ingress-activation','activate-ingress'):
            from openstack_ingress import main as ingress_setup
            return ingress_setup(path,remaining)
        if remaining[0] in ('stage-runtime-enrollment','check-runtime-enrollment','plan-runtime-enrollment','install-runtime-enrollment'):
            from openstack_runtime_enrollment import main as runtime_enrollment
            return runtime_enrollment(path,remaining)
        if remaining[0] in ('plan-spvm-network','apply-spvm-network'):
            from openstack_spvm_network import main as network_api
            return network_api(path,remaining)
        if remaining[0] in ('plan-spvm-resources','apply-spvm-resources','check-spvm-barbican'):
            from openstack_spvm_resources import main as resources
            return resources(path,remaining)
        if remaining[0] in ('plan-kolla-install','run-kolla-install'):
            from openstack_kolla_workflow import main as workflow
            return workflow(path,remaining)
        if remaining[0]=='stage-spvm-enrollment':
            from openstack_spvm_enrollment import main as enrollment
            return enrollment(path,remaining)
        if remaining[0] in ('plan-management','prepare-management','plan-nat','prepare-nat'):
            from openstack_network_prepare import main as host_network
            return host_network(path,remaining)
        if remaining[0] in ('plan-geneve-guard','prepare-geneve-guard'):
            from openstack_geneve_guard import main as geneve_guard
            return geneve_guard(path,remaining)
        if remaining[0]=='initial-host-status':
            from openstack_initial_status import main as initial_status
            return initial_status(path,remaining)
        if remaining[0] in ('plan-base-packages','prepare-base-packages','plan-host-sdk','prepare-host-sdk',
                            'plan-api-trust','prepare-api-trust'):
            from openstack_kolla_packages import main as base_packages
            return base_packages(path,remaining)
        if remaining[0] in ('plan-kolla-prechecks','run-kolla-prechecks'):
            from openstack_kolla_prechecks import main as initial_prechecks
            return initial_prechecks(path,remaining)
        if remaining[0]=='stage-add-compute-workspace':
            from openstack_compute_workspace import main as compute_workspace
            return compute_workspace(path,remaining)
        if remaining[0]=='stage-kolla-computes':
            from openstack_kolla_compute import main as kolla_compute
            return kolla_compute(path,remaining)
        if remaining[0] in ('stage-launcher','check-launcher'):
            from openstack_launcher import main as launcher
            return launcher(path,remaining)
        if remaining[0] in ('plan-host-helper','prepare-host-helper'):
            from openstack_host_helper import main as host_helper
            return host_helper(path,remaining)
        if remaining[0] in ('init-kolla','check-kolla-workspace','list-kolla-tasks'):
            from openstack_kolla_workspace import main as workspace
            return workspace(path,remaining)
        if remaining[0] in ('plan-container-engine','prepare-container-engine'):
            from openstack_container_engine import main as prepare_engine
            return prepare_engine(path,remaining)
        # No sudo, pip installs, bootstrap invocation or hardware changes here.
        return subprocess.run([sys.executable, str(path), *remaining], check=False).returncode
    except InstallerError as error:
        print('ERROR: '+str(error),file=sys.stderr)
        return 2
    except (OSError, ValueError, tarfile.TarError) as error:
        print('ERROR: command '+remaining[0]+' failed ('+type(error).__name__+'). '
              'Driver selection succeeded. Preserve any partial plan/transaction and inspect this phase; '
              'do not automatically retry a preparation.',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
