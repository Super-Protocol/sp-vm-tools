#!/bin/bash
set -e

source_common() {
    local script_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    source "${script_dir}/common.sh"
}

bootstrap() {
    check_os_version "24.04"

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

    local script_dir="$(dirname "${BASH_SOURCE[0]}")"
    if [ -f "${script_dir}/setup_tdx.sh" ]; then
        echo "Running TDX setup script..."
        # setup_tdx.sh runs from TMP_DIR and sources common.sh, so copy both.
        cp "${script_dir}/setup_tdx.sh" "${script_dir}/common.sh" "${TMP_DIR}/"
        chmod +x "${TMP_DIR}/setup_tdx.sh"
        local rc=0
        "${TMP_DIR}/setup_tdx.sh" "${TMP_DIR}" || rc=$?
        if [ "$rc" -eq 2 ]; then
            # Kernel installed but not booted yet — stop and ask for a reboot.
            print_section_header "Reboot required"
            echo "Reboot into the TDX kernel, then re-run this script to finish setup."
            rm -rf "${TMP_DIR}"
            exit 0
        elif [ "$rc" -ne 0 ]; then
            echo -e "${RED}ERROR: TDX setup failed${NC}"
            exit 1
        fi
    else
        echo -e "${RED}ERROR: setup_tdx.sh not found${NC}"
        exit 1
    fi

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
