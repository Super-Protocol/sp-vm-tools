# Shared-interface OpenStack (single controller, no VPN)

This explicit fresh-install profile uses the existing host IPv4 interface for
SSH, OpenStack control traffic, OVN/Geneve and Internet egress. It creates neither
a private inter-host network nor WireGuard. Enable it with:

```yaml
network:
  management_mode: shared-interface
  mode: host-nat
  api_vip: 192.0.2.10
  ingress_host: controller
  public_ip: 192.0.2.10
  transit_cidr: 172.30.79.0/24
```

These documentation-only addresses are not a deployable configuration. Set
`management_interface` and `tunnel_interface` to each host's actual existing
interface and use its verified address. The `api_vip` field in this profile
means the controller's **already assigned address**, not an additional VIP.

SSH key/trust paths are local CLI settings, not deployment YAML fields. Optional
`--identity-file` / `--known-hosts` overrides go before the installer's `--`
separator. If omitted, the invoking user's standard keys/agent and known_hosts
are used, including when the coordinator runs through sudo. Strict host-key
checking and no forwarding remain enforced; arbitrary SSH-config execution is
not enabled. See the main deployment guide's local SSH settings section.

## Boundary and prerequisites

- Exactly one controller, network and ingress node, all the same host. Compute
  roles may be colocated. HA, an external/provider bridge and IPv6 management are
  outside this initial profile. The default profile remains `dedicated`.
- Each declared interface must already be UP with exactly one usable IPv4 address
  matching its host's SSH address. Management/tunnel interfaces must be identical.
  No additional API IP, dummy management interface, VPN or VLAN is required.
- Compute-to-network-node guest probe routes require an on-link controller
  address. Routed public peers outside that connected subnet are not supported
  by this host-NAT profile. Addresses/prefixes and machine hashes come from a
  fresh audit, not guessed defaults.
- Management requests still cover every host. In
  `openstack-host-network.example.json`, replace each `management_address` with
  the actual host address/prefix, set `route_interface` to the verified interface
  and `transport_unit: null`. The request's
  machine identities must be audited again after any OS reinstall.
- Use matching updated installer and spvm-openstack backend components. An older
  published bundle does not understand `management_mode`; do not edit its
  verified manifest or assume that a frontend update changes an old backend.
- Complete the updated TDX/SNP/non-CC host bootstrap before helper provisioning.
  All three bootstraps prepare launcher tools (`uplink`, `nc`, `zstd`, `curl`,
  `jq`, `unzip`). Missing Uplink is installed from the pinned, SHA256-verified
  artifact declared by bootstrap; an existing executable is preserved and checked. The helper
  preparation rejects missing tools before installing services. Its sandbox
  deliberately makes `/usr` read-only: starting a VM is not a host-package
  installation step. Updating only the GUI or driver cannot supply these tools.

## What preparation changes (and what it preserves)

The existing `plan-management` / `prepare-management` flow installs root-owned
policies and a persistent management oneshot. Shared mode installs scoped IPv4
and IPv6 INPUT guards. `plan-nat` / `prepare-nat` subsequently prepares the
existing isolated `spvm-ext0`/`spvm-nat0` transit and host-NAT/probe routes. The
transit is part of Neutron egress, **not** another management network or VPN.

No public IP, prefix, MTU, bond membership, VLAN, default route, Netplan or network
manager configuration is changed. The public interface is never attached to OVS.
The only added routes are the reviewed guest probe routes; only the isolated
transit veth pair is created. No host network service, guest or existing
OpenStack service is restarted by this preparation. Unowned firewall chains and
the FORWARD default policy are preserved; partial ownership/conflicts stop apply.

The coordinator must not invoke dedicated dummy-interface networkd cache repair
for this profile, even when `repair_stale_networkd_cache` is enabled in the
preparation request. A primary interface legitimately carries a default route,
and its existing API address retains the original prefix rather than becoming a
separate `/32` VIP. Shared-interface address validation remains mandatory in the
owned management phase; skipping dummy repair does not skip that validation.

## Firewall and security implications

IPv4 guards apply to the configured host destination address on every inbound
path, including guest-to-host traffic through the NAT veth; loopback traffic is
preserved for local services and SSH tunnels. IPv6 guards cover the declared
interface without adding an IPv6 control plane:

