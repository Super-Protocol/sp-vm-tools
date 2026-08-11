#!/bin/bash

set -euo pipefail

readonly LIBVIRT_VERSION=${1:?libvirt version is required}
readonly DEBIAN_REVISION=${2:?Debian revision is required}
readonly PACKAGING_COMMIT=${3:?packaging commit is required}
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
