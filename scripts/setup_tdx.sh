#!/bin/bash

set -e

# Pull in shared helpers (install_debs, setup_grub, install_tdx_release_packages,
# update_tdx_module, ...). bootstrap_tdx.sh copies common.sh next to this script.
SCRIPT_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
source "${SCRIPT_DIR}/common.sh"

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Modify status indicators:
SUCCESS="[${GREEN}✓${NC}]"
FAILURE="[${RED}✗${NC}]"

print_section_header() {
    echo -e "\n${BLUE}=== $1 ===${NC}"
    echo -e "${BLUE}$(printf '=%.0s' {1..40})${NC}"
}

# Function for error handling
check_error() {
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: $1${NC}"
        exit 1
    fi
}

# Configuration variables
PCCS_API_KEY="aecd5ebb682346028d60c36131eb2d92"
PCCS_PORT="8081"
PCCS_PASSWORD="pccspassword123"
# Generate SHA512 hash of the password
USER_TOKEN=$(echo -n "${PCCS_PASSWORD}" | sha512sum | awk '{print $1}')

check_all_bios_settings() {
    local results=()
    local all_passed=true
    
    print_section_header "BIOS Configuration Check Results"
    echo "Checking all settings..."

    results+=("CPU PA Settings:")
    PA_BITS=$(cpuid -l 0x80000008 | grep "maximum physical address bits" | head -n1 | awk '{print $NF}' | tr -d '()' || echo "0")
    
    if [ "$PA_BITS" -gt "46" ]; then
        results+=("${SUCCESS} CPU PA limit properly configured${NC}")
    else
        results+=("${FAILURE} CPU PA limit to 46 bit must be disabled${NC}")
        results+=("  Location: Uncore General Configuration")
        all_passed=false
    fi

    # SMT check
    results+=("SMT Settings:")
    if [ "$(cat /sys/devices/system/cpu/smt/active)" = "1" ]; then
        results+=("${SUCCESS} SMT enabled${NC}")
    else
        results+=("${FAILURE} SMT not enabled in BIOS${NC}")
        results+=("  Required: Enable SMT in BIOS")
        all_passed=false
    fi

    # TME Check - checks both CPU support and actual enablement
    results+=("TME Settings:")
    
    # Check CPU support and actual TME status
    cpu_tme_support=false
    tme_active=false
    
    # Check CPU TME support
    if cat /proc/cpuinfo | grep -q "tme"; then
        cpu_tme_support=true
    fi
    
    # Check if TME is actually enabled via TDX initialization
    # (Modern TDX systems don't show direct TME messages, TME status is confirmed via TDX)
    if dmesg | grep -q "virt/tdx.*module initialized"; then
        tme_active=true
    fi
    
    # Final TME assessment
    if $cpu_tme_support && $tme_active; then
        results+=("${SUCCESS} Memory encryption (TME) enabled${NC}")
    elif $cpu_tme_support && ! $tme_active; then
        results+=("${WARNING} TME supported but not active${NC}")
        results+=("  Required: Enable TME and TDX in BIOS")
        # Uncomment next line if TME is mandatory
        # all_passed=false
    else
        results+=("${FAILURE} Memory encryption not supported or enabled${NC}")
        all_passed=false
    fi

    # TME-MT Check - checks Multi-Tenant TME support and enablement
    results+=("TME-MT Settings:")
    
    # Check if TME-MT is active via TDX module initialization and PAMT allocation
    # In modern TDX implementations, PAMT allocation indicates TME-MT is working
    tme_mt_active=false
    tdx_enabled=false
    
    # Check if TDX is enabled in KVM (indicates TME-MT capability)
    if [ -f /sys/module/kvm_intel/parameters/tdx ] && [ "$(cat /sys/module/kvm_intel/parameters/tdx)" = "Y" ]; then
        tdx_enabled=true
    fi
    
    # Check if PAMT is allocated (confirms TME-MT is working)
    if dmesg | grep -q "virt/tdx.*KB allocated for PAMT"; then
        tme_mt_active=true
        # Extract PAMT allocation info
        pamt_info=$(dmesg | grep "virt/tdx.*KB allocated for PAMT" | head -1 | sed 's/.*virt\/tdx: //' | sed 's/ KB allocated for PAMT//')
        results+=("  PAMT allocation: ${pamt_info} KB")
    fi
    
    # Final TME-MT assessment
    if $tdx_enabled && $tme_mt_active; then
        results+=("${SUCCESS} Multi-Tenant Memory encryption (TME-MT) enabled${NC}")
    else
        results+=("${FAILURE} TME-MT not enabled${NC}")
        if ! $tdx_enabled; then
            results+=("  Issue: TDX not enabled in KVM")
        fi
        if ! $tme_mt_active; then
            results+=("  Issue: PAMT not allocated")
        fi
        results+=("  Required: Enable TME-MT in BIOS and set non-zero key split")
        all_passed=false
    fi
    
    # SGX Check remains unchanged
    results+=("SGX Settings:")
    if grep -q "sgx" /proc/cpuinfo && [ -c "/dev/sgx_enclave" -o -c "/dev/sgx/enclave" ]; then
        results+=("${SUCCESS} SGX enabled and configured${NC}")
    else
        results+=("${FAILURE} SGX not properly configured${NC}")
        all_passed=false
    fi

    results+=("TXT Settings:")
    
    local sinit_base=""
    
    # 0) Does the CPU support SMX/TXT at all?
    if ! grep -qw smx /proc/cpuinfo; then
        results+=("${FAILURE} CPU does not support SMX/TXT${NC}")
        all_passed=false
    else
        # 1) Read SINIT.BASE directly from TXT public config space:
        #    0xFED30000 + 0x270 (this is what txt-stat used to do)
        sinit_base=$(od -An -tx4 -j $((0xFED30270)) -N4 /dev/mem 2>/dev/null | tr -d ' ')
        [ -n "$sinit_base" ] && sinit_base="0x${sinit_base}"
    
        # 2) Fallback: IA32_FEATURE_CONTROL MSR (0x3A), bit 15 = SENTER global enable.
        #    Set by BIOS when TXT is enabled. Used when /dev/mem is unavailable
        #    (e.g. kernel lockdown).
        if [ -z "$sinit_base" ] && command -v rdmsr >/dev/null 2>&1; then
            modprobe msr 2>/dev/null
            local senter_en
            senter_en=$(rdmsr -f 15:15 0x3a 2>/dev/null)
            if [ "$senter_en" = "1" ]; then
                results+=("${SUCCESS} TXT enabled (SENTER enabled in IA32_FEATURE_CONTROL)${NC}")
            else
                results+=("${FAILURE} TXT not enabled in BIOS${NC}")
                results+=("  Required: Enable TXT in BIOS")
                all_passed=false
            fi
            sinit_base="__msr_checked__"
        fi
    
        if [ "$sinit_base" != "__msr_checked__" ]; then
            # 0xffffffff means the chipset does not decode the TXT region => TXT disabled.
            # Empty value means we could not read /dev/mem at all.
            if [ -n "$sinit_base" ] && [ "$sinit_base" != "0x0" ] && \
               [ "$sinit_base" != "0x00000000" ] && [ "$sinit_base" != "0xffffffff" ]; then
                results+=("${SUCCESS} TXT enabled (SINIT.BASE = $sinit_base)${NC}")
            else
                results+=("${FAILURE} TXT not enabled in BIOS${NC}")
                results+=("  Required: Enable TXT in BIOS")
                all_passed=false
            fi
        fi
    fi
    results+=("SEAM Settings:")
    if dmesg | grep -q "virt/tdx: module initialized" && \
       dmesg | grep -q "virt/tdx: BIOS enabled"; then
        results+=("${SUCCESS} SEAM loader enabled and functioning${NC}")
        local tdx_cap_msr=$(rdmsr -X 0x982 2>/dev/null || echo "0")
        results+=("  MSR 0x982: ${tdx_cap_msr} (for reference only)")
    else
        results+=("${FAILURE} SEAM loader not enabled or not functioning properly${NC}")
        results+=("  Required: Enable SEAM Loader in BIOS")
        all_passed=false
    fi

    results+=("TDX Settings:")
    if dmesg | grep -q "virt/tdx: BIOS enabled"; then
        results+=("${SUCCESS} TDX supported and initialized${NC}")
        
        local pamt_alloc=$(dmesg | grep -i "KB allocated for PAMT" || echo "")
        if [ ! -z "$pamt_alloc" ]; then
            results+=("${SUCCESS} PAMT allocation successful: $(echo $pamt_alloc | grep -o '[0-9]* KB')${NC}")
        fi
        
        if dmesg | grep -q "virt/tdx: module initialized"; then
            results+=("${SUCCESS} TDX module initialized${NC}")
        fi
    else
        results+=("${FAILURE} TDX not properly configured on host${NC}")
        results+=("  Required: Enable TDX in BIOS")
        all_passed=false
    fi
    
    # Check if tdx kernel module is loaded
    if [ -e "/sys/firmware/acpi/tables/TDEL" ] && ! lsmod | grep -q "^tdx"; then
        results+=("${FAILURE} TDX kernel module not loaded${NC}")
        all_passed=false
    fi
        
    # Configuration requirements section remains unchanged
    results+=("${YELLOW}Required BIOS Configuration:${NC}")
    results+=("• Core Security:")
    results+=("  - CPU PA: Limit to 46 bits Disable")
    results+=("  - TXT: Enable")
    results+=("  - SGX: Enable")
    results+=("  - SMT: Enable")
    results+=("• Memory Protection:")
    results+=("  - TME: Enable")
    results+=("  - TME Multi-Tenant: Enable")
    results+=("  - KeyIDs configuration: Present")
    results+=("• TDX Components:")
    results+=("  - TDX: Enable")
    results+=("  - SEAM Loader: Enable")

    print_section_header "Status"
    for result in "${results[@]}"; do
        echo -e "$result"
    done
    if [ "$all_passed" = true ]; then
        echo -e "\n${SUCCESS} All settings properly configured${NC}"
        return 0
    else
        echo -e "\n${FAILURE} Some settings need attention${NC}"
        return 1
    fi
}

