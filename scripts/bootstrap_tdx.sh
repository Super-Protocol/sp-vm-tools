#!/bin/bash
set -e

source_common() {
    local script_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    source "${script_dir}/common.sh"
    source "${script_dir}/setup_libvirt_host.sh"
}

usage() {
    cat <<EOF
Usage: sudo $0 [--gpu-mode auto|cc|ppcie]

  auto    Detect the platform from PCI/VPD data (default).
  cc      Force regular CC mode (standalone/PCIe GPUs and Blackwell NVLink).
  ppcie   Force Protected PCIe mode (Hopper NVSwitch multi-GPU only).
EOF
}

parse_args() {
    GPU_MODE="auto"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --gpu-mode)
                [[ $# -ge 2 ]] || { echo "ERROR: --gpu-mode requires a value"; return 1; }
                GPU_MODE="$2"
                shift 2
                ;;
            --gpu-mode=*)
                GPU_MODE="${1#*=}"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "ERROR: Unknown argument: $1"
                usage
                return 1
                ;;
        esac
    done

    case "${GPU_MODE}" in
        auto|cc|ppcie) ;;
        *) echo "ERROR: Invalid --gpu-mode '${GPU_MODE}'"; return 1 ;;
    esac
}

bootstrap() {
    parse_args "$@"
    check_os_version "24.04"
    get_supported_ubuntu_version || return 1

    # Check if the script is running as root
    print_section_header "Privilege Check"
    if [ "$(id -u)" -ne 0 ]; then
        echo "This script must be run as root. Please run with sudo."
        exit 1
    fi

    # Install the pinned Canonical HWE kernel, project TDX QEMU and host
    # attestation runtime.
    print_section_header "TDX Host Setup"
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

    setup_libvirt_host tdx || {
        echo -e "${RED}ERROR: libvirt host setup failed${NC}"
        return 1
    }

    print_section_header "Hardware Configuration"
    if command -v lspci >/dev/null; then
        echo "Checking NVIDIA GPU configuration..."
        setup_nvidia_gpus "${TMP_DIR}" "${GPU_MODE}"
        setup_cx7_bridge_vfio "intel_iommu=on"
        verify_cx7_vfio_setup
    else
        echo "Skipping NVIDIA GPU check (lspci not found)"
    fi    

    # Clean up temporary directory
    print_section_header "Cleanup"
    echo "Cleaning up..."
    rm -rf "${TMP_DIR}"

    print_section_header "Installation Status"
    echo "TDX host installation complete."
    echo "System reboot required to activate TDX."
    echo "After reboot, re-run this bootstrap to finish validation."
}

source_common

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Script was sourced"
else
  bootstrap "$@"
fi
