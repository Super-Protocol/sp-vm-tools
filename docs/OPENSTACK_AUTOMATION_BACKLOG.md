# OpenStack installer: cleared-host deployment and remaining automation

Checkpoint: 2026-10-21. This is an implementation and acceptance backlog, not a
claim that installation is unattended or that a fresh operating system was tested.
See [the deployment guide](OPENSTACK_DEPLOYMENT.md) for command schemas and safety
boundaries. Repository documentation must remain in English.

## Planned follow-up: add computes using controller SSH access (2026-11-07)

Status: **design required, not implemented**. Allow an administrator to add one
or several hosts to an existing cloud with controller addresses and an operator
SSH key, without a large deployment YAML or the original management machine's
local `--installation` history. Retain only a small description/list of the new
hosts; determine which target/bootstrap/transport details genuinely need input.

- Discover the cloud identity and authoritative installation settings from an
  authenticated, eligible controller. Design a durable controller-side source
  of truth for the settings/artifact pins currently scattered across local
  workspaces and journals. OpenStack API discovery alone does not reconstruct
  our helper, network, launcher, Kolla and credential configuration.
- Verify controller SSH identity, administrative authority, cloud UUID, CA and
  component/version provenance before accepting recovered settings. Never guess
  a cloud, select latest artifacts or regenerate passwords/CA to fill gaps.
- Define the minimal metadata/backup needed if the original deployment host is
  lost, and actionable refusal when controller-side evidence is incomplete.
  Account for multiple controllers and ambiguous/partial operations; coordinate
  with the separate control-plane HA plan rather than assuming a fixed primary.
- Keep operator SSH limited to explicit installation/maintenance. Do not copy
  the operator key/agent to controllers or introduce a persistent service SSH
  account, arbitrary-command endpoint or runtime SSH fallback. Preserve typed
  host-control APIs and the empty-host/ownership/admission guards.
- Preserve all-target preflight, explicit approval, sequential per-host results
  and stop-on-first-failure behavior. No automatic rollback, lost-host/VM
  recovery, legacy-format migration or replay of ambiguous transactions.
- Acceptance must include a new management machine with only controller SSH
  access and a minimal new-host input, lost local installation history, wrong
  cloud/key/CA, missing evidence, competing operators and controller loss.

The current host-only YAML/batch frontend is an intermediate simplification:
it still requires the original local installation receipts and linked history.

## In progress: management CLI and manual resource allocation (2026-11-07)

The entry point is now `scripts/spvm-openstack.sh`; install, add-compute and
remove-compute are subcommands. The old filename is removed without a wrapper.
The manual resource contract is implemented for local verification; publication
and live acceptance are still pending. Do not treat this note as stand acceptance.
See [manual host resources](OPENSTACK_HOST_RESOURCES.md).

## Planned follow-up: native OpenStack control-plane HA (2026-11-06)

Status: **planned, not implemented**. Keep network/Swarm ingress HA as a separate
workstream. Use native Kolla/OpenStack redundancy rather than a replacement
control-plane implementation: three controllers, redundant APIs/schedulers/
conductors, MariaDB/Galera and RabbitMQ with a tested one-controller-loss quorum.

- Clients use one stable cloud API endpoint; no user-selected primary controller.
  The deployment host retains real addresses of every controller in inventory
  and performs installation/lifecycle work over operator SSH.
- Remove assumptions that controller-local operations always execute on one
  fixed server. Select an available eligible controller explicitly, bind its
  identity and serialize mutations across controllers. Never retry ambiguous
  operations on another controller without reconciling durable evidence.
- Adapt the SPVM resource/catalog coordinators, executors and their protected
  credentials/state to the HA design. Multiple API instances alone do not make
  these components highly available. Keep database/Barbican persistence and
  recovery/backup requirements explicit.
- Controllers need no GPU/CC when they do not host corresponding workloads.
  Compute failure and VM replacement/recovery remain separate; no automatic
  recovery of lost SPVM nodes is introduced.
- Test loss and return/replacement of each controller, quorum loss, concurrency
  and partial/lost acknowledgements. Distinguish deployment-host access, common
  API availability and correctness of persistent control-plane state.

