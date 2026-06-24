#!/bin/bash
#
# swarm-cluster.sh — bring up a 3-node Super Protocol Swarm CC-VM cluster on one host.
#
# Topology:
#   - isolated bridge swarmbr0 (10.0.0.1/24), host = gateway 10.0.0.1
#   - 3 VMs with tap interfaces in the bridge: 10.0.0.10 (bootstrap) + .11/.12 (join)
#   - each VM gets its address statically via cloud-init/netplan inside the image
#   - external ingress (host WAN) DNAT only to bootstrap: 80/443/9443 tcp, 53 tcp+udp
#   - join nodes fetch PKI (9443) from bootstrap over the LOCAL address 10.0.0.10 (no hairpin)
#   - each VM runs in its own tmux session
#
# Requires a patched start_super_protocol.sh (see network-tap.patch.md):
# support for --netdev_mode tap --bridge <name>.
#
# Usage:
#   sudo ./swarm-cluster.sh up        --provider-config-template ./provider-template [opts]
#   sudo ./swarm-cluster.sh ensure-network
#   sudo ./swarm-cluster.sh down
#   sudo ./swarm-cluster.sh status
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration (override via flags or edit here)
# ----------------------------------------------------------------------------
BRIDGE="swarmbr0"
SUBNET_CIDR="10.0.0.0/24"
HOST_GW_IP="10.0.0.1"          # host address on the bridge = gateway for VMs
SUBNET_PREFIX="10.0.0"
BOOTSTRAP_IP="10.0.0.10"
JOIN_IPS=("10.0.0.11" "10.0.0.12")

GOSSIP_PORT=7946
PKI_PORT=9443

# MAC and CID — must be unique per node
MAC_BASE="52:54:00:12:34"      # suffix = last octet of the IP (10/11/12)
CID_BOOTSTRAP=122
CID_JOIN=(123 124)

# Ports forwarded externally to bootstrap
WAN_TCP_PORTS=(80 443 9443)
WAN_UDP_PORTS=(53)
WAN_TCP_ALSO_UDP=(53)          # 53 is forwarded both tcp and udp

# Per-node resources (split the host; tune to your machine)
VM_CORES=10
VM_MEM=20
STATE_DISK_SIZE=50

VM_MODE=""                     # empty = auto-detect (tdx/sev-snp) in start script
RELEASE=""                     # empty = latest; pin a working build, e.g. build-358
CACHE="/data/sp-vm/cache"
START_SCRIPT="${HOME}/projects/sp-vm-tools/scripts/start_super_protocol.sh"
PROVIDER_TEMPLATE=""           # provider config template dir (--provider-config-template)
WORKDIR="/data/sp-vm/cluster"  # per-node provider configs are generated here
WAN_IFACE=""                   # empty = auto-detect from ip route
GPU_TARGET="bootstrap"         # bootstrap | none — where to pass the GPU (single-GPU host)

