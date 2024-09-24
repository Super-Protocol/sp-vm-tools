#!/bin/bash

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
if [ ! -f "$ARCHIVE_PATH" ]; then
  echo "Archive not found: $ARCHIVE_PATH"
  exit 1
fi

# Create a temporary directory for extraction
TMP_DIR=$(mktemp -d)
DEB_DIR=${TMP_DIR}/package
mkdir -p ${DEB_DIR}

# Extract the archive to the temporary directory
echo "Extracting archive..."
tar -xf "$ARCHIVE_PATH" -C "$DEB_DIR"

# Install dependencies
apt update && DEBIAN_FRONTEND=noninteractive apt install -y libslirp0

# Install deb packages
echo "Installing deb packages..."
DEBIAN_FRONTEND=noninteractiv dpkg -i "$DEB_DIR"/*.deb

# Check for installation errors
if [ $? -ne 0 ]; then
  echo "Failed to install some deb packages."
  exit 1
fi

# Download the setup-attestation-host.sh script
git clone -b noble-24.04 --single-branch --depth 1 --no-tags https://github.com/canonical/tdx.git ${TMP_DIR}/tdx-cannonical
SCRIPT_PATH=${TMP_DIR}/tdx-cannonical/attestation/setup-attestation-host.sh

# Check for download errors
if [ $? -ne 0 ]; then
  echo "Failed to download the setup-attestation-host.sh script."
  exit 1
fi

# Make the script executable
chmod +x "$SCRIPT_PATH"

# Run the script
echo "Running setup-attestation-host.sh..."
"$SCRIPT_PATH"

# Clean up temporary directory
echo "Cleaning up..."
rm -rf "$TEMP_DIR"


if ! grep -q 'kvm_intel.tdx=on' /etc/default/grub; then
  sed -i 's/\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)/\1 nohibernate kvm_intel.tdx=on/' /etc/default/grub
fi

update-grub


echo "Installation and setup completed successfully. Please reboot your server"