## Planned follow-up: install first, allocate host resources separately (2026-11-06)

Status: **implementation and verification in progress**. The checkpoints below
describe earlier behavior, not acceptance of the new manual resource contract.

### Installation and resource lifecycle

- Remove `compute_resources`, `storage_pools`, `storage_discovery` and
  `gpu_inventory` from installation YAML. Reject these fields with an actionable
  message; do not translate old configuration or migrate existing deployments.
- Install OpenStack, the driver/helper, GUI and restricted host-control
  executors. Detect CPU/RAM, mounted filesystems and GPUs for display only:
  do not allocate budgets, register workload pools or enable GPU use.
- Separate technical storage from workload pools. Use
  `/var/lib/spvm/registered-releases` for the registered-image cache, not an
  automatically selected state pool. Do not format disks or alter partitioning.
- Register compute disabled from the outset, without a scheduling window.
  Distinguish `UNCONFIGURED`, `CONFIGURED_DISABLED` and `ENABLED` from service
  health and CC readiness. Driver/helper admission must reject unconfigured or
  administratively disabled hosts even if their Nova service is manually enabled.
- Apply the same behavior to initial deployment and later compute additions.
  Installation succeeds with a healthy, manageable but unconfigured fleet;
  its final message directs the administrator to configure and enable hosts.
- Configure CPU/RAM, the first and subsequent state pools, and optional GPU use
  after installation through GUI or CLI/API. Enable compute separately only
  after budget, storage, runtime/helper, network and launch-mode checks pass.
  GPU discovery alone does not authorize use; CPU-only compute remains supported.
- Preserve the empty-host requirement across all projects, domains, allocations
  and reservations. Disabling scheduling never stops existing VMs. No automatic
  enablement, VM/host recovery, rollback or replay after ambiguous operations.
- Support operator-owned automation through the same authenticated, typed
  APIs/CLI, with generation checks, request IDs, operation results and explicit
  enable/disable. Do not introduce SSH service keys or arbitrary remote commands.
- Keep project quotas independent: do not derive or rewrite them from physical
  host budgets during installation.

### Remove misleading Automatic mode and the installation baseline

The current `Automatic` label is derived by comparing actual settings with a
baseline saved at installation. CPU/RAM are hardware totals minus saved host
reserves; pool budgets are capped by the saved budget and current free space
minus reserve. Matching these numbers does not establish a persistent automatic
allocation policy, and catalog refresh does not autonomously apply that policy.

- Remove the `Automatic / administrator defaults` selector and the API/CLI
  `auto` budget mode, installation-baseline policy/catalog dependencies and the
  inference that labels matching current values as `Automatic`.
- Display detected hardware and the actually applied explicit budgets. Before
  first configuration, display `Not configured`, not inferred automatic values
  or a fictitious positive budget. Do not merely rename the obsolete mode.
- Keep autodetection/recommendations as a separate optional post-install action:
  preview recommended values and apply them only on explicit administrative
  request. Once applied, they are ordinary explicit budgets, not an ongoing
  automatic mode. Refreshes/reboots must not overwrite manual choices.
- Do not retain hidden install-time defaults or reconstruct a baseline from
  current settings. Backward compatibility and baseline migration are out of
  scope.

### Acceptance and delivery

- Test fresh installation and add-compute without resource fields, with all
  targets visible but unavailable through Auto, explicit host selection and
  direct Nova requests until configuration and explicit enablement complete.
- Test first CPU/RAM/pool configuration, root-only and multiple filesystems,
  duplicate mounts, GPU-less hosts and synthetic multi-GPU. Preserve root-pool
  warnings, no double accounting and non-CC debug-only admission.
- Test GUI and CLI/API setup and enable/disable, partial configuration, occupied
  hosts, stale generations, repeated request IDs, interrupted operations and
  reboots. No saved value may silently switch to `Automatic` merely because it
  matches a recommendation; recommendations never apply on discovery/refresh.
- Implement orchestration and English instructions in sp-vm-tools; driver,
  helper, GUI, CLI/API and their English documentation in swarm-cloud. Stage
  production code/documentation only; leave tests unstaged. Commits, push,
  builds and stand deployment are authorized for the current task but remain
  pending until the new contract passes live acceptance.

