# One-command fresh OpenStack installation

The public entry point is:

```bash
sudo bash scripts/spvm-openstack.sh install \
  --config /srv/spvm/deployment.yaml \
  --environment /srv/spvm/environment-01 \
  --output /var/lib/spvm-install-01 \
  --use-test-images
```

This is an **explicit fresh installation**, not an upgrade, reset, adoption or
repair command. Hardware bootstrap and preparation of the management Python
environment remain separate. The installer does not format disks, distribute
SSH keys, launch guests, import a provider configuration or create a Swarm.

Generated Ansible configurations use standard SSH pipelining, as recommended
by the pinned Kolla `doc/source/user/ansible-tuning.rst`. This reduces extra
module-transfer connections; it does not create a persistent SSH control master,
distribute service keys, allow agent forwarding, weaken host-key checking or
change remote sudo policy. Passwordless non-TTY sudo remains a prerequisite.
Caller-supplied Ansible environment/hooks are still filtered.

A clean-reset procedure must also retire the previous deployment's **proven**
managed host CA anchor. Its filename is deployment-bound, not a content hash:
a new installation of the same deployment ID creates a different CA and refuses
to overwrite the old anchor. Confirm the old cloud is gone, preserve its public
certificate/evidence, remove only that exact anchor and refresh the system trust
store. Never remove unrelated CAs, accept an unverified replacement or use
`--yes` as a trust override. The installer is not a reset/migration command.

## Inputs and prerequisites

Use the infrastructure-only deployment YAML described in
[the deployment guide](OPENSTACK_DEPLOYMENT.md). `installation_kit.directory` selects a
previously verified, expanded release bundle with source checksums and its
matching wheel; `manual-host-resource-lifecycle-v1`, `host-control-api-v1` and infrastructure-first
capabilities are mandatory. No compatibility or workspace migration is offered.
The kit must also advertise `installation-kit-selection-v1`; the old `driver`
YAML field/CLI selectors are rejected, not renamed automatically. Choose the kit
directory, not a Nova container tag; the kit pins the exact image digests.

`--environment` is the READY directory created by `prepare-environment.sh`.
The entry point chooses its `installer-venv` automatically. Kolla must match the
configuration's exact commit. The source collections must still match the READY
receipt; the installer prepares the Docker role in its own workspace, never in
the original environment.

The management host needs the prepared Python/Kolla environment, Git, OpenSSH,
OpenSSL and outbound HTTPS access. No local Docker binary/socket, image build,
CC, KVM or GPU is required. Target hosts retrieve the exact prebuilt Nova and
Skyline digests after approval; the manager verifies manifests and compatibility
before changing servers. See [prebuilt CI kits](OPENSTACK_PREBUILT_IMAGES.md).

Every server must already have its appropriate hardware bootstrap, working
operator SSH and passwordless sudo. A service-only controller need not have KVM.
Compute hosts must be empty and have an explicitly configured launch mode and
no initial resource fields. Assign resources after installation using
[manual host resources](OPENSTACK_HOST_RESOURCES.md). There must be no old OpenStack configuration, helper,
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

Base-image overrides are rejected. Select the complete coordinated
`prebuilt-openstack-images-v1` driver archive, verify its SHA256 and use the
unchanged sp-vm-tools commit recorded in `installation-kit.json`.
The kit pins the three repositories, Kolla, wheel, launcher, images and UI/nginx
files. Image availability, config ID, platform and source/wheel labels are
checked before host changes. There is no local image build or JS patch step.
Older kits/workspaces are not migrated. These two images are prebuilt; remaining
Kolla/OS/Python dependencies may still require Internet. Publication and GHCR
retention rules are described in [the CI-kit guide](OPENSTACK_PREBUILT_IMAGES.md).

## Scan, approval and execution

### One output directory for the installation

Keep the same `--output` for checking, approval and subsequent continuation.
A new directory starts a fresh installation. An existing directory is validated
under the coordinator lock before any server operation. A check-only run or
refused approval can be scanned again because no installation phase started.
Completed installations return their recorded result without replaying phases;
this is not a fresh live health check. Changed inputs or inconsistent evidence
are rejected, not silently adopted.

After successful checks, an existing output prints that installation will
continue from the previous verified point and asks
`Continue the previous installation? [y/N]:`. Only `y`/`yes` accepts; an empty
answer, `no` or EOF refuses without claiming the continuation or starting an
installation phase. This replaces the fresh-install deployment-name prompt,
not an additional question. `--yes` explicitly accepts continuation without
prompting, but never bypasses checks or enables an unsupported recovery.
`--check` remains read-only with respect to servers and asks no confirmation.
Already completed installations remain a no-op, without a continuation prompt.

Continuation is selected from the journal, not from an administrator-supplied
phase name. The currently supported post-mutation boundaries are the reviewed
test-image Kolla prechecks refusal, the reviewed OVN router refusal, and the
first-compute enrollment boundary below.
Other interrupted or failed operations stop with the phase and evidence path:
the installer does **not** assume that a failed command made no changes. This
does not implement arbitrary-phase recovery or rollback. Completed host
preparation and Kolla phases are never repeated just to get past an error.

