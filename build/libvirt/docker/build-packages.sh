#!/bin/bash

set -euo pipefail

readonly SOURCE_DIR=/opt/libvirt-source
readonly OUTPUT_DIR=/out

readonly LIBVIRT_VERSION=${LIBVIRT_VERSION:?LIBVIRT_VERSION is required}
readonly DEBIAN_REVISION=${DEBIAN_REVISION:?DEBIAN_REVISION is required}
readonly SPVM_REVISION=${SPVM_REVISION:-2}
readonly TARGET_UBUNTU_VERSION=${TARGET_UBUNTU_VERSION:?TARGET_UBUNTU_VERSION is required}
readonly SKIP_TESTS=${SKIP_TESTS:-false}
readonly OUTPUT_UID=${OUTPUT_UID:-0}
readonly OUTPUT_GID=${OUTPUT_GID:-0}

# Provided by every Ubuntu builder image.
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "${TARGET_UBUNTU_VERSION}" ]]; then
    echo "Error: builder is Ubuntu ${VERSION_ID:-unknown}, target is ${TARGET_UBUNTU_VERSION}" >&2
    exit 1
fi

case "${TARGET_UBUNTU_VERSION}" in
    24.04)
        expected_codename=noble
        ;;
    26.04)
        expected_codename=resolute
        ;;
    *)
        echo "Error: unsupported Ubuntu target: ${TARGET_UBUNTU_VERSION}" >&2
        exit 1
        ;;
esac

if [[ "${VERSION_CODENAME:-}" != "${expected_codename}" ]]; then
    echo "Error: Ubuntu ${TARGET_UBUNTU_VERSION} has unexpected codename ${VERSION_CODENAME:-unknown}" >&2
    exit 1
fi

if [[ ! "${SPVM_REVISION}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: SPVM_REVISION must be a positive integer" >&2
    exit 1
fi

readonly PACKAGE_VERSION="${LIBVIRT_VERSION}-${DEBIAN_REVISION}spvm${SPVM_REVISION}~ubuntu${TARGET_UBUNTU_VERSION}.1"

work_dir=$(mktemp -d /tmp/libvirt-package-build.XXXXXX)
cleanup() {
    rm -rf -- "${work_dir}"
}
trap cleanup EXIT

cp -a "${SOURCE_DIR}" "${work_dir}/libvirt"
cd "${work_dir}/libvirt"

export DEBFULLNAME="Super Protocol VM Tools"
export DEBEMAIL="devnull@superprotocol.com"
dch \
    --newversion "${PACKAGE_VERSION}" \
    --distribution "${expected_codename}" \
    --force-distribution \
    "Enable runtime detection of Intel TDX KVM VM types on Ubuntu ${TARGET_UBUNTU_VERSION}."

build_options=""
if [[ "${SKIP_TESTS}" == "true" ]]; then
    build_options="nocheck"
elif [[ "${SKIP_TESTS}" != "false" ]]; then
    echo "Error: SKIP_TESTS must be true or false" >&2
    exit 1
fi
export DEB_BUILD_OPTIONS="${build_options}"

echo "Building libvirt ${PACKAGE_VERSION} on Ubuntu ${TARGET_UBUNTU_VERSION} (${VERSION_CODENAME})"
dpkg-buildpackage --build=binary --unsigned-source --unsigned-changes -jauto

shopt -s nullglob
driver_packages=("${work_dir}"/libvirt-daemon-driver-qemu_*.deb)
if [[ ${#driver_packages[@]} -ne 1 ]]; then
    echo "Error: expected one libvirt-daemon-driver-qemu package, found ${#driver_packages[@]}" >&2
    exit 1
fi

verify_dir="${work_dir}/verify-tdx-driver"
dpkg-deb --extract "${driver_packages[0]}" "${verify_dir}"
driver_candidates=("${verify_dir}"/usr/lib/*/libvirt/connection-driver/libvirt_driver_qemu.so)
if [[ ${#driver_candidates[@]} -ne 1 ]]; then
    echo "Error: expected one libvirt QEMU connection driver, found ${#driver_candidates[@]}" >&2
    exit 1
fi
if ! LC_ALL=C grep -aFq 'KVM VM types:' "${driver_candidates[0]}"; then
    echo "Error: libvirt QEMU driver was built without the runtime KVM VM-types probe" >&2
    exit 1
fi
if LC_ALL=C grep -aFq 'KVM not compiled' "${driver_candidates[0]}"; then
    echo "Error: libvirt QEMU driver still contains the compile-time-disabled TDX probe" >&2
    exit 1
fi
echo "Verified runtime KVM VM-types probing in libvirt QEMU driver."

mkdir -p "${OUTPUT_DIR}"
artifacts=(
    "${work_dir}"/*.deb
    "${work_dir}"/*.ddeb
    "${work_dir}"/*.changes
    "${work_dir}"/*.buildinfo
)
if [[ ${#artifacts[@]} -eq 0 ]]; then
    echo "Error: package build produced no artifacts" >&2
    exit 1
fi

for artifact in "${artifacts[@]}"; do
    install -m 0644 "${artifact}" "${OUTPUT_DIR}/"
done

(
    cd "${OUTPUT_DIR}"
    debs=( ./*.deb ./*.ddeb )
    if [[ ${#debs[@]} -eq 0 ]]; then
        echo "Error: no .deb or .ddeb packages were produced" >&2
        exit 1
    fi
    sha256sum "${debs[@]}" > SHA256SUMS
)

chown -R "${OUTPUT_UID}:${OUTPUT_GID}" "${OUTPUT_DIR}"

echo "Built ${#artifacts[@]} artifacts in ${OUTPUT_DIR}"
echo "Package version: ${PACKAGE_VERSION}"