## Planned follow-up: immutable, CI-validated installation kit (2026-11-05)

Status: **planned, not implemented**. Local Skyline adaptation and successful
artifact assembly do not complete this work. The earlier checkpoints below
remain historical, not evidence for the new kit.

Motivation: previously pinned upstream manifests became unavailable; replacing
them with reachable images exposed a different Skyline asset layout. The unified
installer also had an isolated sibling-import defect. Mock-only checks and
registry metadata inspection did not exercise the complete real-image path.

- Build matched Nova and Skyline derivative images in CI, not for the first time
  on the customer's management host. Keep Skyline customization in swarm-cloud;
  keep installation orchestration in sp-vm-tools.
- Publish one compatibility manifest binding installer and Kolla revisions,
  driver wheel, launcher, GUI sources, base/derivative image digests and archive
  checksums. Refuse mismatched components; do not add old-workspace migrations.
- Store required images in an operator-controlled release registry with an
  explicit retention policy. Provide a checksum-verified offline installation
  kit for the declared supported scope; enumerate any remaining online
  dependencies. A digest proves identity, not registry availability.
- Use unique immutable published release identifiers. A mutable development
  channel may point to a release, but must not be the installation identity or
  silently replace bytes under an already published version.
- Require CI to exercise the real pinned images, isolated builder imports,
  exact source hashes and unique patch sites, complete offline derivative
  builds, served identity/gzip assets and browser login rendering. Keep mock
  tests, but never present them as real-image or authenticated cloud acceptance.
- Before any remote mutation, verify the complete kit's compatibility,
  checksums, required artifact availability and local runtime prerequisites.
  Report actionable errors; never fall back to floating tags, unknown patches
  or checksum bypasses.

Acceptance: a fresh management environment installs using the published kit
without compiling or patching Skyline/Nova locally. A mismatched kit, tampered
artifact, missing registry image or unavailable required dependency fails before
host changes. Test the documented offline scope without outbound access. Keep
clean-OS deployment and authenticated live GUI/API acceptance separate from
local artifact/browser checks. Publish the tested component inventory and
precise acceptance scope with the release, without credentials.

## Intended operator experience

1. Prepare confidential-computing hardware separately using the existing
   bootstrap scripts, firmware/BIOS configuration and required reboots.
2. Run the OpenStack installer from an operator/controller environment. Select
   initial control-plane deployment or addition of compute hosts explicitly.
3. Review one infrastructure deployment plan, then apply it. The coordinator should drive all
   supported preparation, deployment, project enrollment and GUI stages.
4. After installation, configure per-host CPU/RAM, state pools and optional GPU
   use through GUI or CLI/API, then explicitly enable each compute. Operator-owned
   automation uses the same interfaces; installation does not allocate resources.
5. Log in to Skyline, import/select a named provider template, and create Swarm
   clouds using per-node CPU, memory, state disk and optional GPU profiles.

Hardware bootstrap is **not** part of the OpenStack installer. Preserve a working
host libvirt/QEMU, QGS/PCCS, VFIO configuration and primary management connectivity.

## Verified artifacts and environment for this run

| Item | Pin or scope |
| --- | --- |
| Hosts | `.247` control/network/compute/ingress; `.245` compute |
| Operating system | Ubuntu 26.04; explicitly experimental for this Kolla source |
| Kolla source | `4b493ef36ace2b90257ecf94feb00e2c6e8014c9`, 2026.2 |
| Container distribution | Ubuntu noble; real host facts remain Ubuntu 26.04 |
| Installer snapshot | `52cd397c538d28b7032a9ddb88693ea7955c6c1f` |
| Driver source | `c4df9f8ee9395cd0a5da492c3f4a058ed28c0025`, package 0.2.23 |
| GitHub release channel | `openstack-feature-spvm-bare-metal-cloud-1325095077dd` |
| Driver archive SHA256 | `1b032cc543cd26a4aadcb094bcc5ba786f4484a5a7906e6ff3d5b55b5c9b99f5` |
| Nova base | `quay.io/openstack.kolla/nova-compute@sha256:f5aadb8b42f653403b4db2a4b2b68fafe132b9f57191c318237736df4df7ebae` |
| Skyline base | `quay.io/openstack.kolla/skyline-console@sha256:2ce16dc6bb5bf70fd17bb36248c19c83216da3dfddf870e97e045cbc9f4054d5` |
| Built Nova image ID | `sha256:8e6b7ce2f426e9765b62fa223b6294ba107ec891b72a3920ecca9eec9efe6f8a` |
| Built Skyline image ID | `sha256:48e859c286d8cab280d6a790b0c397cd990e981e0ea15ad3ca85de2a535ed4e5` |
| Enrolled guest release | `build-449-release`; no debug/SSH readiness fallback |

