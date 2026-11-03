# Cleared-host WireGuard deployment from an external operator — 2026-11-03

Status: **installation, management-plane and three-node debug Swarm UI
acceptance passed**. Kolla prechecks/deploy/post-deploy and platform completion
succeeded. The initial management-only phase was followed by the explicitly
requested Swarm launch documented below. This is cleared prepared-host
acceptance, not a new OS installation or deep application workload testing.
The independent Azure non-CC cloud was not touched.

## Scope and exact inputs

- Operator: `petr-dev`, outside the new WireGuard network. Both SSH inventory
  addresses remain public; installation does not require adding the operator
  to the inter-host VPN.
- `super7-tdx-h100`, `92.223.119.247`: controller/network/ingress/compute.
- `super6-tdx-h100`, `92.223.119.245`: compute.
- Installer: committed `8a9df32`; no production source overlay.
- Published driver: clean commit `04c8614a66c75f0305f4f9b3b00a3581f7c5de1d`,
  version 0.2.29, rolling tag
  `openstack-feature-spvm-bare-metal-cloud-1325095077dd`.
- Downloaded asset ID: `705252659`, updated `2026-11-03T10:48:24Z`, SHA256
  `b119c62e50fae1ad1c15a6ad5d2973c64e8463548c6aad710331af4c681d820f`.
  GitHub's digest, sidecar checksum and clean bundle manifest were verified.
- Kolla: `689533989dc27670e4fde6e0b313c5150b8efe7f`, using the reviewed
  external-Docker collection patch and the existing isolated operator venvs.
- Hosts: Ubuntu 26.04, actual `resolute` facts, kernel `7.0.0-42-generic`.
  The experimental Kolla profile still uses Noble containers. No facts or
  host repository distribution were changed to Noble.

The historical immutable Quay Nova base digest was no longer available. The
current `2026.2-ubuntu-noble` tags were resolved and pinned **before** building:

| Base | Recorded digest |
| --- | --- |
| nova-compute | `sha256:47ce472584d66042e9e6437f42ff3f8866929520e956cc350638466a0fd665fc` |
| skyline-console | `sha256:a194866a8505028b68650fee0b203688edd417cf1870e40bf0b8f09f93fc5b9b` |

No fallback to `latest`, another series, a modified release manifest or the
old quarantined Docker cache was used. Moving prerelease tags are not an
indefinite artifact-retention guarantee; retain approved artifacts separately
when long-term reproducibility is required.

## Clearing the previous installation

The previous three-node test Swarm was retired through the authenticated
deletion API and delegated retirement worker: `SUCCEEDED / DELETED`, zero
Nova servers and no libvirt domains. Guest state was retired normally; it is
not promised recoverable. Configuration and caches were treated separately.

Both hosts were audited before changes. Only attributed Kolla containers,
OpenStack units/configuration, owned OVS bridges, network namespaces, state
bookkeeping and firewall chains were removed from active use. The controller
had 36 remaining containers and the compute had nine. Docker CE packages,
native ingress HAProxy and `python3-docker` were removed without `autoremove`.
Docker data/configuration and launcher/SDK/runtime files were quarantined;
the old guest image cache and empty pools were moved aside on `/data`.

Cleanup also explicitly restored the original `/etc/hosts` from its first
owned transaction backup, removed only the previous OpenStack API CA after a
certificate-set comparison, and deleted the old compute guest route only after
matching its destination/gateway/interface/source to the quarantined policy.
An unlistened stale Docker socket was removed only after proving no daemon,
unit or socket listener remained. The installer's refusal to adopt those
leftovers was retained; its guards were not weakened to make cleanup pass.

QEMU/libvirt, CC bootstrap, physical interfaces/default routes and Clonezilla
archives were preserved. Hashes of 31 protected files on the controller and
30 on the compute matched after cleanup. No explicit libvirt, CC-service or
host restart was issued. Shared OS dependencies were not indiscriminately
purged: this is not a byte-for-byte factory OS reset.

Recoverable, root-only evidence/configuration/Docker quarantine:
`/var/lib/spvm-wireguard-acceptance-20261103/quarantine` on each host.
Guest-cache quarantine: `/data/spvm-retired-wireguard-20261103`.
Neither directory is an instruction to recursively erase `/data` or a host.

## Administrator-prepared encrypted underlay

| Purpose | Configuration |
| --- | --- |
| Transport | `wg-spvm`, persistent `wg-quick@wg-spvm.service` |
| Controller / compute addresses | `172.30.77.1/24` / `172.30.77.2/24` |
| OpenStack API VIP | `172.30.77.10`, managed /32 on the controller |
| Outer UDP endpoints | `.247:51820` / `.245:51820` |
| WireGuard MTU | 1420 |
| Neutron guest network | one shared `10.77.0.0/24` |
| Host-NAT transit | `172.30.79.0/24`, router `172.30.79.10` |

