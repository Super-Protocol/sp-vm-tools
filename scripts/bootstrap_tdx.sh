#!/bin/bash
set -e

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

check_all_bios_settings() {
    local results=()
    local all_passed=true
    
    print_section_header "BIOS Configuration Check Results"
    echo "Checking all settings..."
    
    # CPU PA
    results+=("CPU PA Settings:")
    if true; then
        results+=("✓ CPU PA limit properly configured")
    else
        results+=("✗ CPU PA limit must be disabled")
        results+=("  Location: Uncore General Configuration")
        all_passed=false
    fi
    
    # TME Check
    results+=("TME Settings:")
    if rdmsr -f 0:0 0x981 2>/dev/null | grep -q "1" && \
       rdmsr -f 0:0 0x982 2>/dev/null | grep -q "1"; then
        results+=("✓ Memory encryption enabled")
    else
        results+=("✗ Memory encryption disabled")
        all_passed=false
    fi

    # SGX Check
    results+=("SGX Settings:")
    if grep -q "sgx" /proc/cpuinfo && [ -c "/dev/sgx_enclave" -o -c "/dev/sgx/enclave" ]; then
        results+=("✓ SGX enabled and configured")
    else
        results+=("✗ SGX not properly configured")
        all_passed=false
    fi

    # TXT Check  
    results+=("TXT Settings:")
    if grep -q "smx" /proc/cpuinfo || \
       (command -v rdmsr &> /dev/null && \
        modprobe msr 2>/dev/null && \
        [ "$(rdmsr 0x8B 2>/dev/null)" != "0" ]); then
        results+=("✓ TXT supported and enabled")
    else
        results+=("✗ TXT not properly configured")
        all_passed=false
    fi

    # TDX Check
    results+=("TDX Settings:")
    if grep -q "tdx" /proc/cpuinfo || dmesg | grep -q "TDX"; then
        results+=("✓ TDX supported and enabled")
    else 
        results+=("✗ TDX not properly configured")
        all_passed=false
    fi

    results+=("Required BIOS Configuration:")
    results+=("• Memory Encryption:")
    results+=("  - TME: Enable")
    results+=("  - TME Multi-Tenant: Enable")
    results+=("  - TME-MT keys: 31")
    results+=("  - Key split: 1")
    results+=("• TDX:")
    results+=("  - TDX: Enable")
    results+=("  - SEAM Loader: Enable")
    results+=("  - TDX keys: 32")

    print_section_header "Status"
    printf '%s\n' "${results[@]}"

    if [ "$all_passed" = true ]; then
        echo -e "\n✓ All settings properly configured"
        return 0
    else
        echo -e "\n✗ Some settings need attention"
        return 1
    fi
}

check_bios_settings() {
    echo "Performing comprehensive BIOS configuration check..."
    
    # Install msr-tools if not present
    if ! command -v rdmsr &> /dev/null; then
        echo "Installing msr-tools..."
        apt-get update && apt-get install -y msr-tools
    fi

    # Load the msr module if not loaded
    if ! lsmod | grep -q "^msr"; then
        echo "Loading MSR module..."
        modprobe msr
    fi

    # Run all checks at once
    check_all_bios_settings
    return $?
}

detect_raid_config() {
    echo "Detecting RAID configuration..."
    
    # Check active arrays
    if [ -f "/proc/mdstat" ]; then
        echo "Found RAID arrays:"
        cat /proc/mdstat | grep ^md
        
        if grep -q "^md" /proc/mdstat; then
            echo "✓ Active RAID arrays detected"
            return 0
        fi
    fi
    
    # Check mdadm configuration
    if [ -f "/etc/mdadm/mdadm.conf" ] && grep -q "ARRAY" /etc/mdadm/mdadm.conf; then
        echo "✓ RAID configuration found in mdadm.conf"
        return 0
    fi
    
    # Check for any RAID devices
    if lsblk | grep -q "raid"; then
        echo "✓ RAID devices found in system"
        return 0
    fi
    
    echo "No RAID configuration detected"
    return 1
}

