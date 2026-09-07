#!/bin/bash

set -euo pipefail

readonly QEMU_VERSION=10.2.1
readonly QEMU_SOURCE_SHA256=a3717477d8e2c84d630bfffbc20f6cd3293eb45aa1e6dac6d0cc27689991c9e1
readonly QEMU_SOURCE_URL="https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz"
readonly SPVM_REVISION=${SPVM_REVISION:?SPVM_REVISION is required}
readonly OUTPUT_UID=${OUTPUT_UID:-0}
readonly OUTPUT_GID=${OUTPUT_GID:-0}
readonly PACKAGE_NAME=sp-qemu-tdx
readonly PACKAGE_VERSION="${QEMU_VERSION}-1spvm${SPVM_REVISION}~ubuntu24.04.1"
readonly INSTALL_PREFIX=/opt/sp-qemu-tdx-10.2
readonly OUTPUT_DIR=/out

[[ "${SPVM_REVISION}" =~ ^[1-9][0-9]*$ ]] || {
    echo "Error: SPVM_REVISION must be a positive integer" >&2
    exit 2
}

work_dir=$(mktemp -d /tmp/sp-qemu-build.XXXXXX)
cleanup() {
    rm -rf -- "${work_dir}"
}
trap cleanup EXIT

source_archive="${work_dir}/qemu-${QEMU_VERSION}.tar.xz"
curl --fail --location --silent --show-error \
    --output "${source_archive}" \
    "${QEMU_SOURCE_URL}"
echo "${QEMU_SOURCE_SHA256}  ${source_archive}" | sha256sum --check

tar --extract --xz --file "${source_archive}" --directory "${work_dir}"
source_dir="${work_dir}/qemu-${QEMU_VERSION}"
build_dir="${work_dir}/build"
package_root="${work_dir}/debian/${PACKAGE_NAME}"

is_native_dynamic_elf() {
    file --brief "$1" \
        | grep -qE '^ELF 64-bit LSB (pie executable|shared object), x86-64.*dynamically linked'
}

mkdir -p "${build_dir}"
cd "${build_dir}"
"${source_dir}/configure" \
    --prefix="${INSTALL_PREFIX}" \
    --target-list=x86_64-softmmu \
    --enable-kvm \
    --enable-slirp \
    --enable-virtfs \
    --disable-docs \
    --disable-werror

DESTDIR="${package_root}" ninja -C "${build_dir}" install

# Keep a stable executable path for the raw and libvirt launchers.
install -d "${package_root}/usr/local/bin"
ln -s "${INSTALL_PREFIX}/bin/qemu-system-x86_64" \
    "${package_root}/usr/local/bin/qemu-system-x86_64"

# Strip only ELF files; firmware blobs and scripts must remain untouched.
while IFS= read -r -d '' candidate; do
    if is_native_dynamic_elf "${candidate}"; then
        strip --strip-unneeded "${candidate}"
    fi
done < <(find "${package_root}" -type f -print0)

# Derive runtime library dependencies from every installed ELF, including QEMU
# modules that are loaded dynamically and are not visible from the main binary.
cat > "${work_dir}/debian/control" <<EOF
Source: ${PACKAGE_NAME}
Section: misc
Priority: optional
Maintainer: Super Protocol VM Tools <devnull@superprotocol.com>
Standards-Version: 4.6.2

Package: ${PACKAGE_NAME}
Architecture: amd64
Description: Pinned QEMU ${QEMU_VERSION} build for Intel TDX hosts
EOF

elf_args=()
while IFS= read -r -d '' candidate; do
    if is_native_dynamic_elf "${candidate}"; then
        elf_args+=("-e${candidate#"${work_dir}/"}")
    fi
done < <(find "${package_root}" -type f -print0)
[[ ${#elf_args[@]} -gt 0 ]] || {
    echo "Error: QEMU install contains no ELF files" >&2
    exit 1
}

cd "${work_dir}"
shlibs_output=$(dpkg-shlibdeps --ignore-missing-info -O "${elf_args[@]}")
runtime_deps=${shlibs_output#shlibs:Depends=}
[[ -n "${runtime_deps}" && "${runtime_deps}" != "${shlibs_output}" ]] || {
    echo "Error: failed to calculate runtime dependencies" >&2
    exit 1
}

control_dir="${package_root}/DEBIAN"
install -d "${control_dir}"
cat > "${control_dir}/control" <<EOF
Package: ${PACKAGE_NAME}
Version: ${PACKAGE_VERSION}
Section: misc
Priority: optional
Architecture: amd64
Maintainer: Super Protocol VM Tools <devnull@superprotocol.com>
Depends: ${runtime_deps}
Description: QEMU ${QEMU_VERSION} for Intel TDX on Ubuntu 24.04
 Reproducible upstream QEMU build with KVM, VFIO/iommufd and TDX support.
 Installed under ${INSTALL_PREFIX}.
EOF

installed_size=$(du --summarize --block-size=1024 "${package_root}" | cut -f1)
printf 'Installed-Size: %s\n' "${installed_size}" >> "${control_dir}/control"

mkdir -p "${OUTPUT_DIR}"
package_path="${OUTPUT_DIR}/${PACKAGE_NAME}_${PACKAGE_VERSION}_amd64.deb"
dpkg-deb --root-owner-group --build "${package_root}" "${package_path}"

# Verify the built package, its key capabilities, and the firmware ROM needed
# by virtio-net before publishing it as a CI artifact.
verify_root="${work_dir}/verify"
dpkg-deb --extract "${package_path}" "${verify_root}"
qemu_binary="${verify_root}${INSTALL_PREFIX}/bin/qemu-system-x86_64"
"${qemu_binary}" --version | grep -F "QEMU emulator version ${QEMU_VERSION}"
"${qemu_binary}" -object help | grep -q 'tdx-guest'
"${qemu_binary}" -device vfio-pci,help | grep -q 'iommufd'
test -s "${verify_root}${INSTALL_PREFIX}/share/qemu/efi-virtio.rom"

(
    cd "${OUTPUT_DIR}"
    sha256sum "$(basename "${package_path}")" > SHA256SUMS
    tar --create --gzip --file qemu-ubuntu24.tar.gz \
        "$(basename "${package_path}")" SHA256SUMS
    sha256sum qemu-ubuntu24.tar.gz > qemu-ubuntu24.tar.gz.sha256
)

chown -R "${OUTPUT_UID}:${OUTPUT_GID}" "${OUTPUT_DIR}"
echo "Built ${package_path}"
echo "Package version: ${PACKAGE_VERSION}"
