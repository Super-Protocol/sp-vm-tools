#!/bin/bash

set -a
docker build -f ./Dockerfile --tag temp-linux_builder ..
id=$(docker create temp-linux_builder)
mkdir -p ../dist
docker cp $id:/app/dist/ca-initializer ../dist/ca-initializer-linux
docker rm $id