The release channel and package version alone do not identify immutable bytes.
Retain the downloaded archive checksum, source commit, wheel/context/build
receipts and actual container image IDs. Download private GitHub assets with an
authenticated `gh` followed by `--driver-archive`; the public release downloader
does not automatically use `gh` authentication.

The physical hosts were cleared of OpenStack-specific state, containers, volumes
and networking. Their OS, Docker, CC stack, libvirt/QEMU, measured release caches
and prerequisite dependency caches were retained. Consequently this is
**cleared-host redeployment**, not clean-OS or fully cold-cache acceptance.
Unattributed native MySQL/memcached on `.245` were retained, not silently removed.

## Current boundaries

- Kolla `prechecks`, `deploy` and `post-deploy` completed successfully.
- Authenticated internal/public-profile API reads passed with strict TLS.
- The two native Nova compute services registered; no guest was launched.
- The shared Neutron topology, private resource profiles and release marker were
  created through explicit plan/apply commands. The private-ACL Barbican marker
  probe passed and removed its own marker.
- Both computes passed the guarded initial enrollment sequence, including native
  accounting/network/runtime acceptance, and are enabled for scheduling.
- The empty host ingress scaffold and matching Skyline runtime were installed.
  Actual login, domain selection APIs, authenticated Swarm/template catalogs,
  unauthorized-request rejection and nine JS/logo probes including gzip passed.
  One named `default` provider template was imported into private Barbican records.
  This is HTTP/API acceptance, not a headed-browser or guest-launch test.
- An operator still prepares artifacts, images, SDK and private management
  transport explicitly. The common coordinator now drives the supported
  prepared-host-to-platform phases without manual recovery. Lab preparation
  glue is not a portable OS-to-platform installer; do not copy fixed lab UUIDs
  into production.

No existing encrypted guest disk or previous Swarm CA was restored. Guest state
uses ephemeral encryption keys; stopped guests cannot be treated as resumable.
Do not restart/bootstrap an existing Swarm to test installer idempotency.

## Priority 1: finish supported end-to-end orchestration

### Latest combined-path live checkpoint

The latest cleared-host run with the pinned 0.2.23 release completed the common
coordinator uninterrupted: parent `COMPLETE`, platform
`PLATFORM_READY_NO_SWARM_CREATED`, both computes enabled/up, one template/image,
two profiles, native capacity and nine static probes, without creating a guest.
No worker overlay or child recovery was required. The separate web account cannot
read admin OpenRC; installed worker bytes match the release. All 123 protected
host files and both public routes remained unchanged. The preceding failed run
and its scoped-recovery evidence remain preserved, not relabelled as successful.

Live findings fixed in source:

- Defer only the owned NAT host's not-yet-created `spvm-ext0` and API VIP in the
  early guard; verify them fully after the approved network phase.
- Explicitly preserve `/etc/hosts` mode during atomic replace/rollback under
  private installer `umask 077`. Otherwise non-root Kolla lookup cannot read it.
- Explicitly set the operator auth directory to `0750`; keep OpenRC `0640`.
- Keep web export and ingress read-only OpenStack identities separate; validate
  both as non-root, without granting web access to administrator credentials.
  Gate fresh deployment on `separate-web-ingress-identities` in the driver bundle.

353 installer tests and seven publication-worker tests passed after these fixes.
The updated published 0.2.23 bundle passed the uninterrupted common-workflow
acceptance requirement. Remaining acceptance includes clean-OS/cold-cache and
reboot/failure scenarios; retain the broader artifact/bootstrap preparation
boundaries below. This is not OS-to-platform proof or a new Swarm-launch test.

