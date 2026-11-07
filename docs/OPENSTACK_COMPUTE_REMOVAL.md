# Retiring an OpenStack compute host

Run `remove-compute` from a matching prepared **management host**, using
`--controller USER@IP` and the controller-owned management kit.
You do not log into the controller manually. Operator SSH from management runs
the required fixed controller/compute operations; no operator private key or
SSH agent is distributed to servers. Necessary Kolla secrets are
downloaded into the management host's private working directory; treat it as
secret-bearing. No new SSH account, listener or persistent SSH authority is installed.

This is not Nova's service-disable command: it retires native registrations,
SPVM catalogs and deployment inventory. Temporary `compute service set --disable`
only prevents normal scheduling of new VMs; it does not remove/reinstall the
host, free its running workloads or prepare it for fresh enrollment.

For temporary maintenance only, from an already authenticated OpenStack CLI:

```bash
openstack compute service set --disable --disable-reason 'Administrator maintenance' compute03 nova-compute
openstack compute service list --service nova-compute
openstack compute service set --enable compute03 nova-compute
```

Here `compute03` is the Nova service hostname, unlike retirement's `--host`,
which is the deployment config key. These commands do not stop running guests.

## Preconditions and safety boundaries

Only an explicitly selected, reachable, delegated **compute-only** host is
supported. Controller, network and ingress hosts cannot be retired by this
workflow. An unreachable host requires a separate fencing/recovery procedure;
there is no `force` option.

The host must be empty across every project. Stopped, paused and ERROR instances
also count. Unfinished builds/tasks or migrations, Placement allocations, PCI
claims, libvirt domains, direct hypervisor processes, state files/reservations
and GPU leases block retirement. Observation failures are blockers, never proof
of emptiness. No VM is stopped, migrated, evacuated, rebuilt or deleted.

Planning is read-only apart from private local evidence and transient locks.
Both commands serialize with the controller's host-resource worker. They may
wait up to 120 seconds for that lock without stopping the worker or its timer.
The plan expires after 15 minutes; source/configuration/identity drift requires
a new plan. Use a new private output directory for each plan.

## Commands

Use administrative controller SSH, the matching READY prepared environment and
a new private output directory. The authoritative kit supplies infrastructure,
component pins and removal provenance; no deployment YAML or local installation
history is required. See [controller management](OPENSTACK_CLOUD_MANAGEMENT.md).
For example, from the `sp-vm-tools` checkout on the management host:

```bash
sudo bash scripts/spvm-openstack.sh remove-compute \
  --controller operator@192.0.2.10 \
  --environment /srv/private/environment-01 \
  --host compute03 \
  --output /var/lib/spvm/remove-compute03-01 --check

# Review the check, then apply after fresh checks in a new output directory.
sudo bash scripts/spvm-openstack.sh remove-compute \
  --controller operator@192.0.2.10 \
  --environment /srv/private/environment-01 \
  --host compute03 \
  --output /var/lib/spvm/remove-compute03-02 --yes
```

The first command is optional: the second invocation plans, validates and applies
in one call. Without `--yes`, type the exact host
configuration key to approve; this is never inferred from a previous failed run.
`--identity-file` and `--known-hosts` have the same management-local meaning and
invoking-user defaults as installation. Do not forward an agent or copy the key
to the controller. `--check` does not stop containers or remove registrations;
it creates private local evidence and holds only temporary runtime locks.

The initial supported scope is a completed single-controller installation whose
active configuration matches the completed controller generation, except for
local transport/path binding. Infrastructure/identity drift is refused, not
silently imported. Current budgets are read from protected host state. An incomplete installation or
partially removed host requires review. The command never deploys Kolla or
regenerates passwords/TLS to make removal pass.

`--host` is the deployment configuration key, not an Azure resource name or an
IP address. Review `remove-compute-plan.json` before applying: native hostname,
machine/compute identities, container IDs, providers, cell/chassis identity,
catalog IDs, peer policies and source hashes are bound to approval. Plans and
observations are private and must not be committed.

## What apply changes

1. Validate a successor workspace before remote mutation; preserve credentials
   and TLS certificates, without regeneration.
2. Disable scheduling and recheck emptiness. Remove only the target's peer
   permissions and exact hostname mapping while its OVN topology is still present
   (see step 4 for scope/backups), then stop/disable the target's
   reviewed Kolla compute/network support containers and SPVM helper services.
   Disable container restart policies so Docker cannot re-register the host.
3. Remove the host mapping using `nova-manage cell_v2 delete_host`, delete the
   exact Nova compute service, and reconcile only its reviewed empty Placement
   tree. Remove its hostname-bound OVN chassis and reload scheduler mappings
   using SIGHUP. No SQL mutations or controller container restart is used.
4. The peer-network phase in step 2 removes only the target's addresses from owned Geneve/NAT probe allowlists and
   its exact management hostname from peer `/etc/hosts`, preserving unrelated
   rules and aliases. Network backups and journals are retained.
5. Remove resource-worker delegation and update the active deployment config.
   Delete only the target's budget/pool/GPU catalog records in the delegated
   project. Shared image/flavor/template/project data, profile names and request
   history are preserved.
6. Verify native registration removal, stopped target services, empty state and
   unchanged peer identities/domains. Return `COMPUTE_REMOVED` and the successor
   workspace path. The old workspace is marked superseded and is no longer
   accepted by workspace validation; use the returned workspace for future work.

