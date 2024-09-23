#!/bin/bash
IMAGE_NAME=tdx-builder

if [[ "$(docker images -q ${IMAGE_NAME} 2> /dev/null)" == "" ]]; then
  echo "Docker image ${IMAGE_NAME} not found. Building..."
  sudo rm -rf ./build
  docker build -t ${IMAGE_NAME} .
else
  echo "Docker image ${IMAGE_NAME} already exists."
fi

scripts_dir="$( cd "$( dirname "$0" )" && pwd )"
mkdir -p build
docker run -it --rm -v ${scripts_dir}:/builder --entrypoint /bin/bash ${IMAGE_NAME} -c "source /builder/scripts/build.sh && build_main"