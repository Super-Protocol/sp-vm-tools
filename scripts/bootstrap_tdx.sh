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
    if ! mdadm --examine --scan > "/dev/null" 2>&1; then
        echo "No RAID arrays found, skipping RAID configuration"
        return 0
    fi

    mdadm --detail --scan | grep -v "^mdadm:" > "/etc/mdadm/mdadm.conf.new"
    
    if [ -s "/etc/mdadm/mdadm.conf.new" ]; then
        
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
    else
        echo "No valid RAID configuration found"
        rm -f "/etc/mdadm/mdadm.conf.new"
    fi

    return 0
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
    local new_kernel="$1"
    echo "Setting up GRUB for kernel ${new_kernel}..."
    
    # Ensure GRUB directory exists
    mkdir -p /boot/grub
    
    # Backup current GRUB config
    if [ -f /etc/default/grub ]; then
        cp /etc/default/grub "/etc/default/grub.backup.$(date +%Y%m%d_%H%M%S)"
    fi

    # Directly set the first menuentry as default since it's our new kernel
    sed -i '/^GRUB_DEFAULT=/d' /etc/default/grub
    echo 'GRUB_DEFAULT=0' > /etc/default/grub.new
    cat /etc/default/grub >> /etc/default/grub.new
    mv /etc/default/grub.new /etc/default/grub
    
    # Add required kernel parameters if not present
    if ! grep -q 'kvm_intel.tdx=on' /etc/default/grub; then
        if grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub; then
            sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)/\1 nohibernate kvm_intel.tdx=on/' /etc/default/grub
        else
            echo 'GRUB_CMDLINE_LINUX_DEFAULT="nohibernate kvm_intel.tdx=on"' >> /etc/default/grub
        fi
    fi
    
    # Force menu to always show and set reasonable timeout
    sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=menu/' /etc/default/grub
    sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
    
    # Remove any hidden timeout settings
    sed -i '/^GRUB_HIDDEN_TIMEOUT=/d' /etc/default/grub
    
    # Ensure GRUB_RECORDFAIL_TIMEOUT is set
    if ! grep -q '^GRUB_RECORDFAIL_TIMEOUT=' /etc/default/grub; then
        echo 'GRUB_RECORDFAIL_TIMEOUT=5' >> /etc/default/grub
    fi

    # Create a custom configuration file to ensure our kernel is first
    echo "# Custom kernel order configuration" > /etc/default/grub.d/99-tdx-kernel.cfg
    echo "GRUB_DEFAULT=0" >> /etc/default/grub.d/99-tdx-kernel.cfg
    
    # Force regeneration of grub.cfg and initramfs
    update-initramfs -u -k "${new_kernel}"
    update-grub2 || update-grub

    # For UEFI systems, ensure the boot entry is updated
    if [ -d /sys/firmware/efi ]; then
        if command -v efibootmgr >/dev/null 2>&1; then
            # Get the current boot order
            current_order=$(efibootmgr | grep BootOrder: | cut -d: -f2 | tr -d ' ')
            
            # Get Ubuntu's boot entry
            ubuntu_entry=$(efibootmgr | grep -i ubuntu | grep -v -i windows | head -n1 | cut -c5-8)
            
            if [ -n "$ubuntu_entry" ] && [ -n "$current_order" ]; then
                # Move Ubuntu's entry to the front if it's not already there
                if [ "${current_order:0:4}" != "$ubuntu_entry" ]; then
                    new_order="${ubuntu_entry},${current_order}"
                    efibootmgr -o "${new_order}"
                fi
            fi
        fi
    fi

    # Use both grub-set-default and grub-reboot for maximum reliability
    if command -v grub-set-default >/dev/null 2>&1; then
        grub-set-default 0
        echo "Set default boot entry using grub-set-default"
    fi

    if command -v grub-reboot >/dev/null 2>&1; then
        grub-reboot 0
        echo "Set next boot entry using grub-reboot"
    fi

    echo "GRUB configuration completed successfully for kernel ${new_kernel}"
    return 0
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

  echo "Checking for NVIDIA GPUs..."
  if ! command -v lspci >/dev/null; then
    echo "lspci not found, skipping NVIDIA GPU configuration"
    return 0
  fi

  # Blacklist both NVIDIA and Nouveau drivers
  echo "Blacklisting NVIDIA and Nouveau drivers..."
  tee /etc/modprobe.d/blacklist-nvidia.conf << EOF
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_uvm
blacklist nvidia_modeset
blacklist nouveau
EOF

  # Remove Nouveau from modules if present
  sed -i '/nouveau/d' /etc/modules

  echo "Determining PCI IDs for your NVIDIA GPU(s)..."
  gpu_list=$(lspci -nnk -d 10de: | grep -E '3D controller' || true)

  if [ -z "$gpu_list" ]; then
    echo "No NVIDIA GPU found, skipping configuration"
    return 0
  fi

  echo "The following NVIDIA GPUs were found:"
  echo "$gpu_list"

  # enable cc mode
  sudo rm -rf "${TMP_DIR}/gpu-admin-tools" 2>/dev/null || true
  git clone -b v2024.08.09 --single-branch --depth 1 --no-tags https://github.com/NVIDIA/gpu-admin-tools.git "${TMP_DIR}/gpu-admin-tools"
  pushd "${TMP_DIR}/gpu-admin-tools"
  AVAILABLE_GPUS=$(echo "$gpu_list" | awk '{print $1}' | tr '\n' ' ')
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
  if [ -z "$new_pci_ids" ]; then
      echo "No PCI IDs found for NVIDIA GPUs!"
      exit 1
  fi

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
  if ! command -v curl &> /dev/null || ! command -v jq &> /dev/null; then
    apt-get update && apt-get install -y curl jq || return 1
  fi

  # Form file name and paths
  SCRIPT_DIR=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")

  # Get latest tag
  LATEST_TAG=$(curl -s https://api.github.com/repos/Super-Protocol/sp-vm-tools/releases/latest | jq -r '.tag_name')
  if [[ -z "${LATEST_TAG}" ]]; then
    echo "No tags found in repository" >&2
    return 1
  fi

  # Form file name
  ARCHIVE_NAME="package_${LATEST_TAG}.tar.gz"
  ARCHIVE_PATH="${SCRIPT_DIR}/${ARCHIVE_NAME}"

  # Check if file already exists
  if [ -f "${ARCHIVE_PATH}" ]; then
    printf "%s" "${ARCHIVE_PATH}"
    return 0
  fi

  DOWNLOAD_URLS=(
  "https://github.com/Super-Protocol/sp-vm-tools/releases/download/${LATEST_TAG}/package.tar.gz"
  "https://github.com/Super-Protocol/sp-vm-tools/releases/download/${LATEST_TAG}/package-tdx.tar.gz"
  )

  echo "Downloading version ${LATEST_TAG}..." >&2
  
  DOWNLOAD_SUCCESS=false
  for URL in "${DOWNLOAD_URLS[@]}"; do
  echo "Trying to download from ${URL}..." >&2
  if curl -f -L -o "${ARCHIVE_PATH}" "${URL}"; then
    echo "Successfully downloaded from ${URL}" >&2
    DOWNLOAD_SUCCESS=true
    break
  else
    echo "Failed to download from ${URL}" >&2
  fi
  done

  if ! $DOWNLOAD_SUCCESS; then
    echo "Failed to download release from all URLs" >&2
    rm -f "${ARCHIVE_PATH}"
    return 1
  fi

  # Return the path only if everything was successful
  printf "%s" "${ARCHIVE_PATH}"
  return 0
}

