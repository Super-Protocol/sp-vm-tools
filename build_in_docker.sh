#!/bin/bash
IMAGE_NAME=tdx-builder
scripts_dir="$( cd "$( dirname "$0" )" && pwd )"

if [[ "$(docker images -q ${IMAGE_NAME} 2> /dev/null)" == "" || "${FORCE_REBUILD_CONTAINER}" == "1" ]]; then
  echo "Docker image ${IMAGE_NAME} not found or FORCE_REBUILD_CONTAINER is set. Building..."
  docker build -t ${IMAGE_NAME} .
else
  echo "Docker image ${IMAGE_NAME} already exists."
fi

if [[ -n "$NON_INTERACTIVE" ]]; then
  IT=""
else
  IT="-it"
fi

BUILD_TYPE=${1:-"tdx snp"}
DOCKER_CMD="docker run ${IT} --rm -v ${scripts_dir}:/builder --entrypoint /bin/bash ${IMAGE_NAME}"

if [[ $BUILD_TYPE == *"tdx"* ]]; then
  ${DOCKER_CMD} -c "source /builder/scripts/build_tdx.sh && build_main"
  if ! ${DOCKER_CMD} -c "source /builder/scripts/build_tdx.sh && build_main"; then
    echo "Error: TDX build failed" >&2
    exit 1
  fi
fi

if [[ $BUILD_TYPE == *"snp"* ]]; then
  ${DOCKER_CMD} -c "source /builder/scripts/build_snp.sh && build_main"
  if ! ${DOCKER_CMD} -c "source /builder/scripts/build_snp.sh && build_main"; then
    echo "Error: SNP build failed" >&2
    exit 1
  fi
fi
