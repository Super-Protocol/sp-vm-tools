#!/bin/bash
set -euo pipefail
export LC_ALL=C

SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

usage() {
    cat <<'EOF'
Configure all NVIDIA GPUs on an Ubuntu 22.04 SEV-SNP host for confidential
VM passthrough (NVIDIA CC mode + vfio-pci).

Usage:
  sudo ./scripts/setup_snp_gpu_ubuntu22.sh [--yes]

Options:
  -y, --yes  Do not ask for confirmation.
  -h, --help Show this help.

The SEV-SNP host kernel must already be installed and active. This script does
not install an SNP-capable kernel or QEMU.
EOF
}

ASSUME_YES=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        -y|--yes) ASSUME_YES=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: Run this script as root (sudo $0)." >&2
    exit 1
fi

if [ ! -r /etc/os-release ]; then
    echo "ERROR: /etc/os-release is missing." >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "22.04" ]; then
    echo "ERROR: This helper supports Ubuntu 22.04 only." >&2
    echo "Current OS: ${PRETTY_NAME:-unknown}" >&2
    exit 1
fi

if [ "$(lscpu | awk -F: '/^Vendor ID:/{gsub(/^[[:space:]]+/, "", $2); print $2}')" != "AuthenticAMD" ]; then
    echo "ERROR: SEV-SNP requires an AMD host CPU." >&2
    exit 1
fi

SNP_PARAM=/sys/module/kvm_amd/parameters/sev_snp
if [ ! -r "${SNP_PARAM}" ] || [ "$(tr '[:lower:]' '[:upper:]' < "${SNP_PARAM}")" != "Y" ]; then
    echo "ERROR: kvm_amd.sev_snp is not active." >&2
    echo "Install and boot an Ubuntu 22.04-compatible SEV-SNP host kernel first." >&2
    exit 1
fi

echo "Installing GPU setup prerequisites..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git pciutils python3

mapfile -t GPUS < <(lspci -Dnn -d 10de: | awk '/VGA compatible controller|3D controller/{print $1}')
if [ "${#GPUS[@]}" -eq 0 ]; then
    echo "ERROR: No NVIDIA VGA/3D controllers found." >&2
    exit 1
fi

print_section_header "NVIDIA GPUs"
for gpu in "${GPUS[@]}"; do
    lspci -Dnn -s "${gpu}"
done

echo
echo "WARNING: all NVIDIA host drivers will be blacklisted and every GPU listed"
echo "above will be configured for confidential VM passthrough. Local graphics"
echo "and host CUDA workloads on these GPUs will stop working after reboot."
if [ "${ASSUME_YES}" != true ]; then
    read -r -p "Continue? [y/N] " answer
    case "${answer}" in
        y|Y|yes|YES) ;;
        *) echo "Cancelled."; exit 0 ;;
    esac
fi

if [ ! -d /sys/kernel/iommu_groups ] \
    || ! find /sys/kernel/iommu_groups -mindepth 2 -maxdepth 2 -type l -print -quit | grep -q .; then
    print_section_header "Enable AMD IOMMU"
    ensure_cmdline_param "amd_iommu=on"
    ensure_cmdline_param "iommu=pt"
    if command -v update-grub2 >/dev/null 2>&1; then
        update-grub2
    else
        update-grub
    fi
    echo "IOMMU parameters were added to GRUB. Reboot, then run this script again."
    exit 2
fi

for gpu in "${GPUS[@]}"; do
    sysfs_gpu="/sys/bus/pci/devices/${gpu}"
    if [ ! -L "${sysfs_gpu}/iommu_group" ]; then
        echo "ERROR: GPU ${gpu} has no IOMMU group." >&2
        exit 1
    fi
done

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

print_section_header "NVIDIA CC mode and VFIO"
setup_nvidia_gpus "${TMP_DIR}"

print_section_header "Result"
echo "GPU confidential-computing mode and persistent vfio-pci binding are configured."
echo "Reboot the host, then verify with:"
echo "  lspci -Dnnk -d 10de:"
echo "Each passthrough GPU must show: Kernel driver in use: vfio-pci"