# Whether the CURRENTLY RUNNING kernel has Intel TDX host support. The BIOS/TDX
# flag verification relies on a TDX-capable running kernel (dmesg "virt/tdx",
# kvm_intel.tdx, ...). On a freshly bootstrapped host the TDX kernel is only
# installed (not yet booted), so this returns false until the reboot.
running_kernel_supports_tdx() {
    # 1) running kernel built with TDX host support
    if [ -f "/boot/config-$(uname -r)" ] && \
       grep -q '^CONFIG_INTEL_TDX_HOST=y' "/boot/config-$(uname -r)"; then
        return 0
    fi
    # 2) kvm_intel exposes the tdx parameter (only on TDX-capable kernels)
    if [ -e /sys/module/kvm_intel/parameters/tdx ]; then
        return 0
    fi
    # 3) TDX subsystem came up at boot
    if dmesg 2>/dev/null | grep -q "virt/tdx"; then
        return 0
    fi
    return 1
}

check_bios_settings() {
    echo "Performing comprehensive BIOS configuration check..."
    echo "Installing components..."
    apt-get update && apt-get install -y msr-tools cpuid
    
    # Load the msr module if not loaded
    if ! lsmod | grep -q "^msr"; then
        echo "Loading MSR module..."
        modprobe msr
    fi

    # Run all checks at once
    check_all_bios_settings
    return $?
}

