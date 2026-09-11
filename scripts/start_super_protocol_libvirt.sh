#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
BASE_SCRIPT="${SCRIPT_DIR}/start_super_protocol.sh"
LIBVIRT_LAUNCHER="${SCRIPT_DIR}/libvirt_launcher.py"

# Reuse the sourceable release, validation, VFIO, and provider-config helpers.
# shellcheck disable=SC1090
source "${BASE_SCRIPT}"

# qemu:///system normally runs QEMU as libvirt-qemu, which cannot traverse
# /root. Keep the same --cache option, but use libvirt's image directory as the
# safe default for this launcher.
DEFAULT_CACHE="/var/lib/libvirt/images/superprotocol"
CACHE=${DEFAULT_CACHE}

LIBVIRT_DOMAIN_NAME=""
LIBVIRT_DOMAIN_UUID=""
REUSE_DISKS=false
LIBVIRT_QEMU_USER=""
BASE_ARGS=()

usage_libvirt() {
    usage
    echo "Libvirt-specific options:"
    echo "  --name <domain>                Transient domain name (default: super-protocol-<guest-cid>)"
    echo "  --uuid <uuid>                  Domain UUID (for Nova-managed instances)"
    echo "  --reuse-disks                  Reuse existing state/provider disks on power-on"
    echo ""
    echo "Runtime behavior:"
    echo "  --debug false                  Start in the background and return"
    echo "  --debug true                   Attach serial console and tee it to --log_file"
}

extract_libvirt_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name)
                if [[ $# -lt 2 ]]; then
                    echo "Error: --name requires a value" >&2
                    exit 1
                fi
                LIBVIRT_DOMAIN_NAME=$2
                shift 2
                ;;
            --uuid)
                [[ $# -ge 2 ]] || { echo "Error: --uuid requires a value" >&2; exit 1; }
                LIBVIRT_DOMAIN_UUID=$2
                shift 2
                ;;
            --reuse-disks)
                REUSE_DISKS=true
                shift
                ;;
            --help)
                usage_libvirt
                exit 0
                ;;
            *)
                BASE_ARGS+=("$1")
                shift
                ;;
        esac
    done
}

check_target_os() {
    local os_id os_version
    os_id=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')
    os_version=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')
    if [[ "${os_id}" != "ubuntu" ]]; then
        echo "Error: this launcher supports Ubuntu 24.04 and 26.04 (found ${os_id:-unknown})." >&2
        exit 1
    fi
    if [[ "${os_version}" != "24.04" && "${os_version}" != "26.04" ]]; then
        echo "Error: this launcher supports Ubuntu 24.04 and 26.04 (found ${os_version:-unknown})." >&2
        exit 1
    fi
}

bootstrap_hint() {
    if [[ "${VM_MODE}" == "tdx" ]]; then
        echo "Fix: run sudo ${SCRIPT_DIR}/bootstrap_tdx.sh to restore the libvirt host configuration." >&2
    elif [[ "${VM_MODE}" == "sev-snp" ]]; then
        echo "Fix: run sudo ${SCRIPT_DIR}/bootstrap_snp.sh to restore the libvirt host configuration." >&2
    else
        echo "Fix: re-run the host bootstrap with sudo to restore the libvirt host configuration." >&2
    fi
}

check_libvirt_dependencies() {
    local missing=()
    command -v python3 >/dev/null 2>&1 || missing+=(python3)
    command -v virsh >/dev/null 2>&1 || missing+=(libvirt-clients)
    command -v setfacl >/dev/null 2>&1 || missing+=(acl)
    command -v runuser >/dev/null 2>&1 || missing+=(util-linux)
    if ! python3 -c 'import libvirt' >/dev/null 2>&1; then
        missing+=(python3-libvirt)
    fi
    if [[ "${NETDEV_MODE}" == "user" || -n "${SSH_PORT}" ]]; then
        command -v passt >/dev/null 2>&1 || missing+=(passt)
    fi
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "Error: missing libvirt runtime dependencies: ${missing[*]}" >&2
        echo "Install them with: apt-get install libvirt-daemon-system libvirt-clients python3-libvirt passt acl" >&2
        echo "GPU passthrough additionally requires libvirt >= 12.1.0." >&2
        exit 1
    fi
    if [[ ! -x "${LIBVIRT_LAUNCHER}" ]]; then
        echo "Error: launcher is not executable: ${LIBVIRT_LAUNCHER}" >&2
        exit 1
    fi
}

