# Fresh shared-interface acceptance — 2026-11-02

Status (2026-11-03): **basic two-host OpenStack and debug Swarm acceptance passed,
with the corrections recorded below**. Earlier pending checkpoints are retained
as chronological evidence, not the final result. The independent Azure non-CC
cloud is out of scope and has not been modified.

## Scope and source provenance

The operator is `petr-dev`. Kolla runs there, targeting freshly reinstalled Ubuntu
26.04 CC hosts `super7-tdx-h100` (`92.223.119.247`) and `super6-tdx-h100`
(`92.223.119.245`). The former carries controller, network, ingress and compute
roles; the latter is compute-only. Both use their existing `bond0.2907` public
interface. No VPN, extra API address or public-interface OVS attachment is used.

- Installer base: `8a174fe`, with the explicitly recorded coordinator correction
  described below. The original source archive is not represented as containing
  that correction.
- Initial driver and GUI: `7ff658c0c62b8bd1abc7317abc8a3fbeec941e4d`, clean local bundle.
- Kolla: `689533989dc27670e4fde6e0b313c5150b8efe7f` and the reviewed external-Docker
  collection patch. Actual Ubuntu 26 facts are preserved.
- Bundle SHA256:
  `9711c19e73dd1358fa90b59b7db9d678754c7ab2034cbc75d458b1917b780481`.
- Original installer archive SHA256:
  `880c1f70e406cb046ae841e92b674759755b1f127268013038f20bd3721abd3c`.

These are local acceptance artifacts, not a newly published GitHub release.
Private journals, credentials, provider files and deployment SSH keys are not
included in this repository.

## Completed checkpoints

1. Strict SSH, fresh machine fingerprints, passwordless sudo and empty libvirt
   hosts were verified. A dedicated deployment key replaced reliance on a
   forwarded interactive SSH agent.
2. Old state-pool bookkeeping, containing no guest disks, was preserved by
   renaming `/data/spvm-state` to `/data/spvm-state-before-shared1102` on both
   hosts. No retained cache, Clonezilla image or other data was deleted.
3. The storage preparation and GPU discovery stages completed on both hosts.
   Each has one NVIDIA `10de:2321` device at `0000:2a:00.0` using VFIO.
4. Native Kolla Docker installation succeeded on both actual Ubuntu 26 hosts,
   using the host's actual distribution repository, not Noble's Docker repository.
5. Persistent Geneve and management guards, isolated NAT transit and probe routes
   were prepared. Existing public addresses and the default route were retained.
6. Nova and Skyline images were built from digest-pinned Kolla bases. The Nova
   image was exported and imported on the second host with archive checksums and
   exact image identity verification. Image IDs:

   - Nova: `sha256:fff4d926c1cd65424073274ffc66d6c424351a706f494d657fa6085e9e717a88`.
   - Skyline: `sha256:9244fea4d915e7b28e2eaeef5be7c5803438c8e52b6b8129f638355f55954922`.

7. Basic preflight, rendering, native compute identity staging and bound Kolla
   workspace preparation completed on the operator. The verified launcher and
   helper were installed on both empty hosts, without starting guests.
8. Scoped base packages, system Python Docker SDK and API CA trust were prepared
   and verified on both hosts. The first core-only Kolla attempt stopped at the
   missing Docker SDK before deployment; a new attempt follows preparation.

## Corrections and remaining gates

The live run exposed an unconditional call to dedicated dummy-interface cache
repair in the network coordinator. That check rejects a primary interface with
a default route. Shared-interface preparation now skips only that dummy-specific
repair; owned management validation and address/firewall admission remain active.
The real network phase passed after this correction. Targeted local regression
results: 13 shared-interface, 18 network preparation, 15 Geneve and 17 package
tests passed (63 total). Test sources remain outside staging.

The second Kolla precheck run exposed a second rendering error: with ProxySQL
disabled, `mariadb_external_lb.enabled` resolved to false. Moving the backend to
13306 alone does not create a 3306 frontend. The backend now explicitly enables
`enable_external_mariadb_load_balancer`; all five shared-interface backend tests
pass with the new assertion. The precheck runner and its remaining Ansible child
were interrupted before deployment to avoid knowingly deploying this layout.
This correction requires a newly verified local bundle/context; previously built
images are retained as evidence, not claimed to contain the subsequent correction.

The acceptance harness initially supplied incorrect probe ports. Its explicit
policy was corrected on the empty controller to `80,443,7946,9180,9443`, verifying
the existing owned rules before replacement and unchanged unrelated network
state afterward. This was test-input correction, not a relaxation of peer access.

Kolla prechecks/deploy/post-deploy, authenticated GUI, actual three-node debug
Swarm, common CA and public Swarm UI acceptance are still required. Controller
completion and provider credentials must be handled through protected, explicitly
authorized inputs; do not copy an entire operator home or SSH private directory.
Do not treat installed custom GUI files as proof of a working cloud or Swarm.

