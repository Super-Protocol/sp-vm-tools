#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
QEMU_VERSION=10.2.1
SPVM_REVISION=1
OUTPUT_ROOT="${SCRIPT_DIR}/out"
DOCKER_PLATFORM=linux/amd64
NO_CACHE=false
PULL_BASE=false

usage() {
    cat <<'EOF'
Build the pinned QEMU TDX package for Ubuntu 24.04.

Usage:
  ./build/qemu/build.sh [options]

Options:
  --spvm-revision NUMBER  CI/build revision (default: 1)
  --output DIR            Artifact directory (default: build/qemu/out)
  --platform PLATFORM     Docker platform (default: linux/amd64)
  --no-cache              Rebuild the Docker image without cache
  --pull                  Pull the latest Ubuntu 24.04 base image
  -h, --help              Show this help

The upstream QEMU version and source SHA-256 are intentionally fixed in the
builder. This command only creates local artifacts; it does not install or
publish them.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --spvm-revision)
            [[ $# -ge 2 ]] || { echo "Error: --spvm-revision requires a value" >&2; exit 2; }
            SPVM_REVISION=$2
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

[[ "${SPVM_REVISION}" =~ ^[1-9][0-9]*$ ]] || {
    echo "Error: SPVM revision must be a positive integer" >&2
    exit 2
}
command -v docker >/dev/null 2>&1 || {
    echo "Error: docker is not installed" >&2
    exit 1
}
docker info >/dev/null 2>&1 || {
    echo "Error: cannot connect to the Docker daemon" >&2
    exit 1
}

mkdir -p "${OUTPUT_ROOT}"
OUTPUT_ROOT=$(realpath "${OUTPUT_ROOT}")
output_dir="${OUTPUT_ROOT}/ubuntu-24.04"
image_name="sp-vm-qemu-builder:ubuntu24.04-${QEMU_VERSION}"

rm -rf -- "${output_dir}"
mkdir -p "${output_dir}"

build_args=(
    build
    --platform "${DOCKER_PLATFORM}"
    --file "${SCRIPT_DIR}/Dockerfile.ubuntu24"
    --tag "${image_name}"
)
[[ "${NO_CACHE}" == true ]] && build_args+=(--no-cache)
[[ "${PULL_BASE}" == true ]] && build_args+=(--pull)
build_args+=("${SCRIPT_DIR}")

echo "Building ${image_name}"
docker "${build_args[@]}"

echo "Building QEMU ${QEMU_VERSION} for Ubuntu 24.04"
docker run \
    --rm \
    --platform "${DOCKER_PLATFORM}" \
    --env "SPVM_REVISION=${SPVM_REVISION}" \
    --env "OUTPUT_UID=$(id -u)" \
    --env "OUTPUT_GID=$(id -g)" \
    --volume "${output_dir}:/out" \
    "${image_name}"

echo "Artifacts: ${output_dir}"

