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
blacklist nouveau
blacklist nvidia_uvm
blacklist nvidia_modeset
EOF

  # Remove Nouveau from modules if present
  sed -i '/nouveau/d' /etc/modules

  echo "Determining PCI IDs for your NVIDIA GPU(s)..."
  
  # Get GPU devices (only actual GPUs, not bridges/switches)
  gpu_list=$(lspci -nnk -d 10de: | grep -E '3D controller|VGA compatible controller' || true)
  # Get all other NVIDIA devices for reference
  all_nvidia_devices=$(lspci -d 10de: | wc -l)
  other_nvidia_devices=$((all_nvidia_devices - $(echo "$gpu_list" | wc -l)))

  if [ -z "$gpu_list" ]; then
    echo "No NVIDIA GPUs found, skipping configuration"
    return 0
  fi

  # Count actual GPUs
  gpu_count=$(echo "$gpu_list" | wc -l)

  echo "Found NVIDIA devices:"
  echo "- GPUs: $gpu_count"
  echo "- Other NVIDIA devices (NVLink/bridges): $other_nvidia_devices"
  echo "- Total: $all_nvidia_devices"
  echo ""
  echo "GPU details:"
  echo "$gpu_list"

  # Clone gpu-admin-tools
  sudo rm -rf "${TMP_DIR}/gpu-admin-tools" 2>/dev/null || true
  git clone -b v2025.04.07 --single-branch --depth 1 --no-tags https://github.com/NVIDIA/gpu-admin-tools.git "${TMP_DIR}/gpu-admin-tools"
  pushd "${TMP_DIR}/gpu-admin-tools"

  # Step 1: Disable PPCIe mode on GPUs only (using BDF addresses)
  echo "Disabling PPCIe mode on GPU devices..."
  if [ "$gpu_count" -gt 0 ]; then
    gpu_bdfs=$(echo "$gpu_list" | awk '{print $1}')
    for gpu_bdf in $gpu_bdfs; do
      echo "Disabling PPCIe mode for GPU ${gpu_bdf}"
      python3 ./nvidia_gpu_tools.py --gpu-bdf=${gpu_bdf} --set-ppcie-mode=off --reset-after-ppcie-mode-switch
      if [ $? -ne 0 ]; then
        echo "Warning: Failed to disable PPCIe mode for GPU ${gpu_bdf} (this may be normal)"
      fi
    done
  else
    echo "No GPUs found to configure"
    popd
    return 0
  fi

  # Step 2: Configure GPUs for CC mode
  echo "Configuring GPUs for Confidential Computing mode..."
  
  gpu_bdfs=$(echo "$gpu_list" | awk '{print $1}')
  
  for gpu_bdf in $gpu_bdfs; do
    echo "Setting CC mode for GPU ${gpu_bdf}"
    python3 ./nvidia_gpu_tools.py --gpu-bdf=${gpu_bdf} --set-cc-mode=on --reset-after-cc-mode-switch
    if [ $? -ne 0 ]; then
      echo "ERROR: Failed to enable CC mode for GPU ${gpu_bdf}"
      echo "This is critical for confidential computing functionality"
      exit 1
    fi
    
    # Verify the mode was set correctly
    echo "Verifying CC mode for GPU ${gpu_bdf}"
    python3 ./nvidia_gpu_tools.py --gpu-bdf=${gpu_bdf} --query-cc-settings
  done

  # Note: We don't need to handle NVSwitches separately as nvidia_gpu_tools
  # will handle all necessary infrastructure automatically

  popd

  # Step 3: Configure VFIO-PCI for GPU passthrough
  echo "Configuring VFIO-PCI for GPU passthrough..."
  
  # Get PCI IDs for GPUs only
  new_pci_ids=$(echo "$gpu_list" | grep -oP '\[\K[0-9a-f]{4}:[0-9a-f]{4}(?=\])' | sort -u | tr '\n' ',' | sed 's/,$//')
  
  if [ -z "$new_pci_ids" ]; then
    echo "ERROR: No PCI IDs found for NVIDIA GPUs!"
    exit 1
  fi

  # Merge with existing PCI IDs if any
  existing_pci_ids=""
  if [ -f /etc/modprobe.d/vfio.conf ]; then
    existing_pci_ids=$(grep -oP '(?<=ids=)[^ ]+' /etc/modprobe.d/vfio.conf | tr ',' '\n' | sort -u | tr '\n' ',' | sed 's/,$//')
  fi

  if [ -n "$existing_pci_ids" ]; then
    combined_pci_ids=$(echo -e "${existing_pci_ids}\n${new_pci_ids}" | tr ',' '\n' | sort -u | tr '\n' ',' | sed 's/,$//')
  else
    combined_pci_ids="$new_pci_ids"
  fi

  echo "Updating VFIO-PCI configuration with GPU IDs: $combined_pci_ids"
  sudo bash -c "echo 'options vfio-pci ids=$combined_pci_ids' > /etc/modprobe.d/vfio.conf"

  # Ensure VFIO-PCI module is loaded
  echo "Configuring VFIO-PCI module loading..."
  if [ ! -f /etc/modules-load.d/vfio-pci.conf ]; then
    sudo bash -c "echo 'vfio-pci' > /etc/modules-load.d/vfio-pci.conf"
    echo "Created /etc/modules-load.d/vfio-pci.conf"
  else
    if ! grep -q '^vfio-pci$' /etc/modules-load.d/vfio-pci.conf; then
      sudo bash -c "echo 'vfio-pci' >> /etc/modules-load.d/vfio-pci.conf"
      echo "Added vfio-pci to modules-load.d"
    else
      echo "vfio-pci module already configured"
    fi
  fi

  # Regenerate initramfs
  echo "Regenerating kernel initramfs..."
  sudo update-initramfs -u

  echo "NVIDIA GPU configuration completed successfully"
  echo "GPUs configured for CC mode: $gpu_count"
  echo "Total NVIDIA devices in system: $all_nvidia_devices"
  echo "VFIO-PCI configured with IDs: $combined_pci_ids"
  
  echo ""
  echo "GPU Status Summary:"
  for gpu_bdf in $(echo "$gpu_list" | awk '{print $1}'); do
    echo "- $gpu_bdf: CC mode enabled, VFIO ready"
  done

  echo ""
  echo "IMPORTANT: GPU configuration is persistent across reboots."
  echo "To revert changes, run the following commands:"
  echo "cd ${TMP_DIR}/gpu-admin-tools"
  for gpu_bdf in $(echo "$gpu_list" | awk '{print $1}'); do
    echo "sudo python3 ./nvidia_gpu_tools.py --gpu-bdf=${gpu_bdf} --set-cc-mode=off --reset-after-cc-mode-switch"
  done
  
  return 0
}

