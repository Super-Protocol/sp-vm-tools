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

# Per-node resources.
# Bootstrap (GPU node) gets the REMAINDER of host resources after the host
# reserve and the join nodes are accounted for. Join nodes get a fixed minimum.
# Override any of these with flags; if not set, the defaults here are used
# automatically and bootstrap is sized dynamically from the detected host.
STATE_DISK_SIZE=50

# Host reserve — left for host OS, kernel, and QEMU per-VM overhead.
# CC-VMs (TDX/SEV-SNP) reserve memory HARD (no swap, no overcommit), so
# under-reserving here causes VM launch FAILURE, not slowdown. Be generous.
HOST_RESERVE_CORES=4         # cores left for the host OS / kernel / qemu threads
HOST_RESERVE_MEM=8           # GB left for the host OS
QEMU_MEM_OVERHEAD_PER_VM=1   # GB headroom per VM (firmware, device model)

# Join-node minimums (auto-used when not overridden by --join-cores/--join-mem)
JOIN_CORES=4
JOIN_MEM=4

# Computed at runtime by compute_allocation(); do not set by hand.
BOOTSTRAP_CORES=""
BOOTSTRAP_MEM=""
HOST_TOTAL_CORES=""
HOST_TOTAL_MEM=""

VM_MODE=""                     # empty = auto-detect (tdx/sev-snp) in start script
RELEASE=""                     # empty = latest; pin a working build, e.g. build-358
CACHE="/data/sp-vm/cache"
START_SCRIPT="${HOME}/projects/sp-vm-tools/scripts/start_super_protocol.sh"
PROVIDER_TEMPLATE=""           # provider config template dir (--provider-config-template)
WORKDIR="/data/sp-vm/cluster"  # per-node provider configs are generated here
WAN_IFACE=""                   # empty = auto-detect from ip route
GPU_TARGET="bootstrap"         # bootstrap | none — where to pass the GPU (single-GPU host)

# Debug mode: passes --debug true + --log_file to the start script (verbose boot log),
# and forwards SSH per node on distinct host ports (internal use only).
DEBUG_MODE="false"
SSH_PORT_BOOTSTRAP=2210
SSH_PORT_JOIN=(2211 2212)

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
# Resource detection + allocation
# ----------------------------------------------------------------------------
detect_host_resources() {
    HOST_TOTAL_CORES="$(nproc)"
    # MemTotal is in kB; round DOWN to whole GB to stay conservative.
    # NOTE: on a TDX host the kernel may already have carved out protected/CMA
    # memory before userspace, so MemTotal can be LESS than the physical DIMMs.
    # That is in our favour: we allocate from what the OS actually sees.
    local mem_kb; mem_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"
    HOST_TOTAL_MEM=$(( mem_kb / 1024 / 1024 ))
    log "Host resources detected: ${HOST_TOTAL_CORES} cores, ${HOST_TOTAL_MEM}GB RAM"
}

