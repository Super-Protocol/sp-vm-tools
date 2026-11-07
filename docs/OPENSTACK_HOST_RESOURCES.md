# Manual compute resource lifecycle

The `manual-host-resource-lifecycle-v1` contract requires a matching installer,
Nova driver, privileged helper, executor, CLI and GUI. Old experimental bundles,
resource-bearing installation YAML and workspaces are rejected. There is no
compatibility wrapper, identifier conversion, resource migration or adoption.

## Installation is infrastructure, not resource allocation

Use `sp-vm-tools/scripts/spvm-openstack.sh install`, `add-compute` or
`remove-compute`. The former `install-openstack.sh` no longer exists.

Installation YAML contains addresses, SSH users, roles, interfaces, explicit
launch mode, network and pinned kit/Kolla inputs. A host-only add-compute YAML
contains only those hosts' infrastructure settings and optional debug opt-in.
Neither accepts `compute_resources`, `storage_pools`, `storage_discovery` or
`gpu_inventory`. Their presence is an error, not an instruction to import defaults.

Detection reports CPU/RAM, mounted filesystems and PCI devices. Detection never
allocates a CPU/RAM budget, creates a workload pool or enables a GPU. A CPU-only
compute is valid. A service-only controller has no VM launch target.

The technical registered-image cache is `/var/lib/spvm/registered-releases`,
independent of workload pools. Provisioning requires a writable filesystem and
at least 10 GiB free there; this minimum is not a promise that every release fits.
Registration and launch retain their own space checks. The cache may consume
root-disk space even when workload state uses a separate disk. Monitor it.
No installation operation partitions, formats or mounts a disk.

Nova's global configuration sets `enable_new_services = false`, including the
conductor which creates compute Service records. Compute starts healthy but
administratively disabled. Enrollment does not enable it. Native CPU/RAM totals
remain physical and positive, with `reserved == total` until first assignment;
no fictitious positive workload budget is advertised. Workload pools, GPU
registrations and SPVM readiness traits are absent until explicitly configured.

Final installation acceptance expects this deny-all state: healthy disabled
compute services, `UNCONFIGURED` protected catalogs, real CPU/RAM totals fully
reserved, no workload child providers, no enabled GPUs and no SPVM-ready traits.
Zero schedulable hosts and an empty flavor-candidate catalog are successful
initial outcomes, not reasons to enable a host or invent a resource budget.

## States and authority

| Resource status | Meaning |
| --- | --- |
| `UNCONFIGURED` | CPU/RAM not assigned, or no valid enabled state pool |
| `CONFIGURED_DISABLED` | Mandatory resources assigned, but no explicit Enable |
| `ENABLED` | Resources complete and administrator explicitly enabled admissions |

Technical health, Nova heartbeat, network readiness and TDX/SEV-SNP/non-CC launch
readiness are separate observations, not substitutes for administrator intent.
An inconsistent or interrupted state cannot be enabled.

`/var/lib/spvm/host-resources.json` is root-owned `0600` state, bound to the
compute UUID. It stores applied CPU/RAM limits, managed pool definitions, the
administrator flag, revision and pending transaction. GPU inventory remains in
the protected host GPU configuration. Installation workspaces preserve
infrastructure and provenance, **not current resource budgets**. Resource edits
do not rewrite deployment YAML or invalidate it by changing numeric baselines.

The driver checks protected helper admission before **both ordinary Nova and
SPVM spawns**. The helper independently checks SPVM execution admission. Tenant
metadata and a manual `openstack compute service set --enable` cannot replace
SPVM host Enable. The scheduler is an additional filter, not the security boundary.
Accepted launches have local identity-bound admission records. Disable rejects
new admissions without cancelling already accepted work or stopping guests.
An interrupted admission/configuration is retained for review, never replayed.

## First assignment in GUI

Open **Compute → Hypervisors → Host resource budgets**.

1. Read detected hardware and the host's status. Unassigned limits show
   **Not configured**, not zero-sized manual defaults or Automatic.
2. In **Change resource limits**, explicitly enter vCPU and RAM limits. Leave
   headroom for OS, QEMU, controllers and networking. This changes admission
   capacity, not CPU affinity or a host cgroup guarantee.
3. In **Manage storage pools**, add an eligible mounted filesystem, choose a
   new empty directory and explicit positive budget/reserve in GiB, then apply.
   Several independent filesystems can be separate pools; one state disk must
   fit one pool. Two directories on the same filesystem do not double capacity.
4. Optionally configure GPUs. All detected devices start disabled. GPU model
   names are descriptive; PCI identity, isolation and whole-group constraints
   remain authoritative. Display-name-only changes do not disable the host.
5. Refresh and use **Enable host**. It verifies resource completeness,
   configuration, Nova/Placement, helper, network and configured launch mode.
   Nova scheduling is enabled only after successful checks.

Enable is two-phase: a protected pending transaction continues rejecting launches
while Nova is enabled. Only the final durable host commit opens admission. A lost
Nova acknowledgement does not commit the host flag; a lost final host response
can be confirmed by read-only request-bound evidence, never by replaying Enable.

Every CPU/RAM, pool or technical GPU change requires an empty host, across all
projects: stopped/ERROR instances, domains, allocations, migrations, claims,
reservations, pending launches and GPU leases count. Increases are not exempt.
After a successful change the host stays disabled until another explicit Enable.
GPU is optional; CPU/RAM and an enabled state pool are mandatory even for ordinary
Nova VM admission. Project quotas are separate and are not changed automatically.