Keys were generated independently on the hosts. Private keys did not leave
their hosts; configurations are root-only. A persistent, scoped UDP guard admits
only the opposite public IPv4 peer on 51820 and refuses IPv6 transport on that
port. It does not change the SSH or web-ingress ports. `Table = off` avoids an
automatic default route. AllowedIPs cover the peer/VIP and the explicitly
required compute guest-probe return addresses; they are not `0.0.0.0/0`.

Recent authenticated handshakes, bidirectional pings and a full-MTU DF ping
(`1392` bytes of ICMP payload) passed. Public default routes remained through
`92.223.119.241` on `bond0.2907`. No new VPN interface or route was created on
`petr-dev`; its route towards the private VIP remained its ordinary default.

The installer then used the normal dedicated profile with management and tunnel
interfaces `wg-spvm`. Owned Geneve guards, management VIP, guest probe routes and
host-NAT were prepared by shipped code, not by attaching the public bond to OVS.
Rendering observed transport MTU 1420 on both nodes and recorded Geneve overhead
58, yielding maximum guest MTU **1362**. WireGuard overhead was not subtracted twice.

This underlay is an administrator preparation choice, not a newly mandatory
installer prerequisite or a separate Neutron network per Swarm.

## Preparation and operator/controller boundary

Both container-engine transactions used **INSTALL**, not PRESERVE. Kolla's
container-engine role ran from `petr-dev` with real Ubuntu 26 facts and installed
Docker `29.9.2`. State storage and GPU inventories were generated afresh; both
hosts expose one unblocked H100 profile. No disks were formatted or GPU modes
reset. Missing host Docker SDK packages and the fresh API CA were prepared by
the scoped shipped Ansible phases.

The new custom OCI images are:

- Nova: `sha256:a33a4ce9d5a9dd21347603b4b10f5f5798e13f85c24f3c2a005d53c97442d6db`.
- Skyline: `sha256:5e9fdfd13b17695135be7e1dd4d40c068d2e552c72f43eec3f67c7f011635bba`.

The Nova image was hash-verified and imported on the second host before host
prerequisite admission. The launcher was staged against recipe commit
`22f4113f44e1718c901b419f933b0d779510fbd2`; bootstrap's pinned Uplink was preserved.

Kolla preflight/render, immutable workspace binding, prechecks and deployment
execute on `petr-dev`, targeting public SSH addresses. Protected provider inputs
are delivered to the controller over SSH only. A separate controller deployment
key was generated there; the operator's private SSH key was not transferred.
The controller SDK venv is `/opt/spvm-openstack-wg1103-client`, with separate
non-login `spvm-web` and `spvm-ingress` identities, not `petr` services.

Current limitation: the combined fresh-platform entry point's full preflight
is root-local on the controller and expects its Kolla/Ansible environment there.
This acceptance deliberately uses **split orchestration**: core Kolla from
`petr-dev`, then shipped root-local platform completion invoked over SSH on the
controller. A standalone call to that full preflight from the copied controller
workspace cannot stand in for the operator's successful Kolla preflight. It was
not bypassed by copying a foreign venv or relabelling its result. Separate checks
verified the controller's staged files, SDK/service accounts, both physical
machine identities and private API/host-source policies for completion.

Private journals and receipts are under
`/var/lib/spvm-wireguard-acceptance-20261103`; credentials and private keys must
not be added to Git. The initial prerequisite probes that preceded image import,
and acceptance-harness invocation errors, are retained separately and are not
reported as production installer regressions or successful mutating attempts.

## Initial management-plane verification, before guest creation

The shipped core workflow finished with
`OPENSTACK_INSTALLED_NOT_SPVM_ENROLLED`; shipped platform completion then finished
with `PLATFORM_READY_NO_SWARM_CREATED`. Both compute transactions completed
`quarantine`, `stop`, `enroll`, `start`, `accept`, `enable`, ending in
`COMPUTE_READY`. No failed mutation was blindly replayed.

