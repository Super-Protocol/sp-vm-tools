#!/bin/bash

# Shared libvirt host preparation for bootstrap_tdx.sh and bootstrap_snp.sh.
# This file is sourceable for unit tests and can also be executed directly.

LIBVIRT_REQUIRED_VERSION="12.5.0"
LIBVIRT_REQUIRED_PACKAGE_VERSION="${LIBVIRT_REQUIRED_VERSION}"
LIBVIRT_RELEASE_REPO="Super-Protocol/sp-vm-tools"
LIBVIRT_URI="qemu:///system"
PASST_UNPRIVILEGED_PORT_START="0"
PASST_APPARMOR_DISCONNECTED_PATH="/att/passt/"

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
    libvirt-daemon-system-systemd
    libvirt-dev
)

LIBVIRT_PACKAGE_PATHS=()
PASST_REAL_BINARIES=()
LIBVIRT_QEMU_CONFIG_CHANGED=0

libvirt_host_error() {
    echo "ERROR: $*" >&2
    return 1
}

select_libvirt_release() {
    local ubuntu_version=$1
    case "${ubuntu_version}" in
        24.04)
            LIBVIRT_REQUIRED_PACKAGE_VERSION="12.5.0-1spvm45~ubuntu24.04.1"
            LIBVIRT_RELEASE_TAG="45-libvirt-ubuntu24"
            LIBVIRT_RELEASE_ASSET="libvirt-ubuntu24.tar.gz"
            LIBVIRT_RELEASE_SHA256="ab1f980944a9ffb452654e3897fd91465e4673debf51683db9b7f2df96a21d61"
            ;;
        26.04)
            LIBVIRT_REQUIRED_PACKAGE_VERSION="${LIBVIRT_REQUIRED_VERSION}"
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
    local os_release=/etc/os-release
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