check_passt_apparmor_profile() {
    if [[ "${NETDEV_MODE}" != "user" && -z "${SSH_PORT}" ]]; then
        return
    fi

    local profile=/etc/apparmor.d/abstractions/libvirt-qemu
    [[ -r "${profile}" ]] || return 0

    if awk '
        /^[[:space:]]*profile passt([[:space:]]+flags=\([^)]*\))?[[:space:]]*\{/ { in_passt = 1 }
        in_passt && /\/usr\/bin\/passt[[:space:]]+r,/ { incompatible = 1 }
        in_passt && /^[[:space:]]*}/ { exit }
        END { exit incompatible ? 0 : 1 }
    ' "${profile}"; then
        echo "Error: the libvirt AppArmor profile permits reading /usr/bin/passt but not mmap." >&2
        echo "Ubuntu AppArmor 5 will kill passt with fatal signal 11." >&2
        bootstrap_hint
        exit 1
    fi

    if ! awk '
        /^[[:space:]]*profile passt([[:space:]]+flags=\([^)]*\))?[[:space:]]*\{/ { in_passt = 1 }
        in_passt && /^[[:space:]]*capability[[:space:]]+net_bind_service,/ { found = 1 }
        in_passt && /^[[:space:]]*}/ { exit }
        END { exit found ? 0 : 1 }
    ' "${profile}"; then
        echo "Error: the nested passt AppArmor profile does not allow CAP_NET_BIND_SERVICE." >&2
        bootstrap_hint
        exit 1
    fi
}

check_tdx_vsock_apparmor_profile() {
    [[ "${VM_MODE}" == "tdx" ]] || return 0
    local dropin=/etc/apparmor.d/abstractions/libvirt-qemu.d/99-sp-vm-tools-local
    if [[ ! -r "${dropin}" ]] || ! grep -qF 'network vsock stream,' "${dropin}"; then
        echo "Error: TDX QGS requires the AppArmor rule 'network vsock stream,'." >&2
        echo "Expected it in ${dropin}." >&2
        bootstrap_hint
        exit 1
    fi
}

