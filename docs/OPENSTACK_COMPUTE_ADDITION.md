# Add or re-add computes from the management host

Use the same command for one or several new servers, previously retired servers,
or already connected ready servers. Do not choose a `fresh`/`returning` mode or
assemble enrollment requests, image recipes and workspace hashes manually.

The input is a **host-only YAML**, not the fresh-install deployment YAML:

```yaml
hosts:
  compute02:
    address: 192.0.2.20
    user: operator
    inventory_name: compute02
    launch_mode: tdx
    management_interface: mgmt0
    tunnel_interface: mgmt0
  compute03:
    address: 192.0.2.21
    user: operator
    inventory_name: compute03
    launch_mode: tdx
    management_interface: mgmt0
    tunnel_interface: mgmt0
```

`roles` defaults to `[compute]`; other roles are refused. Omit `--host` to process
every entry in YAML order, or use `--host compute02` to select just that entry.
For explicit debug opt-in, a host entry may contain `allow_debug: true`; its
default is **false**, even if other computes permit debug. An `untrusted` entry
without this explicit opt-in fails before any host is contacted. `launch_mode` remains
explicit and there is no CC-to-untrusted fallback. The YAML root accepts only
`hosts`; duplicate keys and alias/address collisions are errors. Fresh-install
cloud configuration is not accepted as a compatibility input.

```bash
sudo bash scripts/spvm-openstack.sh add-compute \
  --config /srv/private/computes.yaml \
  --environment /srv/private/installer-environment \
  --installation /var/lib/spvm-install/run-01 \
  --output /var/lib/spvm-install/add-computes-01 \
  --identity-file /srv/private/operator-key \
  --check
```

`--check` performs read-only host/cloud inspection and writes a private local
plan; it does not reconcile metadata, install packages, start containers, create
identities or enroll the server. To apply, invoke the same command with a **new**
output directory, omit `--check`, and confirm the deployment name or supply `--yes`.
The command computes and revalidates its internal approval hashes. `--yes` skips
only the interactive prompt, never validation. Existing or partially attempted
outputs are not overwritten or automatically replayed.

`--identity-file` and `--known-hosts` are optional management-host SSH overrides,
with the same invoking-user defaults as installation/removal. SSH trust must
already exist; there is no trust-on-first-use or copying the operator key/agent
to a controller. Ubuntu 26 requires `--allow-experimental-ubuntu26` explicitly.
This command does not make Ubuntu 26 upstream-supported.

## Inputs and version selection

The YAML describes **only the selected computes**: address/user, bootstrap launch mode, management/tunnel
interfaces and launch mode. Resource fields are rejected. The new host is
UNCONFIGURED and disabled; assign resources and explicitly Enable it afterward.
See [manual resources](OPENSTACK_HOST_RESOURCES.md).

The original `--installation` supplies cloud/network/Kolla/image settings, API
CA and password store. The management kit is selected only from manifest hashes
in the completed installation receipt, matching its recorded controller commit.
The guest CIDR and API region come from saved network/binding receipts. Missing,
changed or ambiguous kits are refused; no directory search, latest download or
driver upgrade is performed. Keep these local installation artifacts available.

## Batch transaction and failure behavior

All selected hosts undergo read-only preflight before approval or the first
mutating host stage. Duplicate physical/native identities are refused even if
addresses differ. Each target is rechecked under the controller resource lock
immediately before its apply. The local installation lock serializes the entire
batch. Cloud lineage may advance only through this batch's completed children;
unrelated maintenance/configuration drift is refused, not automatically rebased.

Hosts are committed **sequentially**, not atomically. On the first failure, stop:
earlier successful hosts remain in the cloud and later hosts are untouched.
There is no rollback or automatic replay. `batch.json` records each result and
the failing host, with `failure.json` containing sanitized frame locations.
Per-host evidence is under `hosts/<alias>/scan` and `hosts/<alias>/apply`; the
latter retains the single-host WAL, backups and workspace. A completed child
updates the same verified lineage used by subsequent adds/removals. Parent
failure cannot invalidate a successfully committed child.

The preflight summary is `ADD_COMPUTE_BATCH_PLAN_READY`; successful application
returns `COMPUTES_ADDED`. Child results are `COMPUTE_ADDED` or
`ALREADY_ADDED_VERIFIED`. A used/partial batch directory is never overwritten,
resumed or migrated. Review ambiguous mutations before any further operation.

