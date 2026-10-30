# Retiring an OpenStack compute host

The installer provides `plan-remove-compute` and `remove-compute`. Run them as
root on the configured controller using the matching installer and driver
sources. This is not Nova's service-disable command: it retires the host's
native registrations, SPVM catalogs and deployment inventory.

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

Use the same root-owned host-budget policy and bound workspace as the active
installation. The SDK interpreter must have the OpenStack dependencies.
For example, from the `sp-vm-tools` checkout:

```bash
sudo /opt/spvm-openstack-client/bin/python \
  scripts/openstack/openstack_installer.py \
  --driver-dir /srv/swarm-cloud -- \
  plan-remove-compute \
  --policy /etc/spvm/host-budget-policy.json \
  --workspace /var/lib/spvm/kolla-bound \
  --host compute03 \
  --output /var/lib/spvm/remove-compute03-01

sudo /opt/spvm-openstack-client/bin/python \
  scripts/openstack/openstack_installer.py \
  --driver-dir /srv/swarm-cloud -- \
  remove-compute \
  --policy /etc/spvm/host-budget-policy.json \
  --workspace /var/lib/spvm/kolla-bound \
  --host compute03 --confirm-host compute03 \
  --plan-sha256 PLAN_SHA256 \
  --output /var/lib/spvm/remove-compute03-01
```

`--host` is the deployment configuration key, not an Azure resource name or an
IP address. Review `remove-compute-plan.json` before applying: native hostname,
machine/compute identities, container IDs, providers, cell/chassis identity,
catalog IDs, peer policies and source hashes are bound to approval. Plans and
observations are private and must not be committed.

## What apply changes

1. Validate a successor workspace before remote mutation; preserve credentials
   and TLS certificates, without regeneration.
2. Disable scheduling and recheck emptiness, then stop/disable the target's
   reviewed Kolla compute/network support containers and SPVM helper services.
   Disable container restart policies so Docker cannot re-register the host.
3. Remove the host mapping using `nova-manage cell_v2 delete_host`, delete the
   exact Nova compute service, and reconcile only its reviewed empty Placement
   tree. Remove its hostname-bound OVN chassis and reload scheduler mappings
   using SIGHUP. No SQL mutations or controller container restart is used.
4. Remove only the target's addresses from owned Geneve/NAT probe allowlists and
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

The command does **not** uninstall Docker, QEMU, libvirt, bootstrap packages,
delete state directories/cache, rebind GPUs, change BIOS, or delete the machine.
It does not automatically dismantle external/shared management transports such
as WireGuard. Retire a dedicated transport peer separately only after proving
it is not used by another host or cloud.

## Failure and repeated execution

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
