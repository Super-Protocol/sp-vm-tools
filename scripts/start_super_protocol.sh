#!/bin/bash

# Default values
SCRIPT_DIR=$( cd "$( dirname "$0" )" && pwd )

REQUIRED_PACKAGES=("s3cmd" "sp-qemu-tdx")

S3_ACCESS_KEY="jxekrow2wxmjps6pr2jv22hamtha"
S3_SECRET_KEY="jztnpl532njcljtdolnpbszq66lgqmwmgkbh747342hwc72grkohi"
S3_ENDPOINT="gateway.storjshare.io"
S3_BUCKET="builds-vm"
RELEASE_REPO="Super-Protocol/sp-vm"
RELEASE_ASSET="vm.json"

DEFAULT_CORES=$(( $(nproc) - 2 )) # All cores minus 2
DEFAULT_MEM=$(( $(free -g | awk '/^Mem:/{print $2}') - 8 ))
DEFAULT_CACHE="${HOME}/.cache/superprotocol" # Default cache path
DEFAULT_MOUNT_CONFIG="/sp"

DEFAULT_SSH_PORT=2222

LOG_FILE=""
DEFAULT_MAC_PREFIX="52:54:00:12:34"
DEFAULT_MAC_SUFFIX="56"
QEMU_PATH="/usr/local/bin/qemu-system-x86_64"
DEFAULT_DEBUG=false

TDX_SUPPORT=$(lscpu | grep -i tdx)
SEV_SUPPORT=$(lscpu | grep -i sev)

# Add default mode
DEFAULT_MODE="untrusted"  # Can be "untrusted", "tdx", or "sev"

# Function to display usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --cores <number>             Number of CPU cores (default: ${DEFAULT_CORES})"
    echo "  --mem <size>                 Amount of memory (default: ${DEFAULT_MEM})"
    echo "  --gpu <gpu_id>               Specify GPU(s) (default: all available gpu, specify --gpu none to disable gpu passthrough)"
    echo "  --disk_path <path>           Path to disk image (default: <cache>/state_disk.qcow2)"
    echo "  --disk_size <size>           Size of disk (default: autodetermining, but no less than 512G)"
    echo "  --cache <path>               Cache directory (default: ${DEFAULT_CACHE})"
    echo "  --provider_config <file>     Provider configuration file (default: no)"
    echo "  --mac_address <address>      MAC address (default: ${DEFAULT_MAC_PREFIX}:${DEFAULT_MAC_SUFFIX})"
    echo "  --ssh_port <port>            SSH port (default: ${DEFAULT_SSH_PORT})"
    echo "  --log_file <file>            Log file (default: no)"
    echo "  --debug <true|false>         Enable debug mode (default: ${DEFAULT_DEBUG})"
    echo "  --release <name>             Release name (default: latest)"
    echo "  --mode <mode>                VM mode: untrusted, tdx, sev (default: ${DEFAULT_MODE})"
    echo ""
}

# [Previous functions remain unchanged until parse_args]

parse_args() {
    # Parse arguments
    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --cores) VM_CPU=$2; shift ;;
            --mem) VM_RAM=$(echo $2 | sed 's/G//'); shift ;;
            --gpu) USED_GPUS+=("$2"); shift ;;
            --disk_path) STATE_DISK_PATH=$2; shift ;;
            --disk_size) STATE_DISK_SIZE=$2; shift ;;
            --cache) CACHE=$2; shift ;;
            --provider_config) PROVIDER_CONFIG=$2; shift ;;
            --mount_config) MOUNT_CONFIG=$2; shift ;;
            --mac_address) MAC_ADDRESS=$2; shift ;;
            --ssh_port) SSH_PORT=$2; shift ;;
            --log_file) LOG_FILE=$2; shift ;;
            --debug) DEBUG_MODE=$2; shift ;;
            --release) RELEASE=$2; shift ;;
            --mode) VM_MODE=$2; shift ;;
            --help) usage; exit 0;;
            *) echo "Unknown parameter: $1"; usage ; exit 1 ;;
        esac
        shift
    done

    # Set default mode if not specified
    VM_MODE=${VM_MODE:-$DEFAULT_MODE}
}

# [Previous functions remain unchanged until main]

