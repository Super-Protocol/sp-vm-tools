# sp-vm-tools

Utilities for bootstrapping a Confidential Computing host (Intel **TDX** or AMD **SEV-SNP**) and launching Super Protocol confidential VMs on it.

The repository provides:

- `scripts/bootstrap_tdx.sh` — turns an Ubuntu host into a TDX-capable hypervisor (kernel, QEMU, OVMF, attestation, GPU passthrough).
- `scripts/bootstrap_snp.sh` — turns an Ubuntu host into a SEV-SNP-capable hypervisor (firmware, modules, GPU passthrough).
- `scripts/start_super_protocol.sh` — starts a confidential VM (TDX / SEV-SNP / untrusted) using a Super Protocol release image.
- `scripts/check_configuration.sh`, `scripts/get_super_running_vms.sh`, `scripts/create_provider_offer.sh` — auxiliary tooling.

## Minimum Requirements

### Common

- **OS:** Ubuntu **25.04 or newer** on the host (enforced by the bootstrap scripts).
- **Privileges:** `root` (run with `sudo`).
- **Network:** outbound HTTPS to GitHub, AMD/Intel download servers and the Ubuntu archive.
- **Memory / CPU:** enough headroom to run a VM. Defaults of `start_super_protocol.sh` reserve `nproc - 2` cores and `RAM - 8 GiB` for the guest.
- **Disk:** ≥ 512 GiB free for the guest state disk (auto-sized, but not less than 512 GiB).
- **IOMMU enabled** in BIOS/UEFI (required for GPU passthrough).

### Intel TDX host

- Intel Xeon CPU with **TDX** support (Sapphire Rapids / Emerald Rapids / Granite Rapids generation).
- BIOS configured with:
  - `CPU PA limit to 46 bits` — **Disabled**
  - `SMT` — **Enabled**
  - `TXT` — **Enabled**
  - `SGX` — **Enabled**
  - `TME` — **Enabled**
  - `TME-MT (Multi-Tenant)` — **Enabled**, KeyIDs configured (non-zero key split)
  - `SEAM Loader` — **Enabled**
  - `TDX` — **Enabled**
- For confidential GPU workloads: NVIDIA H100/H200 (or B200 with ConnectX-7) in CC mode, present in its own IOMMU group.

### AMD SEV-SNP host

- AMD EPYC CPU: **Milan (7xx3)**, **Genoa (9xx4)** or **Turin (9xx5)**.
- BIOS configured with:
  - `SEV-SNP` — **Enabled**
  - `SMEE / Memory Encryption` — **Enabled**
  - `IOMMU` — **Enabled**
  - Sufficient SEV/SEV-ES/SEV-SNP ASIDs allocated
- For confidential GPU workloads: NVIDIA H100/H200 with CC mode support, present in its own IOMMU group.

## Bootstrapping the Host

> All scripts must run as `root` and require an internet connection. A reboot is needed at the end.

### 1. Clone the repo on the target host

```bash
git clone https://github.com/Super-Protocol/sp-vm-tools.git
cd sp-vm-tools
```

### 2a. Bootstrap an Intel TDX host

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

### 2b. Bootstrap an AMD SEV-SNP host

```bash
sudo ./scripts/bootstrap_snp.sh
```

What it does:

1. Verifies Ubuntu version, root privileges and detects the EPYC generation (Milan / Genoa / Turin).
2. Downloads and installs the matching AMD SEV firmware blob to `/lib/firmware/amd/` and reloads `ccp` / `kvm_amd`.
3. Runs SNP status checks (RMP table, SEV / SEV-SNP API versions, ASID allocation, IOMMU groups, hugepages, CPU governor).
4. Configures NVIDIA GPUs for CC mode and binds them to `vfio-pci`.

### 3. Reboot

```bash
sudo reboot
```

After reboot, re-run the same bootstrap script if it asks you to — some steps (firmware, kernel parameters, VFIO bindings) only take effect after a reboot.

### 4. Verify the host

```bash
sudo ./scripts/check_configuration.sh
```

This prints a hardware overview (CPU, memory, network, disks, RAID/SMART) you can compare against the requirements above.

## Running a Confidential VM

Once the host is bootstrapped and reboot-clean, start a Super Protocol VM:

```bash
sudo ./scripts/start_super_protocol.sh --mode tdx       # or --mode sev-snp
```

The script auto-detects the CPU when `--mode` is omitted. Common flags:

| Flag | Description |
|---|---|
| `--mode {tdx\|sev-snp\|untrusted}` | Confidential mode (auto-detected by default). |
| `--cores N` | vCPUs to assign (default: `nproc - 2`). |
| `--mem N` | RAM in GiB (default: host RAM − 8 GiB). |
| `--gpu <BDF>` | Pass a specific GPU; repeatable. `--gpu none` disables passthrough. |
| `--state_disk_path PATH` | Persistent state disk (default: `<cache>/state_disk.qcow2`). |
| `--state_disk_size SIZE` | State disk size (auto, ≥ 512 GiB). |
| `--release NAME` | Pin a `Super-Protocol/sp-vm` release (default: latest). |
| `--ssh_port`, `--http_port`, `--https_port`, `--wg_port` | Host port forwards. |

Run `./scripts/start_super_protocol.sh --help` for the full list.

To list running VMs started by this tool:

```bash
./scripts/get_super_running_vms.sh
```

## Running a Swarm VM

For the full Swarm flow — provider configuration, building the VM image with `buildx`, launching it on a bootstrapped host, and the GCP/Terraform variant — see [docs/swarm.md](docs/swarm.md).

## License

See [LICENSE](LICENSE).
