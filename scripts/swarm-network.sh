#!/bin/bash
#
# swarm-network.sh — host-side network plumbing for a Super Protocol Swarm cluster.
#
# Responsibilities (split out of swarm-cluster.sh):
#   - isolated bridge swarmbr0 (10.0.0.1/24), host = gateway 10.0.0.1
#   - IP forwarding + outbound NAT for VM egress (GHCR / ACME pulls)
#   - external ingress via HAProxy (NOT static DNAT):
#       WAN:80 / WAN:443  --(mode tcp, passthrough)-->  gw.dyn.<global_id>.<base_domain>
#     HAProxy resolves that name at runtime and load-balances across ALL A-records
#     (10.0.0.11/.12/...). When the gateway service migrates between VMs and the
#     cluster updates the DNS record, HAProxy follows automatically — no rule churn,
#     no single fixed target, multiple simultaneous backend IPs supported.
#
# Why HAProxy and not nftables DNAT:
#   - the gateway service can live on SEVERAL VM IPs at once and the set changes
#     over time; DNAT to a single literal (or a named set) can't health-check or
#     balance across a dynamic multi-IP backend. HAProxy server-template does.
#   - the cluster publishes the live set as A-records on gw.dyn.<global_id>.<base_domain>;
#     HAProxy re-resolves on a short interval and adds/removes backends live.
#
# Notes:
#   - DNS is PUBLIC: the record is served by a public authoritative DNS, so HAProxy's
#     resolver points at a public nameserver, not the bootstrap node. Keep the TTL on
#     gw.dyn.* LOW (cluster side) so migrations propagate quickly.
#   - 53/9443 are intentionally NOT exposed here (ingress is 80/443 only).
#   - client source IP is NOT preserved (no PROXY protocol) — by design for this setup.
#
# Usage:
#   sudo ./swarm-network.sh up   --global-id <id> [--base-domain <d>] [opts]
#   sudo ./swarm-network.sh down
#   sudo ./swarm-network.sh status
#   sudo ./swarm-network.sh reload-haproxy   --global-id <id> [--base-domain <d>] [opts]
#   sudo ./swarm-network.sh bridge-only          # just bridge + NAT, no ingress
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Configuration (override via flags)
# ----------------------------------------------------------------------------
BRIDGE="swarmbr0"
SUBNET_CIDR="10.0.0.0/24"
HOST_GW_IP="10.0.0.1"
WAN_IFACE=""                       # empty = auto-detect from ip route

# Ingress target name: gw.dyn.<global_id>.<base_domain>
GLOBAL_ID=""                       # REQUIRED for `up` / `reload-haproxy`
BASE_DOMAIN="superprotocol.io"     # override with --base-domain
GW_BACKEND_PORT=443                # port the gateway service listens on inside the VMs

# WAN ports HAProxy accepts (TLS passthrough on 443, plain TCP on 80)
INGRESS_PORTS=(80 443)

# Public resolver HAProxy uses to follow the gw.dyn.* record.
# (DNS is public; do NOT point this at the bootstrap node.)
RESOLVER_ADDRS=("1.1.1.1:53" "8.8.8.8:53")
DNS_HOLD_VALID="5s"                # how long HAProxy trusts a good resolution
DNS_HOLD_OBSOLETE="5s"             # grace before dropping a vanished record
RESOLVE_RETRIES=3
MAX_BACKENDS=16                    # server-template slot count (max simultaneous IPs)

