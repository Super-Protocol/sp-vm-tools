#!/bin/bash

build_kernel_packages() {
    local build_dir=$1
    local config=$2
    pushd ${build_dir}

    git clone -b ${KERNEL_HOST_BRANCH} --single-branch --depth 1 --no-tags ${KERNEL_GIT_URL} host-kernel
    cd host-kernel
    cp -v ${config} .config

    VER="-snp-host"
    COMMIT=$(git log --format="%h" -1 HEAD)

    ./scripts/config --set-str LOCALVERSION "$VER-$COMMIT"
    ./scripts/config --disable LOCALVERSION_AUTO
    ./scripts/config --enable  EXPERT
    ./scripts/config --enable  DEBUG_INFO
    ./scripts/config --enable  DEBUG_INFO_REDUCED
    ./scripts/config --enable  AMD_MEM_ENCRYPT
    ./scripts/config --disable AMD_MEM_ENCRYPT_ACTIVE_BY_DEFAULT
    ./scripts/config --enable  KVM_AMD_SEV
    ./scripts/config --module  CRYPTO_DEV_CCP_DD
    ./scripts/config --disable SYSTEM_TRUSTED_KEYS
    ./scripts/config --disable SYSTEM_REVOCATION_KEYS
    ./scripts/config --disable MODULE_SIG_KEY
    ./scripts/config --module  SEV_GUEST
    ./scripts/config --disable IOMMU_DEFAULT_PASSTHROUGH
    ./scripts/config --disable PREEMPT_COUNT
    ./scripts/config --disable PREEMPTION
    ./scripts/config --disable PREEMPT_DYNAMIC
    ./scripts/config --disable DEBUG_PREEMPT
    ./scripts/config --enable  CGROUP_MISC
    ./scripts/config --module  X86_CPUID
    ./scripts/config --disable UBSAN
    ./scripts/config --set-val RCU_EXP_CPU_STALL_TIMEOUT 1000
    ./scripts/config --disable MLX4_EN
    ./scripts/config --module MLX4_EN
    ./scripts/config --enable MLX4_EN_DCB
    ./scripts/config --module MLX4_CORE
    ./scripts/config --enable MLX4_DEBUG
    ./scripts/config --enable MLX4_CORE_GEN2
    ./scripts/config --module MLX5_CORE
    ./scripts/config --enable MLX5_FPGA
    ./scripts/config --enable MLX5_CORE_EN
    ./scripts/config --enable MLX5_EN_ARFS
    ./scripts/config --enable MLX5_EN_RXNFC
    ./scripts/config --enable MLX5_MPFS
    ./scripts/config --enable MLX5_ESWITCH
    ./scripts/config --enable MLX5_BRIDGE
    ./scripts/config --enable MLX5_CLS_ACT
    ./scripts/config --enable MLX5_TC_CT
    ./scripts/config --enable MLX5_TC_SAMPLE
    ./scripts/config --enable MLX5_CORE_EN_DCB
    ./scripts/config --enable MLX5_CORE_IPOIB
    ./scripts/config --enable MLX5_SW_STEERING
    ./scripts/config --module MLXSW_CORE
    ./scripts/config --enable MLXSW_CORE_HWMON
    ./scripts/config --enable MLXSW_CORE_THERMAL
    ./scripts/config --module MLXSW_PCI
    ./scripts/config --module MLXSW_I2C
    ./scripts/config --module MLXSW_SPECTRUM
    ./scripts/config --enable MLXSW_SPECTRUM_DCB
    ./scripts/config --module MLXSW_MINIMAL
    ./scripts/config --module MLXFW

    yes "" | make olddefconfig
    make -j$(nproc) deb-pkg
    popd
}

build_ovmf() {
    local build_dir=$1
    pushd ${build_dir}
    git clone --single-branch -b ${OVMF_BRANCH} ${OVMF_GIT_URL} ovmf
    cd ovmf
    git config --global url.https://github.com/tianocore/edk2-subhook.git.insteadOf https://github.com/Zeex/subhook.git
    git submodule update --init --recursive
     rm -rf Build
    make -C BaseTools

    set -- #workaround for source from function with arg
    source edksetup.sh

    cat <<-EOF > Conf/target.txt
ACTIVE_PLATFORM = OvmfPkg/OvmfPkgX64.dsc
TARGET = RELEASE
TARGET_ARCH = X64
TOOL_CHAIN_CONF = Conf/tools_def.txt
TOOL_CHAIN_TAG = GCC5
BUILD_RULE_CONF = Conf/build_rule.txt
MAX_CONCURRENT_THREAD_NUMBER = $(nproc)
EOF

    build clean
    build
    if [ ! -f Build/OvmfX64/RELEASE_GCC5/FV/OVMF.fd ]; then
        echo "Build failed, OVMF.fd not found"
        return 1
    fi
    popd
}

build_qemu() {
    local build_dir=$1
    pushd ${build_dir}
    git clone --single-branch -b ${QEMU_BRANCH} ${QEMU_GIT_URL} qemu
    cd qemu

    ./configure --enable-slirp --enable-kvm --target-list=x86_64-softmmu
    make -j$(nproc)

    # Workaround, without first calling make install, checkinstall terminates with an error
    # 1) We call make install to install files
    # 2) Create deb package and install it (default behavior)
    # 3) Remove package
    make install
    PACKAGE_NAME=sp-qemu-snp
    checkinstall -y --pkgname=${PACKAGE_NAME} --pkgversion=1.0 --requires="libslirp0" --backup=no --nodoc --maintainer=SuperProtocol
    dpkg -r ${PACKAGE_NAME}

    popd
}

packaging() {
    local build_dir=$1
    local root_dir=$2
    pushd ${build_dir}
    rm -rf ./package
    mkdir -p package
    find ./ -type f -name "*.deb" ! -name "*dbg*.deb" -exec cp -fv {} package/ \;
    cp -fv qemu/sp-qemu-snp*.deb package/
    cp -fv ovmf/Build/OvmfX64/RELEASE_GCC5/FV/OVMF.fd package/
    cp -fv ${root_dir}/sources/amd/AMDSEV/kvm.conf package/
    cd package
    tar -czvf ../package-snp.tar.gz .
    popd
}

build_main() {

    local scripts_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    local root_dir=${scripts_dir}/../
    local build_dir=${scripts_dir}/../build/snp

    source ${root_dir}/sources/amd/AMDSEV/stable-commits

    rm -rf ${build_dir}
    mkdir -p ${build_dir}

    pushd ${root_dir}
    config_file=$(find ./config -name "config-*" | head -n 1)

    if [[ -z "$config_file" ]]; then
        echo "Old config not found!"
        exit 1
    fi
    config_file_abs=$(readlink -f "${config_file}")
    popd

    build_kernel_packages ${build_dir} ${config_file_abs}
    build_qemu ${build_dir}
    build_ovmf ${build_dir}
    packaging ${build_dir} ${root_dir}
}