main() {
    check_packages
    check_params

    mkdir -p "${CACHE}"
    download_release "${RELEASE}" "${RELEASE_ASSET}" "${CACHE}" "${RELEASE_REPO}"
    parse_and_download_release_files ${RELEASE_FILEPATH}

    # Prepare QEMU command with GPU passthrough and chassis increment
    GPU_PASSTHROUGH=""
    CHASSIS=1
    for GPU in "${USED_GPUS[@]}"; do
        GPU_PASSTHROUGH+=" -object iommufd,id=iommufd$CHASSIS"
        GPU_PASSTHROUGH+=" -device pcie-root-port,id=pci.$CHASSIS,bus=pcie.0,chassis=$CHASSIS"
        GPU_PASSTHROUGH+=" -device vfio-pci,host=$GPU,bus=pci.$CHASSIS,iommufd=iommufd$CHASSIS"
        GPU_PASSTHROUGH+=" -fw_cfg name=opt/ovmf/X-PciMmio64,string=262144"
        CHASSIS=$((CHASSIS + 1))
    done

    # Initialize machine parameters based on mode
    MACHINE_PARAMS=""
    CC_PARAMS=""
    CC_SPECIFIC_PARAMS=""

    case ${VM_MODE} in
        "tdx")
            if [[ ! $TDX_SUPPORT ]]; then
                echo "Error: TDX is not supported on this system"
                exit 1
            fi
            CC_PARAMS+=" -object memory-backend-ram,id=mem0,size=${VM_RAM}G "
            MACHINE_PARAMS="q35,kernel-irqchip=split,confidential-guest-support=tdx,memory-backend=mem0"
            CC_SPECIFIC_PARAMS=" -object '{\"qom-type\":\"tdx-guest\",\"id\":\"tdx\",\"quote-generation-socket\":{\"type\":\"vsock\",\"cid\":\"${BASE_CID}\",\"port\":\"4050\"}}'"
            ;;
        "sev")
            if [[ ! $SEV_SUPPORT ]]; then
                echo "Error: SEV is not supported on this system"
                exit 1
            fi
            MACHINE_PARAMS="q35,kernel_irqchip=split,confidential-guest-support=sev0"
            CC_PARAMS+=" -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1"
            ;;
        "untrusted")
            MACHINE_PARAMS="q35"
            ;;
        *)
            echo "Error: Invalid mode '${VM_MODE}'. Must be 'untrusted', 'tdx', or 'sev'."
            exit 1
            ;;
    esac

    NETWORK_SETTINGS=" -device virtio-net-pci,netdev=nic_id$BASE_NIC,mac=$MAC_ADDRESS"
    NETWORK_SETTINGS+=" -netdev user,id=nic_id$BASE_NIC"
    DEBUG_PARAMS=""
    KERNEL_CMD_LINE=""
    ROOT_HASH=$(grep 'Root hash' "${ROOTFS_HASH_PATH}" | awk '{print $3}')
    
    if [[ ${DEBUG_MODE} == true ]]; then
        NETWORK_SETTINGS+=",hostfwd=tcp:127.0.0.1:$SSH_PORT-:22"
        KERNEL_CMD_LINE="root=/dev/vda1 console=ttyS0 clearcpuid=mtrr systemd.log_level=trace systemd.log_target=log rootfs_verity.scheme=dm-verity rootfs_verity.hash=${ROOT_HASH} sp-debug=true"
    else
        KERNEL_CMD_LINE="root=/dev/vda1 clearcpuid=mtrr rootfs_verity.scheme=dm-verity rootfs_verity.hash=${ROOT_HASH}"
    fi

    QEMU_COMMAND="${QEMU_PATH} \
    -enable-kvm \
    -append \"${KERNEL_CMD_LINE}\" \
    -drive file=${ROOTFS_PATH},if=virtio,format=raw \
    -drive file=${STATE_DISK_PATH},if=virtio,format=qcow2 \
    -kernel ${KERNEL_PATH} \
    -smp cores=${VM_CPU} \
    -m ${VM_RAM}G \
    -cpu host \
    -machine ${MACHINE_PARAMS} \
    ${CC_SPECIFIC_PARAMS} \
    ${NETWORK_SETTINGS} \
    -nographic \
    ${CC_PARAMS} \
    -bios ${BIOS_PATH} \
    -vga none \
    -nodefaults \
    -serial stdio \
    -device vhost-vsock-pci,guest-cid=3 \
    ${GPU_PASSTHROUGH} \
    "

    if [ -n "${PROVIDER_CONFIG}" ] && [ -d "${PROVIDER_CONFIG}" ]; then
        QEMU_COMMAND+=" -fsdev local,security_model=passthrough,id=fsdev0,path=${PROVIDER_CONFIG} \
                    -device virtio-9p-pci,fsdev=fsdev0,mount_tag=sharedfolder"
    fi

    # Create VM state disk
    rm -f ${STATE_DISK_PATH}
    qemu-img create -f qcow2 ${STATE_DISK_PATH} ${STATE_DISK_SIZE}G
    echo "Starting QEMU with the following command:"

    NORMALIZED_COMMAND=$(echo "$QEMU_COMMAND" | tr -s ' ')
    # Replace " -" with a newline
    ARGS_SPLIT=$(echo "$NORMALIZED_COMMAND" | sed 's/ -/\n-/g')
    # Output each argument on a new line
    echo -e "$ARGS_SPLIT"

    eval $QEMU_COMMAND
}

parse_args $@
main