## Continuation on 2026-11-03 (still in progress)

The administrator explicitly approved transfer of provider credentials and private
deployment inputs to the controller **over SSH**. Only the required configuration
files, provider directory and bound workspace were transferred, over strict SSH,
into the existing root-only controller transaction directory. A separate deployment
key was generated on the controller and its public key authorized on the two CC
hosts. The operator's private SSH key was not transferred. Provider parsing,
service SDK access, workspace integrity and both physical machine identities were
verified from the controller without printing credentials.

The corrected local source artifact has SHA256
`d252ca85da550e3580ffd0052787752294951036e3f8d5fcff6172f99e25c566`.
Its manifest honestly records `source_tree_dirty: true` against the 7ff658c0 base;
every declared source and wheel hash was verified. It is an explicitly trusted
local test source directory, not a clean published bundle. New image IDs:

- Nova: `sha256:013bca0c59a4e32e2e785dab62875100c6b18b381e28e68eb8d0af0b90c1702f`.
- Skyline: `sha256:5db1c5965a6aa5f3d2092872f2947e94f067d9657bbf9e1c6e5097ea039d7509`.

The new local workspace was rendered with the MariaDB frontend enabled. The Nova
image was imported and verified on the second compute. Both hosts remained empty
before deployment, and the original default routes through 92.223.119.241 on
bond0.2907 were unchanged.

The previous unused public API CA anchor was preserved before installing the new
workspace CA. The trust guard correctly required review because the system bundle
lost the previous test CA as well as gaining the new one. Certificate-set review
confirmed exactly those two intended changes and no others. A subsequent scoped
NOOP check returned `API_TRUST_READY_NOT_BOOTSTRAPPED`, without rerunning package
installation or trust updates. The third core Kolla transaction is now running
from the operator host; its completion is not yet claimed here.

During that deployment, read-only checks confirmed the corrected MariaDB 3306
frontend returned a MariaDB handshake from **both** CC hosts. The Keystone
frontend at `https://92.223.119.247:5000/v3` also returned its v3 discovery document
with normal system CA verification enabled. No authentication bypass or
`--insecure` setting was used. Kolla completed prechecks and proceeded through
database, load-balancer, Keystone, Glance, Placement and OVN setup; deployment and
Swarm acceptance are still pending at this checkpoint. The expected ignored
MariaDB liveness probe on an initially empty host is not an unignored failure.

## Core deployment and GUI completed (2026-11-03)

Kolla prechecks, deploy and post-deploy completed from `petr-dev`. Both computes
were activated as `COMPUTE_READY`. Shared Neutron networking, the Barbican smoke
check, provider-template enrollment, debug image registration, three resource
profiles, budgets, GPU catalogs, workers and ingress setup completed. A separate
read-only final verification returned `PLATFORM_API_AND_GUI_VERIFIED_NO_GUESTS`:
authenticated GUI, two compute hosts, one template, one image, three profiles,
nine browser probes and native capacity verification, with no guests yet.

The original completion transaction remains `NEEDS_REVIEW` at final verification;
its completed mutating phases were not replayed or its journal rewritten. The
separate successful re-verification is recorded in the private acceptance receipt.
Strict TLS over an operator SSH tunnel passed. Direct operator connections to
controller TCP 3306, 5000 and 9999 were denied by the shared-interface guard.

The first final verification exposed a pinned Kolla Skyline Gunicorn template
binding the public API port (9998) instead of its backend listen port (19998).
The updated renderer now emits a source-revision-checked custom Gunicorn
override. The live correction changed only Skyline's Gunicorn configuration and
restarted only `skyline_apiserver`; other container identities and start times
were unchanged. The production override SHA256 is
`0ef4013077edcd10ee984afdf1ae79f24232b64f0402aacf642288bf3d7d928e`.
The earlier driver02 artifact does not contain this subsequent renderer fix.

## Fresh launcher prerequisite failure and correction

The first debug cluster was accepted through the GUI API and its idempotent
replay returned the same operation. Nova's GPU claim succeeded on super6, but
the launcher failed before creating any libvirt domain: Uplink was absent and
its runtime installer could not write `/usr/local/bin` inside the helper sandbox.
This was not a guest CA or scheduler failure. Cluster
`f5867672-0327-4630-bb5e-48c46da2fa9f` was retired through the normal authenticated
GUI deletion API; the worker reported `SUCCEEDED / DELETED` and zero Nova servers.

