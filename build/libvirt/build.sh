#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

LIBVIRT_VERSION=12.5.0
DEBIAN_REVISION=1
SPVM_REVISION=2
PACKAGING_COMMIT=a8f73eb070c24b72f9d6dfbeffc28a334f29e076
OUTPUT_ROOT="${SCRIPT_DIR}/out"
DOCKER_PLATFORM=linux/amd64
SKIP_TESTS=false
NO_CACHE=false
PULL_BASE=false
TARGET=""

usage() {
    cat <<'EOF'
Build local libvirt Debian packages for Ubuntu 24.04 and/or 26.04.

Usage:
  ./build/libvirt/build.sh <ubuntu24|ubuntu26|all> [options]

Options:
  --libvirt-version VERSION   Upstream libvirt version (default: 12.5.0)
  --debian-revision NUMBER    Debian packaging revision (default: 1)
  --spvm-revision NUMBER      Local package revision (default: 2)
  --packaging-commit SHA      Immutable Debian packaging commit
  --output DIR                Artifact root (default: build/libvirt/out)
  --platform PLATFORM         Docker platform (default: linux/amd64)
  --skip-tests                Set DEB_BUILD_OPTIONS=nocheck
  --no-cache                  Rebuild the Docker image without cache
  --pull                      Pull the latest Ubuntu base image
  -h, --help                  Show this help

The script only builds local artifacts. It does not install or publish them.
EOF
}

if [[ $# -eq 0 ]]; then
    usage >&2
    exit 2
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

TARGET=$1
shift

while [[ $# -gt 0 ]]; do
    case "$1" in
        --libvirt-version)
            [[ $# -ge 2 ]] || { echo "Error: --libvirt-version requires a value" >&2; exit 2; }
            LIBVIRT_VERSION=$2
            shift 2
            ;;
        --debian-revision)
            [[ $# -ge 2 ]] || { echo "Error: --debian-revision requires a value" >&2; exit 2; }
            DEBIAN_REVISION=$2
            shift 2
            ;;
        --spvm-revision)
            [[ $# -ge 2 ]] || { echo "Error: --spvm-revision requires a value" >&2; exit 2; }
            SPVM_REVISION=$2
            shift 2
            ;;
        --packaging-commit)
            [[ $# -ge 2 ]] || { echo "Error: --packaging-commit requires a value" >&2; exit 2; }
            PACKAGING_COMMIT=$2
            shift 2
            ;;
        --output)
            [[ $# -ge 2 ]] || { echo "Error: --output requires a value" >&2; exit 2; }
            OUTPUT_ROOT=$2
            shift 2
            ;;
        --platform)
            [[ $# -ge 2 ]] || { echo "Error: --platform requires a value" >&2; exit 2; }
            DOCKER_PLATFORM=$2
            shift 2
            ;;
        --skip-tests)
            SKIP_TESTS=true
            shift
            ;;
        --no-cache)
            NO_CACHE=true
            shift
            ;;
        --pull)
            PULL_BASE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "${TARGET}" in
    ubuntu24)
        targets=(ubuntu24)
        ;;
    ubuntu26)
        targets=(ubuntu26)
        ;;
    all)
        targets=(ubuntu24 ubuntu26)
        ;;
    *)
        echo "Error: target must be ubuntu24, ubuntu26, or all" >&2
        usage >&2
        exit 2
        ;;
esac

if ! [[ "${LIBVIRT_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: invalid libvirt version: ${LIBVIRT_VERSION}" >&2
    exit 2
fi
if ! [[ "${DEBIAN_REVISION}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: Debian revision must be a positive integer" >&2
    exit 2
fi
if ! [[ "${SPVM_REVISION}" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: SPVM revision must be a positive integer" >&2
    exit 2
fi
if ! [[ "${PACKAGING_COMMIT}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Error: packaging commit must be a full lowercase Git SHA" >&2
    exit 2
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed" >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Error: cannot connect to the Docker daemon" >&2
    exit 1
fi

mkdir -p "${OUTPUT_ROOT}"
OUTPUT_ROOT=$(realpath "${OUTPUT_ROOT}")

build_target() {
    local target=$1 ubuntu_version image_name output_dir package_version
    local -a build_args
    case "${target}" in
        ubuntu24)
            ubuntu_version=24.04
            ;;
        ubuntu26)
            ubuntu_version=26.04
            ;;
    esac

    image_name="sp-vm-libvirt-builder:ubuntu${ubuntu_version}-${LIBVIRT_VERSION}-${DEBIAN_REVISION}-spvm${SPVM_REVISION}"
    package_version="${LIBVIRT_VERSION}-${DEBIAN_REVISION}spvm${SPVM_REVISION}~ubuntu${ubuntu_version}.1"
    output_dir="${OUTPUT_ROOT}/ubuntu-${ubuntu_version}/${package_version}"
    rm -rf -- "${output_dir}"
    mkdir -p "${output_dir}"

    build_args=(
        build
        --platform "${DOCKER_PLATFORM}"
        --file "${SCRIPT_DIR}/Dockerfile.ubuntu"
        --tag "${image_name}"
        --build-arg "UBUNTU_VERSION=${ubuntu_version}"
        --build-arg "LIBVIRT_VERSION=${LIBVIRT_VERSION}"
        --build-arg "DEBIAN_REVISION=${DEBIAN_REVISION}"
        --build-arg "PACKAGING_COMMIT=${PACKAGING_COMMIT}"
    )
    if [[ "${NO_CACHE}" == "true" ]]; then
        build_args+=(--no-cache)
    fi
    if [[ "${PULL_BASE}" == "true" ]]; then
        build_args+=(--pull)
    fi
    build_args+=("${SCRIPT_DIR}")

    echo "Building Docker image ${image_name}"
    docker "${build_args[@]}"

    echo "Building libvirt packages for Ubuntu ${ubuntu_version}"
    docker run \
        --rm \
        --platform "${DOCKER_PLATFORM}" \
        --env "LIBVIRT_VERSION=${LIBVIRT_VERSION}" \
        --env "DEBIAN_REVISION=${DEBIAN_REVISION}" \
        --env "SPVM_REVISION=${SPVM_REVISION}" \
        --env "TARGET_UBUNTU_VERSION=${ubuntu_version}" \
        --env "SKIP_TESTS=${SKIP_TESTS}" \
        --env "OUTPUT_UID=$(id -u)" \
        --env "OUTPUT_GID=$(id -g)" \
        --volume "${output_dir}:/out" \
        "${image_name}"

    echo "Artifacts: ${output_dir}"
}

for target in "${targets[@]}"; do
    build_target "${target}"
done
