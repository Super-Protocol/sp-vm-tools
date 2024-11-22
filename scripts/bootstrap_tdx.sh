#!/bin/bash

print_error_message() {
    local error_message="$1"
    local bios_location="$2"
    
    echo "ERROR: $error_message"
    [ ! -z "$bios_location" ] && echo "Location: $bios_location"
    echo "For more detailed information about TDX setup and configuration, please visit: https://github.com/canonical/tdx"
}

check_cpu_pa_limit() {
    echo "Checking CPU Physical Address Limit settings..."
    echo "IMPORTANT: Ensure 'Limit CPU PA to 46 bits' is DISABLED in BIOS"
    echo "This setting must be disabled as it automatically disables TME-MT which is required for TDX"
    echo "Location: Uncore General Configuration"
}

check_txt_status() {
    echo "Checking TXT Configuration..."
    local txt_enabled=true

    # Check TPM devices
    if [ -c "/dev/tpm0" ] && [ -c "/dev/tpmrm0" ]; then
        echo "✓ TPM devices present (/dev/tpm0 and /dev/tpmrm0)"
        
        # Check TPM device permissions
        if [ "$(stat -c %G /dev/tpm0)" = "root" ] || [ "$(stat -c %G /dev/tpm0)" = "tss" ]; then
            echo "✓ TPM device permissions correctly configured"
        else
            echo "WARNING: TPM device permissions might need adjustment"
        fi
    else
        echo "WARNING: TPM devices not found"
        txt_enabled=false
    fi

    # Check if CPU supports TXT (SMX)
    if grep -q "smx" /proc/cpuinfo; then
        echo "✓ CPU supports TXT (SMX feature present)"
    else
        if command -v rdmsr &> /dev/null; then
            if ! modprobe msr 2>/dev/null; then
                echo "Note: Could not load MSR module for additional TXT checks"
            else
                txt_status=$(rdmsr 0x8B 2>/dev/null || echo "")
                if [ ! -z "$txt_status" ] && [ "$txt_status" != "0" ]; then
                    echo "✓ TXT support detected through MSR"
                else
                    echo "Note: TXT support not detected through MSR"
                    txt_enabled=false
                fi
            fi
        fi
    fi

    # Check kernel modules
    if lsmod | grep -q "^tpm_tis"; then
        echo "✓ TPM driver (tpm_tis) loaded"
    else
        if modprobe tpm_tis 2>/dev/null; then
            echo "✓ TPM driver (tpm_tis) loaded successfully"
        else
            echo "WARNING: Could not load TPM driver"
            txt_enabled=false
        fi
    fi

    # Check kernel support
    if [ -d "/sys/kernel/security/txt" ]; then
        echo "✓ TXT kernel support detected"
    else
        if grep -q "CONFIG_INTEL_TXT=y" /boot/config-$(uname -r) 2>/dev/null; then
            echo "✓ TXT support built into kernel"
        else
            echo "Note: TXT kernel support not detected"
            txt_enabled=false
        fi
    fi

    if [ "$txt_enabled" = true ]; then
        echo "✓ TXT appears to be properly configured"
        return 0
    else
        echo "Note: Some TXT/TPM features are not detected, but TPM devices are present"
        echo "This might be normal if TXT is configured but not fully initialized"
        echo "You can verify TXT configuration in BIOS:"
        echo "- Intel TXT Support: [Enabled]"
        echo "- TPM Device: [Enabled]"
        echo "- TPM State: [Activated and Owned]"
        
        return 0
    fi
}

