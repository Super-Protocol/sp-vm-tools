# Non-CC hosts for SPVM OpenStack

## Scope

Non-CC compute is **debug only** and untrusted. It is not a substitute for TDX or
SEV-SNP, and failed CC preparation never triggers a non-CC fallback. Use matching
current installer, driver, helper, launcher recipe and GUI sources. There are no
old-configuration adapters or migration steps. Require a matching kit advertising
both `explicit-host-launch-policy-v1` and `manual-host-resource-lifecycle-v1`.
Install/add leaves compute UNCONFIGURED and disabled; assign resources and
explicitly Enable it as described in [manual resources](OPENSTACK_HOST_RESOURCES.md).

Fresh-install details: system runtime parent directories must have explicit
0755 permissions even when the coordinator uses umask 077 for private journals.
The helper's fresh-host inspection stages its own approved discovery modules;
an earlier helper installation must not be required. The selected prebuilt
Skyline image is checked against its kit descriptor; the installer does not
assemble or patch its JavaScript.
Docker preparation probes socket-activated libvirt before capturing its service
identity, so the probe itself cannot cause a false runtime-drift report.
Generated Ansible configuration uses noninteractive SSH without a PTY.
Passwordless non-TTY sudo is required. Never distribute the operator's private
key as a deployment prerequisite.

Use a Docker SDK compatible with the selected Kolla and verify access to Docker.
Do not replace distribution Python packages
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
absent, bootstrap installs the official artifact pinned and SHA256-verified by
the matching bootstrap sources.
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
    # SSH/network fields omitted here; use the full deployment example.
    roles: [compute]
    launch_mode: untrusted
installation:
  allow_debug: true
```

Do not put `launch_mode` on a service-only host. For CC compute select `tdx` or
`sev-snp` explicitly; detection does not select or downgrade the configured mode.
Installation selects no guest build. The old `bootstrap_release` and
`guest_release` fields are rejected. Every later launch requires an authorized
registered image and explicit build. For an untrusted host, the matching helper
request must contain:

```json
{"launch_mode": "untrusted", "allow_debug": true, "registered_release_cache_root": "/var/lib/spvm/registered-releases"}
```

This is an internal fragment, not a user preparation step or a complete helper
request. The installer supplies machine/compute identity, pinned launcher,
`pools: []`, disabled detected GPU inventory and `gpus: []`. Do not place resource
fields in installation YAML. GPU tooling is not installed when no GPU/fabric
devices are discovered.

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

Do not run `init-kolla`, generate new cloud passwords/CA, or assemble manual
helper/enrollment recipes. Use the current host-only add-compute interface:

```bash
sudo bash scripts/spvm-openstack.sh add-compute \
  --config /srv/operator/noncc-computes.yaml \
  --environment /srv/operator/environment \
  --controller operator@192.0.2.10 \
  --output /var/lib/spvm-install/add-noncc-01 \
  --check
```

The YAML root contains only `hosts`. Each untrusted host explicitly sets
`launch_mode: untrusted` and `allow_debug: true`; omit CPU/RAM, pools and GPU
settings. See [compute addition](OPENSTACK_COMPUTE_ADDITION.md) for the full
schema, SSH options and batch behavior. `--check` contacts hosts/cloud read-only;
it does not install or enroll. To apply, use a new output directory, omit
`--check`, and approve interactively or with `--yes`. Ubuntu 26 also needs
`--allow-experimental-ubuntu26`.

The command retains the existing cloud credentials/CA, prepares only selected
computes, deploys them, enrolls their restricted executors and refreshes real
catalogs. It does not restart existing guests or automatically enable scheduling.
New and returning computes both finish UNCONFIGURED/disabled.

Each new compute needs management routes to **every** existing compute, not just
to the controller. A hub-only WireGuard configuration is insufficient unless
forwarding and peer routes are explicitly configured. Verify this before native
deployment. The owned add-compute command updates the controller's explicit
per-host executor delegation. Runtime resource management uses the typed API
channel, not service SSH keys or installation baselines. Current resource limits
are protected host state; the workspace is infrastructure/provenance only.
An empty GPU inventory is valid, but a missing catalog is not. Never fabricate
capabilities to make a new host appear schedulable.

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

## Cloud-provider prerequisites

For nested compute, choose a provider VM/security profile that actually exposes
KVM. Check the current provider documentation and quota before provisioning;
availability of a SKU is not proof that acceleration works in the guest OS.
Budget for the host OS and controller services before assigning VM resources.
Provider prices, image versions and resource names are deployment inputs, not
installation defaults.

Configure explicit outbound access, NIC forwarding/anti-spoofing and routes for
the selected topology. A public translated IP is not necessarily a local bind
address. Verify effective tunnel/overlay MTU and paths between every compute,
including readiness probes and ingress return traffic. Restrict SSH and keep
internal API, database and RabbitMQ endpoints private. Use an approved management
route/tunnel for the GUI; a public GUI requires a separate reviewed access policy.
Neither bootstrap nor the installer partitions, formats or mounts provider disks.