# On Ubuntu 24.04 the matched TDX kernel + QEMU are installed from the
# sp-vm-tools release archive (package-tdx.tar.gz): a custom kernel plus
# sp-qemu-tdx (QEMU 9.x + Intel TDX device-passthrough patches, with iommufd).
# They share the same TDX KVM ABI; the stock 24.04 kernel + PPA QEMU (8.2.2) do
# not provide iommufd / a compatible interface.
QEMU_RELEASE_REPO="Super-Protocol/sp-vm-tools"
QEMU_RELEASE_TAG="38-tdx+snp"          # tag carrying package-tdx.tar.gz
QEMU_RELEASE_ASSET="package-tdx.tar.gz"

# Resolve the TDX SEAM module version to install for the host CPU.
# Prints the version string (e.g. "2.0.14") to stdout; all human-readable
# progress goes to stderr so callers can capture the version cleanly.
# Returns non-zero on an unsupported (older / non-Intel) CPU.
detect_tdx_module_version() {
  local vendor family model codename version
  # Newest known TDX module branch. Used as the fallback for unrecognized CPUs
  # that appear to be newer than anything in the map below.
  local TDX_MODULE_LATEST="2.0.14"

  vendor=$(awk -F: '/^vendor_id/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo)
  family=$(awk -F: '/^cpu family/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo)
  # Match the bare "model\t\t: N" line, not "model name".
  model=$(awk -F: '/^model[[:space:]]*:/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo)

  if [ "$vendor" != "GenuineIntel" ]; then
    echo "ERROR: Unsupported CPU vendor '${vendor}' (Intel TDX requires GenuineIntel)" >&2
    return 1
  fi

  if [ -z "$family" ] || [ -z "$model" ]; then
    echo "ERROR: Could not determine CPU family/model from /proc/cpuinfo" >&2
    return 1
  fi

  # Anything beyond family 6 is necessarily newer than the current map.
  if [ "$family" -gt 6 ]; then
    echo "WARNING: Unrecognized newer CPU (family ${family} model ${model}); using latest TDX module ${TDX_MODULE_LATEST}" >&2
    echo "${TDX_MODULE_LATEST}"
    return 0
  fi

  if [ "$family" -ne 6 ]; then
    echo "ERROR: Unsupported CPU (family ${family} model ${model}); Intel TDX requires Sapphire Rapids or newer" >&2
    return 1
  fi

  case "$model" in
    143) codename="Sapphire Rapids"; version="1.5.24" ;;  # 0x8F
    207) codename="Emerald Rapids";  version="1.5.24" ;;  # 0xCF
    175) codename="Sierra Forest";   version="1.5.25" ;;  # 0xAF
    173) codename="Granite Rapids";  version="2.0.14" ;;  # 0xAD
    *)
      if [ "$model" -gt 175 ]; then
        echo "WARNING: Unrecognized newer CPU (family 6 model ${model}); using latest TDX module ${TDX_MODULE_LATEST}" >&2
        echo "${TDX_MODULE_LATEST}"
        return 0
      fi
      echo "ERROR: Unsupported CPU (family 6 model ${model})." >&2
      echo "Supported: Sapphire Rapids (143), Emerald Rapids (207), Sierra Forest (175), Granite Rapids (173)." >&2
      return 1
      ;;
  esac

  echo "Detected CPU: ${codename} (family ${family} model ${model}) -> TDX module ${version}" >&2
  echo "${version}"
}

