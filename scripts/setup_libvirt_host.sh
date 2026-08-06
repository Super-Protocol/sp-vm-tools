#!/bin/bash

# Shared libvirt host preparation for bootstrap_tdx.sh and bootstrap_snp.sh.
# This file is sourceable for unit tests and can also be executed directly.

LIBVIRT_REQUIRED_VERSION="12.5.0"
LIBVIRT_RELEASE_REPO="Super-Protocol/sp-vm-tools"
LIBVIRT_URI="qemu:///system"

LIBVIRT_BASE_PACKAGES=(
    libvirt0
    libvirt-common
    libvirt-clients
    libvirt-daemon
    libvirt-daemon-common
    libvirt-daemon-config-network
    libvirt-daemon-config-nwfilter
    libvirt-daemon-driver-network
    libvirt-daemon-driver-nodedev
    libvirt-daemon-driver-nwfilter
    libvirt-daemon-driver-qemu
    libvirt-daemon-driver-secret
    libvirt-daemon-driver-storage
    libvirt-daemon-log
    libvirt-daemon-lock
    libvirt-daemon-plugin-lockd
    libvirt-daemon-system
)

LIBVIRT_PACKAGE_PATHS=()
PASST_REAL_BINARIES=()

libvirt_host_error() {
    echo "ERROR: $*" >&2
    return 1
}

libvirt_host_path() {
    printf '%s%s\n' "${SPVM_TEST_ROOT:-}" "$1"
}

select_libvirt_release() {
    local ubuntu_version=$1
    case "${ubuntu_version}" in
        24.04)
            LIBVIRT_RELEASE_TAG="44-libvirt-ubuntu24"
            LIBVIRT_RELEASE_ASSET="libvirt-ubuntu24.tar.gz"
            LIBVIRT_RELEASE_SHA256="17a1aa837260e3e584b2ebcdd066ff541d2d89d1b798dc71c626976ff70ae452"
            ;;
        26.04)
            LIBVIRT_RELEASE_TAG="43-libvirt-ubuntu26"
            LIBVIRT_RELEASE_ASSET="libvirt-ubuntu26.tar.gz"
            LIBVIRT_RELEASE_SHA256="fd1ba716a9ee722c5fc0d3db72b92ccba3c57954c2464759ff4dc3f70a8bf6ad"
            ;;
        *)
            libvirt_host_error "libvirt bootstrap supports Ubuntu 24.04 and 26.04 only (found ${ubuntu_version:-unknown})"
            return 1
            ;;
    esac
    LIBVIRT_RELEASE_URL="https://github.com/${LIBVIRT_RELEASE_REPO}/releases/download/${LIBVIRT_RELEASE_TAG}/${LIBVIRT_RELEASE_ASSET}"
}

get_supported_ubuntu_version() {
    local os_release
    os_release=$(libvirt_host_path /etc/os-release)
    if [[ ! -r "${os_release}" ]]; then
        libvirt_host_error "cannot read ${os_release}"
        return 1
    fi

    local ID="" VERSION_ID=""
    # shellcheck disable=SC1090
    source "${os_release}"
    if [[ "${ID}" != "ubuntu" ]]; then
        libvirt_host_error "libvirt bootstrap requires Ubuntu (found ${ID:-unknown})"
        return 1
    fi
    select_libvirt_release "${VERSION_ID}"
    # Exported result for bootstrap callers.
    # shellcheck disable=SC2034
    UBUNTU_VERSION="${VERSION_ID}"
}

installed_libvirt_version() {
    dpkg-query -W -f='${Version}\n' libvirt-daemon 2>/dev/null || true
}

libvirt_upgrade_required() {
    local installed_version=${1:-}
    [[ -z "${installed_version}" ]] || \
        ! dpkg --compare-versions "${installed_version}" ge "${LIBVIRT_REQUIRED_VERSION}"
}