- Existing established/related flows are preserved.
- Declared OpenStack peer IPv4 addresses may initiate connections.
- TCP 22, 80 and 443 remain subject to existing firewall policy; the guard does
  not block administrative SSH or public Swarm HTTP/HTTPS ingress.
- Other incoming TCP/UDP connections from non-peers are dropped, including
  Skyline, DB, RabbitMQ, memcached and API backend ports. No new inbound IPv6
  peer access is granted. Other IP protocols retain existing firewall policy.

Rules use RETURN, not ACCEPT: earlier scoped approval never overrides later
administrator rules or the Geneve allowlist. The management guard must lead
INPUT. The Geneve guard may follow this explicitly recognized non-accepting
guard; arbitrary earlier ACCEPT rules are still refused. IPv4/IPv6 guard drift
blocks Kolla prechecks/deployment and is checked again at phase boundaries.
Failure is manual-review-required, not an automatic firewall reset or rollback.

This is deliberately broad trust **between declared hosts**, not service-by-service
least privilege. No inter-host encryption is added; non-TLS traffic, including
Geneve and potentially DB/messaging, can be observed/tampered with by an attacker
on the network. IP allowlists do not authenticate peers or prevent source-IP
spoofing. TDX and Swarm's own overlay do not protect OpenStack host traffic.
Use this only with administrator acceptance of those risks. Existing unrelated
inbound services on non-web/non-SSH ports will be blocked: use empty dedicated
OpenStack hosts, review the plan and do not apply this to a shared application host.

The guard does not flush existing rules or configure upstream/provider firewalls.
Those must still permit the selected peers and SSH/ingress as intended. Managed
firewall conflicts retain the installer's existing fail-closed behavior. Peer
membership is derived from the complete inventory and bound to approved plans;
it is not an automatic discovery/trust mechanism. Membership changes require a
new reviewed policy on every affected host. Membership operations must preserve
the owned peer guards. Verify the supported topology before maintenance or IP
reuse; an allowlist does not introduce HA or authorize an arbitrary network.

## Kolla listener layout and access

Keepalived is disabled and no address is added. HAProxy remains the TLS frontend
on the existing controller IP. API backend listeners move to distinct ports:

| Service | Frontend | Backend |
| --- | ---: | ---: |
| Keystone | 5000 | 15000 |
| Nova / metadata | 8774 / 8775 | 18774 / 18775 |
| Placement | 8780 | 18780 |
| Glance | 9292 | 19292 |
| Neutron | 9696 | 19696 |
| Barbican | 9311 | 19311 |
| Skyline API / console | 9998 / 9999 | 19998 / 19999 |
| MariaDB | 3306 | 13306 |

Nova console backend ports are likewise separated. ProxySQL and Horizon are
disabled in this profile. MariaDB uses the native HAProxy TCP frontend; do not
assume ProxySQL frontend TLS is active. The render must explicitly set
`enable_external_mariadb_load_balancer: true`; otherwise pinned Kolla omits the
3306 frontend when ProxySQL is disabled. This setting does not bypass the
management peer guard or make database access public. RabbitMQ's management HAProxy frontend
is disabled (it has no separate upstream listener-port variable); native
management remains peer-only. The pinned RabbitMQ service definition is read
from Git rather than copied from a potentially modified working tree.

The backend render also emits `config/skyline/gunicorn.py` from the pinned Kolla
template. Upstream Gunicorn uses the frontend `skyline_apiserver_port` despite
the service healthcheck and HAProxy backend using `skyline_apiserver_listen_port`.
The reviewed override changes only this bind variable, so the API listens on
19998 rather than colliding with the 9998 frontend. Template/override-hook drift
is rejected instead of silently producing an unavailable GUI. The new file is
included in the normal render lock and private Kolla workspace; no image rebuild
or manual host edit is needed for a fresh install with the corrected backend.

API frontend TLS verification remains mandatory. Generated shared-profile server
certificates also include `localhost` and `127.0.0.1` SANs for administrative
SSH tunnels. Trust the generated API CA in the browser/client; never use
`--insecure` as a deployment fix. For example:

```bash
ssh -N -L 9999:192.0.2.10:9999 operator@192.0.2.10
```

Then use `https://localhost:9999`. Other OpenStack clients need suitable tunnels
and endpoint handling too: the operator workstation is not automatically a
trusted network peer and direct API calls from it may be blocked. An SSH login
does not make direct public API traffic originate from the controller.
