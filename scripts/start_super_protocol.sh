#!/bin/bash
set -e

# Add error handler
trap 'exit_handler $? $LINENO' ERR

exit_handler() {
    local exit_code=$1
    local line_number=$2
    echo "Error on line $line_number: Command exited with status $exit_code"
    exit $exit_code
}

# Default values
SCRIPT_DIR=$( cd "$( dirname "$0" )" && pwd )

REQUIRED_TDX_PACKAGES=("sp-qemu-tdx")
REQUIRED_SNP_PACKAGES=("sp-qemu-snp")

STORJ_TOKEN="1UXqNMwov41q9TgHmyopNg5q2giQ8aTdh1gjKWKjfbWPFrcrnhenp6QZfd5ukyVnYXDx9Cok6RtnQMMnXmoZPrSUMNGZGF9KuLCzvRNmQYHowX14C2xAxtJeH6VCuNX39ist4bRE9L5VT3k41frDVh3cG1gZvsqh4EaDeaJyV6U4xVaqXqULnSb9PozqU97VVLWhfwdnj6XgUM59Wzq7yo7vn8RxwSyn8H74TEiLNGUPPA3frsYZuoqWQkNzbiYev5ByWeLro1TXo7DogD4WALCKfEmpwHs9j9rsX5WZvvZ13ourTiuZp5vTTZkByB2ibxUJqkSoZSpCNVtmDToNVKkMREVySe"
RELEASE_REPO="Super-Protocol/sp-vm"
RELEASE_ASSET="vm.json"

DEFAULT_CORES=$(( $(nproc) - 2 )) # All cores minus 2
DEFAULT_MEM=$(( $(free -g | awk '/^Mem:/{print $2}') - 8 ))
DEFAULT_CACHE="${HOME}/.cache/superprotocol" # Default cache path
DEFAULT_MOUNT_CONFIG="/sp"

DEFAULT_IP_ADDRESS="0.0.0.0"
DEFAULT_SSH_PORT=2222
DEFAULT_GUEST_CID=3

LOG_FILE=""
DEFAULT_MAC_PREFIX="52:54:00:12:34"
DEFAULT_MAC_SUFFIX="56"
QEMU_PATH=""
DEFAULT_DEBUG=false
DEFAULT_ARGO_BRANCH="main"
DEFAULT_ARGO_SP_ENV="main"
LOCAL_BUILD_DIR=""

# VM mode
VM_MODE=""
DEFAULT_VM_MODE="untrusted"  # Can be "untrusted", "tdx", or "sev-snp"
TDX_SUPPORT=$(lscpu | grep -i tdx || echo "")
SEV_SNP_SUPPORT=$(lscpu | grep -i sev_snp || echo "")

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
    echo "  --ip_address <address>       IP address (default: ${DEFAULT_IP_ADDRESS})"
    echo "  --ssh_port <port>            SSH port (default: ${DEFAULT_SSH_PORT})"
    echo "  --http_port <port>           HTTP port (default: no port forward)"
    echo "  --https_port <port>          HTTPS port (default: no port forward)"
    echo "  --log_file <file>            Log file (default: no)"
    echo "  --debug <true|false>         Enable debug mode (default: ${DEFAULT_DEBUG})"
    echo "  --argo_branch <name>         Name of argo branch for init SP components (default: ${DEFAULT_ARGO_BRANCH})"
    echo "  --argo_sp_env <name>         Name of argo environment for init SP components (default: ${DEFAULT_ARGO_SP_ENV})"
    echo "  --release <name>             Release name (default: latest)"
    echo "  --mode <mode>                VM mode: untrusted, tdx, sev-snp (default: ${DEFAULT_VM_MODE})"
    echo "  --guest-cid <id>             Guest CID for vsock (default: ${DEFAULT_GUEST_CID})"
    echo "  --build_dir <path>           Path to the local builded kata container (default: no)"
    echo ""
}