Existing infrastructure and control-plane provenance are taken from the latest
**completed** workspace. Current budgets and GPU settings remain authoritative
on each protected host and are not restored from that workspace. The command follows explicit,
verified successful add/remove supersessions from the original installation;
unfinished transactions and configuration drift are not silently adopted.
The selected verified driver, Kolla revision and immutable compute image must
match the installed cloud kit. An old target is prepared with those current
cloud components, not with whatever it used previously. This is not a cloud
upgrade or a migration of old configuration formats. Upgrading the cloud kit
itself remains a separate controlled operation.

The verified management-kit location is separate from the sealed workspace's
driver intent. Selecting the controller-matched management kit does not rewrite
that historical field or relax the existing-host/global-intent comparison.

Keep the completed installation artifacts (launcher staging, compute image
package, bound workspace, certificates/password store and helper requests).
Missing or mismatched artifacts are a preflight error, not permission to build
an unrelated latest driver or regenerate cloud passwords/CA. The CLI does not
select guest images/releases, create flavors/templates/Swarm clusters or change
existing release enrollment. The new helper receives the existing secret-free
runtime enrollment, filtered by its explicit launch/debug policy.

## State-based behavior

| Current state | Result |
| --- | --- |
| New, bootstrapped, empty, no native registration | Prepare only this compute, deploy, enroll and enable it |
| Previously used, empty, absent from native inventory, proven owned metadata | Archive owned metadata; regenerate current configuration and use the same preparation/deploy/enroll path |
| Registered and exactly ready for the active configuration | Read-only `ALREADY_ADDED_VERIFIED`, including occupied ready hosts; no restart or cleanup |
| Domains, guest processes, state disks/claims, allocations or migrations | Refuse without stopping/deleting a VM or releasing its reservations |
| Foreign/unproven containers or configuration; partial native registration; unfinished operation | Refuse with diagnostic evidence; require explicit review |

A missing registered compute still present in the active installation is not
automatically recreated. Explicitly retire/review its identity and workload
reservations first. `add-compute` is an administrator action, never a timer-based
repair/recovery mechanism for failed VMs, computes or whole hosts.

## Returning hosts and backups

Ownership requires matching physical identity, kernel hostname/native compute
UUID, and either a completed hash-bound retirement receipt for that machine or
the installed helper/runtime's binding to this cloud/project. A familiar
container name alone is insufficient. Known stopped container identities from
the retirement receipt may be older than the active image; they are replaced,
not treated as compatible merely because their names match.

After approval, stop only owned helper/executor and reviewed compute Kolla
services. Remove the stopped owned containers without force or volume deletion.
Rename proven owned configuration/bookkeeping into sibling
`.spvm-retired-<operation>` backups; save a private remote reconciliation journal
under `/var/lib/spvm-compute-reconcile/<operation>/transaction.json`. Empty
state-pool markers can be archived with their directory. VM disk files, nonempty
claims or unknown nonempty workload history block reconciliation.

The owned compute-only `/etc/kolla` tree and stopped OVS runtime directory are
included in reconciliation. Only the reviewed compute service directories and
OVS socket/PID names are accepted. Before archiving OVS runtime, require no OVS
process and no live UNIX endpoint in the kernel table; a socket file alone does
not prove a running daemon. Foreign entries or active OVS are refused. Docker
volumes are not deleted. Historical failure journals remain evidence, not an
instruction to rerun completed preparation.
The exact `/etc/kolla` directory may use Kolla's root:root `0770` mode;
other writable groups, world access with group write, and writable ancestors
remain refused. Archived directory roots are tightened to `0700`.

Backups are retained, can contain old private executor credentials, and must
remain protected. Removing/reclaiming them is separate administrator maintenance.
Do not publish their contents. No disk formatting/partition changes, bootstrap,
QEMU/libvirt replacement/restart, CC mode changes, GPU binding changes, host
reboot or removal of unrelated containers is performed. Mounted/bind-mounted,
symlinked, oversized or otherwise ambiguous managed state is not automatically
archived. Unknown historical schemas may require an explicit ownership/data
review; the command never adds backward-compatibility parsers or guesses that
unknown data is disposable.

## Scoped pipeline and credentials

Preparation is target-only: storage directories, explicit GPU discovery, matched
launcher/helper, packages/Docker preservation, public API CA, target networking
and delivery of the exact current compute image. Existing peers receive only
the selected host's additive owned allowlist/hostname entries. Scoped Kolla deploy
uses the single literal compute alias and preserves the existing password store;
there is no global deploy/reconfigure or control-plane/container restart.

