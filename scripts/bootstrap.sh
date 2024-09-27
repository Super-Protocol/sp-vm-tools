#!/bin/bash

install_debs() {
  DEB_DIR=$1
  # Install dependencies
  apt update && DEBIAN_FRONTEND=noninteractive apt install -y libslirp0

  # Install deb packages
  echo "Installing deb packages..."
  DEBIAN_FRONTEND=noninteractiv dpkg -i "${DEB_DIR}"/*.deb

  # Check for installation errors
  if [ $? -ne 0 ]; then
    echo "Failed to install some deb packages."
    exit 1
  fi
}

setup_attestation() {
  TMP_DIR=$1
   # Download the setup-attestation-host.sh script
  git clone -b noble-24.04 --single-branch --depth 1 --no-tags https://github.com/canonical/tdx.git "${TMP_DIR}/tdx-cannonical"
  SCRIPT_PATH=${TMP_DIR}/tdx-cannonical/attestation/setup-attestation-host.sh

  # Check for download errors
  if [ $? -ne 0 ]; then
    echo "Failed to download the setup-attestation-host.sh script."
    exit 1
  fi

  # Make the script executable
  chmod +x "${SCRIPT_PATH}"

  # Run the script
  echo "Running setup-attestation-host.sh..."
  "${SCRIPT_PATH}"

  # Change pccs url from local to public
  echo "Configuring pccs service..."
  cp /etc/sgx_default_qcnl.conf /etc/sgx_default_qcnl.conf.bak
  sed -i 's|"pccs_url": "https://localhost:8081/sgx/certification/v4/"|"pccs_url": "https://pccs.superprotocol.io/sgx/certification/v4/"|' /etc/sgx_default_qcnl.conf
}

setup_grub() {
  if ! grep -q 'kvm_intel.tdx=on' /etc/default/grub; then
    sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)/\1 nohibernate kvm_intel.tdx=on/' /etc/default/grub
  fi

  update-grub
}

update_tdx_module() {
  TMP_DIR=$1
  echo "Updating TDX-module..."
  pushd "${TMP_DIR}"
  wget https://github.com/intel/tdx-module/releases/download/TDX_1.5.05/intel_tdx_module.tar.gz
  tar -xvzf intel_tdx_module.tar.gz
  mkdir -p /boot/efi/EFI/TDX/
  cp -vf TDX-Module/intel_tdx_module.so /boot/efi/EFI/TDX/TDX-SEAM.so
  cp -vf TDX-Module/intel_tdx_module.so.sigstruct /boot/efi/EFI/TDX/TDX-SEAM.so.sigstruct
  popd
}

setup_nvidia_gpus() {

}


bootstrap() {
  # Check if the script is running as root
  if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root. Please run with sudo."
    exit 1
  fi

  # Check for the parameter
  if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <path_to_archive>"
    exit 1
  fi

  ARCHIVE_PATH="$1"

  # Check if the archive exists
  if [ ! -f "${ARCHIVE_PATH}" ]; then
    echo "Archive not found: ${ARCHIVE_PATH}"
    exit 1
  fi

  # Create a temporary directory for extraction
  TMP_DIR=$(mktemp -d)
  DEB_DIR="${TMP_DIR}/package"
  mkdir -p "${DEB_DIR}"

  # Extract the archive to the temporary directory
  echo "Extracting archive..."
  tar -xf "${ARCHIVE_PATH}" -C "${DEB_DIR}"

  # Clean up temporary directory
  echo "Cleaning up..."
  rm -rf "${TMP_DIR}"

  install_debs "${DEB_DIR}"
  setup_attestation "${TMP_DIR}"
  setup_grub
  update_tdx_module "${TMP_DIR}"
  setup_nvidia_gpus "${TMP_DIR}"

  echo "Installation and setup completed successfully. Please reboot your server"
}

bootstrap()