Implementation follow-up: the working tree now contains `stage-kolla-identities`,
owned hostname preparation, optional narrowly approved stale networkd-cache repair,
and fresh common-workflow `platform` completion. See
[the platform workflow](OPENSTACK_PLATFORM_WORKFLOW.md). Regression checks and
read-only checks are not a complete live run of that new path. Artifact generation,
Docker provisioning, helper installation, physical/WireGuard transport and initial
workspace preparation remain explicit earlier stages; integrating those is still
needed for the broader OS-to-platform operator experience below.

### One reviewed workflow with explicit stage boundaries

Extend the public coordinator to own dependency setup, host network preparation,
helper installation, image build/distribution, Kolla initialization/deployment,
shared network creation, project enrollment, compute activation, ingress and
Skyline runtime installation. Keep independent durable child receipts and exact
source/input hashes. Never turn this into an unrestricted Kolla reconfigure.

Fresh project/user/network IDs are unavailable before Keystone/Neutron exist.
Therefore generate the GUI enrollment and final backend configuration **after**
API deployment. Console image construction may happen earlier. The existing
combined Skyline preparation command cannot eliminate this dependency merely by
requiring a preexisting enrollment file.

Acceptance: one public plan/apply workflow reaches an explicitly named
`PLATFORM_READY_NO_SWARM_CREATED` boundary, verifies the real API/GUI, and reports
remaining user actions. A repeat must inspect live state without generating new
credentials, changing image pins, restarting guests or replaying an ambiguous
child operation.

### Native Nova compute UUID alignment

In the pinned Kolla `nova-cell/tasks/config.yml`, an unregistered hypervisor gets
`hypervisor_nodename | to_uuid`; an existing registered host uses its matched UUID.
The initial helper requests in this run used independently generated UUIDs.
Kolla overwrote `compute_id`, causing helper/pool identity checks to refuse
runtime activation even though the Nova service was healthy.

The lab recovery quarantined empty computes, proved absence of domains/disks/
claims, stopped their Nova process/helper, backed up helper configs, and bound
helper/pool identities to the **unchanged actual Nova UUID**. New immutable
binding/enrollment inputs were then generated; the failed journals were retained.

Implement this properly in the installer: derive/resolve the authoritative UUID
before helper installation where possible and verify it after Kolla registration.
Use the pinned upstream rule rather than a second unrelated UUID scheme.
An adoption/reconciliation path must prove the host is empty, protect pool locks,
preserve existing Nova identity and refuse populated or ambiguous hosts. Never
silently rewrite UUIDs in an already sealed workspace or ownership ledger.

### Hostname resolution before the long Kolla prechecks

RabbitMQ requires the controller hostname to resolve uniquely to its API-interface
address. These hosts initially resolved their own names to `127.0.1.1`; the other
host's name was absent. A scoped repair backed up `/etc/hosts`, removed only the
exact conflicting lab hostname entries and added the approved management mappings.
No public interface/default route was changed.

Add an early read-only diagnostic and an explicit owned hostname/DNS preparation
phase. Preserve unrelated aliases and comments; refuse conflicting external DNS
or ambiguous aliases rather than replacing `/etc/hosts` wholesale. Verify the
result on every selected host before invoking Kolla.

### Management interface convergence after cleanup

systemd-networkd retained an old in-memory definition for the management dummy
interface after its files were removed. The old `/24` and VIP reappeared when the
new interface was created. A networkd reload and reconfiguration of **only** the
dummy interface restored the approved `/32`; public connectivity was checked.

Teach preparation/cleanup to verify effective live addresses and cached manager
state, not just files on disk. Do not reload/reconfigure the physical management
bond as a generic fix. WireGuard may carry approved routes with `Table=off`;
ownership of guest probe routes must remain with the network preparation phase.

### Firewall policy detection

This run found `ufw.service` active/exited while `ufw status` reported inactive
and neither IPv4 nor IPv6 contained UFW rules. Commit `fbad069` adds conservative
shared detection for that exact disabled-policy case. Active/unknown policy and
other active managers remain blockers. Do not disable UFW or flush rules to make
installation pass. Integrate this committed fix into the next installer release.

