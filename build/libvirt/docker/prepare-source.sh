#!/bin/bash

set -euo pipefail

readonly LIBVIRT_VERSION=${1:?libvirt version is required}
readonly DEBIAN_REVISION=${2:?Debian revision is required}
readonly PACKAGING_COMMIT=${3:?packaging commit is required}
readonly LOCAL_PATCH_DIR=${4:?local patch directory is required}
readonly PACKAGING_REF="debian/${LIBVIRT_VERSION}-${DEBIAN_REVISION}"
readonly SOURCE_DIR=/opt/libvirt-source
readonly PACKAGING_REPOSITORY=https://salsa.debian.org/libvirt-team/libvirt.git

git clone \
    --branch "${PACKAGING_REF}" \
    --depth 1 \
    "${PACKAGING_REPOSITORY}" \
    "${SOURCE_DIR}"

actual_commit=$(git -C "${SOURCE_DIR}" rev-parse HEAD)
if [[ "${actual_commit}" != "${PACKAGING_COMMIT}" ]]; then
    echo "Error: ${PACKAGING_REF} resolves to ${actual_commit}, expected ${PACKAGING_COMMIT}" >&2
    exit 1
fi

actual_version=$(dpkg-parsechangelog -l"${SOURCE_DIR}/debian/changelog" -SVersion)
expected_version="${LIBVIRT_VERSION}-${DEBIAN_REVISION}"
if [[ "${actual_version}" != "${expected_version}" ]]; then
    echo "Error: ${PACKAGING_REF} contains version ${actual_version}, expected ${expected_version}" >&2
    exit 1
fi

shopt -s nullglob
local_patches=("${LOCAL_PATCH_DIR}"/*.patch)
shopt -u nullglob
if [[ ${#local_patches[@]} -eq 0 ]]; then
    echo "Error: no local libvirt patches found in ${LOCAL_PATCH_DIR}" >&2
    exit 1
fi

mkdir -p "${SOURCE_DIR}/debian/patches"
touch "${SOURCE_DIR}/debian/patches/series"
for local_patch in "${local_patches[@]}"; do
    patch_name=$(basename "${local_patch}")
    install -m 0644 \
        "${local_patch}" \
        "${SOURCE_DIR}/debian/patches/${patch_name}"
    if ! grep -qxF "${patch_name}" "${SOURCE_DIR}/debian/patches/series"; then
        printf '%s\n' "${patch_name}" >> "${SOURCE_DIR}/debian/patches/series"
    fi
    echo "Added local libvirt patch: ${patch_name}"
done

rm -rf -- "${SOURCE_DIR}/.git"

cd "${SOURCE_DIR}"
apt-get update
mk-build-deps \
    --install \
    --remove \
    --tool 'apt-get -y --no-install-recommends' \
    debian/control

apt-get clean
rm -rf /var/lib/apt/lists/*
