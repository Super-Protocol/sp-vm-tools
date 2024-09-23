#!/bin/bash

KERNEL_CONFIG_FILE="/builder/config/config-6.8.0-45-generic"

preparePatches() {
    local build_dir=$1
    pushd ${build_dir}
    git clone https://github.com/intel/tdx-linux.git ./patches
    cd ./patches
    git checkout -b device-passthrough 1323f7b1ddf81076e3fcda6385c0c0dcf506258c
    popd
}

build_kernel_packages() {
    local build_dir=$1
    local config=$2
    pushd ${build_dir}
    git clone -b kvm-coco-queue-20240512 --single-branch --depth 1 --no-tags https://git.kernel.org/pub/scm/linux/kernel/git/vishal/kvm.git
    cd ./kvm
    cp -v ../patches/tdx-kvm/tdx_kvm_baseline_698ca1e40357.mbox .
    git am --empty=drop tdx_kvm_baseline_698ca1e40357.mbox

    cp -v ${config} .config
    return 0

    scripts/config -d KEXEC \
    -d KEXEC_FILE \
    -d SYSTEM_TRUSTED_KEYS \
    -d SYSTEM_REVOCATION_KEYS

    scripts/config -e KVM \
    -e KVM_INTEL \
    -e KVM_TDX_GUEST_DRIVER \
    -e HYPERV \
    -e INTEL_TDX_HOST \
    -e CRYPTO_ECC \
    -e CRYPTO_ECDH \
    -e CRYPTO_ECDSA \
    -e CRYPTO_ECRDSA

    make olddefconfig

    #make -j$(nproc)
    #make modules -j$(nproc)
    make -j$(nproc) deb-pkg
    popd
}

build_qemu() {
    local build_dir=$1
    pushd ${build_dir}
    git init qemu
    cd qemu
    git remote add origin https://gitlab.com/qemu-project/qemu
    git fetch --depth 1 origin ff6d8490e33acf44ed8afd549e203a42d6f813b5
    git checkout ff6d8490e33acf44ed8afd549e203a42d6f813b5

    cp -v ../patches/tdx-qemu/tdx_qemu_baseline_900536d3e9.mbox .
    git am --empty=drop tdx_qemu_baseline_900536d3e9.mbox

    ./configure --enable-slirp --enable-kvm --target-list=x86_64-softmmu
    make -j$(nproc)

    # Workaround, without first calling make install, checkinstall terminates with an error
    # 1) We call make install to install files
    # 2) Create deb package and install it (default behavior)
    # 3) Remove package
    make install
    PACKAGE_NAME=sp-qemu-tdx
    checkinstall -y --pkgname=${PACKAGE_NAME} --pkgversion=1.0 --requires="libslirp-dev" --backup=no --nodoc --maintainer=SuperProtocol
    dpkg -r ${PACKAGE_NAME}

    popd
}

build_ovmf() {
    pushd ${BUILD_DIR}
    git clone -b edk2-stable202405 --single-branch --depth 1 --no-tags https://github.com/tianocore/edk2
    cd edk2
    git submodule update --init
    rm -rf Build
    make -C BaseTools
    . edksetup.sh
    cat <<-EOF > Conf/target.txt
ACTIVE_PLATFORM = OvmfPkg/OvmfPkgX64.dsc
TARGET = DEBUG
TARGET_ARCH = X64
TOOL_CHAIN_CONF = Conf/tools_def.txt
TOOL_CHAIN_TAG = GCC5
BUILD_RULE_CONF = Conf/build_rule.txt
MAX_CONCURRENT_THREAD_NUMBER = $(nproc)
EOF

    build clean
    build
    if [ ! -f Build/OvmfX64/DEBUG_GCC5/FV/OVMF.fd ]; then
        echo "Build failed, OVMF.fd not found"
    fi
}
tmp_dir=$(mktemp -d)
mkdir -p ${tmp_dir}
preparePatches ${tmp_dir}
build_kernel_packages ${tmp_dir} $(readlink -f "/builder/config/config-6.8.0-45-generic")
#build_qemu ${tmp_dir}
#build_ovmf ${tmp_dir}