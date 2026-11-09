# OpenStack with SPVM: deployment and operations

The management entry point is `scripts/spvm-openstack.sh`. Use `install` for a
new cloud and `add-compute` / `remove-compute` for compute membership. Hardware
bootstrap, installation, host resource allocation and Swarm creation are separate
operations. Installation creates no guest or Swarm cluster.

## Components and immutable kit selection

- `sp-vm-tools` owns hardware bootstrap, the launcher and management installer.
- `spvm-openstack` owns the Nova driver, CLI/API, restricted helper/executors,
  deployment backend and coordinated installation kit.
- `skyline-console` owns the frontend and its source-built container.

Choose a reviewed installation kit. Its `installation-kit.json` is the source
of exact component versions, source revisions, Kolla revision, image digests and
checksums. Do not copy identifiers from documentation or select `latest`.
Verify the downloaded archive against its companion checksum before extraction.
Checksums establish integrity, not a cryptographic signature or guest attestation.

Deployment YAML selects the complete extracted kit on the management machine:

```yaml
installation_kit:
  directory: /srv/spvm/installation-kit
```

The directory contains `manifest.json`, `installation-kit.json`, verified
sources and the matching wheel. It is not a Nova container tag or the kit's
`deployment` subdirectory. Advanced staging also accepts
`installation_kit.archive` or `.release` with `.sha256`. Private release assets
require authenticated delivery; the public release downloader does not inherit
GitHub CLI authentication. Never put tokens in YAML, command arguments or Git.

Use the installer sources recorded in the selected kit. The current schemas
and required capabilities are checked before host changes; incompatible inputs
are rejected without conversion, migration or fabricated receipts. The old
`driver` YAML selector and `--driver-*` flags are not accepted. Diagnostic
selectors are `--installation-kit-dir`, `--installation-kit-archive` and
`--installation-kit-release`.

Nova and Skyline images are built in CI and retrieved on target hosts by
verified digest. The management host does not build containers, patch JavaScript
or require Docker. Other Kolla images and system/Python dependencies still need
online access. See [prebuilt images and retention](OPENSTACK_PREBUILT_IMAGES.md).

## Prepare the management host and servers

Prepare the isolated installer/Kolla environment using
[environment preparation](OPENSTACK_ENVIRONMENT.md). This checks prerequisites
without installing system packages. Use the Kolla revision declared by the kit;
the READY receipt records the resolved tooling. Environment preparation does not
install OpenStack, the driver SDK or networking.

Every target needs verified operator SSH and noninteractive passwordless sudo.
Compute hosts need their appropriate hardware bootstrap, working KVM/libvirt,
launcher prerequisites and an explicit `tdx`, `sev-snp` or `untrusted` launch mode.
Detection never chooses a mode or downgrades failed CC. Non-CC compute is debug
only with explicit opt-in; service-only hosts do not require CC or KVM. See
[non-CC hosts](OPENSTACK_NON_CC.md) and [debug policy](OPENSTACK_DEBUG_BUILDS.md).

The integrated profile uses one controller/network/ingress host, compute hosts,
Ubuntu/Noble Kolla containers and `network.mode: host-nat`. Ubuntu 26 target hosts
require explicit `--allow-experimental-ubuntu26`; their real Ansible facts are
retained. Do not substitute another OS's Docker repository or falsify host facts.
This is not a highly available control-plane or ingress topology.

Fresh installation refuses unexplained existing OpenStack state or occupied
managed targets. It is not a reset, upgrade, adoption or recovery command.
Bootstrap, QEMU/libvirt, unrelated files and existing disks must not be removed
to bypass a refusal. Cleaning a previous cloud is a separate reviewed operation.

## Network responsibility and security

Administrators prepare the existing interfaces, addresses, routes, DNS,
connectivity and access policy. The installer does not create an encrypted
inter-host network or distribute VPN keys. Encryption is optional as a product
requirement, not a claim that an unencrypted or Internet-facing underlay is safe.

`network.management_mode` selects the management transport profile;
`network.mode` selects guest external networking. They are different settings.
The default management profile is `dedicated`. The explicit
[shared-interface profile](OPENSTACK_SHARED_INTERFACE.md) uses an existing
interface/address with owned peer guards and separate backend ports. It is
limited to its documented topology and does not make public interfaces private.
Ambiguous interface addresses or default routes are errors, not guessed choices.

Platform completion creates the shared project Neutron guest network on the
prepared transport. A separate guest network per Swarm is not required. Geneve
encapsulation, VLAN isolation and security groups do not encrypt packets.
Swarm's WireGuard protects its overlay, not all OpenStack control traffic.
TDX/SEV-SNP do not encrypt packets outside the guest boundary. API HTTPS does not
prove TLS on every internal service.

On an untrusted underlay, exposed traffic can disclose credentials or permit
tampering and disruption. Use authenticated encrypted transport or separately
review every relevant protocol. Never expose database, RabbitMQ, internal APIs
or virtualization endpoints unrestricted to the Internet. The administrator owns
firewall/isolation, MTU, VPN key lifecycle and reboot persistence. All computes
need the required peer connectivity, not just a route to the controller.