update_tdx_module() {
  local TMP_DIR=$1
  local version
  version=$(detect_tdx_module_version) || exit 1
  echo "Updating TDX-module to ${version}..."
  pushd "${TMP_DIR}"
  wget "https://github.com/intel/confidential-computing.tdx.tdx-module/releases/download/TDX_MODULE_${version}/intel_tdx_module.tar.gz"
  tar -xvzf intel_tdx_module.tar.gz
  mkdir -p /boot/efi/EFI/TDX/
  cp -vf TDX-Module/intel_tdx_module.so /boot/efi/EFI/TDX/TDX-SEAM.so
  cp -vf TDX-Module/intel_tdx_module.so.sigstruct /boot/efi/EFI/TDX/TDX-SEAM.so.sigstruct
  popd
}

install_tdx_release_packages() {
  local tmp_dir="$1"

  # The bundled debs (custom TDX kernel + sp-qemu-tdx, with iommufd / device
  # passthrough) are built for Ubuntu 24.04 (Noble), whose stock kernel + PPA
  # QEMU (8.2.2) lack what we need. The kernel and QEMU are a matched pair (same
  # TDX KVM ABI). Newer Ubuntu (24.10 / 25.04+) already ship a suitable kernel
  # and QEMU >= 9 with iommufd, so the bundle is neither needed nor
  # binary-compatible there -- install it only on 24.04.
  local ubuntu_version=""
  [ -f /etc/os-release ] && ubuntu_version=$(. /etc/os-release && echo "$VERSION_ID")
  if [ "$ubuntu_version" != "24.04" ]; then
    echo "Ubuntu ${ubuntu_version:-unknown}: skipping bundled TDX kernel/QEMU (distro stack is used)"
    return 0
  fi

  local work="${tmp_dir}/tdx-pkg"
  # URL-encode the '+' in the tag for the direct download URL.
  local tag_enc="${QEMU_RELEASE_TAG//+/%2B}"
  local url="https://github.com/${QEMU_RELEASE_REPO}/releases/download/${tag_enc}/${QEMU_RELEASE_ASSET}"

  echo "Installing TDX kernel + QEMU from ${QEMU_RELEASE_REPO}@${QEMU_RELEASE_TAG}..."
  mkdir -p "${work}"
  echo "Downloading ${QEMU_RELEASE_ASSET} (~160 MB), this may take a while..."
  wget -O "${work}/${QEMU_RELEASE_ASSET}" "${url}"
  echo "Download complete: ${work}/${QEMU_RELEASE_ASSET}"
  tar -xzf "${work}/${QEMU_RELEASE_ASSET}" -C "${work}"

  # Install ALL packages from the archive: custom kernel, headers, sp-qemu-tdx.
  # install_debs (common.sh) installs libslirp0, the kernel and the remaining
  # debs, and sets NEW_KERNEL_VERSION.
  install_debs "${work}"

  # Boot the freshly installed custom kernel and enable TDX in KVM. setup_grub
  # (common.sh) sets it as default and adds "kvm_intel.tdx=on" to the kernel
  # command line -- without which kvm_intel loads without TDX and QEMU fails
  # with "vm-type TDX not supported by KVM" (Canonical's 3.x setup no longer
  # sets this flag).
  setup_grub "${NEW_KERNEL_VERSION}" tdx
}

