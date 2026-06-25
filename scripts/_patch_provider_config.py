#!/usr/bin/env python3
"""Patch provider config YAML for swarm-cluster.sh."""
import argparse, json, sys
from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("--node-name", required=True)
    p.add_argument("--advertise-ip", required=True)
    p.add_argument("--join-addresses", required=True)  # JSON array
    p.add_argument("--network-id", required=True)
    p.add_argument("--ca-bundle", default=None)
    p.add_argument("--role", default="join", choices=["bootstrap", "join"])
    a = p.parse_args()

    with open(a.file) as f:
        doc = yaml.load(f)
    if not isinstance(doc, dict):
        sys.exit(0)  # not a mapping; pass through

    # swarm_db
    if "swarm_db" in doc and isinstance(doc["swarm_db"], dict):
        sd = doc["swarm_db"]
        sd["node_name"] = a.node_name
        sd["advertise_addr"] = a.advertise_ip
        sd["join_addresses"] = json.loads(a.join_addresses)

    # pki_authority (top-level section only — never touches tags.pki_authority)
    if "pki_authority" in doc and isinstance(doc["pki_authority"], dict):
        pki = doc["pki_authority"]
        pki["networkID"] = a.network_id
        if a.role == "bootstrap":
            pki["servers"] = []
            pki.pop("caBundle", None)
        elif a.ca_bundle:
            from ruamel.yaml.scalarstring import LiteralScalarString
            pki["caBundle"] = LiteralScalarString(a.ca_bundle.strip() + "\n")

    with open(a.file, "w") as f:
        yaml.dump(doc, f)

if __name__ == "__main__":
    main()