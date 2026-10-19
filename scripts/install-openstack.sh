#!/usr/bin/env bash
# Keep host BIOS/TDX/SEV/libvirt preparation in the separate bootstrap scripts.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${SPVM_INSTALLER_PYTHON:-python3}" "${SCRIPT_DIR}/openstack/openstack_installer.py" "$@"
