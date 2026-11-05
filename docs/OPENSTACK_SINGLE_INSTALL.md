# One-command fresh OpenStack installation

The public entry point is:

```bash
sudo bash scripts/install-openstack.sh install \
  --config /srv/spvm/deployment.yaml \
  --environment /srv/spvm/environment-01 \
  --output /var/lib/spvm-install-01 \
  --use-test-images
```

This is an **explicit fresh installation**, not an upgrade, reset, adoption or
repair command. Hardware bootstrap and preparation of the management Python
environment remain separate. The installer does not format disks, distribute
SSH keys, launch guests, import a provider configuration or create a Swarm.

## Inputs and prerequisites

Use the infrastructure-only deployment YAML described in
[the deployment guide](OPENSTACK_DEPLOYMENT.md). `driver.directory` selects a
previously verified, expanded release bundle with source checksums and its
matching wheel; `host-control-api-v1` and the current infrastructure-first
capabilities are mandatory. No compatibility or workspace migration is offered.

`--environment` is the READY directory created by `prepare-environment.sh`.
The entry point chooses its `installer-venv` automatically. Kolla must match the
configuration's exact commit. The source collections must still match the READY
receipt; the installer prepares the Docker role in its own workspace, never in
the original environment.

The management host needs a **working local Docker engine** to assemble the
matched Nova and Skyline images, sufficient local disk space for the bases and
export archives, Git/OpenSSH/OpenSSL and outbound access to the pinned public
images and Python package repositories. The environment preparer installs only
Python environments; neither it nor this installer installs/repairs management
host Docker or other management system packages. No local CC, KVM or GPU is
required. Actual image build/export failures remain failures, not readiness.

Every server must already have its appropriate hardware bootstrap, working
operator SSH and passwordless sudo. A service-only controller need not have KVM.
Compute hosts must be empty and have an explicitly configured launch mode and
resource reserves. There must be no old OpenStack configuration, helper,
occupied service targets, native OVS runtime or unexplained cloud state.
An enabled/running native HAProxy is not automatically taken over.

The first integrated profile supports Kolla 2026.2 Ubuntu/Noble, one combined
controller/network/ingress host, at least one compute, and `network.mode:
host-nat`. A separate management network or existing shared native interface is
supported; **no protected network is created**. All required host interfaces
must already exist. NAT egress is derived from the unique lowest-metric IPv4
default-route interface, not assumed to be the management interface. Ambiguous
addresses/routes are errors. The risks of unencrypted/public management remain
those in the deployment guide, not a guarantee supplied by SSH or TLS ingress.

Ubuntu 26.04 requires `--allow-experimental-ubuntu26`. Actual host facts are
retained, Noble containers are used and no Docker repository from another OS is
silently substituted.

Optional installation settings in the same YAML are:

```yaml
installation:
  guest_cidr: 10.77.0.0/24
  region: RegionOne
  allow_debug: false
```

These are defaults except `allow_debug`, which defaults to false and must be
explicitly true for an untrusted compute. This permits later authorized debug
images; it does not register/authorize any build. Release admission still
requires a CC-ready compute. The guest CIDR must be a non-overlapping RFC1918
IPv4 /16 through /30. The integrated network uses `physnet1`, the configured
transit CIDR, its `.1` offset as the host gateway and offset 10 as the router
address. DNS defaults are `1.1.1.1` and `8.8.8.8`. Check this policy against local
network requirements before approving. For a differently structured network,
do not pretend this profile applies; use a reviewed supported extension.

`installation.compute_base_image` and `installation.console_base_image` may
override the reviewed default Noble bases with explicit repository@sha256
references. Unknown Skyline/launcher source layouts still fail their exact
source checks; supplying a digest is not a compatibility bypass. Default digests
are recorded in the implementation and the installation artifact receipt. There
are no `latest` downloads or operator patch/SHA256 steps in this scenario.
The scan contacts the configured registries to inspect both pinned manifests
before any installation phase. It does not download image layers. Registry
garbage collection can remove old digests; pinning guarantees identity, not
availability. A missing or inaccessible manifest blocks installation early.
The default pins were refreshed from the official `2026.2-ubuntu-noble` images
on 2026-11-05; source-layout and build smoke checks remain mandatory.

### Matched Skyline source contract (2026-11-05)

The updated overlay targets reviewed `*.bundle.1791833599.js` assets from
Noble 2026.2 console digest
`f2f2c81685e656b2652691251d9dca38b68f67b61cc6ca58df273228209cd91b`.
An older already-downloaded bundle does not acquire these source changes.
The initial scan executes the selected bundle's isolated builder to check its
imports and explicit base contract before host preparation. Unknown pairs fail
closed. Registry availability alone is **not** GUI compatibility; actual source
checksums, exact one-occurrence patch sites and offline image checks remain
mandatory. No renaming, fuzzy patches, checksum bypass or old temporary-directory
assets are used. Mirrors may retain the same approved manifest digest.
Local real-image rendering/build checks are distinct from end-to-end cloud
installation and authenticated live UI acceptance, which must still complete.