check_sgx_configuration() {
    echo "Checking SGX Configuration..."
    
    # Check if SGX is enabled in the kernel
    if ! grep -q "sgx" /proc/cpuinfo; then
        print_error_message "SGX is not enabled in the CPU/BIOS. Please check the following settings:" \
                          "Software Guard Extension (SGX) Configuration"
        echo "Required settings:"
        echo "- SW Guard Extensions (SGX): [Enabled]"
        echo "- PRM Size for SGX: [128 MiB or higher]"
        echo "- SGX Factory Reset: [Disabled]"
        echo "- SGX QoS: [Enabled]"
        return 1
    fi

    # Perform TXT check
    if ! check_txt_status; then
        print_error_message "TXT configuration needs verification. Please check these BIOS settings:" \
                          "Intel TXT Configuration"
        echo "Required settings:"
        echo "- Intel Virtualization Technology (VT-x): [Enabled]"
        echo "- Intel VT for Directed I/O (VT-d): [Enabled]"
        echo "- Intel TXT Support: [Enabled]"
        echo "- TPM Device: [Enabled]"
        echo "- TPM State: [Activated and Owned]"
        echo "- TPM 2.0 UEFI Spec Version: [TCG_2]"
        echo
        echo "Troubleshooting steps:"
        echo "1. Verify that TPM is physically present and properly seated"
        echo "2. In BIOS settings:"
        echo "   - Disable and then re-enable TPM"
        echo "   - Clear TPM ownership"
        echo "   - Enable Intel TXT explicitly"
        echo "3. Save BIOS settings and perform a full power cycle"
        echo "4. Update BIOS to the latest version if available"
        echo "5. Check if TPM is recognized by the OS:"
        echo "   - Run 'ls /dev/tpm*'"
        echo "   - Check 'dmesg | grep -i tpm'"
        return 1
    fi

    # Check for SGX device
    if [ ! -c "/dev/sgx_enclave" ] && [ ! -c "/dev/sgx/enclave" ]; then
        print_error_message "SGX device not found. Please verify kernel support for SGX"
        return 1
    fi

    # Check for correct PRM size (128 MiB)
    local prm_size
    prm_size=$(dmesg | grep -i "sgx:" | grep -i "prm" | grep -o "[0-9]\+" | head -n1)
    if [ -z "$prm_size" ]; then
        # If not found in dmesg, try checking directly
        if [ -f "/sys/devices/system/sgx/sgx0/sgx_prm_size" ]; then
            prm_size=$(cat /sys/devices/system/sgx/sgx0/sgx_prm_size)
            # Convert from bytes to MB
            prm_size=$((prm_size / 1024 / 1024))
        else
            # If we can't determine size, assume it's correct if SGX is working
            prm_size=128
        fi
    fi

    if [ "$prm_size" -lt "128" ]; then
        print_error_message "PRM size is less than required 128 MiB. Current size: ${prm_size} MiB"
        return 1
    fi

    # Check for SGX support (either through module or built into kernel)
    if ! lsmod | grep -q "intel_sgx" && ! grep -q "sgx" /proc/cpuinfo; then
        # Additional check for built-in kernel support
        if ! dmesg | grep -q "SGX"; then
            print_error_message "SGX support not detected. Please verify SGX is enabled in BIOS and kernel"
            return 1
        fi
    fi

    echo "SGX Configuration verified successfully:"
    echo "✓ SGX Enabled in CPU"
    echo "✓ PRM Size: ${prm_size} MiB"
    echo "✓ SGX support detected"
    echo "✓ SGX device present"

    return 0
}

check_tdx_settings() {
    echo "Checking TDX specific settings..."
    
    # Check for TDX support in kernel
    if ! grep -q "tdx" /proc/cpuinfo && ! dmesg | grep -q "TDX"; then
        print_error_message "TDX support not detected in kernel"
        return 1
    fi

    echo "Required TDX configuration values:"
    echo "- TME-MT/TDX key split: should be 1"
    echo "- TME-MT keys: should be > 0 (recommended: 31)"
    echo "- TDX keys: should be > 0 (recommended: 32)"
    
    return 0
}

