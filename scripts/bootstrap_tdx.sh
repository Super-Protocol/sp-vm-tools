#!/bin/bash
set -e

source_common() {
    local script_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    source "${script_dir}/common.sh"
}

# QEMU is installed from the sp-vm-tools release archive (package-tdx.tar.gz),
# which bundles sp-qemu-tdx (QEMU 9.x + Intel TDX device-passthrough patches,
# with iommufd support). The PPA QEMU on Ubuntu 24.04 (8.2.2) lacks iommufd.
QEMU_RELEASE_REPO="Super-Protocol/sp-vm-tools"
QEMU_RELEASE_TAG="39-tdx+snp"          # newest tag carrying package-tdx.tar.gz
QEMU_RELEASE_ASSET="package-tdx.tar.gz"

# Resolve the TDX SEAM module version to install for the host CPU.
# Prints the version string (e.g. "2.0.14") to stdout; all human-readable
# progress goes to stderr so callers can capture the version cleanly.
# Returns non-zero on an unsupported (older / non-Intel) CPU.
detect_tdx_module_version() {
  local vendor family model codename version
  # Newest known TDX module branch. Used as the fallback for unrecognized CPUs
  # that appear to be newer than anything in the map below.
  local TDX_MODULE_LATEST="2.0.14"

  vendor=$(awk -F: '/^vendor_id/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo)
  family=$(awk -F: '/^cpu family/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo)
  # Match the bare "model\t\t: N" line, not "model name".
  model=$(awk -F: '/^model[[:space:]]*:/{gsub(/ /,"",$2);print $2;exit}' /proc/cpuinfo)

  if [ "$vendor" != "GenuineIntel" ]; then
    echo "ERROR: Unsupported CPU vendor '${vendor}' (Intel TDX requires GenuineIntel)" >&2
    return 1
  fi

  if [ -z "$family" ] || [ -z "$model" ]; then
    echo "ERROR: Could not determine CPU family/model from /proc/cpuinfo" >&2
    return 1
  fi

  # Anything beyond family 6 is necessarily newer than the current map.
  if [ "$family" -gt 6 ]; then
    echo "WARNING: Unrecognized newer CPU (family ${family} model ${model}); using latest TDX module ${TDX_MODULE_LATEST}" >&2
    echo "${TDX_MODULE_LATEST}"
    return 0
  fi

  if [ "$family" -ne 6 ]; then
    echo "ERROR: Unsupported CPU (family ${family} model ${model}); Intel TDX requires Sapphire Rapids or newer" >&2
    return 1
  fi

  case "$model" in
    143) codename="Sapphire Rapids"; version="1.5.24" ;;  # 0x8F
    207) codename="Emerald Rapids";  version="1.5.24" ;;  # 0xCF
    175) codename="Sierra Forest";   version="1.5.25" ;;  # 0xAF
    173) codename="Granite Rapids";  version="2.0.14" ;;  # 0xAD
    *)
      if [ "$model" -gt 175 ]; then
        echo "WARNING: Unrecognized newer CPU (family 6 model ${model}); using latest TDX module ${TDX_MODULE_LATEST}" >&2
        echo "${TDX_MODULE_LATEST}"
        return 0
      fi
      echo "ERROR: Unsupported CPU (family 6 model ${model})." >&2
      echo "Supported: Sapphire Rapids (143), Emerald Rapids (207), Sierra Forest (175), Granite Rapids (173)." >&2
      return 1
      ;;
  esac

  echo "Detected CPU: ${codename} (family ${family} model ${model}) -> TDX module ${version}" >&2
  echo "${version}"
}

update_tdx_module() {
  TMP_DIR=$1
  local version
  version=$(detect_tdx_module_version) || exit 1
  echo "Updating TDX-module to ${version}..."
  pushd "${TMP_DIR}"
  wget "https://github.com/intel/confidential-computing.tdx.tdx-module/releases/download/TDX_MODULE_${version}/intel_tdx_module.tar.gz"
  tar -xvzf intel_tdx_module.tar.gz
  mkdir -p /boot/efi/EFI/TDX/
  cp -vf TDX-Module/intel_tdx_module.so /boot/efi/EFI/TDX/TDX-SEAM.so
  cp -vf TDX-Module/intel_tdx_module.so.sigstruct /boot/efi/EFI/TDX/TDX-SEAM.so.sigstruct
  popd
}

