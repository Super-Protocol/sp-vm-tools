#!/bin/bash
IMAGE_NAME=tdx-builder
scripts_dir="$( cd "$( dirname "$0" )" && pwd )"

if [[ "$(docker images -q ${IMAGE_NAME} 2> /dev/null)" == "" || "${FORCE_REBUILD_CONTAINER}" == "1" ]]; then
  echo "Docker image ${IMAGE_NAME} not found or FORCE_REBUILD_CONTAINER is set. Building..."
  sudo rm -rf ${scripts_dir}/build
  docker build -t ${IMAGE_NAME} .
else
  echo "Docker image ${IMAGE_NAME} already exists."
fi

mkdir -p build
docker run -it --rm -v ${scripts_dir}:/builder --entrypoint /bin/bash ${IMAGE_NAME} -c "source /builder/scripts/build.sh && build_main"