# Initialize parameters
VM_CPU=${DEFAULT_CORES}
VM_RAM=${DEFAULT_MEM}
USED_GPUS=() # List of used GPUs (to be filled dynamically)
GUEST_CID=${DEFAULT_GUEST_CID}
CACHE=${DEFAULT_CACHE}
STATE_DISK_PATH=""
STATE_DISK_SIZE=0
MAC_ADDRESS=${DEFAULT_MAC_PREFIX}:${DEFAULT_MAC_SUFFIX}
PROVIDER_CONFIG=""
MOUNT_CONFIG=${DEFAULT_MOUNT_CONFIG}
DEBUG_MODE=${DEFAULT_DEBUG}
ARGO_BRANCH=${DEFAULT_ARGO_BRANCH}
ARGO_SP_ENV=${DEFAULT_ARGO_SP_ENV}
RELEASE=""
RELEASE_FILEPATH=""

IP_ADDRESS=${DEFAULT_IP_ADDRESS}
SSH_PORT=${DEFAULT_SSH_PORT}
HTTP_PORT=""
HTTPS_PORT=""
BASE_CID=$(get_next_available_id 2 guest-cid)
BASE_NIC=$(get_next_available_id 0 nic_id)

BIOS_PATH=""
ROOTFS_PATH=""
ROOTFS_HASH_PATH=""
KERNEL_PATH=""

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
            --ip_address) IP_ADDRESS=$2; shift ;;
            --ssh_port) SSH_PORT=$2; shift ;;
            --http_port) HTTP_PORT=$2; shift ;;
            --https_port) HTTPS_PORT=$2; shift ;;
            --log_file) LOG_FILE=$2; shift ;;
            --debug) DEBUG_MODE=$2; shift ;;
            --argo_branch) ARGO_BRANCH=$2; shift ;;
            --argo_sp_env) ARGO_SP_ENV=$2; shift ;;
            --release) RELEASE=$2; shift ;;
            --mode) VM_MODE=$2; shift ;;
            --guest-cid) GUEST_CID=$2; shift ;;
            --build_dir) LOCAL_BUILD_DIR=$2; shift ;;
            --help) usage; exit 0;;
            *) echo "Unknown parameter: $1"; usage ; exit 1 ;;
        esac
        shift
    done
}

detect_cpu_type() {
    if [[ -n "$VM_MODE" ]]; then
        echo "CPU type was overwritten by '--mode' cmd arg. value: '$VM_MODE'";
        return;
    fi

    if [[ -n "$TDX_SUPPORT" ]]; then
        VM_MODE="tdx";
    elif [[ -n "$SEV_SNP_SUPPORT" ]]; then
        VM_MODE="sev-snp";
    fi

    if [[ -z "$VM_MODE" ]]; then
        echo "failed to detect CPU type, using default: '$DEFAULT_VM_MODE'";
        VM_MODE="$DEFAULT_VM_MODE";
    else
        echo "detected CPU type: $VM_MODE";
    fi
}

find_qemu_path() {
    local qemu_locations=(
        "/usr/local/bin/qemu-system-x86_64"
        "/usr/bin/qemu-system-x86_64"
        "/bin/qemu-system-x86_64"
        "/usr/local/sbin/qemu-system-x86_64"
        "/usr/sbin/qemu-system-x86_64"
    )
    
    for location in "${qemu_locations[@]}"; do
        if [[ -x "$location" ]]; then
            QEMU_PATH="$location"
            echo "Found QEMU at: $QEMU_PATH"
            return 0
        fi
    done
    
    echo "Error: Could not find qemu-system-x86_64 executable"
    exit 1
}