TMP_DIR=$1
TDX_REF="3.3"

check_tdx_os_version() {
    local min_version="24.04"
    local min_num
    min_num=$(echo "$min_version" | awk -F. '{printf "%d%02d", $1, $2}')

    if [ ! -f /etc/os-release ]; then
        echo -e "${RED}ERROR: Could not determine OS version${NC}"
        echo "This script requires Ubuntu ${min_version} or higher."
        exit 1
    fi

    . /etc/os-release

    if [ "$ID" != "ubuntu" ]; then
        echo -e "${RED}ERROR: Unsupported operating system${NC}"
        echo "This script requires Ubuntu ${min_version} or higher."
        echo "Current OS: $PRETTY_NAME"
        exit 1
    fi

    local version_num
    version_num=$(echo "$VERSION_ID" | awk -F. '{printf "%d%02d", $1, $2}')

    if [ "$version_num" -lt "$min_num" ]; then
        echo -e "${RED}ERROR: Unsupported Ubuntu version${NC}"
        echo "This script requires Ubuntu ${min_version} or higher."
        echo "Current version: $PRETTY_NAME"
        echo "Please upgrade your system to continue."
        exit 1
    fi
}

check_tdx_os_version

# Determine package source based on Ubuntu version
UBUNTU_VERSION=$(. /etc/os-release && echo "$VERSION_ID")
UBUNTU_NUM=$(echo "$UBUNTU_VERSION" | awk -F. '{printf "%d%02d", $1, $2}')
CODENAME=$(lsb_release -cs)

if [ "$UBUNTU_NUM" -ge 2510 ]; then
    USE_INTEL_REPO=1
    echo "Ubuntu ${UBUNTU_VERSION}: using Intel SGX repository"
else
    USE_INTEL_REPO=0
    echo "Ubuntu ${UBUNTU_VERSION}: using Canonical kobuk-team PPA"
fi