# Bootstrap = host_total - host_reserve - (join nodes) - (qemu overhead).
# Join nodes use the fixed minimums (JOIN_CORES/JOIN_MEM), which are either the
# built-in defaults or whatever was passed via --join-cores/--join-mem.
compute_allocation() {
    detect_host_resources

    local num_join="${#JOIN_IPS[@]}"
    local num_vms=$(( num_join + 1 ))

    # --- cores ---
    local join_cores_total=$(( JOIN_CORES * num_join ))
    local reserved_cores=$(( HOST_RESERVE_CORES + join_cores_total ))
    BOOTSTRAP_CORES=$(( HOST_TOTAL_CORES - reserved_cores ))
    # Some QEMU topologies dislike odd vCPU counts; round bootstrap down to even.
    BOOTSTRAP_CORES=$(( (BOOTSTRAP_CORES / 2) * 2 ))

    # --- memory ---
    local join_mem_total=$(( JOIN_MEM * num_join ))
    local qemu_overhead=$(( QEMU_MEM_OVERHEAD_PER_VM * num_vms ))
    local reserved_mem=$(( HOST_RESERVE_MEM + join_mem_total + qemu_overhead ))
    BOOTSTRAP_MEM=$(( HOST_TOTAL_MEM - reserved_mem ))

    # --- sanity checks ---
    if (( BOOTSTRAP_CORES < JOIN_CORES )); then
        die "Not enough cores: host=${HOST_TOTAL_CORES}, reserve=${HOST_RESERVE_CORES}, join=${join_cores_total} (${JOIN_CORES}x${num_join}) -> bootstrap would get ${BOOTSTRAP_CORES}. Lower --join-cores or --host-reserve-cores."
    fi
    if (( BOOTSTRAP_MEM < JOIN_MEM )); then
        die "Not enough RAM: host=${HOST_TOTAL_MEM}GB, reserve=${HOST_RESERVE_MEM}GB, join=${join_mem_total}GB (${JOIN_MEM}x${num_join}), qemu=${qemu_overhead}GB -> bootstrap would get ${BOOTSTRAP_MEM}GB. Lower --join-mem or --host-reserve-mem."
    fi

    log "Resource allocation:"
    log "  bootstrap : ${BOOTSTRAP_CORES} cores, ${BOOTSTRAP_MEM}GB RAM (+GPU)"
    log "  join x${num_join}    : ${JOIN_CORES} cores, ${JOIN_MEM}GB RAM each"
    log "  host kept : ${HOST_RESERVE_CORES} cores, ${HOST_RESERVE_MEM}GB + ${qemu_overhead}GB qemu overhead"
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
# Takes the user's raw config.yaml (no placeholders required) and produces a
# per-node copy with concrete values. Two-phase processing:
#   Phase 1 (awk):  inject placeholders — replaces whatever value was there
#                   with __PLACEHOLDER__, including multi-line pki_authority blocks.
#                   All other fields pass through unchanged.
#   Phase 2 (sed):  substitute placeholders with target values.
#
# Fields managed by the script (replaced regardless of original value):
#   swarm_db.node_name, swarm_db.advertise_addr, swarm_db.join_addresses
#   pki_authority.networkID (auto-generated UUID, same for all nodes)
#   pki_authority.caBundle (injected if missing), pki_authority.servers (injected if missing)
#
# Everything else (github, tags, powerdns, base_domain, …) passes through as-is.
prepare_config() {
    local role="$1"          # bootstrap | join1 | join2
    local node_ip="$2"
    local node_name="$3"
    local join_addr="$4"     # "" for bootstrap, "10.0.0.10:7946" for join
    local ca_bundle="${5:-}" # PEM CA cert (join nodes only)
    local network_id="${6:-}" # UUID for pki_authority.networkID (same across cluster)
    local dest="${WORKDIR}/${role}"

    rm -rf "${dest}"
    mkdir -p "${dest}"
    cp -r "${PROVIDER_TEMPLATE}/." "${dest}/"

    local join_yaml="[]"
    if [[ -n "${join_addr}" ]]; then
        join_yaml="[\"${join_addr}\"]"
    fi

    find "${dest}" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 | \
    while IFS= read -r -d '' f; do
        # --- Phase 1: inject placeholders with awk ---
        # Handles multi-line pki_authority blocks correctly;
        # simple scalar fields are matched by key regex.
        awk -i inplace -v role="${role}" '
        BEGIN                         { in_pki = 0; ca_done = 0; srv_done = 0; skip_body = 0 }
        /^pki_authority:/             { in_pki = 1; ca_done = 0; srv_done = 0; skip_body = 0; print; next }
        in_pki && /^[a-z]/            { in_pki = 0; skip_body = 0 }

        # Skip remaining body lines of a replaced block (indent 3+ = body, indent 0-2 = next key)
        in_pki && skip_body {
            if (/^[[:space:]]{3,}/) { next }
            skip_body = 0
        }

        # Replace existing caBundle block with placeholder
        in_pki && !skip_body && /caBundle:/ {
            print "  caBundle: |"
            print "    __CA_BUNDLE__"
            ca_done = 1; skip_body = 1; next
        }
        # Bootstrap: remove existing servers block (join nodes keep original)
        in_pki && !skip_body && role == "bootstrap" && /servers:/ {
            srv_done = 1; skip_body = 1; next
        }
        # Replace networkID value with placeholder (auto-generated UUID)
        in_pki && !skip_body && /networkID:/ {
            sub(/:.*/, ": __NETWORK_ID__")
            print
            if (!ca_done) { print "  caBundle: |"; print "    __CA_BUNDLE__"; ca_done = 1 }
            if (role == "bootstrap" && !srv_done) { print "  servers: []"; srv_done = 1 }
            next
        }

        # Simple scalar fields — replace value with placeholder
        /^[[:space:]]*node_name:/       { sub(/:.*/, ": __NODE_NAME__"); print; next }
        /^[[:space:]]*advertise_addr:/  { sub(/:.*/, ": __ADVERTISE_IP__"); print; next }
        /^[[:space:]]*join_addresses:/  { sub(/:.*/, ": __JOIN_ADDRESSES__"); print; next }

        { print }
        ' "${f}"

        # --- Phase 2: substitute placeholders with concrete values ---
        sed -i \
            -e "s|__ADVERTISE_IP__|\"${node_ip}\"|g" \
            -e "s|__BIND_IP__|0.0.0.0|g" \
            -e "s|__BOOTSTRAP_IP__|${BOOTSTRAP_IP}|g" \
            -e "s|__NODE_NAME__|\"${node_name}\"|g" \
            -e "s|__JOIN_ADDRESSES__|${join_yaml}|g" \
            -e "s|__NETWORK_ID__|\"${network_id}\"|g" \
            "${f}"
        # __CA_BUNDLE__ contains newlines — sed can't handle multiline replacement.
        # Replace the placeholder line with the PEM, preserving the YAML block indent.
        if [[ -n "${ca_bundle}" ]]; then
            # Detect the indentation of the __CA_BUNDLE__ line and apply it to every PEM line.
            CA_BUNDLE="${ca_bundle}" awk '
                /__CA_BUNDLE__/ {
                    match($0, /^[[:space:]]*/)
                    indent = substr($0, 1, RLENGTH)
                    n = split(ENVIRON["CA_BUNDLE"], lines, "\n")
                    for (i = 1; i <= n; i++) {
                        # drop a possible trailing empty line
                        if (i == n && lines[i] == "") continue
                        print indent lines[i]
                    }
                    next
                }
                { print }
            ' "${f}" > "${f}.tmp" && mv "${f}.tmp" "${f}"
        else
            # bootstrap: remove caBundle block (bootstrap is self-sufficient).
            # servers is already handled by awk (removed or set to []).
            sed -i '/caBundle: |/{N;/__CA_BUNDLE__/d;}' "${f}"
        fi
    done

    log "provider config ready: ${dest} (ip=${node_ip}, join=${join_yaml}, network_id=${network_id}, ca_bundle=$(if [[ -n "${ca_bundle}" ]]; then echo 'yes'; else echo 'no'; fi))"
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
    local node_cores="$7"    # vCPU count for this node
    local node_mem="$8"      # RAM (GB) for this node
    local ssh_port="${9:-}"  # host SSH port (debug mode only)
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

    # Debug mode: verbose boot log + per-node SSH port. start_super_protocol.sh
    # requires --log_file when --debug true. NOTE: the script forwards SSH via
    # hostfwd (user-mode) only; in tap mode that hostfwd is inactive, so SSH must
    # go to the VM's bridge IP (ssh ubuntu@<node_ip>). The main value of debug
    # here is the verbose serial/boot log written to the log file.
    local debug_args=()
    if [[ "${DEBUG_MODE}" == "true" ]]; then
        debug_args=(--debug true --log_file "${CACHE}/boot-${node_ip##*.}.log")
        [[ -n "${ssh_port}" ]] && debug_args+=(--ssh_port "${ssh_port}")
    fi

    # build the patched start-script command line in tap mode
    local cmd=(
        "${START_SCRIPT}"
        --netdev_mode tap
        --bridge "${BRIDGE}"
        --tap_iface "${tap_iface}"
        --cores "${node_cores}"
        --mem "${node_mem}"
        --provider_config "${provider_dir}"
        --mac_address "${mac}"
        --vm_ip "${node_ip}/24"
        --state_disk_size "${STATE_DISK_SIZE}"
        --state_disk_path "${CACHE}/state-${node_ip##*.}.qcow2"
        --provider_config_disk_path "${CACHE}/pcfg-${node_ip##*.}.img"
        --cache "${CACHE}"
        --guest-cid "${cid}"
        "${mode_args[@]}"
        "${release_args[@]}"
        "${gpu_args[@]}"
        "${debug_args[@]}"
        --swarm-init "${swarm_init}"
    )

    log "Starting ${session}: ip=${node_ip} cid=${cid} tap=${tap_iface} swarm-init=${swarm_init} gpu=${with_gpu} debug=${DEBUG_MODE}"

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
# 4. Healthcheck: wait until bootstrap is listening on gossip AND PKI is serving
#    the CA certificate (not just port open — we pull the actual PEM)
# ----------------------------------------------------------------------------
wait_bootstrap() {
    local timeout="${1:-1000}"
    local waited=0
    local pki_url="https://${BOOTSTRAP_IP}:${PKI_PORT}/api/v1/pki/certs/ca"
    log "Waiting for bootstrap: gossip ${BOOTSTRAP_IP}:${GOSSIP_PORT} + PKI ${pki_url}"
    log "  timeout: ${timeout}s"

    while (( waited < timeout )); do
        local gossip_ok=false pki_ok=false
        # gossip 7946 (tcp) — swarm-db memberlist
        if nc -z -w2 "${BOOTSTRAP_IP}" "${GOSSIP_PORT}" 2>/dev/null; then gossip_ok=true; fi

        # pki 9443 — must actually serve the CA certificate (not just accept TCP)
        local pki_resp
        pki_resp="$(curl -sk --connect-timeout 2 --max-time 5 "${pki_url}" 2>/dev/null || true)"
        if [[ -n "${pki_resp}" ]] && echo "${pki_resp}" | grep -q 'BEGIN CERTIFICATE'; then
            pki_ok=true
        fi

        if [[ "${gossip_ok}" == true && "${pki_ok}" == true ]]; then
            echo >&2
            log "bootstrap is ready (gossip=${gossip_ok}, PKI serving CA cert)"
            return 0
        fi

        local pki_status="down"
        [[ "${pki_ok}" == true ]] && pki_status="serving"
        printf '\r[%s]   [%ss] gossip=%s  pki=%s\033[K' \
            "$(date +%H:%M:%S)" "${waited}" \
            "$(if ${gossip_ok}; then echo 'up'; else echo 'down'; fi)" \
            "${pki_status}" >&2
        sleep 5; waited=$(( waited + 5 ))
    done
    echo >&2
    die "bootstrap did not come up within ${timeout}s. Check: tmux attach -t ${TMUX_BOOTSTRAP}"
}

# ----------------------------------------------------------------------------
# 4b. Fetch the CA bundle from the bootstrap PKI authority
# ----------------------------------------------------------------------------
fetch_ca_bundle() {
    local pki_url="https://${BOOTSTRAP_IP}:${PKI_PORT}/api/v1/pki/certs/ca"
    log "Fetching CA bundle from bootstrap PKI: ${pki_url}"

    local ca_pem
    ca_pem="$(curl -sk --connect-timeout 5 --max-time 10 "${pki_url}" 2>/dev/null)" || true
    if [[ -z "${ca_pem}" ]]; then
        die "Failed to fetch CA bundle from ${pki_url} — PKI not responding"
    fi
    if ! echo "${ca_pem}" | grep -q 'BEGIN CERTIFICATE'; then
        err "Response doesn't look like a PEM certificate:"
        err "${ca_pem}"
        die "Invalid CA bundle from ${pki_url}"
    fi

    log "CA bundle fetched successfully (${#ca_pem} bytes)"

    # Return it via stdout (caller captures with $())
    echo "${ca_pem}"
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
    command -v nc &>/dev/null   || die "nc is required (apt install netcat-openbsd)"
    command -v curl &>/dev/null || die "curl is required (apt install curl)"
    command -v awk &>/dev/null  || die "awk is required"
    command -v tmux &>/dev/null || die "tmux is required (apt install tmux)"
    command -v nft &>/dev/null  || die "nftables is required (apt install nftables)"

    mkdir -p "${CACHE}" "${WORKDIR}"

    # Detect host resources and size the bootstrap node dynamically.
    compute_allocation

    ensure_network

    # --- generate cluster-wide networkID (UUID) ---
    local network_id
    if command -v uuidgen &>/dev/null; then
        network_id="$(uuidgen)"
    elif [[ -r /proc/sys/kernel/random/uuid ]]; then
        network_id="$(cat /proc/sys/kernel/random/uuid)"
    else
        network_id="$(od -x /dev/urandom | head -1 | awk '{print $2$3$4"-"$5$6"-"$7$8"-"$9$10"-"$11$12$13}')"
    fi
    log "Cluster networkID: ${network_id}"

    # --- bootstrap node (no CA bundle, no join addresses) ---
    local boot_dir
    boot_dir="$(prepare_config bootstrap "${BOOTSTRAP_IP}" "swarm-bootstrap" "" "" "${network_id}")"

    local boot_gpu=false
    [[ "${GPU_TARGET}" == "bootstrap" ]] && boot_gpu=true
    start_vm "${TMUX_BOOTSTRAP}" "${BOOTSTRAP_IP}" "${CID_BOOTSTRAP}" "${boot_dir}" \
        true "${boot_gpu}" "${BOOTSTRAP_CORES}" "${BOOTSTRAP_MEM}" "${SSH_PORT_BOOTSTRAP}"

    wait_bootstrap 1000

    # --- fetch PKI CA bundle from bootstrap ---
    log "Bootstrap is up — fetching CA bundle for join nodes..."
    local ca_bundle
    ca_bundle="$(fetch_ca_bundle)"
    log "CA bundle: $(echo "${ca_bundle}" | head -1) ... ($(echo "${ca_bundle}" | wc -l) lines)"

    # --- join nodes (with CA bundle and bootstrap gossip address) ---
    local join1_dir join2_dir
    log "Generating join-node provider configs with CA bundle..."
    join1_dir="$(prepare_config join1 "${JOIN_IPS[0]}" "swarm-join-1" "${BOOTSTRAP_IP}:${GOSSIP_PORT}" "${ca_bundle}" "${network_id}")"
    join2_dir="$(prepare_config join2 "${JOIN_IPS[1]}" "swarm-join-2" "${BOOTSTRAP_IP}:${GOSSIP_PORT}" "${ca_bundle}" "${network_id}")"

    start_vm "${TMUX_JOIN[0]}" "${JOIN_IPS[0]}" "${CID_JOIN[0]}" "${join1_dir}" \
        false false "${JOIN_CORES}" "${JOIN_MEM}" "${SSH_PORT_JOIN[0]}"
    start_vm "${TMUX_JOIN[1]}" "${JOIN_IPS[1]}" "${CID_JOIN[1]}" "${join2_dir}" \
        false false "${JOIN_CORES}" "${JOIN_MEM}" "${SSH_PORT_JOIN[1]}"

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
    echo "=== resources ==="
    if [[ -n "$(command -v nproc)" ]]; then
        local _c; _c="$(nproc)"
        local _m; _m=$(( $(awk '/^MemTotal:/{print $2}' /proc/meminfo) / 1024 / 1024 ))
        echo "  host: ${_c} cores, ${_m}GB"
        echo "  plan: bootstrap=remainder+GPU, join=${JOIN_CORES}c/${JOIN_MEM}g each, reserve=${HOST_RESERVE_CORES}c/${HOST_RESERVE_MEM}g"
    fi
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
        --join-cores)         JOIN_CORES="$2"; shift 2 ;;
        --join-mem)           JOIN_MEM="$2"; shift 2 ;;
        --host-reserve-cores) HOST_RESERVE_CORES="$2"; shift 2 ;;
        --host-reserve-mem)   HOST_RESERVE_MEM="$2"; shift 2 ;;
        --state-disk-size) STATE_DISK_SIZE="$2"; shift 2 ;;
        --mode)           VM_MODE="$2"; shift 2 ;;
        --release)        RELEASE="$2"; shift 2 ;;
        --debug)          DEBUG_MODE="$2"; shift 2 ;;
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
