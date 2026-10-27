# GPU profile preparation (development worktree)

This work is not yet a completed or deployed release. Use matching installer,
driver, helper and Skyline sources. No migration/adoption of former inventories
or existing helper installations is implemented.

## Initial preparation

Complete CC/libvirt bootstrap separately and resolve state storage. Before host
audit and helper/compute staging, explicitly prepare empty compute hosts:

```bash
sudo scripts/install-openstack.sh --driver-dir /opt/swarm-cloud -- \
  prepare-gpus --config /etc/spvm/deployment.yaml \
  --known-hosts /etc/spvm/known_hosts --output /var/lib/spvm/gpu-preparation-001
```

Use the resulting `deployment.resolved.yaml` for subsequent steps. Source YAML is
not overwritten. The output directory must be new; failed journals are retained,
never replayed automatically. This step currently remains an explicit prerequisite
instead of being silently inserted into an existing installation workflow.

The command uses pinned SSH host trust, independently refuses existing domains
and running QEMU processes, installs reviewed gpu-admin-tools commit
`94b22ff51cfa13137264c75cee7d8cb8aa47ac34` under
`/opt/nvidia/gpu-admin-tools`, then queries exact BDFs. No `nvidia-smi` is needed.
The query can access MMIO: it is not a live monitoring probe. It never resets a
GPU, binds a driver or changes CC/PPCIe mode. Initial checkout requires GitHub
access; existing dirty, differently pinned or unprotected checkouts are rejected.

Unknown model/memory/architecture/variant does not prevent standalone GPU
advertisement. Incomplete PCI identity, missing VFIO/IOMMU readiness or disabled
devices still block allocation. A zero/invalid memory reading is recorded as
unknown, never as a real capacity or a reason to invent one. Detected facts are kept
separate from administrator `overrides` (`model`, `memory_bytes`, `variant`,
`architecture`). Memory is in bytes. Initial corrections can be made in the
resolved inventory before enrollment. Rediscovery preserves labels/overrides
only for an unchanged PCI identity, not a replacement at the same BDF.

The helper template uses `gpus: "from-deployment"` and
`gpu_inventory: "from-deployment"`; identity staging resolves them. Direct helper
provisioning requires the complete resolved inventory and exact eligible BDF list.
CPU-only intent is explicit, never an instruction to attach every host GPU.
Native PCI Placement reporting and scheduling must both be enabled; do not treat
enabling them on an old occupied compute as an automatic compatibility migration.

## Names and later changes

Host/BDF labels are cosmetic. Identical cards named `H100` and `Hopper 100` share
one technical profile. Identity hashes only vendor/product/subsystem IDs and
whole-device type. Different subsystem IDs create distinct profiles, but model,
VRAM, architecture, PCIe/SXM and BDF addresses do not. Nova owns allocation.

Automatic shared names use a detected model only when all eligible cards across
delegated hosts agree and none has an unknown model. Conflicting or missing models
use the full PCI identity. Local labels and model overrides do not control that
name; an administrator can explicitly set a shared name. Defaults are recomputed,
not persisted as manual overrides, so host enumeration order does not select a name.

**A flavor guarantees a PCI class, not minimum VRAM or PCIe/SXM execution.**
Identical PCI identities may have different capacities. `Unknown` is not a promise
of 80 GB, and an administrator label such as `H100 80 GB` is not a scheduling
constraint. Characteristic filtering is not implemented. Allocation is whole-card;
no VRAM subdivision, sharing or MIG is added. GUI and CLI list actual per-device
characteristics separately from the shared profile.

The GUI flavor table displays the count and shared GPU profile name, not the
physical card assigned to a running VM. Nova selects a BDF at VM launch. A
flavor whose alias is absent from the current catalog is marked `GPU profile
unavailable`; no model is inferred and no old alias is translated automatically.
Refresh inventory or explicitly recreate obsolete flavors against current profiles.