| Check | Observed result |
| --- | --- |
| Nova compute services | Two, both `up` and `enabled` |
| Host launch readiness | Both TDX, debug-ready |
| Neutron network | `spvm-shared`, MTU 1362 |
| Project setup | Barbican probe, provider template, image and flavors prepared |
| Authenticated GUI catalog | Two hosts, three profiles, one registered image, one template |
| Anonymous catalog access | HTTP 401 |
| OpenStack CLI | Two hypervisors; zero servers across all projects |
| Read-only capacity | Three flavors, Auto and each explicit host: nine successful checks, no reservation |
| Private Keystone HTTPS | HTTP 200 with trusted CA from both hosts; compute route uses `wg-spvm` |
| Operator GUI test | Successful login and catalog over a temporary localhost SSH socket, with strict TLS |
| Final WireGuard | Recent handshakes, full-MTU DF ping both ways, public routes unchanged |
| Guard persistence/idempotence | Both units enabled/active; repeated application leaves rules unchanged |
| Preserved bootstrap | 31 controller / 30 compute protected file hashes unchanged |
| Versions on both nodes | QEMU 10.2.1, libvirt 12.5.0, Docker 29.9.2; kernel unchanged |
| Guests | No Nova servers or libvirt domains |

The controller-side read-only capacity check used the installed production
capacity implementation, live Placement/quota information and the fresh
root-owned collector snapshot. It was not a VM reservation or scheduling test.

### Listener audit and underlay hardening

An initial external check found OVN DB 6641/6642 reachable on the controller's
public IPv4 address despite use of a private API interface. A complete listener
audit also identified wildcard ProxySQL 6132/6133 listeners. These were real
exposures, not failed TLS authentication, and were not ignored.

The administrator-owned persistent WireGuard guard was extended with scoped
INPUT rules. Management destinations `172.30.77.0/24` and those four wildcard
TCP ports allow local traffic, established/related replies and authenticated
traffic arriving on `wg-spvm` from the management subnet; other new traffic is
dropped. RETURN rules preserve subsequent administrator policy. No global flush,
Docker restart, database restart or WireGuard bounce was used. OVN still listens
on all IPv4 addresses; firewall restriction, not a claimed changed bind address,
is the control. Neither OVN port had an IPv6 listener in the final audit.

The final guard SHA256 on both nodes is
`5c10b154792ef011a7a0447552582920007486f42e0e14a38fac21b32bc6d1b7`.
Live repeat-application checks passed after excluding only the generated
iptables-save timestamps and changing packet/byte counters from comparison.
The initial overly strict comparisons failed on INPUT traffic counters, not
on added/removed/reordered rules. Both WireGuard and guard units remained
enabled and active; neither was restarted for this verification.

From both nodes, private OVN DB TCP connections succeeded and `ovn-appctl`
reported the controller connection as `connected`. Initial diagnostics used
`ovs-appctl` instead of `ovn-appctl`; their failure was a test command error,
not evidence of a broken OVN session. Geneve remains protected by the shipped
registered-host transport guard.

Final probes from `petr-dev` to **both public IPv4 addresses** rejected TCP ports
1984, 3306, 4369, 4567, 5000, 5672, 6032, 6132, 6133, 15672, 25672, 11211,
8774, 8775, 9292, 9696, 9311, 8780, 9998, 9999, 6640–6644, 16641, 6080,
8022, 8023, 8080, 8081, 9101 and 61313. Public SSH and intended native web
ingress 80/443 were preserved. This is not an exhaustive internet/IPv6
penetration test. VPN/firewall units are configured for persistence, but a host
reboot and an external firewall-manager reload were not tested.

### GUI access and certificate identity

The GUI is at `https://172.30.77.10:9999`, reachable from management peers.
The operator was not added to the VPN; direct access from `petr-dev` failed as
expected. A temporary SSH tunnel through the controller successfully carried
the GUI page, native Skyline login and authenticated SPVM catalog requests.
The tunnel was closed after testing.

For an interactive tunnel:

```bash
ssh -N -L 127.0.0.1:9999:172.30.77.10:9999 petr@92.223.119.247
```

The certificate identifies the private VIP, **not `localhost`**. Therefore a
browser opened at `https://localhost:9999` has a hostname mismatch, in addition
to needing the installation's CA. Do not describe that URL as strict-TLS-valid
or disable TLS verification in automation. The successful automated tunnel
check connected its TCP socket to localhost while verifying TLS against the
actual `172.30.77.10` IP SAN and the generated CA. A browser-friendly deployment
needs an administrator-selected DNS name in the certificate and matching local
name resolution/tunnel, or direct authorized VPN access. No certificate was
silently reissued merely to make the test pass.

### Evidence and remaining limits

Root-only receipts include `core-result.json`, `platform-result.json`,
`basic-acceptance.json`, `capacity-acceptance.json`, `external-acceptance.json`,
`external-exposure.json`, `bootstrap-final-verification.private.log.json` and
`guard-final-verification-v3.private.log.json`, plus the per-host WireGuard/OVN
probe results under the receipt root above.
Failed initial exposure/TLS diagnostics remain in private logs; successful
final checks are recorded separately. Authentication material is not reproduced
here and must remain outside Git.

