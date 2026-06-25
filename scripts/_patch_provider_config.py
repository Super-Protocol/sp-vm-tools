#!/usr/bin/env python3
"""
Patch provider config YAML files for swarm-cluster.sh.

Replaces fragile awk/sed in-place YAML manipulation with a reliable
line-by-line processor that:

1. Always injects networkID (even if missing from the template)
2. Handles caBundle (multi-line PEM) correctly without duplicates
3. Updates node_name, advertise_addr, join_addresses
4. Handles pki_authority.servers for bootstrap correctly

Usage:
  python3 _patch_provider_config.py \\
    --file <path>                \\
    --node-name <name>           \\
    --advertise-ip <ip>          \\
    --join-addresses <json_array> \\
    --network-id <uuid>          \\
    [--ca-bundle <pem>]          \\
    [--role bootstrap|join]

All fields not listed above pass through unchanged.
"""

import argparse
import os
import sys
import tempfile
from typing import Optional


def find_key_prefix(line: str) -> Optional[str]:
    """Return the key name if line is a simple YAML key: value, else None."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    # Must be at some indentation level and contain ': '
    if ":" not in stripped:
        return None
    key = stripped.split(":")[0].strip()
    return key


def is_list_item(line: str) -> bool:
    """Check if line is a YAML list item (starts with '- ' after indent)."""
    stripped = line.lstrip()
    return stripped.startswith("- ")


def indent_of(line: str) -> int:
    """Return indentation level (number of leading spaces)."""
    return len(line) - len(line.lstrip())


def process_file(
    file_path: str,
    node_name: str,
    advertise_ip: str,
    join_addresses: str,
    network_id: str,
    ca_bundle: Optional[str],
    role: str,
) -> None:
    """Process a single YAML file, replacing placeholder values."""
    with open(file_path, "r") as f:
        lines = f.readlines()

    result: list[str] = []
    i = 0
    n = len(lines)

    # Tracking state
    in_swarm_db = False
    swarm_db_indent = 0
    in_pki = False
    pki_indent = 0
    network_id_found = False
    ca_bundle_found = False
    servers_found = False

    # Keys we replace inside swarm_db
    swarm_db_keys_done: set[str] = set()

    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        cur_indent = indent_of(line)

        # --- Detect top-level sections ---
        if stripped.startswith("swarm_db:"):
            in_swarm_db = True
            swarm_db_indent = cur_indent
            swarm_db_keys_done.clear()
            result.append(line)
            i += 1
            continue

        if stripped.startswith("pki_authority:"):
            in_pki = True
            pki_indent = cur_indent
            network_id_found = False
            ca_bundle_found = False
            servers_found = False
            result.append(line)
            i += 1
            continue

        # --- Exit sections when indentation returns to parent level ---
        if in_swarm_db and cur_indent <= swarm_db_indent and stripped and not stripped.startswith("#"):
            in_swarm_db = False

        if in_pki and cur_indent <= pki_indent and stripped and not stripped.startswith("#"):
            # We're leaving pki_authority — inject any missing keys before the next section
            injected = _inject_missing_pki_keys(
                result, pki_indent + 2, network_id_found, ca_bundle_found, servers_found,
                network_id, ca_bundle, role,
            )
            network_id_found = True  # mark as done so END block doesn't inject again
            ca_bundle_found = True
            servers_found = True
            in_pki = False
            # result already has injected lines, continue processing current line
            result.append(line)
            i += 1
            continue

        # --- Process swarm_db keys ---
        if in_swarm_db:
            key = find_key_prefix(line)
            if key == "node_name" and "node_name" not in swarm_db_keys_done:
                new_indent = indent_of(line)
                result.append(f"{' ' * new_indent}node_name: \"{node_name}\"\n")
                swarm_db_keys_done.add("node_name")
                i += 1
                continue
            elif key == "advertise_addr" and "advertise_addr" not in swarm_db_keys_done:
                new_indent = indent_of(line)
                result.append(f"{' ' * new_indent}advertise_addr: \"{advertise_ip}\"\n")
                swarm_db_keys_done.add("advertise_addr")
                i += 1
                continue
            elif key == "join_addresses" and "join_addresses" not in swarm_db_keys_done:
                new_indent = indent_of(line)
                result.append(f"{' ' * new_indent}join_addresses: {join_addresses}\n")
                swarm_db_keys_done.add("join_addresses")
                i += 1
                continue

        # --- Process pki_authority keys ---
        if in_pki:
            key = find_key_prefix(line)
            child_indent = pki_indent + 2  # expected indent for pki_authority children

            if key == "networkID" and not network_id_found:
                result.append(f"{' ' * child_indent}networkID: \"{network_id}\"\n")
                network_id_found = True
                i += 1
                continue

            elif key == "caBundle":
                ca_bundle_found = True
                # Skip the original caBundle block entirely
                if role == "bootstrap":
                    # Bootstrap: remove caBundle block
                    i = _skip_multiline_block(lines, i + 1, child_indent)
                    continue
                else:
                    # Join node: replace with our CA bundle
                    if ca_bundle:
                        result.append(f"{' ' * child_indent}caBundle: |\n")
                        for pem_line in ca_bundle.strip().split("\n"):
                            result.append(f"{' ' * (child_indent + 2)}{pem_line}\n")
                    else:
                        # Keep original caBundle if no override
                        result.append(line)
                        i += 1
                        continue
                    i = _skip_multiline_block(lines, i + 1, child_indent)
                    continue

            elif key == "servers" and role == "bootstrap":
                servers_found = True
                # Bootstrap: replace with empty servers list
                result.append(f"{' ' * child_indent}servers: []\n")
                i = _skip_multiline_block(lines, i + 1, child_indent)
                continue

        result.append(line)
        i += 1

    # --- End of file: inject missing pki_authority keys if section never closed ---
    if in_pki:
        _inject_missing_pki_keys(
            result, pki_indent + 2, network_id_found, ca_bundle_found, servers_found,
            network_id, ca_bundle, role,
        )

    # Write back
    with open(file_path, "w") as f:
        f.writelines(result)


def _skip_multiline_block(lines: list[str], start: int, parent_indent: int) -> int:
    """Skip over a multi-line YAML block (indented content).
    Returns the index of the first line NOT in the block."""
    i = start
    while i < len(lines):
        stripped = lines[i].lstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if indent_of(lines[i]) > parent_indent:
            i += 1
        else:
            break
    return i


def _inject_missing_pki_keys(
    result: list[str],
    child_indent: int,
    network_id_found: bool,
    ca_bundle_found: bool,
    servers_found: bool,
    network_id: str,
    ca_bundle: Optional[str],
    role: str,
) -> None:
    """Inject pki_authority keys that were missing from the template."""
    pref = " " * child_indent

    if not network_id_found:
        result.append(f"{pref}networkID: \"{network_id}\"\n")

    if not ca_bundle_found:
        if role == "bootstrap":
            # Bootstrap doesn't need caBundle
            pass
        elif ca_bundle:
            result.append(f"{pref}caBundle: |\n")
            for pem_line in ca_bundle.strip().split("\n"):
                result.append(f"{' ' * (child_indent + 2)}{pem_line}\n")

    if not servers_found and role == "bootstrap":
        result.append(f"{pref}servers: []\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Patch provider config YAML")
    parser.add_argument("--file", required=True, help="Path to YAML file")
    parser.add_argument("--node-name", required=True)
    parser.add_argument("--advertise-ip", required=True)
    parser.add_argument("--join-addresses", required=True, help="JSON array string, e.g. '[]' or '[\"10.0.0.10:7946\"]'")
    parser.add_argument("--network-id", required=True)
    parser.add_argument("--ca-bundle", default=None, help="PEM CA certificate for join nodes")
    parser.add_argument("--role", default="join", choices=["bootstrap", "join"])
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print(f"ERROR: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    process_file(
        file_path=args.file,
        node_name=args.node_name,
        advertise_ip=args.advertise_ip,
        join_addresses=args.join_addresses,
        network_id=args.network_id,
        ca_bundle=args.ca_bundle,
        role=args.role,
    )


if __name__ == "__main__":
    main()