The original `installation.json`, child journals and logs remain intact.
New local evidence is written below `attempts/attempt-NNNNNN/`; the private
`attempts/current.json` locates the latest attempt. A checked or unapproved
attempt does not claim the deployment boundary. A real approved continuation
claims it once before mutations. All history stays root-private; symlinks,
unrecognized directories, pointer cycles and inconsistent lineage are refused.
Do not delete evidence or change output to bypass a safety refusal.

### Explicit upstream test-image acknowledgement

This built-in profile uses `quay.io/openstack.kolla` images, which upstream
Kolla marks as testing-only. Supply `--use-test-images` only for an authorized
test installation. It is distinct from `--yes` and experimental Ubuntu 26
approval, and says nothing about guest debug/CC admission. Without it, the
installer fails before host preparation. The value is bound into the plan and
passed to Kolla's `prechecks`; it is not added to unsupported deploy actions.
It does not turn this kit into a production-certified distribution.

### Reviewed continuation after the test-image prechecks refusal

Repeat the ordinary install command with the same `--output`. The installer
selects this reconciler only when its journal identifies this boundary. There
are no public continuation-mode flags and no need to select a new run number.
This is not general repair of unknown partial operations. It accepts
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
marker reserves one continuation before deployment. A later failure is examined
at its own boundary; unknown outcomes need manual review, not a blind retry. Do not delete markers or rewrite
failed journals to manufacture successful boundaries.

`--check` performs the all-host read-only scan and writes a private **local**
plan. It creates no host directory, installs no package, probes no GPU MMIO and
downloads no dependency. Local launcher validation uses a private copy. The
command returns nonzero if a host or local prerequisite is blocked.

```bash
sudo bash scripts/spvm-openstack.sh install \
  --config /srv/spvm/deployment.yaml \
  --environment /srv/spvm/environment-01 \
  --output /var/lib/spvm-check-01 --use-test-images --check
```

For installation repeat the command with the **same output directory**, without `--check`.
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

The controller preparation/completion SSH channel uses encrypted OpenSSH
keepalive probes (`ServerAliveInterval=15`, `ServerAliveCountMax=12`), including
while the management process executes a long compute callback. An unresponsive
peer is detected after approximately three minutes; keepalive does not extend
operation deadlines, retry failed operations, or acknowledge completed work.
After a disconnect, inspect both transaction journals before continuing: a
remote process may still be waiting for an acknowledgement. Keepalive reduces
idle-channel failures but is not a recovery mechanism or proof of their cause.

If that channel is lost after the reviewed compute continuation has activated
the complete fleet, reuse the same `install --output` (optionally `--check`
first). This boundary is accepted only when every activation has a complete,
hash-bound `COMPUTE_READY` receipt and fresh GET/probe acceptance succeeds.
The controller must still be at the last compute acknowledgement, with no quota,
ingress, web or executor work started. Unknown or partial states are refused.

Approval (interactive or `--yes`) authorizes terminating only the exact old
root-owned, single-threaded controller Python reader blocked on stdin, if it
still exists. Its command hash and process start time are verified again;
termination uses a pidfd and SIGTERM, never a process-group kill or SIGKILL.
The new native continuation claim and stop intent are persisted first. Changes
or an unsuccessful termination stop the continuation for review. `--check`
never terminates a process, creates a native continuation, or mutates the cloud.

New versioned controller evidence inherits completed network, project-resource
and runtime-request receipts. Compute activation is forbidden on both sides of
the new dialogue: there is no repeated enrollment, quarantine, stop, start or
enable. Only remaining platform setup and authenticated acceptance run. Old
journals remain unchanged; source, identity, credential, configuration or live
state drift requires manual review. This is an explicit installer operation,
not automatic recovery of failed VMs/hosts or a generic failed-deploy retry.

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

### Host-side runtime dependency preflight

Initial compute activation must validate the runtime request using the prepared,
measured helper package before disabling scheduling or stopping Nova. The driver
is not installed into system Python. The isolated host script verifies and uses
`/usr/local/libexec/nova-spvm`; it does not use forwarded Python paths or install
global packages. Failure output identifies the compute, activation step and
private evidence directory. Bounded remote stderr is saved only in mode-0600
`observations/private-remote-*.json`; do not publish those diagnostics.

The observed super6 failure on 2026-11-06 stopped at `enroll` after quarantine
and Nova stop. Read-only inspection with the corrected host payload confirmed
the expected bootstrap runtime and an empty stopped host. No runtime enrollment,
Nova start, scheduling enable or platform continuation was performed by that
inspection. The cloud had zero servers; super7 remained up/enabled and super6
down/disabled. The idle libvirt process had a refreshed PID/start timestamp,
so the old protected-services baseline cannot simply be asserted unchanged.
Use a new verified bundle and an explicitly reconciled continuation; do not
reset old journals or present a read-only payload test as successful activation.

### Continue the first compute enrollment failure

