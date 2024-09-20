FROM ubuntu:24.04

RUN apt update
RUN DEBIAN_FRONTEND=noninteractive apt install -y build-essential libncurses-dev bison flex libssl-dev libelf-dev \
    debhelper-compat=12 meson ninja-build libglib2.0-dev python3-pip nasm iasl \
    git bc cpio kmod rsync zstd checkinstall libslirp-dev

RUN git config --global user.email "youremail@yourdomain.com"
RUN git config --global user.name "Your Name"