Native registration starts disabled. Existing guarded cell mapping and SPVM
enrollment/acceptance are reused. A new restricted per-host Keystone executor is
provisioned; disabled old identities are not silently adopted/reactivated. The
current dispatcher and its credentials remain on the controller. New per-host
credentials travel only in memory over verified operator SSH stdin/stdout to the
target's protected credential file; never CLI arguments, YAML, plan/results or
terminal output. Secret-bearing provisioning failures expose only safe types/
frame locations. No Unix service account/SSH key is created on the controller
for host administration and no runtime SSH fallback is added.

After acceptance, add only this host's delegation to native policy/configuration,
refresh the host catalogs, complete the controller claim, and supersede the old
workspace. `COMPUTE_ADDED` identifies the new resolved deployment/workspace.
Keep using the original installation root: future add/remove resolves the latest
completed workspace. Serialize maintenance with scheduling and other installer
operations; unexpected workload/controller/network drift fails closed.

Each mutating stage has a private write-ahead journal. Ambiguous failure leaves
`NEEDS_REVIEW` and a persistent addition claim; it is not replayed from a new
directory. If native registration has been reached, the coordinator attempts
to disable only future scheduling on this target, never stop guests. A lost
quarantine acknowledgement requires manual review, not a success claim.

## Verification boundary

Synthetic tests exercise classification, workload/ownership refusals, metadata
backup preservation (including retained Kolla configuration and dead OVS socket
files), common new/returning orchestration, already-added NOOP, interrupted child
phases and additive controller commit. The 21-test synthetic suite also executes
the generated executor-install call, ensuring request data cannot shadow the
installer's `POLICY` path constant.

On 2026-11-06 the retired CC compute `super6` was returned to the existing cloud:
`add-super6-01/transaction.json` reached `COMPUTE_ADDED`; scoped Kolla deploy,
enrollment, acceptance, scheduling enablement, additive native configuration and
catalog refresh completed. Controller container lifecycle was preserved and no
guest was changed/restarted. The original management installation root remains
`run-05`, with the completed lineage pointing to `add-super6-01/added-workspace`.
An independent ordinary `add-compute --check` using the original installation
root then succeeded: classification `registered`, ready plan `mode: NOOP` and
`status: NOOP_READY` (`add-super6-verify-01`). It did not redeploy the target.

This was **reviewed recovery**, not an uninterrupted CLI acceptance. The first
attempts exposed missing coordinator state, accidental replacement of sealed
driver intent, omitted Kolla/OVS remnants and a request-variable collision in
executor installation. These were fixed with regression coverage. Completed
storage/GPU and other preparation stages were not replayed. The administrator
explicitly authorized deleting only the two proven inactive remnant directories
`/etc/kolla` and `/run/openvswitch` instead of archiving them for this test; no
Docker volume was deleted. The unused executor identity created before the
failed install was explicitly disabled, and a distinct restricted identity was
provisioned for completion. This is not an automatic credential/transaction
repair mechanism. Evidence remains in the private `reviewed-completion-*`
directories and child journals.

A real fresh-host run and an uninterrupted returning-host apply were separate
acceptance cases after that recovery; mocked orchestration is not evidence for them.
Older compute-workspace fixtures currently
need updates for automatic collections and the current helper schema; their
initialization errors are not evidence of a passing end-to-end suite.

On 2026-11-07 the host-only batch frontend was checked with 13 synthetic tests:
all-host preflight before apply, sequence/lineage updates, partial failure,
duplicate physical/IP identities, target/input drift, registered NOOP, strict
YAML and CLI check-only/existing-output refusal. The 21 single-host safety tests
also passed. Using the real saved installation, `super6.yaml` was locally merged
with the active cloud and the receipt-selected `50009f21` management kit, with
network connections and SSH subprocesses explicitly forbidden.

Later on 2026-11-07 the operator successfully completed the uninterrupted return
of `super6` after `remove-super6-05`, using the ordinary host-only batch frontend.
`add-super6-batch-01/batch.json` confirms `COMPUTES_ADDED`, one completed host,
no inflight operation and `guests_changed: false`; the child
`hosts/super6/apply` returned `COMPUTE_ADDED` with classification `returning`.
All stages completed without manual recovery. This covers one returning host
through the batch frontend, not an actual multi-host batch or a fresh-host run;
those live acceptance cases remain separate.