download_release() {
    RELEASE_NAME=$1
    ASSET_NAME=$2
    TARGET_DIR=$3
    REPO=$4

    if [[ -z "${LOCAL_BUILD_DIR}" ]]; then
        # Check if release name is provided or not
        if [[ -z "${RELEASE_NAME}" ]]; then
            echo "No release name provided. Fetching the latest release..."
            LATEST_TAG=$(curl -s https://api.github.com/repos/$REPO/releases/latest | jq -r '.tag_name')
            if [[ -z "${LATEST_TAG}" ]]; then
                echo "Failed to fetch the latest release tag."
                exit 1
            fi
            RELEASE_NAME=${LATEST_TAG}
            RELEASE=${LATEST_TAG}
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
        curl -L "${ASSET_URL}" -o "${TARGET_DIR}/${ASSET_NAME}"

        if [[ -f "${TARGET_DIR}/${ASSET_NAME}" && -s "${TARGET_DIR}/${ASSET_NAME}" ]]; then
            echo "Download complete! File saved to ${TARGET_DIR}/${ASSET_NAME}"
        else
            echo "Download failed or the file is empty!"
            exit 1
        fi
    else
        TARGET_DIR="${LOCAL_BUILD_DIR}";
    fi
    RELEASE_FILEPATH="${TARGET_DIR}/${ASSET_NAME}"
}

parse_and_download_release_files() {
    RELEASE_JSON=$1
    DOWNLOAD_DIR=$(dirname ${RELEASE_JSON})

    echo "Parsing release JSON at: ${RELEASE_JSON}"

    # First, validate that we can read all required entries from JSON
    required_keys=()
    if [[ "${VM_MODE}" == "sev-snp" ]]; then
        required_keys=("rootfs" "bios_amd" "root_hash" "kernel")
    else
        required_keys=("rootfs" "bios" "root_hash" "kernel")
    fi

    for key in "${required_keys[@]}"; do
        if ! jq -e ".${key}" "${RELEASE_JSON}" > /dev/null; then
            echo "Error: Required key '${key}' not found in release JSON"
            exit 1
        fi
    done

    while read -r entry; do
        key=$(echo "$entry" | jq -r '.key')
        bucket=$(echo "$entry" | jq -r '.value.bucket')
        prefix=$(echo "$entry" | jq -r '.value.prefix')
        filename=$(echo "$entry" | jq -r '.value.filename')
        sha256=$(echo "$entry" | jq -r '.value.sha256')

        echo "Processing entry - key: ${key}, filename: ${filename}"

        local_path="$DOWNLOAD_DIR/$filename"

        case $key in
            rootfs) ROOTFS_PATH=$local_path; echo "Set ROOTFS_PATH to ${local_path}" ;;
            bios)
                if [[ "${VM_MODE}" != "sev-snp" ]]; then
                    BIOS_PATH=$local_path
                    echo "Set BIOS_PATH to ${local_path}"
                fi
                ;;
            bios_amd)
                if [[ "${VM_MODE}" == "sev-snp" ]]; then
                    BIOS_PATH=$local_path
                    echo "Set BIOS_PATH to ${local_path}"
                fi
                ;;
            root_hash) ROOTFS_HASH_PATH=$local_path; echo "Set ROOTFS_HASH_PATH to ${local_path}" ;;
            kernel) KERNEL_PATH=$local_path; echo "Set KERNEL_PATH to ${local_path}" ;;
            *) echo "Warning: Unknown key ${key} in release JSON" ;;
        esac

        # Check existing file
        if [[ -f "$local_path" ]]; then
            computed_sha256=$(sha256sum "$local_path" | awk '{print $1}')
            if [[ "$computed_sha256" == "$sha256" ]]; then
                echo "File $filename already exists and checksum is valid. Skipping download."
                continue
            else
                if [[ -z "${LOCAL_BUILD_DIR}" ]]; then
                    echo "Warning: Checksum mismatch for existing file $filename. Downloading again."
                    rm -f "$local_path"
                else
                    echo "Error: Checksum mismatch for existing file $filename builded locally."
                    exit 1;
                fi
            fi
        fi

        echo "Downloading $filename from sj://$bucket/$prefix/$filename to $local_path..."
        uplink cp --parallelism 16 --progress --access ${STORJ_TOKEN} "sj://$bucket/$prefix/$filename" "$local_path"

        if [ $? -ne 0 ]; then
            echo "Error: Failed to download $filename"
            exit 1
        fi

        computed_sha256=$(sha256sum "$local_path" | awk '{print $1}')
        if [[ "$computed_sha256" != "$sha256" ]]; then
            echo "Error: Checksum mismatch for $filename after download. Expected $sha256, got $computed_sha256."
            exit 1
        else
            echo "Successfully downloaded and verified $filename."
        fi
    done < <(jq -c 'to_entries[]' "$RELEASE_JSON")

    # Verify that all required paths are set
    if [[ -z "${ROOTFS_PATH}" ]] || [[ -z "${BIOS_PATH}" ]] || [[ -z "${ROOTFS_HASH_PATH}" ]] || [[ -z "${KERNEL_PATH}" ]]; then
        echo "Error: Not all required files were processed successfully"
        echo "ROOTFS_PATH: ${ROOTFS_PATH}"
        echo "BIOS_PATH: ${BIOS_PATH}"
        echo "ROOTFS_HASH_PATH: ${ROOTFS_HASH_PATH}"
        echo "KERNEL_PATH: ${KERNEL_PATH}"
        exit 1
    fi
}