install_qemu_from_release() {
  local tmp_dir="$1"

  # The bundled sp-qemu-tdx deb is built for Ubuntu 24.04 (Noble), whose PPA
  # QEMU (8.2.2) lacks iommufd. Newer Ubuntu releases (24.10 / 25.04+) already
  # ship QEMU >= 9 with iommufd from the archive/PPA, so the bundled deb is
  # neither needed nor binary-compatible there.
  local ubuntu_version=""
  [ -f /etc/os-release ] && ubuntu_version=$(. /etc/os-release && echo "$VERSION_ID")
  if [ "$ubuntu_version" != "24.04" ]; then
    echo "Ubuntu ${ubuntu_version:-unknown}: skipping bundled QEMU (distro QEMU >= 9 with iommufd is used)"
    return 0
  fi

  local work="${tmp_dir}/qemu-pkg"
  # URL-encode the '+' in the tag for the direct download URL.
  local tag_enc="${QEMU_RELEASE_TAG//+/%2B}"
  local url="https://github.com/${QEMU_RELEASE_REPO}/releases/download/${tag_enc}/${QEMU_RELEASE_ASSET}"

  echo "Installing QEMU from ${QEMU_RELEASE_REPO}@${QEMU_RELEASE_TAG}..."
  mkdir -p "${work}"
  echo "Downloading ${QEMU_RELEASE_ASSET} (~160 MB), this may take a while..."
  wget -O "${work}/${QEMU_RELEASE_ASSET}" "${url}"
  echo "Download complete: ${work}/${QEMU_RELEASE_ASSET}"
  tar -xzf "${work}/${QEMU_RELEASE_ASSET}" -C "${work}"

  # The archive contains several debs (kernel, qemu, OVMF). Install ONLY QEMU.
  local qemu_deb
  qemu_deb=$(find "${work}" -maxdepth 2 -name 'sp-qemu-tdx*.deb' | head -n1)
  if [ -z "${qemu_deb}" ]; then
    echo -e "${RED}ERROR: sp-qemu-tdx*.deb not found in ${QEMU_RELEASE_ASSET}${NC}"
    exit 1
  fi
  echo "Installing QEMU package: $(basename "${qemu_deb}")"
  # apt resolves the deb's deps (e.g. libslirp0); installs into /usr/local/bin.
  # APT::Sandbox::User=root: the deb lives in a root-only mktemp dir that the
  # unprivileged '_apt' user cannot read, which otherwise triggers a sandbox
  # permission warning.
  if ! apt-get install -y -o APT::Sandbox::User=root "${qemu_deb}"; then
    echo -e "${RED}ERROR: Failed to install QEMU package${NC}"
    exit 1
  fi
}

bootstrap() {
    check_os_version "24.04"

    # Check if the script is running as root
    print_section_header "Privilege Check"
    if [ "$(id -u)" -ne 0 ]; then
        echo "This script must be run as root. Please run with sudo."
        exit 1
    fi

    # Download and setup official Canonical TDX
    print_section_header "Official TDX Setup"
    TMP_DIR=$(mktemp -d)

    echo "Installing required tools..."
    apt update && apt install -y unzip wget

    if [ -f "$(dirname "${BASH_SOURCE[0]}")/setup_tdx.sh" ]; then
        echo "Running TDX setup script..."
        cp "$(dirname "${BASH_SOURCE[0]}")/setup_tdx.sh" "${TMP_DIR}/"
        chmod +x "${TMP_DIR}/setup_tdx.sh"
        if ! "${TMP_DIR}/setup_tdx.sh"; then
            echo -e "${RED}ERROR: TDX setup failed${NC}"
            exit 1
        fi
    else 
        echo -e "${RED}ERROR: setup_tdx.sh not found${NC}"
        exit 1
    fi
    
    print_section_header "QEMU Installation"
    install_qemu_from_release "${TMP_DIR}"

    print_section_header "TDX Module Update"
    echo "Updating TDX module..."
    update_tdx_module "${TMP_DIR}"

    print_section_header "Hardware Configuration"
    if command -v lspci >/dev/null; then
        echo "Checking NVIDIA GPU configuration..."
        setup_nvidia_gpus "${TMP_DIR}" || true
        setup_cx7_bridge_vfio
        verify_cx7_vfio_setup
    else
        echo "Skipping NVIDIA GPU check (lspci not found)"
    fi    

    # Clean up temporary directory
    print_section_header "Cleanup"
    echo "Cleaning up..."
    rm -rf "${TMP_DIR}"

    print_section_header "Installation Status"
    echo "Official TDX installation complete."
    echo "System reboot required to activate TDX."
    echo "After reboot, use official tools to create and run TDs."
}

source_common

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Script was sourced"
else
  bootstrap "$@"
fi
