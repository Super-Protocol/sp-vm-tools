#!/bin/bash
set -e

source_common() {
    local script_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    source "${script_dir}/common.sh"
}

check_snp_status() {
    print_section_header "SNP Status Validation"
    
    local all_good=true
    
    echo "Checking KVM AMD parameters..."
    for param in sev sev_es sev_snp; do
        local param_file="/sys/module/kvm_amd/parameters/$param"
        if [ -f "$param_file" ]; then
            local value=$(cat "$param_file" 2>/dev/null || echo "N")
            printf "  %-12s: %s" "kvm_amd.$param" "$value"
            if [ "$value" = "Y" ]; then
                echo " ✓"
            else
                echo " ✗ FAILED"
                all_good=false
            fi
        else
            echo "  kvm_amd.$param: FILE NOT FOUND ✗"
            all_good=false
        fi
    done
    
    echo ""
    echo "Checking SEV-SNP dmesg messages..."
    
    if dmesg | grep -q "SEV-SNP: RMP table physical range"; then
        local rmp_range=$(dmesg | grep "SEV-SNP: RMP table physical range" | tail -1)
        echo "  RMP Table: ✓ ($rmp_range)"
    else
        echo "  RMP Table: ✗ FAILED - No RMP table found"
        all_good=false
    fi
    
    if dmesg | grep -q "SEV API:"; then
        local sev_api=$(dmesg | grep "SEV API:" | tail -1 | awk '{print $5}')
        echo "  SEV API: ✓ (version $sev_api)"
    else
        echo "  SEV API: ✗ FAILED - No SEV API info"
        all_good=false
    fi
    
    if dmesg | grep -q "SEV-SNP API:"; then
        local snp_api=$(dmesg | grep "SEV-SNP API:" | tail -1 | awk '{print $5}')
        echo "  SEV-SNP API: ✓ (version $snp_api)"
    else
        echo "  SEV-SNP API: ✗ FAILED - No SEV-SNP API info"
        all_good=false
    fi
    
    if dmesg | grep -q "SEV-SNP enabled"; then
        local snp_asids=$(dmesg | grep "SEV-SNP enabled" | tail -1 | sed 's/.*(\(ASIDs.*\))/\1/')
        echo "  SEV-SNP ASIDs: ✓ ($snp_asids)"
    else
        echo "  SEV-SNP ASIDs: ✗ FAILED - No ASID allocation found"
        all_good=false
    fi
    
    if [ "$all_good" = "true" ]; then
        echo ""
        echo "✓ All SNP checks passed"
        return 0
    else
        echo ""
        echo "✗ Some SNP checks failed"
        return 1
    fi
}

check_iommu_configuration() {
    print_section_header "IOMMU Configuration Check"
    
    local all_good=true
    
    if [ -d "/sys/kernel/iommu_groups" ]; then
        local group_count=$(ls /sys/kernel/iommu_groups/ | wc -l)
        echo "  IOMMU Groups: ✓ ($group_count groups found)"
    else
        echo "  IOMMU Groups: ✗ FAILED - IOMMU not enabled"
        all_good=false
    fi
    
    if dmesg | grep -q "AMD-Vi:"; then
        echo "  AMD-Vi: ✓ (AMD IOMMU detected)"
    else
        echo "  AMD-Vi: ⚠ WARNING - No AMD-Vi messages found"
    fi
    
    if lsmod | grep -q vfio; then
        echo "  VFIO Modules: ✓ (VFIO loaded)"
        lsmod | grep vfio | while read line; do
            echo "    - $line"
        done
    else
        echo "  VFIO Modules: ⚠ WARNING - VFIO not loaded"
    fi
    
    return 0
}

