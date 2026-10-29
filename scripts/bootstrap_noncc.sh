#!/bin/bash
# Explicit KVM-only preparation. Does not configure confidential computing.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "${script_dir}/common.sh"
source "${script_dir}/setup_libvirt_host.sh"

if [[ "${1:-}" == "--help" ]]; then
    echo 'Usage: sudo bash scripts/bootstrap_noncc.sh'
    echo 'Prepare an Ubuntu 24.04/26.04 x86-64 host for untrusted debug KVM guests.'
    echo 'Not needed on service-only OpenStack hosts. No CC configuration is changed.'
    exit 0
fi
[[ $# == 0 ]] || { echo 'ERROR: unexpected arguments' >&2; exit 1; }
[[ $(id -u) == 0 ]] || { echo 'ERROR: run bootstrap as root' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'ERROR: x86-64 is required' >&2; exit 1; }
get_supported_ubuntu_version
grep -Eq '(^|[[:space:]])(vmx|svm)([[:space:]]|$)' /proc/cpuinfo || {
    echo 'ERROR: hardware virtualization is unavailable; enable it in firmware or expose nested virtualization.' >&2
    exit 1
}
modprobe kvm
if grep -qw vmx /proc/cpuinfo; then modprobe kvm_intel; else modprobe kvm_amd; fi
[[ -c /dev/kvm ]] || { echo 'ERROR: /dev/kvm is unavailable. No TCG fallback is permitted.' >&2; exit 1; }
modprobe vhost_vsock
modprobe tun
[[ -c /dev/vhost-vsock && -c /dev/net/tun ]] || {
    echo 'ERROR: SPVM requires virtio-vsock and TUN devices; install matching host kernel modules before retrying.' >&2
    exit 1
}

# Reject occupied hosts before APT can upgrade the distro QEMU too, not only
# before installing the project package. Include guests outside libvirt.
assert_no_running_libvirt_domains
if pgrep -f '^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$)' >/dev/null; then
    echo 'ERROR: active hypervisor process; bootstrap requires an empty host and never stops guests.' >&2
    exit 1
else
    probe_status=$?
    [[ "${probe_status}" == 1 ]] || { echo 'ERROR: cannot verify host emptiness.' >&2; exit 1; }
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates wget ovmf \
    qemu-system-x86 qemu-utils iproute2 iptables dnsmasq-base python3 \
    curl jq zstd unzip netcat-openbsd pciutils util-linux e2fsprogs
bootstrap_work=$(mktemp -d /tmp/spvm-noncc.XXXXXXXX)
trap 'rm -rf -- "${bootstrap_work}"' EXIT
if ! command -v uplink >/dev/null 2>&1; then
    # Avoid the launcher's floating latest installer at first guest launch.
    download_pinned_package "${bootstrap_work}/uplink.zip" \
        'https://github.com/storj/storj/releases/download/v1.165.6/uplink_linux_amd64.zip' \
        '6998d64c7671393898b6be839911b1e7b22d4e1b0a0646631c57a62f9b0ee966'
    unzip -q "${bootstrap_work}/uplink.zip" -d "${bootstrap_work}/uplink"
    install -m 0755 "${bootstrap_work}/uplink/uplink" /usr/local/bin/uplink
fi
if [[ "${UBUNTU_VERSION}" == 24.04 ]]; then
    # Reuse the checksum-pinned project QEMU, without installing its CC kernel.
    assert_no_running_libvirt_domains
    install_noble_coco_qemu "${bootstrap_work}"
fi
setup_libvirt_host untrusted
if [[ -e /var/run/reboot-required ]]; then
    echo 'Host packages request a reboot. Reboot manually, then rerun bootstrap and verify KVM before deploying compute.' >&2
    exit 2
fi
echo 'Non-CC KVM bootstrap complete. This host is NOT CC-ready.'
echo 'OpenStack debug-only compute additionally requires explicit untrusted mode and debug opt-in.'