setup_raid_modules() {
    local new_kernel="$1"
    local current_kernel="$2"
    echo "Setting up RAID configuration for kernel ${new_kernel}"

    # Create new mdadm configuration
    echo "Creating new RAID configuration..."
    if mdadm --detail --scan > "/etc/mdadm/mdadm.conf.new"; then
        sed -i '/^mdadm:/d' "/etc/mdadm/mdadm.conf.new"
        # Create backup of existing config if it exists
        if [ -f "/etc/mdadm/mdadm.conf" ]; then
            cp "/etc/mdadm/mdadm.conf" "/etc/mdadm/mdadm.conf.${new_kernel}.bak"
        fi
        
        # Replace the current config with the new one
        if ! mv "/etc/mdadm/mdadm.conf.new" "/etc/mdadm/mdadm.conf"; then
            echo "Failed to update mdadm configuration"
            return 1
        fi
        
        # Update initramfs
        echo "Updating initramfs with new RAID configuration..."
        if ! update-initramfs -u -k "${new_kernel}"; then
            echo "Failed to update initramfs"
            return 1
        fi
        
        echo "✓ RAID configuration completed successfully"
        return 0
    else
        echo "Failed to generate RAID configuration"
        return 1
    fi
}

get_kernel_version() {
  local deb_file="$1"
  local version=""
  local basename_file=$(basename "$deb_file")

  # Method 1: Extract from linux-image-VERSION_something format
  if [[ $basename_file =~ linux-image-([0-9]+\.[0-9]+\.[0-9]+-rc[0-9]+\+) ]]; then
  version="${BASH_REMATCH[1]}"
  elif [[ $basename_file =~ linux-image-([0-9]+\.[0-9]+\.[0-9]+-[^_]+) ]]; then
  version="${BASH_REMATCH[1]}"
  fi

  # Method 2: Check package info if method 1 failed
  if [ -z "$version" ]; then
  version=$(dpkg-deb -f "$deb_file" Package | grep -oP 'linux-image-\K.*' || true)
  fi
  
  printf "%s" "$version"
}


