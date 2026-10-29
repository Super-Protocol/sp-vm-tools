# Non-CC hosts for SPVM OpenStack

## Scope and implementation status

Non-CC compute is **debug only** and untrusted. It is not a substitute for TDX or
SEV-SNP, and failed CC preparation never triggers a non-CC fallback. Use matching
current installer, driver, helper, launcher recipe and GUI sources. There are no
old-configuration adapters or migration steps. The existing published driver
0.2.29 is a baseline only; it does not contain the new launch-policy contract.

As of 2026-10-29, live checks passed for bootstrap/repeated KVM verification on
Ubuntu 24.04 and 26.04 Azure D8s_v5, fresh OpenStack installation, separate
compute onboarding, mixed CC/non-CC guests, real debug/release Auto scheduling,
compute-admission negative cases and the separate three-node debug cluster's
public HTTPS UI. See [the final checks](#final-publication-and-admission-checks-2026-10-29)
and the matching `swarm-cloud` `deployment/NON_CC.md` evidence. Older dated
checkpoints are audit history. Dedicated fresh controller-only reinstallation
was not repeated; role-aware screening without KVM/libvirt and actual service
containers without KVM were checked instead. Ubuntu 26 remains experimental.

The Azure checkpoint also includes successful native Kolla Docker preparation
on both real host distributions, explicit management/Geneve/NAT preparation,
state directory preparation, checksum-verified custom Nova/Skyline images and
initial deny-all helpers. These are installation prerequisites, not evidence of
completed OpenStack or Swarm guest acceptance.

Fresh-install details: system runtime parent directories must have explicit
0755 permissions even when the coordinator uses umask 077 for private journals.
The helper's fresh-host inspection stages its own approved discovery modules;
an earlier helper installation must not be required. Skyline assembly checks the
complete current asset allowlist, individual hashes and compressed siblings.
Docker preparation probes socket-activated libvirt before capturing its service
identity, so the probe itself cannot cause a false runtime-drift report.
Ansible's SSH transport needs PTY support unless the administrator explicitly
disables PTY requests; a source-restricted deployment key may allow PTY while
keeping port forwarding and agent forwarding disabled. Never distribute the
operator's original private key as a deployment prerequisite.

Pinned Kolla requires Docker SDK >= 3.4.1; native Ubuntu 24 SDK 5.0.3 and Ubuntu
26 SDK 7.1.0 passed live Docker ping. Do not replace distribution Python packages
through system pip merely to impose an unrelated major-version threshold.
Quay's upstream Kolla testing images require explicit `--use-test-images`.
Run the owned hostname preparation before Kolla: RabbitMQ names must resolve
uniquely to the configured API/management interface addresses, not Azure DHCP
addresses. On Azure, `ingress_address` is the assigned private bind address;
DNS/publication uses the separate deployment `network.public_ip` translated by
Azure. These addresses must not be conflated.

## Roles and prerequisites

| Host role | CC required | KVM required | SPVM guests |
| --- | --- | --- | --- |
| Controller/network/ingress only | No | No | None |
| Explicit non-CC compute | No | Yes, including nested KVM when virtualized | Enrolled debug builds only |
| TDX / SEV-SNP compute | Configured platform must be ready | Yes | Compatible enrolled release/debug builds |

Supported bootstrap systems are Ubuntu 24.04 and 26.04, x86-64, root/sudo,
internet access to Ubuntu and the pinned project release artifacts, and hardware
virtualization. A service-only host does not need `bootstrap_noncc.sh`; compute
preparation is separate from installing OpenStack.

Run on a prepared, empty compute host:

```bash
sudo bash scripts/bootstrap_noncc.sh
```

The script installs OVMF, QEMU/libvirt and ordinary networking prerequisites. On
24.04 it reuses the SHA256-pinned project QEMU artifact without installing the
CC kernel. Shared libvirt preparation retains the existing SHA256-pinned project
libvirt packages and service-account/AppArmor/passt configuration. The existing
shared passt workaround sets `net.ipv4.ip_unprivileged_port_start=0`; this is a
host-wide relaxation, not a confidential-computing feature. Account for it in
host hardening. No disks are formatted, GPU binding changed or CC service installed.
BIOS, PCCS, QGS, CC kernel and CC modes are not configured by this script.
SPVM runtime prerequisites include jq, curl, zstd, netcat and Uplink. If Uplink is
absent, bootstrap installs official `v1.165.6`, archive SHA256
`6998d64c7671393898b6be839911b1e7b22d4e1b0a0646631c57a62f9b0ee966`.
An existing administrator-installed Uplink is retained rather than replaced.

Verification starts one uniquely named, diskless, networkless transient KVM
domain and queries QEMU's **live** `query-kvm` status. It only destroys its own
probe domain, never an existing guest. A CPU flag or `/dev/kvm` alone is not a
successful result. There is no TCG fallback.
The probe also attaches virtio-vsock, used by the SPVM launcher. Bootstrap loads
ordinary `vhost_vsock` and `tun` modules; missing host kernel modules are an error,
not permission to use a different accelerator or launch mode.
Package/runtime changes are refused
when the shared safety checks find existing guests. If packages request a reboot,
the script exits with status 2 and explicit instructions; it does not reboot.
Rerun after a manually approved reboot before enabling compute.

## Explicit installation inputs

In deployment YAML, every compute requires an explicit mode, independent of roles:

```yaml
hosts:
  debug-compute:
    # SSH/network/storage fields omitted here; use the full deployment example.
    roles: [compute]
    launch_mode: untrusted
    bootstrap_release: build-447-debug
    compute_resources:
      reserved_cpus: 2
      reserved_memory_mb: 8192
```

Do not put `launch_mode` on a service-only host. For CC compute select `tdx` or
`sev-snp` explicitly; detection does not select or downgrade the configured mode.
`bootstrap_release` pins this compute's initial helper/cache build. If omitted,
the explicit cloud-wide `guest_release` is used. A mixed cloud whose default is
`build-449-release` must explicitly select a debug bootstrap build for each
untrusted compute; otherwise validation refuses installation. The helper request
must match that root-owned per-host selection exactly. An arbitrary debug build
in a helper request is not accepted, and this setting does not override the
host launch mode or allow release guests on non-CC. It is not a restriction on
all future enrolled guest images; later image registration has separate authority
and build/cache checks. Fresh platform enrollment includes the explicitly selected
bootstrap builds; existing-cloud enrollment needs an explicit operator update.
The matching helper request must contain:

```json
{"launch_mode": "untrusted", "allow_debug": true, "release": "build-447-debug"}
```

This is a fragment, not a complete helper request. The full schema also requires
machine/compute identity, pinned launcher/cache, storage pools and GPU inventory.
An empty GPU inventory and `gpus: []` are normal. GPU tooling is not installed
when no GPU/fabric devices are discovered.

The installer writes `/etc/spvm/launch-policy.json` as protected root-owned data
and binds it read-only into Nova. Its two fields are `mode` and `allow_debug`.
Never use instance metadata or the launcher's console `--debug` option as a
security opt-in. Existing debug registration/project enrollment opt-ins remain
separate and mandatory; a compute opt-in does not grant a tenant permission.

The driver bundle must advertise `explicit-host-launch-policy-v1`; installation
rejects an older bundle rather than pretending it supports untrusted compute.
Deploy agreeing driver/helper/launcher/configuration before enabling scheduling.
Do not update only the GUI. Existing experimental images must be explicitly
replaced/registered with the new image traits; there is no automatic migration.

## Adding a previously unknown compute host

Do not run `init-kolla`, `kolla-genpwd`, certificate generation or a full
controller deployment to add a host. Preserve the existing deployment YAML and
bound workspace as immutable inputs. Create a desired YAML adding exactly one
compute-only host; existing hosts and global settings must remain unchanged.
Audit and render the desired full inventory with the normal pinned backend,
then resolve a helper request with the new machine fingerprint and native
kernel-hostname-derived compute UUID. Use the same driver image and explicit
build/cache binding as the existing deployment.

Prepare a new private workspace, without any remote mutation:

```bash
sudo scripts/install-openstack.sh --driver-dir /srv/selected-driver -- \
  stage-add-compute-workspace \
  --config /srv/operator/deployment-existing.yaml \
  --new-config /srv/operator/deployment-three-hosts.yaml \
  --workspace /srv/operator/kolla-existing-bound \
  --rendered /srv/operator/rendered-three-hosts \
  --audit /srv/operator/audit-three-hosts.json \
  --request /srv/operator/new-compute-helper.json \
  --host compute03 --output /srv/operator/kolla-three-hosts-bound
```

The command copies the original passwords and CA byte-for-byte, adds the one
audited compute identity, and emits `NEW_COMPUTE_WORKSPACE_STAGED_NOT_ENROLLED`.
It does not contact the cloud, prepare a host, start a container or run Kolla.
It rejects controller additions, changes to old hosts/budgets/modes, image or
global setting changes, duplicate identities, non-native UUIDs and mismatched
helper build/mode/pool intent. Missing descriptions such as GPU VRAM remain
irrelevant to the PCI identity contract.

Existing Nova overrides and controller PCI aliases must remain unchanged. A new
GPU identity requiring different controller aliases needs a separately reviewed
controlled update; this input-only command will not silently deploy controllers.
This is expansion of the current configuration, not migration of an old schema.

Feed the new workspace and desired YAML to the existing `mode: add-compute`
workflow, with its explicit enrollment request and `new_compute.preparation`
recipe. That owned workflow installs missing native Docker/dependencies,
network/helper/launcher and the exact image on the **new** host, then performs a
compute-only deployment. It does not rerun the original cloud installation.
Existing controllers/guests are not restarted. Runtime enrollment/verification
and GUI capacity reconciliation remain required; staging alone is not readiness.

Each new compute needs management routes to **every** existing compute, not just
to the controller. A hub-only WireGuard configuration is insufficient unless
forwarding and peer routes are explicitly configured. Verify this before native
deployment. Adding the new host to a Kolla workspace does not extend the root
budget worker's delegation automatically: explicitly bind its desired deployment,
trusted SSH endpoints and per-host automatic baselines, then refresh real host,
pool and GPU catalogs. An empty GPU inventory is valid, but a missing catalog is
not. Never fabricate capabilities to make a new host appear schedulable.

Use a distinct SSH known-hosts file for the new transaction. Never append a new
key to a trust file while another approved operation is using its checksum.
The installer intentionally detects such input drift, even if deployment tasks
themselves succeeded. Review the original journal instead of replaying deploy.

## Scheduling and readiness

Resource flavors can be shared by release and debug images. Release image
registration adds `trait:CUSTOM_SPVM_CC_READY=required`; debug registration adds
`trait:CUSTOM_SPVM_DEBUG_READY=required`. The compute updates these managed traits
from current host readiness and withdraws them when checks fail. Existing
`CUSTOM_SPVM`, CPU/RAM, state-pool and native PCI accounting remain in force.

Auto only schedules compatible hosts. Explicit incompatible selection is rejected
by image-aware capacity/admission checks. Native scheduling applies to direct
Nova requests too, and the driver, privileged helper and launcher independently
reject release/untrusted combinations. Stale Placement data is not authority to
downgrade. No guest is automatically stopped, restarted or replaced on readiness
loss. Budgets still cannot be changed while the host has any VMs/reservations.

Untrusted debug readiness skips CC measurement/signature waiting, not PKI, CA
chain/digest checks or join membership. UI/CLI display **Untrusted debug**, not
successful hardware attestation. An untrusted CA is not a hardware trust anchor.
If Swarm itself rejects untrusted mode, record that exact failure; do not change
Swarm, fake measurements or mark it ready. Bootstrap guests must not be restarted
to repair application readiness: SPVM state keys are ephemeral at boot.

### CLI guest network preparation

The platform's base project enrollment selects an existing shared network; it is
not a replacement for preparing a cluster's security group. Before CLI creation,
run the existing network preparation command with the cluster name and the
actual source addresses used by compute readiness probes and host ingress:

```bash
openstack spvm network prepare noncc-example \
  --network <shared-network-uuid> \
  --host-cidr 172.30.91.10/32 \
  --host-cidr 172.30.91.11/32 \
  --host-cidr 172.30.91.12/32 \
  --output /srv/operator/noncc-example-enrollment.json --apply
openstack spvm cluster create --spec /srv/operator/noncc-example.yaml \
  --enrollment /srv/operator/noncc-example-enrollment.json
```

These private IPs are examples, not universal defaults. Verify the selected
source with `ip route get <guest-ip>` on each compute. This command reuses the
network/router and creates a scoped per-cluster group; it does not create another
guest network. GUI creation performs this preparation from the operator-approved
template host-source policy. Extend that policy explicitly when adding a compute
with a new management source. Do not open readiness ports to the public internet
or change an existing guest's durable birth enrollment to bypass a rule mismatch.

Ubuntu 26 remains an **experimental Kolla host** profile with Noble containers.
Keep real Ansible facts, use existing Docker if present, otherwise let the
reviewed Kolla workflow install it for the real host OS. Never point a 26.04 host
at the Noble Docker apt repository or present false distribution facts.

## Azure acceptance topology and lifecycle

Use only the separate `rg-openstack-noncc-test` resource group in Poland Central.
Four approved VMs are Standard_D8s_v5, 8 vCPU / 32 GiB, no GPU, Gen2 x64,
**Standard security** (explicit, not default Trusted Launch). Pin Canonical image
versions before provisioning. The initial versions are
`Canonical:ubuntu-24_04-lts:server:24.04.202610210` and
`Canonical:ubuntu-26_04-lts:server:26.04.202610210`.

Create the first controller/compute and second compute, then a fresh third host
through add-compute, and only later a separate fourth host for physical CC-cloud
integration. Do not dismantle the first stand for that test. Every created Azure
VM remains **running**; stop/delete requires a separate user instruction.

OS disks are 128 GiB; new dedicated data disks are 256 GiB. Only the test harness
may format an exactly identified newly created blank test disk and mount `/data`.
Neither bootstrap nor the installer formats arbitrary storage. Guest resources
are 4 vCPU / 16 GiB / 64 GiB state, one per host. Reserve 4 CPU / 12 GiB on the
controller and 2 CPU / 8 GiB on computes, without overcommit.

Recheck regional quota and Retail Prices API before provisioning. The approved
Linux rate on 2026-10-27 was $0.46/hour per VM: $1.38/hour for three and $1.84/hour
for four, plus disks/IP/traffic. Do not automatically increase count or size.
Use explicit outbound, restricted SSH and an isolated VNet; internal APIs, DB and
RabbitMQ must not be public. Access OpenStack UI over SSH tunneling. Swarm ingress
uses separate DNS and normal TLS. Azure anti-spoofing/NIC forwarding and private
VIP routing must be verified rather than assuming bare-metal L2 behavior. The
fourth compute requires a scoped management tunnel to the physical CC cloud.

References: [Dsv5 nested virtualization](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dsv5-series),
[Azure Retail Prices API](https://prices.azure.com/api/retail/prices).
Do not include provider credentials, API tokens or private SSH keys in evidence.

### Acceptance checkpoint: 2026-10-28

The initial Ubuntu 24/26 pair passed native Kolla prechecks, deploy and
post-deploy, compute runtime enrollment, shared-network preparation, a Barbican
smoke check and authenticated GUI/API verification (nine browser probes). The
private GUI endpoint is `https://172.30.90.2:9999`, reached through an SSH tunnel;
internal services remain private. Both hosts report debug-ready and not CC-ready.
No Swarm guest acceptance is implied by these platform checks.

Fresh activation exposed missing gateway DNS and missing initial GPU-less host
catalogs. DNS is now checked before web units are installed; inspected catalogs
are initialized before capacity collection, and the explicit-request budget
worker is activated separately afterward. Interrupted activations were reviewed
against original hashes and journals, not blindly replayed. Therefore this is
not a claim that the original installer completed unattended without corrections.

The third, independently bootstrapped Ubuntu 26 host passed the full
add-compute path, including native dependencies/Docker, network, helper, image
transfer, Nova deployment and runtime enrollment. No guest was restarted.
Its first preparation exposed a missing management
peer route. The failed journal was retained, the mesh corrected without tunnel
restarts, and a new plan generated. Three debug guests subsequently reached
ACTIVE/READY on three different hosts, with matching real CA and working gossip.
Honest release requests through direct Nova API failed with NoValidHost for both
Auto and explicit non-CC targeting. A normal CirrOS Nova guest also booted and
remains running. Public Swarm UI and mixed CC acceptance remain pending checks.
The exact source hashes, Azure VM
identities and ongoing results are recorded in the driver's `deployment/NON_CC.md`.

Guest launch exposed an existing libvirt renderer error: QEMU's `kvm-steal-time`
CPU property was passed as an unknown libvirt CPU-map feature. The corrected
untrusted launcher preserves the setting through the explicit QEMU global
`host-x86_64-cpu.kvm-steal-time=off`; TDX/SEV paths are unchanged. A transient,
diskless KVM probe verified the live accelerator and actual property value.
The driver recipe includes the corresponding hash-pinned patch. Install matching
launcher bytes **and** helper provenance hashes; replacing only the file must
fail provenance validation. Strict execution journals also require the launch
mode for subsequent observation and cleanup; older journals are not converted.

The failed seed never created a domain and was explicitly retired via Nova after
identity/journal checks. Its replacement and both joins are now ACTIVE/READY.
This is not a release-guest acceptance claim. Public UI publication timed out
because no guest gateway listened on 80/443, despite running PKI and application
containers. Do not mark public UI ready from Nova status or assume untrusted-mode
rejection without evidence; Swarm sources were not modified.

All four approved Azure VMs now exist and remain running ($1.84/hour compute at
the rechecked rate, plus disks/IP/traffic). The fourth Ubuntu 24 host passed
bootstrap/repeat and exact-new-disk preparation. Its scoped WireGuard connection
reaches both physical hosts and their private Keystone VIP; no internal API port
was published. The fourth host is **not yet enrolled in Nova**. The physical CC
components need matching explicit host launch policies and driver/helper/launcher
activation before mixed-cloud acceptance can be claimed.

When extending an existing tunnel, check candidate addresses against local
network/broadcast routes on **all** peers. A cryptographic handshake alone does
not prove IP reachability: the first proposed new address was broadcast in the
physical hosts' /30. It was corrected without restarting their tunnels. Preserve
existing peers and routes, and when replacing an address add the new address
before removing the last IPv4 address, to avoid flushing interface routes.

### Mixed-cloud preparation checkpoint (2026-10-28)

The physical controller can now SSH to the fourth Azure host over the restricted
tunnel using its existing deployment key. Only its source-restricted public key
was installed on the target, with an Azure-authenticated pinned host key; no
deployment private key was copied off the controller.

Both physical computes have the same candidate Nova image cached, plus matching
helper/launcher/root launch policy files staged privately, not activated. The
new explicit TDX configuration passes preflight; the bound update workspace
preserves existing CA/passwords byte for byte, native compute UUIDs, resource
budgets, state pools and GPU inventory. The all-project Nova snapshot found no
instances, all seven Placement providers had no allocations, and both hosts
had no libvirt/QEMU guests. Recheck these guards at apply time: this snapshot
is not a durable authorization to overwrite a now-busy host.

Image ID:
`sha256:6e2d94ad6cece123396224204b2eebf31576dd6eb1b397364e4d5e087647e8de`.
Archive SHA256:
`bb53e169ce8eb14352a2009a293aed24cd33da8f067820adee7ee89529ce5616`.
Nova `34.0.1.dev7` matches the existing physical API/scheduler. This is a
modified-source test candidate, not a published non-CC release.

The pre-activation guard found a false positive on unused state pools. A normal
`StateStore.report()` creates only an empty `.spvm-state.lock`; it does not
write `.spvm-claims.json` until there is an allocation. The compute-only guard
now accepts that current state under a shared nonblocking lock, while still
rejecting actual claims, unknown files, orphan ledgers and invalid or busy
locks. This is not a backward-compatibility path. All 28 compute-scope tests
pass and the corrected read-only guard passes on both physical hosts. Because
the installer guard source changed, regenerate source-bound compute plans;
never replay the earlier plans or edit their hashes to bypass validation.

Remaining: controlled helper/launcher/compute activation, full fourth-host
preparation/enrollment and mixed-cloud debug/release acceptance. All four Azure
VMs remain running; no working SPVM guest was restarted.

### Physical activation and mixed add-compute (2026-10-28)

Matching Nova/helper/launcher and explicit TDX host policy have now been activated
on both physical computes through scoped Kolla operations. Both native Placement
roots advertise CC-ready and debug-ready; exact read-only mounts, image identity
and unchanged non-compute containers were verified before scheduling was enabled.
No guest was restarted. This verifies host capability, not a positive SPVM launch.

The first empty-host stop reached Docker's 60-second timeout: Nova's main process
exited 137, but `ExecStop` returned success, the container was stopped and not
OOM-killed, and protected files were unchanged. The original transaction stopped
for review; a new reviewed continuation verified those postconditions. Never
ignore an arbitrary stop failure, replay a partial operation, or apply this
empty-host procedure to occupied hosts.

Current-schema checks subsequently found `experimental_debug_only` in the old
physical lab enrollment. The new runtime intentionally rejects that field.
Explicit current-format enrollment was prepared with the same IDs/networks and
release policy, but replacing it on existing CC hosts requires separate operator
approval. It has not been applied. There is no automatic conversion or old-schema
loader. Validate the mounted document with the current runtime normalizer before
claiming positive SPVM launch acceptance, even when Placement traits look ready.

The fourth Azure host has a dedicated management interface and its state pool
was prepared with the normal host-scoped storage command. Basic preflight passes;
the bound mixed workspace preserves existing credentials, CA and CC host intent.
Its separate explicit enrollment authorizes `build-447-debug` on the new host.
A dedicated matching SDK was installed at `/opt/spvm-noncc-test-client` because
the old physical lab does not have `/opt/spvm-openstack-client`; no existing SDK
or GUI installation was replaced.

Full add-compute preparation has started: peer rules and hostname mappings have
completed, with dependencies, Docker, network, helper, image and Nova stages
tracked separately. The new compute is not accepted until the workflow reports
`COMPUTE_READY`; starting the runner is not success evidence.

The initial native deploy reached `nova-cell : List all hypervisors` but failed
with HTTP 401: the historical Kolla workspace still held the admin password
from before an earlier password change. Current root SDK auth was verified and
used only in a new private workspace. This did not rotate any Keystone password,
change other service passwords or regenerate CA. Keep operator deployment inputs
in sync when changing an administrator password; a working OpenRC alone does not
prove that Kolla's saved credential is current.

The failed attempt had only preregistered a disabled/down service for the new
host, with no hypervisor or guest. Only that owned record was explicitly removed;
original journals were retained. Recovery uses a fresh prepared-compute plan,
not a replay of bootstrap, Docker, networking or helper installation. Native
Kolla then passed the hypervisor query and progressed into Neutron tasks.

A later driver source hardening also withholds ready-traits when current
protected project enrollment is absent, invalid or still deny-all. Four targeted
regression tests passed. That change is staged for a subsequent matching artifact,
not deployed in candidate05. Old SSH/legacy-backend test fixtures still prevent
claiming a clean full historical test-suite run.

### Fourth host accepted; live debug test (2026-10-28)

The reviewed prepared-compute transaction completed `OPENSTACK_COMPUTE_READY`:
native UUID `c3f459f0-e730-52d7-acee-6240d24bed55`, enabled/up, debug-ready and
not CC-ready. Its native allocation query returned a candidate for
4 vCPU / 16 GiB / 64 GiB debug resources and none for release. A direct Nova API
release request explicitly targeting this host failed with `NoValidHost`; only
the temporary ERROR record and marker were removed.

A cold-cache debug guest now runs at `10.77.0.185`, Nova UUID
`1c68e8c8-61d6-4f28-b4ef-b38dd62d6371`, with those resources and no GPU.
Libvirt confirms KVM, no launch security and explicit untrusted mode; Nova is
ACTIVE and reports `untrusted-debug` without fabricated measurement. This is a
one-node launch test, not a healthy three-node Swarm cloud. CA readiness and UI
acceptance have not been claimed.

**networkd ownership matters for custom tunnel policy routing.** The management
interface reload removed a wg-quick-created source-policy rule, although its
VTEP routes remained. Header-only diagnostics showed Geneve returning through
eth0 instead of the authenticated tunnel. The new Azure host now declares its
rule in the dedicated management `.network` file:

```ini
[RoutingPolicyRule]
From=172.30.80.4/32
Table=51828
Priority=10928
```

Remove duplicate PostUp/PostDown creation of that same rule, but retain the
WireGuard VTEP routes. Verify the rule survives networkd reload and reconfigure,
that `ip route get <peer-vtep> from <local-vtep>` selects the tunnel, and that the
outer WireGuard endpoint route still uses the normal NIC. Do not restart a
bootstrap VM to repair host policy routing. This live fix changed neither peer
keys nor guest state and required no tunnel restart.

Afterward SSH/measurement endpoints were reachable; read-only guest diagnostics
reported no failed units, working registry HTTPS and `swarm-init` still starting.
No private Swarm logs were read, and no guest service was changed or restarted.
SSH was debug-only diagnostics with an in-memory PTY password, not a launch or
readiness prerequisite and not cryptographic attestation.

The operator subsequently approved explicit current-format enrollment on the
two empty physical CC hosts. Both documents now normalize inside the compute
containers with matching mounted hashes. Project identities, network permissions
and build policies were preserved. Only Nova compute services were restarted;
other containers and guest VMs were unchanged. Nova independently reports both
CC hosts and the mixed Azure compute up/enabled. All four Azure VMs remain on.
Protected receipts are `explicit-runtime10/transaction.json` (super6) and
`explicit-runtime11/transaction.json` (super7), in the mixed test workspace.
This explicit deployment operation adds no product migration support.
GUI/catalog refresh, later source-hardening rollout and remaining mixed release
acceptance are not complete.

### Mixed-cloud final checkpoint, 2026-10-28

The subsequent OpenStack checks passed:

- A real `build-449-release` instance on the TDX host `.245` reached ACTIVE /
  READY; public CA and gossip were independently reachable. Instance UUID:
  `0fd55668-63d6-47c9-9608-5cd0be369b6f`.
- Direct Nova requests with forged debug metadata/misleading image trait and
  an unapproved debug image reached the Azure compute and failed driver
  preflight. Native action events and UUID-scoped Nova diagnostic categories
  proved compute execution/rejection. Temporary ERROR records/images/flavor
  were removed; existing guests were unchanged. This is not a full native
  valid-grant/disabled-opt-in matrix test.
- Installed driver/helper and the shipped launcher policy block independently
  rejected release on untrusted mode; unknown mode and absent opt-in were also
  rejected without modifying protected policies or starting guests.
- The matching GUI/API is active at `/srv/spvm-web-noncc-20261028-06` on `.247`.
  Skyline login/profile/domain/logout, nine authenticated catalogs and eight
  identity/gzip asset checks passed. Three host choices are visible; the Azure
  host is labelled `Non-CC · debug only`. Release filters exclude it, debug
  filters include it. Explicit non-CC release capacity is rejected, while the
  empty CC compute has positive release capacity.
- The explicit budget-worker delegation includes the third host, preserving
  all existing host donations. Activation used read-only catalog synchronization,
  not processing of queued changes. CLI node show reports `untrusted-debug`,
  no measurement and `NOT_APPLICABLE_UNTRUSTED`.

Candidate06 was built locally from modified sources, not published:

- Bundle SHA256: `6eeb344c4dd45dabd156453b08df53c0d5672d261f3fa02e8034561fd154456f`.
- Wheel SHA256: `7574f29aa19c354479320130af6cf9304894f565b1094a14e0f512e755d618fb`.
- Nova image ID: `sha256:504e373b1d3f76c61eb7514579214e567f77ac702954b1ceb58d3ce4a8dd643d`.
- Image context SHA256: `f79a4f27adf0e4312a88f63fbdcd3e173604aae8fb5a5b47e8b7cc75b7414081`.
- Compressed image SHA256: `9d3e13d42a324110470381a36f09fc270b88ab56548aacb894dd4e7fa0085ccd`.
- GUI image ID: `sha256:451afd4aa6c701cb1791357903fc292b1a01367ffa6f543842a735449251fbbd`.

The compute image's later enrollment-readiness hardening is active only on the
empty `.247` compute. Other computes retain their previously verified non-CC
images; this is not an all-host candidate06 rollout. Other containers and all
guests were preserved. Stopped previous Nova/console containers were retained.
The reviewed console activation preserved existing container settings and is
not a general-purpose product upgrade workflow.

Protected evidence in `/var/lib/spvm-noncc-mixed-20261028-01`: `mixed-release12`,
`native-forgery13`, `web06`, `catalog06-final`, `compute06`,
`final-gui-smoke06.json`, `final-cli06.json`, `final-live-ca-check06.json`.
The fourth Azure host retains `/var/lib/spvm-noncc-final-policy06.json`.

75 targeted source tests passed; this does not claim the retired-fixture full
suite is green. Production code and English docs are staged, tests are not.
No commit, push or release publication was performed.

All four Azure VMs remain running as Standard_D8s_v5. The original Azure cloud
still has three ACTIVE / READY untrusted SPVM nodes on separate hosts and an
ACTIVE ordinary Nova guest. The additional mixed-cloud Azure seed is ACTIVE /
WAITING_CA: its real PKI endpoint is not listening. No Swarm code or guest
service was changed, and no private application logs were read. Full Swarm UI
acceptance and that seed's CA readiness are outstanding application-level work;
do not report them as passed or fabricate attestation/readiness.

### MTU requirements and completed CA acceptance, 2026-10-28

The mixed Azure seed's previous WAITING_CA has been resolved. It was an MTU
black hole: guest/Neutron MTU 1442 did not fit WireGuard MTU 1420 plus Geneve.
Small HTTPS requests worked; asset-CDN handshakes timed out. A diagnostic socket
with MSS 1200 succeeded on the same IP, establishing the size-dependent failure.
No permanent TCP MSS workaround was installed.

Fresh rendering now reads every compute/network host's actual configured
`tunnel_interface` MTU. The smallest transport MTU sets Neutron
`global_physnet_mtu` and ML2 `path_mtu`; a checksummed `network-mtu.json` records
the observations. The pinned IPv4 OVN profile explicitly uses
`overlay_ip_version = 4` and Geneve `max_header_size = 38`, giving 58 bytes of
overhead. Transport MTU 1420 permits guest MTU up to 1362. Do not subtract
WireGuard overhead twice: its interface MTU already excludes that overhead.

Missing/unknown/unsupported MTU observations block preflight. Controllers without
a network/compute role do not need a Geneve MTU observation. This is an interface
budget, not an end-to-end PMTU discovery protocol: configure the routed/VPN path
correctly before installation and test more than a small ping or HTTPS response.
IPv6 tunnel underlays, VXLAN and different Geneve option budgets are outside this
calculation; do not apply the 58-byte constant to them.

Add-compute checks both actual transport interfaces and existing Neutron
networks before full preparation or prepared-host deployment. Oversized guest
networks fail `NETWORK_MTU_EXCEEDS_TRANSPORT`; unknown overlays/MTUs fail closed.
Prepared-host apply also compares a fresh observation with its approved plan.
The installer never silently lowers an occupied cloud's network MTU or restarts
guests. Old plans are not converted: regenerate and approve a new plan.

For a deliberate live repair:

1. Determine the minimum supported transport/path MTU and account for overlay
   overhead. Review affected networks and existing guests.
2. Persist Neutron/Kolla custom overrides in `config/neutron.conf` and
   `config/neutron/ml2_conf.ini`, then load the reviewed Neutron configuration.
3. Explicitly update the affected Neutron network and arrange DHCP renewal or
   a live NIC MTU update for existing guests. Neutron MTU alone does not instantly
   update every NIC. Never reboot an SPVM bootstrap VM for this.
4. Verify guest outbound asset downloads and PKI/gossip from the compute itself.
   Controller-only access can conceal a missing compute management route. If
   networkd was manually reconfigured, revalidate/reconcile the owned management
   unit's guest route and policy rules.
5. Use the fenced native readiness observer; never manually stamp READY.

On the test stand the shared network and debug NIC now use MTU 1360, and the
persistent Neutron transport settings use 1420. Only the exact stuck asset curl
child was terminated, allowing its existing init retry to progress. No VM,
swarm-init service or CA-generation service was restarted. Only Neutron API was
restarted to load config; the data plane and other guests were retained. Modified
workspace inputs invalidate the earlier approval instead of re-signing it.

The real CA/gossip checks succeeded and the native observer returned SEED_READY.
Instance `1c68e8c8-61d6-4f28-b4ef-b38dd62d6371` is ACTIVE / READY /
untrusted-debug. CA SHA256:
`0f14eca9b595b080310ad640302f0a7fd2504ebe9ee4ee1c9b5401b079c5208e`.
Evidence is in `/var/lib/spvm-noncc-mixed-20261028-01/mtu-repair01`.
This closes the earlier CA blocker, not the separate full Swarm UI acceptance.

108 focused MTU/preflight/render/resource/plan/compute/workspace tests passed.
Tests remain outside stage. No automatic node recovery, compatibility adapters,
commit, push or release publication was introduced.

### Stand activation checkpoint, 2026-10-29

All six test compute hosts now run the same current Nova image and root driver /
runtime-config modules. The three-host Azure cloud has three debug-only computes;
the mixed cloud has two TDX computes and one Azure debug-only compute. Native
Placement traits and installed helper/launcher policy checks passed. Scheduling
was restored only after checking current enrollment, empty hosts, healthy Nova
heartbeats and absence of root Placement allocations.

The operator explicitly deleted five disposable SPVM guests and one CirrOS guest
through Nova before activation, checking their exact UUID/host/cluster identities.
No replacement guests were created and no guest was restarted. Only Nova compute
containers were restarted; unrelated containers were verified unchanged. Previous
containers and module backups remain for manual inspection, not automatic rollback.
All four outer Azure VMs remain running, unchanged Standard_D8s_v5. Their lifecycle
and disks were not changed. Deleted SPVM CA/state cannot be resumed.

Current prepared sources and private receipts are on every host under
`/var/lib/spvm-noncc-candidate07-20261029/`. For new test plans select its
`driver/deployment/installer.py` backend and matching
`installer/scripts/openstack/` sources. Existing approved workspaces were not
silently re-signed or migrated: render/review fresh inputs and bind the current
image digest before another Kolla deployment, rather than reapplying an old
workspace that selects an older image.

The modified installer baseline is `1012586cc5d94af15b7c078604d534bb0007b645`;
the modified driver baseline is `b4ab15c918c1956533fff2b9930c6d60d700056b`.
Candidate07 bundle SHA256 is
`d33ee2b90cb01e12895cc334c0fc13bc253dc4331f72f627ec300de23738d3cc`.
Its wheel SHA256 is
`7574f29aa19c354479320130af6cf9304894f565b1094a14e0f512e755d618fb`,
identical to candidate06, allowing reuse of the verified image
`sha256:504e373b1d3f76c61eb7514579214e567f77ac702954b1ceb58d3ce4a8dd643d`.
Candidate07 adds the later installer/MTU sources. Its manifest records the exact
bundle snapshot, not subsequent documentation edits. No release was published.

The physical-cloud authenticated UI/API and capacity smoke passed: nine catalog
routes, eight identity/gzip asset checks, login/profile/domain/logout, release
rejection on non-CC and compatible CC capacity. An initial probe during catalog
refresh returned StopIteration; the retained successful retry is
`final-gui-smoke07b.json` in the mixed workspace. 126 focused tests passed; tests
remain unstaged. No commit or push was performed.

Public Swarm UI is still not accepted. Before retirement, the Azure seed's local
HTTP UI returned 200 on port 3000 and Swarm/PKI/Kubernetes services were running,
but no guest gateway listened on 443. DNS pointed to the expected controller;
publication remained NEEDS_REVIEW / WAITING_GATEWAY. Internal UI availability
does not validate public TLS routing. No public port-3000 bypass, guest restart,
Swarm source change or fabricated readiness was introduced. The missing Swarm
gateway is a separate application issue; those disposable guests are now deleted.
This checkpoint does not claim a fresh end-to-end guest boot after activation.

### Required cluster identity and empty-host checks

The matching Nova provider renderer now injects a cluster-wide `global_id`:
the authoritative cluster UUID without hyphens (32 lowercase hex digits).
`pki_authority.networkID` remains the full canonical UUID. Seed and joins share
the identity; a new cluster must not inherit a fixed ID from a reusable template.
Operators do not need to enter this value manually. No migration or live rewrite
of an existing cluster is provided.

Current OpenResty/service state expressions need the SwarmDB
`SwarmIdPointer/global_id` initialized by a supporting guest build. Local UI
HTTP 200, PKI and Nova READY alone do not establish gateway readiness. Supplying
this field is OpenStack integration, not a Swarm or guest-code modification.

Before activating an empty host, inspect local pool ledgers/disks and runtime
leases, not only Nova servers, libvirt domains and root Placement allocations.
Include child-provider allocations. A local orphan reservation fails storage
reporting closed and must not be automatically freed. The existing installer
guards reject it; explicit test cleanup requires exact UUID/project ownership,
no Nova server, no domain and no allocation before invoking owned runtime cleanup.

For the tested OpenResty `services: develop` version, Let's Encrypt is the
default when **`swarm/openresty.yaml` is absent**. The optional file is the
ZeroSSL/EAB override; `ACME_PROVIDER: letsencrypt` inside that file is invalid for
this service contract. Omit it from a Let's Encrypt provider template rather
than rewriting running guests or adding configuration-compatibility adapters.

Permit public TCP 80 for ACME HTTP-01 only after checking the controller's
managed HTTP router: enrolled hostnames and GET/HEAD challenge paths only,
404 for other requests. The Azure test NSG rule `swarm-ui-acme-http01` targets
only `10.254.120.10/32`, TCP 80, priority 125. This does not open internal
OpenStack APIs, DB or RabbitMQ, nor HTTP on the other test compute hosts.

The integration-fix candidate08 is active on all six compute hosts. Bundle SHA256:
`0622956c212f2d2bb8e465659dd283ece079aa0fafb2c2016570c229a7da3254`;
wheel SHA256:
`7461439a40201cbf764d7cde4eaff4d672e10b54fef47d53ef13e7daee895385`;
Nova image:
`sha256:df79f0c93ea9a2cd1d3816f078900788384d2165358e7b3cfc09b73f22244500`.
Per-host receipts are under `/var/lib/spvm-noncc-candidate08-20261029/`.
An unallocated old test reservation blocked storage reporting and Nova's provider
tree. Explicit owned runtime cleanup removed only UUID
`33fffe58-7ae7-4a9e-8216-878ee4d10884` after server/domain/allocation/project
checks; its 64-GiB state disk is not recoverable. No automatic cleanup was added.

UI09 then verified generated identity in the real guest/SwarmDB, three ACTIVE /
READY nodes, common CA and reachable gossip. 134 focused tests passed. A separate
test-harness mistake had supplied the unsupported Let's Encrypt override. It was
privately backed up outside the template; publication was stopped before
enrollment and exactly that disposable three-guest cluster was deleted. Read-only
provider disks and Swarm/guest code were not edited. The ad-hoc publisher input
directory also needed explicit 0750 under UMask 0077; production web staging
already performs the explicit permission setup.

Protected UI09 evidence and retirement receipts are under
`/srv/spvm-noncc-test/debug-cluster09/` and `ui09-retirement/`. A fresh corrected
UI10 run is under `/srv/spvm-noncc-test/debug-cluster10/`. All four outer Azure
VMs remain running. Public UI acceptance must be recorded separately after a
successful strict HTTPS smoke; CA/readiness alone does not close that check.

### Final publication and admission checks, 2026-10-29

UI10 subsequently completed its normal publisher workflow with `UI_READY` /
`complete` and strict public HTTPS 200 at
`https://noncc-20261029-ui10.swarm.superprotocol.dev/`. Its three guests remain
ACTIVE / READY on different Azure hosts. The separate mixed cloud accepted a
real debug/untrusted seed on Azure and a real release/TDX seed on the physical
host, including live Nova/Neutron, grant and Barbican CA bindings. These two
single-seed tests do not claim Swarm quorum or another public Swarm UI.

Final native Nova compute-negative evidence is under
`/var/lib/spvm-noncc-mixed-20261028-01/remaining24/` on the physical controller:
stale CC traits do not admit release on non-CC, a Glance class differing from
the trusted grant fails image authorization, and disabling debug opt-in rejects
debug despite stale readiness. All three reached installed compute admission
and failed before domain creation; scheduler-only failures were not counted.
Exact temporary ERROR records, ports, SGs and the misclassified image were
deleted. Protected launch policy bytes, traits and scheduling were restored;
no running guest or outer Azure VM was restarted. Only the disposable earlier
`mixed13-debug` was deleted to free the negative-test host.

Role-aware production screening also passed inside a real private-device
systemd namespace without KVM and without libvirt access. The running Keystone,
Neutron, Barbican and Skyline containers had no `/dev/kvm`. Evidence is under
`/srv/spvm-noncc-test/service-only-no-kvm19/`. This is not a second fresh
controller-only installation: the existing combined-role host was not rebuilt
or converted for this check.

When reverting deliberately injected Placement traits during tests, do not
restore an obsolete `COMPUTE_STATUS_DISABLED` onto an enabled compute. Check
fresh native status and capacity before the next request. Old unused per-cluster
SGs can exhaust the Neutron rule quota even after guests disappear. Retire only
explicitly identified, unattached test objects; no automatic cleanup or quota
increase was added. Earlier failed harness attempts are retained separately
and are not admission success evidence.

Real Auto scheduling then passed with both images, with no explicit Nova host
parameter: debug guest `b6574e71-53d5-4bfb-b8ec-0faa372f1656` landed on
`openstack-test-ubuntu24-cc-join`; release guest
`367c9934-635f-4ab2-a85e-8c44a98ea007` landed on `super6-tdx-h100`. Both are
ACTIVE / READY with normal live CA/grant/Nova/Neutron validation. Debug reports
untrusted/no measurement and no applicable signature; release reports TDX and a
present registry record. Evidence: mixed-test `auto20/transaction.json`, status
`REAL_AUTO_DEBUG_NONCC_AND_RELEASE_CC_PASSED`.

The test briefly disabled new scheduling on CC hosts only until the debug Auto
guest was assigned, then restored both and checked native Placement before
submitting release Auto with all computes available. Existing guests were not
restarted. A final authenticated GUI/CLI rerun passed nine catalogs and eight
asset checks (`final-gui-smoke27.json`); an earlier HTTP-error receipt is retained
without claiming a diagnosed cause for that non-reproduced failure.

37 exact retired test secrets/intents across seven clusters were explicitly
removed after creator/identity/no-live-guest checks, to avoid stale test clusters
in the UI. The active Auto guests, earlier release seed and independent Azure
three-node cluster were excluded. All four outer Azure VMs remain running.
No commit, push or release publication was performed. Dedicated fresh
controller-only reinstallation remains outside this rerun's evidence; service
screening without KVM and actual running service containers were checked instead.
