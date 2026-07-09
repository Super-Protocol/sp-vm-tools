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

# Per-node resources.
# Bootstrap (GPU node) gets the REMAINDER of host resources after the host
# reserve and the join nodes are accounted for. Join nodes get a fixed minimum.
# Override any of these with flags; if not set, the defaults here are used
# automatically and bootstrap is sized dynamically from the detected host.
#
# Disk: auto-detected — 90% of free space on CACHE, split proportionally to CPU
# cores. Override with --state-disk-size (applies to all nodes equally).
STATE_DISK_SIZE=""             # empty = auto (proportional to cores); set to override
HOST_DISK_RESERVE_PCT=10        # % of free disk left for the host

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
BOOTSTRAP_DISK=""
JOIN_DISK=""
HOST_TOTAL_CORES=""
HOST_TOTAL_MEM=""
HOST_TOTAL_DISK=""
HOST_AVAIL_DISK=""

VM_MODE=""                     # empty = auto-detect (tdx/sev-snp) in start script
RELEASE=""                     # empty = latest; pin a working build, e.g. build-358
CACHE="/data/sp-vm/cache"
START_SCRIPT="${PWD}/start_super_protocol.sh"
PROVIDER_TEMPLATE=""           # provider config template dir (--provider-config-template)
WORKDIR="/data/sp-vm/cluster"  # per-node provider configs are generated here
WAN_IFACE=""                   # empty = auto-detect from ip route
GPU_TARGET="bootstrap"         # bootstrap | none — where to pass the GPU (single-GPU host)

NETWORK_SCRIPT="${PWD}/swarm-network.sh"
GLOBAL_ID=""                   # empty = generate a fresh one on `up`
BASE_DOMAIN="superprotocol.io" # gw hostname = gw.dyn.<global_id>.<base_domain>
GW_BACKEND_PORT=443

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

# Detect free disk space (GB) on the filesystem that hosts CACHE.
# Called by compute_allocation() only when STATE_DISK_SIZE is empty (auto mode).
detect_host_disk() {
    mkdir -p "${CACHE}"
    # df --output=avail returns blocks; -B G gives integer GB (rounded down).
    local avail_gb
    avail_gb="$(df --output=avail -BG "${CACHE}" 2>/dev/null | tail -1 | tr -d ' G')" || true
    if [[ -z "${avail_gb}" || "${avail_gb}" -le 0 ]]; then
        die "Cannot detect free disk space on ${CACHE}. Check filesystem."
    fi
    HOST_AVAIL_DISK="${avail_gb}"
    HOST_TOTAL_DISK="$(df --output=size -BG "${CACHE}" 2>/dev/null | tail -1 | tr -d ' G')" || true
    log "Host disk detected: ${HOST_AVAIL_DISK}GB free (total: ${HOST_TOTAL_DISK}GB) on ${CACHE}"
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

    # --- disk (auto or manual) ---
    if [[ -n "${STATE_DISK_SIZE}" ]]; then
        # Manual override: same size for all nodes.
        BOOTSTRAP_DISK="${STATE_DISK_SIZE}"
        JOIN_DISK="${STATE_DISK_SIZE}"
        log "Disk allocation (manual): ${STATE_DISK_SIZE}GB per node"
    else
        # Auto: 90% of free space, split proportionally to CPU cores.
        detect_host_disk
        local host_keep_pct="${HOST_DISK_RESERVE_PCT}"
        local alloc_gb=$(( HOST_AVAIL_DISK * (100 - host_keep_pct) / 100 ))
        local total_cores=$(( BOOTSTRAP_CORES + join_cores_total ))
        if (( total_cores <= 0 )); then
            die "total_cores is zero — cannot compute disk allocation"
        fi
        BOOTSTRAP_DISK=$(( alloc_gb * BOOTSTRAP_CORES / total_cores ))
        JOIN_DISK=$(( alloc_gb * JOIN_CORES / total_cores ))
        # Ensure minimum 10GB per node so the VM has enough for rootfs + state.
        local min_disk=10
        if (( BOOTSTRAP_DISK < min_disk )); then BOOTSTRAP_DISK="${min_disk}"; fi
        if (( JOIN_DISK < min_disk )); then JOIN_DISK="${min_disk}"; fi
        log "Disk allocation (auto: ${alloc_gb}GB usable = ${HOST_AVAIL_DISK}GB free − ${host_keep_pct}% host reserve):"
        log "  bootstrap : ${BOOTSTRAP_DISK}GB (${BOOTSTRAP_CORES}/${total_cores} of ${alloc_gb}GB)"
        log "  join      : ${JOIN_DISK}GB each (${JOIN_CORES}/${total_cores} of ${alloc_gb}GB)"
        log "  host kept : ~$(( HOST_AVAIL_DISK - alloc_gb ))GB (${host_keep_pct}% free space)"
    fi

    log "Resource allocation:"
    log "  bootstrap : ${BOOTSTRAP_CORES} cores, ${BOOTSTRAP_MEM}GB RAM, ${BOOTSTRAP_DISK}GB disk (+GPU)"
    log "  join x${num_join}    : ${JOIN_CORES} cores, ${JOIN_MEM}GB RAM, ${JOIN_DISK}GB disk each"
    log "  host kept : ${HOST_RESERVE_CORES} cores, ${HOST_RESERVE_MEM}GB + ${qemu_overhead}GB qemu overhead"
}