Root (`/`, not `/root`) is permitted only by explicit administrator pool choice.
The GUI displays a warning, including the root-pool icon tooltip. No redundant
consent checkbox is needed after choosing/enabling the root pool. Through the
typed pool API, root use is represented by `allow_root: true`.

`reserve_gib` is headroom used by the capacity calculation, **not a partition,
filesystem reservation or OS quota**. Budget plus reserve must fit currently
free space when applied. Later external OS/cache writes can reduce capacity
below the configured budget and block new state allocations. Existing guests
are not evicted, but exhausting the filesystem can still damage their operation.
Remote, temporary, read-only, bind/subtree and duplicate filesystem presentations
are excluded. Mounting/formatting, data movement, pool aggregation, MIG and shared
GPU memory are not provided.

## CLI and typed API

Use a matching OpenStackClient plugin and an administrator API context. Trusted
publisher/worker user IDs come from the installed delegation, not caller guesses.
`PUBLISHER_ID` is the separate dispatcher in `reader_user_ids` of the controller's
root-private `/etc/spvm/host-budget-policy.json`; use the same ID for `WORKER_ID`.
These identifiers are not passwords. Do not print the credential file or OpenRC.

```bash
openstack spvm host status --publisher-user-id "$PUBLISHER_ID" -f json
openstack spvm host budget list --publisher-user-id "$PUBLISHER_ID" -f json

# Use the current budget catalog's generation. No --automatic or mode field.
openstack spvm host budget set compute1 --vcpus 8 --memory-mb 32768 \
  --generation "$BUDGET_GENERATION" --publisher-user-id "$PUBLISHER_ID" \
  --worker-user-id "$WORKER_ID" --apply

openstack spvm host pool list --publisher-user-id "$PUBLISHER_ID" -f json
# Write the COMPLETE desired pool list to pools.json using the catalog's
# filesystem_id, explicit paths, budget_gib, reserve_gib, enabled and allow_root.
openstack spvm host pool set compute1 --file pools.json \
  --generation "$POOL_GENERATION" --publisher-user-id "$PUBLISHER_ID" \
  --worker-user-id "$WORKER_ID" --apply

# Refresh AFTER resource changes; lifecycle has its own generation.
openstack spvm host status compute1 --publisher-user-id "$PUBLISHER_ID" -f json
openstack spvm host enable compute1 --generation "$LIFECYCLE_GENERATION" \
  --publisher-user-id "$PUBLISHER_ID" --worker-user-id "$WORKER_ID" --apply
openstack spvm host status --request-id "$REQUEST_ID" \
  --publisher-user-id "$PUBLISHER_ID" -f json

# Disable also works on an occupied configured host; no guest is stopped.
openstack spvm host disable compute1 --generation "$LIFECYCLE_GENERATION" \
  --publisher-user-id "$PUBLISHER_ID" --worker-user-id "$WORKER_ID" --apply
```

Without `--apply`, mutation commands preview rather than submit. A budget edit
on a host with existing enabled pools includes each existing pool's limits with
`--pool ID:BUDGET_GIB:RESERVE_GIB`; pool creation/removal uses `host pool set`.
Every request has a UUID. Keep it and inspect its result after timeout; never
invent a replacement ID to repeat an uncertain operation. Reusing an ID with
different intent is refused. There is no arbitrary remote command endpoint.

`GET /v1/host-lifecycle` returns administrator-scoped host catalogs. POST accepts
exactly `request_id`, `host`, `generation`, `enabled` (a JSON boolean).
`GET /v1/host-lifecycle/<request-id>` reads the result. Existing budget, pool and
GPU APIs remain typed; budget bodies contain `vcpus`, `memory_mb`, `pools` only.
GUI paths are exposed under the existing same-origin `/spvm-api` proxy. No new
public port, runtime SSH credential or CC downgrade is introduced.

## Failures and operations

The root coordinator verifies current administrator authority and serializes
host operations. The compute's distinct executor independently verifies command
creator, scope, binding, expiry and durable request journal. See
[Host control security](https://github.com/Super-Protocol/swarm-cloud/blob/feature/spvm-bare-metal-cloud/examples/test-cluster/bare-metal-spvm/deployment/HOST_CONTROL.md). Installing GUI assets alone does not
activate this management channel or the new host contract.

On an uncertain mutation, keep scheduling disabled and inspect
`/var/lib/spvm-host-budget-worker/<request-id>` on the controller, the executor
journal and the host's `/var/backups/spvm-host-*` evidence. Pending protected
transactions and review-required journals block Enable. No automatic retry,
rollback, migration, guest restart, failed-node recovery or resource reclamation
is performed. Preserve evidence for explicitly reviewed manual recovery.

Disable retains configured inventories and running guests; it is not removal or
an evacuation. Removing/re-adding an empty compute installs it **UNCONFIGURED**
again; previous budgets are not restored from the installation workspace.
Retired pool directories and their data are not adopted or deleted. Use an empty
directory or perform separately reviewed administrator data handling.

An arbitrary external Kolla reconfigure is not a resource-setting API. Old
installation overrides reserve all CPU/RAM and are not current budget backups.
Reapplying them can create a protected/native configuration mismatch; admission
then fails closed rather than silently restoring installation defaults. Use the
typed resource APIs. A controlled in-place upgrade that preserves live host
state is a separate workflow, not a promise made by this fresh installer.