running_libvirt_version() {
    local numeric
    numeric=$(python3 -c '
import libvirt
conn = libvirt.openReadOnly("qemu:///system")
try:
    print(conn.getLibVersion())
finally:
    conn.close()
' 2>/dev/null || true)
    if [[ ! "${numeric}" =~ ^[0-9]+$ ]]; then
        return 1
    fi
    printf '%d.%d.%d\n' \
        "$((numeric / 1000000))" \
        "$(((numeric / 1000) % 1000))" \
        "$((numeric % 1000))"
}

libvirt_upgrade_required() {
    local installed_version=${1:-}
    [[ -z "${installed_version}" ]] || \
        ! dpkg --compare-versions "${installed_version}" ge "${LIBVIRT_REQUIRED_PACKAGE_VERSION}"
}

missing_required_libvirt_packages() {
    local package
    for package in "${LIBVIRT_BASE_PACKAGES[@]}"; do
        if ! dpkg-query -W -f='${db:Status-Status}' "${package}" 2>/dev/null | grep -qx installed; then
            printf '%s\n' "${package}"
        fi
    done
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
    assert_no_running_libvirt_domains || return 1
    work_dir=$(mktemp -d /var/tmp/sp-vm-libvirt.XXXXXX) || return 1
    chmod 0755 "${work_dir}" || return 1
    archive="${work_dir}/${LIBVIRT_RELEASE_ASSET}"

    echo "Downloading libvirt ${LIBVIRT_REQUIRED_PACKAGE_VERSION} from ${LIBVIRT_RELEASE_URL}"
    wget --https-only --tries=3 -O "${archive}" "${LIBVIRT_RELEASE_URL}" || return 1
    chmod 0644 "${archive}" || return 1
    verify_and_extract_libvirt_archive "${archive}" "${work_dir}" || return 1
    prepare_libvirt_package_compatibility "${LIBVIRT_PACKAGE_DIR}" "${UBUNTU_VERSION}" || return 1
    find "${work_dir}" -type d -exec chmod a+rx {} + || return 1
    find "${work_dir}" -type f \( -name '*.deb' -o -name '*.ddeb' \) -exec chmod a+r {} + || return 1
    build_libvirt_package_plan "${LIBVIRT_PACKAGE_DIR}" || return 1

    echo "APT simulation for the libvirt upgrade:"
    simulation=$(LC_ALL=C apt-get --simulate --no-install-recommends --no-remove install "${LIBVIRT_PACKAGE_PATHS[@]}") || return 1
    printf '%s\n' "${simulation}"
    assert_safe_apt_simulation "${simulation}" || return 1

    DEBIAN_FRONTEND=noninteractive apt-get \
        --no-install-recommends \
        --no-remove \
        -o Dpkg::Options::=--force-confold \
        install -y "${LIBVIRT_PACKAGE_PATHS[@]}" || return 1
    rm -rf "${work_dir}" || return 1
}

passthrough_profile_state() {
    local profile=$1
    awk '
        /^[[:space:]]*profile passt([[:space:]]+flags=\([^)]*\))?[[:space:]]*\{/ {
            in_passt = 1
            found = 1
            next
        }
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

passthrough_profile_handles_disconnected_sockets() {
    local profile=$1
    awk '
        /^[[:space:]]*profile passt([[:space:]]+flags=\([^)]*\))?[[:space:]]*\{/ {
            found = 1
            if ($0 ~ /attach_disconnected/) handled = 1
        }
        END { exit !(found && handled) }
    ' "${profile}"
}

patch_passthrough_disconnected_socket_handling() {
    local profile=$1 tmp
    tmp=$(mktemp)
    if ! awk -v path="${PASST_APPARMOR_DISCONNECTED_PATH}" '
        BEGIN { patched = 0 }
        /^[[:space:]]*profile passt([[:space:]]+flags=\([^)]*\))?[[:space:]]*\{/ {
            if ($0 ~ /attach_disconnected/) {
                patched = 1
            } else if ($0 ~ /flags=\(/) {
                sub(/flags=\(/, "flags=(attach_disconnected.path=" path " ")
                patched = 1
            } else {
                sub(/\{[[:space:]]*$/, "flags=(attach_disconnected.path=" path ") {")
                patched = 1
            }
        }
        { print }
        END { if (!patched) exit 1 }
    ' "${profile}" > "${tmp}"; then
        rm -f "${tmp}"
        libvirt_host_error "failed to add disconnected socket handling to the nested passt profile"
        return 1
    fi
    cat "${tmp}" > "${profile}"
    rm -f "${tmp}"
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

    # TODO: Remove this workaround after Ubuntu's passt/libvirt AppArmor
    # policy handles the listening Unix socket that becomes disconnected when
    # passt pivots into its empty sandbox root.  AppArmor 5 on Ubuntu 26.04
    # otherwise rejects accept4() with EACCES.  A synthetic attachment prefix
    # is scoped to the nested passt profile and avoids disabling confinement.
    if [[ "${UBUNTU_VERSION:-}" == "26.04" ]] && \
        ! passthrough_profile_handles_disconnected_sockets "${profile}"; then
        patch_passthrough_disconnected_socket_handling "${profile}" || return 1
    fi
    if [[ "$(passthrough_profile_state "${profile}")" != "ready" ]]; then
        libvirt_host_error "failed to make the nested passt AppArmor profile usable"
        return 1
    fi
    if [[ "${UBUNTU_VERSION:-}" == "26.04" ]] && \
        ! passthrough_profile_handles_disconnected_sockets "${profile}"; then
        libvirt_host_error "nested passt AppArmor profile does not handle disconnected Unix sockets"
        return 1
    fi
}

configure_libvirt_apparmor() {
    local profile dropin_dir dropin template daemon_profile daemon_local tmp
    profile=/etc/apparmor.d/abstractions/libvirt-qemu
    dropin_dir=/etc/apparmor.d/abstractions/libvirt-qemu.d
    dropin="${dropin_dir}/99-sp-vm-tools-local"
    template=/etc/apparmor.d/libvirt/TEMPLATE.qemu
    daemon_profile=/etc/apparmor.d/usr.sbin.libvirtd
    daemon_local=/etc/apparmor.d/local/usr.sbin.libvirtd

    [[ -r "${profile}" ]] || {
        libvirt_host_error "libvirt AppArmor profile is missing: ${profile}"
        return 1
    }
    patch_libvirt_apparmor_profile "${profile}" || return 1
    install -d -m 0755 "${dropin_dir}"
    tmp=$(mktemp)
    printf '%s\n' \
        '# Managed by sp-vm-tools bootstrap.' \
        '/usr/local/bin/qemu-system-x86_64 rmix,' \
        "${NOBLE_QEMU_INSTALL_PREFIX}/bin/qemu-system-x86_64 rmix," \
        '/usr/local/share/qemu/** rk,' \
        "${NOBLE_QEMU_INSTALL_PREFIX}/share/qemu/** rk," \
        '/usr/local/lib{,64}/qemu/*.so mr,' \
        '/usr/local/lib/@{multiarch}/qemu/*.so mr,' \
        'owner @{run}/libvirt/qemu/passt/* rw,' \
        '@{run}/tdx-qgs/qgs.socket rw,' \
        'network vsock stream,' > "${tmp}"
    install -m 0644 "${tmp}" "${dropin}"
    rm -f "${tmp}"

    if [[ -r "${daemon_profile}" ]]; then
        if ! grep -qF 'include if exists <local/usr.sbin.libvirtd>' "${daemon_profile}"; then
            libvirt_host_error "${daemon_profile} does not include its standard local override; refusing to modify AppArmor"
            return 1
        fi
        install -d -m 0755 "$(dirname "${daemon_local}")"
        touch "${daemon_local}"
        chmod 0644 "${daemon_local}"
        if ! grep -qF '/usr/local/bin/qemu-system-x86_64 PUx,' "${daemon_local}"; then
            printf '%s\n' \
                '# Managed by sp-vm-tools: allow libvirtd capabilities probing.' \
                '/usr/local/bin/qemu-system-x86_64 PUx,' >> "${daemon_local}"
        fi
        if ! grep -qF "${NOBLE_QEMU_INSTALL_PREFIX}/bin/qemu-system-x86_64 PUx," "${daemon_local}"; then
            printf '%s\n' \
                '# The project package exposes /usr/local/bin as a symlink;' \
                '# libvirt resolves it before execve, so allow the real target too.' \
                "${NOBLE_QEMU_INSTALL_PREFIX}/bin/qemu-system-x86_64 PUx," >> "${daemon_local}"
        fi
        apparmor_parser -Q -r "${daemon_profile}"
    fi

    [[ -r "${template}" ]] || {
        libvirt_host_error "libvirt AppArmor template is missing: ${template}"
        return 1
    }
    apparmor_parser -Q -r "${template}"
    systemctl reload apparmor
}

configure_tdx_qgs_access() {
    getent group qgsd >/dev/null || {
        libvirt_host_error "QGS group qgsd is missing"
        return 1
    }
    usermod -a -G qgsd libvirt-qemu || return 1
}

configure_libvirt_qemu_runtime() {
    local config=/etc/libvirt/qemu.conf backup tmp
    backup="${config}.sp-vm-tools.bak"
    [[ -r "${config}" ]] || {
        libvirt_host_error "libvirt QEMU configuration is missing: ${config}"
        return 1
    }
    tmp=$(mktemp)
    awk '
        BEGIN {
            user_written = 0
            group_written = 0
            ownership_written = 0
        }
        /^[[:space:]]*user[[:space:]]*=/ {
            if (!user_written) {
                print "user = \"libvirt-qemu\""
                user_written = 1
            }
            next
        }
        /^[[:space:]]*group[[:space:]]*=/ {
            if (!group_written) {
                print "group = \"libvirt-qemu\""
                group_written = 1
            }
            next
        }
        /^[[:space:]]*dynamic_ownership[[:space:]]*=/ {
            if (!ownership_written) {
                print "dynamic_ownership = 1"
                ownership_written = 1
            }
            next
        }
        { print }
        END {
            if (!user_written)
                print "user = \"libvirt-qemu\""
            if (!group_written)
                print "group = \"libvirt-qemu\""
            if (!ownership_written)
                print "dynamic_ownership = 1"
        }
    ' "${config}" > "${tmp}"

    if cmp -s "${tmp}" "${config}"; then
        rm -f "${tmp}"
        echo "Libvirt QEMU runtime already uses libvirt-qemu."
        return
    fi

    if ! assert_no_running_libvirt_domains; then
        rm -f "${tmp}"
        return 1
    fi
    if [[ ! -e "${backup}" ]]; then
        cp -a "${config}" "${backup}"
    fi
    cat "${tmp}" > "${config}"
    chmod 0600 "${config}"
    rm -f "${tmp}"
    LIBVIRT_QEMU_CONFIG_CHANGED=1
    echo "Configured libvirt QEMU runtime user/group as libvirt-qemu with dynamic ownership."
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

verify_passt_unprivileged_ports() {
    local value
    value=$(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null || true)
    if [[ "${value}" != "${PASST_UNPRIVILEGED_PORT_START}" ]]; then
        libvirt_host_error \
            "net.ipv4.ip_unprivileged_port_start must be ${PASST_UNPRIVILEGED_PORT_START} for passt privileged-port forwarding (found ${value:-unavailable})"
        return 1
    fi
}

configure_passt_unprivileged_ports() {
    local sysctl_dir=/etc/sysctl.d
    local config="${sysctl_dir}/99-sp-vm-tools-passt.conf" tmp

    install -d -m 0755 "${sysctl_dir}"
    tmp=$(mktemp)
    printf '%s\n' \
        '# Managed by sp-vm-tools bootstrap.' \
        '# TODO: UNSAFE CONFIGURATION. Temporary workaround for a passt regression.' \
        '# Remove it when passt can bind forwarded low ports before entering its' \
        '# unprivileged user namespace. New passt versions create host listeners' \
        '# after user-namespace isolation,' \
        '# so CAP_NET_BIND_SERVICE on the passt binary no longer authorizes bind()' \
        '# in the host network namespace. This is the only stock, unpatched setup' \
        '# currently found to keep libvirt low-port forwarding working. Setting' \
        '# this value to 0 lets every unprivileged process on the host bind any' \
        '# free TCP or UDP port.' \
        "net.ipv4.ip_unprivileged_port_start = ${PASST_UNPRIVILEGED_PORT_START}" > "${tmp}"
    install -m 0644 "${tmp}" "${config}"
    rm -f "${tmp}"

    sysctl -w \
        "net.ipv4.ip_unprivileged_port_start=${PASST_UNPRIVILEGED_PORT_START}" >/dev/null
    verify_passt_unprivileged_ports || return 1
    echo "Configured net.ipv4.ip_unprivileged_port_start=${PASST_UNPRIVILEGED_PORT_START} for passt port forwarding (unsafe workaround)."
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

configure_qemu_binary_permissions() {
    local qemu real parent
    qemu=$(find_bootstrap_qemu) || {
        libvirt_host_error "qemu-system-x86_64 was not found"
        return 1
    }
    real=$(readlink -f -- "${qemu}")
    [[ -n "${real}" && -f "${real}" ]] || {
        libvirt_host_error "cannot resolve QEMU binary ${qemu}"
        return 1
    }

    if [[ "${real}" == /usr/local/* ]]; then
        chmod a+rx "${real}"
        parent=$(dirname "${real}")
        while [[ "${parent}" == /usr/local/* ]]; do
            chmod a+x "${parent}"
            parent=$(dirname "${parent}")
        done
        chmod a+x /usr/local
    fi

    if ! runuser -u libvirt-qemu -- test -x "${qemu}"; then
        libvirt_host_error "libvirt-qemu cannot execute ${qemu}; check directory permissions and noexec mounts"
        return 1
    fi
}

configure_iommufd() {
    local modules_dir=/etc/modules-load.d modules_file tmp
    modules_file="${modules_dir}/sp-vm-tools-iommufd.conf"

    install -d -m 0755 "${modules_dir}"
    tmp=$(mktemp)
    printf '%s\n' \
        '# Managed by sp-vm-tools bootstrap.' \
        'iommufd' > "${tmp}"
    install -m 0644 "${tmp}" "${modules_file}"
    rm -f "${tmp}"

    modprobe iommufd
    [[ -c /dev/iommu ]] || {
        libvirt_host_error "iommufd loaded but /dev/iommu is missing"
        return 1
    }
}

verify_libvirt_host() {
    local mode=$1 installed_version missing_packages daemon_version qemu version_line qemu_major capabilities dropin
    installed_version=$(installed_libvirt_version)
    if [[ -z "${installed_version}" ]] || ! dpkg --compare-versions "${installed_version}" ge "${LIBVIRT_REQUIRED_PACKAGE_VERSION}"; then
        libvirt_host_error "libvirt package ${LIBVIRT_REQUIRED_PACKAGE_VERSION} or newer is required; installed package is ${installed_version:-missing}"
        return 1
    fi
    missing_packages=$(missing_required_libvirt_packages)
    if [[ -n "${missing_packages}" ]]; then
        libvirt_host_error "required libvirt packages are missing: ${missing_packages//$'\n'/, }"
        return 1
    fi
    python3 -c 'import libvirt' || {
        libvirt_host_error "python3-libvirt is not importable"
        return 1
    }
    id libvirt-qemu >/dev/null || {
        libvirt_host_error "the libvirt-qemu user is missing"
        return 1
    }
    aa-status --enabled >/dev/null || {
        libvirt_host_error "AppArmor is not enabled"
        return 1
    }
    virsh -c "${LIBVIRT_URI}" uri >/dev/null || {
        libvirt_host_error "cannot connect to ${LIBVIRT_URI}"
        return 1
    }
    daemon_version=$(running_libvirt_version || true)
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
    if ! capabilities=$(virsh -c "${LIBVIRT_URI}" domcapabilities --emulatorbin "${qemu}" 2>&1); then
        libvirt_host_error "libvirt cannot probe ${qemu}: ${capabilities}"
        echo "Check recent access denials with: journalctl -k --since '-5 min' --no-pager | grep -E 'apparmor=\"DENIED\"|qemu-system'" >&2
        return 1
    fi
    if ! grep -Eq "<enum[[:space:]][^>]*name=['\"]iommufd['\"]" <<< "${capabilities}"; then
        libvirt_host_error "libvirt domain capabilities do not advertise IOMMUFD for ${qemu}"
        return 1
    fi
    # shellcheck disable=SC2119
    verify_passt_capabilities || return 1
    verify_passt_unprivileged_ports || return 1

    dropin=/etc/apparmor.d/abstractions/libvirt-qemu.d/99-sp-vm-tools-local
    grep -qF 'network vsock stream,' "${dropin}" || {
        libvirt_host_error "AppArmor VSOCK rule is missing from ${dropin}"
        return 1
    }
    if [[ "${UBUNTU_VERSION:-}" == "26.04" ]] && \
        ! passthrough_profile_handles_disconnected_sockets \
            /etc/apparmor.d/abstractions/libvirt-qemu; then
        libvirt_host_error "nested passt AppArmor profile lacks Ubuntu 26.04 disconnected socket handling; rerun bootstrap"
        return 1
    fi
    if [[ "${mode}" == "tdx" ]]; then
        [[ -c /dev/vhost-vsock ]] || {
            libvirt_host_error "/dev/vhost-vsock is missing"
            return 1
        }
        if grep -Eq '^[[:space:]]*port[[:space:]]*=' /etc/qgs.conf; then
            libvirt_host_error "QGS must use its Unix socket; remove the port setting from /etc/qgs.conf"
            return 1
        fi
        systemctl is-active --quiet qgsd || {
            libvirt_host_error "qgsd is not active"
            return 1
        }
        [[ -S /var/run/tdx-qgs/qgs.socket ]] || {
            libvirt_host_error "QGS Unix socket is missing: /var/run/tdx-qgs/qgs.socket"
            return 1
        }
        id -nG libvirt-qemu | tr ' ' '\n' | grep -qx qgsd || {
            libvirt_host_error "libvirt-qemu is not a member of the qgsd group"
            return 1
        }
        if [[ "$(stat -Lc '%U:%G' /var/run/tdx-qgs/qgs.socket 2>/dev/null)" != "qgsd:qgsd" ]]; then
            libvirt_host_error "QGS Unix socket must be owned by qgsd:qgsd: /var/run/tdx-qgs/qgs.socket"
            return 1
        fi
        if ! runuser -u libvirt-qemu -- test -w /var/run/tdx-qgs/qgs.socket; then
            local qgs_socket_mode
            qgs_socket_mode=$(stat -Lc '%a' /var/run/tdx-qgs/qgs.socket 2>/dev/null || echo unknown)
            libvirt_host_error "libvirt-qemu cannot connect to the QGS Unix socket (mode ${qgs_socket_mode}); rerun bootstrap_tdx.sh"
            return 1
        fi
        grep -qF '@{run}/tdx-qgs/qgs.socket rw,' "${dropin}" || {
            libvirt_host_error "AppArmor QGS socket rule is missing from ${dropin}"
            return 1
        }
        if ! grep -Eq "<enum[[:space:]][^>]*name=['\"]sectype['\"]" <<< "${capabilities}" || \
            ! grep -Eq '<value>tdx</value>' <<< "${capabilities}"; then
            libvirt_host_error "domain capabilities do not advertise native TDX launch security"
            return 1
        fi
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
    local mode=$1 installed_version missing_packages
    if [[ "${mode}" != "tdx" && "${mode}" != "sev-snp" ]]; then
        libvirt_host_error "setup_libvirt_host mode must be tdx or sev-snp"
        return 1
    fi
    if [[ $(id -u) -ne 0 ]]; then
        libvirt_host_error "libvirt host setup must run as root"
        return 1
    fi
    get_supported_ubuntu_version || return 1

    print_section_header "Libvirt Host Setup"
    installed_version=$(installed_libvirt_version)
    missing_packages=$(missing_required_libvirt_packages)
    if libvirt_upgrade_required "${installed_version}" || [[ -n "${missing_packages}" ]]; then
        assert_no_running_libvirt_domains || return 1
    fi
    apt-get update || return 1
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        acl apparmor-utils ca-certificates libcap2-bin passt procps python3-libvirt \
        qemu-system-x86 qemu-utils wget || return 1

    installed_version=$(installed_libvirt_version)
    missing_packages=$(missing_required_libvirt_packages)
    if libvirt_upgrade_required "${installed_version}" || [[ -n "${missing_packages}" ]]; then
        if libvirt_upgrade_required "${installed_version}"; then
            echo "Installed libvirt ${installed_version:-none} is older than ${LIBVIRT_REQUIRED_PACKAGE_VERSION}."
        fi
        if [[ -n "${missing_packages}" ]]; then
            echo "Required libvirt packages are missing: ${missing_packages//$'\n'/, }"
        fi
        install_project_libvirt || return 1
    else
        echo "Installed libvirt ${installed_version} is ${LIBVIRT_REQUIRED_PACKAGE_VERSION} or newer and all required packages are present; keeping it."
    fi

    if dpkg-query -W -f='${db:Status-Status}' libvirt-daemon-system-systemd 2>/dev/null | grep -qx installed; then
        apt-mark manual libvirt-daemon-system-systemd >/dev/null
    fi

    configure_libvirt_qemu_runtime || return 1
    if [[ "${mode}" == "tdx" ]]; then
        configure_tdx_qgs_access || return 1
    fi
    configure_libvirt_apparmor || return 1
    configure_qemu_binary_permissions || return 1
    configure_iommufd || return 1
    # shellcheck disable=SC2119
    configure_passt_capabilities || return 1
    configure_passt_unprivileged_ports || return 1
    systemctl daemon-reload || return 1
    systemctl enable --now libvirtd.service || return 1
    systemctl start virtlogd.socket virtlockd.socket || return 1

    local daemon_version
    daemon_version=$(running_libvirt_version || true)
    if [[ "${LIBVIRT_QEMU_CONFIG_CHANGED}" -eq 1 ]] || \
        [[ -z "${daemon_version}" ]] || \
        ! dpkg --compare-versions "${daemon_version}" ge "${LIBVIRT_REQUIRED_VERSION}"; then
        assert_no_running_libvirt_domains || return 1
        echo "Restarting libvirtd to activate the installed ${LIBVIRT_REQUIRED_VERSION} runtime"
        systemctl restart libvirtd.service || return 1
    fi

    install -d -o libvirt-qemu -g libvirt-qemu -m 0750 \
        /var/lib/libvirt/images/superprotocol || return 1
    verify_libvirt_host "${mode}" || return 1
    echo "Libvirt host setup complete."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    script_dir=$(cd "$(dirname "$0")" && pwd)
    # shellcheck disable=SC1091
    source "${script_dir}/common.sh"
    setup_libvirt_host "${1:-}"
fi
