# sp-vm-tools

Utilities for bootstrapping a Confidential Computing host (Intel **TDX** or AMD **SEV-SNP**) and launching Super Protocol confidential VMs on it.

## Contents

- [What's in here](#whats-in-here)
- [Quick start](#quick-start) — bootstrap a host and launch your first VM
  - [1. Clone the repo](#1-clone-the-repo)
  - [2. Bootstrap the host](#2-bootstrap-the-host)
  - [3. Reboot](#3-reboot)
  - [4. Verify the host](#4-verify-the-host)
  - [5. Launch a confidential VM](#5-launch-a-confidential-vm)
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
| `scripts/swarm-cluster.sh` | Bring up a 3-node Swarm cluster on a single host. |
| `scripts/check_configuration.sh`, `get_super_running_vms.sh` | Auxiliary tooling. |

---

## Quick start

This is the main path: take a bare Ubuntu host, turn it into a confidential hypervisor, and launch your first VM. Before you begin, confirm your hardware and BIOS match the [Requirements](#requirements) — the bootstrap scripts enforce the supported OS versions and will check CPU/BIOS settings for you.

> All scripts run as `root` and need an internet connection. A reboot is required partway through.

### 1. Clone the repo

On the target host:

```bash
git clone https://github.com/Super-Protocol/sp-vm-tools.git
cd sp-vm-tools
```

### 2. Bootstrap the host

Pick the script that matches your CPU vendor.

#### Intel TDX

```bash
sudo ./scripts/bootstrap_tdx.sh
```

What it does:

1. Verifies Ubuntu version and root privileges.
2. Runs `setup_tdx.sh` to install the Canonical TDX 3.3 stack and PCCS attestation host components.
3. Verifies BIOS/CPU TDX settings (TME, TME-MT, SEAM, TXT, SGX, …).
4. Runs the official `setup-tdx-host.sh` from `canonical/tdx`.
5. Updates the Intel TDX-Module to a known-good version.
6. Configures NVIDIA GPUs for Confidential Computing (CC mode + `vfio-pci` binding) and, on B200 systems, sets up ConnectX-7 bridges for VFIO passthrough.

> **Note:** Some steps require manual action to take effect. The script may stop and ask you to do something, then re-run `bootstrap_tdx.sh` — this is expected. Follow the on-screen instructions and run the same command again to finish.

#### AMD SEV-SNP

```bash
sudo ./scripts/bootstrap_snp.sh
```

What it does:

1. Verifies Ubuntu version, root privileges, and detects the EPYC generation (Milan / Genoa / Turin).
2. Installs the SEV-SNP hypervisor stack: bundled kernel/QEMU from `package-snp.tar.gz` in release `42-snp` on Ubuntu 24.04, or distro QEMU on newer Ubuntu releases.
3. Downloads and installs the matching AMD SEV firmware blob to `/lib/firmware/amd/` and reloads `ccp` / `kvm_amd`.
4. Runs SNP status checks (RMP table, SEV / SEV-SNP API versions, ASID allocation, IOMMU groups, hugepages, CPU governor).
5. Configures NVIDIA GPUs for CC mode and binds them to `vfio-pci`.

> **Ubuntu 24.04 note:** the SNP bootstrap installs a bundled Linux **6.16** kernel. On some systems, network interfaces may be renamed after reboot, which can affect networking and remote SSH access. Make sure you have iKVM or other interactive console access before rebooting, so you can reconfigure networking for the new interface names if needed.

### 3. Reboot

```bash
sudo reboot
```

After reboot, re-run the same bootstrap script if it asks you to — some steps (firmware, kernel parameters, VFIO bindings) only take effect after a reboot.

### 4. Verify the host

```bash
sudo ./scripts/check_configuration.sh
```

This prints a hardware overview (CPU, memory, network, disks, RAID/SMART) you can compare against the [Requirements](#requirements).

## Running a Swarm cluster

There are two ways to run a Super Protocol Swarm cluster.

### Single-host cluster (quick start)

`scripts/swarm-cluster.sh` brings up a **3-node Swarm cluster on a single host** — no multi-machine setup. It creates an isolated bridge network, launches one bootstrap + two join VMs in separate `tmux` sessions, auto-configures provider configs, and sets up ingress via HAProxy. You still need to set `gateway_hostname` in the provider template to point to the machine's public IP.

```bash
# Start
sudo ./scripts/swarm-cluster.sh up --provider-config-template ./provider-template --release build-370

# Status
./scripts/swarm-cluster.sh status

# Stop
sudo ./scripts/swarm-cluster.sh down
```

Prerequisites: a bootstrapped host (TDX or SEV-SNP), a populated provider config template (see [config.yaml reference](docs/swarm.md#configyaml-reference) for an example), and `tmux` / `nftables` / `curl` installed. See `./scripts/swarm-cluster.sh --help` (or the header comment in the script) for all flags — `--join-cores`, `--join-mem`, `--release`, `--gpu-target`, etc.

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
| `TXT` | Enabled |
| `SGX` | Enabled |
| `TME` | Enabled |
| `TME-MT (Multi-Tenant)` | Enabled, KeyIDs configured (non-zero key split) |
| `SEAM Loader` | Enabled |
| `TDX` | Enabled |

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