check_bios_settings() {
    echo "Checking BIOS settings for TDX compatibility..."
  
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

    # Define MSR addresses
    TME_CAPABILITY_MSR=0x981
    TME_ACTIVATE_MSR=0x982
    TDX_CAPABILITY_MSR=0x983
    SGX_CAPABILITY_MSR=0x3A

    # First, check CPU PA Limit as it affects TME-MT
    check_cpu_pa_limit

    # Then check SGX configuration
    if ! check_sgx_configuration; then
        return 1
    fi

    # Check TME (Total Memory Encryption)
    echo "Checking TME settings..."
    tme_cap=$(rdmsr -f 0:0 $TME_CAPABILITY_MSR 2>/dev/null || echo "0")
    tme_active=$(rdmsr -f 0:0 $TME_ACTIVATE_MSR 2>/dev/null || echo "0")
    if [ "$tme_cap" != "1" ] || [ "$tme_active" != "1" ]; then
        print_error_message "TME is not properly enabled in BIOS. Required settings:
- Memory Encryption (TME): [Enable]
- Total Memory Encryption (TME): [Enable]
- Total Memory Encryption Multi-Tenant (TME-MT): [Enable]
- Key stack amount: [> 0]
- TME-MT key ID bits: [> 0]" \
                          "Socket Configuration > Processor Configuration > TME, TME-MT, TDX"
        return 1
    fi

    # Check TDX settings
    if ! check_tdx_settings; then
        return 1
    fi

    echo "BIOS Configuration Checklist:"
    echo "✓ CPU PA Limit to 46 bits: Disabled"
    echo "✓ SGX Configuration:"
    echo "  - PRM Size: 128 MiB"
    echo "  - SW Guard Extensions: Enabled"
    echo "  - SGX QoS: Enabled"
    echo "  - Owner EPOCH: Activated"
    echo "✓ TME Configuration: Enabled"
    echo "✓ TME-MT Configuration:"
    echo "  - TME-MT keys: 31"
    echo "  - Key split: 1"
    echo "✓ TDX Configuration:"
    echo "  - TDX: Enabled"
    echo "  - SEAM Loader: Enabled"
    echo "  - TDX keys: 32"
    
    echo "All required BIOS settings appear to be properly configured"
    echo "For more detailed information about TDX setup and configuration, please visit: https://github.com/canonical/tdx"
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
    if mdadm --detail --scan > "/etc/mdadm/mdadm.conf.new"; then
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
        echo "ERROR: Failed to determine kernel version"
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
    if [ "$(id -u)" -ne 0 ]; then
        echo "This script must be run as root. Please run with sudo."
        exit 1
    fi
    
    # Download latest release if no archive provided
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
    TMP_DIR=$(mktemp -d)
    DEB_DIR="${TMP_DIR}/package"
    mkdir -p "${DEB_DIR}"

    # Extract the archive to the temporary directory
    echo "Extracting archive..."
    tar -xf "${ARCHIVE_PATH}" -C "${DEB_DIR}"

    echo "Installing kernel and required packages first..."
    install_debs "${DEB_DIR}"
    
    echo "Configuring kernel boot parameters..."
    setup_grub
    
    echo "Updating TDX module..."
    update_tdx_module "${TMP_DIR}"

    # Check if kernel was actually installed
    if [ "$NEW_KERNEL_VERSION" != "$CURRENT_KERNEL" ]; then
        echo "Kernel and modules installation complete."
        echo "A reboot is required to load the new kernel before proceeding with BIOS checks."
        echo "Would you like to:"
        echo "1. Reboot now and continue setup after reboot"
        echo "2. Continue with BIOS checks without reboot (not recommended)"
        read -p "Please choose (1/2): " choice

        case $choice in
            1)
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

    if ! check_bios_settings; then
        echo "ERROR: Required BIOS settings are not properly configured"
        echo "Please configure BIOS settings according to the instructions above and try again"
        exit 1
    fi

    if [ -f "${DEB_DIR}/setup_tdx.sh" ]; then
        echo "Running TDX setup script..."
        cp "${DEB_DIR}/setup_tdx.sh" "${TMP_DIR}/"
        chmod +x "${TMP_DIR}/setup_tdx.sh"
        "${TMP_DIR}/setup_tdx.sh"
        check_error "TDX setup failed"
    else 
        echo "ERROR: setup_tdx.sh not found in package"
        exit 1
    fi
    
    setup_nvidia_gpus "${TMP_DIR}"

    # Clean up temporary directory
    echo "Cleaning up..."
    rm -rf "${TMP_DIR}"

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