if [ -d "${TMP_DIR}/tdx-cannonical" ]; then
    echo -e "${YELLOW}Directory ${TMP_DIR}/tdx-cannonical already exists${NC}"
    echo -e "Removing existing directory..."
    rm -rf "${TMP_DIR}/tdx-cannonical"
fi

# Download the setup-attestation-host.sh script
git clone https://github.com/canonical/tdx.git "${TMP_DIR}/tdx-cannonical"
SCRIPT_PATH=${TMP_DIR}/tdx-cannonical/setup-tdx-host.sh
# Check for download errors
if [ $? -ne 0 ]; then
    echo "Failed to download the setup-tdx-host.sh script."
    exit 1
fi

git -C "${TMP_DIR}/tdx-cannonical" checkout --detach "${TDX_REF}"
if [ $? -ne 0 ]; then
    echo "Failed to checkout tdx ref ${TDX_REF}."
    exit 1
fi

# Make the script executable
echo "Running setup-tdx-host.sh..."
chmod +x "${SCRIPT_PATH}"
"${SCRIPT_PATH}"

# Install our hypervisor + kernel (custom kernel + sp-qemu-tdx) and TDX module
# right after the canonical host setup, so our kernel becomes the default boot
# entry and the BIOS verification below can run against it (after a reboot).
print_section_header "Installing hypervisor and kernel..."
install_tdx_release_packages "${TMP_DIR}"
update_tdx_module "${TMP_DIR}"

print_section_header "Configuring package repositories..."

if [ "$USE_INTEL_REPO" -eq 1 ]; then
    # Remove kobuk-team PPA if present (conflicts with Intel packages)
    PPA_FILES=$(grep -rl "kobuk-team" /etc/apt/sources.list.d/ 2>/dev/null || true)
    if [ -n "$PPA_FILES" ]; then
        echo "Removing kobuk-team PPA: $PPA_FILES"
        rm -f $PPA_FILES
    fi

    # Add Intel SGX repository
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.01.org/intel-sgx/sgx_repo/ubuntu/intel-sgx-deb.key \
        -o /etc/apt/keyrings/intel-sgx-keyring.asc
    check_error "Failed to download Intel SGX repository key"

    tee /etc/apt/sources.list.d/intel-sgx.list > /dev/null <<EOF
deb [signed-by=/etc/apt/keyrings/intel-sgx-keyring.asc arch=amd64] https://download.01.org/intel-sgx/sgx_repo/ubuntu ${CODENAME} main
EOF
else
    # Ensure kobuk-team PPA is present
    if ! grep -rq "kobuk-team" /etc/apt/sources.list.d/ 2>/dev/null; then
        add-apt-repository -y ppa:kobuk-team/tdx-release
        check_error "Failed to add kobuk-team PPA"
    fi
fi

apt-get update
check_error "Failed to update package lists"

print_section_header "BIOS Configuration Verification"
if ! running_kernel_supports_tdx; then
    echo -e "${RED}Running kernel $(uname -r) has no active TDX support.${NC}"
    echo "The TDX kernel is installed but not booted yet."
    echo "Reboot into the TDX kernel and re-run the bootstrap to continue"
    echo "(BIOS verification, attestation and PCCS registration)."
    exit 2   # distinct code: "reboot required", not a failure
fi
if ! check_bios_settings; then
    echo -e "${RED}ERROR: Required BIOS settings are not properly configured${NC}"
    echo "Please configure BIOS settings according to the instructions above and try again"
    exit 1
fi

# Function to wait for service
wait_for_service() {
    local service=$1
    local max_attempts=30
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if systemctl is-active --quiet $service; then
            echo -e "${GREEN}$service is up${NC}"
            return 0
        fi
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done
    echo -e "${RED}$service failed to start${NC}"
    return 1
}

register_platform() {
    local csv_file="pckid_retrieval.csv"
    
    if [ ! -f "$csv_file" ]; then
        echo -e "${RED}PCK ID retrieval file not found${NC}"
        return 1
    fi

    # Register with PCCS using password
    echo -e "${GREEN}Registering with PCCS...${NC}"
    PCKIDRetrievalTool \
        -url "https://localhost:${PCCS_PORT}" \
        -use_secure_cert false \
        -user_token "${PCCS_PASSWORD}"
    
    return $?
}