HAPROXY_CFG="/etc/haproxy/swarm-ingress.cfg"
HAPROXY_PIDFILE="/run/swarm-haproxy.pid"
HAPROXY_BIN="$(command -v haproxy || true)"

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
log()  { echo -e "[$(date +%H:%M:%S)] $*" >&2; }
err()  { echo -e "[$(date +%H:%M:%S)] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

require_root() { [[ "$EUID" -eq 0 ]] || die "Must be run as root (use sudo)."; }

ensure_haproxy() {
    if [[ -z "${HAPROXY_BIN}" ]]; then
        log "haproxy not found, installing..."
        apt-get update -qq && apt-get install -y -qq haproxy
        HAPROXY_BIN="$(command -v haproxy)"
        [[ -n "${HAPROXY_BIN}" ]] || die "haproxy installation failed"
    fi
}

ensure_nft() {
    if ! command -v nft &>/dev/null; then
        log "nftables not found, installing..."
        apt-get update -qq && apt-get install -y -qq nftables
        command -v nft &>/dev/null || die "nftables installation failed"
    fi
}

detect_wan_iface() {
    if [[ -n "${WAN_IFACE}" ]]; then echo "${WAN_IFACE}"; return; fi
    ip route get 8.8.8.8 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1
}

gw_hostname() {
    [[ -n "${GLOBAL_ID}" ]] || die "global_id is empty (pass --global-id)."
    echo "gw.dyn.${GLOBAL_ID}.${BASE_DOMAIN}"
}

# ----------------------------------------------------------------------------
# Bridge + forwarding + outbound NAT
# ----------------------------------------------------------------------------
ensure_bridge_nat() {
    require_root
    local wan; wan="$(detect_wan_iface)"
    [[ -n "${wan}" ]] || die "Could not determine WAN interface. Pass --wan-iface."
    log "WAN interface: ${wan}"

    if ! ip link show "${BRIDGE}" &>/dev/null; then
        log "Creating bridge ${BRIDGE} (${HOST_GW_IP}/24)"
        ip link add name "${BRIDGE}" type bridge
        ip addr add "${HOST_GW_IP}/24" dev "${BRIDGE}"
        ip link set "${BRIDGE}" up
    else
        log "bridge ${BRIDGE} already exists"
        ip addr show dev "${BRIDGE}" | grep -q "${HOST_GW_IP}/24" || \
            ip addr add "${HOST_GW_IP}/24" dev "${BRIDGE}"
        ip link set "${BRIDGE}" up
    fi

    sysctl -q -w net.ipv4.ip_forward=1

    # Outbound NAT for VM egress (idempotent: drop and recreate the table)
    nft list table ip swarm_nat &>/dev/null && nft delete table ip swarm_nat
    nft add table ip swarm_nat
    nft add chain ip swarm_nat postrouting '{ type nat hook postrouting priority 100 ; }'
    nft add rule  ip swarm_nat postrouting ip saddr "${SUBNET_CIDR}" oifname "${wan}" masquerade

    log "Bridge + NAT ready: ${BRIDGE}, egress via ${wan}"
}

# ----------------------------------------------------------------------------
# HAProxy ingress (dynamic, DNS-following, multi-backend)
# ----------------------------------------------------------------------------
write_haproxy_cfg() {
    local gw; gw="$(gw_hostname)"
    log "Generating HAProxy config: ingress 80/443 -> ${gw}:${GW_BACKEND_PORT}"

    mkdir -p "$(dirname "${HAPROXY_CFG}")"

    # Build the resolvers nameserver lines
    local ns_lines="" i=1
    local addr
    for addr in "${RESOLVER_ADDRS[@]}"; do
        ns_lines+="    nameserver dns${i} ${addr}"$'\n'
        i=$(( i + 1 ))
    done

    cat > "${HAPROXY_CFG}" <<EOF
# Generated by swarm-network.sh — do not edit by hand.
# Ingress for Swarm gateway: WAN 80/443 -> ${gw} (A-records 10.0.0.x), TLS passthrough.
global
    log /dev/log local0
    maxconn 20000
    pidfile ${HAPROXY_PIDFILE}

defaults
    log     global
    mode    tcp
    option  tcplog
    timeout connect 5s
    timeout client  50s
    timeout server  50s
    retries 3

# Runtime DNS resolution against the PUBLIC authoritative DNS.
# Keep gw.dyn.* TTL low on the cluster side so migrations propagate fast.
resolvers swarm_dns
${ns_lines}    resolve_retries ${RESOLVE_RETRIES}
    timeout resolve 1s
    timeout retry   1s
    hold valid    ${DNS_HOLD_VALID}
    hold obsolete ${DNS_HOLD_OBSOLETE}
    hold nx       ${DNS_HOLD_OBSOLETE}
    hold timeout  ${DNS_HOLD_OBSOLETE}
    accepted_payload_size 8192

EOF

    # One frontend+backend pair per ingress port. server-template expands the
    # single hostname into up to MAX_BACKENDS slots from its A-records; HAProxy
    # adds/removes/health-checks them live as the DNS set changes.
    local p
    for p in "${INGRESS_PORTS[@]}"; do
        cat >> "${HAPROXY_CFG}" <<EOF
frontend ingress_${p}
    bind *:${p}
    default_backend gw_${p}

backend gw_${p}
    balance roundrobin
    server-template gw ${MAX_BACKENDS} ${gw}:${GW_BACKEND_PORT} resolvers swarm_dns resolve-prefer ipv4 check

EOF
    done

    log "HAProxy config written: ${HAPROXY_CFG}"
}

# ----------------------------------------------------------------------------
# Wait for HAProxy DNS resolution + backend health, single-line status (like bootstrap wait).
# ----------------------------------------------------------------------------
wait_haproxy_resolve() {
    local timeout="${1:-60}"
    local waited=0
    local gw; gw="$(gw_hostname)"
    log "Waiting for HAProxy to resolve ${gw} (timeout ${timeout}s)..."

    while (( waited < timeout )); do
        local resolved_ips
        resolved_ips="$(getent ahostsv4 "${gw}" 2>/dev/null | awk '{print $1}' | sort -u | xargs echo 2>/dev/null || echo 'none')"
        local ip_count; ip_count="$(echo "${resolved_ips}" | sed 's/none//' | wc -w)"

        local backends_up=0
        if [[ "${resolved_ips}" != "none" ]]; then
            local ip
            for ip in ${resolved_ips}; do
                if nc -z -w2 "${ip}" "${GW_BACKEND_PORT}" 2>/dev/null; then
                    backends_up=$(( backends_up + 1 ))
                fi
            done
        fi

        local resolve_status="down"
        [[ "${resolved_ips}" != "none" ]] && resolve_status="ok"

        printf '\r[%s]   [%ss] dns=%s  backends=%s/%s  ips=%s\033[K' \
            "$(date +%H:%M:%S)" "${waited}" \
            "${resolve_status}" "${backends_up}" "${ip_count}" \
            "${resolved_ips}" >&2

        if (( backends_up > 0 )); then
            echo >&2
            log "HAProxy ingress ready: ${backends_up}/${ip_count} backends up (${gw})"
            return 0
        fi

        sleep 3; waited=$(( waited + 3 ))
    done
    echo >&2
    log "HAProxy: DNS resolved but no backends reachable on ${GW_BACKEND_PORT} (continuing anyway)."
    return 0
}

start_haproxy() {
    ensure_haproxy
    write_haproxy_cfg

    # validate before (re)starting
    "${HAPROXY_BIN}" -c -f "${HAPROXY_CFG}" >/dev/null \
        || die "HAProxy config check failed: ${HAPROXY_CFG}"

    if [[ -f "${HAPROXY_PIDFILE}" ]] && kill -0 "$(cat "${HAPROXY_PIDFILE}")" 2>/dev/null; then
        # hitless reload: hand sockets to the new process
        log "Reloading HAProxy (hitless, sf $(cat "${HAPROXY_PIDFILE}"))"
        "${HAPROXY_BIN}" -f "${HAPROXY_CFG}" -p "${HAPROXY_PIDFILE}" \
            -sf "$(cat "${HAPROXY_PIDFILE}")"
    else
        log "Starting HAProxy"
        "${HAPROXY_BIN}" -f "${HAPROXY_CFG}" -p "${HAPROXY_PIDFILE}" -D
    fi
    log "HAProxy ingress up on ports: ${INGRESS_PORTS[*]}"
}

stop_haproxy() {
    if [[ -f "${HAPROXY_PIDFILE}" ]] && kill -0 "$(cat "${HAPROXY_PIDFILE}")" 2>/dev/null; then
        log "Stopping HAProxy ($(cat "${HAPROXY_PIDFILE}"))"
        kill "$(cat "${HAPROXY_PIDFILE}")" 2>/dev/null || true
        rm -f "${HAPROXY_PIDFILE}"
    else
        log "HAProxy not running"
    fi
}

# ----------------------------------------------------------------------------
# Top-level commands
# ----------------------------------------------------------------------------
cmd_up() {
    require_root
    ensure_nft
    ensure_bridge_nat
    start_haproxy
    wait_haproxy_resolve 120
    log "Network ready. Ingress 80/443 -> $(gw_hostname) (dynamic, multi-backend)."
}

cmd_bridge_only() {
    require_root
    ensure_nft
    ensure_bridge_nat
    log "Bridge + NAT ready (no ingress)."
}

cmd_reload_haproxy() {
    require_root
    start_haproxy
}

cmd_down() {
    require_root
    log "Tearing down host network..."
    stop_haproxy
    nft list table ip swarm_nat &>/dev/null && { log "  del nft swarm_nat"; nft delete table ip swarm_nat; }
    # legacy table from the old DNAT design, if present
    nft list table ip swarm_dnat &>/dev/null && { log "  del nft swarm_dnat (legacy)"; nft delete table ip swarm_dnat; }
    log "Bridge ${BRIDGE} left in place (remove with: ip link del ${BRIDGE})."
}

cmd_status() {
    echo "=== bridge ==="
    ip -br addr show "${BRIDGE}" 2>/dev/null || echo "  no bridge ${BRIDGE}"
    echo "=== nat ==="
    nft list table ip swarm_nat &>/dev/null && echo "  swarm_nat: present" || echo "  swarm_nat: absent"
    echo "=== haproxy ==="
    if [[ -f "${HAPROXY_PIDFILE}" ]] && kill -0 "$(cat "${HAPROXY_PIDFILE}")" 2>/dev/null; then
        echo "  running (pid $(cat "${HAPROXY_PIDFILE}")), cfg ${HAPROXY_CFG}"
        if [[ -n "${GLOBAL_ID}" ]]; then
            echo "  ingress target: $(gw_hostname):${GW_BACKEND_PORT}"
            echo "  current A-records:"
            getent ahostsv4 "$(gw_hostname)" 2>/dev/null | awk '{print "    "$1}' | sort -u || echo "    (resolve failed)"
        fi
    else
        echo "  not running"
    fi
}

# ----------------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------------
CMD="${1:-}"; shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --global-id)        GLOBAL_ID="$2"; shift 2 ;;
        --base-domain)      BASE_DOMAIN="$2"; shift 2 ;;
        --gw-backend-port)  GW_BACKEND_PORT="$2"; shift 2 ;;
        --bridge)           BRIDGE="$2"; shift 2 ;;
        --wan-iface)        WAN_IFACE="$2"; shift 2 ;;
        --resolver)         RESOLVER_ADDRS=("$2"); shift 2 ;;   # single override
        --max-backends)     MAX_BACKENDS="$2"; shift 2 ;;
        --haproxy-cfg)      HAPROXY_CFG="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

case "${CMD}" in
    up)              cmd_up ;;
    down)            cmd_down ;;
    status)          cmd_status ;;
    bridge-only)     cmd_bridge_only ;;
    reload-haproxy)  cmd_reload_haproxy ;;
    "" )             die "Command: up | down | status | bridge-only | reload-haproxy" ;;
    *)               die "Unknown command: ${CMD}" ;;
esac