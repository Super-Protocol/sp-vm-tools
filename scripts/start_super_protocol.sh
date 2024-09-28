#!/bin/bash

# @TODO add example / help
# ./start-sp-vm.sh --use_gpu 8a:00.0 --use_gpu 89:00.0 --cpu 30 --mem 350 --debug on
# @TODO add function for download VM image, kernel, bios, rootfs from github releases
# @TODO check passed gpus with available in host
# @TODO check all required files and dirs are present on host

# Default values
SCRIPT_DIR=$( cd "$( dirname "$0" )" && pwd )

DEFAULT_VM_DISK_SIZE=1000
DEFAULT_CORES=$(( $(nproc) - 2 )) # All cores minus 2
DEFAULT_MEM=$(( $(free -g | awk '/^Mem:/{print $2}') - 8 ))
DEFAULT_CACHE="${HOME}/.cache/superprotocol" # Default cache path

DEFAULT_SSH_PORT=2222

PROVIDER_CONFIG_SRC="$SCRIPT_DIR/provider_config"
#ROOT_HASH=$(grep 'Root hash' "$SCRIPT_DIR/root_hash.txt" | awk '{print $3}')
LOG_FILE="$SCRIPT_DIR/vm_log_$(date +"%FT%H%M").log"
DEFAULT_MAC_PREFIX="52:54:00:12:34"
DEFAULT_MAC_SUFFIX="56"
QEMU_PATH="/usr/local/bin/qemu-system-x86_64"
QEMU_COMMAND="${QEMU_PATH}"
DEFAULT_DEBUG=false


# Function to display usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --cores <number>             Number of CPU cores (default: ${DEFAULT_CORES})"
    echo "  --mem <size>                 Amount of memory (default: ${DEFAULT_MEM})"
    echo "  --gpu <gpu_id>               Specify GPU(s) (default: no passthrough gpu)"
    echo "  --disk_path <path>           Path to disk image (default: <cache>/state_disk.qcow2)"
    echo "  --disk_size <size>           Size of disk (default: autodetermining)"
    echo "  --cache <path>               Cache directory (default: ${DEFAULT_CACHE})"
    echo "  --provider_config <file>     Provider configuration file (default: ${DEFAULT_PROVIDER_CONFIG})"
    echo "  --mount_config <path>        Mount configuration directory (default: ${DEFAULT_MOUNT_CONFIG})"
    echo "  --mac_address <address>      MAC address (default: ${DEFAULT_MAC_PREFIX}:${DEFAULT_MAC_SUFFIX})"
    echo "  --ssh_port <port>            SSH port (default: ${DEFAULT_SSH_PORT})"
    echo "  --log_file <file>            Log file (default: ${LOG_FILE})"
    echo "  --debug <true|false>         Enable debug mode (default: ${DEFAULT_DEBUG})"
    echo "  --release <name>             Release name (default: latest)"
    echo ""
}

# Function to get the next available guest-cid and nic_id numbers
get_next_available_id() {
    local base_id=$1
    local check_type=$2
    local max_id=10
    for (( id=$base_id; id<=$max_id; id++ )); do
        if [[ "$check_type" == "guest-cid" ]]; then
            if ! lsof -i :$id &>/dev/null; then
                echo $id
                return
            fi
        elif [[ "$check_type" == "nic_id" ]]; then
            if ! ip link show nic_id$id &>/dev/null; then
                echo $id
                return
            fi
        fi
    done
    echo "No available ID found for $check_type"
    exit 1
}

# Default parameters
VM_CPU=${DEFAULT_CORES}
VM_RAM=${DEFAULT_MEM}
USED_GPUS=() # List of used GPUs (to be filled dynamically)
DISK_PATH=""
DISK_SIZE=0
CACHE=${DEFAULT_CACHE}
MAC_ADDRESS=${DEFAULT_MAC_PREFIX}:${DEFAULT_MAC_SUFFIX}
DEBUG_MODE=${DEFAULT_DEBUG}
RELEASE=""

SSH_PORT=${DEFAULT_SSH_PORT}
BASE_CID=$(get_next_available_id 3 guest-cid)
BASE_NIC=$(get_next_available_id 0 nic_id)