validate_tar_listing() {
    local entry trimmed component
    local -a components
    while IFS= read -r entry; do
        trimmed=${entry#./}
        if [[ "${entry}" == /* || -z "${trimmed}" ]]; then
            libvirt_host_error "unsafe archive entry: ${entry}"
            return 1
        fi
        IFS='/' read -r -a components <<< "${trimmed}"
        for component in "${components[@]}"; do
            if [[ "${component}" == ".." ]]; then
                libvirt_host_error "unsafe archive entry: ${entry}"
                return 1
            fi
        done
    done
}

verify_and_extract_libvirt_archive() {
    local archive=$1 destination=$2 actual_sha package_dir sums_file
    actual_sha=$(sha256sum "${archive}" | awk '{print $1}')
    if [[ "${actual_sha}" != "${LIBVIRT_RELEASE_SHA256}" ]]; then
        libvirt_host_error "checksum mismatch for ${archive}: expected ${LIBVIRT_RELEASE_SHA256}, got ${actual_sha}"
        return 1
    fi
    if ! tar -tzf "${archive}" | validate_tar_listing; then
        return 1
    fi
    tar -xzf "${archive}" -C "${destination}"
    sums_file=$(find "${destination}" -mindepth 2 -maxdepth 3 -type f -name SHA256SUMS -print -quit)
    if [[ -z "${sums_file}" ]]; then
        libvirt_host_error "archive does not contain SHA256SUMS"
        return 1
    fi
    package_dir=$(dirname "${sums_file}")
    if ! (cd "${package_dir}" && sha256sum -c SHA256SUMS); then
        libvirt_host_error "one or more files in the libvirt archive failed checksum validation"
        return 1
    fi
    LIBVIRT_PACKAGE_DIR="${package_dir}"
}

prepare_libvirt_package_compatibility() {
    local package_dir=$1 ubuntu_version=$2 deb unpacked sysusers_file rebuilt package version
    [[ "${ubuntu_version}" == "24.04" ]] || return 0
    deb=$(find_package_deb "${package_dir}" libvirt-daemon-driver-qemu) || {
        libvirt_host_error "release archive is missing libvirt-daemon-driver-qemu"
        return 1
    }
    unpacked=$(mktemp -d)
    dpkg-deb --raw-extract "${deb}" "${unpacked}"
    sysusers_file="${unpacked}/usr/lib/sysusers.d/libvirt-qemu.conf"
    if [[ ! -r "${sysusers_file}" ]]; then
        rm -rf "${unpacked}"
        libvirt_host_error "libvirt QEMU package does not contain its sysusers configuration"
        return 1
    fi
    if ! grep -qE '^u![[:space:]]' "${sysusers_file}"; then
        rm -rf "${unpacked}"
        return 0
    fi

    echo "Adapting libvirt-qemu sysusers syntax for Ubuntu 24.04 systemd 255"
    sed -i -E 's/^u!([[:space:]])/u\1/' "${sysusers_file}"
    if [[ -f "${unpacked}/DEBIAN/md5sums" ]]; then
        local updated_md5
        updated_md5=$(cd "${unpacked}" && md5sum usr/lib/sysusers.d/libvirt-qemu.conf)
        sed -i '\|  usr/lib/sysusers.d/libvirt-qemu.conf$|d' "${unpacked}/DEBIAN/md5sums"
        printf '%s\n' "${updated_md5}" >> "${unpacked}/DEBIAN/md5sums"
    fi
    rebuilt="${deb}.spvm-rebuilt"
    dpkg-deb --build --root-owner-group "${unpacked}" "${rebuilt}" >/dev/null
    package=$(dpkg-deb -f "${rebuilt}" Package)
    version=$(dpkg-deb -f "${rebuilt}" Version)
    if [[ "${package}" != "libvirt-daemon-driver-qemu" || "${version}" != 12.5.0-* ]]; then
        rm -rf "${unpacked}" "${rebuilt}"
        libvirt_host_error "rebuilt compatibility package has unexpected metadata"
        return 1
    fi
    mv "${rebuilt}" "${deb}"
    rm -rf "${unpacked}"
}

find_package_deb() {
    local package_dir=$1 package=$2 candidate actual_package
    while IFS= read -r -d '' candidate; do
        actual_package=$(dpkg-deb -f "${candidate}" Package 2>/dev/null || true)
        if [[ "${actual_package}" == "${package}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done < <(find "${package_dir}" -maxdepth 1 -type f -name '*.deb' -print0)
    return 1
}

build_libvirt_package_plan() {
    local package_dir=$1 package deb
    local -A selected=()
    LIBVIRT_PACKAGE_PATHS=()

    for package in "${LIBVIRT_BASE_PACKAGES[@]}"; do
        if ! deb=$(find_package_deb "${package_dir}" "${package}"); then
            libvirt_host_error "release archive is missing required package ${package}"
            return 1
        fi
        selected["${package}"]="${deb}"
    done

    while IFS= read -r -d '' deb; do
        package=$(dpkg-deb -f "${deb}" Package 2>/dev/null || true)
        [[ -n "${package}" ]] || continue
        if dpkg-query -W -f='${db:Status-Status}' "${package}" 2>/dev/null | grep -qx installed; then
            selected["${package}"]="${deb}"
        fi
    done < <(find "${package_dir}" -maxdepth 1 -type f -name '*.deb' -print0)

    while IFS= read -r package; do
        LIBVIRT_PACKAGE_PATHS+=("${selected[${package}]}")
    done < <(printf '%s\n' "${!selected[@]}" | sort)
}

assert_no_running_libvirt_domains() {
    command -v virsh >/dev/null 2>&1 || return 0
    local running
    running=$(virsh -c "${LIBVIRT_URI}" list --name 2>/dev/null | sed '/^[[:space:]]*$/d' || true)
    if [[ -n "${running}" ]]; then
        libvirt_host_error "refusing to upgrade libvirt while domains are running: ${running//$'\n'/, }"
        return 1
    fi
}

assert_safe_apt_simulation() {
    local simulation=$1
    if grep -qE '^Remv[[:space:]]' <<< "${simulation}"; then
        libvirt_host_error "APT would remove packages; refusing the libvirt transaction"
        return 1
    fi
    if grep -qiE 'DOWNGRADED|downgraded' <<< "${simulation}"; then
        libvirt_host_error "APT would downgrade packages; refusing the libvirt transaction"
        return 1
    fi
}

install_project_libvirt() {
    local work_dir archive simulation
    assert_no_running_libvirt_domains
    work_dir=$(mktemp -d /var/tmp/sp-vm-libvirt.XXXXXX)
    chmod 0755 "${work_dir}"
    archive="${work_dir}/${LIBVIRT_RELEASE_ASSET}"

    echo "Downloading libvirt ${LIBVIRT_REQUIRED_VERSION} from ${LIBVIRT_RELEASE_URL}"
    wget --https-only --tries=3 -O "${archive}" "${LIBVIRT_RELEASE_URL}"
    chmod 0644 "${archive}"
    verify_and_extract_libvirt_archive "${archive}" "${work_dir}"
    prepare_libvirt_package_compatibility "${LIBVIRT_PACKAGE_DIR}" "${UBUNTU_VERSION}"
    find "${work_dir}" -type d -exec chmod a+rx {} +
    find "${work_dir}" -type f \( -name '*.deb' -o -name '*.ddeb' \) -exec chmod a+r {} +
    build_libvirt_package_plan "${LIBVIRT_PACKAGE_DIR}"

    echo "APT simulation for the libvirt upgrade:"
    simulation=$(apt-get --simulate --no-install-recommends --no-remove install "${LIBVIRT_PACKAGE_PATHS[@]}")
    printf '%s\n' "${simulation}"
    assert_safe_apt_simulation "${simulation}"

    DEBIAN_FRONTEND=noninteractive apt-get \
        --no-install-recommends \
        --no-remove \
        -o Dpkg::Options::=--force-confold \
        install -y "${LIBVIRT_PACKAGE_PATHS[@]}"
    rm -rf "${work_dir}"
}

passthrough_profile_state() {
    local profile=$1
    awk '
        /^[[:space:]]*profile passt[[:space:]]*\{/ { in_passt = 1; found = 1; next }
        in_passt && /^[[:space:]]*}/ { in_passt = 0; done = 1 }
        in_passt && /\/usr\/bin\/passt[[:space:]]+r,/ { readonly = 1 }
        in_passt && /\/usr\/bin\/passt[[:space:]]+rm,/ { mmap = 1 }
        in_passt && /^[[:space:]]*capability[[:space:]]+net_bind_service,/ { capability = 1 }
        END {
            if (!found || !done) print "unknown"
            else if (readonly) print "readonly"
            else if (mmap && capability) print "ready"
            else if (mmap) print "needs-capability"
            else print "unknown"
        }
    ' "${profile}"
}

patch_libvirt_apparmor_profile() {
    local profile=$1 state backup tmp
    state=$(passthrough_profile_state "${profile}")
    if [[ "${state}" == "unknown" ]]; then
        libvirt_host_error "unrecognized nested passt profile in ${profile}; refusing to modify it"
        return 1
    fi
    backup="${profile}.sp-vm-tools.bak"
    if [[ ! -e "${backup}" ]]; then
        cp -a "${profile}" "${backup}"
    fi

    if [[ "${state}" == "readonly" ]]; then
        sed -i \
            '/^[[:space:]]*profile passt[[:space:]]*{/,/^[[:space:]]*}/ s|/usr/bin/passt[[:space:]]\+r,|/usr/bin/passt rm,|' \
            "${profile}"
    fi
    state=$(passthrough_profile_state "${profile}")
    if [[ "${state}" == "needs-capability" ]]; then
        tmp=$(mktemp)
        awk '
            /^[[:space:]]*profile passt[[:space:]]*\{/ { in_passt = 1 }
            { print }
            in_passt && /\/usr\/bin\/passt[[:space:]]+rm,/ {
                print "    capability net_bind_service,"
            }
            in_passt && /^[[:space:]]*}/ { in_passt = 0 }
        ' "${profile}" > "${tmp}"
        cat "${tmp}" > "${profile}"
        rm -f "${tmp}"
    fi
    if [[ "$(passthrough_profile_state "${profile}")" != "ready" ]]; then
        libvirt_host_error "failed to make the nested passt AppArmor profile usable"
        return 1
    fi
}

configure_libvirt_apparmor() {
    local profile dropin_dir dropin template tmp
    profile=$(libvirt_host_path /etc/apparmor.d/abstractions/libvirt-qemu)
    dropin_dir=$(libvirt_host_path /etc/apparmor.d/abstractions/libvirt-qemu.d)
    dropin="${dropin_dir}/99-sp-vm-tools-local"
    template=$(libvirt_host_path /etc/apparmor.d/libvirt/TEMPLATE.qemu)

    [[ -r "${profile}" ]] || {
        libvirt_host_error "libvirt AppArmor profile is missing: ${profile}"
        return 1
    }
    patch_libvirt_apparmor_profile "${profile}"
    install -d -m 0755 "${dropin_dir}"
    tmp=$(mktemp)
    printf '%s\n' \
        '# Managed by sp-vm-tools bootstrap.' \
        'owner @{run}/libvirt/qemu/passt/* rw,' \
        'network vsock stream,' > "${tmp}"
    install -m 0644 "${tmp}" "${dropin}"
    rm -f "${tmp}"

    [[ -r "${template}" ]] || {
        libvirt_host_error "libvirt AppArmor template is missing: ${template}"
        return 1
    }
    apparmor_parser -Q -r "${template}"
    if [[ -z "${SPVM_TEST_ROOT:-}" ]]; then
        systemctl reload apparmor
    fi
}

collect_passt_binaries() {
    local candidate real
    local -A seen=()
    PASST_REAL_BINARIES=()
    if [[ $# -eq 0 ]]; then
        for candidate in passt passt.avx2; do
            real=$(command -v "${candidate}" 2>/dev/null || true)
            [[ -n "${real}" ]] || continue
            set -- "$@" "${real}"
        done
    fi
    for candidate in "$@"; do
        [[ -x "${candidate}" ]] || continue
        real=$(readlink -f -- "${candidate}")
        [[ -n "${real}" && -z "${seen[${real}]:-}" ]] || continue
        seen["${real}"]=1
        PASST_REAL_BINARIES+=("${real}")
    done
    if [[ ${#PASST_REAL_BINARIES[@]} -eq 0 ]]; then
        libvirt_host_error "no executable passt binary was found"
        return 1
    fi
}

verify_passt_capabilities() {
    local binary capabilities
    collect_passt_binaries "$@"
    for binary in "${PASST_REAL_BINARIES[@]}"; do
        capabilities=$(getcap "${binary}" 2>/dev/null || true)
        if [[ "${capabilities}" != *cap_net_bind_service* ]]; then
            libvirt_host_error "${binary} does not have CAP_NET_BIND_SERVICE"
            return 1
        fi
    done
}

# Optional arguments are used by unit tests to exercise symlink handling.
# shellcheck disable=SC2120
configure_passt_capabilities() {
    local binary
    collect_passt_binaries "$@"
    for binary in "${PASST_REAL_BINARIES[@]}"; do
        setcap cap_net_bind_service=ep "${binary}"
        if ! verify_passt_capabilities "${binary}"; then
            libvirt_host_error "failed to set CAP_NET_BIND_SERVICE on ${binary}; check filesystem xattr support"
            return 1
        fi
        echo "Configured CAP_NET_BIND_SERVICE on ${binary}"
    done
}

find_bootstrap_qemu() {
    local path
    for path in \
        /usr/local/bin/qemu-system-x86_64 \
        /usr/bin/qemu-system-x86_64 \
        /bin/qemu-system-x86_64 \
        /usr/local/sbin/qemu-system-x86_64 \
        /usr/sbin/qemu-system-x86_64; do
        [[ -x "${path}" ]] && { printf '%s\n' "${path}"; return 0; }
    done
    return 1
}

verify_libvirt_host() {
    local mode=$1 installed_version daemon_version qemu version_line qemu_major capabilities dropin
    installed_version=$(installed_libvirt_version)
    if [[ -z "${installed_version}" ]] || ! dpkg --compare-versions "${installed_version}" ge "${LIBVIRT_REQUIRED_VERSION}"; then
        libvirt_host_error "libvirt ${LIBVIRT_REQUIRED_VERSION} or newer is required; installed package is ${installed_version:-missing}"
        return 1
    fi
    python3 -c 'import libvirt'
    id libvirt-qemu >/dev/null
    aa-status --enabled >/dev/null
    virsh -c "${LIBVIRT_URI}" uri >/dev/null
    daemon_version=$(virsh -c "${LIBVIRT_URI}" version --daemon 2>/dev/null | sed -nE 's/.*daemon:[[:space:]]*([0-9.]+).*/\1/p' | tail -n 1)
    if [[ -z "${daemon_version}" ]] || ! dpkg --compare-versions "${daemon_version}" ge "${LIBVIRT_REQUIRED_VERSION}"; then
        libvirt_host_error "running libvirt daemon is older than ${LIBVIRT_REQUIRED_VERSION} (${daemon_version:-unknown})"
        return 1
    fi
    qemu=$(find_bootstrap_qemu) || {
        libvirt_host_error "qemu-system-x86_64 was not found"
        return 1
    }
    version_line=$("${qemu}" --version 2>/dev/null | head -n 1)
    qemu_major=$(sed -nE 's/.*version ([0-9]+).*/\1/p' <<< "${version_line}")
    if [[ -z "${qemu_major}" || "${qemu_major}" -lt 9 ]]; then
        libvirt_host_error "QEMU 9 or newer is required (found ${version_line:-unknown})"
        return 1
    fi
    capabilities=$(virsh -c "${LIBVIRT_URI}" domcapabilities --emulatorbin "${qemu}")
    if ! grep -Eq "<enum[[:space:]][^>]*name=['\"]iommufd['\"]" <<< "${capabilities}"; then
        libvirt_host_error "libvirt domain capabilities do not advertise IOMMUFD for ${qemu}"
        return 1
    fi
    # shellcheck disable=SC2119
    verify_passt_capabilities

    dropin=$(libvirt_host_path /etc/apparmor.d/abstractions/libvirt-qemu.d/99-sp-vm-tools-local)
    grep -qF 'network vsock stream,' "${dropin}" || {
        libvirt_host_error "AppArmor VSOCK rule is missing from ${dropin}"
        return 1
    }
    if [[ "${mode}" == "tdx" ]]; then
        [[ -c /dev/vhost-vsock ]] || {
            libvirt_host_error "/dev/vhost-vsock is missing"
            return 1
        }
        grep -Eq '^[[:space:]]*port[[:space:]]*=[[:space:]]*4050([[:space:]]|$)' /etc/qgs.conf || {
            libvirt_host_error "QGS is not configured for VSOCK port 4050"
            return 1
        }
        systemctl is-active --quiet qgsd
    elif [[ "${mode}" == "sev-snp" ]]; then
        grep -qi 'sev-snp' <<< "${capabilities}" || {
            libvirt_host_error "domain capabilities do not advertise SEV-SNP launch security"
            return 1
        }
    else
        libvirt_host_error "invalid confidential VM mode: ${mode}"
        return 1
    fi
}

setup_libvirt_host() {
    local mode=$1 installed_version
    if [[ "${mode}" != "tdx" && "${mode}" != "sev-snp" ]]; then
        libvirt_host_error "setup_libvirt_host mode must be tdx or sev-snp"
        return 1
    fi
    if [[ $(id -u) -ne 0 ]]; then
        libvirt_host_error "libvirt host setup must run as root"
        return 1
    fi
    get_supported_ubuntu_version

    print_section_header "Libvirt Host Setup"
    installed_version=$(installed_libvirt_version)
    if libvirt_upgrade_required "${installed_version}"; then
        assert_no_running_libvirt_domains
    fi
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        acl apparmor-utils ca-certificates libcap2-bin passt python3-libvirt \
        qemu-system-x86 qemu-utils wget

    installed_version=$(installed_libvirt_version)
    if libvirt_upgrade_required "${installed_version}"; then
        echo "Installed libvirt ${installed_version:-none} is older than ${LIBVIRT_REQUIRED_VERSION}."
        install_project_libvirt
    else
        echo "Installed libvirt ${installed_version} is ${LIBVIRT_REQUIRED_VERSION} or newer; keeping it."
    fi

    configure_libvirt_apparmor
    # shellcheck disable=SC2119
    configure_passt_capabilities
    systemctl daemon-reload
    systemctl enable --now libvirtd.service
    systemctl start virtlogd.socket virtlockd.socket
    install -d -o libvirt-qemu -g libvirt-qemu -m 0750 \
        /var/lib/libvirt/images/superprotocol
    verify_libvirt_host "${mode}"
    echo "Libvirt host setup complete."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    script_dir=$(cd "$(dirname "$0")" && pwd)
    # shellcheck disable=SC1091
    source "${script_dir}/common.sh"
    setup_libvirt_host "${1:-}"
fi