# ----------------------------------------------------------------------------
# Reset all vfio-pci devices (GPU) before cluster start.
# Fixes stale state left by a dirty QEMU exit:
#   "error bind device fd=N to iommufd=M: Invalid argument"
# Sequence per device: FLR -> unbind -> rebind -> FLR (covers the whole
# IOMMU group, not just the primary function).
# ----------------------------------------------------------------------------
reset_vfio_devices() {
    local drv="/sys/bus/pci/drivers/vfio-pci"
    [[ -d "${drv}" ]] || { log "vfio-pci driver not loaded — nothing to reset"; return 0; }

    # Refuse to reset devices under a live QEMU
    if pgrep -f 'qemu-system-x86_64.*vfio' >/dev/null 2>&1; then
        die "QEMU with VFIO still running — refusing to reset devices. Run 'down' first."
    fi

    # Collect all BDFs bound to vfio-pci, expanded to full IOMMU groups
    # (multi-function cards: GPU + audio must be reset together).
    local devs=() seen=" "
    local p d g
    for p in "${drv}"/0000:*; do
        [[ -e "${p}" ]] || continue
        d="$(basename "${p}")"
        for g in "/sys/bus/pci/devices/${d}/iommu_group/devices/"*; do
            g="$(basename "${g}")"
            [[ "${seen}" == *" ${g} "* ]] && continue
            devs+=("${g}"); seen="${seen}${g} "
        done
    done
    if (( ${#devs[@]} == 0 )); then
        log "No devices bound to vfio-pci — nothing to reset"
        return 0
    fi

    for d in "${devs[@]}"; do
        local sys="/sys/bus/pci/devices/${d}"
        local name; name="$(lspci -s "${d}" 2>/dev/null | sed 's/^[^ ]* //')"
        log "Resetting vfio device ${d} (${name:-unknown})"

        # 1. Function-level reset (if supported)
        if [[ -w "${sys}/reset" ]]; then
            echo 1 > "${sys}/reset" 2>/dev/null \
                || err "  ${d}: pre-unbind reset failed (continuing)"
        else
            log "  ${d}: no FLR support, skipping pre-reset"
        fi

        # 2. Unbind — destroys the vfio device node and any stale iommufd context
        if [[ -e "${drv}/${d}" ]]; then
            echo "${d}" > "${drv}/unbind"
            local w=0
            while [[ -e "${drv}/${d}" ]] && (( w < 20 )); do sleep 0.5; w=$((w+1)); done
            [[ -e "${drv}/${d}" ]] && { err "  ${d}: unbind did not complete"; continue; }
        fi

        # 3. Rebind to vfio-pci
        if ! echo "${d}" > "${drv}/bind" 2>/dev/null; then
            # driver_override may have been lost — restore and re-probe
            echo vfio-pci > "${sys}/driver_override" 2>/dev/null || true
            echo "${d}" > /sys/bus/pci/drivers_probe 2>/dev/null || true
        fi
        local w=0
        while [[ ! -e "${drv}/${d}" ]] && (( w < 20 )); do sleep 0.5; w=$((w+1)); done
        if [[ ! -e "${drv}/${d}" ]]; then
            err "  ${d}: failed to rebind to vfio-pci"
            continue
        fi

        # 4. Final reset on the freshly bound device
        if [[ -w "${sys}/reset" ]]; then
            sleep 1
            echo 1 > "${sys}/reset" 2>/dev/null \
                || err "  ${d}: post-bind reset failed"
        fi
        log "  ${d}: reset + rebind done"
    done
}

# ----------------------------------------------------------------------------
# VM liveness check: the runner does `exec qemu | tee`, so if QEMU dies for
# any reason the tmux session collapses. Detect that instead of waiting blind.
# ----------------------------------------------------------------------------
vm_alive() {
    local session="$1"
    tmux has-session -t "${session}" 2>/dev/null
}

report_vm_death() {
    local session="$1" node_ip="$2"
    local logf="${CACHE}/log-${node_ip##*.}.txt"
    err "VM session '${session}' has exited — QEMU failed."
    err "Last lines of ${logf}:"
    tail -n 25 "${logf}" 2>/dev/null | sed 's/^/    /' >&2 || true
    # Common failure hint
    if grep -qs 'error bind device.*iommufd.*Invalid argument' "${logf}"; then
        err "Hint: stale VFIO/iommufd state — reset_vfio_devices should fix it on next 'up'."
    fi
}
prepare_config() {
    local role="$1"
    local node_ip="$2"
    local node_name="$3"
    local join_addr="$4"
    local ca_bundle="${5:-}"
    local network_id="${6:-}"
    local dest="${WORKDIR}/${role}"

    rm -rf "${dest}"; mkdir -p "${dest}"
    cp -r "${PROVIDER_TEMPLATE}/." "${dest}/"

    local join_yaml="[]"
    [[ -n "${join_addr}" ]] && join_yaml="[\"${join_addr}\"]"

    local is_bootstrap="no"
    [[ "${role}" == "bootstrap" ]] && is_bootstrap="yes"

    # caBundle written to a temp file so awk reads it line-by-line (join nodes only)
    local ca_file=""
    if [[ "${is_bootstrap}" == "no" && -n "${ca_bundle}" ]]; then
        ca_file="$(mktemp)"
        printf '%s\n' "${ca_bundle}" > "${ca_file}"
    fi

    find "${dest}" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 | \
    while IFS= read -r -d '' f; do
        local tmp; tmp="$(mktemp)"
        awk \
            -v node_name="${node_name}" \
            -v advertise_ip="${node_ip}" \
            -v join_yaml="${join_yaml}" \
            -v network_id="${network_id}" \
            -v is_bootstrap="${is_bootstrap}" \
            -v ca_file="${ca_file}" '
        function indent(s,   i) { i = match(s, /[^ ]/); return (i ? i - 1 : 0) }

        # Emit the networkID + (for join) caBundle + (for bootstrap) servers
        # that belong to the pki section, exactly once.
        function flush_pki(   cl) {
            if (!pki_nid_done) {
                print "  networkID: \"" network_id "\""
                pki_nid_done = 1
            }
            if (ca_file != "" && !pki_ca_done) {
                print "  caBundle: |"
                while ((getline cl < ca_file) > 0) print "    " cl
                close(ca_file)
                pki_ca_done = 1
            }
            if (is_bootstrap == "yes" && !pki_srv_done) {
                print "  servers: []"
                pki_srv_done = 1
            }
        }

        BEGIN { sec = "" }
        {
            ind = indent($0)
            line = $0

            # A new top-level key (indent 0, real key, not a comment) ends any section.
            if (ind == 0 && line !~ /^[ \t]*#/ && line ~ /^[A-Za-z0-9_]+:/) {
                key = line; sub(/:.*/, "", key)
                # leaving pki without having written everything → flush now
                if (sec == "pki") flush_pki()

                if      (key == "swarm_db")      { sec = "swarm_db" }
                else if (key == "pki_authority") { sec = "pki"; pki_nid_done = 0; pki_ca_done = 0; pki_srv_done = 0 }
                else                             { sec = "" }
                print line
                next
            }

            if (sec == "swarm_db") {
                if (line ~ /^[ \t]+node_name:/)      { print "  node_name: \"" node_name "\"";      next }
                if (line ~ /^[ \t]+advertise_addr:/) { print "  advertise_addr: \"" advertise_ip "\""; next }
                if (line ~ /^[ \t]+join_addresses:/) { print "  join_addresses: " join_yaml;        next }
                print line; next
            }

            if (sec == "pki") {
                # networkID: print our value, then immediately the caBundle (join)
                # and servers (bootstrap), so they are guaranteed to land here.
                if (line ~ /^[ \t]+networkID:/) {
                    flush_pki()
                    next
                }
                # Existing caBundle in the template: drop it and its block body;
                # the real one is emitted by flush_pki() at networkID time.
                if (line ~ /^[ \t]+caBundle:/) {
                    if (ca_file != "" && !pki_ca_done) {
                        print "  caBundle: |"
                        while ((getline cl < ca_file) > 0) print "    " cl
                        close(ca_file)
                        pki_ca_done = 1
                    }
                    # swallow the old literal-block body (indent deeper than caBundle:)
                    while ((getline nx) > 0) {
                        if (indent(nx) > ind) continue
                        # not part of the body → re-feed this line through main loop
                        $0 = nx
                        ind = indent($0); line = $0
                        if (ind == 0 && line !~ /^[ \t]*#/ && line ~ /^[A-Za-z0-9_]+:/) {
                            key = line; sub(/:.*/, "", key)
                            if (sec == "pki") flush_pki()
                            if      (key == "swarm_db")      sec = "swarm_db"
                            else if (key == "pki_authority") { sec = "pki"; pki_nid_done = 0; pki_ca_done = 0; pki_srv_done = 0 }
                            else                             sec = ""
                        }
                        print line
                        break
                    }
                    next
                }
                # Existing servers block on bootstrap: replace with [] and skip body.
                if (line ~ /^[ \t]+servers:/ && is_bootstrap == "yes") {
                    if (!pki_srv_done) { print "  servers: []"; pki_srv_done = 1 }
                    while ((getline nx) > 0) {
                        if (indent(nx) > ind) continue
                        $0 = nx
                        ind = indent($0); line = $0
                        if (ind == 0 && line !~ /^[ \t]*#/ && line ~ /^[A-Za-z0-9_]+:/) {
                            key = line; sub(/:.*/, "", key)
                            if (sec == "pki") flush_pki()
                            if      (key == "swarm_db")      sec = "swarm_db"
                            else if (key == "pki_authority") { sec = "pki"; pki_nid_done = 0; pki_ca_done = 0; pki_srv_done = 0 }
                            else                             sec = ""
                        }
                        print line
                        break
                    }
                    next
                }
                print line; next
            }

            print line
        }
        END {
            # File ended while still inside pki and we never hit networkID.
            if (sec == "pki") flush_pki()
        }
        ' "${f}" > "${tmp}" && mv "${tmp}" "${f}"
    done

    [[ -n "${ca_file}" ]] && rm -f "${ca_file}"

    inject_top_level_identity "${dest}" 

    # Verify networkID landed in a real pki_authority section (indent 0 → child indent 2)
    if ! grep -Rqs "^  networkID:.*${network_id}" \
            --include='*.yaml' --include='*.yml' "${dest}"; then
        err "networkID ${network_id} not found at pki_authority level under ${dest}"
        find "${dest}" -type f \( -name '*.yaml' -o -name '*.yml' \) | sed 's/^/  /' >&2
        return 1
    fi

    # Verify caBundle actually got injected for join nodes.
    if [[ "${is_bootstrap}" == "no" && -n "${ca_bundle}" ]]; then
        if ! grep -Rqs "BEGIN CERTIFICATE" \
                --include='*.yaml' --include='*.yml' "${dest}"; then
            err "caBundle (CA cert) not injected into ${dest}"
            return 1
        fi
    fi

    log "provider config ready: ${dest} (ip=${node_ip}, join=${join_yaml}, network_id=${network_id}, ca=$([[ -n ${ca_file} ]] && echo yes || echo no))"
    echo "${dest}"
}

# ============================================================================
# PATCH for swarm-cluster.sh — global_id generation + top-level config injection
# ============================================================================

# --- 2a. Generate or reuse a cluster-wide global_id, persisted on the host so
#         restarts keep the same DNS name (gw.dyn.<global_id>.<base_domain>). ---
ensure_global_id() {
    if [[ -n "${GLOBAL_ID}" ]]; then
        log "Using provided global_id: ${GLOBAL_ID}"
        return
    fi
    local id_file="${CACHE}/global_id"
    if [[ -r "${id_file}" ]]; then
        GLOBAL_ID="$(cat "${id_file}")"
        log "Reusing persisted global_id: ${GLOBAL_ID} (${id_file})"
        return
    fi
    # Short, DNS-safe id: 12 lowercase hex chars.
    if command -v uuidgen &>/dev/null; then
        GLOBAL_ID="$(uuidgen | tr -d '-' | cut -c1-12)"
    else
        GLOBAL_ID="$(od -An -tx1 -N6 /dev/urandom | tr -d ' \n')"
    fi
    echo "${GLOBAL_ID}" > "${id_file}"
    log "Generated global_id: ${GLOBAL_ID} (persisted to ${id_file})"
}

# --- 2b. Inject top-level global_id + gateway_hostname into every provider yaml.
#         These live at indent 0, next to other top-level keys. Idempotent:
#         existing keys are replaced, missing ones appended. Call from inside
#         prepare_config AFTER the awk section-rewrite, BEFORE the verifications. ---
inject_top_level_identity() {
    local dest="$1"

    find "${dest}" -type f \( -name '*.yaml' -o -name '*.yml' \) -print0 | \
    while IFS= read -r -d '' f; do
        local tmp; tmp="$(mktemp)"
        awk \
            -v gid="${GLOBAL_ID}" '
        BEGIN { seen_gid = 0 }
        # Replace existing top-level key in place; gateway_hostname is left as-is from template.
        /^global_id:[ \t]*/  { print "global_id: \"" gid "\""; seen_gid = 1; next }
        { print }
        END {
            if (!seen_gid) print "global_id: \"" gid "\""
        }
        ' "${f}" > "${tmp}" && mv "${tmp}" "${f}"
    done
    log "  injected top-level: global_id=${GLOBAL_ID} (gateway_hostname left unchanged from template)"
}

# ----------------------------------------------------------------------------
# 3. Start a single VM in tmux
# ----------------------------------------------------------------------------
start_vm() {
    local session="$1"
    local node_ip="$2"
    local cid="$3"
    local provider_dir="$4"
    local with_gpu="$5"     # true | false
    local node_cores="$6"   # vCPU count for this node
    local node_mem="$7"     # RAM (GB) for this node
    local node_disk="$8"    # state disk (GB) for this node
    local ssh_port="${9:-}" # host SSH port (debug mode only)
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
        --state_disk_size "${node_disk}"
        --state_disk_path "${CACHE}/state-${node_ip##*.}.qcow2"
        --provider_config_disk_path "${CACHE}/pcfg-${node_ip##*.}.img"
        --cache "${CACHE}"
        --guest-cid "${cid}"
        "${mode_args[@]}"
        "${release_args[@]}"
        "${gpu_args[@]}"
        "${debug_args[@]}"
    )

    log "Starting ${session}: ip=${node_ip} cid=${cid} tap=${tap_iface} gpu=${with_gpu} cores=${node_cores} mem=${node_mem}GB disk=${node_disk}GB debug=${DEBUG_MODE}"

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
# Healthcheck: wait until bootstrap is listening on gossip AND the PKI is
# actually serving the CA certificate. Aborts immediately if the VM dies.
# ----------------------------------------------------------------------------
wait_bootstrap() {
    local timeout="${1:-1000}"
    local waited=0
    local pki_url="https://${BOOTSTRAP_IP}:${PKI_PORT}/api/v1/pki/certs/ca"
    log "Waiting for bootstrap: gossip ${BOOTSTRAP_IP}:${GOSSIP_PORT} + PKI ${pki_url}"
    log "  timeout: ${timeout}s"

    while (( waited < timeout )); do
        # Fail fast: QEMU crashed (vfio bind error, OOM, bad flags, ...)
        if ! vm_alive "${TMUX_BOOTSTRAP}"; then
            echo >&2
            report_vm_death "${TMUX_BOOTSTRAP}" "${BOOTSTRAP_IP}"
            die "Bootstrap VM died while waiting — aborting cluster startup."
        fi

        local gossip_ok=false pki_ok=false
        if nc -z -w2 "${BOOTSTRAP_IP}" "${GOSSIP_PORT}" 2>/dev/null; then gossip_ok=true; fi

        local pki_resp
        pki_resp="$(curl -sk --connect-timeout 2 --max-time 5 "${pki_url}" 2>/dev/null || true)"
        if [[ -n "${pki_resp}" && "${pki_resp}" == *"BEGIN CERTIFICATE"* ]]; then
            pki_ok=true
        fi

        if [[ "${gossip_ok}" == true && "${pki_ok}" == true ]]; then
            echo >&2
            log "Bootstrap is ready (gossip up, PKI serving CA cert)"
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
    die "Bootstrap did not come up within ${timeout}s. Check: tmux attach -t ${TMUX_BOOTSTRAP}"
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
    if [[ "${ca_pem}" != *"BEGIN CERTIFICATE"* ]]; then
        err "Response doesn't look like a PEM certificate:"
        printf '%s\n' "${ca_pem}" >&2
        die "Invalid CA bundle from ${pki_url}"
    fi

    log "CA bundle fetched successfully (${#ca_pem} bytes)"

    # Return it via stdout (caller captures with $())
    echo "${ca_pem}"
}

# ----------------------------------------------------------------------------
# Top-level commands
# ----------------------------------------------------------------------------
cmd_up() {
    require_root
    [[ -n "${PROVIDER_TEMPLATE}" ]] || die "Specify --provider-config-template <dir>"
    [[ -d "${PROVIDER_TEMPLATE}" ]] || die "Template ${PROVIDER_TEMPLATE} not found"
    [[ -x "${START_SCRIPT}" ]] || die "start script not found/executable: ${START_SCRIPT}"

    # base_domain is authoritative in the provider template (config.yaml). Read it
    # so the host ingress (gw.dyn.<gid>.<base_domain>) and the in-cluster DNS agree,
    # instead of relying on the hardcoded default.
    local _tmpl_cfg="${PROVIDER_TEMPLATE}/swarm/config.yaml"
    if [[ -r "${_tmpl_cfg}" ]]; then
        local _bd
        _bd="$(grep -E '^[[:space:]]*base_domain:' "${_tmpl_cfg}" | head -1 \
               | sed -E 's/.*base_domain:[[:space:]]*"?([^"[:space:]]+)"?.*/\1/')"
        [[ -n "${_bd}" ]] && BASE_DOMAIN="${_bd}"
    fi
    log "Using base_domain: ${BASE_DOMAIN}"

    command -v nc &>/dev/null   || die "nc is required (apt install netcat-openbsd)"
    command -v curl &>/dev/null || die "curl is required (apt install curl)"
    command -v tmux &>/dev/null || die "tmux is required (apt install tmux)"
    if ! command -v nft &>/dev/null; then
        log "nftables not found, installing..."
        apt-get update -qq && apt-get install -y -qq nftables
        command -v nft &>/dev/null || die "nftables installation failed"
    fi
    
    mkdir -p "${CACHE}" "${WORKDIR}"

    # Detect host resources and size the bootstrap node dynamically.
    compute_allocation
    ensure_global_id 

    "${NETWORK_SCRIPT}" bridge-only --bridge "${BRIDGE}" \
        ${WAN_IFACE:+--wan-iface "${WAN_IFACE}"}


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

    # Guard against a half-dead previous cluster + stale GPU state
    if [[ "${GPU_TARGET}" == "bootstrap" ]]; then
        reset_vfio_devices
    fi
    
    # --- bootstrap node (no CA bundle, no join addresses) ---
    local boot_dir
    boot_dir="$(prepare_config bootstrap "${BOOTSTRAP_IP}" "swarm-bootstrap" "" "" "${network_id}")"

    local boot_gpu=false
    [[ "${GPU_TARGET}" == "bootstrap" ]] && boot_gpu=true
    start_vm "${TMUX_BOOTSTRAP}" "${BOOTSTRAP_IP}" "${CID_BOOTSTRAP}" "${boot_dir}" \
        "${boot_gpu}" "${BOOTSTRAP_CORES}" "${BOOTSTRAP_MEM}" "${BOOTSTRAP_DISK}" "${SSH_PORT_BOOTSTRAP}"

    wait_bootstrap 1000

    # --- fetch PKI CA bundle from bootstrap ---
    log "Bootstrap is up — fetching CA bundle for join nodes..."
    local ca_bundle
    ca_bundle="$(fetch_ca_bundle)"
    log "CA bundle: $(printf '%s' "${ca_bundle}" | head -1) ... ($(printf '%s' "${ca_bundle}" | wc -l) lines)"

    # --- join nodes (with CA bundle and bootstrap gossip address) ---
    local join1_dir join2_dir
    log "Generating join-node provider configs with CA bundle..."
    join1_dir="$(prepare_config join1 "${JOIN_IPS[0]}" "swarm-join-1" "${BOOTSTRAP_IP}:${GOSSIP_PORT}" "${ca_bundle}" "${network_id}")"
    join2_dir="$(prepare_config join2 "${JOIN_IPS[1]}" "swarm-join-2" "${BOOTSTRAP_IP}:${GOSSIP_PORT}" "${ca_bundle}" "${network_id}")"

    start_vm "${TMUX_JOIN[0]}" "${JOIN_IPS[0]}" "${CID_JOIN[0]}" "${join1_dir}" \
        false "${JOIN_CORES}" "${JOIN_MEM}" "${JOIN_DISK}" "${SSH_PORT_JOIN[0]}"
    start_vm "${TMUX_JOIN[1]}" "${JOIN_IPS[1]}" "${CID_JOIN[1]}" "${join2_dir}" \
        false "${JOIN_CORES}" "${JOIN_MEM}" "${JOIN_DISK}" "${SSH_PORT_JOIN[1]}"

    # external ingress
    "${NETWORK_SCRIPT}" up \
        --global-id "${GLOBAL_ID}" \
        --base-domain "${BASE_DOMAIN}" \
        --gw-backend-port "${GW_BACKEND_PORT}" \
        --bridge "${BRIDGE}" \
        ${WAN_IFACE:+--wan-iface "${WAN_IFACE}"}

    log "Cluster started. Ingress: gw.dyn.${GLOBAL_ID}.${BASE_DOMAIN} -> 80/443"

    log "Cluster started. Sessions: tmux ls"
    log "  bootstrap: tmux attach -t ${TMUX_BOOTSTRAP}"
    log "  join:      tmux attach -t ${TMUX_JOIN[0]} | ${TMUX_JOIN[1]}"

    # Verify join VMs survived startup (they can hit the same vfio error
    # if GPU_TARGET is ever changed, or die on bad config / OOM).
    sleep 10
    local i
    for i in 0 1; do
        if ! vm_alive "${TMUX_JOIN[$i]}"; then
            report_vm_death "${TMUX_JOIN[$i]}" "${JOIN_IPS[$i]}"
            die "Join node ${JOIN_IPS[$i]} died right after start — aborting."
        fi
    done
}

cmd_status() {
    [[ -z "${GLOBAL_ID}" && -r "${CACHE}/global_id" ]] && GLOBAL_ID="$(cat "${CACHE}/global_id")"

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
    
    echo "=== ingress (haproxy) ==="
    "${NETWORK_SCRIPT}" status --global-id "${GLOBAL_ID:-}" --base-domain "${BASE_DOMAIN}" 2>/dev/null \
        | sed 's/^/  /' || echo "  network script unavailable"
}

wait_qemu_gone() {
    local timeout="${1:-90}" waited=0
    log "Waiting for QEMU processes to exit (up to ${timeout}s)..."
    while pgrep -f 'qemu-system-x86_64.*sw-tap-' >/dev/null 2>&1; do
        if (( waited >= timeout )); then
            err "QEMU still alive after ${timeout}s, sending SIGKILL"
            pkill -9 -f 'qemu-system-x86_64.*sw-tap-' 2>/dev/null || true
            timeout=$(( timeout + 120 ))
        fi
        printf '\r[%s]   qemu still running... %ss\033[K' "$(date +%H:%M:%S)" "${waited}" >&2
        sleep 2; waited=$(( waited + 2 ))
        (( waited >= 300 )) && { echo >&2; die "QEMU did not exit in 300s — check dmesg (stuck unpinning?)"; }
    done
    echo >&2
    log "All QEMU processes gone (${waited}s)"
}

wait_vfio_free() {
    local timeout="${1:-120}" waited=0
    compgen -G '/dev/vfio/[0-9]*' >/dev/null || return 0
    log "Waiting for VFIO groups to be released (up to ${timeout}s)..."
    while (( waited < timeout )); do
        local busy=""
        local g
        for g in /dev/vfio/[0-9]*; do
            [[ -e "${g}" ]] || continue
            if fuser -s "${g}" 2>/dev/null; then busy="${g}"; break; fi
        done
        if [[ -z "${busy}" ]]; then
            log "VFIO free (${waited}s)"
            return 0
        fi
        printf '\r[%s]   %s still held... %ss\033[K' "$(date +%H:%M:%S)" "${busy}" "${waited}" >&2
        sleep 2; waited=$(( waited + 2 ))
    done
    echo >&2
    err "VFIO group still busy after ${timeout}s:"
    fuser -v /dev/vfio/* 2>&1 | sed 's/^/    /' >&2 || true
    return 1
}

cmd_down() {
    require_root
    log "Stopping cluster..."

    pkill -TERM -f 'qemu-system-x86_64.*sw-tap-' 2>/dev/null || true

    wait_qemu_gone 90

    wait_vfio_free 120 || err "GPU may still be busy — next 'up' can fail; check 'fuser -v /dev/vfio/*'"

    for s in "${TMUX_BOOTSTRAP}" "${TMUX_JOIN[@]}"; do
        tmux kill-session -t "${s}" 2>/dev/null || true
    done

    for ip in "${BOOTSTRAP_IP}" "${JOIN_IPS[@]}"; do
        local tap="sw-tap-${ip##*.}"
        ip link show "${tap}" &>/dev/null && { log "  del ${tap}"; ip link del "${tap}"; }
    done

    "${NETWORK_SCRIPT}" down --bridge "${BRIDGE}"
    log "Cluster stopped."
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
    "" )             die "Command: up | down | status | ensure-network | setup-dnat" ;;
    *)               die "Unknown command: ${CMD}" ;;
esac
