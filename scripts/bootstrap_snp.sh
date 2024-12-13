#!/bin/bash
set -e

source_common() {
    local script_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
    source ${script_dir}/common.sh
}


update_snp_firmware() {
    TMP_DIR=$1
    if ! command -v unzip &> /dev/null ; then
        apt-get update && apt-get install -y unzip || return 1
    fi
    echo "Updating SEV firmware..."
    pushd "${TMP_DIR}"
    local firmware_name="amd_sev_fam19h_model0xh_1.55.21"
    wget "https://download.amd.com/developer/eula/sev/${firmware_name}.zip"
    mkdir -p /lib/firmware/amd
    unzip "${firmware_name}.zip"
    cp -vf "${firmware_name}.sbin" /lib/firmware/amd/amd_sev_fam19h_model0xh.sbin
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

    # Download latest release if no archive provided
    print_section_header "Release Package Setup"
    ARCHIVE_PATH=""
    if [ "$#" -ne 1 ]; then
        echo "No archive provided, downloading latest release..."
        ARCHIVE_PATH=$(download_latest_release snp) || {
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
        setup_grub "$NEW_KERNEL_VERSION" "snp"
    fi

    print_section_header "SNP Firmware Update"
    echo "Updating SNP firmware..."
    update_snp_firmware "${TMP_DIR}"

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

    SNP_HOST_FILE="$DEB_DIR/snphost"
    LIBSEV_FILE="$DEB_DIR/libsev.so"

    if [ -f "${SNP_HOST_FILE}" ] && [ -f "${LIBSEV_FILE}" ]; then
        echo "Running configuration check..."
        pushd $DEB_DIR
        ./snphost ok
        if [ $? -ne 0 ]; then
            echo -e "${RED}ERROR: some checks failed${NC}"
            exit 1
        fi
        popd
    else
        echo -e "${RED}ERROR: snphost or or its components not found${NC}"
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

source_common

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Script was sourced"
else
  bootstrap $@
fi
