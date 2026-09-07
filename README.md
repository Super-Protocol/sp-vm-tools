# sp-vm-tools

Utilities for bootstrapping a Confidential Computing host (Intel **TDX** or AMD **SEV-SNP**) and launching Super Protocol confidential VMs on it.

## Contents

- [What's in here](#whats-in-here)
- [Quick start](#quick-start) — bootstrap a host and launch your first VM
  - [1. Clone the repo](#1-clone-the-repo)
  - [2. Bootstrap the host](#2-bootstrap-the-host)
  - [3. Reboot](#3-reboot)
  - [4. Verify the host](#4-verify-the-host)
- [Running a Swarm cluster](#running-a-swarm-cluster)
- [Requirements](#requirements) — OS, CPU, BIOS, GPU
- [Roadmap](#roadmap)
- [License](#license)

## What's in here

| Script | Purpose |
|---|---|
| `scripts/bootstrap_tdx.sh` | Turn an Ubuntu host into a TDX-capable hypervisor (kernel, QEMU, OVMF, attestation, GPU passthrough). |
| `scripts/bootstrap_snp.sh` | Turn an Ubuntu host into a SEV-SNP-capable hypervisor (firmware, modules, GPU passthrough). |
| `scripts/start_super_protocol.sh` | Start a confidential VM (TDX / SEV-SNP / untrusted) from a Super Protocol release image. |
| `scripts/start_super_protocol_libvirt.sh` | Start the same VM as a transient `qemu:///system` domain through libvirt-python (Ubuntu 24.04 or 26.04). |
| `scripts/swarm-cluster.sh` | Bring up a 3-node Swarm cluster on a single host. |
| `scripts/check_configuration.sh`, `get_super_running_vms.sh` | Auxiliary tooling. |

---

## Quick start

This is the main path: take a bare Ubuntu host, turn it into a confidential hypervisor, and launch your first VM. Before you begin, confirm your hardware and BIOS match the [Requirements](#requirements) — the bootstrap scripts enforce the supported OS versions and will check CPU/BIOS settings for you.

> All scripts run as `root` and need an internet connection. A reboot is required partway through.

For the exact commands to clone the repository, run the bootstrap scripts, and launch a VM, see [docs/swarm.md](docs/swarm.md).

### Libvirt launcher (Ubuntu 24.04 and 26.04)

`scripts/start_super_protocol_libvirt.sh` reuses the release, disk, provider-config, and VFIO preparation from the direct QEMU launcher, then builds domain XML and starts a transient domain through `libvirt-python`. GPU passthrough uses IOMMUFD. The TDX and SEV-SNP bootstrap scripts install libvirt **12.5.0** when the system version is older, configure AppArmor, grant `passt` the capability required for privileged ports, and validate the daemon and domain capabilities before VFIO devices are bound.

The command line is the same as for `start_super_protocol.sh`, with an optional domain name:

```bash
sudo ./scripts/start_super_protocol_libvirt.sh \
  --name super-protocol-3 \
  --provider_config /path/to/provider-configs \
  --mode tdx
```

With `--debug false` the command returns after the domain starts. With `--debug true --log_file /path/to/boot.log`, it follows the domain serial log and copies it to the log file; `Ctrl-C` detaches without stopping the VM. The domain always records its console to `/var/log/libvirt/qemu/<domain>-serial.log` from the first byte, so a VM that fails early can still be diagnosed. The serial port is a file sink rather than a pty, because a pty nobody reads fills up and stalls the guest inside console output. Use `virsh -c qemu:///system list`, `shutdown`, or `destroy` to manage it. `--gpu none` disables GPU, NVSwitch, and CX7 passthrough for diagnostics.

#### Libvirt host configuration

Run the bootstrap matching the host CPU before using the libvirt launcher:

```bash
sudo ./scripts/bootstrap_tdx.sh
# or
sudo ./scripts/bootstrap_snp.sh
```

The TDX bootstrap selects the NVIDIA confidential GPU mode from driverless PCI
and VPD data. It uses regular CC mode for standalone/PCIe GPUs and Blackwell
NVLink systems, and Protected PCIe mode for Hopper NVSwitch systems. Automatic
detection is the default; use `--gpu-mode cc` or `--gpu-mode ppcie` only as an
explicit hardware override.

The bootstrap performs the host-wide work that previously required manual fixes:

- installs the complete project libvirt 12.5 package set, including `libvirt-dev`, when the installed version is older or any required split package is missing;
- preserves already installed libvirt split drivers during the package transaction;
- enables executable mmap and the libvirt socket in the nested AppArmor `passt` profile;
- permits QEMU to contact TDX QGS through VSOCK;
- applies `CAP_NET_BIND_SERVICE` to every installed `passt` binary;
- prepares `/var/lib/libvirt/images/superprotocol` and validates `qemu:///system`.

The bootstrap deliberately does not change `net.ipv4.ip_unprivileged_port_start`. File capabilities can be removed when the administrator upgrades or reinstalls `passt`; re-run the same bootstrap to restore them. The launcher detects missing AppArmor rules or capabilities before preparing VM disks and prints the appropriate bootstrap command.

On Ubuntu 24.04, the bootstrap adapts only the verified temporary copy of the
project `libvirt-daemon-driver-qemu` package to the `systemd-sysusers` syntax
supported by that release. The downloaded release archive itself is not
modified.

Do not disable AppArmor globally. For diagnostics, inspect recent denials with `journalctl -k --since '-10 min' --no-pager`.

### 1. Clone the repo

Clone the repository onto the target host. See [docs/swarm.md](docs/swarm.md) for the exact command.

### 2. Bootstrap the host

Pick the script that matches your CPU vendor. See [docs/swarm.md](docs/swarm.md) for how to invoke each one.

#### Intel TDX

What it does:

1. Verifies Ubuntu version and root privileges.
2. Runs `setup_tdx.sh` to install the pinned Canonical HWE kernel, the project TDX QEMU package, and PCCS attestation host components.
3. Verifies BIOS/CPU TDX settings (TME, TME-MT, SEAM, TXT, SGX, …).
4. Installs the required QGS/PCCS attestation packages directly, without running Canonical's host-setup script or enabling global package downgrades.
5. Updates the Intel TDX-Module to a known-good version.
6. Configures NVIDIA GPUs for Confidential Computing (CC mode + `vfio-pci` binding) and, on B200 systems, sets up ConnectX-7 bridges for VFIO passthrough.
7. Installs and validates libvirt 12.5, AppArmor policy, VSOCK access, and `passt` capabilities before binding devices to the VM stack.

> **Note:** Some steps require manual action to take effect. The script may stop and ask you to do something, then need to be re-run — this is expected. Follow the on-screen instructions and re-run to finish.

#### AMD SEV-SNP

What it does:

1. Verifies Ubuntu version, root privileges, and detects the EPYC generation (Milan / Genoa / Turin).
2. Installs the SEV-SNP hypervisor stack: the pinned Canonical **7.0.0-31** kernel and project QEMU **10.2.1** package on Ubuntu 24.04 (the same base stack as TDX), or distro kernel/QEMU on newer Ubuntu releases.
3. Downloads and installs the matching AMD SEV firmware blob to `/lib/firmware/amd/` and reloads `ccp` / `kvm_amd`.
4. Runs SNP status checks (RMP table, SEV / SEV-SNP API versions, ASID allocation, IOMMU groups, hugepages, CPU governor).
5. Configures NVIDIA GPUs for CC mode and binds them to `vfio-pci`.
6. Installs and validates libvirt 12.5, AppArmor policy, and `passt` capabilities before binding devices to the VM stack.

> **Ubuntu 24.04 note:** the SNP bootstrap pins the exact Canonical HWE kernel and QEMU package versions validated by this project. A reboot into the new kernel is required before SNP validation can finish.

### 3. Reboot

A reboot is required partway through bootstrap. After reboot, re-run the same bootstrap script if it asks you to — some steps (firmware, kernel parameters, VFIO bindings) only take effect after a reboot. See [docs/swarm.md](docs/swarm.md) for the exact command.

### 4. Verify the host

`scripts/check_configuration.sh` prints a hardware overview (CPU, memory, network, disks, RAID/SMART) you can compare against the [Requirements](#requirements). See [docs/swarm.md](docs/swarm.md) for how to run it.

Hardware acceptance remains a manual step because containers cannot validate KVM, IOMMUFD, QGS, VSOCK, or physical GPU assignment. On each prepared host verify:

- a transient VM starts in release and debug modes;
- TCP/UDP forwarding works on host ports 53, 80, and 443;
- TDX measurement returns a non-empty quote and PKI/gossip become ready;
- SEV-SNP launch security is active;
- `--gpu none` works and an enabled GPU is attached through IOMMUFD;
- the kernel audit log contains no new `passt`, libvirt, or VSOCK AppArmor denial.

## Running a Swarm cluster

There are two ways to run a Super Protocol Swarm cluster.

### Single-host cluster (quick start)

`scripts/swarm-cluster.sh` brings up a **3-node Swarm cluster on a single host** — no multi-machine setup. It creates an isolated bridge network, launches one bootstrap + two join VMs as transient libvirt domains, auto-configures provider configs, and sets up ingress via HAProxy. In debug mode their serial consoles remain attached in separate `tmux` sessions. You still need to set `gateway_hostname` in the provider template to point to the machine's public IP.

Prerequisites: a bootstrapped host (TDX or SEV-SNP), a populated provider config template (see [config.yaml reference](docs/swarm.md#configyaml-reference) for an example), and `tmux` / `nftables` / `curl` installed.

For the exact commands (`up`, `status`, `down`, and all flags — `--provider-config-template`, `--release`, `--join-cores`, `--join-mem`, `--gpu-target`, etc.), see [docs/swarm.md](docs/swarm.md).

### Full Swarm deployment

For the full Swarm flow — provider configuration, building the VM image with `buildx`, launching individual VMs on bootstrapped hosts, and the GCP/Terraform variant — see [docs/swarm.md](docs/swarm.md).

---

## Requirements

Check these before running the [Quick start](#quick-start). Supported OS versions are enforced by the bootstrap scripts; CPU/BIOS settings are verified during bootstrap.

### Common

- **OS:** Ubuntu LTS — **24.04 LTS** or **26.04 LTS** for both Intel TDX and AMD SEV-SNP (**26.04 LTS** recommended).
- **Privileges:** `root` (run with `sudo`).
- **Network:** outbound HTTPS to GitHub, AMD/Intel download servers, and the Ubuntu archive.
- **Memory / CPU:** enough headroom to run a VM. Defaults of `start_super_protocol.sh` reserve `nproc - 2` cores and `RAM − 8 GiB` for the guest.
- **Disk:** ≥ 512 GiB free for the guest state disk (auto-sized, but never less than 512 GiB).
- **IOMMU:** enabled in BIOS/UEFI (required for GPU passthrough).
- **Confidential GPU (optional):** supported NVIDIA GPUs with CC mode — **H100**, **H200**, **B200**, or **RTX 6000 Pro**.

### Intel TDX host

CPU: Intel Xeon with **TDX** support — **Sapphire Rapids**, **Emerald Rapids**, **Sierra Forest**, or **Granite Rapids**. Newer Intel family/model values are handled by the bootstrap fallback with the latest known TDX module.

BIOS settings:

| Setting | Value |
|---|---|
| `CPU PA limit to 46 bits` | Disabled |
| `SMT` | Enabled |
| `TXT` | Optional for TDX; status is reported but does not block setup |
| `SGX` | Enabled |
| `TME` | Enabled |
| `TME-MT (Multi-Tenant)` | Enabled, KeyIDs configured (non-zero key split) |
| `SEAM Loader` | Enabled |
| `TDX` | Enabled |

> **First boot:** in `Software Guard Extension (SGX)` settings, it's recommended to set `SGX Factory Reset` and `SGX Auto MP Registration` to **Enabled** for the initial run. They can be set back to `Disabled` afterwards.

### AMD SEV-SNP host

CPU: AMD EPYC with **SEV-SNP** support — **Milan (7xx3)**, **Genoa (9xx4)**, or **Turin (9xx5)**.

BIOS settings:

| Setting | Value |
|---|---|
| `SEV-SNP` | Enabled |
| `SMEE / Memory Encryption` | Enabled |
| `IOMMU` | Enabled |
| SEV / SEV-ES / SEV-SNP ASIDs | Sufficient allocation |

---

## Roadmap

Planned hardware support. These items are **not yet supported** and are listed for transparency only.

| Hardware | Type | Status |
|---|---|---|
| NVIDIA B300 (CC mode) | Confidential GPU | 📋 Planned |
| NVIDIA Rubin (CC mode) | Confidential GPU | 📋 Planned |

> This roadmap reflects current intentions and is subject to change. It does not constitute a commitment to deliver support for any hardware or feature, nor to any timeline.

---

## License

See [LICENSE](LICENSE).