check_passt_unprivileged_ports() {
    local ports=()
    local port port_number minimum=65536

    if [[ "${NETDEV_MODE}" == "user" ]]; then
        ports+=(
            "${HTTP_PORT}" "${HTTPS_PORT}" "${PKI_PORT}"
            "${PKI_VM_MEASURE_PORT}" "${WG_PORT}"
            "${SWARM_DB_GOSSIP_PORT}" "${DNS_PORT}"
        )
    fi
    if [[ -n "${SSH_PORT}" ]]; then
        ports+=("${SSH_PORT}")
    fi

    for port in "${ports[@]}"; do
        [[ -n "${port}" ]] || continue
        port_number=$((10#${port}))
        if ((port_number < minimum)); then
            minimum=${port_number}
        fi
    done

    if ((minimum >= 1024)); then
        return
    fi

    local sysctl_path=/proc/sys/net/ipv4/ip_unprivileged_port_start
    local unprivileged_port_start=""
    if [[ -r "${sysctl_path}" ]]; then
        read -r unprivileged_port_start < "${sysctl_path}" || true
    fi
    if [[ "${unprivileged_port_start}" != "0" ]]; then
        # TODO: UNSAFE CONFIGURATION. Temporary workaround for a passt regression:
        # recent versions create forwarded host listeners after entering a user
        # namespace, so the binary's CAP_NET_BIND_SERVICE cannot authorize bind()
        # in the host network namespace. This is the only stock, unpatched setup
        # currently found to work; it makes all host ports unprivileged.
        echo "Error: passt must bind host port ${minimum}, but net.ipv4.ip_unprivileged_port_start is ${unprivileged_port_start:-unavailable} (expected 0)." >&2
        echo "This temporary workaround allows every unprivileged process on the host to bind any free TCP/UDP port." >&2
        bootstrap_hint
        exit 1
    fi
}

preflight_libvirt() {
    local require_iommufd=false
    local gpu
    for gpu in "${USED_GPUS[@]}"; do
        if [[ "${gpu}" != "none" ]]; then
            require_iommufd=true
            break
        fi
    done
    # With no --gpu option, check_params will select all available GPUs.
    if [[ ${#USED_GPUS[@]} -eq 0 ]] && \
        lspci -nnk -d 10de: 2>/dev/null | grep -qE '3D controller'; then
        require_iommufd=true
    fi

    local ubuntu_version=""
    if [[ -r /etc/os-release ]]; then
        ubuntu_version=$(
            # shellcheck disable=SC1091
            source /etc/os-release
            printf '%s' "${VERSION_ID:-}"
        )
    fi
    if [[ "${ubuntu_version}" == "26.04" ]]; then
        local passt_apparmor=/etc/apparmor.d/abstractions/libvirt-qemu
        if [[ ! -r "${passt_apparmor}" ]] || \
            ! grep -Eq '^[[:space:]]*profile[[:space:]]+passt[[:space:]]+flags=\([^)]*attach_disconnected(\.path=[^[:space:])]+)?[^)]*\)[[:space:]]*\{' \
                "${passt_apparmor}"; then
            echo "Error: Ubuntu 26.04 AppArmor blocks passt from accepting the libvirt Unix socket." >&2
            bootstrap_hint
            exit 1
        fi
    fi

    local args=(
        preflight
        --emulator "${QEMU_PATH}"
        --name "${LIBVIRT_DOMAIN_NAME}"
        --mode "${VM_MODE}"
    )
    if [[ "${require_iommufd}" == "true" ]]; then
        args+=(--require-iommufd)
    fi
    "${LIBVIRT_LAUNCHER}" "${args[@]}"
}

scan_cx7_bridges() {
    local dev_path dev_bdf vpd_file device_info
    AVAILABLE_CX7_BRIDGES=()
    for dev_path in /sys/bus/pci/devices/*/; do
        [[ -e "${dev_path}" ]] || continue
        dev_bdf=$(basename "${dev_path}")
        vpd_file="${dev_path}vpd"
        if [[ -f "${vpd_file}" ]] && grep -q "SW_MNG" "${vpd_file}" 2>/dev/null; then
            device_info=$(lspci -s "${dev_bdf}" 2>/dev/null || true)
            if [[ "${device_info}" == *"Mellanox"* && "${device_info}" == *"ConnectX-7"* ]]; then
                AVAILABLE_CX7_BRIDGES+=("${dev_bdf#0000:}")
            fi
        fi
    done
}

prepare_selected_host_devices() {
    HOSTDEV_ARGS=()
    if [[ ${#USED_GPUS[@]} -eq 0 ]]; then
        echo "GPU passthrough disabled; NVSwitch and CX7 companion devices will not be attached."
        return
    fi

    prepare_gpus_for_vfio "${USED_GPUS[@]}"
    scan_cx7_bridges

    local device
    for device in "${USED_GPUS[@]}"; do
        HOSTDEV_ARGS+=(--hostdev "gpu:${device}")
    done
    for device in "${AVAILABLE_NVSWITCHES[@]}"; do
        HOSTDEV_ARGS+=(--hostdev "aux:${device}")
    done
    for device in "${AVAILABLE_CX7_BRIDGES[@]}"; do
        HOSTDEV_ARGS+=(--hostdev "aux:${device}")
    done
}

prepare_mode_parameters() {
    SNP_VCPU_ARG=""
    PHYS_BITS_ARG=""
    CBITPOS_ARG=""

    case "${VM_MODE}" in
        tdx)
            if [[ -z "${TDX_SUPPORT}" ]]; then
                echo "Error: TDX is not supported on this system" >&2
                exit 1
            fi
            ;;
        sev-snp)
            if [[ -z "${SEV_SNP_SUPPORT}" ]]; then
                echo "Error: SEV-SNP is not supported on this system" >&2
                exit 1
            fi
            get_cbitpos
            detect_snp_vCPU
            detect_phys_bits
            SNP_VCPU_ARG=${SNP_VCPU}
            PHYS_BITS_ARG=${PHYS_BITS}
            CBITPOS_ARG=${CBITPOS}
            ;;
        untrusted)
            ;;
        *)
            echo "Error: invalid mode '${VM_MODE}'" >&2
            exit 1
            ;;
    esac
}

prepare_tap_network() {
    if [[ "${NETDEV_MODE}" != "tap" ]]; then
        return
    fi
    if [[ -z "${TAP_IFACE}" ]]; then
        TAP_IFACE="sw-tap${BASE_NIC}"
    fi
    if ! ip link show "${BRIDGE}" >/dev/null 2>&1; then
        echo "Error: bridge ${BRIDGE} does not exist. Run 'swarm-cluster.sh ensure-network' first." >&2
        exit 1
    fi
    if ! ip link show "${TAP_IFACE}" >/dev/null 2>&1; then
        ip tuntap add dev "${TAP_IFACE}" mode tap user root
    fi
    ip link set "${TAP_IFACE}" master "${BRIDGE}"
    ip link set "${TAP_IFACE}" up
}

build_kernel_cmdline() {
    local clearcpuid=" "
    local snp_additional=""
    local rootfs_hash
    rootfs_hash=$(<"${ROOTFS_HASH_PATH}")

    if [[ "${VM_MODE}" == "tdx" ]]; then
        clearcpuid=" clearcpuid=mtrr "
    elif [[ "${VM_MODE}" == "sev-snp" ]]; then
        snp_additional=" build=${RELEASE} pci=realloc,nocrs"
    fi

    KERNEL_CMD_LINE="root=LABEL=rootfs${clearcpuid}rootfs_verity.scheme=dm-verity rootfs_verity.hash=${rootfs_hash}${snp_additional}"
    if [[ "${DEBUG_MODE}" == "true" ]]; then
        KERNEL_CMD_LINE+=" console=ttyS0 systemd.log_level=trace systemd.log_target=log"
    fi
}

resolve_libvirt_qemu_user() {
    if [[ -n "${LIBVIRT_QEMU_USER}" ]]; then
        return
    fi

    local qemu_config=/etc/libvirt/qemu.conf
    local configured_user=""
    if [[ -r "${qemu_config}" ]]; then
        configured_user=$(awk '
            /^[[:space:]]*#/ { next }
            /^[[:space:]]*user[[:space:]]*=/ {
                value = $0
                sub(/^[^=]*=[[:space:]]*/, "", value)
                sub(/[[:space:]]*#.*/, "", value)
                gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
                if (value ~ /^"[^"]*"$/) {
                    sub(/^"/, "", value)
                    sub(/"$/, "", value)
                }
                configured = value
            }
            END { print configured }
        ' "${qemu_config}")
    fi
    configured_user=${configured_user:-libvirt-qemu}

    local passwd_entry=""
    if [[ "${configured_user}" =~ ^\+([0-9]+)$ ]]; then
        local configured_uid=${BASH_REMATCH[1]}
        passwd_entry=$(getent passwd | awk -F: -v uid="${configured_uid}" '
            $3 == uid { print; exit }
        ')
    else
        passwd_entry=$(getent passwd "${configured_user}" || true)
    fi
    if [[ -z "${passwd_entry}" ]]; then
        echo "Error: libvirt QEMU runtime user '${configured_user}' does not exist." >&2
        echo "Check the user setting in ${qemu_config}." >&2
        exit 1
    fi

    LIBVIRT_QEMU_USER=${passwd_entry%%:*}
    echo "Libvirt QEMU runtime user: ${LIBVIRT_QEMU_USER}"
}

grant_libvirt_file_access() {
    local label=$1 requested_path=$2 permissions=$3
    resolve_libvirt_qemu_user
    local qemu_user=${LIBVIRT_QEMU_USER}

    local path
    if ! path=$(realpath -e -- "${requested_path}"); then
        echo "Error: cannot resolve ${label} path: ${requested_path}" >&2
        exit 1
    fi

    local directory
    directory=$(dirname -- "${path}")
    local directories=()
    while [[ "${directory}" != "/" ]]; do
        directories+=("${directory}")
        directory=$(dirname -- "${directory}")
    done

    local index
    for ((index = ${#directories[@]} - 1; index >= 0; index--)); do
        directory=${directories[${index}]}
        if ! runuser -u "${qemu_user}" -- test -x "${directory}"; then
            if ! setfacl -m "u:${qemu_user}:--x" -- "${directory}"; then
                echo "Error: failed to grant ${qemu_user} traversal access to ${directory}" >&2
                exit 1
            fi
        fi
    done

    if [[ "${permissions}" == "rw-" ]]; then
        # The launcher creates mutable disks itself. Giving the QEMU process
        # ownership is more reliable than a named ACL on mounted data volumes.
        if ! chown -- "${qemu_user}" "${path}" || ! chmod -- u+rw "${path}"; then
            echo "Error: failed to assign writable ${label} to ${qemu_user}: ${path}" >&2
            exit 1
        fi
    else
        if ! setfacl -m "u:${qemu_user}:${permissions}" -- "${path}"; then
            echo "Error: failed to grant ${qemu_user} access to ${label}: ${path}" >&2
            exit 1
        fi
    fi

    if ! runuser -u "${qemu_user}" -- test -r "${path}"; then
        echo "Error: ${qemu_user} still cannot read ${label}: ${path}" >&2
        exit 1
    fi
    if [[ "${permissions}" == "rw-" ]] && \
        ! runuser -u "${qemu_user}" -- test -w "${path}"; then
        echo "Error: ${qemu_user} still cannot write ${label}: ${path}" >&2
        exit 1
    fi

    echo "Granted ${qemu_user} ${permissions} access to ${label}: ${path}"
}

grant_static_libvirt_resource_access() {
    grant_libvirt_file_access rootfs "${IMAGE_PATH}" r--
    grant_libvirt_file_access kernel "${KERNEL_PATH}" r--
    grant_libvirt_file_access firmware "${BIOS_PATH}" r--
}

cleanup_provider_disk() {
    local provider_loop=${1:-} provider_mount=${2:-}
    if [[ -n "${provider_mount}" ]] && mountpoint -q "${provider_mount}"; then
        umount "${provider_mount}" || true
    fi
    if [[ -n "${provider_loop}" ]]; then
        losetup -d "${provider_loop}" 2>/dev/null || true
    fi
    if [[ -n "${provider_mount}" ]]; then
        rmdir "${provider_mount}" 2>/dev/null || true
    fi
}

create_vm_disks() {
    local provider_loop="" provider_mount

    if [[ "${REUSE_DISKS}" == "true" && -f "${STATE_DISK_PATH}" && -f "${PROVIDER_CONFIG_DISK_PATH}" ]]; then
        if ! qemu-img info --output=json "${STATE_DISK_PATH}" | grep -q '"format": "qcow2"'; then
            echo "Error: existing state disk is not a valid qcow2 image: ${STATE_DISK_PATH}" >&2
            exit 1
        fi
        if [[ "$(blkid -o value -s TYPE "${PROVIDER_CONFIG_DISK_PATH}" || true)" != "ext4" ]]; then
            echo "Error: existing provider config disk is not ext4: ${PROVIDER_CONFIG_DISK_PATH}" >&2
            exit 1
        fi
        echo "Validated reusable state and provider disks."
        grant_libvirt_file_access state-disk "${STATE_DISK_PATH}" rw-
        grant_libvirt_file_access provider-config-disk "${PROVIDER_CONFIG_DISK_PATH}" r--
        return
    fi

    rm -f "${STATE_DISK_PATH}"
    qemu-img create -f qcow2 "${STATE_DISK_PATH}" "${STATE_DISK_SIZE}G"

    rm -f "${PROVIDER_CONFIG_DISK_PATH}"
    dd if=/dev/zero of="${PROVIDER_CONFIG_DISK_PATH}" bs=1M count=1 status=none
    mkfs.ext4 -q -O '^has_journal,^huge_file,^meta_bg,^ext_attr' \
        -L provider_config "${PROVIDER_CONFIG_DISK_PATH}"
    provider_mount=$(mktemp -d)
    trap 'cleanup_provider_disk "${provider_loop:-}" "${provider_mount:-}"' RETURN
    provider_loop=$(losetup --find --show --partscan "${PROVIDER_CONFIG_DISK_PATH}")

    mount "${provider_loop}" "${provider_mount}"
    cp -a "${PROVIDER_CONFIG}/." "${provider_mount}/"
    rm -rf "${provider_mount}/lost+found"
    cleanup_provider_disk "${provider_loop}" "${provider_mount}"
    trap - RETURN

    grant_libvirt_file_access state-disk "${STATE_DISK_PATH}" rw-
    grant_libvirt_file_access provider-config-disk "${PROVIDER_CONFIG_DISK_PATH}" r--
}

append_optional_arg() {
    local flag=$1 value=$2
    if [[ -n "${value}" ]]; then
        LAUNCH_ARGS+=("${flag}" "${value}")
    fi
}

launch_with_libvirt() {
    LAUNCH_ARGS=(
        "${LIBVIRT_LAUNCHER}"
        --name "${LIBVIRT_DOMAIN_NAME}"
        --mode "${VM_MODE}"
        --emulator "${QEMU_PATH}"
        --memory-gib "${VM_RAM}"
        --vcpus "${VM_CPU}"
        --bios "${BIOS_PATH}"
        --kernel "${KERNEL_PATH}"
        --kernel-cmdline "${KERNEL_CMD_LINE}"
        --rootfs "${IMAGE_PATH}"
        --state-disk "${STATE_DISK_PATH}"
        --provider-config-disk "${PROVIDER_CONFIG_DISK_PATH}"
        --guest-cid "${GUEST_CID}"
        --qgs-socket "/var/run/tdx-qgs/qgs.socket"
        --mac-address "${MAC_ADDRESS}"
        --netdev-mode "${NETDEV_MODE}"
        --ip-address "${IP_ADDRESS}"
        --ssh-port "${SSH_PORT}"
        --wg-port "${WG_PORT}"
        --swarm-db-gossip-port "${SWARM_DB_GOSSIP_PORT}"
        --dns-port "${DNS_PORT}"
    )
    append_optional_arg --uuid "${LIBVIRT_DOMAIN_UUID}"
    append_optional_arg --http-port "${HTTP_PORT}"
    append_optional_arg --https-port "${HTTPS_PORT}"
    append_optional_arg --pki-port "${PKI_PORT}"
    append_optional_arg --pki-vm-measure-port "${PKI_VM_MEASURE_PORT}"
    append_optional_arg --cpu-model "${SNP_VCPU_ARG}"
    append_optional_arg --phys-bits "${PHYS_BITS_ARG}"
    append_optional_arg --cbitpos "${CBITPOS_ARG}"

    if [[ "${NETDEV_MODE}" == "tap" ]]; then
        LAUNCH_ARGS+=(--bridge "${BRIDGE}" --tap-iface "${TAP_IFACE}")
    fi
    if [[ "${DEBUG_MODE}" == "true" ]]; then
        mkdir -p "$(dirname "${LOG_FILE}")"
        LAUNCH_ARGS+=(--debug --log-file "${LOG_FILE}")
    fi
    LAUNCH_ARGS+=("${HOSTDEV_ARGS[@]}")

    echo "Starting ${LIBVIRT_DOMAIN_NAME} through qemu:///system (mode=${VM_MODE}, debug=${DEBUG_MODE})"
    "${LAUNCH_ARGS[@]}"
}

main_libvirt() {
    check_target_os
    check_packages
    check_libvirt_dependencies
    check_passt_apparmor_profile
    check_tdx_vsock_apparmor_profile
    find_qemu_path
    check_qemu_version
    preflight_libvirt
    check_params
    check_passt_unprivileged_ports
    prepare_selected_host_devices
    prepare_mode_parameters

    mkdir -p "${CACHE}"
    download_release "${RELEASE}" "${RELEASE_ASSET}" "${CACHE}" "${RELEASE_REPO}"
    parse_and_download_release_files "${RELEASE_FILEPATH}"
    grant_static_libvirt_resource_access
    prepare_tap_network
    build_kernel_cmdline
    create_vm_disks
    launch_with_libvirt
}

extract_libvirt_args "$@"
parse_args "${BASE_ARGS[@]}"
detect_cpu_type
if [[ -z "${LIBVIRT_DOMAIN_NAME}" ]]; then
    LIBVIRT_DOMAIN_NAME="super-protocol-${GUEST_CID}"
fi
main_libvirt