Repeat the ordinary command with the same `--output`. The installer recognizes
the first `activate-<host>` journal only when `quarantine` and `stop` completed,
`enroll` is the failed/inflight action, quarantine was confirmed and no guests
were restarted. Kolla's successful inherited transaction and every parent/child
plan hash must agree. The selected clean bundle may change only the reviewed
`initial_compute.py`, `compute_workflow.py` dependency fix and its documentation;
wheel, capabilities and all other driver files must stay unchanged. No extra
CLI flag, source patching, bundle editing or workspace migration is required.

Before approval, read-only checks inspect every project's guests/reservations,
domain/state/PCI claims, actual stopped container, immutable prepared inputs,
bootstrap runtime, native identity, admin scope and fixed network/project
resources. Operator/service credentials are verified, not recreated. A runtime
already changed to the enrolled document is **not** assumed safe to replay.
Other service/container or protected-file drift refuses continuation. Only an
idle libvirt PID/start-time refresh with zero restart count is allowed while
the independent empty-host/virsh checks pass. A fresh activation plan then binds
its full current baseline and expires normally; no force bypass exists.

`--check` writes only new private local review evidence; it neither claims the
controller nor delivers sources, enrolls runtime, starts Nova, enables scheduling
or runs remaining platform phases. Normal interactive confirmation or `--yes`
is required for application. The management host claims the old transaction,
then the controller exclusively claims its own boundary **before** any compute
activation, preventing competing coordinators. Lost acknowledgement or another
partial failure stays blocked for review; claims are never cleared automatically.

The already disabled service is reconciled without another API disable write;
the stopped container is verified without a second stop command. The fixed
enrollment runs, then only that empty host's Nova compute starts, passes its
acceptance checks and is enabled. The remaining compute and GUI/ingress phases
continue. Completed auth, Neutron, enrollment staging and Barbican operations
are not replayed: verified evidence is copied into a new private checkpoint.
Old journals and credentials remain intact. No guests or Swarm nodes are created,
stopped, restarted or automatically restored.

The coordinator sends only the manifest-bound bundle/current installer and
typed acknowledgements over the finite installation channel. Operator SSH keys
and agents are never delivered to the controller; native API calls use the
existing verified SDK. Final success still requires authenticated platform/GUI
acceptance. Selecting this mode or passing `--check` is not installation success.

This continuation was tested synthetically and against the locally saved
failed/fresh-review plans on `petr-dev`. No target-host command or installation
was run for this continuation implementation. The next operator-run `--check`
must perform fresh live observations; the cached plans are not treated as them.

### Same-output validation evidence

The 66 targeted synthetic tests cover transaction ordering, both reviewed
reconcilers, same-command check/apply/no-op sequences, refused unknown failures,
private history validation and Skyline checks. Shell syntax and dependency-free
public `--help` passed. Tests do not prove successful live continuation.

Three real read-only invocations with the same output
`/var/lib/spvm-openstack-install-01/run-05` passed on super7 and super6; evidence
is below `attempts/attempt-000001` through `attempt-000003`. Selection chose
platform only and retained the original failed journal. No continuation claim,
router creation, Kolla deployment or other installation phase was executed.

### Reviewed platform-only continuation of the OVN router refusal

Repeating the ordinary command with the same `--output` selects the platform
reconciler for the observed `Provider ovn does not support ha=False` boundary:
Kolla's three phases are complete, platform completed only `operator-auth`,
the failed action is `shared-network`, the two exact networks/subnets exist,
the router is absent, all computes are healthy and there are no guests.
It does not accept other platform failures or attempt generic repair/adoption.

The selected clean bundle may differ only in `deployment/spvm_network.py` and
its documentation; wheel, version, capabilities and all other sources must
match. The reviewed update must already be staged on the controller under
`/opt/spvm-openstack/<deployment-id>-update-<source-commit-prefix>`.
Only `installation_kit.directory` may change in the infrastructure YAML; the prepared
region, guest/debug/base-image policies, workspace, SSH trust, identities and
host state are rechecked. Existing SDK versions and installed driver module
bytes must still match. `--use-test-images` remains explicit.

`--check` performs GET/authentication and filesystem checks only: root-private
state lineage, unchanged credentials and delegation metadata, network scope,
compute readiness and host observations. It does not create a router, deploy
sources or reserve a continuation. A new real run rechecks this review before
applying anything. `--yes` replaces only the administrator confirmation.

Only platform completion runs; Kolla, disk/pool preparation, helper deployment
and image delivery are not repeated. Existing operator/service auth is verified,
not recreated or rotated. New versioned controller sources and private journals
are used; original failed journals remain unchanged. Exclusive durable markers
on both the management host and controller prevent duplicate continuations.
No SSH key/agent is sent to the controller. The root-local SDK and validated
existing auth are used over the finite installation channel.

This mode changes provider-default router handling, not control-plane HA.
Any subsequent partial failure requires another explicit review; do not remove
markers or rewrite journals to retry blindly. Success still requires all native
API, compute, web/ingress and GUI checks, not just a created router.

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
reviewed failed run using the same output directory, never
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
starting with the original infrastructure YAML (without resource fields) and the
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
