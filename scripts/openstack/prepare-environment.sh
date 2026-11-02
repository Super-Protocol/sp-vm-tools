#!/usr/bin/env bash
# Local operator environment only: no host preparation or privilege escalation.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo 'ERROR: python3 is required (Ubuntu: install python3 and python3-venv).' >&2
    exit 1
fi
exec python3 -I -B "${SCRIPT_DIR}/prepare_environment.py" "$@"
