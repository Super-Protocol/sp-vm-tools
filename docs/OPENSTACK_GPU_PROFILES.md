# GPU profile preparation (development worktree)

This work is not yet a completed or deployed release. Use matching installer,
driver, helper and Skyline sources. No migration/adoption of former inventories
or existing helper installations is implemented.

## Initial detection and later configuration

Installation only detects hardware and leaves every GPU disabled. It does not
write resource inventory into deployment YAML. After installation, use the host
resource page or `openstack spvm host gpu` commands to discover metadata and
explicitly configure selected devices on an empty host. Then separately Enable
the host; technical GPU changes leave it disabled. CPU-only hosts are supported.
See [manual resources](OPENSTACK_HOST_RESOURCES.md).

Metadata probing is a separate explicit empty-host operation. The pinned query
tool may access MMIO; it is not a live monitoring probe and never resets devices,
changes bindings or switches CC/PPCIe mode. Unknown descriptive metadata does
not block a standalone whole GPU with valid PCI identity, VFIO and IOMMU.

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
Kolla overrides and protected host state, not deployment YAML. Unrelated exact-BDF PCI specifications/aliases survive;
ambiguous masks or overlapping non-SPVM registrations are refused before writes.

Changed aliases cause controlled Nova API/scheduler/conductor restarts. This can
briefly interrupt API access cluster-wide, but never restarts running VM processes
or another host's compute. Semantically unchanged settings do not restart services.
Before reporting success, the worker verifies exact BDF identity/availability,
complete Placement resource-class totals and repeated all-project empty-host proofs.
Every technical change leaves the host disabled until a separate Enable. Existing flavors are not rewritten:
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

## Activation prerequisites

Install one matching set of driver/compute image, helper/model sources, pinned
launcher, web backend/GUI, worker and capacity collector. On empty hosts, prepare
the disabled inventory and native PCI Placement modes before activation. Verify
exact BDF/resource-class registration and fresh catalogs before enabling GPU use
and separately enabling host scheduling. A console update alone does not activate
GPU management; hardware bootstrap and CC/PPCIe setup remain separate.

The kit must advertise `gpu-pci-identity-v1`; the web environment must expose the
matching PCI-only model API. An equal package version alone is insufficient.
Technical changes may restart the affected Nova services and need explicit
administrative approval. Retain quarantine and private journals on partial
failure; there is no automatic repair, rollback or replay.

Old-ID aliases, conversion and automatic flavor reassignment are unsupported.
Review and retire obsolete experimental GPU flavors explicitly, then create
replacements against the current PCI profiles. Do not edit existing allocations
or extra specs to disguise an identity change. Synthetic checks cannot establish
physical GPU isolation, DMA safety, CC readiness or NVSwitch fabric support.