check_packages() {
    if [[ "$EUID" -ne 0 ]]; then
        echo "This script must be run as root. Please use sudo."
        exit 1
    fi

    # Check for uplink binary
    if ! command -v uplink &> /dev/null; then
        echo "Uplink is not installed. Installing..."
        curl -L https://github.com/storj/storj/releases/latest/download/uplink_linux_amd64.zip -o uplink_linux_amd64.zip
        apt update && apt install -y unzip
        unzip -o uplink_linux_amd64.zip
        sudo install uplink /usr/local/bin/uplink
        # cleanup
        rm uplink_linux_amd64.zip
        rm uplink
    fi

    # Check nc
    if ! command -v nc &> /dev/null; then
        echo "nc is not installed. Installing..."
        apt update && apt install -y netcat-openbsd;
    fi

    # Check TDX packages if needed
    local missing=()
    if [[ "${VM_MODE}" == "tdx" ]]; then
        for package in "${REQUIRED_TDX_PACKAGES[@]}"; do
            if ! dpkg -l | grep -q "^ii.*$package"; then
                missing+=("$package")
            fi
        done
    fi

    if [[ "${VM_MODE}" == "sev-snp" ]]; then
        for package in "${REQUIRED_SNP_PACKAGES[@]}"; do
            if ! dpkg -l | grep -q "^ii.*$package"; then
                missing+=("$package")
            fi
        done
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo "The following packages are missing: ${missing[*]}"
        echo "Please install these packages before running the script."
        exit 1
    fi
}

prepare_gpus_for_vfio() {
    local gpu_ids=("$@")

    # Detect NVSwitch devices
    local nvswitch_ids=($(lspci -mm -n -d 10de:22a3 | cut -d' ' -f1))
    echo "Debug: Found NVSwitch devices: ${nvswitch_ids[@]}"

    echo "Unloading NVIDIA modules..."
    modprobe -r nvidia_uvm nvidia_drm nvidia_modeset nvidia || true

    echo "Loading VFIO modules..."
    modprobe vfio
    modprobe vfio-pci

    # Function to bind device to vfio-pci
    bind_to_vfio() {
        local device=$1
        local device_type=$2

        echo "Preparing $device_type $device for VFIO passthrough"

        # Check current driver
        local current_driver=$(lspci -k -s "$device" | grep "Kernel driver in use:" | awk '{print $5}')
        echo "Current driver for $device_type $device: $current_driver"

        if [[ "$current_driver" != "vfio-pci" ]]; then
            # If nvidia modules are still loaded, try to remove them
            if [[ "$current_driver" == "nvidia" ]]; then
                echo "Forcing removal of NVIDIA modules..."
                rmmod -f nvidia_uvm nvidia_drm nvidia_modeset nvidia 2>/dev/null || true
            fi

            # Unbind from current driver if bound
            if [[ -e "/sys/bus/pci/devices/0000:$device/driver" ]]; then
                echo "Unbinding from current driver"
                echo "0000:$device" > /sys/bus/pci/devices/0000:$device/driver/unbind
            fi

            # Add to vfio-pci
            echo "Adding device to vfio-pci"
            echo "vfio-pci" > /sys/bus/pci/devices/0000:$device/driver_override
            echo "0000:$device" > /sys/bus/pci/drivers/vfio-pci/bind
        else
            echo "$device_type $device is already bound to vfio-pci"
        fi

        # Verify binding
        current_driver=$(lspci -k -s "$device" | grep "Kernel driver in use:" | awk '{print $5}')
        if [[ "$current_driver" != "vfio-pci" ]]; then
            echo "Error: Failed to bind $device_type $device to vfio-pci (current driver: $current_driver)"
            exit 1
        fi
    }

    # Process GPUs
    for gpu in "${gpu_ids[@]}"; do
        bind_to_vfio "$gpu" "GPU"
    done

    # Process NVSwitch devices
    for nvswitch in "${nvswitch_ids[@]}"; do
        bind_to_vfio "$nvswitch" "NVSwitch"
    done

    # Final verification
    echo "Verifying device bindings..."
    for gpu in "${gpu_ids[@]}"; do
        current_driver=$(lspci -k -s "$gpu" | grep "Kernel driver in use:" | awk '{print $5}')
        echo "GPU $gpu is using driver: $current_driver"
    done
    for nvswitch in "${nvswitch_ids[@]}"; do
        current_driver=$(lspci -k -s "$nvswitch" | grep "Kernel driver in use:" | awk '{print $5}')
        echo "NVSwitch $nvswitch is using driver: $current_driver"
    done
}