The host budget page presents one shared **GPU profile name** and per-card
**Use for OpenStack** switches. There is no second local-name editor in the GUI;
optional local labels remain CLI-only metadata. Hardware characteristics are
read-only summaries; corrections live in collapsed **Advanced hardware settings**.
NVSwitch controls appear only for discovered groups. Explicit empty-host
rediscovery lives under collapsed **Hardware discovery**. CLI: `openstack spvm host gpu
list/show/set/discover/configure/status`. Mutation commands preview unless `--apply` is
specified and require explicit administrator delegation. Display-name changes
neither restart guests nor alter allocations.

If rediscovery changes technical identity/eligibility, scheduling remains disabled
for review. Inventory alone cannot update Nova aliases, helper registrations and
Placement. Use the explicit technical configuration transaction for that change;
do not silently enable scheduling after a changed discovery result.

## Experimental fabric boundary

The synthetic model accepts one full Hopper group per host: 8 matching SXM GPUs
and 4 NVIDIA 22a3 NVSwitch functions, confirmed PPCIe mode and explicit host
`experimental_nvswitch: true`. All devices must be VFIO-ready, with complete IOMMU
membership inside the group. One logical flavor unit is 12 native PCI claims.
Partial groups, sharing, MIG, arbitrary partitions and Blackwell fabric support
are not claimed. Keep opt-in disabled until exact physical hardware is validated.

Use the matching launcher recipe including `sp-vm-tools-nova-pci.patch`: only
Nova-assigned GPU/auxiliary PCI functions are attached, never all host companions.
Synthetic tests do not establish physical DMA/firmware/CC/fabric acceptance.

## Explicit technical changes

Open **GPU devices & profiles** to change availability, or expand a card's
**Advanced hardware settings** for optional corrections. Architecture and PCIe/SXM
are detected facts, not mandatory setup choices. Use **Apply GPU configuration**;
the empty-host checks and typed confirmation still apply.
The CLI equivalent accepts the `configuration` object from `host gpu show` as JSON:

```bash
openstack spvm host gpu configure COMPUTE_HOST --settings /secure/gpu-settings.json \
  --generation GENERATION --publisher-user-id "$WORKER_USER_ID" \
  --worker-user-id "$WORKER_USER_ID" --apply
```

Settings cover exactly discovered devices/groups: enablement, separate overrides,
and explicit experimental fabric confirmation. They cannot fabricate hardware.
Blank GUI override fields revert to detected values. CLI memory is in bytes;
GUI memory is in GiB. Incorrect manual characteristics remain the administrator's
responsibility. Physical CC/PPCIe configuration stays outside this operation.

The current implementation supports one delegated controller, not HA alias
propagation. Matching native PCI Placement modes must already be enabled. It
preflights every destination, proves emptiness across all projects/reservations,
disables target scheduling and stops only its compute/helper services. It backs
up and updates inventory, helper registrations, device specs, aliases and canonical
Kolla/deployment intent. Unrelated exact-BDF PCI specifications/aliases survive;
ambiguous masks or overlapping non-SPVM registrations are refused before writes.

Changed aliases cause controlled Nova API/scheduler/conductor restarts. This can
briefly interrupt API access cluster-wide, but never restarts running VM processes
or another host's compute. Semantically unchanged settings do not restart services.
Before scheduling is restored, the worker verifies exact BDF identity/availability,
complete Placement resource-class totals and repeated all-project empty-host proofs.
A previously disabled host remains disabled. Existing flavors are not rewritten:
descriptive corrections do not change technical identity, aliases or Placement
resource classes and do not require a new flavor. The empty-host restriction
still applies to characteristic edits. A physical PCI identity change needs a
matching new flavor; it is not an automatic rewrite of existing claims.

Failures are not automatically rolled back or replayed. The worker reasserts the
scheduling fence; a lost API acknowledgement explicitly requires manual confirmation
that the service is disabled. A completed durable outcome can be republished only
for the same intent, without repeating its mutation.

Private coordinator evidence: `/var/lib/spvm-host-budget-worker/<request-id>`.
Private host backups: `/var/backups/spvm-host-gpus/<request-id>/{compute,controller}`.
Intents list original paths/digests; numbered `.before` files follow sorted paths.
Controller backups contain credentials (0700 directories / 0600 files). Staged,
applied and service-progress records identify partial completion. Do not restore
files, clear review markers or re-enable scheduling until actual helper/native/
canonical/Placement consistency is verified on an empty host. No automatic adoption
or migration of an old/interrupted installation is attempted.