At the end of the initial management-only phase, no Swarm cluster, guest boot,
GPU passthrough launch, east-west guest packet
path, public Swarm UI, reboot persistence or controller failover was tested in
this run. In particular, correct calculated MTU and host pings are not proof of
guest data-plane health. Single-controller/ingress availability limitations and
experimental Ubuntu 26 support remain unchanged. The split operator/controller
workflow limitation described above is not claimed fixed by this acceptance.
No production code, upstream Kolla source, driver artifact, guest OS or Swarm
code was modified during the run. Only administrator deployment/network policy
and these English documentation updates changed; no new build was published.

## Follow-up: three-node Swarm launch and public UI acceptance

After the initial report, the administrator explicitly requested a test Swarm
launch. It was created through the authenticated Skyline/SPVM cluster API with
the registered `build-447-debug` image and existing Barbican-backed provider
template. Only the per-cluster domains/network identity and unique node names
were generated through the normal creation flow. No guest OS or Swarm code was
changed and no guest was rebooted to make readiness pass.

- Name: `wg-install-1103`.
- Cluster UUID: `990a190a-74bc-413b-b58b-c40a3710e4e8`.
- Create operation: `6acf1320-1272-4ba8-b18a-f0d3d4905d9a`.
- Public UI: <https://openstack-wg-test.swarm.superprotocol.dev/>.
- PKI hostname: `ca.openstack-wg-test.swarm.superprotocol.dev`.
- A replay of the identical create request returned the same cluster/operation;
  it did not create duplicate guests.

| Role | Nova instance UUID | Physical host | Final status |
| --- | --- | --- | --- |
| Seed, one H100 | `e7263dce-44cc-48bd-8fb8-bc2b3915c76e` | `super6-tdx-h100` / .245 | ACTIVE / READY |
| Join | `b9f20b27-0b97-4f38-8623-d9a46e23539a` | `super7-tdx-h100` / .247 | ACTIVE / READY |
| Join | `bd5744ff-9958-4c4e-b434-799d92b2ef4e` | `super7-tdx-h100` / .247 | ACTIVE / READY |

Both joins use the 8-vCPU/32-GiB/300-GiB no-GPU profile; the seed uses the
16-vCPU/64-GiB/300-GiB/one-GPU profile. Live libvirt XML confirmed passthrough
of `0000:2a:00.0` to the seed; this is not a CUDA application benchmark.

Final checks passed:

- Create operation `SUCCEEDED`, phase `UI_READY`; CLI cluster status `ACTIVE`
  with three nodes. Nova and SPVM readiness were checked separately.
- All three guest PKI endpoints returned the same CA, SHA256
  `bd54f1b9e2dd4c8b6e7b014eee7a6b4112ae80dbd7cb67a5f86425e6c8fe4a3d`.
- Gossip TCP 7946 was reachable on all three guests from the controller. The
  seed's guest APIs were reachable across physical hosts over the new topology.
- Public DNS resolved the UI to `92.223.119.247`; native HAProxy routed UI TLS
  to join `10.77.0.99:443` and PKI to seed `10.77.0.72:9443`.
- External strict-TLS smoke from `petr-dev` passed HTTP 200 for `/`,
  `/api/config` and the GraphQL `{ __typename }` query.
- The installed live UI probe reported `REACHABLE`, HTTP 200 at
  `2026-11-03T14:26:49Z`.

There was a startup interval with `WAITING_GATEWAY`, followed by
`VERIFYING_PUBLIC_UI`: initially the listeners were absent, then TLS timed out
or closed before a certificate was served. It resolved without network,
HAProxy, guest or Swarm configuration changes or restarts. It is not reported
as a proven MTU defect or a diagnosed ACME fault. Live gateway XML already
contained MTU 1362; a successful direct TLS handshake subsequently showed
guest MSS 1322. The bounded packet diagnostic retained TCP headers only, not
application payload; offload-visible packet lengths/checksum annotations were
not treated as proof of wire-level MTU errors.

The running cluster is left in place. These results supersede the initial
zero-guest observations, not their historical receipts. Private evidence is
under the same root: `wg-cluster-start.json`, `wg-cluster-status.json`,
`wg-cluster-verify.json`, `wg-cluster-ui.json`, controller
`cluster-verification.json`, and the seed's `swarm-gpu-verification.json`.
Initial not-yet-ready UI probe attempts are retained as startup diagnostics,
not final failures. No debug SSH authentication succeeded or was needed for
the final verification; readiness used the normal guest APIs.

Limits: this remains three guest nodes on **two physical hosts**, not physical
host HA. No host-failure, quorum-loss, guest reboot, recovery, deep S3/Harbor,
CUDA workload or long-running soak test was performed. No automatic replacement
or restart of a node was added. The independent non-CC cloud remains untouched.