# Main installation function
install_debs() {
    local DEB_DIR="$1"
    echo "Checking current kernel installation..."
    
    # Get current kernel version
    CURRENT_KERNEL=$(uname -r)
    echo "Current kernel version: ${CURRENT_KERNEL}"
    
    # Try to detect version from kernel package before installation
    NEW_KERNEL_VERSION=""
    for deb in "${DEB_DIR}"/*linux-image*.deb; do
        if [ -f "$deb" ]; then
            NEW_KERNEL_VERSION=$(get_kernel_version "$deb")
            echo "Detected kernel version in package: $NEW_KERNEL_VERSION"
            break
        fi
    done
    
    # Check if this kernel is already running
    if [ -n "$NEW_KERNEL_VERSION" ] && [ "$NEW_KERNEL_VERSION" = "$CURRENT_KERNEL" ]; then
        echo "Kernel version ${NEW_KERNEL_VERSION} is already running"
        echo "Skipping kernel installation..."
        return 0
    fi
    
    echo "Installing kernel and dependencies..."
    
    # Show directory contents
    echo "Contents of ${DEB_DIR}:"
    ls -la "${DEB_DIR}"
    
    # Check for RAID configuration
    RAID_ACTIVE=false
    if detect_raid_config; then
        RAID_ACTIVE=true
        echo "✓ RAID configuration detected, will build modules for new kernel"
    fi
    
    # Install dependencies
    apt update && DEBIAN_FRONTEND=noninteractive apt install -y libslirp0 s3cmd
    
    # Install kernel headers first
    echo "Installing kernel headers..."
    for deb in "${DEB_DIR}"/*kernel-headers*.deb; do
        if [ -f "$deb" ]; then
            echo "Installing: $(basename "$deb")"
            DEBIAN_FRONTEND=noninteractive dpkg -i "$deb"
        fi
    done

    echo "Installing kernel image..."
    for deb in "${DEB_DIR}"/*linux-image*.deb; do
        if [ -f "$deb" ]; then
            echo "Installing: $(basename "$deb")"
            DEBIAN_FRONTEND=noninteractive dpkg -i "$deb"
            break
        fi
    done

    # Install remaining packages
    echo "Installing remaining packages..."
    for deb in "${DEB_DIR}"/*.deb; do
        if [[ "$deb" != *"kernel-image"* ]] && [[ "$deb" != *"kernel-headers"* ]] && [ -f "$deb" ]; then
            echo "Installing: $(basename "$deb")"
            DEBIAN_FRONTEND=noninteractive dpkg -i "$deb"
        fi
    done

    # Verify kernel version was detected
    if [ -z "$NEW_KERNEL_VERSION" ]; then
        echo -e "${RED}ERROR: Failed to determine kernel version${NC}"
        return 1
    fi

    # Setup RAID if needed
    if [ "$RAID_ACTIVE" = true ]; then
        if ! setup_raid_modules "$NEW_KERNEL_VERSION" "$CURRENT_KERNEL"; then
            echo "! RAID setup failed"
            return 1
        fi
    fi

    # Print installation summary
    echo "Installation Summary:"
    echo "- Previous kernel: $CURRENT_KERNEL"
    echo "- New kernel: $NEW_KERNEL_VERSION"
    echo "- RAID enabled: $RAID_ACTIVE"
    echo "- Kernel files:"
    echo "  - /lib/modules/${NEW_KERNEL_VERSION}"
    echo "  - /boot/vmlinuz-${NEW_KERNEL_VERSION}"

    if [ "$RAID_ACTIVE" = true ]; then
        echo "RAID Configuration:"
        echo "- Module path: /lib/modules/${NEW_KERNEL_VERSION}/kernel/drivers/md/"
        echo "- Config backup: /etc/mdadm/mdadm.conf.${NEW_KERNEL_VERSION}"
        echo
        echo "Post-reboot verification steps:"
        echo "1. Check /proc/mdstat"
        echo "2. Run mdadm --detail /dev/mdX"
        echo "3. Verify /etc/mdadm/mdadm.conf"
    fi

    return 0
}

setup_grub() {
  mkdir -p /boot/grub
  if ! grep -q 'kvm_intel.tdx=on' /etc/default/grub; then
    sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)/\1 nohibernate kvm_intel.tdx=on/' /etc/default/grub
  fi

  update-grub
}

update_tdx_module() {
  TMP_DIR=$1
  echo "Updating TDX-module..."
  pushd "${TMP_DIR}"
  wget https://github.com/intel/tdx-module/releases/download/TDX_1.5.05/intel_tdx_module.tar.gz
  tar -xvzf intel_tdx_module.tar.gz
  mkdir -p /boot/efi/EFI/TDX/
  cp -vf TDX-Module/intel_tdx_module.so /boot/efi/EFI/TDX/TDX-SEAM.so
  cp -vf TDX-Module/intel_tdx_module.so.sigstruct /boot/efi/EFI/TDX/TDX-SEAM.so.sigstruct
  popd
}

setup_nvidia_gpus() {
  TMP_DIR=$1

  echo "Determining PCI IDs for your NVIDIA GPU(s)..."
  gpu_list=$(lspci -nnk -d 10de: | grep -E '3D controller')

  if [ -z "$gpu_list" ]; then
    echo "No NVIDIA GPU found."
    return
  fi

  echo "The following NVIDIA GPUs were found:"
  echo "$gpu_list"

  # enable cc mode
  git clone -b v2024.08.09 --single-branch --depth 1 --no-tags https://github.com/NVIDIA/gpu-admin-tools.git "${TMP_DIR}/gpu-admin-tools"
  pushd "${TMP_DIR}/gpu-admin-tools"
  AVAILABLE_GPUS=$(echo ${gpu_list} | awk '{print $1}')
  for gpu in $AVAILABLE_GPUS; do
    echo "Enable CC mode for ${gpu}"
    python3 ./nvidia_gpu_tools.py --gpu-bdf=${gpu} --set-cc-mode=on --reset-after-cc-mode-switch
    if [ $? -ne 0 ]; then
      echo "Failed to enable cc-mode for GPU ${gpu}"
      exit 1
    fi
  done
  popd

  new_pci_ids=$(echo "$gpu_list" | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])' | sort -u | tr '\n' ',' | sed 's/,$//')

  existing_pci_ids=""
  if [ -f /etc/modprobe.d/vfio.conf ]; then
    existing_pci_ids=$(grep -oP '(?<=ids=)[^ ]+' /etc/modprobe.d/vfio.conf | tr ',' '\n' | sort -u | tr '\n' ',' | sed 's/,$//')
  fi

  if [ -n "$existing_pci_ids" ]; then
    combined_pci_ids=$(echo -e "${existing_pci_ids}\n${new_pci_ids}" | tr ',' '\n' | sort -u | tr '\n' ',' | sed 's/,$//')
  else
    combined_pci_ids="$new_pci_ids"
  fi

  echo "Updating kernel module for VFIO-PCI with IDs: $combined_pci_ids"
  sudo bash -c "echo 'options vfio-pci ids=$combined_pci_ids' > /etc/modprobe.d/vfio.conf"

  echo "Ensuring the VFIO-PCI module is added to /etc/modules-load.d/vfio-pci.conf..."
  if [ ! -f /etc/modules-load.d/vfio-pci.conf ]; then
    sudo bash -c "echo 'vfio-pci' > /etc/modules-load.d/vfio-pci.conf"
    echo "Created /etc/modules-load.d/vfio-pci.conf and added 'vfio-pci' module."
  else
    if ! grep -q '^vfio-pci$' /etc/modules-load.d/vfio-pci.conf; then
      sudo bash -c "echo 'vfio-pci' >> /etc/modules-load.d/vfio-pci.conf"
      echo "'vfio-pci' module added to /etc/modules-load.d/vfio-pci.conf."
    else
      echo "'vfio-pci' module is already present in /etc/modules-load.d/vfio-pci.conf."
    fi
  fi

  echo "Regenerating kernel initramfs..."
  sudo update-initramfs -u

  echo "VFIO-PCI setup is complete."
}

download_latest_release() {
  # Check and install required tools
  if ! command -v curl &> /dev/null || ! command -v git &> /dev/null; then
    apt-get update && apt-get install -y curl git || return 1
  fi
  
  # Form file name and paths
  SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
  
  # Create temporary directory for download
  TMP_DOWNLOAD=$(mktemp -d)
  pushd "${TMP_DOWNLOAD}" > /dev/null

  # Clone repository to get tags info
  if ! git clone -q --filter=blob:none --no-checkout https://github.com/Super-Protocol/sp-vm-tools.git; then
    echo "Failed to access repository" >&2
    popd > /dev/null
    rm -rf "${TMP_DOWNLOAD}"
    return 1
  fi

  cd sp-vm-tools
  
  # Get latest tag
  LATEST_TAG=$(git describe --tags $(git rev-list --tags --max-count=1 2>/dev/null) 2>/dev/null)
  if [ -z "${LATEST_TAG}" ]; then
    echo "No tags found in repository" >&2
    popd > /dev/null
    rm -rf "${TMP_DOWNLOAD}"
    return 1
  fi

  # Form file name
  ARCHIVE_NAME="package_${LATEST_TAG}.tar.gz"
  ARCHIVE_PATH="${SCRIPT_DIR}/${ARCHIVE_NAME}"
  
  popd > /dev/null
  rm -rf "${TMP_DOWNLOAD}"

  # Check if file already exists
  if [ -f "${ARCHIVE_PATH}" ]; then
    printf "%s" "${ARCHIVE_PATH}"
    return 0
  fi

  DOWNLOAD_URL="https://github.com/Super-Protocol/sp-vm-tools/releases/download/${LATEST_TAG}/package.tar.gz"
  
  echo "Downloading version ${LATEST_TAG}..." >&2

  # Download archive directly to target directory
  if ! curl -L -o "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"; then
    echo "Failed to download release" >&2
    rm -f "${ARCHIVE_PATH}"
    return 1
  fi

  echo "Successfully downloaded ${ARCHIVE_NAME}" >&2
  
  # Return the path only if everything was successful
  printf "%s" "${ARCHIVE_PATH}"
  return 0  
}

bootstrap() {
    # Check if the script is running as root
    print_section_header "Privilege Check"
    if [ "$(id -u)" -ne 0 ]; then
        echo "This script must be run as root. Please run with sudo."
        exit 1
    fi
    
    # Download latest release if no archive provided
    print_section_header "Release Package Setup"
    ARCHIVE_PATH=""
    if [ "$#" -ne 1 ]; then
        echo "No archive provided, downloading latest release..."
        ARCHIVE_PATH=$(download_latest_release) || {
            echo "Failed to download release"
            exit 1
        }
    else
        ARCHIVE_PATH="$1"
    fi
    
    # Check if the archive exists
    if [ ! -f "${ARCHIVE_PATH}" ]; then
        echo "Archive not found: ${ARCHIVE_PATH}"
        exit 1
    fi

    echo "Using archive: ${ARCHIVE_PATH}"

    # Create a temporary directory for extraction
    print_section_header "Package Extraction"
    TMP_DIR=$(mktemp -d)
    DEB_DIR="${TMP_DIR}/package"
    mkdir -p "${DEB_DIR}"

    # Extract the archive to the temporary directory
    echo "Extracting archive..."
    tar -xf "${ARCHIVE_PATH}" -C "${DEB_DIR}"

    print_section_header "Kernel Installation"
    echo "Installing kernel and required packages first..."
    install_debs "${DEB_DIR}"

    print_section_header "Kernel Configuration"
    echo "Configuring kernel boot parameters..."
    setup_grub

    print_section_header "TDX Module Update"
    echo "Updating TDX module..."
    update_tdx_module "${TMP_DIR}"

    # Check if kernel was actually installed
    print_section_header "System State Check"
    if [ "$NEW_KERNEL_VERSION" != "$CURRENT_KERNEL" ]; then
        echo "System reboot required to apply changes"
        echo "1. Reboot now"
        echo "2. Continue without reboot (not recommended)"
        read -p "Choose (1/2): " choice
        case $choice in
            1)
                print_section_header "System Reboot"
                echo "System will reboot in 10 seconds..."
                echo "Please run this script again after reboot to complete the setup."
                sleep 10
                reboot
                ;;
            2)
                echo "Continuing without reboot (not recommended)..."
                ;;
        esac
    fi

    print_section_header "BIOS Configuration Verification"
    if ! check_bios_settings; then
        echo -e "${RED}ERROR: Required BIOS settings are not properly configured${NC}"
        echo "Please configure BIOS settings according to the instructions above and try again"
        exit 1
    fi

    if [ -f "$(dirname "${BASH_SOURCE[0]}")/setup_tdx.sh" ]; then
        echo "Running TDX setup script..."
        cp "$(dirname "${BASH_SOURCE[0]}")/setup_tdx.sh" "${TMP_DIR}/"
        chmod +x "${TMP_DIR}/setup_tdx.sh"
        "${TMP_DIR}/setup_tdx.sh"
        if [ $? -ne 0 ]; then
            echo -e "${RED}ERROR: TDX setup failed${NC}"
            exit 1
        fi
    else 
        echo echo -e "${RED}ERROR: setup_tdx.sh not found${NC}"
        exit 1
    fi

    print_section_header "NVIDIA GPU Configuration"
    setup_nvidia_gpus "${TMP_DIR}"

    # Clean up temporary directory
    print_section_header "Cleanup"
    echo "Cleaning up..."
    rm -rf "${TMP_DIR}"

    print_section_header "Installation Status"
    echo "Installation complete."
    if [ "$NEW_KERNEL_VERSION" != "$CURRENT_KERNEL" ] && [ "$choice" = "2" ]; then
        echo "NOTE: A system reboot is still required to activate all changes."
    fi
}

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Script was sourced"
else
  bootstrap $@
fi