## Remaining acceptance

### Activation and experimental format replacement

Install one matching set of driver wheel/compute image, helper/model sources,
pinned launcher, web backend/GUI, worker and capacity collector. On empty hosts,
stage inventory/canonical intent and provision native PCI Placement modes before
activating the set; verify exact native BDF/resource-class registration and fresh
catalogs before restoring scheduling. A console update or a prepared image alone
does not activate GPU management. Technical lab activation and Nova control-service
restarts require separate explicit approval. CC/bootstrap and hypervisor setup
remain separate and are not changed by metadata corrections.
The bundle must advertise `gpu-pci-identity-v1`; worker activation requires the
matching PCI-only model API in the web environment. A matching package version
or the older generic GPU capability alone is insufficient.

No backward compatibility, old-ID aliases, conversion or automatic flavor
reassignment is supported. Explicitly review and retire obsolete experimental
GPU flavors, then create replacements using the current profiles. Do not change
flavor extra specs or instance PCI allocations to disguise an ID change. On a
partial failure retain quarantine/evidence for manual review, not migration or
automatic replay. This stage does not introduce characteristics-based scheduling.

First-time GPU preparation is still an explicit onboarding prerequisite. Complete
installer/workflow regression acceptance and physical GPU validation remain before
rollout. Synthetic race/failure and browser checks are not physical fabric acceptance.
The prior lab update installed observation-only GUI/backend and metadata; compute,
helper/launcher and PCI modes were not activated. This implementation does not
change that activation status or claim physical fabric acceptance.

Local evidence (2026-10-27): all 447 installer tests passed, including current GPU
inventory fixtures and synthetic native-configuration transactions. The matching
driver's current Nova set (646 tests), related deployment set (68 tests), pinned
launcher tests (21 tests) and Chromium mock-UI checks passed. Full local Nova
discovery still has 52 errors/one failure in 137 older-contract fixtures; the same
counts reproduce with HEAD resource/launch logic. No compatibility entrypoints
were restored to satisfy those fixtures. This is not a physical GPU rollout or
clean-host/fabric acceptance. Test files are not part of the staged changes.

PCI-only follow-up: all 447 installer tests and 655 tests in the current Nova set
passed with the previously documented older-contract exclusions. Related native
transaction/discovery/deployment tests and Chromium mock-API checks passed.
New coverage includes unknown VRAM without a fabricated capacity, stable PCI
identities/aliases/flavors after descriptive corrections, deterministic shared
names across hosts, CLI unknown fields, read-only free counts and preserved
experimental fabric restrictions. Current source snapshots and wheel are prepared
locally. This follow-up did not activate any lab runtime or change native PCI
settings; a separate approved activation is still necessary.

### Full native lab activation, 2026-10-27

The operator subsequently authorized full rollout on `.247` (controller/compute)
and `.245` (compute). Matching Nova/helper/launcher, web/GUI, administrator worker,
collector and operator CLI are now active. Native PCI Placement modes and the
current GPU alias were provisioned on empty hosts with backups and scheduling
fenced; Nova control/compute services were restarted, not QEMU/libvirt or CC
bootstrap. Scheduling is enabled again. Canonical configuration/image references
were persisted. The superseded observation-only timer is stopped and disabled.

Both hosts report one free H100 in the shared PCI-only profile
`gpu-03293e2feebd5bf23a657c57`, despite unknown VRAM. Nova device status and
Placement per-device inventories were verified with no overcommit/reservations.
The live GUI reports two free GPUs and permits selecting NVIDIA H100 when creating
a flavor. A temporary GPU flavor was created through the GUI, its generated alias
verified, then that exact test flavor was removed. Existing flavors were not
rewritten. No VMs or Swarm clusters were started; guest passthrough, multi-GPU/
NVSwitch and clean-host acceptance are still separate checks.

Active image tag suffix: `gpu-20261027-02`. Evidence/backups on both hosts:
`/var/lib/spvm-gpu-20261027-02`; controller web runtime:
`/srv/spvm-web-gpu-20261027-02`. See the matching driver's `GPU_PROFILES.md`
for exact image IDs and verification records. No commit/push/release was created.