## Priority 2: reproducibility, operator input and acceptance

### Cold-cache artifacts and dependencies

The previously recorded Nova base digest was no longer downloadable from Quay.
Resolve an explicitly selected version tag to an immutable digest in a separate
reviewed step, record it and build only from that pin. Never silently substitute
`latest`, switch releases, or reuse a local image as proof of remote availability.
Keep failed build receipts; use a new transaction for an approved replacement pin.

Verify a fully cold download of Kolla, collections, Python dependencies and both
container bases. The current run used verified prerequisite dependency caches.
Document registry authentication/rate limits and an offline mirror/export option.

### Project scope, resource budgets and provider templates

Add supported input/plan stages for publisher/reader identities, project role
membership, private flavors/images, project quotas and provider templates.
Do not equate global project quota with host capacity: Nova/Placement must still
place the complete VM on one eligible host. Retain the chosen host reserves and
explicit state-pool budgets; thin qcow2 allocation does not authorize unlimited
logical disk commitments. Multiple physical disks require independently owned
mounts/pool inventories and a reviewed usable-capacity policy.

GPU is a per-node resource, accounted by native Nova PCI. Discover hardware and
allow the administrator to select registered devices. Human-readable aliases
such as H100 memory variants remain a planned usability improvement; do not map
different GPUs to one misleading label. Preserve per-node CPU/RAM/state/GPU and
optional host-placement choices.

Named provider templates live in Barbican with explicit ACLs. Import the current
approved provider directory through CLI/UI; do not restore previous cloud secrets
or hardcode tokens in installer output. Preview masks credentials as `HIDDEN`.
At cluster creation change only the supported cluster DNS fields. DNS zone and
gateway policy must come from the selected provider configuration, not a lab
hostname embedded in a script. Validate gateway DNS ownership and reachability
before claiming a Swarm UI is published.

### Acceptance levels must be reported separately

1. Host prerequisites, protected CC/libvirt continuity and public SSH connectivity.
2. Kolla deployment and actual authenticated HTTPS service APIs.
3. Two compute identities, native PCI/state accounting and shared Neutron routing.
4. Skyline login/domain selection, patched assets including negotiated gzip,
   authenticated Swarm catalogs and unauthorized-request rejection.
5. A separately authorized three-node release Swarm: seed API readiness/CA,
   joins, domain publication, public UI and exact cleanup/accounting restoration.
6. A second simultaneous Swarm and neighbor-preserving deletion.
7. Actual fresh-OS and cold-cache installation, then addition of a bare prepared
   compute from a separate operator environment.

Do not call static HTTP checks a headed browser test. Do not call an empty-cloud
capacity check a guest launch. No new Swarm was launched in this checkpoint.

### Remaining infrastructure limits

- Ubuntu 26.04 remains an explicit experimental path for this pinned Kolla
  revision. Do not fake Ansible distro facts. Reuse existing supported Docker;
  separately verify Kolla's missing-Docker path on the target OS.
- One control/network/ingress host is still a single point of failure. Controller,
  database, message bus, ingress and external DNS failover need a separate HA
  design and failure test; two compute hosts do not provide control-plane HA.
- Host resource accounting and GUI capacity snapshots are currently scoped to
  the supported single-cell topology; do not claim arbitrary multi-cell support.
- Cleanup must remain target-scoped, preserve libvirt/QEMU and unrelated native
  services, and distinguish recoverable config backup from irreversible removal
  of OpenStack DB volumes and encrypted guest state.

## Evidence and recovery rules

The private run root is
`/var/lib/spvm-openstack-reinstall-20261021-01` on the hosts. The prior cleanup
backup is `/var/backups/spvm-openstack-cleanup-20261021`. These are local operator
evidence, not portable input examples. Do not commit credentials, provider file
contents, raw service logs or database dumps.

Keep failed transactions, input hashes and backups. Review actual side effects
before generating a new plan. A failed precheck can be rerun in a new transaction
after proving it made no deployment changes; a partially executed deploy or
runtime enrollment must not be blindly replayed. Publish the installer and driver
as separately pinned artifacts, and report precisely which acceptance levels
passed for each pair.