The management coordinator holds the existing controller resource-worker lock
over an ephemeral, keepalive-protected operator SSH channel. Controller operations
verify the lock process identity/start time and descriptor; losing the channel
blocks further work. A controller-local retirement claim is durable before the
first remote mutation, alongside the management WAL. Interrupted claims block
another retirement until reviewed; there is no cross-controller failover/replay.
Native policy/config updates use native paths, while the successor workspace and
`remaining-deployment.yaml` stay on management. Native credentials are retained
and never exported. Catalog/capacity refresh uses the installed controller worker.

The command does **not** uninstall Docker, QEMU, libvirt, bootstrap packages,
delete state directories/cache, rebind GPUs, change BIOS, or delete the machine.
It does not automatically dismantle external/shared management transports such
as WireGuard. Retire a dedicated transport peer separately only after proving
it is not used by another host or cloud.

## Failure and repeated execution

To add this or another compute later, use the unified
[`add-compute` command](OPENSTACK_COMPUTE_ADDITION.md), not a fresh cloud install
or manually re-enabling retired containers. Proven owned leftovers are archived
and current configuration is regenerated after approval; guest data and ambiguous
history still block the operation.

After quarantine and the empty-host guard, remove only the target's peer
permissions **before** stopping its OVN services and deleting its chassis. OVN
can remove the last `genev_sys_6081` device and associated addresses/routes on
surviving hosts when the remote chassis disappears. That expected teardown must
not invalidate a peer allowlist comparison against the pre-retirement topology.
Keep the exact peer snapshot checks; do not ignore link/route drift or refresh
an approved snapshot silently. A failed transaction that already completed
native removal needs an explicitly reviewed completion, not a new removal plan
or a replay of completed steps.

While the private controller claim is `REMOVING_COMPUTE`, subsequent resource
worker passes defer both administrator request processing and catalog sync under
the same lock. They do not wait for the retired executor, republish stale host
catalogs or attempt recovery. After explicit completion marks `COMPUTE_REMOVED`,
normal passes resume using the reduced policy. Invalid coordination records
fail closed. Existing active worker passes must finish before retirement can
acquire the lock; do not terminate a worker that is applying resource changes.

Planning (including `--check`) creates and validates a private `workspace-preview`
before reporting a removal plan. Prepared collections are copied without modifying
the original workspace, preserving executable permissions, empty directories and
content/provenance hashes; their receipt points to the new private copy. Apply
prepares and validates a separate `remaining-workspace` before any remote mutation.
A local preparation failure does not retire the host. Preserve the failed output
for review; after confirming an empty transaction journal (`done: []`,
`inflight: null`) and no controller claim, use a new output directory for a new plan.

Controller retirement record validation is included in the fixed SSH operation
and uses only Python's standard library. Management-host installation/continuation
modules are not runtime dependencies of the installed controller; no installer
module is copied into it to satisfy imports.
Planning also executes a read-only controller preflight through the same native
operation code used to claim retirement, validating its imports and current
configuration without creating a claim.

Each external step is journaled before execution. Failure leaves `NEEDS_REVIEW`
with the last `inflight` step and safe diagnostic locations. There is no blind
resume or automatic scheduling re-enable after a partially applied operation.
Inspect Nova state, local service state, configuration backups and the private
journal before an explicit recovery. Configuration/enrollment changes from a
partial operation must be reconciled before attempting another deployment.

Reapplying the exact successfully completed operation returns its recorded
receipt with `recorded_completion: true`; it does not rerun remote deletion or
claim a new live audit. This still works after the physical machine is removed.
There is no compatibility conversion for old experimental workspaces or
identifiers.

## Deleting a cloud VM afterwards

Infrastructure deletion is deliberately separate from `remove-compute`.
Require a successful retirement receipt and verify that the compute is absent
from Nova, Placement, SPVM catalogs and the active inventory. Resolve the exact
cloud resource ID, not just the public IP or display name. Delete only that VM
and its exclusively attached disks, NIC and public IP after checking ownership.
Never delete a shared resource group, network, NSG, gateway or transport peer as
a shortcut. Detached disks and public IPs may continue billing if left behind.

## Reviewed completion acceptance (2026-11-06)

The interrupted `remove-super6-04` operation was completed only after explicit
operator approval, without replaying quarantine, service stop, executor revocation
or native Nova/Placement/OVN deletion. Before continuation, the original plan hash,
exact controller claim, policy/config/source hashes, validated successor workspace,
empty stopped target and removed native registrations were checked again. The
re-approved peer snapshot differed only in the diagnosed last-OVN-tunnel topology
hashes (links, addresses and routes); policy files, firewall state, machine identity
and hostname mappings were unchanged. Original failure evidence was retained.

The final receipt is `COMPUTE_REMOVED`, with no inflight step and
`guests_changed: false`. Its private `reviewed-completion-01` directory preserves
the prior journal and the explicitly reviewed network snapshot. The remaining
policy/config contains only `super7`. Its Nova service and resource timer are
active, and a normal resource worker pass completed after retirement.

The future-operation fixes are covered by 25 targeted synthetic tests: removal
coordination/order and prepared workspace handling (15), strict peer networking
(7), and unfinished-retirement worker deferral (3). The older, broader budget
fixture suite is not fully passing: six failures/errors reproduce against the
pre-change module too (stale activation/dispatcher fixtures and missing test import
paths). Do not describe this result as a fully passing budget suite or as a fresh
end-to-end run of the reordered removal. Worker deferral is a source-level fix for
the next matching installer/tool deployment; the installed worker was restored
using the completed, reduced policy, not manually patched in place.
