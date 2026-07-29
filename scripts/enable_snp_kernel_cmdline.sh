#!/bin/bash
set -euo pipefail
export LC_ALL=C

SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/common.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root: sudo $0" >&2
    exit 1
fi

if [ ! -r /etc/os-release ]; then
    echo "ERROR: cannot read /etc/os-release" >&2
    exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then
    echo "ERROR: this script supports Ubuntu only (found: ${PRETTY_NAME:-unknown})." >&2
    exit 1
fi

if [ "$(lscpu | awk -F: '/^Vendor ID:/{gsub(/^[[:space:]]+/, "", $2); print $2}')" != "AuthenticAMD" ]; then
    echo "ERROR: SEV-SNP requires an AMD CPU." >&2
    exit 1
fi

if [ ! -f /etc/default/grub ]; then
    echo "ERROR: /etc/default/grub does not exist." >&2
    exit 1
fi

backup="/etc/default/grub.backup.$(date +%Y%m%d_%H%M%S)"
cp -a /etc/default/grub "${backup}"
echo "GRUB backup: ${backup}"

cpu_family="$(lscpu | awk -F: '/^CPU family:/{gsub(/[[:space:]]/, "", $2); print $2}')"
cpu_model="$(lscpu | awk -F: '/^Model:/{gsub(/[[:space:]]/, "", $2); print $2}')"
firmware_version=""
firmware_stem=""
firmware_target=""

if ! [[ "${cpu_family}" =~ ^[0-9]+$ && "${cpu_model}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: cannot determine numeric CPU family/model from lscpu." >&2
    exit 1
fi

if [ "${cpu_family}" = "25" ] && [ "${cpu_model}" -ge 0 ] && [ "${cpu_model}" -le 15 ]; then
    firmware_version="1.58.02"
    firmware_stem="amd_sev_fam19h_model0xh"
    firmware_target="amd_sev_fam19h_model0xh.sbin"
    platform="Milan"
elif [ "${cpu_family}" = "25" ] && [ "${cpu_model}" -ge 16 ] && [ "${cpu_model}" -le 31 ]; then
    firmware_version="1.58.02"
    firmware_stem="amd_sev_fam19h_model1xh"
    firmware_target="amd_sev_fam19h_model1xh.sbin"
    platform="Genoa/Bergamo/Siena"
elif [ "${cpu_family}" = "26" ] && [ "${cpu_model}" -ge 0 ] && [ "${cpu_model}" -le 15 ]; then
    firmware_version="1.58.06"
    firmware_stem="amd_sev_fam1ah_model0xh"
    firmware_target="amd_sev_fam1ah_model0xh.sbin"
    platform="Turin"
else
    echo "ERROR: unsupported AMD CPU family/model: ${cpu_family:-unknown}/${cpu_model:-unknown}." >&2
    echo "SEV firmware was not changed." >&2
    exit 1
fi

echo "Detected platform: ${platform} (family ${cpu_family}, model ${cpu_model})"
echo "Installing AMD SEV firmware ${firmware_version}..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y unzip wget

firmware_archive="${firmware_stem}_${firmware_version}.zip"
firmware_url="https://download.amd.com/developer/eula/sev/${firmware_archive}"
firmware_tmp="$(mktemp -d)"
trap 'rm -rf "${firmware_tmp}"' EXIT

wget -O "${firmware_tmp}/${firmware_archive}" "${firmware_url}"
unzip -j "${firmware_tmp}/${firmware_archive}" \
    "${firmware_stem}_${firmware_version}.sbin" -d "${firmware_tmp}"

install -d -m 0755 /lib/firmware/amd
if [ -f "/lib/firmware/amd/${firmware_target}" ]; then
    firmware_backup="/lib/firmware/amd/${firmware_target}.backup.$(date +%Y%m%d_%H%M%S)"
    cp -a "/lib/firmware/amd/${firmware_target}" "${firmware_backup}"
    echo "Firmware backup: ${firmware_backup}"
fi
install -m 0644 "${firmware_tmp}/${firmware_stem}_${firmware_version}.sbin" \
    "/lib/firmware/amd/${firmware_target}"
echo "Installed: /lib/firmware/amd/${firmware_target}"

params=(
    mem_encrypt=on
    kvm_amd.sev=1
    kvm_amd.sev_es=1
    kvm_amd.sev_snp=1
    amd_iommu=on
    iommu=pt
)

for param in "${params[@]}"; do
    ensure_cmdline_param "${param}"
done

echo "Configured kernel command line:"
grep '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub

if command -v update-grub2 >/dev/null 2>&1; then
    update-grub2
elif command -v update-grub >/dev/null 2>&1; then
    update-grub
else
    echo "ERROR: update-grub is not installed." >&2
    exit 1
fi

update-initramfs -u

if [ ! -e /sys/module/kvm_amd/parameters/sev_snp ]; then
    echo
    echo "WARNING: the running kernel does not expose kvm_amd.sev_snp."
    echo "If it is still absent after reboot, install an SNP-capable host kernel."
fi

echo
echo "Done. Reboot the host:"
echo "  sudo reboot"
echo
echo "After reboot, verify:"
echo "  cat /proc/cmdline"
echo "  for p in sev sev_es sev_snp; do printf '%-8s ' \"\$p\"; cat \"/sys/module/kvm_amd/parameters/\$p\" 2>/dev/null || echo 'not exposed'; done"
echo "  sudo dmesg | grep -iE 'SEV-SNP|RMP|kvm_amd' | tail -50"