TDX, SNP and non-CC bootstrap now share preparation of launcher prerequisites.
The missing Uplink archive is pinned to v1.165.6 and SHA256
`6998d64c7671393898b6be839911b1e7b22d4e1b0a0646631c57a62f9b0ee966`.
Fresh helper provisioning checks the executable dependencies before installing
services. Existing executables are preserved rather than upgraded. Only the
scoped prerequisite function was applied to these already prepared empty hosts;
no CC/GPU/kernel setup was rerun and neither host was rebooted. Both installed
Uplink binaries hash to
`060ea24fc8b300c8f8502155d8c72ddd85ca92a121af22e1a57ff49824b33f57`.

Local checks passed: 24 helper provisioning tests, four synthetic prerequisite
tests, seven shared-interface backend tests and shell syntax checks for the
common helper and all three bootstraps. Synthetic tests did not install packages
or access servers. Test sources remain unstaged.

A new unique cluster `985e1bc1-0f35-43cc-91a8-41b309f39bc4` was accepted, with
one GPU seed on super6 and two CPU joins requested on super7. Guest readiness,
common CA and public UI remain pending at this checkpoint.

## Final basic acceptance — 2026-11-03 10:31 UTC

The replacement cluster completed the authenticated GUI creation/publication
operation successfully, including `UI_READY`. Its unique name is
`shared-install-1103b`; cluster UUID is
`985e1bc1-0f35-43cc-91a8-41b309f39bc4` and operation UUID is
`434821eb-345b-4e41-b993-1466823cfcd7`. Image: `build-447-debug`;
the protected test provider copy uses `tags.services: develop`.

| Role | Physical host | Resources | Nova / SPVM |
| --- | --- | --- | --- |
| Seed | super6-tdx-h100 (.245) | 16 vCPU, 64 GiB RAM, 300 GiB state, one H100 | ACTIVE / READY |
| Join | super7-tdx-h100 (.247) | 8 vCPU, 32 GiB RAM, 300 GiB state, no GPU | ACTIVE / READY |
| Join | super7-tdx-h100 (.247) | 8 vCPU, 32 GiB RAM, 300 GiB state, no GPU | ACTIVE / READY |

Read-only libvirt inspection verified the seed actually owns PCI GPU
`0000:2a:00.0`, not merely a flavor request. All three public CA endpoints returned
the same certificates-only chain, SHA256
`6d0049969793b0648f482f31c5a013a0a05933c22741835dc9e0c54fac476dc8`.
All three gossip listeners were reachable from the controller. The installed
OpenStack CLI reported cluster `ACTIVE` with three nodes. Debug signature-registry
skipping was expected; this test does not claim release-signature attestation.

Public UI: <https://openstack-shared-test-b.swarm.superprotocol.dev/>.
Normal trusted TLS and HTTP 200 were verified from `petr-dev`. The UI HTML,
bounded `/api/config` JSON and unauthenticated GraphQL `{ __typename }` smoke
all passed both externally and from the controller. Initial GraphQL HTTP 404
cleared as the Swarm backend initialized; no Swarm code or readiness criteria
were changed to conceal it. The gateway was discovered on the second join,
not assumed to reside on the seed.

The final SSH-tunnel GUI check also passed after public ingress publication.
Direct operator TCP checks to 3306, 5000 and 9999 remained blocked. The first
failed cluster was independently verified absent from Nova and the GUI, with
its device ports and Barbican secrets removed, empty Placement allocations,
and the shared image/template/flavor catalog preserved.

Private receipts are under `/var/lib/spvm-shared-acceptance-20261102` on the
operator/controller: `cluster-verification.json` (controller),
`external-ui-smoke.json` (operator), `controller-ui-smoke.json` (controller),
`public-ui-verification.json`, `external-access-final.json` and
`failed-cluster-cleanup-verification.json`. They contain no credentials in the
public summaries; raw journals and provider/auth files remain root-only.
Local targeted suites passed **98 tests** (63 network/package, 24 helper,
four synthetic prerequisites, seven backend), plus bootstrap shell syntax checks.
Production changes and English documentation are staged; tests are not staged.
No commit, push or release publication was performed.

### Limits and handoff

- This is one controller and two physical hosts, not HA. Three guests do not
  make controller/network/ingress fault-tolerant.
- Ubuntu 26 Kolla remains experimental, using the pinned Noble container
  profile and real Ubuntu 26 facts. No additional OS/platform support is claimed.
- S3/Harbor functional workloads, release signing, host failure/recovery and
  add/remove-compute were not re-tested by this basic acceptance.
- The final original platform transaction was not rewritten from its historical
  `NEEDS_REVIEW`; its correction was verified by a separate read-only acceptance.
- The working test cluster is left running for administrator inspection. Before
  restoring OS-only Clonezilla images, retire the cluster normally and review
  test bookkeeping on `/data`: an OS restore does not revert that data disk.
  Never recursively remove `/data` or Clonezilla archives. The independent
  Azure non-CC installation remains untouched.
- A future clean run must use the updated staged source/components, including
  host bootstrap prerequisites and the renderer fixes. The older published
  artifact must not be described as containing these corrections.
