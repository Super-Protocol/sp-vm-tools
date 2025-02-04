#!/bin/bash
set -e

source_common() {
    local script_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    source "${script_dir}/common.sh"
}

update_snp_firmware() {
    TMP_DIR=$1
    local model=$2

    local firmware_name=""
    local destination_filename=""
    if [[ "$model" == "Milan" ]]; then
        firmware_name="amd_sev_fam19h_model0xh_1.55.21"
        destination_filename="amd_sev_fam19h_model0xh.sbin"
    elif [[ "$model" == "Genoa" ]]; then
        firmware_name="amd_sev_fam19h_model1xh_1.55.37"
        destination_filename="amd_sev_fam19h_model1xh.sbin"
    else
        echo "Skipping firmware update: Model is not Milan or Genoa."
        return 0
    fi

    if ! command -v unzip &> /dev/null ; then
        apt-get update && apt-get install -y unzip || return 1
    fi

    pushd "${TMP_DIR}"
    wget "https://download.amd.com/developer/eula/sev/${firmware_name}.zip"
    if [ $? -ne 0 ]; then
        echo "Failed to download ${firmware_name}.zip"
        return 1
    fi
    mkdir -p /lib/firmware/amd
    unzip "${firmware_name}.zip"
    cp -vf "${firmware_name}.sbin" "/lib/firmware/amd/${destination_filename}"
    popd
}

bootstrap() {
    check_os_version

    CPU_MODEL=$(lscpu | grep "^Model name:" | sed 's/Model name: *//')

    if [[ ! "$CPU_MODEL" =~ "AMD" ]]; then
        echo "ERROR: This script is only intended for AMD processors."
        exit 1
    fi

    AMD_GEN="unknown"
    if [[ "$CPU_MODEL" =~ ^AMD[[:space:]]*EPYC[[:space:]]*7[0-9]{2}3.*$ ]]; then
        echo "AMD Milan CPU Found"
        AMD_GEN="Milan"
    elif [[ "$CPU_MODEL" =~ ^AMD[[:space:]]*EPYC[[:space:]]*9[0-9]{2}4.*$ ]]; then
        echo "This processor is AMD Genoa."
        AMD_GEN="Genoa"
    else
        echo "Unknown CPU model: <$CPU_MODEL>"
        read -p "Do you want to continue? (y/n): " choice
        if [[ "$choice" != "y" ]]; then
            echo "Exiting script."
            exit 1
        fi
    fi

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
        ARCHIVE_PATH=$(download_latest_release "snp") || {
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
    update_snp_firmware "${TMP_DIR}" "${AMD_GEN}"

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
        set +e
        ./snphost ok
        if [ $? -ne 0 ]; then
            echo -e "${RED}ERROR: some checks failed${NC}"
            exit 1
        fi
        set -e
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
  bootstrap "$@"
fi