Management API/GUI reachability and public Swarm ingress are separate policies.
An SSH login does not authorize direct public API traffic. Use an approved tunnel
or management route and trust the generated CA; never disable TLS verification
or remove guards to make deployment pass. Connectivity checks are not a security
audit or a guarantee of public UI availability.

## SSH settings on the management host

`--identity-file` and `--known-hosts` are optional local CLI overrides, not
per-host YAML fields. Without overrides, the invoking user's standard SSH key
files/available agent and known-hosts are used, including under sudo. An explicit
key restricts authentication to that file and disables agent use; it must work
without interactive passphrase entry. For an unlocked agent under sudo, preserve
`SSH_AUTH_SOCK` explicitly if needed. Arbitrary key filenames are not scanned.

Host-key verification is always strict. Verify unknown or changed keys manually.
User SSH config, ProxyCommand/ProxyJump and forwarding are not implicitly enabled.
The installer never copies an operator private key or forwards an agent to a
server. SSH, SCP and Ansible share the selected policy. Keep the same key/trust
inputs throughout a transaction; their hashes participate in approval.
Runtime resource administration uses the protected typed API/executor channel,
not service SSH keys or arbitrary remote commands.

## Install, configure resources, then create workloads

Deployment YAML describes infrastructure: hosts, addresses, users, roles,
interfaces, launch modes, explicit debug policy, networking and kit/Kolla inputs.
It must not include `compute_resources`, `storage_pools`, `storage_discovery` or
`gpu_inventory`. It also does not accept guest builds, provider credentials or
Swarm domains. Detection is for display only, not resource allocation.

Run from the matching checkout using your reviewed paths:

```bash
sudo bash scripts/spvm-openstack.sh install \
  --config /srv/spvm/deployment.yaml \
  --environment /srv/spvm/environment \
  --output /var/lib/spvm/install
```

The command scans all hosts and requests explicit approval before preparing
hosts and deploying the cloud. `--check` performs read-only target checks;
`--yes` skips only confirmation. Use `--use-test-images` only for an authorized
deployment using the upstream testing images, as required by the selected
profile. This does not certify a production kit or enable debug guests.

Keep the same `--output` for supported installation continuation. Completed
phases are not blindly replayed; unknown/ambiguous partial failures require
review. The installer reports private diagnostic paths. Do not alter journals,
change output directories or edit kit hashes to bypass a refusal. See
[one-command installation](OPENSTACK_SINGLE_INSTALL.md) for supported boundaries.
Progress and observation-based WAIT messages use stderr; JSON/results remain on
stdout. See [progress and long operations](OPENSTACK_PROGRESS.md).

After installation, healthy computes are **UNCONFIGURED/disabled**. Neither
ordinary nor SPVM VMs may launch until an administrator assigns CPU/RAM, at least
one enabled state pool and explicitly enables the host. GPU use is optional and
starts disabled. The technical image cache is separate from workload storage.
See [manual host resources](OPENSTACK_HOST_RESOURCES.md) and
[GPU profiles](OPENSTACK_GPU_PROFILES.md). Installation does not format disks,
create workload pools or derive project quotas from physical host limits.

After resources are configured, administrators register authorized images,
create private flavors, import provider templates and configure scoped ingress
and retirement delegation through the CLI/UI. An installed GUI or uploaded
template alone grants no DNS authority. Secrets belong in protected storage,
not deployment YAML. See [platform administration](OPENSTACK_PLATFORM_WORKFLOW.md)
and [service delegation](OPENSTACK_SERVICE_DELEGATION.md).

## Add or retire compute hosts

Membership uses controller-owned configuration and a matching local environment,
without the original installation directory. `add-compute` takes a small
host-only YAML; `remove-compute` takes a host key. Both require operator SSH to
the controller, selected targets and existing peers. They do not copy operator
keys, regenerate cloud passwords/CA or automatically recover lost hosts.

See [controller management](OPENSTACK_CLOUD_MANAGEMENT.md),
[adding computes](OPENSTACK_COMPUTE_ADDITION.md) and
[retiring computes](OPENSTACK_COMPUTE_REMOVAL.md) for commands, backups, sequential
batch behavior and failure rules. New/returning hosts remain UNCONFIGURED/disabled.
Retirement requires a reachable empty compute-only host across all projects,
domains, allocations, migrations, reservations and GPU claims. It stops/deletes
no guest and retains bootstrap, QEMU/libvirt, state directories and cached data.

## Operational limits and private diagnostics

Resource edits require an empty host; increases are not exempt. Disable stops
new admissions, not existing guests. Changing a name alone is not technical GPU
reconfiguration. Failed observations are blockers, not proof of empty capacity.
Use request IDs and read-only status to inspect uncertain operations; do not
invent a new request ID to replay them.

Nova ACTIVE, SPVM readiness and public UI reachability are different states.
Release guests retain image authorization, measurement/signature-presence and
original-CA checks. Non-CC debug does not fabricate attestation. There is no
automatic VM/host recovery, evacuation, CC downgrade or single-controller HA.
SPVM state loses its per-boot encryption key on stop; preserved disk files do
not make reboot or state-preserving recovery supported.

Transaction directories contain credentials and private operational evidence.
Protect them, back up required controller state and inspect the named logs on
failure. Never commit them, expose raw private logs in UI output, or treat
checksums as protection against a compromised controller root.