# tmux sessions
TMUX_BOOTSTRAP="swarm-bootstrap"
TMUX_JOIN=("swarm-join-1" "swarm-join-2")

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
log()  { echo -e "[$(date +%H:%M:%S)] $*" >&2; }
err()  { echo -e "[$(date +%H:%M:%S)] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

require_root() {
    [[ "$EUID" -eq 0 ]] || die "Must be run as root (use sudo)."
}

detect_wan_iface() {
    if [[ -n "${WAN_IFACE}" ]]; then echo "${WAN_IFACE}"; return; fi
    ip route get 8.8.8.8 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1
}

mac_for_ip() {
    # 10.0.0.11 -> 52:54:00:12:34:11
    local ip="$1"; local last="${ip##*.}"
    printf "%s:%02d" "${MAC_BASE}" "${last}"
}

# ----------------------------------------------------------------------------
# 1. Network: bridge + forwarding + outbound NAT (for egress: GHCR/ACME pulls)
# ----------------------------------------------------------------------------
ensure_network() {
    require_root
    local wan; wan="$(detect_wan_iface)"
    [[ -n "${wan}" ]] || die "Could not determine WAN interface. Pass --wan-iface."

    log "WAN interface: ${wan}"

    # bridge (idempotent)
    if ! ip link show "${BRIDGE}" &>/dev/null; then
        log "Creating bridge ${BRIDGE} (${HOST_GW_IP}/24)"
        ip link add name "${BRIDGE}" type bridge
        ip addr add "${HOST_GW_IP}/24" dev "${BRIDGE}"
        ip link set "${BRIDGE}" up
    else
        log "bridge ${BRIDGE} already exists"
        # make sure the address is present
        ip addr show dev "${BRIDGE}" | grep -q "${HOST_GW_IP}/24" || \
            ip addr add "${HOST_GW_IP}/24" dev "${BRIDGE}"
        ip link set "${BRIDGE}" up
    fi

    # ip forwarding
    sysctl -q -w net.ipv4.ip_forward=1

    # Outbound NAT for VM egress (pull service images from GHCR, ACME, etc.).
    # nftables: a dedicated table so it can be torn down easily.
    nft list table ip swarm_nat &>/dev/null && nft delete table ip swarm_nat
    nft add table ip swarm_nat
    nft add chain ip swarm_nat postrouting '{ type nat hook postrouting priority 100 ; }'
    nft add rule  ip swarm_nat postrouting ip saddr "${SUBNET_CIDR}" oifname "${wan}" masquerade

    log "Network ready: bridge ${BRIDGE}, NAT via ${wan}"
}

# ----------------------------------------------------------------------------
# 2. Generate per-node provider config from the template
# ----------------------------------------------------------------------------
# Logic: copy the template into WORKDIR/<role>, then substitute placeholders:
#   __ADVERTISE_IP__   -> node local bridge IP (for advertise_addr)
#   __BIND_IP__        -> 0.0.0.0 (listen on all; the address is local anyway)
#   __JOIN_ADDRESSES__ -> "[]" on bootstrap, '["10.0.0.10:7946"]' on join
#   __BOOTSTRAP_IP__   -> 10.0.0.10 (for PKI/encryption.addresses in join configs)
#   __NODE_NAME__      -> node name
# If a placeholder is absent from the template, sed simply makes no change (safe).
# PKI fields and encryption.mode are NOT touched — they are owned by your template/image.
prepare_config() {
    local role="$1"          # bootstrap | join1 | join2
    local node_ip="$2"
    local node_name="$3"
    local join_addr="$4"     # "" for bootstrap, "10.0.0.10:7946" for join
    local dest="${WORKDIR}/${role}"

    rm -rf "${dest}"
    mkdir -p "${dest}"
    cp -r "${PROVIDER_TEMPLATE}/." "${dest}/"

    local join_yaml="[]"
    if [[ -n "${join_addr}" ]]; then
        join_yaml="[\"${join_addr}\"]"
    fi

    # substitute placeholders across all yaml files of the template
    find "${dest}" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 | \
    while IFS= read -r -d '' f; do
        sed -i \
            -e "s|__ADVERTISE_IP__|${node_ip}|g" \
            -e "s|__BIND_IP__|0.0.0.0|g" \
            -e "s|__BOOTSTRAP_IP__|${BOOTSTRAP_IP}|g" \
            -e "s|__NODE_NAME__|${node_name}|g" \
            -e "s|__JOIN_ADDRESSES__|${join_yaml}|g" \
            "${f}"
    done

    log "provider config ready: ${dest} (ip=${node_ip}, join=${join_yaml})"
    echo "${dest}"
}

# ----------------------------------------------------------------------------
# 3. Start a single VM in tmux
# ----------------------------------------------------------------------------
start_vm() {
    local session="$1"
    local node_ip="$2"
    local cid="$3"
    local provider_dir="$4"
    local swarm_init="$5"    # true | false
    local with_gpu="$6"      # true | false
    local tap_iface="sw-tap-${node_ip##*.}"
    local mac; mac="$(mac_for_ip "${node_ip}")"

    if tmux has-session -t "${session}" 2>/dev/null; then
        err "tmux session ${session} already exists. Skipping (run 'down' to clean up)."
        return 0
    fi

    local gpu_args=()
    if [[ "${with_gpu}" == "true" ]]; then
        gpu_args=()    # GPU defaults to all available; on a single-GPU host that is one GPU
    else
        gpu_args=(--gpu none)
    fi

    local mode_args=()
    [[ -n "${VM_MODE}" ]] && mode_args=(--mode "${VM_MODE}")

    local release_args=()
    [[ -n "${RELEASE}" ]] && release_args=(--release "${RELEASE}")

    # build the patched start-script command line in tap mode
    local cmd=(
        "${START_SCRIPT}"
        --netdev_mode tap
        --bridge "${BRIDGE}"
        --tap_iface "${tap_iface}"
        --cores "${VM_CORES}"
        --mem "${VM_MEM}"
        --provider_config "${provider_dir}"
        --state_disk_size "${STATE_DISK_SIZE}"
        --state_disk_path "${CACHE}/state-${node_ip##*.}.qcow2"
        --provider_config_disk_path "${CACHE}/pcfg-${node_ip##*.}.img"
        --cache "${CACHE}"
        --guest-cid "${cid}"
        "${mode_args[@]}"
        "${release_args[@]}"
        "${gpu_args[@]}"
        --swarm-init "${swarm_init}"
    )

    log "Starting ${session}: ip=${node_ip} cid=${cid} tap=${tap_iface} swarm-init=${swarm_init} gpu=${with_gpu}"

    # Safety net: drop any empty array elements before building the runner.
    # An empty positional arg would shift the start-script's two-step arg parser
    # and split a "--flag value" pair (the classic "Unknown parameter: provider").
    local cmd_clean=()
    local a
    for a in "${cmd[@]}"; do
        [[ -n "${a}" ]] && cmd_clean+=("${a}")
    done

    # Write the launch command to an executable runner script, then run THAT in tmux.
    # Passing the whole command as a single "${cmd[*]}" string to tmux (which runs it
    # via sh -c) mangles quoting and empty array elements; a runner script expands the
    # array correctly and is also handy for manual restarts.
    local runner="${CACHE}/run-${node_ip##*.}.sh"
    {
        echo '#!/bin/bash'
        echo 'set -o pipefail'
        printf 'exec'
        printf ' %q' "${cmd_clean[@]}"
        printf ' 2>&1 | tee %q\n' "${CACHE}/log-${node_ip##*.}.txt"
    } > "${runner}"
    chmod +x "${runner}"

    tmux new-session -d -s "${session}" "${runner}"

    # Fail fast: if the command dies immediately (bad flags, missing release, etc.),
    # the tmux session collapses and we must not proceed into a blind wait.
    # The image download alone takes a while, so we only check that the session
    # survives the first few seconds — enough to catch instant failures.
    sleep 6
    if ! tmux has-session -t "${session}" 2>/dev/null; then
        err "Session ${session} exited immediately — startup failed."
        err "Last lines of ${CACHE}/log-${node_ip##*.}.txt:"
        tail -n 20 "${CACHE}/log-${node_ip##*.}.txt" 2>/dev/null | sed 's/^/    /' >&2 || true
        die "Aborting. Fix the error above (often: wrong --release, or start script not patched)."
    fi
}

# ----------------------------------------------------------------------------
# 4. Healthcheck: wait until bootstrap is listening on gossip AND PKI
# ----------------------------------------------------------------------------
wait_bootstrap() {
    local timeout="${1:-600}"
    local waited=0
    log "Waiting for bootstrap (${BOOTSTRAP_IP}: ${GOSSIP_PORT} gossip + ${PKI_PORT} pki), timeout ${timeout}s"
    while (( waited < timeout )); do
        local gossip_ok=false pki_ok=false
        # gossip 7946 (tcp) — swarm-db memberlist
        if nc -z -w2 "${BOOTSTRAP_IP}" "${GOSSIP_PORT}" 2>/dev/null; then gossip_ok=true; fi
        # pki 9443 — required by join nodes to obtain the encryption key
        if nc -z -w2 "${BOOTSTRAP_IP}" "${PKI_PORT}" 2>/dev/null; then pki_ok=true; fi

        if [[ "${gossip_ok}" == true && "${pki_ok}" == true ]]; then
            log "bootstrap is ready (gossip + pki listening)"
            return 0
        fi
        sleep 5; waited=$(( waited + 5 ))
        (( waited % 30 == 0 )) && log "  ...waiting (${waited}s): gossip=${gossip_ok} pki=${pki_ok}"
    done
    die "bootstrap did not come up within ${timeout}s. Check: tmux attach -t ${TMUX_BOOTSTRAP}"
}

# ----------------------------------------------------------------------------
# 5. nftables DNAT: WAN -> bootstrap (external ingress, TLS passthrough)
# ----------------------------------------------------------------------------
setup_dnat() {
    require_root
    local wan; wan="$(detect_wan_iface)"
    [[ -n "${wan}" ]] || die "Could not determine WAN interface."

    log "Configuring DNAT ${wan} -> ${BOOTSTRAP_IP}"

    nft list table ip swarm_dnat &>/dev/null && nft delete table ip swarm_dnat
    nft add table ip swarm_dnat
    nft add chain ip swarm_dnat prerouting  '{ type nat hook prerouting priority -100 ; }'

    # TCP ports
    for p in "${WAN_TCP_PORTS[@]}"; do
        nft add rule ip swarm_dnat prerouting iifname "${wan}" tcp dport "${p}" dnat to "${BOOTSTRAP_IP}"
    done
    # UDP ports
    for p in "${WAN_UDP_PORTS[@]}"; do
        nft add rule ip swarm_dnat prerouting iifname "${wan}" udp dport "${p}" dnat to "${BOOTSTRAP_IP}"
    done
    # add tcp:53 as well (53 needs both tcp and udp)
    for p in "${WAN_TCP_ALSO_UDP[@]}"; do
        nft add rule ip swarm_dnat prerouting iifname "${wan}" tcp dport "${p}" dnat to "${BOOTSTRAP_IP}"
    done

    log "DNAT ready: tcp ${WAN_TCP_PORTS[*]} + tcp/udp ${WAN_TCP_ALSO_UDP[*]} -> ${BOOTSTRAP_IP}"
    log "NOTE: PKI 9443 is exposed externally for outside consumers; join nodes fetch PKI locally (10.0.0.10), no hairpin needed."
}

# ----------------------------------------------------------------------------
# Top-level commands
# ----------------------------------------------------------------------------
cmd_up() {
    require_root
    [[ -n "${PROVIDER_TEMPLATE}" ]] || die "Specify --provider-config-template <dir>"
    [[ -d "${PROVIDER_TEMPLATE}" ]] || die "Template ${PROVIDER_TEMPLATE} not found"
    [[ -x "${START_SCRIPT}" ]] || die "start script not found/executable: ${START_SCRIPT}"
    command -v nc &>/dev/null || die "nc is required (apt install netcat-openbsd)"
    command -v tmux &>/dev/null || die "tmux is required (apt install tmux)"
    command -v nft &>/dev/null || die "nftables is required (apt install nftables)"

    mkdir -p "${CACHE}" "${WORKDIR}"

    ensure_network

    # generate configs
    local boot_dir join1_dir join2_dir
    boot_dir="$(prepare_config bootstrap "${BOOTSTRAP_IP}" "swarm-bootstrap" "")"
    join1_dir="$(prepare_config join1 "${JOIN_IPS[0]}" "swarm-join-1" "${BOOTSTRAP_IP}:${GOSSIP_PORT}")"
    join2_dir="$(prepare_config join2 "${JOIN_IPS[1]}" "swarm-join-2" "${BOOTSTRAP_IP}:${GOSSIP_PORT}")"

    # bootstrap first, with GPU and swarm-init
    local boot_gpu=false
    [[ "${GPU_TARGET}" == "bootstrap" ]] && boot_gpu=true
    start_vm "${TMUX_BOOTSTRAP}" "${BOOTSTRAP_IP}" "${CID_BOOTSTRAP}" "${boot_dir}" true "${boot_gpu}"

    wait_bootstrap 600

    # join nodes, no GPU, no swarm-init
    start_vm "${TMUX_JOIN[0]}" "${JOIN_IPS[0]}" "${CID_JOIN[0]}" "${join1_dir}" false false
    start_vm "${TMUX_JOIN[1]}" "${JOIN_IPS[1]}" "${CID_JOIN[1]}" "${join2_dir}" false false

    # external ingress
    setup_dnat

    log "Cluster started. Sessions: tmux ls"
    log "  bootstrap: tmux attach -t ${TMUX_BOOTSTRAP}"
    log "  join:      tmux attach -t ${TMUX_JOIN[0]} | ${TMUX_JOIN[1]}"
}

cmd_down() {
    require_root
    log "Stopping cluster..."

    # tmux sessions (QEMU runs inside them — killing the session stops the VM)
    for s in "${TMUX_BOOTSTRAP}" "${TMUX_JOIN[@]}"; do
        if tmux has-session -t "${s}" 2>/dev/null; then
            log "  kill tmux ${s}"
            tmux send-keys -t "${s}" C-c 2>/dev/null || true
            sleep 1
            tmux kill-session -t "${s}" 2>/dev/null || true
        fi
    done

    # also reap any qemu started by this cluster, just in case
    pkill -f "qemu-system-x86_64.*sw-tap-" 2>/dev/null || true
    sleep 1

    # tap interfaces
    for ip in "${BOOTSTRAP_IP}" "${JOIN_IPS[@]}"; do
        local tap="sw-tap-${ip##*.}"
        ip link show "${tap}" &>/dev/null && { log "  del ${tap}"; ip link del "${tap}"; }
    done

    # nftables tables
    nft list table ip swarm_dnat &>/dev/null && { log "  del nft swarm_dnat"; nft delete table ip swarm_dnat; }
    nft list table ip swarm_nat  &>/dev/null && { log "  del nft swarm_nat";  nft delete table ip swarm_nat; }

    log "Cluster stopped. bridge ${BRIDGE} left in place (remove with: ip link del ${BRIDGE})."
}

cmd_status() {
    echo "=== tmux ==="
    tmux ls 2>/dev/null | grep -E 'swarm-' || echo "  no sessions"
    echo "=== bridge ==="
    ip -br addr show "${BRIDGE}" 2>/dev/null || echo "  no bridge ${BRIDGE}"
    echo "=== tap ==="
    for ip in "${BOOTSTRAP_IP}" "${JOIN_IPS[@]}"; do
        local tap="sw-tap-${ip##*.}"
        ip -br link show "${tap}" 2>/dev/null || echo "  ${tap}: missing"
    done
    echo "=== bootstrap ports (${BOOTSTRAP_IP}) ==="
    for p in "${GOSSIP_PORT}" "${PKI_PORT}" 80 443 53; do
        if nc -z -w2 "${BOOTSTRAP_IP}" "${p}" 2>/dev/null; then echo "  ${p}: open"; else echo "  ${p}: closed"; fi
    done
    echo "=== nftables ==="
    nft list table ip swarm_dnat 2>/dev/null | grep -c dnat | xargs -I{} echo "  dnat rules: {}"
    nft list table ip swarm_nat  &>/dev/null && echo "  swarm_nat: present" || echo "  swarm_nat: absent"
}

# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------
CMD="${1:-}"; shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider-config-template) PROVIDER_TEMPLATE="$2"; shift 2 ;;
        --start-script)   START_SCRIPT="$2"; shift 2 ;;
        --bridge)         BRIDGE="$2"; shift 2 ;;
        --wan-iface)      WAN_IFACE="$2"; shift 2 ;;
        --cache)          CACHE="$2"; shift 2 ;;
        --workdir)        WORKDIR="$2"; shift 2 ;;
        --cores)          VM_CORES="$2"; shift 2 ;;
        --mem)            VM_MEM="$2"; shift 2 ;;
        --state-disk-size) STATE_DISK_SIZE="$2"; shift 2 ;;
        --mode)           VM_MODE="$2"; shift 2 ;;
        --release)        RELEASE="$2"; shift 2 ;;
        --gpu-target)     GPU_TARGET="$2"; shift 2 ;;   # bootstrap | none
        --bootstrap-ip)   BOOTSTRAP_IP="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

case "${CMD}" in
    up)              cmd_up ;;
    down)            cmd_down ;;
    status)          cmd_status ;;
    ensure-network)  ensure_network ;;
    setup-dnat)      setup_dnat ;;
    "" )             die "Command: up | down | status | ensure-network | setup-dnat" ;;
    *)               die "Unknown command: ${CMD}" ;;
esac
