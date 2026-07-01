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
#   - DNS is INTERNAL: the gw.dyn.<global_id>.* record is served authoritatively by
#     the bootstrap node on the bridge (10.0.0.10:53). HAProxy's resolver points at
#     the bootstrap, NOT a public nameserver. Keep the TTL on gw.dyn.* LOW (cluster
#     side) so gateway migrations propagate quickly.
#
# Usage:
#   sudo ./swarm-network.sh up   --global-id <id> [--base-domain <d>] [opts]
#   sudo ./swarm-network.sh down
#   sudo ./swarm-network.sh status
#   sudo ./swarm-network.sh reload-haproxy   --global-id <id> [--base-domain <d>] [opts]
#   sudo ./swarm-network.sh bridge-only          # just bridge + NAT, no ingress
#
# Missing dependencies (haproxy, nftables, dnsutils, netcat) are installed
# automatically via apt-get when running as root. Pass --no-auto-install to
# restore the old behaviour of failing with an "apt install <pkg>" message.
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

# Bootstrap node serves the authoritative dyn.<global_id>.* zone on the bridge.
# HAProxy must resolve gw.dyn.* HERE, not at a public resolver, because the
# record is internal to the cluster (10.0.0.x) and only the bootstrap knows the
# live set. Bind to the bridge IP, reachable host->bootstrap without DNAT.
BOOTSTRAP_DNS_IP="10.0.0.10"
DNS_PORT=53

# Resolver HAProxy uses to follow the gw.dyn.* record — the bootstrap node.
RESOLVER_ADDRS=("${BOOTSTRAP_DNS_IP}:${DNS_PORT}")
DNS_HOLD_VALID="5s"                # how long HAProxy trusts a good resolution
DNS_HOLD_OBSOLETE="5s"             # grace before dropping a vanished record
RESOLVE_RETRIES=3
MAX_BACKENDS=16                    # server-template slot count (max simultaneous IPs)

HAPROXY_CFG="/etc/haproxy/swarm-ingress.cfg"
HAPROXY_PIDFILE="/run/swarm-haproxy.pid"
HAPROXY_BIN="$(command -v haproxy || true)"

# Set to 0 via --no-auto-install to restore old behaviour of dying instead of
# installing missing packages.
AUTO_INSTALL=1

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
log()  { echo -e "[$(date +%H:%M:%S)] $*" >&2; }
err()  { echo -e "[$(date +%H:%M:%S)] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

require_root() { [[ "$EUID" -eq 0 ]] || die "Must be run as root (use sudo)."; }

detect_wan_iface() {
    if [[ -n "${WAN_IFACE}" ]]; then echo "${WAN_IFACE}"; return; fi
    ip route get 8.8.8.8 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -1
}

gw_hostname() {
    [[ -n "${GLOBAL_ID}" ]] || die "global_id is empty (pass --global-id)."
    echo "gw.dyn.${GLOBAL_ID}.${BASE_DOMAIN}"
}

# ----------------------------------------------------------------------------
# Package auto-install
# ----------------------------------------------------------------------------
# apt package name that provides a given command, when it differs from the
# command name itself (e.g. `dig`/`nslookup` come from dnsutils, `nc` from
# netcat-openbsd).
_pkg_for_cmd() {
    case "$1" in
        haproxy)   echo "haproxy" ;;
        nft)       echo "nftables" ;;
        dig)       echo "dnsutils" ;;
        nslookup)  echo "dnsutils" ;;
        nc)        echo "netcat-openbsd" ;;
        *)         echo "$1" ;;
    esac
}

APT_UPDATED=0

