#!/bin/bash
set -e

source_common() {
    local script_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    source "${script_dir}/common.sh"
}

update_tdx_module() {
  TMP_DIR=$1
  echo "Updating TDX-module..."
  pushd "${TMP_DIR}"
  wget https://github.com/intel/confidential-computing.tdx.tdx-module/releases/download/TDX_MODULE_2.0.14/intel_tdx_module.tar.gz
  tar -xvzf intel_tdx_module.tar.gz
  mkdir -p /boot/efi/EFI/TDX/
  cp -vf TDX-Module/intel_tdx_module.so /boot/efi/EFI/TDX/TDX-SEAM.so
  cp -vf TDX-Module/intel_tdx_module.so.sigstruct /boot/efi/EFI/TDX/TDX-SEAM.so.sigstruct
  popd
}

bootstrap() {
    check_os_version

    # Check if the script is running as root
    print_section_header "Privilege Check"
    if [ "$(id -u)" -ne 0 ]; then
        echo "This script must be run as root. Please run with sudo."
        exit 1
    fi

    # Download and setup official Canonical TDX
    print_section_header "Official TDX Setup"
    TMP_DIR=$(mktemp -d)

    echo "Installing required tools..."
    apt update && apt install -y unzip wget

    if [ -f "$(dirname "${BASH_SOURCE[0]}")/setup_tdx.sh" ]; then
        echo "Running TDX setup script..."
        cp "$(dirname "${BASH_SOURCE[0]}")/setup_tdx.sh" "${TMP_DIR}/"
        chmod +x "${TMP_DIR}/setup_tdx.sh"
        if ! "${TMP_DIR}/setup_tdx.sh"; then
            echo -e "${RED}ERROR: TDX setup failed${NC}"
            exit 1
        fi
    else 
        echo -e "${RED}ERROR: setup_tdx.sh not found${NC}"
        exit 1
    fi
    
    print_section_header "TDX Module Update"
    echo "Updating TDX module..."
    update_tdx_module "${TMP_DIR}"

    print_section_header "Hardware Configuration"
    if command -v lspci >/dev/null; then
        echo "Checking NVIDIA GPU configuration..."
        setup_nvidia_gpus "${TMP_DIR}" || true
        setup_cx7_bridge_vfio
        verify_cx7_vfio_setup
    else
        echo "Skipping NVIDIA GPU check (lspci not found)"
    fi    

    # Clean up temporary directory
    print_section_header "Cleanup"
    echo "Cleaning up..."
    rm -rf "${TMP_DIR}"

    print_section_header "Installation Status"
    echo "Official TDX installation complete."
    echo "System reboot required to activate TDX."
    echo "After reboot, use official tools to create and run TDs."
}

source_common

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Script was sourced"
else
  bootstrap "$@"
fi