check_os_version() {
    if [ ! -f /etc/os-release ]; then
        echo -e "${RED}ERROR: Could not determine OS version${NC}"
        echo "This script is designed for Ubuntu 24.04 or higher."
        exit 1
    fi

    . /etc/os-release
    
    if [ "$ID" != "ubuntu" ]; then
        echo -e "${RED}ERROR: Unsupported operating system${NC}"
        echo "This script requires Ubuntu 24.04 or higher."
        echo "Current OS: $PRETTY_NAME"
        exit 1
    fi

    # Extract major version number
    major_version=$(echo "$VERSION_ID" | cut -d. -f1)
    
    if [ "$major_version" -lt 24 ]; then
        echo -e "${RED}ERROR: Unsupported Ubuntu version${NC}"
        echo "This script requires Ubuntu 24.04 or higher."
        echo "Current version: $PRETTY_NAME"
        echo "Please upgrade your system to continue."
        exit 1
    fi
}

bootstrap() {
    check_os_version

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
    if [ "$NEW_KERNEL_VERSION" != "$CURRENT_KERNEL" ]; then
        setup_grub "$NEW_KERNEL_VERSION"
    fi

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

    print_section_header "Hardware Configuration"
    if command -v lspci >/dev/null; then
        echo "Checking NVIDIA GPU configuration..."
        setup_nvidia_gpus "${TMP_DIR}" || true
    else
        echo "Skipping NVIDIA GPU check (lspci not found)"
    fi    

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