## Scan, approval and execution

### Explicit upstream test-image acknowledgement

This built-in profile uses `quay.io/openstack.kolla` images, which upstream
Kolla marks as testing-only. Supply `--use-test-images` only for an authorized
test installation. It is distinct from `--yes` and experimental Ubuntu 26
approval, and says nothing about guest debug/CC admission. Without it, the
installer fails before host preparation. The value is bound into the plan and
passed to Kolla's `prechecks`; it is not added to unsupported deploy actions.
It does not turn this kit into a production-certified distribution.

### Reviewed continuation after the test-image prechecks refusal

`--continue-from /absolute/previous-run --output /absolute/new-run` is a narrow,
explicit continuation, **not** general resume or automatic repair. It accepts
only a prepared fresh installation whose parent stopped at Kolla and whose
child stopped at `prechecks` with the specific missing-test-images refusal,
zero completed Kolla phases and no deploy/post-deploy execution evidence.
Supply the same config, environment and SSH settings, plus explicit
`--use-test-images`. The previous bundle/config/keys/trust and completed phase
receipts must match. Updated local coordinator code is explicitly re-approved;
the driver and prepared Kolla inputs cannot be replaced or migrated.

Fresh read-only observations must show no guests and unchanged host identities,
protected files, containers, network and services. Only PID/start timestamps
of an otherwise identical active libvirt service with zero restart count may
refresh across the idle interval. Other drift refuses continuation. The new
Kolla child protects its complete fresh process baseline normally.

Use `--check` to validate without running Kolla or reserving a continuation.
Execution requires the normal confirmation or `--yes`. Only `kolla` and
`platform` run: storage, GPU, images, network and helper preparation are never
replayed. The original journals are preserved; an exclusive `continued-by.json`
marker reserves one continuation before deployment. Another failure needs
manual review, not a second automatic retry. Do not delete markers or rewrite
failed journals to manufacture successful boundaries.

`--check` performs the all-host read-only scan and writes a private **local**
plan. It creates no host directory, installs no package, probes no GPU MMIO and
downloads no dependency. Local launcher validation uses a private copy. The
command returns nonzero if a host or local prerequisite is blocked.

```bash
sudo bash scripts/install-openstack.sh install \
  --config /srv/spvm/deployment.yaml \
  --environment /srv/spvm/environment-01 \
  --output /var/lib/spvm-check-01 --use-test-images --check
```

For installation use a **different new output directory**, without `--check`.
The command scans all hosts, presents the selected hosts/pools and asks the
administrator to type the deployment name once. Without a terminal it refuses
implicit approval; `--yes` is an explicit noninteractive opt-in. It is not
inferred from stdin, an earlier check or an existing receipt. Root filesystem
pools are included in the plan; root usage/reserves must be reviewed as part of
that approval. Existing non-empty state is never overwritten or backed up
implicitly. Use the separate reviewed storage workflow if state replacement is
actually intended.

After approval, the fixed built-in sequence prepares storage and GPU inventory,
assembles the matched launcher/driver/console, renders fresh inputs, binds compute
identities, prepares Docker on **every** selected host, configures the owned
host-NAT/Geneve/hostname policies, installs helpers, delivers images, installs
native dependencies/API trust and runs native Kolla prechecks/deploy/post-deploy.
Working Docker is preserved. Failed Docker is not treated as absent.

It then initializes the fresh project/shared Neutron network, runtime enrollment,
compute readiness, dedicated API executors and host catalogs, management web
services and ingress scaffold. The final checks include authenticated Skyline
login/logout, API/catalog access, static assets, fresh capacity and enabled/up
compute services. Native Kolla success alone is **not** full installation
success. Only `OPENSTACK_READY_NO_SWARM_CREATED` means all these phases completed.
Image/flavor/provider/cluster catalogs remain empty for the administrator.

Guest release registration is enabled, but requires later explicit administrator
actions. Domain-scoped publication/retirement policies and provider secrets are
not guessed or installed from a template; configure them through the supported
administrator workflow before relying on web publication/deletion of Swarms.

## SSH and controller-local completion

Optional `--identity-file` and `--known-hosts` are **management-host paths**.
Otherwise invoking-user defaults are resolved, including under sudo. An explicit
key restricts authentication to that key and does not use the agent.

