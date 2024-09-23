#!/bin/bash

#!/bin/bash

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

# Install deb packages
echo "Installing deb packages..."
sudo dpkg -i "$DEB_DIR"/*.deb

# Check for installation errors
if [ $? -ne 0 ]; then
  echo "Failed to install some deb packages."
  exit 1
fi

# Download the setup-attestation-host.sh script
SCRIPT_URL="https://raw.githubusercontent.com/canonical/tdx/noble-24.04/attestation/setup-attestation-host.sh"
SCRIPT_PATH="$TEMP_DIR/setup-attestation-host.sh"

echo "Downloading setup-attestation-host.sh..."
curl -L -o "$SCRIPT_PATH" "$SCRIPT_URL"

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

echo "Installation and setup completed successfully."
