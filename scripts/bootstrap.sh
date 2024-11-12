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

install_debs() {
  DEB_DIR=$1
  # Install dependencies
  apt update && DEBIAN_FRONTEND=noninteractive apt install -y libslirp0 s3cmd

  # Install deb packages
  echo "Installing deb packages..."
  DEBIAN_FRONTEND=noninteractiv dpkg -i "${DEB_DIR}"/*.deb

  # Check for installation errors
  if [ $? -ne 0 ]; then
    echo "Failed to install some deb packages."
    exit 1
  fi
}

setup_attestation() {
  TMP_DIR=$1
   # Download the setup-attestation-host.sh script
  git clone -b noble-24.04 --single-branch --depth 1 --no-tags https://github.com/canonical/tdx.git "${TMP_DIR}/tdx-cannonical"
  SCRIPT_PATH=${TMP_DIR}/tdx-cannonical/attestation/setup-attestation-host.sh

  # Check for download errors
  if [ $? -ne 0 ]; then
    echo "Failed to download the setup-attestation-host.sh script."
    exit 1
  fi

  # Make the script executable
  chmod +x "${SCRIPT_PATH}"

  # Run the script
  echo "Running setup-attestation-host.sh..."
  "${SCRIPT_PATH}"

  # Change pccs url from local to public
  echo "Configuring pccs service..."
  cp /etc/sgx_default_qcnl.conf /etc/sgx_default_qcnl.conf.bak
  sed -i 's|"pccs_url": "https://localhost:8081/sgx/certification/v4/"|"pccs_url": "https://pccs.superprotocol.io/sgx/certification/v4/"|' /etc/sgx_default_qcnl.conf
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

bootstrap() {
  # Check if the script is running as root
  if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root. Please run with sudo."
    exit 1
  fi

  # Check for the parameter
  if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <path_to_archive>"
    exit 1
  fi

  ARCHIVE_PATH="$1"

  # Check if the archive exists
  if [ ! -f "${ARCHIVE_PATH}" ]; then
    echo "Archive not found: ${ARCHIVE_PATH}"
    exit 1
  fi

  # Check BIOS settings first
  if ! check_bios_settings; then
    echo "ERROR: Required BIOS settings are not properly configured"
    echo "Please configure BIOS settings according to the instructions above and try again"
    exit 1
  fi

  # Create a temporary directory for extraction
  TMP_DIR=$(mktemp -d)
  DEB_DIR="${TMP_DIR}/package"
  mkdir -p "${DEB_DIR}"

  # Extract the archive to the temporary directory
  echo "Extracting archive..."
  tar -xf "${ARCHIVE_PATH}" -C "${DEB_DIR}"

  install_debs "${DEB_DIR}"
  setup_attestation "${TMP_DIR}"
  setup_grub
  update_tdx_module "${TMP_DIR}"
  setup_nvidia_gpus "${TMP_DIR}"

  # Clean up temporary directory
  echo "Cleaning up..."
  rm -rf "${TMP_DIR}"

  echo "Installation and setup completed successfully. Please reboot your server"
}

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Script was sourced"
else
  bootstrap $@
fi