validate_yaml_files() {
    local dir="$1"
    local has_errors=false

    # Check if python3 and PyYAML are installed
    if ! command -v python3 &> /dev/null; then
        echo "Error: python3 is required for YAML validation"
        exit 1
    fi

    if ! python3 -c "import yaml" &> /dev/null; then
        echo "Installing PyYAML..."
        apt-get update && apt-get install -y python3-yaml
    fi

    # Create a temporary Python script for YAML validation
    local tmp_script=$(mktemp)
    cat > "$tmp_script" << 'EOF'
import sys
import yaml

def validate_yaml(file_path):
    try:
        with open(file_path, 'r') as f:
            yaml.safe_load(f)
        return True
    except yaml.YAMLError as e:
        print(f"Error in {file_path}:")
        print(e)
        return False

if __name__ == "__main__":
    if not validate_yaml(sys.argv[1]):
        sys.exit(1)
EOF

    # Find all yaml files recursively and validate each one
    local yaml_files=$(find "$dir" -type f \( -name "*.yaml" -o -name "*.yml" \))
    if [ -z "$yaml_files" ]; then
        echo "No YAML files found in $dir"
        rm "$tmp_script"
        return 0
    fi

    echo "Validating YAML files in $dir..."

    while IFS= read -r file; do
        echo "Checking $file..."
        if ! python3 "$tmp_script" "$file"; then
            has_errors=true
            echo "❌ Invalid YAML: $file"
        else
            echo "✓ Valid YAML: $file"
        fi
    done <<< "$yaml_files"

    # Cleanup
    rm "$tmp_script"

    if [ "$has_errors" = true ]; then
        echo "YAML validation failed. Please fix the errors above."
        return 1
    fi

    echo "All YAML files are valid."
    return 0
}

