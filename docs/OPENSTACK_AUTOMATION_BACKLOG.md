# OpenStack installer: cleared-host deployment and remaining automation

Checkpoint: 2026-10-21. This is an implementation and acceptance backlog, not a
claim that installation is unattended or that a fresh operating system was tested.
See [the deployment guide](OPENSTACK_DEPLOYMENT.md) for command schemas and safety
boundaries. Repository documentation must remain in English.

## Intended operator experience

1. Prepare confidential-computing hardware separately using the existing
   bootstrap scripts, firmware/BIOS configuration and required reboots.
2. Run the OpenStack installer from an operator/controller environment. Select
   initial control-plane deployment or addition of compute hosts explicitly.
3. Select CPU/RAM reserves, state storage devices/mounts and budgets, and allowed
   GPUs. Discover actual hardware; never infer that all hardware is donated.
4. Review one deployment plan, then apply it. The coordinator should drive all
   supported preparation, deployment, project enrollment and GUI stages.
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
| Installer snapshot | `fbad0699a298bfa9dd5ba3e401139c9949638483` |
| Driver source | `23ad534f64209a4ff5779315a9a2dea12b1b0431`, package 0.2.22 |
| GitHub release channel | `openstack-feature-spvm-bare-metal-cloud-1325095077dd` |
| Driver archive SHA256 | `7a29a11ef9f365ef4f395600dd5b01ef6ca3783c6befa0bb90d8fb5b8dbf42e6` |
| Nova base | `quay.io/openstack.kolla/nova-compute@sha256:f5aadb8b42f653403b4db2a4b2b68fafe132b9f57191c318237736df4df7ebae` |
| Skyline base | `quay.io/openstack.kolla/skyline-console@sha256:2ce16dc6bb5bf70fd17bb36248c19c83216da3dfddf870e97e045cbc9f4054d5` |
| Built Nova image ID | `sha256:1776e534de881dd584eacc798c2886892f7a10db9d59bead301919a832bbe928` |
| Built Skyline image ID | `sha256:0c26fdc949424571ea8f0ffc1c71b103def1e13552542fc7e69eb9e95591ad2f` |
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
- An operator still assembles inputs and invokes individual phases. Some lab
  preparation and recovery glue exists only in private transaction scripts.
  These are evidence of missing product orchestration, not the supported public
  deployment interface. Do not copy a lab script with fixed UUIDs into production.

No existing encrypted guest disk or previous Swarm CA was restored. Guest state
uses ephemeral encryption keys; stopped guests cannot be treated as resumable.
Do not restart/bootstrap an existing Swarm to test installer idempotency.

## Priority 1: finish supported end-to-end orchestration

### Latest combined-path live checkpoint

The follow-up cleared-host run completed Kolla and API-side network/resources
through the new coordinator. Scoped recovery then reached strict-TLS API/GUI
acceptance with both computes enabled/up, one template/image, two profiles, native
capacity and nine static probes, without creating a guest. The original parent
did not complete uninterrupted; its failed journals were retained, not relabelled
as successful. A local publication-worker overlay was separately hash-recorded.

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
Next acceptance requirement: build/select an updated driver bundle and repeat one
uninterrupted cleared-host common-workflow run. The current published 0.2.22
artifact is not sufficient for the separate-account path. Also retain the broader
artifact/bootstrap preparation boundaries below; this is not OS-to-platform proof.

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