setup_cx7_bridge_vfio() {
    echo "Setting up CX7 Bridge devices for VFIO passthrough..."

    # Check IOMMU
    if [ ! -d "/sys/kernel/iommu_groups" ] || [ -z "$(find /sys/kernel/iommu_groups/ -type l 2>/dev/null)" ]; then
        echo "❌ IOMMU not enabled. Adding to GRUB configuration..."
        
        if ! grep -q 'intel_iommu=on' /etc/default/grub; then
            if grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub; then
                sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)/\1 intel_iommu=on iommu=pt/' /etc/default/grub
            else
                echo 'GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"' >> /etc/default/grub
            fi
        fi
        
        echo "⚠️  IOMMU added to GRUB. Reboot required."
        return 1
    fi

    # Find CX7 Bridge devices
    echo "Scanning for CX7 Bridge devices..."
    cx7_devices=()
    for dev_path in /sys/bus/pci/devices/*/; do
        dev_bdf=$(basename "$dev_path")
        vpd_file="${dev_path}vpd"
        
        if [[ -f "$vpd_file" ]] && grep -q "SW_MNG" "$vpd_file" 2>/dev/null; then
            device_info=$(lspci -s "$dev_bdf" 2>/dev/null || echo "Unknown")
            if [[ $device_info == *"Mellanox"* && $device_info == *"ConnectX-7"* ]]; then
                cx7_devices+=("$dev_bdf")
                echo "Found: $dev_bdf"
            fi
        fi
    done

    if [ ${#cx7_devices[@]} -eq 0 ]; then
        echo "No CX7 Bridge devices found (not a B200 system?)"
        return 0
    fi

    # Get PCI ID for CX7 (all should have same vendor:device ID)
    dev_bdf="${cx7_devices[0]}"
    vendor_id=$(cat "/sys/bus/pci/devices/$dev_bdf/vendor" | sed 's/0x//')
    device_id=$(cat "/sys/bus/pci/devices/$dev_bdf/device" | sed 's/0x//')
    cx7_pci_id="${vendor_id}:${device_id}"

    echo "CX7 Bridge PCI ID: $cx7_pci_id"

    # Update VFIO configuration - simply add CX7 PCI ID
    existing_ids=""
    if [ -f /etc/modprobe.d/vfio.conf ]; then
        existing_ids=$(grep -oP '(?<=ids=)[^ ]+' /etc/modprobe.d/vfio.conf 2>/dev/null || true)
    fi

    if [[ -n "$existing_ids" && "$existing_ids" != *"$cx7_pci_id"* ]]; then
        # Add CX7 ID to existing
        new_ids="${existing_ids},${cx7_pci_id}"
    elif [[ -z "$existing_ids" ]]; then
        # No existing IDs, just add CX7
        new_ids="$cx7_pci_id"  
    else
        # Already contains CX7 ID
        new_ids="$existing_ids"
    fi

    echo "options vfio-pci ids=$new_ids" > /etc/modprobe.d/vfio.conf
    echo "Updated /etc/modprobe.d/vfio.conf with: $new_ids"

    # Ensure VFIO modules load
    echo 'vfio-pci' > /etc/modules-load.d/vfio-pci.conf

    # Update initramfs
    update-initramfs -u

    echo "${SUCCESS} CX7 Bridge VFIO setup complete"
    echo "Found ${#cx7_devices[@]} CX7 Bridge devices: ${cx7_devices[*]}"
    echo "After reboot, these will be bound to vfio-pci automatically"

    return 0
}

verify_cx7_vfio_setup() {
    echo "Verifying CX7 Bridge VFIO setup..."
    
    # Check if VFIO modules are loaded
    echo "VFIO modules status:"
    for module in vfio vfio_iommu_type1 vfio_pci; do
        if lsmod | grep -q "^$module"; then
            echo "  ✓ $module: loaded"
        else
            echo "  ✗ $module: not loaded"
        fi
    done
    
    # Check VFIO configuration
    echo ""
    echo "VFIO configuration:"
    if [ -f /etc/modprobe.d/vfio.conf ]; then
        echo "  ✓ /etc/modprobe.d/vfio.conf exists:"
        cat /etc/modprobe.d/vfio.conf | sed 's/^/    /'
    else
        echo "  ✗ /etc/modprobe.d/vfio.conf not found"
    fi
    
    # Check for CX7 Bridge devices
    echo ""
    echo "CX7 Bridge devices status:"
    found_cx7=false
    for dev_path in /sys/bus/pci/devices/*/; do
        dev_bdf=$(basename "$dev_path")
        vpd_file="${dev_path}vpd"
        
        if [[ -f "$vpd_file" ]] && grep -q "SW_MNG" "$vpd_file" 2>/dev/null; then
            found_cx7=true
            current_driver=""
            if [ -L "${dev_path}driver" ]; then
                current_driver=$(readlink "${dev_path}driver" | xargs basename)
            fi
            
            iommu_group=""
            if [ -f "${dev_path}iommu_group/group" ]; then
                iommu_group=$(cat "${dev_path}iommu_group/group")
            fi
            
            echo "  - $dev_bdf: driver=$current_driver, IOMMU group=$iommu_group"
        fi
    done
    
    if [ "$found_cx7" = false ]; then
        echo "  No CX7 Bridge devices found"
    fi
    
    return 0
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
    local basename_file="$(basename "$deb_file")"

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