Kolla and host preparation run from the management machine using normal operator
SSH. Controller-local API/GUI completion runs over the current verified SSH
session. It may request only the named installation operations: staging the
new enrollment/runtime, activating a bound empty compute, auditing hosts, and
probing/installing its API executor. The management side reconstructs privileged
code from its selected sources and rejects arbitrary commands, scripts, unknown
hosts, changed bindings, duplicates and unexpected ordering. This is a finite
stdin/stdout installation dialogue, **not** a listening daemon or a generic
remote-execution API. No private key, SSH-agent socket or password is copied to
the controller; no SSH account/key is generated. New per-compute API credentials
are relayed through management-process memory over these verified channels,
never printed as terminal output or stored in local phase reports. Closing the session removes
the dialogue, not the permanent cloud services.

Permanent resource administration uses the host-control API design documented
in the selected driver's `deployment/HOST_CONTROL.md`:
root-local actors and restricted per-host Keystone application credentials, not
SSH. Non-login web/ingress Unix service accounts are distinct from the SSH
operator. Persistent sources and SDK are placed outside `/home` under
`/opt/spvm-openstack/<run>`, with private configuration/evidence in its mode-0700
`data` subdirectory. API credentials are root-only; they are not in YAML,
public receipts or browser output. The private Kolla auth document travels only
over the verified installation channel. Runtime SDK versions are recorded;
independent dependency resolution is not promised to be bit-for-bit identical.

## Failures and evidence

The private output directory contains `installation.json`, `plan.json`, generated
configs, child plans/results and private logs. Source/config/environment and
bundle hashes are checked before each phase. The parent intent is fsynced before
each child action. A failed/interrupted phase is `NEEDS_REVIEW`; later phases do
not run. There is no automatic rollback of a partial cloud, workspace adoption,
host reset, service repair, guest recovery or blind restart from a boundary.
Existing output directories are refused even for a successful earlier run.
Inspect both the parent and child journals before selecting another directory;
a new directory does not authorize adoption of a partially installed cloud.

The controller copy also retains its SDK dependency list, API/GUI acceptance and
service configuration journals. Keep these private. Do not commit private logs,
Kolla passwords/TLS keys, API credentials, provider configuration or SSH keys.

## Acceptance boundary

### Test-image continuation validation (2026-11-05)

Installer fix `fa4c79b` passed 45 targeted installer/continuation/Skyline checks.
Real read-only validation of the prepared `.247`/`.245` run passed, followed by
actual Kolla `prechecks` with explicit test-image acknowledgement, including
Nova, Neutron and Skyline checks. Protected host observations before/after were
identical with the libvirt connections held. Evidence is private under
`/var/lib/spvm-openstack-install-01/run-04-real-prechecks-01`; the explicit
continuation was not reserved and no `deploy` or platform completion was run.
This proves the prechecks fix, not a completed cloud installation. Continue the
reviewed failed run using a new output directory and `--continue-from`, never
by changing the failed journal's status or replaying initial preparation.

This new entry point has synthetic failure/order/approval/protocol checks and
local shell/Python/help checks. Those **do not establish** clean-OS, cold-cache,
real Kolla deployment, Ubuntu26 dependency resolution, reboot, API-canary or
authenticated GUI acceptance of the integrated command. Record the actual
outcome of a subsequent live run separately; do not relabel mock callbacks or
an older multi-command installation as this command's live acceptance.

### Earlier checkpoint: 2026-11-05, before the Skyline/continuation fixes

The current implementation passed 24 targeted single-install tests and 46
existing host-control protocol/coordinator tests. The public shell syntax,
Python AST and dependency-free help were also checked. This is a targeted test
result, not a claim that the entire historical untracked test tree passes:
broader attempts found stale fixtures for launcher contracts, workspace source
bindings and the earlier host-catalog API. Production checks were not weakened
to satisfy those fixtures.

A real read-only run from `petr-dev` succeeded against `super7` and `super6`,
starting with the original infrastructure YAML (`storage_pools: auto`) and the
existing READY environment. It validated pip/import/CLI/collection state and
both hosts, then recorded `CHECKED_NOT_INSTALLED`. Private evidence is in
`/var/lib/spvm-openstack-install-01/one-command-check-08`. Earlier local failed
attempts remain retained rather than overwritten. These checks found and fixed
sudo Git ownership handling for pinned launcher blobs and automatic-storage
planning order. No remote directory/package/network/helper/cloud deployment was
performed by these checks.

The selected existing driver is 0.2.30, source commit
`4a70cf06d13ffe107a0e922b371cbf5c9fbef5ca`. The new orchestration changes are in
`sp-vm-tools`; this checkpoint does not require pretending that a new driver
binary was published. Full integrated installation and authenticated API/GUI
acceptance remain **not executed** at this checkpoint.
