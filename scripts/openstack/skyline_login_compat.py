"""Offline compatibility patch for Skyline's pre-login domain selector.

Only the login store changes. Authenticated domain enumeration stays protected.
Refuse unfamiliar assets; callers must pin and approve the original checksum.
"""
import argparse
import hashlib
import os
from pathlib import Path

OLD = b'var t=yield e.client.domains();e.domains=t'
NEW = (
    b'var t=yield fetch("/api/openstack/skyline/api/v1/config",'
    b'{credentials:"same-origin",cache:"no-store"});'
    b'if(!t.ok)throw new Error("Unable to load Skyline login configuration");'
    b'var n=yield t.json();'
    b'if("string"!=typeof n.default_domain||!n.default_domain.trim())'
    b'throw new Error("Skyline default domain is not configured");'
    b'e.domains=[n.default_domain]'
)


def patch_asset(data, expected_sha256):
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('Skyline asset checksum mismatch')
    if data.count(OLD) != 1 or NEW in data:
        raise ValueError('unsupported or already patched Skyline login asset')
    return data.replace(OLD, NEW, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(args.asset)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 8*1024*1024:
        parser.error('bounded regular asset required')
    data = patch_asset(source.read_bytes(), args.sha256)
    with Path(args.output).open('xb') as output:
        os.chmod(output.fileno(), 0o600)
        output.write(data)
    print(hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main()