remove_pccs() {
    print_section_header "Removing existing PCCS installation..."

    # Check package state in dpkg database (any state: ii, iF, iU, rc...)
    local state
    state=$(dpkg-query -W -f='${db:Status-Abbrev}' sgx-dcap-pccs 2>/dev/null || true)

    if [ -n "$state" ] && [ "$state" != "un " ]; then
        echo "Found sgx-dcap-pccs in state '${state}', purging..."

        # Try normal purge first
        if ! dpkg --purge --force-all sgx-dcap-pccs 2>/dev/null; then
            # Maintainer scripts are failing — neutralize them and retry
            echo "Purge failed, neutralizing maintainer scripts..."
            local f
            for f in /var/lib/dpkg/info/sgx-dcap-pccs.{prerm,postrm,postinst,preinst}; do
                if [ -f "$f" ]; then
                    printf '#!/bin/sh\nexit 0\n' > "$f"
                    chmod +x "$f"
                fi
            done
            dpkg --purge --force-all sgx-dcap-pccs
        fi

        # Verify it's actually gone
        state=$(dpkg-query -W -f='${db:Status-Abbrev}' sgx-dcap-pccs 2>/dev/null || true)
        if [ -n "$state" ] && [ "$state" != "un " ]; then
            echo -e "${RED}ERROR: Failed to purge sgx-dcap-pccs (state: ${state})${NC}"
            exit 1
        fi
        echo "Package purged successfully"
    else
        echo "Package sgx-dcap-pccs not installed, skipping purge"
    fi

    # Always remove the directory: PCCS generates files dpkg doesn't track
    # (pckcache.db, logs, node_modules, retrieval CSVs)
    if [ -d /opt/intel/sgx-dcap-pccs ]; then
        echo "Removing /opt/intel/sgx-dcap-pccs..."
        rm -rf /opt/intel/sgx-dcap-pccs
    fi
}

print_section_header "Starting clean PCCS installation and setup..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Stop and disable all services first (ignore missing units)
print_section_header "Stopping all services..."
for svc in pccs qgsd mpa_registration_tool; do
    if systemctl list-unit-files "${svc}.service" --no-legend 2>/dev/null | grep -q .; then
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
    else
        echo "Service ${svc} not found, skipping"
    fi
done

print_section_header "Removing existing packages..."
remove_pccs

# Clean up old configurations
print_section_header "Cleaning up old configurations..."
rm -f /etc/sgx_default_qcnl.conf

# Install packages
print_section_header "Installing packages..."

if [ "$USE_INTEL_REPO" -eq 1 ]; then
    # Intel SGX repo: install attestation packages directly.
    # Top-level packages only; lib* deps (urts, enclave-common, pce/tdx-logic,
    # ae-*) are pulled in automatically.
    #   - sgx-dcap-pccs, tdx-qgs       : PCCS caching service + Quote Generation
    #   - libsgx-dcap-default-qpl      : Intel Quote Provider library (QPL)
    #   - sgx-ra-service               : direct (RA) registration method
    #   - sgx-pck-id-retrieval-tool    : indirect registration method
    apt-get install -y \
        sgx-dcap-pccs \
        tdx-qgs \
        libsgx-dcap-default-qpl \
        sgx-ra-service \
        sgx-pck-id-retrieval-tool
    check_error "Failed to install packages"
else
    # Canonical PPA: install attestation packages via the official script
    # from canonical/tdx.
    ATTEST_SCRIPT="${TMP_DIR}/tdx-cannonical/attestation/setup-attestation-host.sh"
    if [ ! -f "$ATTEST_SCRIPT" ]; then
        echo -e "${RED}ERROR: attestation setup script not found at ${ATTEST_SCRIPT}${NC}"
        exit 1
    fi
    chmod +x "$ATTEST_SCRIPT"
    "$ATTEST_SCRIPT"
    check_error "Failed to install attestation packages"
fi