check_cpu_performance_settings() {
    print_section_header "CPU Performance Settings"
    
    echo "Checking CPU governor settings..."
    local governors_ok=true
    for cpu in /sys/devices/system/cpu/cpu[0-9]*; do
        if [ -f "$cpu/cpufreq/scaling_governor" ]; then
            local cpu_num=$(basename "$cpu")
            local governor=$(cat "$cpu/cpufreq/scaling_governor")
            if [ "$governor" = "performance" ]; then
                echo "  $cpu_num: ✓ $governor"
            else
                echo "  $cpu_num: ⚠ $governor (recommended: performance)"
                governors_ok=false
            fi
        fi
    done
    
    if [ "$governors_ok" = "false" ]; then
        echo ""
        echo "To set performance governor:"
        echo "  echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"
    fi
    
    echo ""
    echo "Checking C-State configuration..."
    if [ -f "/sys/module/intel_idle/parameters/max_cstate" ]; then
        local max_cstate=$(cat /sys/module/intel_idle/parameters/max_cstate)
        echo "  Intel max_cstate: $max_cstate"
    elif [ -d "/sys/devices/system/cpu/cpu0/cpuidle" ]; then
        echo "  AMD cpuidle states detected"
        local state_count=$(ls /sys/devices/system/cpu/cpu0/cpuidle/ | grep state | wc -l)
        echo "  Available idle states: $state_count"
    else
        echo "  C-State info: Not available"
    fi
}

check_memory_configuration() {
    print_section_header "Memory Configuration for SNP"
    
    echo "Checking huge pages configuration..."
    if [ -f "/proc/meminfo" ]; then
        local hugepages_total=$(grep HugePages_Total /proc/meminfo | awk '{print $2}')
        local hugepages_free=$(grep HugePages_Free /proc/meminfo | awk '{print $2}')
        local hugepage_size=$(grep Hugepagesize /proc/meminfo | awk '{print $2}')
        
        echo "  HugePages Total: $hugepages_total"
        echo "  HugePages Free: $hugepages_free"
        echo "  HugePage Size: ${hugepage_size}kB"
        
        if [ "$hugepages_total" -gt 0 ]; then
            echo "  ✓ Huge pages configured"
        else
            echo "  ⚠ No huge pages configured (may impact VM performance)"
        fi
    fi
    
    local total_mem=$(free -g | awk '/^Mem:/{print $2}')
    local available_mem=$(free -g | awk '/^Mem:/{print $7}')
    echo "  Total Memory: ${total_mem}GB"
    echo "  Available Memory: ${available_mem}GB"
}

run_comprehensive_snp_gpu_check() {
    echo "========================================"
    echo "SNP GPU Passthrough Comprehensive Check"
    echo "========================================"
    echo ""
    
    check_snp_status
    local snp_status=$?
    
    check_iommu_configuration
    check_cpu_performance_settings
    check_memory_configuration
    
    echo ""
    print_section_header "Summary"
    if [ $snp_status -eq 0 ]; then
        echo "✓ SNP is properly configured and ready"
        echo "✓ System appears ready for confidential GPU computing"
        echo ""
        echo "Next steps:"
        echo "1. Configure VM with GPU passthrough"
        echo "2. Ensure host NVIDIA drivers are blacklisted for passthrough GPU"
        echo "3. Test VM boot with SEV-SNP + GPU passthrough"
    else
        echo "✗ SNP configuration issues detected"
        echo "✗ Please resolve SNP issues before proceeding with GPU passthrough"
    fi
    
    return $snp_status
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
                reboot && exit 0
                ;;
            2)
                echo "Continuing without reboot (not recommended)..."
                ;;
        esac
    fi

    SNP_HOST_FILE="$DEB_DIR/snphost"

    if [ -f "${SNP_HOST_FILE}" ]; then
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

    print_section_header "SNP GPU Compatibility Check"
    run_comprehensive_snp_gpu_check
    if [ $? -ne 0 ]; then
        echo -e "${RED}ERROR: SNP GPU compatibility check failed${NC}"
        read -p "Continue anyway? (y/N): " continue_choice
        if [[ "$continue_choice" != "y" && "$continue_choice" != "Y" ]]; then
            exit 1
        fi
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