# ensure_cmd <command> [friendly-label]
# Makes sure <command> is on PATH. If missing and AUTO_INSTALL=1 and apt-get
# is available, installs the corresponding package via apt-get. Otherwise
# dies with the old, explicit "apt install <pkg>" message, so behaviour on
# non-Debian hosts (or with --no-auto-install) is unchanged.
ensure_cmd() {
    local cmd="$1" label="${2:-$1}" pkg
    command -v "${cmd}" &>/dev/null && return 0

    pkg="$(_pkg_for_cmd "${cmd}")"

    if [[ "${AUTO_INSTALL}" -ne 1 ]]; then
        die "${label} is required (apt install ${pkg})."
    fi

    if ! command -v apt-get &>/dev/null; then
        die "${label} is required (apt install ${pkg}), but apt-get is not available on this host — install it manually."
    fi

    require_root
    log "${label} not found — installing package '${pkg}' via apt-get..."

    if [[ "${APT_UPDATED}" -ne 1 ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get update -y \
            || die "apt-get update failed while trying to install ${pkg}."
        APT_UPDATED=1
    fi

    DEBIAN_FRONTEND=noninteractive apt-get install -y "${pkg}" \
        || die "apt-get install ${pkg} failed. Install it manually and re-run."

    command -v "${cmd}" &>/dev/null \
        || die "${pkg} installed but '${cmd}' still not on PATH — check the package contents."

    log "${label} installed OK ($(command -v "${cmd}"))."
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
    log "Generating HAProxy config: ingress 80/443 -> ${gw}:${GW_BACKEND_PORT} (via bootstrap DNS ${BOOTSTRAP_DNS_IP}:${DNS_PORT})"
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

# Runtime DNS resolution against the bootstrap node's authoritative DNS
# (serves the internal dyn.<global_id>.* zone on the bridge). Keep gw.dyn.*
# TTL low on the cluster side so migrations propagate fast.
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
    #
    # The gateway (openresty) listens on BOTH 80 and 443: 443 is the TLS
    # passthrough, 80 serves the ACME HTTP-01 challenge (and the HTTP->HTTPS
    # redirect). So each WAN port maps to the SAME backend port — sending WAN:80
    # to a :443 backend breaks HTTP-01 and certificates never issue.
    local p
    for p in "${INGRESS_PORTS[@]}"; do
        cat >> "${HAPROXY_CFG}" <<EOF
frontend ingress_${p}
    bind *:${p}
    default_backend gw_${p}

backend gw_${p}
    balance roundrobin
    server-template gw ${MAX_BACKENDS} ${gw}:${p} resolvers swarm_dns resolve-prefer ipv4 init-addr none check

EOF
    done

    log "HAProxy config written: ${HAPROXY_CFG}"
}

start_haproxy() {
    ensure_cmd nc "netcat"

    # Sanity: can we reach the bootstrap DNS at all? (init-addr none means HAProxy
    # starts even if not — so check explicitly to avoid a silent no-backends state.)
    if ! nc -z -u -w2 "${BOOTSTRAP_DNS_IP}" "${DNS_PORT}" 2>/dev/null; then
        log "WARN: bootstrap DNS ${BOOTSTRAP_DNS_IP}:${DNS_PORT} not reachable yet — HAProxy will start with no backends until it answers."
    fi

    ensure_cmd haproxy "haproxy"
    HAPROXY_BIN="$(command -v haproxy)"
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
    ensure_cmd nft "nftables"
    ensure_bridge_nat
    start_haproxy
    log "Network ready. Ingress 80/443 -> $(gw_hostname) (dynamic, multi-backend)."
}

cmd_bridge_only() {
    require_root
    ensure_cmd nft "nftables"
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
            echo "  current A-records (via bootstrap ${BOOTSTRAP_DNS_IP}):"
            if command -v dig &>/dev/null; then
                dig +short @"${BOOTSTRAP_DNS_IP}" "$(gw_hostname)" A 2>/dev/null \
                    | sed 's/^/    /' | grep . || echo "    (resolve failed)"
            else
                nslookup "$(gw_hostname)" "${BOOTSTRAP_DNS_IP}" 2>/dev/null \
                    | awk '/^Address: /{print "    "$2}' | grep . || echo "    (resolve failed / install dnsutils)"
            fi
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
        --bootstrap-dns)    BOOTSTRAP_DNS_IP="$2"; RESOLVER_ADDRS=("${BOOTSTRAP_DNS_IP}:${DNS_PORT}"); shift 2 ;;
        --resolver)         RESOLVER_ADDRS=("$2"); shift 2 ;;   # single override
        --max-backends)     MAX_BACKENDS="$2"; shift 2 ;;
        --haproxy-cfg)      HAPROXY_CFG="$2"; shift 2 ;;
        --no-auto-install)  AUTO_INSTALL=0; shift ;;
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