# Create PCCS config directory
mkdir -p /opt/intel/sgx-dcap-pccs/config/

# Create PCCS configuration
print_section_header "Creating PCCS configuration..."
cat > /opt/intel/sgx-dcap-pccs/config/default.json << EOL
{
    "HTTPS_PORT" : ${PCCS_PORT},
    "hosts" : "127.0.0.1",
    "uri": "https://api.trustedservices.intel.com/sgx/certification/v4/",
    "ApiKey" : "${PCCS_API_KEY}",
    "proxy" : "",
    "RefreshSchedule": "0 0 1 * *",
    "UserTokenHash" : "${USER_TOKEN}",
    "AdminTokenHash" : "${USER_TOKEN}",
    "CachingFillMode" : "LAZY",
    "LogLevel" : "debug",
    "DB_CONFIG" : "sqlite",
    "sqlite" : {
        "database" : "database",
        "username" : "username",
        "password" : "password",
        "options" : {
            "host": "localhost",
            "dialect": "sqlite",
            "pool": {
                "max": 5,
                "min": 0,
                "acquire": 30000,
                "idle": 10000
            },
            "define": {
                "freezeTableName": true
            },
            "logging" : false, 
            "storage": "pckcache.db"
        }
    }
}
EOL

# Configure QCNL
print_section_header "Configuring QCNL..."
cat > /etc/sgx_default_qcnl.conf << EOL
PCCS_URL=https://localhost:${PCCS_PORT}/sgx/certification/v4/
USE_SECURE_CERT=false
RETRY_TIMES=6
RETRY_DELAY=10
LOCAL_PCK_URL=http://localhost:${PCCS_PORT}/sgx/certification/v4/
LOCAL_PCK_RETRY_TIMES=6
LOCAL_PCK_RETRY_DELAY=10
EOL
check_error "Failed to create QCNL configuration"

# Set correct permissions
print_section_header "Setting permissions..."
chown -R pccs:pccs /opt/intel/sgx-dcap-pccs/
chmod -R 750 /opt/intel/sgx-dcap-pccs/

# Enable and start services
print_section_header "Enabling and starting services..."
systemctl enable pccs qgsd mpa_registration_tool
systemctl daemon-reload

# Start PCCS first
print_section_header "Starting PCCS..."
systemctl start pccs
wait_for_service pccs
check_error "Failed to start PCCS"
sleep 5

# Get platform info and register
cd /opt/intel/sgx-dcap-pccs/
print_section_header "Running PCKIDRetrievalTool..."
rm -f pckid_retrieval.csv
PCKIDRetrievalTool
check_error "PCKIDRetrievalTool failed"

print_section_header "Registering platform with PCCS..."
register_platform
check_error "Failed to register platform"

# Start remaining services
print_section_header "Starting remaining services..."
systemctl start qgsd
wait_for_service qgsd
systemctl start mpa_registration_tool

# Check services status
print_section_header "Checking services status..."
# Check PCCS and QGSD status
for service in pccs qgsd; do
    echo -e "\n${YELLOW}${service} Status:${NC}"
    if ! systemctl is-active --quiet $service; then
        echo -e "${RED}Error: $service is not running${NC}"
        systemctl status $service --no-pager
        exit 1
    else
        echo -e "${GREEN}$service is running${NC}"
        systemctl status $service --no-pager
    fi
done

# Separately handle mpa_registration_tool since it's expected to exit
echo -e "\n${YELLOW}mpa_registration_tool Status:${NC}"
if systemctl is-enabled --quiet mpa_registration_tool; then
    echo -e "${GREEN}mpa_registration_tool was properly configured${NC}"
    systemctl status mpa_registration_tool --no-pager || true
else
    echo -e "${RED}Error: mpa_registration_tool is not properly configured${NC}"
    exit 1
fi

print_section_header "Installation and setup completed!"
echo -e "${YELLOW}To check logs use:${NC}"
echo "PCCS logs: journalctl -u pccs -f"
echo "QGSD logs: journalctl -u qgsd -f"
echo "MPA Registration logs: cat /var/log/mpa_registration.log"
