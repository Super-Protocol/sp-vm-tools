#!/usr/bin/env bash
# Manage the OpenStack installation and compute membership; not guest resources.
# Keep host BIOS/TDX/SEV/libvirt preparation in the separate bootstrap scripts.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# == 0 || "${1:-}" == --help || "${1:-}" == -h ]]; then
    printf '%s\n' 'Usage: spvm-openstack.sh COMMAND [options]' '' \
        '  install         Install infrastructure; computes start UNCONFIGURED/disabled' \
        '  add-compute     Add new or retired computes without allocating resources' \
        '  remove-compute Retire an empty compute-only host' '' \
        'Use COMMAND --help for its options.' \
        'Resources and Enable/Disable are managed afterward through GUI or openstack spvm host.' \
        'Diagnostic backend commands: --installation-kit-dir PATH -- COMMAND [options].'
    exit 0
fi
if [[ "${1:-}" == install ]]; then
    shift
    exec "${SPVM_INSTALLER_PYTHON:-python3}" "${SCRIPT_DIR}/openstack/openstack_install_entry.py" "$@"
fi
if [[ "${1:-}" == remove-compute ]]; then
    shift
    exec "${SPVM_INSTALLER_PYTHON:-python3}" "${SCRIPT_DIR}/openstack/openstack_remove_entry.py" "$@"
fi
if [[ "${1:-}" == add-compute ]]; then
    shift
    exec "${SPVM_INSTALLER_PYTHON:-python3}" "${SCRIPT_DIR}/openstack/openstack_add_entry.py" "$@"
fi
exec "${SPVM_INSTALLER_PYTHON:-python3}" "${SCRIPT_DIR}/openstack/openstack_installer.py" "$@"
