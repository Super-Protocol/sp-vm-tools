# Controller-owned cloud management: acceptance, 2026-11-08

## Delivered sources and CI artifact

- `sp-vm-tools`, branch `feature/openstack`: contract `d2924e3`, private-directory
  fix `2bc93d3`, scoped Git ownership checks `05da08c`, coordinated peer-network
  membership `f74daadf2de9b825f66045216cc3f5da6c34c86a`, and YAML configuration
  fix **`62b95a596363e229e282e54797737952119739c7`**. Sources were committed and
  pushed; the deployed kit records clean production source hashes.
- `swarm-cloud`, branch `feature/spvm-bare-metal-cloud`: contract `05f4d267`,
  driver **0.2.38**, commit **`e4b32c1d35e19e698ce5b12426b135e1d4e1857d`**.
- Successful [CI build](https://github.com/Super-Protocol/swarm-cloud/actions/runs/42420460572).
  Asset `nova-spvm-openstack-feature-spvm-bare-metal-cloud-1325095077dd.tar.gz`:
  SHA256 **`3d4103e43449fa4160104af641ede063b62ddfcf2690b14f8014580467b17ed9`**.
  The branch tag is rolling; the commit and digest identify this acceptance.
- Kolla 2026.2, commit `689533989dc27670e4fde6e0b313c5150b8efe7f`; Ubuntu
  26.04 hosts, explicit experimental-host and upstream test-image opt-ins.
  This is not certification of upstream test images for production.

The last two fixes are installer-side only; the matched 0.2.38 driver artifact
was not rebuilt or relabelled as containing changes to `sp-vm-tools`.

## Stand and manager isolation

The authorized CC hosts are super7 (`92.223.119.247`, controller/network/compute/
ingress) and super6 (`92.223.119.245`, compute). Managed OpenStack components
were cleared and a new single-controller cloud installed using the normal
`install` command, without public resource fields. Bootstrap, libvirt/QEMU,
installed OS/packages, Docker image cache and unrelated data were retained.
This was a cleared OpenStack stand, **not** a fresh OS or Clonezilla restore.
Azure/non-CC was not touched. Shared-interface management was explicitly
approved and remains unencrypted; no additional public opening was introduced.

The second-manager acceptance used an isolated local container, **not a second
physical computer**. It had the matching clean installer checkout, the existing
READY environment mounted read-only, operator SSH credentials/trust, a small
super6-only YAML and a new private operation directory. Original installation
directories and the original source checkout were absent inside the container.
Dependencies were actually verified, not freshly re-resolved on a separate PC.
SSH to the controller, target and existing compute remained necessary.
The mutation work directory retained earlier maintenance outputs, but not the
original installation history. A final independent read-only check used a
different, initially empty work directory with no earlier membership outputs.
It recovered generation 4 and verified super6 as registered; no state was
copied from the other manager's work directory.

The minimal input was:

```yaml
hosts:
  super6:
    inventory_name: super6-tdx-h100
    address: 92.223.119.245
    user: petr
    launch_mode: tdx
    allow_debug: true
    management_interface: bond0.2907
    tunnel_interface: bond0.2907
```

Cloud UUID: `39809a36-a08c-4d58-b0ee-67784d9ae403`. The controller stores the
authoritative generations under `/var/lib/spvm/cloud-management`; the old
management computer's `run-05` is not an input to either membership command.
Kolla passwords, API CA and TLS material are retained unchanged across generations.

## Live acceptance

| Check | Observed result |
| --- | --- |
| Normal cleared-stand installation | Completed with installer `f74daad`, driver 0.2.38; generation 1, super7 healthy but UNCONFIGURED/disabled |
| Wrong expected cloud UUID | Refused before hydration or host changes |
| Initial ordinary Nova requests | Auto, explicit host and manual Nova service enable plus forged metadata all failed; negative-test resources deleted |
| Explicit typed resource APIs | 8 vCPU / 32768 MiB, one 500 GiB state pool with 200 GiB reserve, then separate Enable on super7 |
| Positive ordinary guest | CirrOS booted to a login prompt; ACTIVE, QEMU PID `3332727` |
| New-host add from isolated manager | Preparation, scoped shared-interface peer updates, executor, compute deploy and enrollment passed; final controller configuration exposed the YAML defect below |
| Reviewed completion | Only previously unstarted configuration/catalog/finish/publication completed; generation 2 retained explicit review evidence |
| Normal controller-based removal | super6 removed successfully using installer `62b95a5`; generation 3; no original installation history required |
| Normal returning-host add | Unmodified `add-compute` with `62b95a5` completed; classification returning, generation 4, `guests_changed: false` |
| Existing running peer | Same super7 guest PID/start ticks and exact resource state across new add, removal and normal return |
| Controller services | All 36 container IDs, image IDs, start times and restart counts matched the pre-add baseline |
| Browser acceptance | Both resource cards and both native hypervisor detail pages passed, before and after QA cleanup; no JavaScript/Placement errors or false notifications; super6 Enable disabled |
| Cleanup | Exact QA guest, private image and flavor deleted through authenticated APIs; zero servers across all projects |
| Protected host data | 60 file hashes on super7 and 59 on super6, default routes and 34 Clonezilla file names/sizes/mtimes per host unchanged |
| Independent empty-manager preflight | Matching clean sources/READY environment plus controller SSH and the minimal YAML recovered generation 4 and produced a checked registered-host plan, without any local installation or earlier membership journals |
| Final protected state | Generation 4 COMMITTED; installer source `62b95a5`; CA preserved; no incomplete resource transactions, stale busy-host blockers or servers in any project |

## Defects and reviewed partial-add completion

Live testing found and fixed four independent defects:

1. Nested transferred directories could lose private permissions. All nested
   kit directories are now private and validated.
2. Root maintenance read a user-owned pinned Kolla checkout without scoped Git
   ownership trust. Frontend/backend reads now use exact per-command trust;
   neither global Git settings nor wildcard `safe.directory=*` are introduced.
3. Peer membership updated Geneve without the shared management allowlist and
   did not account for the NAT policy's corresponding peer list. Add/remove now
   coordinate only those owned entries in verified order, retaining unrelated
   rules, interfaces, routes, IPv6 guards and terminal drops.
4. Native add configuration tried to decode the fresh deployment YAML as JSON.
   It now uses the already parsed, hash-bound controller snapshot. No legacy
   configuration parser or migration was added.

The first new-host add stopped before any controller configuration write at
defect 4. Its original failed journal remains `NEEDS_REVIEW`, not rewritten as
successful. A separately recorded, one-off reviewed procedure verified the exact
claim, approved inputs, completed enrollment, empty target, unchanged credentials,
controller containers and running peer. It completed only unstarted phases and
published generation 2. The one changed installed frontend module was replaced
with the exact clean `62b95a5` source under the held lock, retaining the old file
privately. Root-owned review evidence is in the controller's `reviews` directory.
Completed preparation/deployment/enrollment phases were **not** replayed.

This is not a public retry/migration mechanism. A pristine full initial install
at `62b95a5` was not repeated; the clean normal install used `f74daad` and the
subsequent remove/return cycle exercises the final frontend. No claim of a
single uninterrupted initial-install/new-add run at the final revision is made.

## Focused synthetic results

Re-run for this handoff: controller management (29), add manager (22), batch add
(13), removal manager (13), and peer network (11): **88 tests passed**. Add
coverage executes generated configuration code against fresh YAML and rejects
stale hashes. Earlier focused onboarding/shared-interface/workspace and driver
integrity checks also passed. This is not a full repository-suite result.
Tests remain outside commits, as requested.

Multi-host batch ordering, concurrent operators, interruption/corrupt archives,
unsafe links and missing evidence have synthetic coverage; the live stand uses
one target at a time and one controller. It does not establish HA/failover,
controller-loss recovery, a freshly resolved environment on another physical PC,
multi-GPU/NVSwitch or a new Swarm/CC-attestation deployment. The positive guest
was an ordinary Nova CirrOS VM, not a measured SPVM guest.

## Final state and evidence

| Host | State | Explicit administrator resource settings |
| --- | --- | --- |
| super7 / `92.223.119.247` | ENABLED, technically ready, Nova enabled/up | 8 vCPU, 32 GiB RAM, `/data/spvm-state-cloud-v3-20261108`: 500 GiB budget / 200 GiB reserve; no GPU enabled |
| super6 / `92.223.119.245` | Re-added UNCONFIGURED, technically ready, Nova disabled/up | CPU/RAM unset; no workload pools or enabled GPUs; separate resource setup and Enable required |

These are explicit test choices, not automatic recommendations or install-time
baselines. The initial resource catalog after QA deletion briefly retained the
previous busy-host observation. Acceptance waits read-only for normal publication
refresh rather than bypassing the empty-host checks or manually clearing flags.
The Clonezilla comparison is names/size/mtime, **not** a new full-content hash
verification of large disk images. Disks were not formatted. No bootstrap,
libvirt/QEMU binaries/configuration, netplan or unrelated data were replaced.

Final read-only acceptance returned `CONTROLLER_CLOUD_MANAGEMENT_ACCEPTED`.
The independent empty-manager preflight returned `ADD_COMPUTE_BATCH_PLAN_READY`,
classification `registered`, without submitting changes. The old failed local
add journal remains preserved alongside its separate completion review; it is
not the current authority or an unresolved protected resource transaction.

UI: `https://92.223.119.247:9999`, **Compute → Hypervisors → Host resource
budgets**. Tests used a temporary loopback-only SSH SOCKS proxy to the controller;
the API/UI remain peer-restricted, not newly opened to the Internet. Use the
approved management/SSH access path. The browser accepted the lab HTTPS
certificate for this test; SDK/API checks used the installed CA. Credentials
remain in protected installed OpenStack auth files and are not included here.

Private operation evidence is under
`/var/lib/spvm-cloud-test-20261107-v2/second-manager/work`, especially
`add-super6-v3-01`, `reviewed-add-v3-finish-01`, `remove-super6-v3-01` and
`add-super6-v3-return-01`. Installation evidence is `install-04` under the parent
test directory. Pre-reset managed data is archived on both hosts under
`/var/backups/spvm-controller-cloud-20261108-04`. These secret-bearing directories
must not be committed or attached wholesale to reports.