check_os_version() {
    if [ ! -f /etc/os-release ]; then
        echo -e "${RED}ERROR: Could not determine OS version${NC}"
        echo "This script is designed for Ubuntu 25.04 or higher."
        exit 1
    fi

    . /etc/os-release
    
    if [ "$ID" != "ubuntu" ]; then
        echo -e "${RED}ERROR: Unsupported operating system${NC}"
        echo "This script requires Ubuntu 25.04 or higher."
        echo "Current OS: $PRETTY_NAME"
        exit 1
    fi

    # Extract major version number
    major_version=$(echo "$VERSION_ID" | cut -d. -f1)
    
    if [ "$major_version" -lt 25 ]; then
        echo -e "${RED}ERROR: Unsupported Ubuntu version${NC}"
        echo "This script requires Ubuntu 25.04 or higher."
        echo "Current version: $PRETTY_NAME"
        echo "Please upgrade your system to continue."
        exit 1
    fi
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
    apt update && DEBIAN_FRONTEND=noninteractive apt install -y libslirp0
    
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
    local type=$2

    if [[ "$type" != "tdx" && "$type" != "snp" ]]; then
        echo "Invalid type: $type. Must be 'tdx' or 'snp'." >&2
        return 1
    fi

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
    
    if [[ "$type" == "tdx" ]]; then
        # Add required kernel parameters if not present
        if ! grep -q 'kvm_intel.tdx=on' /etc/default/grub; then
            if grep -q '^GRUB_CMDLINE_LINUX_DEFAULT=' /etc/default/grub; then
                sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)/\1 nohibernate kvm_intel.tdx=on/' /etc/default/grub
            else
                echo 'GRUB_CMDLINE_LINUX_DEFAULT="nohibernate kvm_intel.tdx=on"' >> /etc/default/grub
            fi
        fi
    fi

    if [[ "$type" == "snp" ]]; then
        if grep -q '^GRUB_CMDLINE_LINUX=".*\biommu=pt\b.*"' /etc/default/grub; then
            sed -i '/^GRUB_CMDLINE_LINUX=".*"/ s/\biommu=pt\b//' /etc/default/grub
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