check_params() {
    # Collect system info
    TOTAL_CPUS=$(nproc)
    TOTAL_RAM=$(free -g | awk '/^Mem:/{print $2}')

    # Get list of all NVIDIA GPUs and NVSwitch devices
    AVAILABLE_GPUS=($(lspci -nnk -d 10de: | grep -E '3D controller' | awk '{print $1}'))
    AVAILABLE_NVSWITCHES=($(lspci -mm -n -d 10de:22a3 | cut -d' ' -f1))

    echo "Debug: Found GPUs: ${AVAILABLE_GPUS[@]}"

    # Process GPU list
    if [[ " ${USED_GPUS[@]} " =~ " none " ]]; then
        USED_GPUS=()
    elif [[ ${#USED_GPUS[@]} -eq 0 ]]; then
        USED_GPUS=("${AVAILABLE_GPUS[@]}")
    fi

    # Remove duplicates efficiently
    declare -A UNIQUE_GPUS
    for GPU in "${USED_GPUS[@]}"; do
        UNIQUE_GPUS["$GPU"]=1
    done
    USED_GPUS=("${!UNIQUE_GPUS[@]}")

    # Verify GPUs
    for GPU in "${USED_GPUS[@]}"; do
        if [[ " ${AVAILABLE_GPUS[*]} " =~ " ${GPU} " ]]; then
            echo "GPU $GPU is available."
        else
            echo "GPU $GPU is NOT available."
            exit 1
        fi
    done

    echo "• Used GPUs for VM / available GPUs on host: ${USED_GPUS[@]:-None} / ${AVAILABLE_GPUS[*]}"

    if [[ -z "$STATE_DISK_PATH" ]]; then
        STATE_DISK_PATH="$CACHE/state.qcow2"
    fi

    echo "Removing old state disk..."
    rm -f ${STATE_DISK_PATH}
    echo "Creating new state disk directory..."
    mkdir -p $(dirname ${STATE_DISK_PATH})
    echo "Initializing state disk..."
    touch ${STATE_DISK_PATH}


    if [[ -n "$HTTP_PORT" ]]; then
        echo "Checking http port aviability..."
        if nc -z "$IP_ADDRESS" "$HTTP_PORT"; then
            echo "http port $HTTP_PORT already bound!"
            exit 1
        fi
    fi
    if [[ -n "$HTTPS_PORT" ]]; then
        echo "Checking https port aviability..."
        if nc -z "$IP_ADDRESS" "$HTTPS_PORT"; then
            echo "https port $HTTPS_PORT already bound!"
            exit 1
        fi
    fi


    echo "Checking mount point..."
    MOUNT_POINT=$(df --output=target "${STATE_DISK_PATH}" | tail -n 1)

    # Check if MOUNT_POINT is empty
    if [ -z "${MOUNT_POINT}" ]; then
        echo "Could not determine the mount point for ${STATE_DISK_PATH}"
        exit 1
    fi

    # Get the size of the mount point
    MOUNT_SIZE_AVAIL=$(( $(df --block-size=1G --output=avail "${MOUNT_POINT}" | tail -n 1) ))
    MOUNT_SIZE_TOTAL=$(( $(df --block-size=1G --output=size "${MOUNT_POINT}" | tail -n 1) ))

    if [ "${STATE_DISK_SIZE}" -eq 0 ]; then
        STATE_DISK_SIZE=$((MOUNT_SIZE_TOTAL - 64))
        echo "Autodetected disk size ${STATE_DISK_SIZE}G"
        if [ "$STATE_DISK_SIZE" -lt 512 ]; then
            echo "The autodetected disk size must be greater than 512Gb"
            exit 1
        fi
    fi

    if [[ "${STATE_DISK_SIZE}" -gt "${MOUNT_SIZE_AVAIL}" ]]; then
        echo "No free space to create virtual disk with ${STATE_DISK_SIZE}Gb"
        exit 1
    fi

    echo "• VM disk size / total available space on host: ${STATE_DISK_SIZE} Gb / ${MOUNT_SIZE_TOTAL} Gb"

    echo "• Cache directory: ${CACHE}"

    if [[ -z "${PROVIDER_CONFIG}" ]]; then
        echo "Error: <provider_config> option must be passed"
        exit 1
    fi

    if [[ -d "${PROVIDER_CONFIG}" ]]; then
        echo "• Provider config: ${PROVIDER_CONFIG}"

        # Validate all yamls
        validate_yaml_files "${PROVIDER_CONFIG}"

        # Check if authorized_keys doesn't exist in provider_config
        if [[ ! -f "${PROVIDER_CONFIG}/authorized_keys" ]]; then
            if [[ -f "${HOME}/.ssh/authorized_keys" ]]; then
                cp "${HOME}/.ssh/authorized_keys" "${PROVIDER_CONFIG}/authorized_keys"
                echo "Copied keys file from ~/.ssh/authorized_keys"
            fi
        fi

        # Setting proper rights and ownership to authorized_keys
        chown root:root "${PROVIDER_CONFIG}"
        chown root:root "${PROVIDER_CONFIG}/authorized_keys"
        chmod 400 "${PROVIDER_CONFIG}/authorized_keys"
    else
        echo "Folder ${PROVIDER_CONFIG} does not exist."
        exit 1
    fi

    if [[ "${MAC_ADDRESS}" =~ ^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$ ]]; then
        echo "• Mac address: ${MAC_ADDRESS}"
    else
        echo "Error: MAC-address $MAC_ADDRESS has invalid format"
        exit 1
    fi

    if [[ "${DEBUG_MODE}" == "true" || "${DEBUG_MODE}" == "false" ]]; then
        echo "• Debug mode: ${DEBUG_MODE}"
    else
        echo "Error: <debug> option must be true or false"
        exit 1
    fi

    if [[ ${DEBUG_MODE} == "true" ]]; then
        echo "   Argo branch: $ARGO_BRANCH"
        echo "   Argo SP env: $ARGO_SP_ENV"
        echo "   SSH Port: $SSH_PORT"
        if [[ -n "$HTTP_PORT" ]]; then
            echo "   HTTP Port: $HTTP_PORT"
        fi
        if [[ -n "$HTTPS_PORT" ]]; then
            echo "   HTTPS Port: $HTTPS_PORT"
        fi

        if [[ -z "${LOG_FILE}" ]]; then
            echo "Error: <log_file> option can't be empty in debug mode"
            exit 1
        fi
        echo "   Log file: ${LOG_FILE}"
    else
        if [[ -n "${LOG_FILE}" ]]; then
            echo "Error: <log_file> option available only in debug mode"
            exit 1
        fi
    fi
    echo "• Superprotocol release: ${RELEASE:-latest}"
    echo "• VM Mode: ${VM_MODE}"
}

main() {
    check_params
    check_packages

    # Prepare GPUs for VFIO passthrough
    prepare_gpus_for_vfio "${USED_GPUS[@]}"

    # Find QEMU path before using it
    find_qemu_path

    mkdir -p "${CACHE}"
    download_release "${RELEASE}" "${RELEASE_ASSET}" "${CACHE}" "${RELEASE_REPO}"
    parse_and_download_release_files ${RELEASE_FILEPATH}

    # Prepare QEMU command with GPU passthrough
    GPU_PASSTHROUGH=""
    CHASSIS=1

    # Add single fw_cfg setting before GPU loop
    GPU_PASSTHROUGH+=" -fw_cfg name=opt/ovmf/X-PciMmio64,string=262144"

    # Add GPUs
    for GPU in "${USED_GPUS[@]}"; do
        echo "Debug: Adding GPU to QEMU: $GPU with chassis $CHASSIS"
        if [[ "${VM_MODE}" == "tdx" ]]; then
            GPU_PASSTHROUGH+=" -object iommufd,id=iommufd$CHASSIS"
            GPU_PASSTHROUGH+=" -device pcie-root-port,id=pci.$CHASSIS,bus=pcie.0,chassis=$CHASSIS"
            GPU_PASSTHROUGH+=" -device vfio-pci,host=$GPU,bus=pci.$CHASSIS,iommufd=iommufd$CHASSIS"
        else
            GPU_PASSTHROUGH+=" -device pcie-root-port,id=pci.$CHASSIS,bus=pcie.0,chassis=$CHASSIS"
            GPU_PASSTHROUGH+=" -device vfio-pci,host=$GPU,bus=pci.$CHASSIS"
        fi
        CHASSIS=$((CHASSIS + 1))
    done

    # Add NVSwitch devices
    if [[ "${VM_MODE}" == "tdx" ]]; then
        GPU_PASSTHROUGH+=" -object iommufd,id=iommufd$CHASSIS"
        IOOMUFD_CHASSIS=$CHASSIS
    fi

    for NVSWITCH in "${AVAILABLE_NVSWITCHES[@]}"; do
        echo "Debug: Adding NVSwitch to QEMU: $NVSWITCH with chassis $CHASSIS"
        GPU_PASSTHROUGH+=" -device pcie-root-port,id=pci.$CHASSIS,bus=pcie.0,chassis=$CHASSIS"
        if [[ "${VM_MODE}" == "tdx" ]]; then
            GPU_PASSTHROUGH+=" -device vfio-pci,host=$NVSWITCH,bus=pci.$CHASSIS,iommufd=iommufd$IOOMUFD_CHASSIS"
        else
            GPU_PASSTHROUGH+=" -device vfio-pci,host=$NVSWITCH,bus=pci.$CHASSIS"
        fi
        CHASSIS=$((CHASSIS + 1))
    done

    # Initialize machine parameters based on mode
    MACHINE_PARAMS=""
    CPU_PARAMS="-cpu host"
    CC_PARAMS=""
    CC_SPECIFIC_PARAMS=""

    case ${VM_MODE} in
        "tdx")
            if [[ ! $TDX_SUPPORT ]]; then
                echo "Error: TDX is not supported on this system"
                exit 1
            fi
            CC_PARAMS+=" -object memory-backend-ram,id=mem0,size=${VM_RAM}G "
            MACHINE_PARAMS="q35,kernel_irqchip=split,confidential-guest-support=tdx,memory-backend=mem0"
            CC_SPECIFIC_PARAMS=" -object '{\"qom-type\":\"tdx-guest\",\"id\":\"tdx\",\"quote-generation-socket\":{\"type\":\"vsock\",\"cid\":\"${BASE_CID}\",\"port\":\"4050\"}}'"
            ;;
        "sev-snp")
            if [[ ! $SEV_SNP_SUPPORT ]]; then
                echo "Error: SEV is not supported on this system"
                exit 1
            fi
            MACHINE_PARAMS="q35,memory-encryption=sev0,vmport=off,memory-backend=ram1"
            CC_PARAMS+=" -cpu EPYC-Milan \
             -object memory-backend-memfd,id=ram1,size=${VM_RAM}G,share=true,prealloc=false \
             -object sev-snp-guest,id=sev0,policy=0x30000,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on "
            ;;
        "untrusted")
            MACHINE_PARAMS="q35,kernel_irqchip=split"
            CPU_PARAMS="-cpu host,-kvm-steal-time,pmu=off"
            ;;
        *)
            echo "Error: Invalid mode '${VM_MODE}'. Must be 'untrusted', 'tdx', or 'sev-snp'."
            exit 1
            ;;
    esac

    NETWORK_SETTINGS=" -device virtio-net-pci,netdev=nic_id$BASE_NIC,mac=$MAC_ADDRESS"
    NETWORK_SETTINGS+=" -netdev user,id=nic_id$BASE_NIC"
    if [[ -n "$HTTP_PORT" ]]; then
        NETWORK_SETTINGS+=",hostfwd=tcp:$IP_ADDRESS:$HTTP_PORT-:80"
    fi
    if [[ -n "$HTTPS_PORT" ]]; then
        NETWORK_SETTINGS+=",hostfwd=tcp:$IP_ADDRESS:$HTTPS_PORT-:443"
    fi
    DEBUG_PARAMS=""
    KERNEL_CMD_LINE=""
    ROOT_HASH=$(grep 'Root hash' "${ROOTFS_HASH_PATH}" | awk '{print $3}')

    CLEARCPUID_PARAM=" " # Space is important
    BUILD_PARAM=""
    VSOCK_CID=""

    if [[ "${VM_MODE}" == "tdx" ]]; then
        CLEARCPUID_PARAM=" clearcpuid=mtrr " # Space before and after is important
        VSOCK_CID="-device vhost-vsock-pci,guest-cid=${GUEST_CID}"
    fi

    if [[ "${VM_MODE}" == "sev-snp" ]]; then
        BUILD_PARAM=" build=$RELEASE"
    fi

    if [[ ${DEBUG_MODE} == true ]]; then
        NETWORK_SETTINGS+=",hostfwd=tcp:127.0.0.1:$SSH_PORT-:22"
        KERNEL_CMD_LINE="root=/dev/vda1 console=ttyS0${CLEARCPUID_PARAM}\
                        systemd.log_level=trace systemd.log_target=log \
                        rootfs_verity.scheme=dm-verity rootfs_verity.hash=${ROOT_HASH} \
                        argo_branch=${ARGO_BRANCH} argo_sp_env=${ARGO_SP_ENV} \
                        sp-debug=true${BUILD_PARAM}"
    else
        KERNEL_CMD_LINE="root=/dev/vda1${CLEARCPUID_PARAM}rootfs_verity.scheme=dm-verity rootfs_verity.hash=${ROOT_HASH}${BUILD_PARAM}"
    fi

    QEMU_COMMAND="${QEMU_PATH} \
        -enable-kvm \
        -append \"${KERNEL_CMD_LINE}\" \
        -drive file=${ROOTFS_PATH},if=virtio,format=raw \
        -drive file=${STATE_DISK_PATH},if=virtio,format=qcow2 \
        -kernel ${KERNEL_PATH} \
        -smp cores=${VM_CPU} \
        -m ${VM_RAM}G \
        ${CPU_PARAMS} \
        -machine ${MACHINE_PARAMS} \
        ${CC_SPECIFIC_PARAMS} \
        ${NETWORK_SETTINGS} \
        -nographic \
        ${CC_PARAMS} \
        -bios ${BIOS_PATH} \
        -vga none \
        -nodefaults \
        -serial stdio \
        -device vhost-vsock-pci,guest-cid=${GUEST_CID} \
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
detect_cpu_type
main