parse_args() {
    # Parse arguments
    while [[ "$#" -gt 0 ]]; do
        case $1 in
            --cores) VM_CPU=$2; shift ;;
            --mem) VM_RAM=$(echo $2 | sed 's/G//'); shift ;;
            --gpu) USED_GPUS+=("$2"); shift ;;
            --disk_path) DISK_PATH=$2; shift ;;
            --disk_size) DISK_SIZE=$2; shift ;;
            --cache) CACHE=$2; shift ;;
            --provider_config) DEBUG_MODE=$2; shift ;;
            --mount_config) DEBUG_MODE=$2; shift ;;
            --mac_address) MAC_ADDRESS=$2; shift ;;
            --ssh_port) SSH_PORT=$2; shift ;;
            --debug) DEBUG_MODE=$2; shift ;;
            --release) RELEASE=$2; shift ;;
            --help) usage; exit 0;;
            *) echo "Unknown parameter: $1"; usage ; exit 1 ;;
        esac
        shift
    done
}

download_release() {
    RELEASE_NAME=$1
    ASSET_NAME=$2
    TARGET_DIR=$3
    REPO=$4

    # Check if a target directory was provided, otherwise set to current directory
    if [[ -z "${TARGET_DIR}" ]]; then
        TARGET_DIR="."
    fi

    # Check if release name is provided or not
    if [[ -z "${RELEASE_NAME}" ]]; then
        echo "No release name provided. Fetching the latest release..."
        LATEST_TAG=$(curl -s https://api.github.com/repos/$REPO/releases/latest | jq -r '.tag_name')

        # Проверка, удалось ли получить тег
        if [[ -z "${LATEST_TAG}" ]]; then
            echo "Failed to fetch the latest release tag."
            exit 1
        fi
        RELEASE_NAME=${LATEST_TAG}
    fi

    echo "Fetching release: ${RELEASE_NAME}"
    RELEASE_URL="https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_NAME}"

    # Fetch the release information
    RESPONSE=$(curl -s $RELEASE_URL)

    # Check if the release was fetched correctly
    if [[ $(echo "$RESPONSE" | jq -r '.message') == "Not Found" ]]; then
        echo "Release not found!"
        exit 1
    fi

    # Extract the browser_download_url of the specified asset
    ASSET_URL=$(echo "${RESPONSE}" | jq -r ".assets[] | select(.name == \"${ASSET_NAME}\") | .browser_download_url")

    # Check if the asset exists
    if [[ -z "${ASSET_URL}" ]]; then
        echo "Asset \"${ASSET_NAME}\" not found!"
        exit 1
    fi

    # Create the target directory if it doesn't exist
    TARGET_DIR="${TARGET_DIR}/${RELEASE_NAME}"
    mkdir -p "${TARGET_DIR}"

    # Download the asset to the specified directory
    echo "Downloading ${ASSET_NAME} to ${TARGET_DIR}..."
    curl -L "$ASSET_URL" -o "$TARGET_DIR/$ASSET_NAME"

    if [[ -f "$TARGET_DIR/$ASSET_NAME" && -s "$TARGET_DIR/$ASSET_NAME" ]]; then
        echo "Download complete! File saved to $TARGET_DIR/$ASSET_NAME"
    else
        echo "Download failed or the file is empty!"
        exit 1
    fi

}

# Function to generate a unique MAC address
generate_mac_address() {
    local mac_prefix=$1
    local mac_suffix=$2
    for (( i=0; i<100; i++ )); do
        current_mac="$mac_prefix:$mac_suffix"
        if ! ip link show | grep -q "$current_mac"; then
            echo "$current_mac"
            return
        fi
        mac_suffix=$(printf '%x\n' $(( 0x$mac_suffix + 1 )))  # Increment the MAC suffix
    done
    echo "Unable to find an available MAC address."
    exit 1
}

# Collect system info
TOTAL_CPUS=$(nproc)
TOTAL_RAM=$(free -g | awk '/^Mem:/{print $2}')
TOTAL_DISK=$(df -h . | awk 'NR==2 {print $4}' | sed 's/G//')
USED_CPUS=0  # Add logic to calculate used CPUs by VM
USED_RAM=0   # Add logic to calculate used RAM by VM
AVAILABLE_GPUS=$(lspci -nnk -d 10de: | grep -E '3D controller' | awk '{print $1}')

TDX_SUPPORT=$(lscpu | grep -i tdx)
SEV_SUPPORT=$(lscpu | grep -i sev)


# Generate a unique MAC address
MAC_ADDRESS=$(generate_mac_address $DEFAULT_MAC_PREFIX $DEFAULT_MAC_SUFFIX)

main() {
    mkdir -p "${CACHE}"
    download_release "${RELEASE}" "MRENCLAVE.sign" "${CACHE}" "Super-Protocol/sp-kata-containers"
    exit 0


    # Collect system information to print
    echo "1. Used / total CPUs on host: $VM_CPU / $TOTAL_CPUS"
    echo "2. Used RAM for VM / total RAM on host: $VM_RAM GB / $TOTAL_RAM GB"
    echo "3. Used GPUs for VM / available GPUs on host: ${USED_GPUS[@]} / $AVAILABLE_GPUS"
    echo "4. VM disk size / total available space on host: $VM_DISK_SIZE GB / $TOTAL_DISK GB"
    echo "5. Available confidential mode by CPU: ${TDX_SUPPORT:+TDX enabled} ${SEV_SUPPORT:+SEV enabled}"
    echo "6. Debug mode: $DEBUG_MODE"
    if [[ $DEBUG_MODE == "on" ]]; then
        echo "   SSH Port: $SSH_PORT"
        echo "   MAC Address: $MAC_ADDRESS"
        echo "   Log File: $LOG_FILE"
    fi

    # Prepare QEMU command with GPU passthrough and chassis increment
    CHASSIS=1
    for GPU in "${USED_GPUS[@]}"; do
        QEMU_COMMAND+=" -object iommufd,id=iommufd$CHASSIS"
        QEMU_COMMAND+=" -device pcie-root-port,id=pci.$CHASSIS,bus=pcie.0,chassis=$CHASSIS"
        QEMU_COMMAND+=" -device vfio-pci,host=$GPU,bus=pci.$CHASSIS,iommufd=iommufd$CHASSIS"
        QEMU_COMMAND+=" -fw_cfg name=opt/ovmf/X-PciMmio64,straing=262144" # @TODO add only once?
        CHASSIS=$((CHASSIS + 1))
    done

    # Check for TDX and SEV support and append relevant options
    if [[ $TDX_SUPPORT ]]; then
        QEMU_COMMAND+=" -machine q35,kernel_irqchip=split,confidential-guest-support=tdx,memory-backend=ram1,hpet=off"
        QEMU_COMMAND+=" -object tdx-guest,sept-ve-disable=on,id=tdx"
        QEMU_COMMAND+=" -object memory-backend-memfd-private,id=ram1,size=${VM_MEMORY}"
        #QEMU_COMMAND+=" -name process=tdxvm,debug-threads=on" # @TODO needed?
    elif [[ $SEV_SUPPORT ]]; then
        QEMU_COMMAND+=" -machine q35,kernel_irqchip=split,confidential-guest-support=sev0" # @TODO check with docs, do we need memory-backend?
        QEMU_COMMAND+=" -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1"
    fi

    # If debug mode is enabled - Add network devices and enable logging
    if [[ $DEBUG_MODE == "on" ]]; then
        QEMU_COMMAND+=" -device virtio-net-pci,netdev=nic_id$BASE_NIC,mac=$MAC_ADDRESS"
        QEMU_COMMAND+=" -netdev user,id=nic_id$BASE_NIC,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22"
        QEMU_COMMAND+=" -chardev stdio,id=mux,mux=on,logfile=$LOG_FILE -monitor chardev:mux -serial chardev:mux"
    fi

    # Add provider config as directory to VM
    if [ -n "${PROVIDER_CONFIG_SRC}" ]; then
        QEMU_COMMAND+=" -fsdev local,security_model=passthrough,id=fsdev0,path=${PROVIDER_CONFIG_SRC}"
        QEMU_COMMAND+=" -device virtio-9p-pci,fsdev=fsdev0,mount_tag=sharedfolder"
    fi

    QEMU_COMMAND+=" -accel kvm \
    -nographic -nodefaults -vga none \
    -cpu host,-kvm-steal-time,pmu=off \
    -bios $BIOS_PATH \
    -m ${VM_RAM}G -smp $VM_CPU \
    -device vhost-vsock-pci,guest-cid=$BASE_CID \
    -drive file=$SCRIPT_DIR/rootfs.img,if=virtio,format=raw \
    -drive file=$SCRIPT_DIR/state.qcow2,if=virtio,format=qcow2 \
    -kernel $SCRIPT_DIR/vmlinuz \
    -append \"root=/dev/vda1 console=ttyS0 systemd.log_level=trace systemd.log_target=log rootfs_verity.scheme=dm-verity rootfs_verity.hash=$ROOT_HASH\"
    "

    # Create VM state disk
    qemu-img create -f qcow2 state.qcow2 ${VM_DISK_SIZE}G
    echo "Starting QEMU with the following command:"
    echo $QEMU_COMMAND
    echo "------------"

    NORMALIZED_COMMAND=$(echo "$QEMU_COMMAND" | tr -s ' ')
    # Replace " -" with a newline
    ARGS_SPLIT=$(echo "$NORMALIZED_COMMAND" | sed 's/ -/\n-/g')
    # Output each argument on a new line
    echo -e "$ARGS_SPLIT"

    #sleep 5
    #eval $QEMU_COMMAND
}

parse_args $@
main