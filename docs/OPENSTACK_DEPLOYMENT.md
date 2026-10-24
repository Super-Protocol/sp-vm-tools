# OpenStack with the SPVM Nova driver: deployment and operations

Updated: 2026-10-21. Read the implementation status before running commands.

Current lab checkpoint: OpenStack was cleared and redeployed on both prepared
hosts without reinstalling their operating systems or changing libvirt/QEMU/CC.
Published driver 0.2.23, source commit
`c4df9f8ee9395cd0a5da492c3f4a058ed28c0025`, is installed on both computes.
Kolla prechecks/deploy/post-deploy, strict-TLS API checks, both initial compute
enrollments, shared Neutron topology, private flavors/release marker, Barbican
probe, ingress scaffold and authenticated Skyline Swarm catalogs passed.
The `default` provider template is available in Barbican. **No Swarm is running;
no guest was launched in this redeployment.** Static/proxy/login checks are not
a headed-browser or end-to-end guest-launch test.

The common workflow completed uninterrupted from prepared hosts and artifacts,
without manual repairs or a worker overlay. Explicit artifact, SDK/image and
private-management transport preparation still preceded that workflow. It is
not proof of unattended OS-to-platform installation, clean-OS acceptance or
fully cold-cache reproducibility. See
[the automation backlog](OPENSTACK_AUTOMATION_BACKLOG.md) for exact remaining
work, pins and acceptance boundaries. Historical Swarm lifecycle observations
below describe earlier deployments, not guests currently running on these hosts.
`verify-kolla-existing` is also deliberately scoped: it does not certify every
control-plane configuration or full installer idempotency (see §10.27).

Local follow-up implementation adds native compute identity generation, owned
hostname/cache/network preparation and an optional complete fresh `platform`
path in the common coordinator. See
[the platform workflow](OPENSTACK_PLATFORM_WORKFLOW.md) for inputs and the
explicit prepared-host/artifact boundary and the latest uninterrupted results.
The selected bundle must advertise `separate-web-ingress-identities` (introduced
in 0.2.23) and `private-publication-input-handoff` (required by the current
installer; select a newly built matching bundle). The historical run below used
unmodified published files and finished with a
`COMPLETE` parent journal and `PLATFORM_READY_NO_SWARM_CREATED` result.

Candidate pins (tested on the existing two-host lab; **not** clean-OS acceptance):

| Component | Accepted identifier |
| --- | --- |
| Driver/CLI | `0.2.23`, source `c4df9f8ee9395cd0a5da492c3f4a058ed28c0025` |
| Published driver archive SHA256 | `1b032cc543cd26a4aadcb094bcc5ba786f4484a5a7906e6ff3d5b55b5c9b99f5` |
| Published wheel SHA256 | `9a8e1701dbc7f56e2bf0377af085e9b7a85f40c2a90ae73ffa3ccad2f8f51d80` |
| Kolla-Ansible source | `4b493ef36ace2b90257ecf94feb00e2c6e8014c9` (2026.2) |
| Custom compute image | `spvm/nova-compute:release023-20261021-c4df9f8e` |
| Exact compute image ID | `sha256:8e6b7ce2f426e9765b62fa223b6294ba107ec891b72a3920ecca9eec9efe6f8a` |
| Image-context SHA256 | `54ba8947758cfcff60126ad03bc7a346b5c4116fa05fdb9a0c6226bb241e8afe` |
| Kolla compute base | `quay.io/openstack.kolla/nova-compute@sha256:f5aadb8b42f653403b4db2a4b2b68fafe132b9f57191c318237736df4df7ebae` |
| Enrolled release; not launched in this redeployment | `build-449-release` |
| Installer snapshot | `52cd397c538d28b7032a9ddb88693ea7955c6c1f` |

The rolling branch release name is a discovery channel, not an immutable pin.
For a private GitHub repository, obtain its asset using an already authenticated
`gh` or another approved delivery method and verify the archive checksum before
using `--driver-archive`. The frontend's `--driver-release` downloader targets
public GitHub releases; do not assume it inherits `gh` authentication. Never put
tokens into command arguments, git or the bundle.

Record the installer checkout separately with `git rev-parse HEAD`; this guide
and its sibling Python modules must be delivered from the same reviewed commit.
The `sp-vm-tools` base before the new verifier/coordinator is
`6d50f04` (historical base, **not** the completed candidate revision).
The corresponding tools revision is pinned in the swarm-cloud deployment README;
no release/tag/push is implied. Private lab journals are local evidence, not
repository user documentation.

Source staging is exclusive. If planning with `--driver-archive` or
`--driver-release`, the first invocation creates a new private staging directory.
For apply/repeat, select the already verified extracted bundle via `--driver-dir`;
do **not** reuse that staging directory for a second extraction. Preserve its
source receipt and checksum. The examples in §10.27–10.28 deliberately use an
existing verified bundle directory for both plan and apply.

Development checkpoint (2026-10-19): the unified driver/CLI source is now 0.2.22.
It includes strict Nova-record disappearance checks in both cluster deletion and
join retirement. The operator CLI candidate was built offline and installed
without changing dependencies; the running compute image remains the previously
accepted 0.2.21 image. Do not confuse the candidate CLI version with a completed
compute-image rollout. The container-restart/live-retirement checks below are
historical observations; preserve detailed lab evidence outside the repository.

The selected live tests passed: an ordinary Ubuntu guest ran alongside two Swarms;
`nova_compute` was restarted on each host, then `nova_api` and `placement_api` on
the controller, one container at a time. All seven guests retained QEMU process
identities and Nova launch times, both CA digests stayed unchanged and the ordinary
guest retained its boot ID. Docker and libvirt daemons were not restarted.
The disposable replacement join passed real API/CA/gossip/UI/installed-ingress
and actual-compute cleanup gates, with an idempotent retirement repeat. This is
not Swarm quorum, physical-host loss, database/RabbitMQ recovery or image-upgrade
acceptance. All five temporary VMs were subsequently removed, including their
owned cloud/state/network/DNS resources; only the original three-node Swarm remains.
Accounting returned to its original baseline and the controller GPU is free again.

To build an offline candidate from reviewed source, use:

```bash
python3 examples/test-cluster/bare-metal-spvm/deployment/build_bundle.py \
  --tag spvm-driver-v0.2.22-rc1 --output /srv/spvm-candidate-01 \
  --wheel /srv/offline-wheels/nova_spvm-0.2.22-py3-none-any.whl
```

The optional wheel must contain exactly the source package's Python modules and
the manifest version, with no injected `.pth`/data files. Without `--wheel`, the
builder uses the existing local build dependencies with network indexes disabled.
Both source files and wheel bytes receive SHA256 entries in the bundle manifest.
Keep its actual `source_commit` and `source_tree_dirty`; do not relabel a dirty
snapshot as a clean release. The installer intentionally rejects a dirty bundle
via `--driver-archive`/`--driver-release`, even with a matching archive SHA256.
For development, an explicitly reviewed checkout or expanded candidate may be
selected via `--driver-dir`; this is operator trust, not release verification.
A clean committed/tagged bundle must be built and tested before GitHub publication.

The accepted private candidate is
`nova-spvm-spvm-driver-v0.2.22-rc1.tar.gz`, SHA256
`3957ffb68d1d5c4ce8bc69062b3f0ff27d554ca194c9600d57deb2735f885f10`.
Its wheel SHA256 is
`ab74f5d44d88eddbf0178295c4d2a772df27c1b0960b9604f9a450ac0760e526`.
The manifest records baseline commit `235034f6ae4fe65f98c4fba77049139a02e659e4`,
`source_tree_dirty: true` and 272 source-file hashes. Two independent local
packages built from the same offline wheel were byte-identical; source/wheel
checks and real installer directory selection passed. The dirty-archive refusal
also passed. Archive, checksum and machine-readable result are retained under
`swarm-cloud/artifacts/spvm-driver-v0.2.22-rc1/` (ignored operator artifacts).
No clean commit, public tag/release or compute-image rollout is implied.

## 1. Scope and implementation status

The goal is a Kolla-managed OpenStack cloud capable of running multiple independent
Swarm clusters. Each Swarm node is a Nova instance; Nova/Placement schedule CPU,
memory, state-disk capacity and GPU requests. Instances are visible in Skyline.
Magnum and Heat are not required. This is not an Azure deployment or DevStack setup.

Repositories have separate responsibilities:

| Repository | Responsibility |
| --- | --- |
| `sp-vm-tools` | Hardware bootstrap, launchers, public `scripts/install-openstack.sh`, implementation in `scripts/openstack/`, this guide |
| `swarm-cloud` | Nova driver, OpenStackClient plugin, versioned deployment backend and driver bundles |

**The generic fresh-install executor is not complete.** The wrapper does not turn
the backend's currently disabled `deploy` command into a working installation.
Do not describe a passing basic preflight as a deployed or attested cloud.

| Workflow | Current status |
| --- | --- |
| Select trusted local checkout/bundle | Implemented |
| Stage checksum-pinned archive/public GitHub release | Implemented; requires updated clean bundle |
| Read-only basic preflight and resource audit | Implemented |
| Generate reviewable Kolla inputs from pinned upstream inventory | Implemented |
| Select existing/missing Docker per host | Implemented in preflight/render; not a Docker installation test |
| Journalled container-engine preparation using the Kolla Docker role | Real missing-Docker installation passed in a disposable Ubuntu 26 VM; physical-host bootstrap remains separate |
| Isolated new-cloud Kolla workspace, passwords, private API CA and task parsing | Exercised in a real cleared-cloud redeployment; credentials and CA retained across reviewed corrections |
| Root-local fresh Kolla SPVM helper provisioning | Applied on both cleared hosts; `.245` needed an explicit reviewed directory-permission repair; not a fresh-OS test |
| Pinned Nova launcher assembly and transactional fresh-host installation | Pinned source plus reviewed patches installed on both cleared hosts; measured bootstrap was retained |
| Offline custom compute-image context and local build | Built, delivered and running on both cleared hosts; image bytes bound to receipts |
| Checksum-bound image export/import and SSH delivery | `.247` to `.245` passed with the exact image ID preserved |
| Initial deny-all runtime and Kolla compute intent binding | Real new-cloud deployment passed; deny-all remains until separate runtime enrollment |
| Approved initial Kolla prechecks executor | Passed on both cleared physical hosts, followed by deploy/post-deploy |
| Scoped base packages and system-Python Docker SDK | Applied on cleared prepared hosts; full clean-OS package coverage remains separate |
| Public OpenStack API CA registration on fresh hosts | Applied on both cleared prepared hosts; internal API strict TLS accepted |
| Combined prepared-host status and next prerequisite | Implemented with fixtures; read-only, not a first-boot executor or deployment authorization |
| Fresh root-local registered-host IPv4 Geneve firewall | Plan/apply and persistence implemented with kernel fixtures; real firewall snapshot parsing and static unit validation passed; no live rule mutation accepted |
| Existing management/VIP and isolated veth host-NAT | Plan/apply, persistent units and protected-state checks implemented; real kernel apply/repeat passed in a disposable namespace on `.247`, not host-root/systemd or Neutron acceptance |
| Fresh Kolla prechecks/deploy/post-deploy transaction | Completed on cleared `.245`/`.247`; service/API/SPVM acceptance is separate, and existing OS/bootstrap/network/cache were retained |
| Portable project/shared-network runtime and flavor intent | Secret-free staging implemented; API reconciliation, activation and publication are separate explicit workflows below |
| Portable private flavor/Glance-marker API reconciliation and Barbican probe | New private flavors/image marker created in the rebuilt cloud; private ACL/payload round-trip and cleanup passed |
| Portable shared guest/transit subnet and router API plan/apply | New topology created and reconciled; OVN HA policy is explicit; retained host routing and guest packet-path acceptance are separate |
| Initial offline runtime enrollment | Real deny-all-to-project enrollment installed on both empty computes in the cleared-cloud redeployment; never-allocated pool lock is supported without fabricating a claims ledger |
| Initial portable host ingress scaffold and activation | Real empty-scaffold install/start/enable passed on freed `.247`; Kolla containers unchanged; namespace routing/negative checks also passed |
| Initial compute quarantine/stop/enroll/start/accept/enable | Full fresh enrollment/activation passed on compute-only `.245` and all-in-one `.247`, protecting other containers/services; clean-OS acceptance remains separate |
| Prepared, registered compute activation with journal/resume | Implemented and tested on the two-host lab |
| Release449 Swarm across two hosts, public HTTPS UI | Re-tested after full cleared-cloud redeployment: three ACTIVE/READY guests, registry presence, Barbican CA, UI/config/GraphQL HTTP200; repeat create reused existing guests |
| Two simultaneous Swarms and exact neighbor-preserving cleanup | Passed on rebuilt Kolla across both hosts: six ACTIVE/READY guests, one H100 per seed, distinct CA/grants/SG/domains, both public UIs; retiring only the second returned accounting to baseline and preserved the first QEMU processes/UI/shared network |
| Resource exhaustion without overcommit | Live oversized CPU/RAM/state requests and a GPU request pinned to the occupied host returned NoValidHost before spawning; no joins, partial namespaces cleaned, repeat cleanup NOT_FOUND; project quotas/inventories unchanged |
| Interrupted client creation/replay and partial BUILD deletion | SIGTERM of the owned OSC child during admitted seed BUILD, exact replay to three READY nodes retaining seed UUID/port/grant, ready replay without duplicates; separate admitted BUILD seed deleted with repeat NOT_FOUND |
| Final failure-test cleanup and strict Nova absence | Six test namespaces/eight Nova targets removed; an SDK DELETED-before-404 race was fixed in the operator CLI, immutable journal resumed; root disk/claim/lease/TAP/OVS and cloud/DNS proofs passed, original cluster QEMU/CA/UI and accounting baseline preserved |
| Ordinary guest alongside SPVM and selected container restarts | Real Ubuntu26 guest plus two Swarms; both nova_compute containers, nova_api and placement_api restarted sequentially without guest/CA changes; ordinary boot ID and accounting verified |
| Strict join-retirement absence and live replacement cleanup | Real replacement join, JOIN_RETIRED / ALREADY_RETIRED with mandatory API/UI/host gates; GET404 required before port/grant deletion; all five temporary guest targets removed afterward |
| Unattended installation on a clean OS | Not yet implemented/accepted |
| Kolla installs missing Docker on Ubuntu 26 | Passed in a disposable Ubuntu 26 VM using its real distro facts; not an OS reinstall of physical hosts |
| Three-controller HA, failover/reboot recovery | Not accepted |

Lab-specific `kolla-super6-*`, `kolla-super7-*` and fixed-UUID acceptance helpers in
swarm-cloud are evidence/recovery tools, **not portable installers**. Do not run
them against a new environment or substitute a different IP into their source.

## 2. Separate host preparation from OpenStack installation

First install the OS and prepare each compute host with the existing
`scripts/bootstrap_tdx.sh` or `scripts/bootstrap_snp.sh`, as appropriate.
Review their help and the platform instructions. This phase owns BIOS/firmware,
kernel, confidential-computing support, QEMU/libvirt and attestation prerequisites.
It can require BIOS changes and reboots. Finish those reboots **before** deploying
workloads. The OpenStack installer must not silently rerun bootstrap, change BIOS,
replace measured QEMU or reboot a host.

Before proceeding, verify:

- The intended TDX/SEV platform is enabled and `/dev/kvm` exists.
- Host libvirt can query domains and uses the project's compatible measured launcher.
- For TDX, QGS/PCCS and guest attestation prerequisites work. Root's ability to
  connect to QGS does not prove that `libvirt-qemu` has permission to connect.
- Selected GPUs have appropriate confidential-computing firmware/mode, IOMMU
  isolation and `vfio-pci` binding. Discovery is not permission to rebind a GPU.
- Time synchronisation, DNS and required outbound artifact/certificate access work.
- The host has no unresolved bootstrap reboot requirement.

The basic preflight checks only a subset of these conditions. It does not issue a
TDX quote, validate a SEV report or certify runtime-user socket access. Both
architectures are intended; the current OpenStack integration acceptance was TDX.

## 3. Operator workstation/controller prerequisites

Run deployment operations from the Kolla deployment/controller machine, not from
inside a guest. Use a dedicated deploy user with reviewed SSH access and
non-interactive `sudo` on targets. Trust SSH host keys through an independent
channel; do not disable `StrictHostKeyChecking`.

You need Python 3, Bash, OpenSSH and Git. The backend needs PyYAML; commands that
query OpenStack additionally need the compatible client/SDK dependencies listed in
the selected swarm-cloud driver package. Do not install into system Python:

```bash
python3 -m venv /srv/spvm-installer-venv
/srv/spvm-installer-venv/bin/pip install 'PyYAML==6.0.3'
export SPVM_INSTALLER_PYTHON=/srv/spvm-installer-venv/bin/python
bash /srv/sp-vm-tools/scripts/install-openstack.sh --help
```

Create `/srv` subdirectories with appropriate ownership first; these paths are
examples. For offline operation prepare a reviewed wheelhouse and pinned artifacts
instead of permitting arbitrary downloads. Do not run the entire wrapper with
`sudo`: the backend elevates only the specific remote operations that need it.

## 4. Version pinning and Ubuntu 26

The tested Kolla source commit is:

```text
4b493ef36ace2b90257ecf94feb00e2c6e8014c9
series: 2026.2
container base distribution: ubuntu
```

This is a tested development snapshot, not a claim of official Ubuntu 26 support.
Host OS and container OS are separate. Permitting `resolute` in a reviewed Kolla
host-distribution check must **not** forge Ansible facts as `noble`. Package tasks
must see the real host distribution, version and codename.

Keep the selected Kolla checkout clean, preserve its matching collections and
record their versions. Do not substitute a floating `master`, `latest` container
tag, a different inventory template or an unrelated collection revision.

The lab used Docker 29.9, Ansible 2.21.5, host Docker SDK 7.2 and a custom
Nova compute image derived from the 2026.2 environment. These are observations,
not a universal compatibility promise. The currently tested image was
`spvm/nova-compute:clean-redeploy-20261018-01`, local image ID
`sha256:5bd4833f7ad72d69fc71a92019e9e63b1145d27b9ace6ad578efd52e691017e1`.
It contains reviewed local fixes; **it is not a published clean driver release**.
A Docker image ID is not a registry manifest digest. A portable release must pin
the actual distributed artifact and its appropriate registry digest separately.

Record swarm-cloud and sp-vm-tools commits, dirty status, bundle SHA256, Kolla
commit, collection versions, launcher checksums, compute image identity and guest
release in deployment receipts. An old commit plus uncommitted changes is not
reproducible provenance. Never put provider credentials in a public lockfile.

The lab operator CLI was subsequently updated independently to a private offline
`nova-spvm 0.2.21+cleanup1` wheel, SHA256
`2d5bb53124184e1f58d6b30941b0efb080872bb63f96423807de840b9f28f4ff`.
Compared with the compute image's original wheel, only
`openstack_spvm/cluster_cleanup.py` changed; all 47 Python files were verified
after installation and dependencies were unchanged. The Nova compute image was
not rebuilt/restarted. This is an unpublished staging artifact, not a new public
release; include the source fix in the next clean bundle rather than assuming
that published version 0.2.21 already contains it.

## 5. Docker policy

The installer must make an independent decision for each host:

1. Existing reachable Docker: preserve it. Do not reinstall packages, change its
   repositories or daemon configuration, or restart it implicitly.
2. No Docker CLI, service or socket: let Kolla install it using actual OS facts.
3. Partially installed or unreachable Docker: stop for administrator review; do
   not treat a broken daemon as permission to reinstall.

Preflight records `preserve-existing`, `install-with-kolla` or `blocked`.
Rendering with a fresh preflight writes host-level `spvm_external_docker` and
`enable_docker_repo` settings. The reviewed
`deployment/patches/kolla-external-docker.patch` from the driver bundle must be
applied to the matching `openstack.kolla` collection before bootstrap. It skips
the Docker role's package/configuration path for preserved daemons and checks
`docker info` instead. Upstream does not automatically understand this variable.

Do not pass `-e spvm_external_docker=true` globally on a mixed deployment:
Ansible extra vars override per-host inventory decisions. The new rendered
profile removes these historical global Docker choices. The old lab-only profile
without `--preflight` still preserves Docker on every host.

An existing daemon passing `docker info` still needs pinned Kolla compatibility
checks. The missing-Docker path on Ubuntu 26 must be exercised on a clean target
before it can be called supported; no live Docker reinstall is part of preflight.

### 5.1. Executable container-engine preparation phase

The wrapper owns `plan-container-engine` and `prepare-container-engine`. These
commands run on the deployment machine with local access to its clean pinned
Kolla checkout, reviewed collections and Ansible virtual environment. Invoke
either command with `--help` after the wrapper's source-selection arguments.

This phase deliberately runs only `openstack.kolla.docker`, not the broader
`baremetal` role or `kolla-ansible deploy`. It does not configure BIOS, install
libvirt, reboot a host, launch guests, install the Nova driver, enroll compute
or finish OpenStack installation.

To make the reviewed collection identity explicit, calculate the SHA256 of the
collection tree using the same read-only helper (after reviewing/applying the
Docker-preservation patch). Record this digest with the deployment lock:

```bash
cd /srv/sp-vm-tools/scripts/openstack
python3 -c 'from openstack_container_engine import collection_hash; print(collection_hash("/srv/ansible-collections"))'
```

`/srv/ansible-collections` must contain
`ansible_collections/openstack/kolla` and matching dependencies. All non-bytecode
regular files in this supplied tree are included in the digest. Internal file
symlinks (used by upstream collection tests/licenses) include both resolved target
identity and content hash. External, broken and directory symlinks are rejected.
A locally calculated digest identifies the reviewed contents; it is
not an independently authenticated upstream signature.

Create a plan for exactly one configured compute host:

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/swarm-cloud -- plan-container-engine \
  --config /srv/deployment/deployment.yaml --host compute-02 \
  --kolla-source /srv/kolla-ansible \
  --collections /srv/ansible-collections \
  --collections-sha256 REPLACE_WITH_REVIEWED_TREE_SHA256 \
  --ansible-playbook /srv/kolla-venv/bin/ansible-playbook \
  --output /srv/deployment/docker-compute-02
```

For Ubuntu 26, also supply `--allow-experimental-ubuntu26` on plan and apply.
The flag acknowledges an experimental path; it does not change Ansible facts or
claim the path has passed a clean-host installation test.
If deployment SSH trust lives outside the default `~/.ssh/known_hosts`, provide
`--known-hosts /absolute/path/to/trusted-known-hosts` on both commands. The file
is used by observations and the generated one-host inventory, and its hash is
bound into the plan. The phase never obtains or accepts a first host key itself.

Review `container-engine-plan.json`. It binds host identity, real OS/codename,
Docker state, observed domains/processes, selected runtime/config file hashes,
service lifecycle fingerprints and container identities/start/restart counters,
Kolla revision, collections, executable and runner/backend source hashes. Plans
expire after 900 seconds. Then repeat the same arguments with action
`prepare-container-engine` and add `--plan-sha256` with the reviewed plan digest.
Use the **same** output directory; do not generate a second plan for apply.

- `PRESERVE`: no Ansible role or package task runs; before/after observations
  must match completely, including Docker version/config, services, containers
  and observed guests/runtime files. Docker probes explicitly select the local
  Unix socket, not a caller-supplied Docker context or remote daemon. Container
  observations contain only identity/image/running/start/restart scalars, not
  container environment variables or registry credentials.
- `INSTALL`: requires no Docker installation artifacts, a prepared libvirt/QEMU
  host and no observed libvirt domains/QEMU guests. A one-host JSON inventory and
  playbook invoke the pinned role, gathering real facts and asserting the actual
  Ubuntu version/codename before package tasks. Acceptance permits only Docker's
  own service/config lifecycle changes: other tracked services, runtime/OS facts,
  domains/processes and container membership must remain unchanged. A protected
  service restart is detected as NEEDS_REVIEW; this is not a package-manager
  sandbox that can prevent every callback or roll a restart back.
- Partial Docker installation or an unreachable daemon is refused.

The write-ahead journal is `container-engine-transaction.json`; raw Ansible output
is kept in a private log and not printed to the terminal. Completed unchanged
operations can be checked again without replaying installation. Interrupted or
failed package tasks require operator review: rerunning the command does **not**
blindly repeat package actions or roll back unrelated host packages. A host-local
exclusive maintenance window is still required: the controller journal lock does
not stop another administrator from concurrently launching guests on a fresh host.

The installation subprocess has a 1800-second controller-side timeout. The journal
records the role attempt **before** invoking Ansible. Timeout, transport failure or
interruption records NEEDS_REVIEW with `remote_state_unconfirmed: true`; terminating
the controller process does not prove a remote apt/dpkg operation has stopped.
Inspect the target before any new approval. A failed post-observation also leaves
a durable NEEDS_REVIEW receipt, without logging raw exception text or credentials.
An uncatchable process kill may leave INSTALLING instead; its prewritten role
attempt still marks an incomplete, non-replayable transaction requiring review.

Do not use this phase to upgrade Docker or run it through the Kolla bootstrap
roles a second time after it has chosen `PRESERVE`. Its runtime hash checks cover
specific QEMU/libvirt files, not every host configuration or firmware property.

### 5.2. Container-engine acceptance checkpoints

Initial clean-redeployment testing on 2026-10-18 found two issues not exposed by
the earlier occupied-host acceptance. Pinned Kolla already mounts `/run` shared:
**never add nested Nova compute binds for `/run/spvm` or `/run/libvirt`**. Real
Docker reproduction in a private disposable tree produced host mount counts
`1,3,7` across three recreations with the nested bind; parent-only mounts stayed
`0,0,0`. The renderer/binder now use only the upstream parent mount for these
sockets, and workspace validation refuses `/run` extra mounts/default overrides.
Inspect `/proc/self/mountinfo` rather than relying solely on `os.path.ismount()`
for same-filesystem bind mounts. Existing mount stacks require separately reviewed
empty-host maintenance, not automatic installer cleanup or lazy unmount.
Shared mount events propagate between peers as described in the
[Linux kernel documentation](https://www.kernel.org/doc/html/v6.1/filesystems/sharedsubtree.html).

Fresh helper provisioning also exposed a directory-mode mismatch: provider and
runtime lease stores must be `0700`; only network/execution stores and state/cache
roots use `0710` where traversal is needed. The installer and permission regression
checks were corrected without weakening backend validators. A partial failed
installation is not automatically replayed: preserve its journal, review actual
installed bytes/state, and record any repair separately. These fixes are part of
ongoing clean-redeployment testing, **not** acceptance of a full fresh cloud yet.

On 2026-10-18 the hardened phase passed a real PRESERVE plan/apply/repeated apply
on occupied compute `.245`: CONTAINER_ENGINE_READY, `mutations: []`, 10 unchanged
host containers and eight tracked service units. It never invoked Ansible.
Controller `.247` independently passed direct verified-SSH before/after probe
comparison: 39 unchanged host containers, services, guest domains/QEMU processes
and Docker/runtime configuration hashes. This controller check was **not** a
second full plan/apply/repeat: the existing controller-to-compute key is not
authorized for self-SSH, and no key/trust enrollment was performed to work around
that. The working Swarm remained ACTIVE/READY and public UI/config/GraphQL all
returned HTTP200. Installer regression: 282 tests passed, including 18 targeted
container-engine tests. No real service restart was induced as a negative test;
timeout, interruption and drift rejection use fixtures.

Private live evidence on `.247`:
`/home/petr/kolla-spvm/container-engine-hardening-20261018-01`.
Plan: `43968a71e39143f56cd1994a4a93bb2ff49327407b4e7036859727e2b20e5636`.
Real OS facts remained Ubuntu26/resolute. This PRESERVE acceptance does not
authorize package tasks on a populated host.

On 2026-10-18 a separate disposable **ordinary Ubuntu 26.04 cloud VM** also passed
the real missing-Docker path. The VM was created through Nova's standard libvirt
path, with 4 vCPUs, 8 GiB RAM, a 20 GiB root disk and no GPU. Its official Ubuntu
cloud image was checked against a GPG-verified Ubuntu SHA256 manifest (image
SHA256 `8800651811af9a85465ad1d552add729947bb16488dddb4a9b5305a3d97332b2`).
Guest libvirt/QEMU prerequisites were installed separately before Docker planning;
this did not run TDX/SNP hardware bootstrap inside the VM.

- The pinned `openstack.kolla.docker` role gathered real Ubuntu26/resolute facts;
  there was no Noble substitution or manual Docker preinstallation.
- Plan/apply returned `CONTAINER_ENGINE_READY` in `INSTALL` mode. An immediate
  repeated apply returned the same completed journal without rerunning the role.
- Kolla configured `https://download.docker.com/linux/ubuntu`, suite `resolute`,
  component `stable`, signed by `/etc/apt/keyrings/docker.asc`.
- Installed packages were `docker-ce` and `docker-ce-cli`
  `5:29.9.1-1~ubuntu.26.04~resolute`, and `containerd.io`
  `2.4.2-2~ubuntu.26.04~resolute`.
- A digest-pinned `hello-world` container ran without network access, host mounts,
  capabilities or root, on a read-only root filesystem, and was removed afterward.
- Both physical hosts retained identical observed runtime/service/container
  fingerprints (10 and 39 host containers). Working Swarm UI/config/GraphQL
  remained HTTP200. All three working Nova instances remained ACTIVE.

Plan digest:
`806f5fbf979205ddb82a4904bcacc3759f976e0888d324a7869963e95a42bf9b`.
Private evidence on `.247`:
`/home/petr/kolla-spvm/docker-install-vm-20261018-01/` (installer receipts under
`installer-01/`). The disposable cloud resources were removed after acceptance;
evidence was retained privately. The tested Kolla revision and reviewed collection
digest are the pins recorded below, not a claim that arbitrary future versions work.

**Scope:** this proves the real Docker package/repository/container path on an
ordinary Ubuntu26 VM. It is not a complete clean-OS bare-metal OpenStack deployment,
hardware attestation test, or official Kolla host-support certification. Ubuntu26
still requires the explicit experimental flag, and fresh-deployment safety gates
remain in place. Do not use a missing-Docker INSTALL plan on an occupied host.

Historical acceptance on 2026-10-16:

On the existing lab controller `.247`, the new wrapper and current schema backend
were staged into an isolated operator directory. The existing controller SSH key
and independently trusted host-key file were used to observe compute `.245`.
The test explicitly refused every mode except `PRESERVE` before invoking apply.

- Plan, apply and repeated apply all passed; final status `CONTAINER_ENGINE_READY`.
- Both receipts reported `mutations: []`; no Ansible package process was started.
- The observed libvirt domain UUIDs, QEMU process IDs and selected runtime/config
  hashes remained unchanged; the existing GPU seed stayed running.
- Real facts remained Ubuntu 26.04/resolute; no noble substitution was used.
- Full local sp-vm-tools Python discovery passed: 21 tests, including 19 new
  installer/container-engine tests. Installation/failure branches use fixtures.

Private evidence on the controller is under
`/home/petr/kolla-spvm/container-engine-accept-RIksHaK6/transaction/`.
Plan digest:
`846c53a4d80adcfec42f025a31093d048df7ea6dfafba8b1d50364da1a303e1e`.
Reviewed collection-tree digest:
`781a523aed8f304c90bf4cd03d71d9d8951eb5171d90a3332a220dd5049358f7`.
These are historical lab receipts, not values to copy into a different deployment
or an unbounded authorization to replay old plans.

**Not tested here:** real Docker package installation on a Docker-free Ubuntu 26
host, broader Kolla bootstrap, initial control-plane deployment or bare-compute
registration. The live Swarm was not shut down to manufacture an empty target.

## 6. Plan networks and public ingress before installation

Provide stable, unique hostnames, management addresses, management and tunnel
interfaces, an API VIP and the selected external-network topology. Ensure targets
can reach the controller's internal services using the intended private network.
Do not expose MariaDB, RabbitMQ, libvirt sockets or management APIs on the public
Internet. Consult the pinned Kolla configuration for service-specific connectivity.

Use one shared Neutron guest network across compute hosts and Swarm clusters.
Swarm creates its own encrypted WireGuard overlays; it does not require a
separate OpenStack network for each cluster. Shared network does not mean
unrestricted security groups or unrestricted public ingress.

- OVN tunnel endpoints must reach one another. Geneve commonly uses UDP 6081;
  permit only the intended peers and account for underlay/overlay MTU.
- `host-nat` is an explicit lab-style topology, not automatic cloud networking.
  Its management/transit links, routes, forwarding and NAT must be prepared and
  reviewed separately until the generic executor implements them.
- Provider networking requires an explicitly approved external interface. Never
  attach the active SSH/default-route interface to an OVS bridge implicitly.
- Multi-host host-to-guest readiness probes need routes and return traffic. A
  healthy guest network does not prove the host helper can reach its API ports.
- Existing Docker may have a `FORWARD DROP` policy. Add only topology-specific
  forwarding rules; do not globally flush iptables/nftables or disable firewalls.

Public Swarm ingress is domain-based. Several clusters can share one public IP
through SNI routing, while HTTP routing handles required ACME paths. Unknown SNI
must be rejected. Register the shared `gateway_hostname` A/AAAA record to the
ingress address and verify that this address actually serves the ingress.
Each cluster requires distinct swarm and PKI domains and owned DNS records.
Do not overwrite another cluster's routes or zone records.

Reserve public 80/443 for Swarm ingress; keep OpenStack API ingress on the planned
private VIP/ports. Trust the OpenStack CA in CLI environments **and containers**.
Do not work around certificate failures with `--insecure`. Public Swarm TLS and
private OpenStack API TLS are separate certificate lifecycles.

For the initial IP-based API certificate/profile, leave
`haproxy_single_external_frontend=false`. Native service TLS ports are used on
the private VIP (Keystone 5000; Skyline console 9999, API server 9998).
Kolla's single external frontend selects backends by the HTTP Host/service FQDN;
it is **not** a URL-path multiplexer. Pointing every service at the same IP and
port produces ambiguous routing even if container healthchecks pass. A future
single-port deployment needs distinct service DNS names and matching TLS SANs,
not merely a port change. The pinned variable for that mode is
`haproxy_single_external_frontend_public_port`, not
`haproxy_single_external_frontend_port`.

Do not relax `/etc/kolla` permissions just to make a publisher read credentials.
The current installer keeps administrative OpenRC at `/etc/spvm/auth` root:root
0750, with `admin-openrc.sh` root:root 0640. Accounting/activation escalate before
sourcing it; the SSH account is transport only. Ingress uses the separate
`spvm-ingress` non-login system account and `/etc/spvm/ingress-auth` directory,
root:spvm-ingress 0750 with a 0640 OpenRC. The web account cannot read either.
Do not reuse the historical lab's root:SSH-user auth ownership. See the
[service-account workflow](OPENSTACK_PLATFORM_WORKFLOW.md) for isolation and
private publication handoff. Never print an OpenRC or source it with shell
tracing enabled. Controller-originated SSH probes also require both host
keys in the controller's trusted known-hosts file; a local operator's trust does
not automatically become controller trust. Copy only independently verified
public host keys, never disable StrictHostKeyChecking or use unverified TOFU.

## 7. Administrator-selected resource budgets

Do not automatically donate all physical resources to OpenStack. Current schema
expresses CPU/RAM budgets through explicit host reservations:

```yaml
compute_resources:
  reserved_cpus: 8
  reserved_memory_mb: 65536
storage_pools:
  - id: data
    path: /data/spvm-state
    mount: /data
    reserve_gib: 200
```

On a 64-logical-CPU machine this makes at most 56 vCPUs available at ratio 1.0.
Memory capacity is measured `MemTotal` minus 65536 MiB, not the marketing DIMM
capacity. To donate a desired amount, subtract it from the audited totals to
choose reservations. Current schema does **not** accept a `donated_vcpus` field.
Preflight requires an explicit CPU/RAM choice rather than accepting renderer
fallbacks. Leave headroom for OS, control-plane containers, QEMU and ingress.

Nova reservations limit scheduler capacity; they are not CPU affinity or a host
cgroup guarantee. CPU isolation/pinning requires an additional explicit design.
Project quotas are a separate aggregate limit, not a per-host placement guarantee.
Every VM must fit on one eligible host; forcing a host does not bypass capacity.

For state storage use existing, persistently mounted local ext4/XFS filesystems.
The current audit rejects the root filesystem, symlinks, remote filesystems and
counting the same filesystem twice. It never partitions, formats or mounts disks.
Multiple physical filesystems may be separate pools; two directories on one
filesystem are not two independent capacity sources. One state disk must fit in
one pool; the installer does not concatenate disks automatically.

`reserve_gib` is a safety reserve, not the configured state budget. The helper's
explicit pool budget must remain below physical capacity minus reserve, with
headroom for existing data. It is reconciled with Placement inventories, claims
and actual filesystem pressure. Do not equate sparse qcow2 allocated bytes with
logical state reservations: four 300 GiB reservations must not fit a 900 GiB
budget merely because the files have not grown yet. External consumers can still
exhaust a filesystem, so ongoing free-space checks remain necessary.

GPU discovery reports BDF, vendor/product, driver and IOMMU isolation. A detected
card is not automatically free or approved for OpenStack. Administrator approval,
host-helper registered devices, Nova PCI `device_spec` and aliases must agree.
GPU selection is currently reviewed in the prepared runtime, **not a completed
interactive GPU picker in this wrapper**. Native Nova PCI claims determine
occupancy; SPVM checks must agree with domain assignments. PCI-in-Placement
reporting remains disabled in the tested profile.

## 8. Select the driver backend

For development use an explicitly trusted swarm-cloud checkout:

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/swarm-cloud -- --help
```

No implicit sibling checkout, clone, pull or branch change occurs. Directory
mode trusts executable code in the supplied directory; review it first. A checkout
can be dirty for development, but that is not release provenance.

For an archive, obtain its SHA256 through a trusted release channel first:

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-archive /srv/releases/nova-spvm-VERSION.tar.gz \
  --sha256 REPLACE_WITH_64_LOWERCASE_HEX_DIGITS \
  --staging-dir /srv/staging/driver-01 -- --help
```

For a public GitHub release replace `--driver-archive` with
`--driver-release OWNER/REPOSITORY@EXACT_TAG`. The release must contain exactly
one `nova-spvm-*.tar.gz` asset. The same SHA256 and new staging directory are
required. Private-release authentication is not implemented in this wrapper;
download privately through your approved tooling, then use archive mode.

Archive staging checks the archive digest before extraction, rejects traversal,
links, special files, bytecode and oversized bundles, and verifies clean-commit
source manifest checksums before starting the backend. SHA256 proves identity
against the expected digest, **not publisher authenticity by itself**. Failed
staging is preserved for inspection and never reused or automatically deleted.
After successful staging, use `--driver-dir` with the extracted bundle root for
subsequent commands; do not re-extract into the same directory.

Older driver releases lack this preflight and required Kolla assets. Use an
updated backend, not an old bundle solely because its version number looks right.

## 9. Run basic checks and render a reviewable plan

Start from the selected backend's `deployment.kolla-two-host.yaml` as a schema
example. It is a **lab snapshot**, not a portable ready-to-apply inventory. Replace
all addresses, aliases, SSH keys, interfaces, domains, image identities and storage
paths. Keep provider files private. Do not copy lab machine identities or UUIDs.

Use an unused output directory for each generation:

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/swarm-cloud -- preflight \
  --config /srv/deployment/deployment.yaml --output /srv/deployment/check-01
```

The command uses trusted SSH and `sudo -n` to observe targets. It creates local
private reports only; it does not install Docker, restart services, launch guests
or change remote configuration. Read `basic-preflight.json` for blockers and
warnings. Exit 2 means blocked; exit 0 is only `BASIC_CHECKS_PASSED_NOT_DEPLOYABLE`.
Audit data expires after 900 seconds and includes host identities.

Read-only acceptance on 2026-10-16 against the existing two-host lab (`.247` and
`.245`) returned no basic blockers, preserved real Ubuntu 26.04 facts and chose
`preserve-existing` on both hosts. This did not install packages or restart any
service/guest. Local tests cover mixed existing/missing Docker policies, broken
Docker refusal, stale/bound reports and safe artifact extraction. These results
do not constitute a clean-OS deployment acceptance.

Render using a checkout containing the pinned Kolla commit:

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/swarm-cloud -- render \
  --config /srv/deployment/deployment.yaml \
  --audit /srv/deployment/check-01/audit.json \
  --preflight /srv/deployment/check-01/basic-preflight.json \
  --kolla-source /srv/kolla-ansible --output /srv/deployment/rendered-01
```

Rendering verifies the preflight binding/freshness and preserves the complete
pinned upstream inventory group hierarchy. Review `hosts.ini`, `globals.yml`,
`host-profile.yml`, `resource-plan.json` and `deployment.lock.json`. A render
without reconciled cloud accounting is not ready for existing-cloud activation.
Do not overwrite `/etc/kolla` by copying the entire render directory.

## 10. Fresh cloud installation: manual integration boundary

### 10.1. Initialize isolated new-cloud configuration

`init-kolla` materializes the reviewed render into a **new private directory**.
It does not merge with `/etc/kolla`, load existing cloud passwords or contact the
inventory hosts. This is initial-cloud setup, **not** an add-compute operation.

Use the pinned Kolla checkout and its compatible dependency virtual environment.
The command invokes the checkout's own `kolla_ansible/cmd/genpwd.py` against a
pristine `etc/kolla/passwords.yml` obtained from the pinned commit, not from a
working deployment. The same reviewed collection-tree digest from section 5 is
required. A fresh passing bound preflight is needed to produce the render; local
workspace creation is not a substitute for refreshing host observations before
an actual deployment.

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/swarm-cloud -- init-kolla \
  --config /srv/deployment/deployment.yaml \
  --rendered /srv/deployment/rendered-01 \
  --kolla-source /srv/kolla-ansible \
  --kolla-python /srv/kolla-venv/bin/python \
  --collections /srv/ansible-collections \
  --collections-sha256 REPLACE_WITH_REVIEWED_TREE_SHA256 \
  --ack-new-cloud --output /srv/deployment/new-cloud
```

Parent directories must already exist. The output must not exist. Never point
this at a live Kolla configuration directory. An existing/partial workspace is
refused, so a repeated command cannot silently regenerate credentials or a CA.
Initialization failure leaves `journal.json` with a review status and private
artifacts; do not retry into that directory or remove it without inspection.

The workspace contains:

```text
new-cloud/
├── hosts.ini
├── host-profile.yml
├── workspace.json                 # private provenance and file hashes
├── journal.json
├── private-init.log
├── private-api-ca.key             # stays OUTSIDE the Kolla trust-copy directory
└── kolla/
    ├── globals.yml
    ├── passwords.yml
    ├── config/nova/...            # reviewed per-host overrides
    └── certificates/
        ├── ca/ca.crt              # public CA trust anchor only
        ├── api.key
        ├── api.csr
        ├── api.crt
        ├── haproxy.pem
        ├── haproxy-internal.pem
        ├── proxysql-ca.pem        # public internal CA, not its private key
        ├── proxysql-cert.pem      # same approved internal frontend leaf
        └── proxysql-key.pem       # private frontend key, mode0600
```

The root directory is 0700; generated files are 0600. Treat the entire workspace
as secret, including its manifest/logs. The private API CA key is never placed
in `certificates/ca/`, which Kolla uses for copying trust anchors to containers.
Back up this workspace securely before a future deployment; preserve the original
passwords instead of generating replacements during add-compute or maintenance.

The new certificate is for the configured API VIP, with an IP SAN, server-auth
usage and a 397-day validity period; the private CA has a 10-year validity period.
Chain, VIP identity, at least one remaining day of validity and key matching are
checked with OpenSSL. This initial interface generates an IP-based private API
certificate; custom API DNS names/external PKI import and certificate rotation
remain separate work. It does not automatically install the new CA into system
or container trust stores.

**This is an OpenStack API CA, not a Swarm CA.** It neither reads nor replaces
guest-generated CA keys/certificates, and does not restart bootstrap or join VMs.

Verify the materialized inputs without printing credentials:

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/swarm-cloud -- check-kolla-workspace \
  --output /srv/deployment/new-cloud
```

Hash drift, extra files, symlinks, broad permissions, an unfinished journal or
invalid TLS block the check. Success remains
`KOLLA_INPUTS_INITIALIZED_NOT_DEPLOYED`, **not** cloud readiness. The manifests
are operator-private review artifacts, not an independent trust authority.

### 10.2. Parse the pinned Kolla task graph without running it

```bash
bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/swarm-cloud -- list-kolla-tasks \
  --output /srv/deployment/new-cloud
```

This invokes the pinned Kolla CLI's `prechecks --list-tasks` with the workspace's
explicit `--configdir`, inventory and profile. The command cannot select deploy,
bootstrap, reconfigure or real prechecks. Source and collection identities are
checked, Ansible/Kolla environment overrides are isolated, and raw output stays
in a separate private sibling report directory. Inventory hosts are not contacted
by remote tasks. The frozen input directory is checked again afterwards.

Success is `KOLLA_TASK_PARSING_PASSED_NOT_PRECHECKED`. Dynamic task includes may
not be fully expanded by Ansible's list mode. This proves task/inventory loading,
**not** package availability, networking, API health or host readiness.

Controller acceptance on 2026-10-16 passed real upstream password generation,
OpenSSL chain/IP/key checks, workspace validation and task listing. Evidence is
under `/home/petr/kolla-spvm/workspace-accept-6cLxu8lE/` on the lab controller.
Active `/etc/kolla/globals.yml` and `passwords.yml` fingerprints, both controller
libvirt domain IDs and the Nova compute unit invocation were unchanged. These
test inputs must not be used to redeploy that running cloud with new credentials.

### 10.3. Provision the SPVM helper on a fresh prepared compute

The wrapper owns `plan-host-helper` and `prepare-host-helper`. Unlike controller
workspace operations, these run **as root on the target compute**. They are a
fresh-install path, not maintenance or a way to adopt existing helper/Nova state.
There is no CLI option to redirect system writes into another filesystem root;
the alternate-root implementation is used only by in-process tests.

Prerequisites for this phase:

- Finish hardware/bootstrap changes and required reboots first.
- No libvirt domains, direct QEMU guests or running Nova compute process.
- No existing helper installation, units, compute ID, runtime state, old SPVM
  config or nonempty Nova data directory. Existing artifacts require a separate
  reviewed migration; the installer never purges them.
- Existing mounted local ext4/XFS state filesystems, with empty state roots
  (which may be absent), sufficient free space and root-controlled ancestors.
- Either a root-owned installed Nova-compatible measured launcher, or a verified
  launcher staging directory prepared as described below. Launcher hashes are
  bound into the plan. **A raw standalone launcher is not sufficient.** An existing
  launcher is never overwritten; staging installation requires an absent tools
  destination. Neither path reruns bootstrap or patches the source checkout.
- Compatible driver-bundle sources and required bootstrap/runtime tools. The
  manifest must declare grant-bound release selection and guest API registry
  presence; a release default also requires `release-only-helper-default`.
  Old published bundles are refused before writes instead of producing a partial
  release-only installation. A development checkout is not a published release.

Copy [the request example](openstack-helper-request.example.json) to a private
operator file on the target and edit every host-specific value. It is intentionally
invalid until edited: the machine fingerprint is all-zero and compute UUID is a
placeholder. Do not reuse another host's compute UUID.

`machine_sha256` is the preflight's `host_identity_sha256`: SHA256 of the **stripped**
`/etc/machine-id` text, not the raw file including its newline. For example:

```bash
python3 -c 'import hashlib; from pathlib import Path; print(hashlib.sha256(Path("/etc/machine-id").read_text().strip().encode()).hexdigest())'
python3 -c 'import uuid; print(uuid.uuid4())'
```

Generate the compute UUID once and keep it for that host/cloud identity. The
installer writes it to `/var/lib/nova/compute_id`, owned by Kolla's Nova UID/GID
42436, before initial registration. Kolla must use the matching host Nova data
bind; verify the resulting Nova/Placement identity before enabling scheduling.
Do not replace an existing registered identity with a new UUID.

Request fields are deliberately explicit:

| Field | Meaning |
| --- | --- |
| `release` | Initial enrolled guest build, for example `build-449-release` |
| `allow_debug` | `false` for release-only; a debug build/cache requires explicit `true` |
| `tools` | Root-owned Nova-compatible launcher directory; must be absent when installing from `--launcher-stage` |
| `cache` | Private traversable cache for this build; cannot overlap journals/state/tools |
| `pools` | Unique pool IDs, existing mountpoints, roots, logical budgets and reserves |
| `gpus` | Approved canonical BDF list; `[]` means no GPU donation, never automatic all |

Debug is off in the shipped request. Registration and host launch admission are
separate opt-ins; see [Debug builds](OPENSTACK_DEBUG_BUILDS.md) for fresh-install
inputs, defaults, validated changes on an existing installation, and rollback.

Selected GPUs must already be NVIDIA display-class devices bound to `vfio-pci`,
with a single-device IOMMU group. The phase does not change PCI drivers, GPU modes
or firmware. More complex shared-IOMMU/NVSwitch layouts are not silently accepted.
Discovery/bootstrap capability checks and administrator selection remain separate
from Nova's later ownership/availability accounting.

Each state budget plus reserve must fit current available bytes. The same physical
filesystem cannot be advertised twice through two directories. State/cache/runtime
roots cannot overlap. Existing cache contents are preserved only when ownership
and private traversal permissions are suitable; state roots must be empty.
CPU/RAM reservations remain in the deployment's Nova overrides, not this request.
GPU/pool choices must agree with those overrides and the eventual Placement/PCI
registration. Compilation of this request directly from the deployment inventory
and cross-host execution are still future orchestration work.

Plan on the **target compute** using a trusted selected backend:

```bash
sudo bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/reviewed-driver-bundle -- plan-host-helper \
  --request /root/helper-request.json --output /var/lib/spvm-helper-install-01
```

Review `helper-plan.json`. It expires after 900 seconds and binds the physical
host, selected compute UUID, sources/manifest/installer hashes, launcher, service
identity, pool device IDs, budgets and measured-runtime hashes. The plan performs
host observations and writes a private local review artifact; it installs nothing.
The source directory is a trusted executable supply, not tenant input. Use a
checksum-verified release when available and review directory-mode development code.

Apply with the same arguments, changing the action and providing the approved hash:

```bash
sudo bash /srv/sp-vm-tools/scripts/install-openstack.sh \
  --driver-dir /srv/reviewed-driver-bundle -- prepare-host-helper \
  --request /root/helper-request.json --output /var/lib/spvm-helper-install-01 \
  --plan-sha256 REPLACE_WITH_APPROVED_PLAN_SHA256
```

The phase creates the dedicated `kolla-nova` identity only when absent and refuses
UID/GID collisions, including UID aliases. It installs fixed helper/protocol
sources, private host configuration, public compute/pool IDs, narrow libvirt
PolicyKit rules and helper units; prepares owned runtime roots and the compute ID;
validates installed configuration; checks unprivileged libvirt access; and enables
only `host-helper.socket`. A peer-UID-authenticated ping verifies capabilities.
It never calls a VM lifecycle operation, restarts libvirt/Nova/Docker, formats a
disk, rebinds a GPU or installs host hypervisor packages.

The journal is written before changes. A failure leaves
`NEEDS_REVIEW_PARTIAL_HOST_INSTALL`; writes are not blindly replayed and unrelated
artifacts are not rolled back/deleted. A completed fresh install also refuses
being overwritten. Use a separate maintenance/verification path, not deletion of
the journal to force a second install. Maintain an exclusive host maintenance
window; the local journal lock does not fence unrelated administrators/processes.

Success is `HOST_HELPER_INSTALLED_NOT_ENROLLED`, **not compute/cloud readiness**.
Runtime API enrollment (`runtime-services.json`), API CA trust, project/network
authorization, Nova custom image/overrides, state traits/providers and native PCI
accounting still have to agree before scheduling is enabled. No tenant credentials
or Swarm CA private keys are generated by this phase. The API registry-presence
gate remains mandatory; config validation alone is not a signature-presence test.

Release-only installation does not require a dummy debug default/cache. Existing
legacy debug configurations remain compatible and still require explicit debug
opt-in. Do not change a running helper's default release/cache as an unreviewed
migration: execution journals bind those values. The historical protocol capability
name `experimental-debug-execution-v1` remains for compatibility; it does not force
selection of a debug guest build or enable SSH in a release guest.

Acceptance on 2026-10-16: a root-local `plan-host-helper` on busy `.245` refused
the host before creating any plan directory. The revised execution validator also
accepted an **in-memory** build449 release-only candidate using existing trusted
launcher/cache paths, with debug disabled. Installed config/code hashes, running
domain ID and Nova compute invocation remained unchanged. Private operator staging
was `/home/petr/helper-guard-CxM1BgkC/`; no candidate config was written there or to
`/etc/spvm`. Unit fixtures exercise actual file materialization with mocked accounts,
mount observations and system commands. A real clean-host apply is not yet accepted.

### 10.4. Reproduce and install the Nova-compatible launcher

The driver bundle owns `deployment/launcher-recipe.json` and the ordered Nova
patches. The tools installer owns assembly. The initial reviewed recipe pins
sp-vm-tools commit **`22f4113f44e1718c901b419f933b0d779510fbd2`**, eight patches,
all three input hashes and all three resulting hashes. Updating a source commit
requires a reviewed recipe update, not substituting `main` or skipping a patch.
The working tree may be dirty: only pinned Git blobs are read. Untracked keys,
provider configs, bootstrap scripts and other repository files are never copied.
This currently requires a local tools checkout containing the pinned commit;
there is no implicit clone, pull or tools-source download.

Create a new private staging directory (its parent must already exist):

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  stage-launcher --tools-source /srv/sp-vm-tools \
  --tools-revision 22f4113f44e1718c901b419f933b0d779510fbd2 \
  --output /srv/launcher-stage-01

./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  check-launcher --output /srv/launcher-stage-01 \
  --receipt-sha256 '<receipt_sha256 returned by stage-launcher>'
```

These operations invoke Git and syntax checks (`bash -n` and Python AST parsing),
not the launcher, bootstrap or any guest. Staging status is explicitly
`LAUNCHER_STAGED_NOT_INSTALLED`. The receipt binds the recipe and output hashes;
this proves reproduction of the selected trusted recipe, not publisher identity
or guest attestation. Use the same independently trusted driver bundle for check
and installation. No recipe/patch matching is guessed. A failed assembly leaves
its directory for inspection without a success receipt; do not reuse that path.

For installation on a **fresh prepared compute**, set `tools` in the helper request
to the intended root-owned system destination (for example `/opt/sp-vm-tools`).
It must not exist, including an empty directory. Place the reviewed staging and
driver sources on that compute, then pass both flags to **both** helper phases:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-host-helper --request /root/helper-request.json \
  --launcher-stage /srv/launcher-stage-01 \
  --launcher-sha256 '<approved launcher receipt_sha256>' \
  --output /var/lib/spvm-helper-install-01

sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-host-helper --request /root/helper-request.json \
  --launcher-stage /srv/launcher-stage-01 \
  --launcher-sha256 '<same approved launcher receipt_sha256>' \
  --output /var/lib/spvm-helper-install-01 \
  --plan-sha256 '<approved helper plan_sha256>'
```

The helper plan binds the staged receipt and exact launcher bytes. Apply verifies
them again and installs the three files root-owned, executable, with exclusive
creation, inside the existing write-ahead-journal transaction. No separate copy,
`chmod -R`, replacement of `/opt/sp-vm-tools`, or launcher invocation is needed.
Existing installed launchers remain supported by omitting both staging flags;
that mode verifies installed paths/hashes without modifying them. The installer
still refuses running guests, existing Nova/helper state and partial-install replay.

Local acceptance on 2026-10-16 reproduced the reviewed real commit/patches and
verified its receipt. Unit tests exercise staged installation in a temporary
filesystem with host actions mocked, including refusal to replace existing tools.
This is **not** acceptance of a fresh system install or a new guest launch. The
running lab cluster and its installed launcher were not changed.
Read-only SHA256 comparison against `/opt/sp-vm-tools/scripts` on `.247` also
matched **all three** staged files byte for byte. This binds reproduction to the
currently installed runtime, not merely to an untested source recipe; it still
does not replace fresh-host installation or guest-launch acceptance.

### 10.5. Stage and build the custom Nova compute image

The driver bundle supplies `compute-image.Dockerfile.in` and the offline driver
import smoke test. The tools installer owns `stage-compute-image` and
`build-compute-image`. The output is a **new local image**, not deployment or
replacement of a running compute container. This phase does not invoke Kolla.

Select a Linux/amd64 Kolla `nova-compute` base by immutable registry digest.
Have an administrator preload that exact base on the build host; the build command
does not pull it. Use a compatible base with all driver dependencies already
installed in `/var/lib/kolla/venv`. The generated Dockerfile uses
`pip install --no-index --no-deps` and `pip check`: missing dependencies cause a
visible failed build, never an implicit download or Nova dependency upgrade.
There is currently no automatic offline dependency-wheelhouse assembly. A base
with an existing SPVM shim is rejected; do not use an old custom SPVM image as an
unreviewed base overlay.

Select the driver wheel from the same reviewed bundle, with its trusted SHA256.
For a trusted development checkout, build its wheel separately with the available
build toolchain; this is not a clean published release. In either case staging
checks wheel package/version, safe bounded ZIP layout and exact Python-source
equality with the selected driver tree. Unexpected payloads such as `.pth` files,
additional modules or a stale wheel under the same version are refused. A bundle's
declared wheel checksum is checked as well.

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  stage-compute-image \
  --base-image 'registry.example/kolla/nova-compute@sha256:<64-hex-digest>' \
  --wheel /srv/reviewed-driver-bundle/wheels/nova_spvm-0.2.21-py3-none-any.whl \
  --wheel-sha256 '<trusted wheel SHA256>' \
  --output /srv/compute-context-01

sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  build-compute-image --output /srv/compute-context-01 \
  --context-sha256 '<approved context_sha256 returned by staging>' \
  --tag spvm/nova-compute:reviewed-install-01 \
  --journal /var/lib/spvm-image-build-01
```

The build caller must own the private context. If switching from a deploy user to
root, review and transfer ownership of the **specific new context directory** and
its files first; do not weaken its private modes. Docker access means privileged
host access; this is not a sandbox for untrusted driver bundles. The installer
targets only the local `/var/run/docker.sock`, not an inherited Docker context or
remote daemon. It sanitizes Docker environment variables and ignores user Docker
credentials/config. The base must already have its exact RepoDigest locally.

Review the context receipt before build. Only Dockerfile, the verified wheel and
smoke test enter the context (plus its non-secret receipt); extra files, symlinks
and changed inputs are refused. Build uses `--pull=false --network=none` so RUN
steps cannot fetch dependencies. Existing tags, including successful prior builds,
are never intentionally overwritten. Use a new explicit non-floating tag and a
new journal directory. Coordinate with other image builders so nobody races to
assign that tag. No image/container/cache cleanup is performed on failure.

The write-ahead journal records context and base image identity. Failed builds
become `NEEDS_REVIEW_IMAGE_BUILD`, retaining private logs; do not replay blindly
or assume a leftover image tag is accepted. A successful build runs the offline
Nova driver import smoke test as `nova`, checks runtime user and context label,
and records `COMPUTE_IMAGE_BUILT_NOT_DEPLOYED` with the immutable local `image_id`.
That ID is **not** a registry RepoDigest. After separately approved image
distribution, use the tag/reference and matching per-host image ID for
`kolla.compute_image` / `kolla.compute_image_id`; never assume a local tag exists on
other computes. Use the verified delivery phase below; Kolla activation and
runtime acceptance remain separate integration work.

Acceptance on 2026-10-16: the real current development wheel was source-matched
and built on `.247` from
`quay.io/openstack.kolla/nova-compute@sha256:0e6a281d09f5841eb04154a19d2e1784580dd85646604b26e9c48ba7ef1e0cac`.
Wheel SHA256:
`2ef05798e9b0a021902af0a998b71286842a044bc5e6478aaa1c15aaf3ff9f16`.
The final offline build passed dependency checks and the Nova-user driver import
smoke test. Its **test-only** tag is
`spvm/nova-compute:installer-accept-20261016-03`, image ID
`sha256:1b2bd23a8a2f76792cdc9e641dd5e899c7b5c74a5860c5aacfc20909df169a16`.
Evidence is under `/home/petr/kolla-spvm/image-build-accept-U7YvxGB8/build-03/`.
This is a development acceptance artifact, not a signed/published driver release.
It was not activated in Kolla or used to launch guests.

The real build caught a permissions pitfall that fixture tests had not: private
context file/directory modes can propagate through Docker COPY. The template now
sets the smoke script to `0644` and its `/usr/local/libexec` directory to `0755`
before switching to `USER nova`. Keep the host build context private; do not
solve container readability by making the host context world-readable. The two
earlier failed attempts and their journals were preserved for diagnosis.

### 10.6. Deliver the accepted compute image to another host

`export-compute-image` packages an accepted build; `deliver-compute-image`
transfers it over explicitly trusted SSH and invokes `import-compute-image` on
the destination. None of these commands updates Kolla config, starts/stops a
container, calls Nova or manipulates a VM. Importing an image is permitted while
workloads run; **activating** it remains a separate reviewed operation.

On the build host, independently review the successful `image-build.json`, its
SHA256 and the immutable image ID. Export into a new private directory:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  export-compute-image \
  --build-receipt /var/lib/spvm-image-build-01/image-build.json \
  --build-sha256 '<approved SHA256 of that build receipt file>' \
  --image-id 'sha256:<approved local image ID>' \
  --output /srv/compute-image-package-01 --reserve-gib 10
```

The command verifies current tag/image ID/context against the approved build,
reserves free space conservatively, runs Docker save and checks the archive's
single intended tag. Export records archive size and SHA256, source image ID,
build receipt SHA256 and build-context hash in `image-package.json`. Approve the
returned `package_sha256` separately. This is the canonical digest of package
metadata, **not** the raw JSON file checksum and not the tar SHA256. Partial
exports keep a failure journal without a successful package receipt.

For each target, obtain the machine fingerprint through a trusted channel:
SHA256 of the **stripped text** in `/etc/machine-id`. Hashing the file including
its final newline produces a different value. The fingerprint binds the command
to that OS installation, not cryptographic hardware attestation. SSH host-key
verification remains mandatory.

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  deliver-compute-image --package /srv/compute-image-package-01 \
  --package-sha256 '<approved package_sha256>' \
  --host deploy@compute.example.net --ssh-port 22 \
  --identity-file /root/.ssh/openstack_deploy_ed25519 \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --machine-sha256 '<approved target machine fingerprint>' \
  --output /var/lib/spvm-image-delivery-compute01 --reserve-gib 10
```

Run delivery as the owner of the private package, with read access to the explicit
SSH identity/trust files. The remote user needs Python 3.11+ and non-interactive
sudo for the installer; Ubuntu 24/26 provide a suitable Python. Only `user@host`
DNS names/IPv4 are currently accepted, not bracketed IPv6 destinations. SSH ignores
user SSH config, disables agent use/forwarding and enforces strict host-key checks.
No TOFU or implicit credential discovery is performed.

Delivery checks the target machine and `/var/tmp` staging capacity before making
a remote directory. It copies only `image.tar`, its package receipt and three
installer modules, never a private key, provider config or OpenStack credentials.
Received file hashes are verified before executing the installer as root; hashing
is streamed, not loaded into RAM. The private staging directory is then root-owned.
Source/remote journals retain exact staging locations for diagnosis and cleanup.

Import rechecks target identity, tar hash/size/layout, existing tags and available
Docker storage. It refuses a tag that points elsewhere. An already matching image
is a no-op. Docker load is followed by an exact image-ID, Linux/amd64, `nova` user
and build-context-label check. OCI containerd archives may include a short
`org.opencontainers.image.ref.name` alongside a full `io.containerd.image.name`;
both must agree with the approved Docker manifest tag. Image identity must survive
transport: a daemon that changes it is not silently accepted as equivalent.

Successful delivery reports `COMPUTE_IMAGE_DELIVERED_NOT_DEPLOYED`; failures keep
`NEEDS_REVIEW_IMAGE_DELIVERY` and any remote import journal. Never retry a partial
transaction blindly or remove mismatched tags automatically. Coordinate image
writers so they cannot race to assign the selected tag. Temporary archives,
remote staging and Docker cache are **not** automatically deleted.

For offline/manual transport, copy the package to a private directory on the
target, review ownership, then invoke the same root-local importer:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  import-compute-image --package /srv/received-image-package-01 \
  --package-sha256 '<approved package_sha256>' \
  --machine-sha256 '<approved local machine fingerprint>' \
  --output /var/lib/spvm-image-import-01 --reserve-gib 10
```

Acceptance on 2026-10-16: `deliver-compute-image` exported the real test image from
`.247` and loaded it on `.245` over the pre-existing trusted deployment SSH
connection. The exact image ID survived Docker/containerd save/load:
`sha256:1b2bd23a8a2f76792cdc9e641dd5e899c7b5c74a5860c5aacfc20909df169a16`.
Approved package digest:
`68f5d73fab0b7493268adbfa80ec1c829ea7a966949f13ddec7533cc9ae98517`.
Source evidence:
`/home/petr/kolla-spvm/image-transfer-accept-KVMQxg31/delivery-01/` on `.247`;
target staging `/var/tmp/spvm-image-receive-vx0z5phj` and sibling `-import` journal
on `.245`. A repeated target import returned
`COMPUTE_IMAGE_ALREADY_PRESENT_NOT_DEPLOYED` without another load. Both running
Nova containers retained their IDs, start times and previous active image
`sha256:44c8802cdf14dfbe6e7aeb3ffbd97d6babbb50740c4732c631c2f06e9251a97b`.
The existing seed remained running. Temporary packages and diagnostic logs were
retained; this acceptance did not deploy Kolla or boot a guest with the new image.

### 10.7. Bind the compute configuration for initial cloud deployment

There is a bootstrap dependency: a new cloud does not yet have real project/user/
network IDs, but Nova's SPVM runtime config bind must already exist when its
container starts. Do **not** invent enrollment IDs, grant wildcard access or put
operator credentials in that file. The driver now supports an explicit
`bootstrap_deny_all: true` document with `projects: {}`. It composes zero service
readers, denies every SPVM project before API access, and does not affect ordinary
non-SPVM guests. Empty projects without that explicit mode remain invalid. The
flag cannot coexist with enrolled projects or debug-only legacy mode.

The selected driver bundle must declare `deny-all-runtime-bootstrap`. Old images
and bundles, even if their development version string is also `0.2.21`, are not
interchangeable with the new source/wheel. Image source equality and manifest
hashes are checked; rebuild and verify the matching image. Existing working
clusters keep their enrolled runtime config unchanged.

`stage-kolla-computes` combines a checked initialized Kolla workspace, its exact
config/render, a source-matched image build context and one explicit helper request
per audited compute. It creates a **new private workspace**. It does not contact
hosts, install helpers, load images or invoke Kolla. The source workspace is
checked before and after; passwords, API CA and private keys are copied unchanged,
not regenerated. Never run both workspaces as independent deployments of the same
cloud.

Set `kolla.compute_image` and `kolla.compute_image_id` in the deployment config to
the approved built image **before** audit/render/init. Provide a resource-validated
render, not a fragment or a render without physical host facts. Copy and edit the
[compute-request mapping example](openstack-compute-requests.example.json): keys
must match **every** inventory host with the compute role, including a controller
that also runs compute. Values are absolute paths to the host-specific helper
request JSON files described in section 10.3. No default GPU selection, new compute
UUID or disk budget is silently invented.

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  stage-kolla-computes --config /srv/deployment.yaml \
  --rendered /srv/reviewed-render --workspace /srv/initial-kolla-workspace \
  --requests /root/compute-requests.json \
  --image-context /srv/compute-context-01 \
  --image-context-sha256 '<approved image context_sha256>' \
  --region RegionOne --output /srv/bound-kolla-workspace

./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  check-kolla-workspace --output /srv/bound-kolla-workspace
```

The binding verifies unique machine fingerprints/compute UUIDs, default guest
release, exact state pool IDs/paths/mounts/reserves, positive budgets within
audited ceilings and selected GPU BDFs within audited VFIO candidates. It generates
per-inventory-alias Nova driver/libvirt/CPU/RAM/device overrides, matching scheduler
PCI aliases/filter, fixed helper/runtime/pool mounts and the explicit compute
image reference. Only **selected** GPUs are registered, including an explicit
empty list; discovery alone does not donate hardware. CPU/RAM allocation ratios
remain 1.0. Region is set consistently in Kolla globals and the bootstrap policy.
The initial Keystone URL is the private API VIP on HTTPS port 5000 `/v3`.

Review `compute-binding.json`, its `binding_sha256`, generated Nova overrides and
`helper-requests/<host>.json`. The status is deliberately
`COMPUTE_INTENT_BOUND_NOT_DEPLOYABLE`. It is configuration intent, not a claim that
a GPU is free, an image was delivered, a compute UUID is registered or physical
facts are still fresh. Helpers must still be applied on the matching fresh host,
and image/CA/network prerequisites revalidated before any Kolla deployment.

On each fresh compute, use that generated request and the binding's bootstrap
endpoint/region in **both** helper phases, in addition to any launcher flags:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-host-helper --request /root/compute01-helper-request.json \
  --bootstrap-auth-url https://192.0.2.10:5000/v3 --bootstrap-region RegionOne \
  --output /var/lib/spvm-helper-install-01

sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-host-helper --request /root/compute01-helper-request.json \
  --bootstrap-auth-url https://192.0.2.10:5000/v3 --bootstrap-region RegionOne \
  --output /var/lib/spvm-helper-install-01 --plan-sha256 '<approved helper plan>'
```

Replace the example URL with the exact generated binding. The helper transaction
installs the root-owned `0644` deny-all `/etc/spvm/runtime-services.json` together
with its other fixed files and binds its bytes into the approval plan. Omitting
both bootstrap flags retains the earlier helper-only path; supplying only one
is refused. Installed helpers or live guests are never adopted/reconfigured by
this fresh-only workflow.

After initial OpenStack deployment, create and validate the real project/users,
shared network and Barbican permissions, then enroll explicit IDs/releases using
a separately reviewed runtime configuration step. The deny-all flag must be
removed when projects are enrolled. This work does not authorize guest or Swarm
bootstrap restart; existing ephemeral guest state remains non-restartable.

Acceptance on 2026-10-16: all 539 Nova driver/CLI tests passed, including explicit
bootstrap rejection and unchanged ordinary-guest behavior. The compute binding
and fresh-helper paths passed fixture tests, with source credential preservation,
GPU selection/budget checks, failure journaling and traversal rejection.

A real new test image was also built on `.247` from the current source-matched
development wheel. Its offline smoke test, running as `nova`, reported
`bootstrap_deny_all: true` together with successful driver/class loading:

- Tag: `spvm/nova-compute:bootstrap-accept-20261016-01`.
- Image ID: `sha256:6da8b1f285d8b7a1dc5ed1a6cbfe9152544e23e495e6489258f507a890e03155`.
- Wheel SHA256: `b1891761523cfecc0be2fbe4d0f573f189cbf9ee2f55784d37b0120dcb376da7`.
- Context digest: `64847d4c1acd2ae482eb0a18f16a245b092e2f72a314c13f3995bac134388c45`.
- Evidence: `/home/petr/kolla-spvm/bootstrap-image-accept-dBrHcAM7/build-01/`.

That image was not activated or distributed. The active `.247` Nova container
retained its previous ID/start time and image. This is not fresh-host Kolla
deployment acceptance; fixture-generated compute intent must not be deployed to
the running cloud.

### 10.8. Plan and run real Kolla prechecks on prepared empty hosts

`list-kolla-tasks` only parses tasks. The new `plan-kolla-prechecks` /
`run-kolla-prechecks` workflow can execute the real pinned Kolla `prechecks`
action, but only after fresh per-host verification and explicit plan approval.
It never substitutes `bootstrap-servers`, `deploy`, `reconfigure` or `destroy`.
It is for an initial cloud, not a way to run an experimental new workspace
against a working installation.

Prerequisites are the compute-bound workspace from section 10.7, installed fresh
helpers with matching deny-all runtime, the approved image already present on
each compute, usable Docker, required prepared network interfaces and any host
dependencies required by Kolla. This phase does **not** install missing packages,
configure network links, distribute API trust or fix failed prechecks automatically.
If dependencies are missing, preserve the failed run. Section 10.9 covers the
limited base-package preparation step; other dependencies still need separately
reviewed preparation. Do not skip upstream assertions.

The basic audit and binding now retain a unique machine fingerprint for **every**
role, including controller-only hosts, not just computes. An older binding without
the `machines` map must be re-staged from its original unbound workspace. Do not
hand-edit a receipt or invent a fingerprint to pass this check.

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-kolla-prechecks --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/initial-prechecks-01

./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  run-kolla-prechecks --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/initial-prechecks-01 \
  --plan-sha256 '<approved plan_sha256>'
```

Use the private workspace owner with explicit per-host `identity_file` in the
deployment config and working non-interactive sudo on targets. The Ubuntu 26
flag acknowledges experimental support; it does not falsify Ansible facts.
An explicit SSH trust file is used by both probes and Ansible. User SSH config
is ignored, agent use/forwarding is disabled, and host-key checking stays strict.

Review `prechecks-plan.json` before execution. A blocked plan returns a failing
exit code and names blockers. The 900-second approval binds workspace/config,
helper/launcher source hashes, SSH identity/trust file hashes, pinned Kolla source,
collection tree, and per-host observations. Execution repeats those checks;
changed inputs or hosts invalidate the plan. Observation collection itself also
has a freshness limit.

The probe refuses wrong machines, existing libvirt domains/direct guests/Nova
processes, existing OpenStack containers/volumes/configuration and mismatched
prerequisites. A busy VM host is rejected **before** reading private helper
configuration. Unrelated Docker containers are not stopped: their IDs, images,
states and start times are protected before/after observations. It checks actual
Ubuntu/architecture, required interfaces, and the persistent VIP for a
single-controller configuration without keepalived.

On computes it additionally checks available capacity above CPU/RAM reserves,
KVM/TDX/SEV prerequisites, peer UID/socket permissions and active helper socket,
compute UUID/public pool binding, empty owned SPVM state, mounted pool budgets,
selected GPU vendor/product/VFIO/IOMMU facts, installed helper/launcher hashes,
explicit deny-all policy and exact cached image ID/context label. QEMU/libvirt
hashing is streamed: actual QEMU binaries exceed the small configuration-file
size limit. This probe does not perform confidential guest attestation or prove
QGS access as the QEMU runtime user; hardware bootstrap acceptance remains separate.

An approved run writes its transaction journal **before** invoking Kolla and
stores stdout/stderr only in `private-prechecks.log`. Input/environment overrides
are sanitized; only the scoped `prechecks` action is passed. On completion the
workspace and protected host observations are checked again. Success is
`INITIAL_KOLLA_PRECHECKS_PASSED_NOT_DEPLOYED`, not a deployed or attested cloud.
Errors, interruption, drift or lost acknowledgement require manual review;
the journal prevents blind replay. A timeout does not prove that all descendant
Ansible processes stopped. Inspect the log and processes before authorizing a
new attempt. Never remove a failure journal simply to bypass a refusal.

Acceptance on 2026-10-16: fixture tests cover prepared empty host checks, approval,
expiry, drift, failures and action scope. The real read-only probe on busy `.245`
returned `EXISTING_LIBVIRT_DOMAINS`, without invoking Kolla or reading private
helper config. Real installed QEMU is a 31,328,048-byte regular file; the streamed
runtime-hash path is covered by a large-file fixture. **A real passing initial
Kolla prechecks run on prepared empty machines is not yet accepted.** No current
Swarm node or Nova container was stopped, restarted or reconfigured by this work.

### 10.9. Prepare base packages without running the full host bootstrap

The installed `openstack.kolla.baremetal` role is not just a package installer:
it imports hosts/journal setup, Docker, user and SDK configuration, AppArmor
handling and systemd setup. Do **not** run a blanket `bootstrap-servers` on an
SPVM host to fix a missing utility.

`plan-base-packages` / `prepare-base-packages` deliberately runs **only** the
reviewed `openstack.kolla.packages` role, with exactly `git`, `iputils-ping` and
`tzdata`, and with both package-removal lists empty. It uses the same bound
workspace, source/collection checks, explicit SSH trust, machine identities and
prepared-empty-host guard as initial prechecks. Consequently this is a late,
scoped preparation step: Docker, helper, approved image and network interfaces
must already exist. It is not a first-boot provisioning command.

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-base-packages --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/base-packages-01

./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-base-packages --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/base-packages-01 \
  --plan-sha256 '<approved plan_sha256>'
```

Review `base-packages-plan.json` before apply. Approval expires after 900 seconds
and binds the exact playbook, reviewed role task/default hashes, installer source,
workspace, SSH trust and host observations. A busy or previously deployed host
is blocked, even if all three packages are already installed. If all selected
packages are present on all targets, apply takes the verified NOOP path and
does not invoke Ansible. Otherwise it processes hosts serially. Ubuntu 26 still
requires explicit acknowledgement and uses its real facts and repositories.

APT runs non-interactively with recommended packages disabled and existing
configuration retained. Normal required package dependencies may also be installed;
this is not an offline or fixed-version package transaction. Automatic installation
of the Ansible APT module's own dependencies is disabled: prepare `python3-apt`
separately if it is absent. Its temporary `policy_rc_d=101` inhibits maintainer-script
service starts/restarts; `NEEDRESTART_MODE=l` requests reporting rather than
automatic restarts. Ansible's installed package action was inspected to confirm
that it forwards the APT module defaults to the selected backend. These controls
are not a sandbox for arbitrary administrator-supplied APT hooks or repository
maintainer scripts. Review host package policy before approving changes.

Before/after checks protect Docker containers, QEMU/libvirt and Docker config
file hashes, plus load/state/PID/start timestamps and restart counters for Docker,
libvirt daemons, QGS/PCCS and the helper socket. Installed versions of the three
selected packages may change; other protected observations may not. A detected
restart or file change is a failed acceptance, not an automatic rollback.
Private logs and write-ahead journals are retained. Failure, interruption or
timeout requires manual review; do not replay or erase a transaction to bypass
it. A timeout does not guarantee every descendant Ansible process has exited.

Success is `BASE_PACKAGES_READY_NOT_BOOTSTRAPPED`: it does **not** mean all
Kolla host dependencies are ready. This step does not install Docker/Python SDKs,
create users or `/etc/kolla`, change AppArmor/firewalls/networking, install the
measured runtime, or deploy/start/restart a VM. Re-run initial prechecks with a
new plan afterwards. Full reviewed host bootstrap, API trust distribution and
fresh deployment acceptance remain separate work. The package mutation path is
fixture-tested, not accepted on a real empty server; existing live servers have
not been used for an installation experiment.

Validation on 2026-10-16: the installed Ansible on `.247` successfully performed
`--syntax-check` against this generated playbook and the real collections, without
executing tasks. Its private temporary syntax-check directory was automatically
removed. The live package-enabled guard on `.245` returned
`EXISTING_LIBVIRT_DOMAINS` before any package/helper operation. Installer tests
cover role scope, NOOP, approval expiry/drift, service/container protection,
incomplete installation and failure journals; the tools suite has 116 passing tests.

### 10.10. Prepare and verify the host Python Docker SDK

Kolla modules must be able to reach the local Docker daemon from the Python
interpreter used by Ansible. Having the Docker CLI, a working compute container,
or a Docker SDK inside a different virtualenv does not establish this.
The portable preparation commands and generated per-host Kolla inventory
explicitly select `/usr/bin/python3` on hosts. Prechecks and these preparation
plans reject an inventory without that declaration or a variable override to
another interpreter. Re-render/re-stage older workspaces; do not edit checksums
or silently rely on Ansible interpreter discovery.
`plan-host-sdk` / `prepare-host-sdk` prepare its distribution packages:
`python3-docker`, `python3-dbus`, and `python3-requests`.

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-host-sdk --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/host-sdk-01

./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-host-sdk --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/host-sdk-01 \
  --plan-sha256 '<approved plan_sha256>'
```

This uses only the reviewed Kolla **packages** role with an explicit SDK package
list, not the entire Docker SDK bootstrap role. Inspection of the installed
`openstack.kolla.docker_sdk` tasks confirmed its externally-managed-environment
distribution-package path; other paths can invoke pip/create virtualenvs. This
installer does neither. It does not need to add pip or setuptools solely to
import the distribution SDK. APT recommendations/removals and automatic APT-module
dependency installation remain disabled as in section 10.9. Required transitive
dependencies may be installed from the host's configured repositories.

The isolated system-Python probe imports Docker SDK, D-Bus and requests, requires
Docker SDK major version 7 or later (matching the reviewed Kolla requirement),
and performs a bounded `ping()` over `unix:///var/run/docker.sock`. It does not
query guest APIs, run containers or contact an external Docker endpoint.
Python bytecode writes are disabled for this probe. The fresh-host guard runs
before the SDK imports. Missing packages can be prepared; if all selected packages
are already installed but imports/version/ping fail, the plan is blocked with
`INSTALLED_SDK_UNUSABLE_REVIEW_REQUIRED`. There is no automatic upgrade or fallback
to a random virtualenv/system pip. An older distribution SDK must be resolved
explicitly; package-name availability alone is not compatibility acceptance.

`host-sdk-plan.json` and `host-sdk-transaction.json` use the same 900-second
approval, identity binding, protected-runtime checks and failure/no-replay rules
as base packages. A working SDK with all packages present is a verified NOOP.
Success is `HOST_SDK_READY_NOT_BOOTSTRAPPED`, not a deployed cloud. The system
Python on the current `.247` lab was observed to lack `docker`; no package was
installed there, and this observation does not diagnose or change its existing
deployment, whose interpreter configuration was not altered. Read-only cached
APT policy on `.247` listed `python3-docker` candidate `7.1.0-2ubuntu1` from
`resolute/universe`; package lists were not refreshed and nothing was installed.
This is candidate availability, not a tested installation or repository guarantee.

### 10.11. Register the public OpenStack API CA on fresh hosts

The OpenStack private API CA is **not** the Swarm CA created inside bootstrap.
This step registers the already-generated API CA from the bound workspace;
it does not generate/rotate a CA, transfer a private key or enroll a Swarm node.

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-api-trust --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/api-trust-01

./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-api-trust --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/api-trust-01 \
  --plan-sha256 '<approved plan_sha256>'
```

Review the public certificate fingerprint **and the global trust change** in
`api-trust-plan.json` before approval. Installing a root CA expands the host's
system-wide trust; it is not just copying a file for this driver. Only approve
the intended cloud CA. Each target must already have `ca-certificates`, its
system bundle, protected `/usr/local/share/ca-certificates` directory and
`/usr/sbin/update-ca-certificates`. Missing prerequisites are blockers, not an
implicit package-install request.

The source is exactly `kolla/certificates/ca/ca.crt`. The whole workspace is
validated, including API certificate chain, VIP identity, validity and key match.
The additional CA selector permits one public PEM certificate only and checks
its CA basic constraint with OpenSSL. A concatenated private key, multiple
certificates or a leaf certificate is rejected. The approved plan binds both
PEM-file SHA256 and DER-certificate SHA256. The CA private key, API private key,
passwords, provider credentials and OpenRC files are never placed in the
generated trust playbook or sent to targets.

The destination is a deterministic deployment-scoped path:
`/usr/local/share/ca-certificates/spvm-openstack-<16-hex-deployment-digest>.crt`.
An existing file must match exactly and already have root:root/0644 permissions;
conflicting files, mismatched permissions and symlink/unprotected paths are
refused. The two-task playbook copies the public anchor as root:root
0644 with overwriting disabled, then runs `update-ca-certificates` when required.
It does not invoke Kolla bootstrap/deploy, install packages, reload container
services or restart Nova. Fully registered identical trust is a verified NOOP.

Afterwards the installed file must match and the system bundle must contain
exactly its previous set of DER certificate identities plus the approved CA.
Removing a prior CA or unexpectedly adding another CA fails acceptance. This
also catches unrelated pending local anchors picked up by `update-ca-certificates`.
Protected runtime/container/service observations must remain unchanged.
Distribution CA-update hooks are executable host policy and are not sandboxed:
review them before apply. A detected side effect is journalled for review;
there is no automatic trust rollback or service restart to hide it.

Success is `API_TRUST_READY_NOT_BOOTSTRAPPED`. It proves the selected CA was
registered, not that an API endpoint already exists or completed a TLS handshake.
No live OpenStack endpoint is required during initial preparation. Future Kolla
service configuration uses the same workspace's `kolla_copy_ca_into_containers:
true` and CA directory; the inspected upstream `service-cert-copy` role handles
its copy into service configuration. This step does not retrofit trust into
running containers. Verify container trust and real API TLS during deployment
acceptance. Do not replace `verify=True` with insecure TLS to bypass a failure.

Both workflows are covered by transaction/probe fixtures, including SDK readiness,
CA conflicts, unrelated trust changes, failure journals and NOOP behavior. Real
OpenSSL fixtures check root-vs-leaf selection. Real empty-host SDK/CA apply and
fresh-cloud container trust remain unaccepted; the active lab is preserved.

All three generated playbooks passed the actual installed Ansible
`--syntax-check` on `.247` without executing tasks. Both new profiles' read-only
guards returned `EXISTING_LIBVIRT_DOMAINS` on `.245` before SDK imports or trust
inspection. Neither host's packages, CA store, services or guests were changed
by those checks. Syntax validation does not establish successful package/CA apply.
The current installer suite passes 129 tests; the backend suite passes 566 tests,
including the explicit system-interpreter inventory assertion.

### 10.12. Inspect combined host readiness and the next prerequisite

Use `initial-host-status` to replace several separate diagnostic invocations with
one read-only observation per configured host. It reports base packages, the
system-Python Docker SDK, registered API CA and local interface/forwarding state,
then names the next preparation plan or manual review. It does **not** execute
that recommendation, reuse an old approval or automatically advance through
mutating steps.

```bash
./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  initial-host-status --config /srv/deployment.yaml \
  --workspace /srv/bound-kolla-workspace \
  --known-hosts /root/.ssh/openstack_known_hosts \
  --allow-experimental-ubuntu26 --output /srv/initial-host-status-01
```

Run as the private workspace owner. All previous input/identity checks remain in
force: clean pinned Kolla source, reviewed collections, a compute-bound workspace,
matching installed helpers/images, explicit SSH identities/trust, system-Python
inventory and prepared network prerequisites. This command is a **late initial
host check**, not an OS/bootstrap planner. It cannot infer helper installation
success from a source archive or propose a safe network topology from missing
administrator input. A missing prerequisite may block observation rather than
produce a complete stage list. It is intentionally unsuitable for an existing
cloud with running or defined guests.

The combined probe checks the union of selected package names, imports/pings the
SDK through isolated `/usr/bin/python3`, compares the deployment-scoped public CA
and system trust bundle, and preserves the busy-host guard before those reads.
Local network checks require the configured management/tunnel interfaces to be
UP with usable IPv4 addresses for this initial IPv4 profile. Their MTUs are
recorded, not adjusted. Host-NAT network-role nodes additionally require
`net.ipv4.ip_forward=1`; other roles/provider mode do not inherit that assumption.
The existing required-interface and persistent API VIP checks also remain.
No ping/UDP traffic, route/firewall changes or link creation are performed.

Local success is **not end-to-end connectivity acceptance**. Geneve UDP peer
reachability, forwarding/firewall policy, path MTU, public/private routing,
Neutron/OVN integration, API TLS handshakes and persistence across a host reboot
remain unaccepted here. In particular, Docker's FORWARD policy cannot be declared
safe merely because IP forwarding is enabled. Never reboot live SPVMs to test
persistence: their ephemeral encryption key is not recoverable.

`initial-host-status.json` is a new private report with source/workspace intent
digests, observation time, per-host public/hash-only facts, stage states,
blockers and a structured `next_step`. No private keys, passwords, provider
configuration or raw Docker environment are emitted. Inputs are checked again
after observations; drift or a collection taking longer than 900 seconds fails.
Existing output directories are not overwritten. This report has no apply
authorization and cannot be supplied as a plan SHA to a mutating command.

Next-step order is conservative:

1. Busy/wrong/incomplete hosts or CA conflicts: operator review, no automatic fix.
2. Known-broken local networking: manual network preparation, no guessed commands.
3. Already-installed but unusable SDK: explicit compatibility/interpreter review.
4. Missing base packages, then SDK packages, then API trust: the corresponding
   `plan-*` command, each requiring its own fresh approved plan before apply.
5. All observed local prerequisites present: `plan-kolla-prechecks`, not deploy.

Exit 0 means `INITIAL_HOSTS_READY_FOR_PRECHECKS_NOT_DEPLOYABLE`. Missing preparation
or blocked observation returns exit 2; inspect `status`, `blockers` and
`next_step`. The stage list explicitly leaves Kolla prechecks requiring a fresh
approved run, deployment not implemented/authorized, and cloud enrollment/Swarm UI
acceptance unchecked. The command does not claim those stages failed or succeeded
based on a previous unrelated journal.

The aggregated command and local network checks are fixture-tested. This does
not add a real clean-host apply or fresh-cloud acceptance. The installer still
does not run a blanket bootstrap or deploy.

Validation on 2026-10-16: 140 installer tests passed, including next-step ordering,
input drift, private/hash-only reporting, busy/incomplete hosts and local network
failures. The updated combined probe on real `.245` returned
`EXISTING_LIBVIRT_DOMAINS` before private helper, SDK, trust or network observation.
No host mutation was attempted. A successful full command run on a prepared empty
host with real bound inputs remains unaccepted; the live negative probe is not
represented as that acceptance.

### 10.13. Prepare a scoped registered-host Geneve firewall

The lab's hard-coded `SPVM_GENEVE` script is not a portable installer. The new
`plan-geneve-guard` / `prepare-geneve-guard` commands are a separate, limited
initial-host workflow. They create an IPv4 INPUT allowlist for UDP/6081 from the
explicitly registered OpenStack network/compute hosts, and install its persistent
systemd unit. This is host-underlay policy, not a per-Swarm-cluster network.

Run each command **as root locally on the selected fresh host**, using the same
reviewed tools/backend sources and deployment/request inputs. These commands do
not SSH to peers or copy private deployment identities. Hardware bootstrap and
usable Docker must already be prepared. They do not require an installed SPVM
helper/image or a compute-bound workspace, so this firewall step can precede those
late initial-host checks. The shared guard still refuses wrong machines, defined
libvirt guests/direct guests/Nova, existing OpenStack containers/configuration and
unreviewed OS state. Compute targets require prepared host QEMU/libvirt binaries.

Provide an explicit public network request, based on
[`openstack-network-request.example.json`](openstack-network-request.example.json).
Replace every example address/fingerprint; the shipped placeholder identities
are deliberately invalid. Its `hosts` map must contain exactly every deployment
host with `network` or `compute` role. Each needs a unique audited machine SHA256
and unique `tunnel_ip`. The fingerprint is SHA256 of the **stripped** machine-id
text, not the raw file including newline. Obtain it through independently trusted
administrative access. Do not copy a private SSH key into the request.

The tunnel interface name comes from that host's deployment configuration; the
IPv4 address comes from this explicit request, never the SSH address. The existing
interface must be UP and have **exactly one IPv4**, matching the request. No link
is created, enslaved, renamed or readdressed. Multiple IPv4s require manual design
review rather than guessing which address Kolla/OVN will select. The initial
profile supports at most 256 participating hosts and IPv4 peers only.

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-geneve-guard --config /srv/deployment.yaml \
  --network-request /srv/network-request.json --host super7 \
  --allow-experimental-ubuntu26 --output /var/tmp/geneve-super7-01

sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-geneve-guard --config /srv/deployment.yaml \
  --network-request /srv/network-request.json --host super7 \
  --allow-experimental-ubuntu26 --output /var/tmp/geneve-super7-01 \
  --plan-sha256 '<approved plan_sha256>'
```

Use the corresponding `--host` and a separate local transaction on every
participating host. Review `geneve-plan.json` before apply. The 900-second plan
binds config/request, local machine, all peer IPv4s, selected interface, installer
and backend sources, exact installed artifact hashes/modes and protected host,
firewall and routing observations. Changed state invalidates approval.

The owned chain is `SPVM_GNV_<16-uppercase-hex-deployment-digest>`:

- One exact source `/32` ACCEPT per registered peer, followed by DROP.
- One jump at the head of INPUT, restricted to the selected incoming interface,
  selected destination `/32`, IPv4 UDP and port 6081.
- A single-host request has no remote peers: the owned chain denies all matching
  incoming packets. It does not open the port to the internet.

No default policy, FORWARD rule, NAT table, route, routing policy or NIC is
changed. Existing unrelated filter rules retain their order; NAT and routing
digests must remain unchanged. No flush/delete operation is used. Active or
unknown `ufw`, `firewalld` or `nftables` service management is refused rather than
disabled or silently bypassed. Unsupported/failed iptables reads are not treated
as an absent chain. A pre-existing conflicting, partial, duplicated or incorrectly
ordered owned chain/jump requires review.

Installed artifacts are limited to:

- `/usr/local/libexec/spvm-geneve-guard.py` — protected root-owned runtime source.
- `/etc/spvm-network/spvm-geneve-<deployment-id>.json` — root0600 public policy.
- `/etc/systemd/system/spvm-geneve-<deployment-id>.service` and its own enablement link.

Existing artifacts/enablement links are refused; this is not an adoption/update
command. The new oneshot unit orders after network-online and before Docker at
boot, with `RemainAfterExit=yes`, `NoNewPrivileges`, read-only `/etc`/`usr` policy
and only `CAP_NET_ADMIN` in its capability bounding set. Initial apply performs
`daemon-reload` and enables/starts **only this unit**; it does not restart Docker,
libvirt, Nova or guests. A future unit start uses only its root-owned policy and
can restore an entirely absent allowlist. An exact existing allowlist is a runtime
NOOP. A partial/conflicting allowlist is rejected, not automatically rebuilt.
There is no ExecStop that deletes security rules.

The write-ahead transaction precedes installation. After any error, interruption,
timeout or acknowledgement loss, inspect artifacts, unit job and firewall state;
there is no automatic rollback or blind replay. A timed-out systemctl client does
not prove its systemd job stopped. Newly created owned files/partial rules may
remain. Do not delete a journal or flush tables to bypass a refusal.

Success is `GENEVE_GUARD_INSTALLED_NOT_CONNECTIVITY_ACCEPTED`. Only the selected
IPv4/interface/destination is covered: other host addresses, IPv6, packet spoofing,
hardware offload and routed/forwarded traffic are not accepted by this check.
An IP allowlist is not peer cryptographic authentication or Geneve encryption.
It does not configure OVN tunnel endpoints, management WireGuard, API connectivity,
host-NAT transit, guest probe routes, public ingress, path MTU or tenant networking.
It does not replace Swarm's encrypted overlay.

Apply must be accepted on a real empty host before production use. Persistence is
implemented but a reboot/boot-order test remains unaccepted; never use a live SPVM
host reboot as a harmless check. External firewall reloads can remove a oneshot
unit's rules after it exits: re-observe/integrate the firewall manager explicitly.
Adding/removing computes changes peer policy and requires a separate reviewed
maintenance workflow on existing hosts; these fresh-only commands do not update
an active cloud. The combined `initial-host-status` does not yet consume this
unit's acceptance as proof of firewall or Geneve connectivity.

Validation: kernel fixtures cover initial install, exact runtime NOOP, conflicts,
partial state, approvals/expiry/drift, failure/no-replay, protected NAT/FORWARD and
full fixture artifact/enablement checks. The generated unit passed local real
`systemd-analyze verify` without starting it. The new snapshot parser read `.247`'s
real filter/NAT tables through a runner explicitly restricted to read commands;
only hashes/state were returned. No live firewall rule or service was changed.
The full installer suite passed 156 tests. The guard-only fresh-host probe on
`.245` still returned `EXISTING_LIBVIRT_DOMAINS` before any firewall inspection.
Rule matching handles iptables' canonical address/interface output order without
allowing additional match conditions or duplicate options. Empty successful
filter output is not accepted as proof of an absent chain.

### 10.14. Prepare existing private management transport and a static API VIP

On Ubuntu, `ufw.service` may be `active (exited)` even with UFW disabled. The
installer accepts that boot-hook state only when `ufw status` reports exactly
`Status: inactive` and complete IPv4/IPv6 filter snapshots contain no UFW rules
or jumps. It never disables/stops UFW, flushes firewall rules or accepts an active
policy merely to pass preparation. Active firewalld/nftables/persistent managers,
unknown status, partial observations and residual UFW rules remain blockers.
This read-only distinction is shared by management/NAT and Geneve preparation.

Installer implementation modules now live together in `scripts/openstack/`.
`scripts/install-openstack.sh` remains the public entry point. Do not copy only
the shell wrapper when staging tools, and do not restore old Python-module copies
at the `scripts/` root. Hardware bootstrap and measured launchers were not moved.

The new `plan-management` / `prepare-management` commands use an **existing,
addressed, connected private management network**. A private LAN is sufficient;
WireGuard is not mandatory. An already configured wg-quick transport is supported
through an explicit dependency. This phase does not install WireGuard, generate
or transfer keys, rewrite Netplan/NetworkManager/networkd, assign primary host IPs
or create a guessed management network.

Use [`openstack-host-network.example.json`](openstack-host-network.example.json),
which is a **different schema** from the Geneve peer request. Replace all sample
addresses/invalid identity placeholders. Its `hosts` map must cover every configured
host, with unique audited machine SHA256 and management IPv4 interface addresses.
`management_interface` is taken from deployment YAML, not guessed from SSH.
`route_interface` explicitly identifies the existing management path: it can equal
the LAN interface or identify an existing WireGuard device carrying separately
assigned management identities.

For the illustrative LAN example, configure `network.api_vip: 10.90.0.2` in the
deployment YAML, reserve it for the single controller, and ensure the declared
management addresses/interfaces already exist. The JSON does not pick/reserve an
available IP for you. Duplicate-address detection against other physical machines
is not performed: allocate/reserve all host/VIP/transit/router addresses through
your network administration before approving the plan.

The first portable profile supports one static-VIP controller, one host-NAT
network node and RFC1918 IPv4 management/guest/transit networks. Three-controller
HA/provider networking require another profile and are refused here. Management
and transport devices must be UP and must not carry an IPv4 or IPv6 default route.
The requested primary management address/prefix must already be assigned.
Management, guest and transit networks must not overlap. Guest CIDRs are restricted
to private /16../30. Neither public NIC addressing nor a default route is changed.

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-management --config /srv/deployment.yaml \
  --network-request /srv/host-network.json --host super7 \
  --allow-experimental-ubuntu26 --output /var/tmp/management-super7-01

sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-management --config /srv/deployment.yaml \
  --network-request /srv/host-network.json --host super7 \
  --allow-experimental-ubuntu26 --output /var/tmp/management-super7-01 \
  --plan-sha256 '<approved plan_sha256>'
```

Run root-locally on each selected host with separate private transaction directories.
As with Geneve preparation, hardware bootstrap/usable Docker are separate prior
steps. No SPVM helper/image/bound workspace is needed yet. Fresh-host guards refuse
existing clouds and any defined/running guests before touching networking.
Active firewall managers are not disabled; they require explicit integration.

Review `network-plan.json`. The controller receives only the reserved API VIP
as a secondary `/32` on its existing private management interface. A conflicting
local VIP/interface/prefix is rejected. An already identical VIP is retained.
Remote compute nodes receive only the declared guest-CIDR route through the network
node's management IP, with an explicit management source IP. The source is important:
the network node's host-to-guest probe SNAT rules bind that exact source. Existing
guest routes must match gateway/device/source; routes are added, never replaced.
The network node itself gets its guest route in the NAT phase instead.

The existing kernel routes to other management identities and the API VIP must
already use the declared private transport. This is route inspection, not a TCP,
ARP, WireGuard-handshake or end-to-end connectivity test. For an existing wg-quick
device set `transport_unit` to exactly `wg-quick@<route_interface>.service`.
The selected unit must already be active. Its AllowedIPs must already cover the
guest probe network/reply sources; public allowed-IP metadata is inspected without
reading private keys. The installer never invokes `wg set` or edits WireGuard
configuration. LAN mode uses `transport_unit: null` and needs no WireGuard tool.

The installed standalone runtime is `/usr/local/libexec/spvm-host-network.py`.
Policies live under `/etc/spvm-host-network/`, separate from the Geneve namespace.
Management installs its own root0600 policy and `spvm-management-<id>.service`.
Its oneshot unit runs after network-online, after an optional Geneve guard and
after/requiring the explicit existing wg-quick unit when selected, and before
Docker at boot. It does not restart a transport, Docker or any guest during apply.
It does not remove the VIP/route on stop. Kernel repeat with identical state is a
NOOP; initial installer replay is still blocked by the transaction journal.

### 10.15. Prepare isolated host-NAT transit and compute-to-guest API routes

`plan-nat` / `prepare-nat` run **only on the selected network node**, after its
matching management phase is installed/enabled/active. They use the same host
network request, not hard-coded lab addresses or a physical bridge attachment.

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  plan-nat --config /srv/deployment.yaml \
  --network-request /srv/host-network.json --host super7 \
  --allow-experimental-ubuntu26 --output /var/tmp/nat-super7-01

sudo ./scripts/install-openstack.sh --driver-dir /srv/reviewed-driver-bundle -- \
  prepare-nat --config /srv/deployment.yaml \
  --network-request /srv/host-network.json --host super7 \
  --allow-experimental-ubuntu26 --output /var/tmp/nat-super7-01 \
  --plan-sha256 '<approved plan_sha256>'
```

The selected egress must be an existing UP default-route interface, distinct from
management and the two owned veth names. Its addresses/routes are preserved.
The phase creates only the reciprocal `spvm-ext0` ↔ `spvm-nat0` veth pair:

```text
Neutron router external port / OVS br-ex (created later by OpenStack)
  → spvm-ext0 [unnumbered; Kolla attaches this virtual endpoint]
  ↔ spvm-nat0 [explicit transit gateway/prefix]
  → existing egress [scoped MASQUERADE; no physical NIC enslaving]
```

The new interface names match the rendered Kolla host-NAT profile. No Linux bridge
or OVS bridge is created/reconfigured by this phase. A pre-existing veth/owned
firewall namespace is not adopted by fresh install. Runtime starts validate a
fully existing pair's kind, reciprocal peer name/index, address and UP state;
partial/conflicting links or numbered OVS-side IPv4 are refused. Modern iproute2
may expose reciprocal peer names rather than `link_index`; both are checked.
Kernel-generated IPv6 link-local/multicast routes on these **owned** devices are
accounted for without ignoring IPv6 routes on unowned interfaces. This is still
an IPv4 NAT profile, not IPv6 security/connectivity acceptance.

The explicit transit subnet must match `network.transit_cidr`. Reserve both
`transit_address` and `router_ip`; they must be distinct usable addresses. The
guest route is added through that exact future Neutron router IP, with transit
gateway source. No packet/router readiness is inferred from successfully adding
the route. A later cloud-enrollment step must create the external Neutron subnet
with that gateway, reserve the host's address from allocation, and assign the
router exactly `router_ip`; it must create the matching shared guest subnet.
This step does **not** create those OpenStack API objects.

Only deployment-scoped chains and scoped FORWARD/POSTROUTING jumps are added.
The existing FORWARD default policy, including Docker's DROP, is preserved:

- Transit-subnet traffic can leave via the selected egress and is masqueraded;
  established/related replies are allowed back.
- Remote computes' exact management `/32` sources can reach the declared guest
  CIDR over only `probe_ports` TCP ports. Sources are derived from the same host
  request; the management phase pins their outgoing route source.
- Those remote probes are SNATed to the transit gateway; established replies are
  allowed back through the existing management transport.
- Other forwarded traffic through the owned transit endpoint is dropped by the
  owned filter chain. This is not a generic floating-IP/inbound-forwarding policy.
  Local ingress proxies use host OUTPUT and the guest route rather than that path.

`probe_ports` must match the ports supplied to the guest launcher/readiness profile;
they are not SSH ports and are not inferred from a particular debug build.
At most 15 explicit TCP ports and 255 remote probe sources are supported.
Unrelated filter/NAT rules remain opaque and in their original order, including
Docker negation/module syntax. No table flush/delete/default-policy change is used.
Empty/partial/conflicting owned rules are not accepted as a complete install.

NAT explicitly enables `net.ipv4.ip_forward=1`, installs its own sysctl drop-in,
policy, `spvm-transit-<id>.service` and `spvm-nat-<id>.service`. The runtime source is shared with management
only when its root-owned content/mode matches exactly; it is never overwritten
to perform an implicit upgrade. The transit unit requires/starts after matching
management and an optional Geneve guard, creates only veth/address/forwarding/route,
and orders **before Docker** so OVS can find its external endpoint. The NAT unit
requires transit/management and the already-active systemd `docker.service`, and
orders **after Docker**, installing its scoped firewall jumps after Docker's own
startup rules. It does not restart/start an absent Docker daemon during apply;
that precondition must already hold. The transit dependency is a static unit
started by the enabled NAT unit. This avoids both missing OVS endpoints and
Docker startup moving the new FORWARD rules behind its jumps.
External/Docker firewall rebuilds or network-manager reconfiguration still need explicit
integration; oneshot units are not continuous reconciliation services.

Both phases bind source/config/request and protected host/network observations
to a 900-second plan, write a journal before changes, and compare unowned
interfaces, addresses, routes, routing policy and firewall after apply. Existing
primary addressing/default routes are not repaired or replaced. Failures can
leave owned VIP/routes/veth/rules/files: inspect the journal/unit job and state;
there is no automatic deletion, rollback or blind retry. A timed-out systemctl
client does not prove its systemd job stopped. These are fresh-only workflows,
not live network migration or hot-add policy updates.

Success is `HOST_NETWORK_CONFIGURED_NOT_CONNECTIVITY_ACCEPTED`. Real traffic
through Neutron/OVN, guest API probes, public UI, path MTU and reboot/boot-order
acceptance remain separate. Host-root plan/apply plus real systemd activation on
a prepared empty server is not yet accepted; do not treat namespace tests as that.

The reproducible opt-in kernel test is:

```bash
sudo python3 tests/accept_openstack_network_namespace.py
```

It creates a disposable new network namespace, asserts it is not the parent/PID1
namespace, creates only dummy/veth devices there, and exercises management and
NAT plus exact repeats against real iproute2/iptables/sysctl. It writes no service
files and checks the host namespace/default routes/filter/NAT stayed unchanged.
The namespace disappears on exit. Do not remove its isolation checks or substitute
a host namespace. The local development sandbox denied namespaces; this test
passed on `.247` through in-memory SSH staging, with no host service/NIC changes.
Static unit checks and fixtures cover all three units, file install, phase ordering, approvals,
failure journals, source binding and optional existing WireGuard behavior.
Final regression for this change: 174 installer tests and 566 backend tests passed.
The final namespace run also exercised the pre-Docker links-only transit step,
post-Docker NAT primitives and exact repeats; host namespace/default routes/filter/
NAT hashes remained unchanged. This does not substitute for a real systemd boot,
host-root installation or cross-host/Neutron traffic acceptance.

### 10.16. Remaining integration boundary

The intended sequence is scoped preparation/dependencies, password generation,
image preparation/delivery, `prechecks`, deploy, post-deploy and SPVM acceptance.
The portable workflow does **not** run the full `bootstrap-servers` playbook:
scoped Docker/package/SDK preparation replaces it to preserve the measured host
runtime. These must use one reviewed inventory, matching collection and pinned
profile. `kolla-genpwd` belongs to **initial setup**, not adding a compute.

Across preparation and post-deployment integration, the following must be provided:

1. Persistent management/VIP/transit links and approved firewall/routing topology.
2. Reviewed Docker-preservation patch and per-host policy, with real OS facts.
3. Private Kolla passwords, API TLS CA/certificates and trust distribution.
4. Verified custom compute image containing the Nova driver and compatible dependencies.
5. External host libvirt; `enable_nova_libvirt_container: false`; correct socket
   binds and permissions. Never let Kolla replace the measured host runtime.
6. Root-owned SPVM helper, service units and private configuration; allowlisted
   Nova peer UID, sockets, compute UUID, GPU registration and explicit state budgets.
7. Matching per-host Nova overrides, scheduler PCI aliases/filter and Placement
   traits/state child providers. Compute identity must match Nova, not an old install.
8. Barbican, project RBAC/secret ownership, shared Neutron network, security groups
   and API-port parameters passed to the measured launcher.
9. Domain-scoped ingress publication and DNS credentials stored outside public configs.

This guide deliberately does not present a blanket `kolla-ansible deploy` as a
complete safe SPVM install: portable automation for these steps remains unfinished.
Do not remove capability gates to make the command succeed. Installation should
finish with acceptance, not automatically create a Swarm cluster.

### 10.17. Run the explicit fresh-cloud Kolla transaction

The new commands are **not** an unattended OS-to-cloud installer and do not
enable the backend's generic `deploy` command. Complete the preparation steps
above first: hardware bootstrap, Docker, host dependencies/SDK/CA, management,
Geneve protection, NAT where required, helper/launcher and compute image delivery.
Use the compute-bound workspace from section 10.7. Never select the running lab
or an existing cloud. The first execution refuses existing domains, direct guest
processes, Nova processes, Kolla containers/volumes and old cloud configuration.

The pinned upstream Kolla rejects its public `quay.io/openstack.kolla` images
unless its testing-image acknowledgement is explicit. For an experimental lab
using that namespace, add `--use-test-images` to **both** plan and run below
(and to the standalone precheck pair when used). The flag is passed to every
native prechecks action and bound into the plan fingerprint; changing it requires a new
plan. It defaults to false and is not implied by `--allow-experimental-ubuntu26`.
This is not production image certification: use operator-built/reviewed images
for production. Do not disable upstream assertions or fake distro facts to get
past the testing-image guard. A failed precheck transaction remains evidence;
after review, use a new output directory, not a blind retry of its journal.

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-kolla-install --config /srv/operator/deployment.yaml \
  --workspace /srv/operator/compute-bound --known-hosts /srv/operator/known_hosts \
  --output /srv/operator/install-01 --allow-experimental-ubuntu26
```

Inspect the private `install-plan.json` and approve its exact `plan_sha256`:

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  run-kolla-install --config /srv/operator/deployment.yaml \
  --workspace /srv/operator/compute-bound --known-hosts /srv/operator/known_hosts \
  --output /srv/operator/install-01 --allow-experimental-ubuntu26 \
  --ack-new-cloud --plan-sha256 REPLACE_WITH_APPROVED_PLAN_SHA256
```

Omit the experimental flag on Ubuntu 24.04. Initial approval expires after
900 seconds. Source, workspace, SSH trust/identities and host observations must
still match. The fixed phase order is `prechecks`, `deploy`, `post-deploy`;
there is no arbitrary Kolla action, automatic bootstrap, upgrade, reconfigure,
destroy or VM creation. A failed precheck prevents deploy. Existing Docker is
not installed/restarted by this command; missing Docker must be prepared first.

The transaction has a durable write-ahead journal, flock and private per-phase
logs. Kolla receives a separate private `kolla-run/` copy of its configuration;
generated openrc/cloud credentials must not modify the approved source workspace.
Input copies must retain their hashes. Child processes use a private umask.
Logs and generated credentials are sensitive; do not paste them into tickets.

On socket-activated empty hosts, libvirtd may exit after its normal idle timeout
(120 seconds on the lab hosts). The workflow holds an observational `virsh event`
client over verified SSH for each compute during the protected run, so a healthy
idle exit does not change its PID between guard snapshots. No daemon settings,
guest lifecycle or hypervisor files are modified; the connection may normally
socket-activate libvirtd, just like existing probes. Controller-side EOF releases
the remote client, including failure/disconnection paths. Service drift checks
remain enabled. Outside a running transaction, an idle service may still exit;
resuming an old boundary after such drift still requires explicit review.

The guarded machine IDs, measured binaries/configs/helper source, existing
unrelated container identities/start times and Docker/libvirt/QGS/PCCS/helper
service state are compared after every successful phase. These checks detect
unexpected changes; **they do not roll back a failed deployment**. Every phase
requires empty libvirt domains. Never use this workflow for a live Swarm.

If execution stops exactly at a journaled `PHASE_BOUNDARY`, the same command with
`--resume` skips completed phases after revalidating the inputs/protected hosts.
`RUNNING` or `NEEDS_REVIEW` is not replayable automatically: inspect the private
log and actual remote state. Do not delete the journal to bypass the refusal.
A successful final result is `KOLLA_DEPLOYED_NOT_SPVM_ENROLLED`, **not** an
accepted working SPVM cloud. Keystone/Nova/Placement/Neutron/Barbican and ingress
acceptance remain necessary. The workflow completed on cleared prepared Ubuntu26
physical hosts, with existing hardware bootstrap, management routing and artifact
caches retained. That is not a fresh-OS/cold-cache test. Missing-Docker installation
was separately exercised in a disposable Ubuntu26 VM. `post-deploy` creates
client credentials, not Swarm instances.

### 10.18. Stage portable SPVM project/network enrollment

Copy and edit `docs/openstack-enrollment.example.json` and
`docs/openstack-resource-profiles.example.json`. Replace the placeholders with
real Keystone project/user IDs and an existing shared Neutron network UUID.
These are IDs, **not** passwords, PowerDNS keys, Barbican payloads or CA private
keys. The request does not accept secret fields. Several Swarm clusters may use
the same enrolled project/network; never generate one Neutron network per Swarm.

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  stage-spvm-enrollment --workspace /srv/operator/compute-bound \
  --request /srv/operator/enrollment.json \
  --profiles /srv/operator/resource-profiles.json \
  --output /srv/operator/enrollment-01
```

The output includes `runtime-services.json` and `enrollment-plan.json`, bound to
the compute workspace. It uses the bound HTTPS auth URL/region and guest API
readiness, not SSH. Publisher/reader identities are explicit. Release allowlists
must match the measured cache already prepared on **every** selected compute;
debug builds additionally need the helper's explicit debug permission. Additional
release cache enrollment is not implemented by this staging command.

Resource profiles use the Nova CLI's exact resource validator/flavor encoding:
vCPU, RAM, state GiB and GPU count/profile per node. Generated private flavor
intent requires `CUSTOM_SPVM`, state capacity `CUSTOM_SPVM_STATE_GB` and the
native Nova PCI alias when GPU count is positive. Nova root disk remains zero:
the state request is separately accounted, not thin-provisioned root disk quota.
Choose the actual registered GPU alias, not the illustrative H100 profile in
the example. Host CPU/RAM/state budgets and registered GPU BDFs remain those
already approved in the compute binding; this command cannot donate more hardware.

**Staging performs no API writes and does not activate the runtime.** Before
activation, the remaining integration must verify Keystone membership and
Neutron ownership/security groups; exercise Barbican access; install runtime on
empty computes; create project-private flavors/Glance markers; validate Placement
and PCI capacity; and configure domain-based ingress. Do not copy the document
over the live runtime or run fixed-UUID lab helpers as a substitute. Barbican
stores scoped orchestration secrets/CA public chain; the CA private key remains
inside the bootstrap guest. No certificate signing or verification policy is
weakened by generating this document.

### 10.19. Validate/reconcile project API resources without reinstalling hosts

This workflow works with an existing cloud. It does **not** run Kolla, stop
instances, restart services, replace enrollment, modify quotas or create networks.
The staged plan from section 10.18 is the input. Run using an operator Python
environment containing OpenStackSDK and the driver's Python dependencies.
Select that environment explicitly with
`export SPVM_INSTALLER_PYTHON=/srv/operator/venv/bin/python`. Install reviewed,
versioned dependencies/the verified driver wheel into this operator venv, never
with system-wide `sudo pip` and never by changing the Nova container in place.
Use an explicit trusted `clouds.yaml` profile (never put passwords on the CLI)
and the public API CA. The profile must already be scoped to `--project-id` and
an enrolled publisher, with permission to inspect effective Keystone role
assignments and manage private Nova flavors/Glance images. Missing permissions
fail closed; there is no fallback to another administrator's credentials.

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-spvm-resources --enrollment-plan /srv/operator/enrollment-01/enrollment-plan.json \
  --project-id REPLACE_WITH_PROJECT_ID --cloud spvm-operator \
  --ca-file /srv/operator/api-ca.crt --output /srv/operator/resources-01

# Inspect resources-plan.json and approve its exact plan_sha256 (900s validity).
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  apply-spvm-resources --enrollment-plan /srv/operator/enrollment-01/enrollment-plan.json \
  --project-id REPLACE_WITH_PROJECT_ID --cloud spvm-operator \
  --ca-file /srv/operator/api-ca.crt --output /srv/operator/resources-01 \
  --plan-sha256 REPLACE_WITH_APPROVED_PLAN_SHA256
```

All service endpoints must be HTTPS. The selected Identity origin/path prefix
must match the enrolled auth URL (catalog origins with/without `/v3` are equivalent).
Effective publisher/reader project assignments are checked; the selected shared
Neutron networks must be active, project-owned, port-secured and have IPv4 subnets.
Only existing networks are used: security group policy remains per-cluster and
is enforced by the cluster preparation path, not widened by resource reconciliation.

Before the first write, all existing flavors and images are validated. Conflicting
sizes/specs, public flavors, unreviewed flavor tenant access, duplicate image names,
foreign images or incorrect markers cause refusal. New flavors are private,
zero-root-disk measured profiles with state Placement and optional PCI requests;
access is granted only to the selected enrolled project. New images are tiny
project-private **launch markers**, not downloaded guest disks. Measured release
downloads/measurement presence remain enforced by the guest launcher/runtime.
This operation does not authorize unprepared releases or weaken release checks.

Source/CA/enrollment hashes and observed API state are bound to the approved
plan. A private write-ahead journal prevents blind retries; a partial failure
requires inspection, not automatic deletion or recreation of resources. Local
flock serializes a transaction only: operators must serialize enrollment across
controllers, since Nova/Neutron names are not a distributed compare-and-swap.
Create a new plan after a successful run; its actions should all be `REUSE`.

Explicitly test Barbican with a disposable **non-secret** marker:

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  check-spvm-barbican --enrollment-plan /srv/operator/enrollment-01/enrollment-plan.json \
  --project-id REPLACE_WITH_PROJECT_ID --cloud spvm-operator \
  --ca-file /srv/operator/api-ca.crt --output /srv/operator/barbican-01
```

The probe creates only its own marker, sets `project-access: false` and an explicit
reader ACL before uploading, checks ACL/payload, then deletes that marker in
`finally`. Existing secret payloads are never read. Cleanup failure is a failed
test, with the marker UUID/name preserved for scoped manual review. API outages
can leave partial metadata; no broad secret cleanup is performed.

Real acceptance on the existing `.247` Kolla controller passed: Keystone
membership/network ownership, two real resource profiles, release447-debug and
449-release markers, a no-op apply, and private-ACL Barbican create/read/delete.
No VM/service restart or resource replacement occurred. The creation/conflict/
failure paths have fixture tests; this is not fresh-OS installation acceptance.
The opt-in `tests/accept_openstack_resources.py` repeats the test by injecting
code over trusted SSH in memory; credentials/tokens stay on the controller:

```bash
python3 tests/accept_openstack_resources.py \
  --ssh-target operator@CONTROLLER --identity /secure/operator_key \
  --known-hosts /secure/known_hosts --driver-dir /srv/swarm-cloud \
  --profiles /srv/operator/resource-profiles.json --apply-existing --barbican-probe
```

Without the last two flags this is read-only. `--apply-existing` first requires
every action to be `REUSE`; it refuses to create missing resources. The acceptance
helper uses the controller's existing Kolla admin scope and tests only that
enrolled project; it is not a generic multi-project credential bootstrap.

### 10.20. Create or reuse the shared Neutron topology

Run this API phase after Kolla is deployed and before staging project enrollment
(section 10.18 needs the resulting guest-network UUID). Use the operator venv and
explicit trusted cloud/CA described in section 10.19. An administrator profile
with cloud-wide network/subnet visibility and external-network permissions is
required; ordinary tenant visibility cannot prove absence of overlapping subnets
in other projects. Authentication must be scoped to the request's project/region.

Copy `docs/openstack-neutron-network.example.json` and explicitly fill the cloud,
project, names, CIDRs, gateways, pools, DNS, physnet and fixed router external IP.
This is not a secret file; unknown fields, including credentials, are refused.
The profile currently supports two disjoint canonical RFC1918 IPv4 /16../30
subnets, one flat external physnet, one shared guest subnet and one non-DVR
router. Set optional `router.is_ha` explicitly to `true` for an OVN deployment
that reports HA routers. If omitted it defaults to `false`; existing HA policy
must match the request, and creation explicitly requests that policy. This is
the Neutron router property, not a claim of control-plane or multi-host failover.
IPv6/provider-network alternatives require separate integration.

There is **one guest/data network**, reused by all Swarm clusters and computes.
The additional external/transit Neutron object is technical host-NAT plumbing,
not another per-Swarm network or overlay. Swarm still creates its own WireGuard
overlay. Here “shared” means reused by clusters of the enrolled project, not
Neutron `is_shared=true` granting every project access. Per-cluster security
groups remain managed by cluster preparation, not widened by these commands.

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-spvm-network --request /srv/operator/neutron-network.json \
  --cloud spvm-operator --ca-file /srv/operator/api-ca.crt \
  --output /srv/operator/network-01

# Review network-plan.json and approve its exact plan_sha256 within 900 seconds.
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  apply-spvm-network --request /srv/operator/neutron-network.json \
  --cloud spvm-operator --ca-file /srv/operator/api-ca.crt \
  --output /srv/operator/network-01 --plan-sha256 REPLACE_WITH_APPROVED_SHA256
```

Before any write the backend checks project/region/HTTPS Identity and Neutron
endpoints, duplicate names, network scope/state/port security, exact subnet pools/
DNS/DHCP/gateways, visible CIDR overlaps, unexpected extra subnets, router static
routes/HA/DVR, gateway/SNAT/fixed IP, attached interfaces and external-address
occupancy. Missing resources are added; existing conflicting resources are
**not** rewritten, renamed, deleted or adopted silently. A known object that
disappears is not automatically recreated. New router creation pins
`external_fixed_ips[].ip_address`; Neutron must not allocate an arbitrary transit IP.

The request/source/CA and observed API state are bound to the approved plan.
The private write-ahead journal records mutation intent before apply. Failure
leaves `NEEDS_REVIEW` and potentially partial resources: inspect them and create
a new reviewed plan, never delete the journal to force replay. There is no
automatic rollback or resource deletion. Operators must serialize changes across
controllers; local flock is not distributed locking. Newly created objects may
need time to become ACTIVE; non-ready objects fail closed for operator review.

For host-NAT, the request must match section 10.14/10.15 host configuration:

| API request field | Host-network prerequisite |
| --- | --- |
| `external.cidr` | Transit prefix on the isolated host peer |
| `external.gateway_ip` | Host transit IP, not the API VIP or public NIC |
| `router.external_ip` | Exact `nat.router_ip` used by the guest-CIDR host route |
| `guest.cidr` | Exact `guest_cidr` routed/allowed from all computes |
| `external.physical_network` | Kolla bridge mapping for the isolated external veth |

**These API commands do not inspect or alter host routes, OVS mappings, NAT,
firewalls, VPNs or units.** They do not validate MTU or guest connectivity. A
successful result is `SPVM_SHARED_NETWORK_READY_NOT_HOST_ROUTING_ACCEPTED`.
Take `network_id` from that result for enrollment; do not substitute a subnet,
external-network or router UUID.

On the existing lab `.247`, the legacy launcher had asked Neutron to allocate
the router address automatically. The actual address is **172.30.79.46**, not
the illustrative new-install **172.30.79.10**. The strict inspector correctly
refused `.10`; acceptance used the observed `.46` and passed with every object
`REUSE`, without changing the router or host routing. Never “fix” a live router
to match an example. Keep the actual address on adoption, or explicitly plan a
separate coordinated networking migration.

The opt-in read-only acceptance helper can exercise the existing topology:

```bash
python3 tests/accept_openstack_network.py \
  --ssh-target operator@CONTROLLER --identity /secure/operator_key \
  --known-hosts /secure/known_hosts --driver-dir /srv/swarm-cloud \
  --request /srv/operator/neutron-network.json --apply-existing
```

Without `--apply-existing`, it only inspects. With the flag, it first requires all
network/subnet/router/interface actions to be REUSE and refuses creation or
attachment. Code and auth data are passed in memory; credentials remain on the
controller/toolbox. The helper uses the controller's existing Kolla admin scope,
not a generic project credential bootstrap. Successful real no-op acceptance
does not prove creation from an empty cloud; creation/conflict/approval/failure
paths are covered by fixtures. Working VM and API services are not restarted.

### 10.21. Install initial runtime enrollment on a stopped empty compute

**This is initial enrollment, not a live runtime upgrade.** It only replaces the
exact prepared deny-all document, without changing helper policy, source files,
release caches, GPU registration, pool budgets or Nova image. Every enrolled
project must use the one release already prepared for this fresh compute.
Multiple release-cache enrollment remains separate maintenance.

Why offline: the driver caches runtime services and guards their original file
digest. Docker's file bind mount also pins an inode while the container runs.
Replacing the host file under a running compute therefore does not safely update
the service. Never change a live runtime and assume a Nova API action reloads it.
This command does not relax that driver guard.

First complete API resource/RBAC/network checks and the Barbican probe from
section 10.19. Staging/installation do not repeat those authenticated cloud checks:
they validate the intended IDs/schema and prepared host policy, not Keystone or
Neutron ownership. Operator approval is not cryptographic guest attestation.

Generate host requests from the matching immutable compute-bound workspace and
staged project enrollment:

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  stage-runtime-enrollment --workspace /srv/operator/compute-bound \
  --enrollment-plan /srv/operator/enrollment-01/enrollment-plan.json \
  --output /srv/operator/runtime-requests-01
```

Each `<host>.json` binds machine fingerprint, compute UUID, prepared helper
request, original bootstrap runtime, proposed API-only runtime, immutable image
ID/context SHA and enrollment SHA. Output files are private 0600; they contain
IDs/paths/policy, not passwords or provider secrets. Transfer the selected request
and trusted installer/backend sources securely to that compute. The source
selection/checksum rules from the beginning of this guide still apply.

All root-local transaction directories and ancestors must be root-owned and not
writable by others. Do not use `/tmp`, a user's staging directory, `/etc`, `/run`,
state pool roots or SPVM runtime state directories as transaction storage.
For example prepare an independent persistent directory:

```bash
sudo install -d -m 0700 /var/lib/spvm-enrollment
```

An optional image-schema check can run even while the compute has guests:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  check-runtime-enrollment --request /root/operator/compute.json \
  --output /var/lib/spvm-enrollment/image-check-01
```

It verifies the machine/image/context and starts a **temporary validator**, not
Nova or a guest: image ID pinned, `--pull=never`, overridden Python entrypoint,
user nova, read-only filesystem, no network/IPC/host mounts/capabilities,
no-new-privileges, 1 CPU, 256 MiB and 64 PIDs. JSON is sent through stdin; the
actual image's `nova_spvm.runtime_config.normalize` validates it. The validator
is auto-removed; on failure only its unique generated name/image/ownership label
and absence of mounts permit targeted cleanup. Unknown containers are not deleted.
The check does not prove running-image/helper/Placement/API connectivity and
does not install or activate the candidate runtime.

For actual installation, the operator must first quarantine the compute through
the control plane: disable scheduling, verify no assigned/inflight Nova builds
and no guests, and stop **only that empty compute's Nova container** using the
reviewed activation workflow. The new command neither performs nor independently
proves control-plane quarantine; `--ack-compute-quarantined` is an explicit
operator acknowledgement, not an automated Nova check. Serialize other automation
and prevent the container from being started during installation. Never stop a
Swarm bootstrap guest as a way to satisfy this prerequisite.

Then, on that stopped compute as root:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-runtime-enrollment --request /root/operator/compute.json \
  --output /var/lib/spvm-enrollment/install-01

# Inspect runtime-plan.json, approve exact plan_sha256 within 900 seconds.
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  install-runtime-enrollment --request /root/operator/compute.json \
  --output /var/lib/spvm-enrollment/install-01 \
  --plan-sha256 REPLACE_WITH_APPROVED_SHA256 --ack-compute-quarantined
```

The host guard checks the machine first, then refuses libvirt domains/direct
QEMU/guest/Nova processes **before** private helper inspection. It requires the
exact stopped `nova_compute`, bound immutable image and a read-only host file bind
at `/etc/spvm/runtime-services.json`; the right compute UUID; empty SPVM state/
pool roots; prepared helper release/cache/GPU policy and public/private pool IDs/
budgets; original root-owned 0644 deny-all file; and protected measured binaries.
Symlinks, hardlinks and unprotected system path ancestors are refused.

A host-wide protected flock, source/request/fact fingerprints, fresh approval and
private write-ahead journal guard installation. The pinned-image validator runs
before any replacement. The old file is fsynced to
`bootstrap-runtime.backup.json` (0600) in the transaction directory. The new
root-owned 0644 file is written/fsynced and atomically renamed, then directory
fsynced. Protected files, service PID/start/restart observations and the stopped
container state must remain unchanged. Only the runtime document may differ.

Success is `RUNTIME_ENROLLED_COMPUTE_STOPPED_NOT_ACTIVATED`. It does **not** start
the stopped Nova container, enable scheduling, restart libvirt/helper or launch
guests. Use the reviewed compute activation workflow to start the empty compute,
validate loaded services/Placement/API access and only then enable scheduling.
This activation is still a separate integration step, not implied by file install.

Failure leaves `NEEDS_REVIEW`; if replacement already occurred, the target may
already contain the new runtime. Keep the compute stopped/quarantined, inspect
the backup/journal and actual file. No automatic rollback or blind replay is
performed, especially if another actor started the compute. A new request cannot
overwrite an already enrolled runtime through this initial-install path.

On `.247`, real acceptance proved busy-host refusal and schema compatibility in
the cached prepared image `sha256:6da8b1f285d8b7a1dc5ed1a6cbfe9152544e23e495e6489258f507a890e03155`
(context `64847d4c1acd2ae482eb0a18f16a245b092e2f72a314c13f3995bac134388c45`)
for a candidate narrowed to the already enrolled `build-449-release`.
That image was **not** activated. Working host configuration/container/domain
and service snapshots were unchanged. Backup/atomic-install/refusal/cleanup paths
have filesystem/command fixtures, not real empty-compute installation acceptance.
Opt-in repeat helper: `tests/accept_runtime_enrollment.py`; its required image ID,
context SHA and release are explicit and it never installs a candidate.

### 10.22. Prepare the initial host domain ingress without touching Kolla HAProxy

This phase prepares the **host** HAProxy on one explicit ingress IPv4 address,
separate from Kolla's private API HAProxy container. Public TLS is passed through
to guest gateways/PKI; the host does not receive a guest CA private key or issue
a guest TLS certificate. HTTP is restricted to GET/HEAD ACME HTTP-01 challenge
paths; other HTTP requests/unknown Hosts get 404. Unknown/no TLS SNI is rejected,
never forwarded to another Swarm cluster or an OpenStack API fallback.

This is initial provisioning only: registry and HTTP-port map start **empty**.
It neither adopts nor clears an existing published cluster. Different clusters
share the listener and are selected by approved domain/SNI, not by extra Neutron
networks. The reused registry renderer rejects overlapping domain ownership
across clusters; exact PKI routes take precedence over that cluster's UI wildcard.

Prerequisites, not performed by this command:

- The distro host HAProxy binary/unit already exists, inactive and disabled.
  Package provisioning must suppress automatic daemon start; this scaffold
  command does not install packages or disable/stop an existing proxy.
- A reviewed root-owned 0644 base `/etc/haproxy/haproxy.cfg` containing global/
  defaults only, with no frontend/backend/listen routes or managed fragments.
- The explicit non-wildcard bind address is already assigned to the host;
  ports 80/443 are free on that address and no IPv4/IPv6 wildcard listener owns
  them. Kolla may keep its independent private-IP listeners.
- A dedicated **non-root** trusted publisher account and prepared SPVM-capable
  OpenStackClient are present. Its auth script/public CA must be readable and
  CLI executable under that account. Prefer scoped credentials/least privilege;
  do not supply tenant-controlled publisher code or unrestricted sudo.
- Matching guest CIDR/routing/NAT/firewall are prepared separately. This phase
  does not change OVS, routes, forwarding rules, public firewall or DNS.

Edit `docs/openstack-ingress.example.json`: replace the sample bind IP and both
SHA placeholders with the ingress host's reviewed values. `machine_sha256` is
SHA256 of **stripped** `/etc/machine-id`, not `sha256sum /etc/machine-id` including
its newline. Publisher fields contain paths/identity only, never credentials.
The guest CIDR must be canonical RFC1918 /16../30.

```bash
./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  stage-ingress --request /srv/operator/ingress.json \
  --base-config /srv/operator/reviewed-empty-haproxy.cfg \
  --output /srv/operator/ingress-package-01
```

Staging verifies the exact base SHA and refuses even a digest-approved base that
already contains routes. The package contains the empty TLS/HTTP fragments,
root listener/registry/HTTP map/publisher policy and the existing reviewed TLS,
HTTP, refresh, enrollment and UI-readiness helpers. No lab hostname, project UUID,
public IP or gateway IP is embedded in the installer. `manifest.json` binds all
fixed destinations, source hashes, modes and `package_sha256`; payloads are
private 0600 during transport. On loading, the destination allowlist and rendered
bytes are recomputed from the selected backend: a rehashed arbitrary target is
still refused. Existing shared service-smoke code/retirement lock may be reused
only if protected ownership, bytes and mode exactly match; no blind overwrite.

Transfer the trusted installer/backend and package securely to the ingress host.
Prepare a separate root-owned persistent transaction directory (all ancestors
protected), e.g. `sudo install -d -m 0700 /var/lib/spvm-ingress-install`.
An optional check validates a private candidate with `haproxy -c`, without
reloading, binding listeners or writing the live configuration:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  check-ingress --package /root/operator/ingress-package-01 \
  --package-sha256 REPLACE_WITH_APPROVED_PACKAGE_SHA256 \
  --output /var/lib/spvm-ingress-install/check-01
```

The check may run while the real host proxy is active, but does **not** authorize
replacement. Initial install refuses active/enabled host HAProxy before reading
private publication state. For an actually fresh inactive host:

```bash
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-ingress --package /root/operator/ingress-package-01 \
  --package-sha256 REPLACE_WITH_APPROVED_PACKAGE_SHA256 \
  --output /var/lib/spvm-ingress-install/install-01

# Review ingress-plan.json and approve its exact SHA within 900 seconds.
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  install-ingress --package /root/operator/ingress-package-01 \
  --package-sha256 REPLACE_WITH_APPROVED_PACKAGE_SHA256 \
  --output /var/lib/spvm-ingress-install/install-01 \
  --plan-sha256 REPLACE_WITH_APPROVED_PLAN_SHA256
```

The host guard checks machine identity, proxy inactive/disabled state, exact base,
assigned IP/listener conflicts, existing destination/state conflicts, shared code
and non-root publisher file access. A protected host lock, plan/source/fact SHA,
fresh approval and private write-ahead journal precede file mutation. Syntax is
checked before installation. The base is backed up as
`bootstrap-haproxy.backup.cfg` (0600). Auxiliary files are exclusive-created;
the config is atomically replaced last, with file/directory fsync. Protected
service and listener observations must not change. Failed/partial installation
requires manual review; there is no rollback, deletion or blind replay.

Success is `INGRESS_SCAFFOLD_INSTALLED_NOT_ACTIVATED`: host HAProxy remains
inactive/disabled and there are zero cluster routes. No daemon is enabled,
started, restarted or reloaded; Kolla HAProxy/config/credentials stay untouched.
No refresh timer is installed/enabled against an empty publication policy.
Host-proxy activation and integration with the overall deployment transaction
remain separate acceptance/integration work, not an implied success here.

Once a Swarm cluster exists, use the existing **root-authorized** publication
permit/export/enrollment/UI-readiness path. The root permit binds exact cluster
UUID, swarm/PKI/service domains and explicit HTTP port; exporter checks live
Nova/Barbican-derived endpoints within the allowed guest CIDR. The initial
installer neither invents guest IPs nor treats arbitrary instance metadata as
domain authority. Gateway hostname/public DNS registration, guest certificate
issuance and authorization of each cluster's routes remain explicit later steps.
Do not start the empty policy's refresh command and interpret its lack of exports
as a missing-cluster signal to delete anything.

Real acceptance on `.247` used `tests/accept_ingress_namespace.py`: a disposable
new network namespace, private temporary source/config copies, a dummy guest
address and a foreground test HAProxy. It checked scaffold syntax, refusal to
install over the real active proxy, 11 TLS/SNI cases (two synthetic clusters,
PKI priority, UI/service-zone routing, unknown/no SNI and label boundary) and
13 HTTP challenge/method/path/Host cases. Live configuration, routes, firewall,
proxy PID/start observations and domains were unchanged. This proves routing,
**not** TLS certificate validation/attestation or two real Swarm deployments.
Initial file installation remains fixture-tested, not empty-host accepted.

```bash
python3 tests/accept_ingress_namespace.py \
  --ssh-target operator@CONTROLLER --identity /secure/operator_key \
  --known-hosts /secure/known_hosts --driver-dir /srv/swarm-cloud
```

The opt-in helper requires the existing lab-style root ingress files for its
read-only snapshots; those paths are generic, but it is not a fresh-host check.
Every network setup command in the child verifies that it is outside the parent
and PID1 host namespace. The child has no physical NIC/default route. No public
ports are bound by the test and no DNS or guest lifecycle action occurs.

### 10.23. Activate the installed empty host ingress

Installation and activation are separate approvals. After §10.22 returns
`INGRESS_SCAFFOLD_INSTALLED_NOT_ACTIVATED`, keep the exact staged package and its
SHA. On the ingress host, as root, run:

```bash
sudo scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-ingress-activation \
  --package /srv/spvm-ingress-package --package-sha256 <PACKAGE_SHA> \
  --output /var/lib/spvm-ingress-activation/initial

sudo scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  activate-ingress \
  --package /srv/spvm-ingress-package --package-sha256 <PACKAGE_SHA> \
  --output /var/lib/spvm-ingress-activation/initial --plan-sha256 <PLAN_SHA>
```

Replace placeholders with the hashes returned by staging/planning. Use a new,
separate persistent root-protected transaction directory, not `/tmp` or an
existing installation journal. The host HAProxy package and its standard service
unit must already be installed; this step is not package installation.

The 900-second approval binds package/source/host/unit/listener observations. A
global lock serializes initial installation and activation. Activation requires
the exact installed **empty** scaffold, empty ingress state, assigned bind IP,
free explicit 80/443 listeners, inactive/disabled host HAProxy, and successful
`haproxy -c`. It refuses existing publications; it does not adopt a working proxy.

Only `systemctl start haproxy.service` is requested. Before enabling boot startup,
the workflow verifies active status, exact listeners, the master's `-f` config
argument, unchanged protected services/files, HTTP rejection of an unknown Host,
and TLS rejection without SNI. The negative TLS probe deliberately accepts any
certificate: a completed handshake is a **failure**, not a trusted connection.
Guest/public UI certificate validation is never disabled. Enablement is followed
by another scaffold/unit/service check. Success is `INGRESS_ACTIVE_EMPTY`.

No Kolla HAProxy container, Nova container, guest, DNS record, certificate or
publication policy is modified. On a partial failure, preserve
`ingress-activation-transaction.json` and inspect actual service state: HAProxy
may already be running, or enabled if the final verification failed. There is
no automatic stop, rollback or replay, and no claim that an error implies that
nothing happened. Review the journal before any recovery.

The initial start/enable path passed on the real freed .247 ingress after the
sole old publication was withdrawn and its exact owned files/state backed up.
The workflow returned `INGRESS_ACTIVE_EMPTY`, with Kolla/OpenStack container
start timestamps unchanged. This was an existing-OS empty-ingress experiment,
not a newly installed OS. Namespace routing tests remain separate evidence.

### 10.24. Publish an explicitly authorized Swarm and wait for HTTPS UI

Compute activation must precede guest creation. Existing prepared computes use
the §11 `plan-add-compute`/`add-compute` transaction; its verified `NOOP` mode
does not restart a populated compute. A new offline-enrolled compute from
§10.21 must remain quarantined until its separate startup, runtime, API,
Placement, native PCI and network acceptance succeeds. The new ingress/UI
commands do **not** bypass the §10.25 compute startup/acceptance workflow or
silently enable scheduling. Never confuse a Nova-container restart on an empty
host with a bootstrap-guest restart.

Create the Swarm through the existing `openstack spvm cluster create` flow and
its explicit spec/enrollment/unique-names journal. Do not create replacement
guests by restarting old encrypted state disks. Prepare an exact root-owned,
0600 publication permit with `prepare-ingress-request` and review the cluster
UUID, Swarm/PKI domains, service zones and HTTP port. Its input paths must be
readable by the configured non-root publisher; provider credentials stay out of
the permit and reports. The installed admission helpers validate project,
Nova/Neutron endpoint ownership and Barbican CA references. A root permit is
authorization, not proof that the gateway is already ready.

On the ingress host:

```bash
sudo scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-spvm-publication --request /etc/spvm/ingress-request-my-cluster.json \
  --timeout 1800 --output /var/lib/spvm-publication/my-cluster-01

sudo scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  publish-spvm-ui --request /etc/spvm/ingress-request-my-cluster.json \
  --timeout 1800 --output /var/lib/spvm-publication/my-cluster-01 \
  --plan-sha256 <PLAN_SHA>
```

These commands use the fixed installed root-protected helpers and
`/etc/spvm/ingress-policy.json`, not arbitrary commands from a plan. They require
the canonical protected private permit, current machine identity, and the fixed
registry/HTTP port policy. Planning validates admission without applying it and
binds all helper/input hashes to a 900-second approval. Applying uses a global
workflow lock and durable stage journal, then:

1. Enroll the exact root-authorized cluster/domain selection. Only a definite
   `WAITING_GATEWAY` (no enrollment mutation) may be retried within the deadline.
2. Invoke `spvm-ui-ready --apply`. It re-exports authorized endpoints, verifies
   gateway service ports, reconciles TLS/HTTP routes, and waits for public HTTPS
   UI, JSON UI configuration and GraphQL readiness with strict certificate trust.
3. Return `UI_READY`, the cluster UUID and Swarm domain. Do not print response
   bodies, credentials or arbitrary helper diagnostics.

No guest SSH, lifecycle operations, CA private-key export, DNS creation or
certificate issuance is added by this wrapper. Existing ingress reconcilers may
reload **host** HAProxy for approved routes. Unknown SNI/Host do not fall back
to another cluster or OpenStack API. Secrets remain in the existing provider
config/Barbican boundary; a CA public chain is not the private signing key.

Cold-start acceptance on 2026-10-19 with the published 0.2.22 driver and
build-449-release took 1443 seconds (24 minutes 3 seconds) for publication after
the three guests were ready. Both Swarm UIs then passed strict HTTPS UI/config/
GraphQL checks, and the disposable cluster was removed without affecting the
original cluster. The examples explicitly allow 1800 seconds; the command's
default remains 900. This does not extend the separate 900-second plan-approval
window: approve and start promptly, using the same timeout for plan and apply.
Nova ACTIVE/READY confirms CA/core readiness, not UI readiness. Do not restart
bootstrap, recreate guests, relax TLS checks or replay publication to speed up
this wait. Gateway/TLS/UI became ready without such changes; the internal cause
of the long Swarm startup was not established by this test.

An ambiguous timeout or failed mutating call stops with `NEEDS_REVIEW`; there is
no blind replay or automatic route rollback/deletion. A policy/registry may
already be enrolled, and HAProxy may already have published routes even if the
final UI check fails. Inspect `publication-transaction.json`, native helper state
and the current UI. A new reviewed plan after reconciliation is allowed; simply
rerunning an old transaction is not.

Real acceptance on the existing .247 cloud completed with `UI_READY` for
`openstack-activation.swarm.superprotocol.dev`. Nova container identity/start,
libvirt service and guest-domain inventory were unchanged. Both existing
computes also passed read-only runtime/Placement/state/native-PCI/network checks.
This is existing-cloud acceptance, **not** OS-to-cloud or initial empty-host
runtime activation acceptance. The published cluster was reused, not recreated.

The opt-in test `tests/accept_openstack_publication.py` requires `--apply`, exact
SSH identity/known-hosts, driver directory and an existing root permit. It may
publish/reload authorized host ingress. Source copies are temporary; private
transaction evidence remains under `/var/lib/spvm-publication/` on the server.
Do not run it as a supposedly read-only probe.

The acceptance harness now allocates a known unique report directory **before**
starting SSH (the directory itself is created by the approved remote plan).
New apply runs save `acceptance-before.json` before applying publication and
`acceptance-receipt.json` before printing the final result. Both are private,
fsync-backed files next to the native publication journal. Existing report
directories are never adopted or overwritten by another apply run.

SSH uses strict host-key checks plus bounded keepalives. A timeout, connection
failure, nonzero SSH exit or progress-only stdout cannot produce a successful
acceptance result. Failures print the known report path and a recovery hint,
not raw stdout/stderr or an unhandled timeout traceback. Keepalives and durable
receipts address bounded failure/recovery; they do not establish the root cause
of the previously observed hung connection.

After a lost acknowledgement, use the explicit read-only mode, not `--apply`:

```bash
python3 tests/accept_openstack_publication.py \
  --ssh-target deploy@ingress.example.net \
  --identity /private/deploy_ed25519 --known-hosts /private/known_hosts \
  --driver-dir /srv/swarm-cloud --request /etc/spvm/ingress-request-my-cluster.json \
  --timeout 60 \
  --check-report /var/lib/spvm-publication/accept-<32 lowercase hex digits>
```

Run from the sp-vm-tools checkout. This mode creates only temporary source
copies; it does **not** enroll, publish, reload ingress, rewrite historical
receipts or change guest lifecycle. It checks the protected completed native
journal, plan digest, exact permit/cluster/domain/machine/helper hashes, current
strict HTTPS UI/config/GraphQL, and unchanged Nova/libvirt snapshot during the
read-only check. An incomplete journal, conflicting permit, helper drift or
available prior baseline mismatch refuses success.

`mode=check-report` is distinct from an original apply receipt. If the old report
predates durable acceptance snapshots, `prior_acceptance_snapshot=UNAVAILABLE`
explicitly means that original before/after proof cannot be reconstructed.
The current read-only check may pass; it does not retroactively make the old
SSH wrapper successful. New reports with a matching saved baseline return
`MATCH`; drift returns `DRIFT` and fails. Do not use a missing receipt as authority
to repeat publication, and do not confuse recovery validation with clean-OS
installation or service-credential acceptance.

Real read-only verification of the previously published `openstack-fresh`
report returned exit0 / UI_READY with all three public checks passing and
current Nova/libvirt state unchanged. Its historical baseline is correctly
reported UNAVAILABLE. New apply receipt persistence, timeout sanitization,
progress-only/nonzero transport refusal, drift and evidence preservation are
covered by local tests; a new real apply was deliberately not run on that Swarm.

### 10.25. Quarantine, enroll and activate one prepared empty compute

The installer now exposes a separate prepared-host workflow:
`quarantine → stop → enroll → start → accept → enable`. It is not hardware
bootstrap, Kolla deployment, automatic draining, an image upgrade or VM recovery.
The selected host must have a **compute** role. A prepared all-in-one host may
also have control, network and ingress roles: only its `nova_compute` unit is
stopped/started, never the whole Nova role or Docker daemon. The separate
`add-compute` configuration/genconfig workflow in §11 still requires compute-only.

All host preparation, Kolla configuration, image delivery and identity binding
must already be complete. Supply the exact pinned compute image in the deployment
configuration. The matching `nova_compute` container must already exist with a
read-only bind mount of `/etc/spvm/runtime-services.json`. The workflow never
recreates that container or substitutes an image, nor modifies BIOS, libvirt,
QEMU, QGS, GPU registration, pool budgets, host networking or provider credentials.

Run from the trusted operator machine with the required Python dependencies and
strict SSH known-hosts already configured. Example fresh enrollment:

```bash
scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-compute-activation --config /srv/deployment.yaml --host worker1 \
  --controller control1 --controller-python /opt/openstack-client/bin/python \
  --openrc /etc/kolla/admin-openrc.sh \
  --enrollment-request /srv/runtime-enrollment/worker1.json \
  --output /srv/private/worker1-initial-01

scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  activate-compute --config /srv/deployment.yaml --host worker1 \
  --controller control1 --controller-python /opt/openstack-client/bin/python \
  --openrc /etc/kolla/admin-openrc.sh \
  --enrollment-request /srv/runtime-enrollment/worker1.json \
  --output /srv/private/worker1-initial-01 --plan-sha256 <PLAN_SHA>
```

The request comes from §10.21 `stage-runtime-enrollment`. Fresh mode requires
the exact existing deny-all document, prepared helper release/cache/GPU/pool
intent, machine and Nova UUID, immutable image/context, and an explicit API-only
runtime. It does not adopt an already enrolled runtime. The root-local installer
retains a private bootstrap backup and validates the candidate using the pinned
image normalizer before atomic replacement.

To test activation or recover scheduling on an **already enrolled**, otherwise
matching prepared empty host, replace `--enrollment-request ...` in both commands
with `--preserve-enrollment`. This explicit mutually exclusive mode leaves the
existing runtime file untouched; its `enroll` stage is a no-op. It is not proof
that a deny-all-to-enrolled write was exercised in that run.

Planning is read-only on the hosts/cloud. Private local observation files are
created. Approval binds the complete deployment config, backend source hashes,
controller/auth paths, root helper/runtime/host-code observations, Nova service
identity and host UUID. The initial approval expires after 900 seconds. An admin
token is required for all-project drain observations; endpoint comparisons in
fresh mode allow only the same HTTPS origin/path with an optional `/v3` suffix.

Before every stage, the workflow checks the exact host identity, absence of
libvirt domains/direct hypervisor processes, empty physical state claims/images,
empty Placement allocations, no assigned Nova instances or inflight BUILD/task
states, and no claimed native PCI devices. Neighboring hosts' domain inventories
are protected. This conservative check may refuse activation because of a build
on another host: wait/review it, do not bypass the guard.

Quarantine is a Nova API operation on the exact service ID. Only after confirmed
disablement may the workflow stop `kolla-nova_compute-container.service` on the
selected empty host. It then installs the fresh runtime or preserves the existing
one, starts the same prepared Nova container, and checks:

- pinned image, driver import/configuration, helper capabilities and host libvirt;
- actual mounted runtime bytes and schema inside the running Nova container;
- heartbeat, healthy enabled OVN controller, reconciled Placement CPU/RAM/state
  inventory and native Nova PCI accounting;
- unchanged host runtime code/configuration and protected neighboring guests.

Every non-`nova_compute` Docker container is bound by ID, image, configuration
hash, running state, start timestamp and restart count. Replacement, restart,
addition/removal or configuration drift fails acceptance. On all-in-one hosts,
required Keystone, Nova API/scheduler/conductor, Placement, Barbican, Neutron,
OVN and OVS containers must also be running and healthy when healthchecks exist.
Host HAProxy lifecycle is protected alongside Docker/libvirt/QGS/helper services.
Environment/configuration values are not exported in these fingerprints.

Service status reconciliation skips an already matching enable/disable write
and still performs the follow-up GET. This matters with OpenStack SDK dirty-field
tracking: repeating disable with a different reason can omit the unchanged
`status` field, which Nova rejects as "No updates were requested". Do not treat
that SDK/API error as evidence that the compute was enabled or restart it.

Only successful acceptance permits enabling scheduling. A final read-only
acceptance follows enablement; failure attempts to re-quarantine the same exact
service. It never powers off a guest as compensation. The check cannot serialize
an unrelated administrator deliberately starting services/VMs or reconfiguring
the host: keep other deployment automation out of this maintenance window.

Transactions retain `transaction/transaction.json`, private observations and
sanitized failure locations. `stop`, fresh `enroll`, `start` and `enable` are
non-replayable after a lost acknowledgement: `NEEDS_REVIEW` means inspect actual
service/runtime state, not rerun those actions blindly. A failed read-only
acceptance may resume the same transaction without another stop/start. Completed
transactions do not repeat enablement; new workloads prevent maintenance replay
and are never automatically quarantined/stopped by a completed retry.

Normal Nova identity ownership is accepted only for `/var/lib/nova/compute_id`
and its immediate parent (root or UID42436, no symlinks or group/world writes).
The file is bounded, unaliased and tied to the root helper UUID. Executable code
and all other protected configuration remain root-owned. An otherwise empty
state pool may contain only a valid empty `.spvm-claims.json` and its protected
empty `.spvm-state.lock`; claims, disks, incomplete bookkeeping or unknown files
still refuse fresh enrollment. These allowances do not free capacity or delete
anything.

Fresh OS-to-cloud installation remains a separate acceptance target. The real
existing-host experiment and any preserve-mode run must not be reported as a
fresh image installation or a real deny-all enrollment write.

Real acceptance on 2026-10-17 advanced this boundary: empty `.245` completed an
actual deny-all → enrolled runtime replacement and all six activation stages;
prepared all-in-one `.247` completed preserve-mode activation with every other
container and protected host-service lifecycle unchanged. Both returned
`COMPUTE_READY`. Private plans/journals are retained on the controller under
`/var/lib/spvm-activation-acceptance/20261017-fresh-all-in-one`.

The test first explicitly backed up/prepared the legacy lab configuration:
release449-only helper policy, debug disabled, matching existing release cache,
and a deny-all runtime on a stopped empty `.245` compute. Its old installed
helper still required a debug default; only the tested `validate_config`
implementation differed and was updated with a backup before planning. Empty
GPU lease bookkeeping was archived after stopping its helper, not silently
accepted by weakening the fresh-state guard. No allocated claims/disks were
removed to force acceptance, and no measured launcher/libvirt/QEMU/QGS changes
were made. These are explicit existing-lab preparation actions, not implicit
behaviour of `activate-compute` or proof of clean-OS acceptance.

Both compute containers now use the separately prepared pinned image
`spvm/nova-compute:bootstrap-accept-20261016-01` (ID
`sha256:6da8b1f285d8b7a1dc5ed1a6cbfe9152544e23e495e6489258f507a890e03155`).
Its original receipt is in `bootstrap-image-accept-dBrHcAM7/build-01`, **not**
the similarly named earlier `image-build-accept-*/build-03` directory, which
describes a different image/context. Never substitute a receipt by filename.

The separate lab image preparation also exposed a Kolla scoping pitfall:
overriding `nova_services={}` selected no API containers, but a later upstream
task still indexed `nova-api`. Both compute replacements were verified healthy
and protected snapshots matched, although the enclosing Kolla command returned
nonzero. Preserve its log and verify actual state; do not call that a successful
full Kolla run or blindly repeat image replacement. A first attempt also exposed
operator read permissions after atomic root-owned `globals.yml` replacement:
retain private read access for the deployment user, never make secrets world-readable.

After both activations, a newly created release449 Swarm (`openstack-fresh`)
completed with all three Nova instances `ACTIVE/READY`: GPU seed on `.245`,
two CPU-only joins on `.247`, one shared Neutron network. Full read-only
reconciliation returned `ACCOUNTING_RECONCILED` with no blockers: native PCI
ownership, domain UUIDs, physical state claims and Placement allocations agree.
The native publication journal reached `UI_READY`, and an independent public
strict-TLS smoke passed UI HTML, JSON config and GraphQL on its first attempt.
Final private receipts are in the controller acceptance directory's
`new-swarm-final/` subdirectory. The cluster was left running.

The remote acceptance SSH wrapper itself hung after server success and was
closed separately; its exit code was 1, not a successful wrapper receipt.
Publication was not replayed. Distinguish this transport failure from the
durable native UI_READY record and independent HTTPS checks; its root cause
remains unconfirmed. Do not claim the wrapper's final unchanged-snapshot check
passed, or use a successful public smoke as proof of clean-OS installation,
S3/Harbor credentials or Swarm quorum/failover testing.

### 10.26. Reconcile only a registered Nova compute container

Use `plan-kolla-compute-container` / `reconcile-kolla-compute-container` for a
prepared registered compute whose persistent Kolla configuration and cached image
are already ready. This does not generate Nova configuration, download an image,
register a bare compute, deploy the control plane, enroll a project or launch VMs.
Image export/delivery, hardware bootstrap and runtime activation remain separate.

The earlier lab workaround combined `--tags nova` with `nova_services={}`.
It updated the compute containers but failed when a later API task indexed the
missing `nova-api` definition. Do not use that workaround. This workflow selects
the pinned **nova-cell-deploy** play and one literal inventory host; it copies
only the upstream `nova-compute` definition into `nova_cell_services` and leaves
the complete API service dictionary untouched. API/bootstrap/migration/reload
plays are not selected.

The workflow makes an isolated run tree from tracked files in the clean pinned
Kolla checkout. A narrowly guarded copy of the upstream container module is used
there; the original checkout is not patched. Native container comparison and
creation remain upstream code. The guard runs before the Docker worker and
restricts actions to the exact `nova_compute`, machine fingerprint, compute UUID,
helper UUID and preloaded image ID. Implicit image pulls are refused. Container,
domain, protected host-service and runtime-file snapshots must remain unchanged,
except the compute container itself during explicitly allowed empty replacement.

This is not `ansible --check`: the pinned native container module does not support
check mode. The explicit request mode determines admission:

- `verify`: native comparison must be NOOP. Any mutating handler, including one
  unexpectedly caused by drift, is refused before constructing the Docker worker.
  No Nova service enable/disable calls are made.
- `replace-empty`: requires a current administrator OpenStack context, correct
  hypervisor/service identity, no instances assigned to that host and no inflight
  builds/tasks anywhere in the observed cloud, confirmed
  scheduling quarantine, and no local domains/direct guests/state claims/disks/
  launch journals/GPU leases. Only the compute container may be replaced. It is
  deliberately **not** enabled for scheduling after the Kolla command.

Write a private request on the existing controller/deployment machine. Example
values are placeholders; obtain full fingerprints and IDs through trusted audit:

```json
{
  "schema_version": 1,
  "source": "/srv/kolla-ansible",
  "revision": "<full pinned 40-hex Kolla commit>",
  "inventory": "/srv/openstack/hosts.ini",
  "profile": "/srv/openstack/host-profile.yml",
  "kolla_python": "/opt/kolla-venv/bin/python",
  "collections": "/srv/ansible/collections",
  "configdir": "/etc/kolla",
  "host": "compute01",
  "hostname": "compute01.example.net",
  "machine_sha256": "<SHA256 of stripped /etc/machine-id>",
  "compute_uuid": "<canonical registered Nova compute UUID>",
  "image": "registry.example.net/spvm/nova-compute:reviewed-version",
  "image_id": "sha256:<exact preloaded image ID>",
  "mode": "verify"
}
```

`host` is one inventory alias, not a group, wildcard or comma-separated pattern;
`hostname` is the actual registered Nova hypervisor name. Ansible's INI parser
must bind the alias to a compute host. The SDK exposes Nova's JSON
`hypervisor_hostname` as `Hypervisor.name`; do not test the raw JSON field as an
SDK attribute. The machine digest is SHA256 of the stripped machine-id text.

The existing `globals.yml` must already select the same image reference. That
persistent configuration is not rewritten implicitly. Use a reviewed non-floating
tag plus exact local image ID: this pinned Kolla Docker worker looks up RepoTags,
so digest-only references are not accepted by this prepared-container command.
The tag must not be retargeted or removed by another process during the operation.
Private logs may contain native comparison diagnostics; never publish them raw.

Run as the trusted non-root deployment user with private read access to Kolla
configuration, working strict Ansible SSH trust and sudo on the selected host.
The installer interpreter needs PyYAML; replacement additionally needs
OpenStackSDK/current admin authentication and trusted API CA. `kolla_python`
separately selects the existing Ansible/Kolla environment. Credentials are not
request fields and are removed from the environment passed to Ansible.

```bash
scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-kolla-compute-container --request /srv/private/compute01-scope.json \
  --output /srv/private/compute01-scope-01

scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  reconcile-kolla-compute-container --request /srv/private/compute01-scope.json \
  --output /srv/private/compute01-scope-01 --plan-sha256 <PLAN_SHA>
```

The plan binds controller inputs, inventory/profile, globals/passwords/fragments,
Nova compute configuration, the tracked Kolla source, installer sources and the
complete staged run tree. Approval expires after 900 seconds. Unexpected staged
files, symlinks and input/source drift refuse execution. One transaction has a
nonblocking lock; administrators must serialize different operations/controllers
against the same compute and exclude unrelated deployment/image writers.

An exit0 verify result is `COMPUTE_CONTAINER_VERIFIED`, with native exit0 and
proof that the guarded module executed. Successful empty replacement returns
`COMPUTE_CONTAINER_APPLIED_QUARANTINED`: this is **not** runtime/Placement/native
PCI/network acceptance. Use §10.25 with matching pinned configuration before
enabling scheduling. The original service status is recorded in the private
journal; this image step never silently restores it.

An ambiguous timeout or native failure leaves `NEEDS_REVIEW`; inspect the private
`compute-container-transaction.json`, `private-kolla.log` and actual container/
quarantine state. A container may already have changed before an error. An
existing transaction is never replayed. To perform another NOOP verification,
create and approve a **new** plan after reviewing the prior outcome.

Real acceptance on the existing Ubuntu26/Kolla2026.2 lab completed two native
verify runs on each host (.247 all-in-one and .245 compute-only), all exit0.
Protected before/after snapshots for both hosts matched, including every Nova
container, guest domain, control-plane/network container and runtime file.
Real read-only Nova admission refused replacement on both occupied hosts, and a
uniquely named guarded test module rejected a synthetic mutating action before
the Docker worker (expected Ansible exit2). A real restart handler was not used.
Final private positive evidence on the controller:
`/home/petr/kolla-spvm/compute-scope-accept-1792246392`.
Negative evidence: `compute-scope-accept-1792246230` under the same parent.

Subsequent live acceptance on 2026-10-17 exercised the **actual replace-empty
branch** after operator-approved deletion of the disposable Swarm. On `.245`,
two separately approved native Kolla operations changed the compute image from
`bootstrap-accept-20261016-01` (`sha256:6da8b1f285d8b7a1dc5ed1a6cbfe9152544e23e495e6489258f507a890e03155`)
to the already cached `installer-accept-20261016-03`
(`sha256:1b2bd23a8a2f76792cdc9e641dd5e899c7b5c74a5860c5aacfc20909df169a16`)
and back. Both returned native exit0 and
`COMPUTE_CONTAINER_APPLIED_QUARANTINED`; the actual image changed, not just its
tag. Protected container/service/runtime snapshots matched. Controller `.247`
Nova also matched its earlier independent verify baseline. No guest was running
during either operation; scheduling remained disabled for separate acceptance.

Private evidence:
`/home/petr/kolla-spvm/compute-image-roundtrip-20261017-01` on `.247`.
This is a controlled image roundtrip, **not** a new driver release, an automatic
rollback test, or a guest evacuation/data-preservation test. Never obtain an
empty compute by assuming that READY replacement joins prove Swarm quorum.
Destroying a disposable Swarm requires explicit scope and loses its ephemeral
guest state; a new Swarm needs a new UUID, CA and unique node identities.

The separate preserve-enrollment activation subsequently returned `COMPUTE_READY`.
One local edit of a SHA-bound backend helper caused a fail-closed pause before
enable; the exact approved sources were restored and the same journal resumed
only the remaining step, without another stop/start. The BLOCKED receipt and
error history were retained. Do not edit **any** backend Python source during a
transaction, including unrelated lab helpers. Do not resume an ambiguous inflight
stop/start just because restoring the source resolved a different guard failure.

A new `openstack-reconciled` release449 Swarm then completed creation with three
ACTIVE/READY nodes across both hosts. Full Nova/libvirt/Placement/state/native-PCI
reconciliation passed without blockers. Public HTTPS UI/config/GraphQL all returned
HTTP200. The new SSH publication acceptance completed exit0 with its own durable
before snapshot and passed receipt, without publication replay or guest lifecycle.
Final private evidence is under
`/var/lib/spvm-activation-acceptance/20261017-image-roundtrip` on `.247`.
TCP-open alone did not prove UI readiness: an initial external smoke timed out
while gateway TLS was not ready; the native bounded wait later succeeded without
network edits or restarts. Internal service root cause was not established.

Clean-OS deployment and installing a missing Docker daemon on Ubuntu26 remain
separate unaccepted tests. The roundtrip does not remove those limitations.

### 10.27. Verify an occupied Kolla installation without changing it

Use `verify-kolla-existing`, **not** the backend's `verify-existing` or
`plan-existing` / `apply-existing`: those older commands target DevStack paths,
repository pins and services. Do not run fresh-host preparation or replay
deny-all bootstrap on an occupied cloud to make a verification pass.

Run from the trusted deployment controller as the private workspace owner:

```bash
scripts/install-openstack.sh --driver-dir /srv/verified-driver-bundle -- \
  verify-kolla-existing \
  --config /srv/private/deployment.yaml \
  --workspace /srv/private/bound-workspace \
  --known-hosts /srv/private/known_hosts \
  --controller control01 \
  --controller-python /opt/spvm-client/bin/python \
  --openrc /srv/private/admin-openrc.sh \
  --compute-request /srv/private/compute01-verify.json \
  --compute-request /srv/private/compute02-verify.json \
  --output /srv/private/existing-verification-01
```

`controller` is a host key in the normalized deployment config, with the control
role. Supply exactly one §10.26 compute request per compute inventory alias,
including an all-in-one controller if it also has the compute role. Every request
must explicitly select `mode: verify`; `replace-empty` is refused. Requests must
point to this workspace's inventory/configdir and the same pinned Kolla source,
Python, collections, machine identities and image reference/ID. Existing compute
UUIDs remain explicit and are checked by native admission and accounting.

The runtime profile must preserve the bound host profile, with changes allowed
only for an explicit absolute existing host Python interpreter and this exact
strict transport (substitute the same `--known-hosts` path):

```yaml
ansible_python_interpreter: /opt/kolla-host-venv/bin/python
ansible_ssh_common_args: '-F /dev/null -o IdentityAgent=none -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/srv/private/known_hosts'
```

This does not install the interpreter/SDK or create SSH trust. Keep the bound
workspace immutable. The tool validates its receipt, rendered configuration,
image binding, clean pinned Kolla checkout and collections. It checks physical
host identity/reserves, native Nova PCI accounting, Placement/state allocations,
local claims/disks and registered compute services through the existing read-only
accounting collector. That collector needs controller OpenStackSDK/current admin
auth and sudo access to the existing `nova_conductor` container for Nova PCI
observations. The non-root operator needs strict SSH and sudo on all configured
hosts, and existing Ansible/Kolla dependencies for native compute comparison.

For each compute the tool creates a fresh §10.26 `verify` plan and runs only
guarded `nova-cell-deploy` container comparison. Restart, recreation and implicit
pull remain forbidden before the Docker worker. It never repairs drift, changes
service scheduling state, installs packages, creates guests, or runs a global
deploy/reconfigure. Full runtime snapshots before/after cover container identities,
start times/configuration, protected service identities, domains, QEMU process
identities and installed compute configuration hashes. Local private evidence,
Ansible temporary files and a read-only Nova-conductor observation process are
expected; this is not a zero-process/zero-local-write command.

Success is exit 0 / `EXISTING_KOLLA_SCOPED_VERIFIED`. Exit 2 / `NEEDS_REVIEW`
contains the failing stage and sanitized error type, with accounting blockers and
private native logs where available. Do not publish raw logs. Inspect
`existing-kolla.json`, `runtime-before.json`, `runtime-after.json`, `accounting/`
and per-compute transaction directories. Failure is never automatic repair
authority. Initial source/output admission failures may leave no final report;
preserve any partial private directory for review.

Use a **new** private output directory for every run, never overwrite an earlier
transaction. The output must be separate from and outside the bound workspace.
The command requires stable inputs and completes within a 900-second observation
window; input/runtime drift fails closed. Administrators must serialize it with
deployments, scheduling and other maintenance to avoid inconsistent observations.

The report explicitly excludes full rendered-to-installed control-plane config
comparison, every service/API's functional health, loaded Python code identity,
Swarm UI/quorum acceptance, full deployment idempotency and clean-OS installation.
Run separate UI/CA/readiness probes when appropriate. Do not interpret the scoped
success status as blanket permission to run Kolla deploy on occupied SPVM hosts.

### 10.28. Common scenario plan/apply

`plan-openstack` / `apply-openstack` coordinate the guarded phases. Hardware
bootstrap remains separate. Select the scenario explicitly; there is no auto
mode, implicit upgrade or fallback from an occupied cloud to fresh deployment.

| Request mode | Operation | Mutation boundary |
| --- | --- | --- |
| `fresh` | Fresh Kolla prechecks/deploy/post-deploy | Empty prepared cloud; explicit approval |
| `existing` | Scoped verification at plan and apply | No repair or deployment |
| `add-compute` | Prepared compute NOOP, otherwise initial enrollment | Only a prepared empty compute-only target |

Write a private JSON request, for example:

```json
{
  "schema_version": 1,
  "mode": "existing",
  "config": "/srv/private/deployment.yaml",
  "workspace": "/srv/private/bound-workspace",
  "known_hosts": "/srv/private/known_hosts",
  "controller": "control01",
  "controller_python": "/opt/spvm-client/bin/python",
  "openrc": "/srv/private/admin-openrc.sh",
  "compute_requests": ["/srv/private/compute01-verify.json", "/srv/private/compute02-verify.json"]
}
```

Use the §10.27 verify-only requests/profiles and include all computes. Remote
controller interpreter/OpenRC paths need not exist locally; normal venv Python
symlinks are supported. Local inputs/workspace must be absolute canonical paths.
Requests reference protected files, never contain secret payloads or shell code.

```bash
scripts/install-openstack.sh --driver-dir /srv/verified-driver-bundle -- \
  plan-openstack --request /srv/private/workflow.json \
  --output /srv/private/workflow-01

# Review workflow-plan.json and the private child plans; copy the printed SHA.
scripts/install-openstack.sh --driver-dir /srv/verified-driver-bundle -- \
  apply-openstack --request /srv/private/workflow.json \
  --output /srv/private/workflow-01 --plan-sha256 <REVIEWED_SHA256>
```

First apply requires a plan younger than 900 seconds. Approval binds request,
config/workspace, SSH trust, runtime profiles/enrollment input, installer/backend
sources and child plan. Input/source/child-plan drift and mode/action mismatch
are refused. A private transaction lock protects this output; serialize different
controllers/transactions externally. Never overwrite an old plan directory.

For `fresh`, retain only the common fields `schema_version`, `mode`, `config`,
`workspace`, `known_hosts`, and add boolean fields `ack_new_cloud: true`,
`allow_experimental_ubuntu26`, `use_testing_images`. Set the latter two explicitly
for the chosen environment, not automatically. Existing-cloud auth/compute fields
are not accepted in this schema. A prepared bound workspace and the documented
fresh-host prerequisites remain mandatory. Apply additionally requires
`--allow-changes`; blocked fresh plans cannot be approved. The child workflow
retains empty-host, source, protected-runtime and phase-boundary checks.
It never reruns hardware bootstrap or regenerates passwords/certificates.

Fresh completion is `OPENSTACK_INSTALLED_NOT_SPVM_ENROLLED`, not full SPVM readiness.
Project/network/Barbican enrollment and initial compute activation remain the
explicit post-deploy phases documented above. They cannot be approved using
unknown project/resource IDs before the cloud exists. Do not launch a Swarm until
those phases pass. This coordinator is not an unattended bare-host provisioner.

For `add-compute`, retain common and controller/auth fields; replace
`compute_requests` with `host` (deployment config key) and `enrollment_request`
(absolute path to the §10.21 staged runtime request, or null for an already-ready
host). The config/workspace must already describe the intended inventory.
An existing ready registered compute selects NOOP without restart, even if occupied.
Otherwise initial activation requires an empty compute-only target, exact matching
bootstrap/runtime request and `--allow-changes`. Hardware/libvirt, helper, image,
Kolla containers/OVN and initial registration must already be prepared.
The child workflow quarantines/enrolls/starts/accepts/enables that compute only;
no control-plane reconfiguration, guest drain or resource upgrade is included.
Missing preparation is refusal, never a reason to bypass a guard.

`workflow-transaction.json` records RUNNING before delegation, COMPLETE only after
child success and unchanged inputs, NEEDS_REVIEW on failure/interruption.
Inspect parent and child journals before explicit `--resume`. Resume cannot waive
child ambiguous/lost-ack guards; fresh Kolla resumes only at confirmed boundaries.

Repeating a completed `existing` apply performs another read-only verification
and returns `OPENSTACK_NOOP_COMPLETE`. Completed fresh/add-compute apply returns
`ALREADY_APPLIED_NOT_REVERIFIED` and its historical result without repeating
mutation. This is not a health claim: use a new `existing` plan to check the live
cloud. A new add-compute plan for an already-ready host also selects NOOP.
The scenarios do not certify generic Kolla reconfigure idempotency or clean-OS
installation; fresh and mutating compute paths still require separate live
acceptance on suitable empty hosts.

### 10.29. Skyline customization artifact

Skyline itself is supplied by the pinned Kolla console image. All SPVM-specific
UI pages, branding, login compatibility patches and service examples belong to
**swarm-cloud**, alongside its Nova driver and authenticated web API.

Use `deployment/skyline/` from the verified OpenStack driver bundle selected in
section 8. The bundle manifest binds these files, including the logo and web
assets, to source SHA256 hashes. Do not copy patches from an unrelated installer
checkout or mix UI and API versions.

The detailed build, service, capacity-admission and recovery instructions are in
`deployment/skyline/README.md` inside that same artifact. Repository reference:
[SPVM Skyline extension](https://github.com/Super-Protocol/swarm-cloud/blob/feature/spvm-bare-metal-cloud/examples/test-cluster/bare-metal-spvm/deployment/skyline/README.md).
That branch link is for navigation, not an immutable deployment input; use the
documentation shipped with the approved archive.

The installer remains responsible for artifact resolution, derivative-image
deployment, service/nginx configuration and reviewed Kolla overrides. It does
not maintain another implementation of the frontend or patch helpers. Existing
releases such as 0.2.22 predate the Skyline extension; do not assume those archives
include it.

Pin the derivative console image and its exact image ID with the paired
`kolla.skyline_console_image` / `kolla.skyline_console_image_id` fields in a
supported backend. Preserve the nginx proxy customization in Kolla's approved
custom configuration. Regenerate reviewed inputs instead of editing immutable
workspaces. Deploying the console must not restart Nova, libvirt or SPVM guests.

### 10.30. Super image category and logo

See the matching bundle's `deployment/skyline/README.md`, **Super image category
and logo** section. The unchanged Glance marker is `os_distro=super-spvm`.
The logo is bundled, not fetched from an external service at runtime.

### 10.31. Swarm pages and join admission

See the matching bundle's `deployment/skyline/README.md`, **Add a join node from
Skyline** section, for the authenticated API, service examples, toolbar links,
minimum-three-ready-node availability display, native resource observations and
guarded failed-tail recovery. Keep credentials and journals private; do not expose
the loopback API or grant the web service Docker/DB access. Capacity admission is
advisory, not a Nova reservation.

## 11. Add a compute to an existing cloud

Required inputs are the existing Kolla deployment directory/inventory/profile,
the pinned driver/image versions, and SSH address/user/identity, unique hostname,
management/tunnel interfaces, resource reservations, storage pools and approved
GPUs for the new host. Preserve the existing control plane and secrets.

An OpenStack API endpoint and administrator password alone are insufficient.
The deployment machine needs internal service configuration and trust from the
existing Kolla deployment. Do not manually copy passwords into a public host file.
Use the existing password store, inventory and generated service configuration.

Current `plan-add-compute` / `add-compute` support a **prepared and already
registered** compute, not enrollment of a bare server. Run backend `--help` and
`plan-add-compute --help` through the wrapper for exact arguments. Required paths
include controller Python/OpenRC, Kolla executable/inventory/profile/collections
and source checkout; these are explicit, not inferred from an API token.

For prepared activation the journalled sequence is:

```text
fresh guard → disable scheduling → stage per-host Nova config → scoped genconfig
            → restart empty nova_compute → acceptance → enable scheduling
```

Only an empty compute-only host may take the mutating path. Existing matching
hosts can take a read-only NOOP path. No global control-plane deploy, libvirt
restart, guest stop or reboot is part of this transaction. Apply requires a
reviewed plan and its exact SHA256. Resume the same journal; do not invent a new
transaction to bypass an ambiguous restart. Changed workloads or ownership block
the operation. Once new VMs exist, do not replay an old empty-host acceptance plan.

## 12. Swarm lifecycle, certificates and secrets

Use the OpenStackClient SPVM plugin after cloud acceptance. A cluster spec defines
resources for **each** seed/join: vCPUs, RAM, state size, GPU count/profile and
optional placement constraints. A host constraint does not bypass Nova resource
checks. Logical names receive unique actual node suffixes to avoid reusing a
departed Swarm identity.

Seed readiness precedes join creation. Release builds expose measurement APIs;
the orchestration checks measurement-record presence in the configured GitHub
registry and reports a waiting condition if absent. Host-side cryptographic
signature validation is not required here; guest checks are unchanged. Debug
build exceptions do not imply SSH is required for release orchestration. Guest
readiness/CA endpoints use ports explicitly supplied in launcher configuration.

CA private keys stay inside guests. The public CA chain and project-bound
bootstrap information are distributed through Barbican, not a shared compute-local
file. A public CA certificate is not a private key. Provider credentials, secret
references and publication permissions must retain project/publisher ownership.

**A VM restart is not recovery.** SPVM state encryption uses a new random key at
boot; previous encrypted state is unavailable after a stop/reboot. Restarting a
bootstrap creates a new Swarm/CA, not the previous cluster. Do not test Nova
stop/start, reboot or host reboot as harmless lifecycle operations. An existing
Swarm can survive loss of bootstrap once joins exist, subject to its own service
requirements. Capacity changes require explicit node replacement/rejoin semantics,
not a promise that reboot-based resize preserves state.

## 13. Acceptance checklist

- Every compute is up, mapped to the intended Nova cell and has the correct UUID.
- Nova/Placement, helper pool claims and libvirt domains reconcile without orphans.
- CPU/RAM/state budgets match administrator choices; no accidental overcommit.
- Approved GPU availability/claims match Nova PCI and actual domain assignments.
- The Nova UUID equals each SPVM libvirt domain UUID.
- Ordinary non-SPVM guests still follow the stock libvirt path.
- Guest network and API probes work both locally and across compute hosts.
- Seed readiness and CA retrieval work without guest SSH; joins use the bound CA.
- Signed-release presence is checked; missing records produce a visible wait state.
- Public UI, `/api/config` and GraphQL respond over verified TLS.
- A second cluster gets independent domains/CA and does not replace first-cluster routes.
- Owned cluster deletion removes its records/routes/claims only; neighbors survive.

Not every item above has been re-run for every new build. Preserve explicit test
receipts and list untested items rather than treating a green Nova status as full
Swarm service acceptance. S3/Harbor functional credential tests were outside the
latest UI-focused acceptance scope.

## 14. Common failures and operational limits

### Portable cluster retirement versus Nova-only deletion

Nova may briefly return a server with status `DELETED` before its GET becomes
404. The SDK's `wait_for_delete` accepts that status as completion. SPVM cluster
cleanup now waits for actual API absence with a bounded deadline and checks the
immutable identity while polling; ports/secrets are retained until absence is
confirmed. An already `DELETED` or `task_state=deleting` server is not sent another
DELETE. A timeout, permission failure or identity drift retains the cleanup journal
for explicit reconciliation. Never hard-delete SQL records or force-clear port
bindings to bypass this wait. This hardening applies to cluster cleanup; do not
infer acceptance of other node-retirement/replacement workflows from it.

The live failure-test campaign did not change project quotas, fill physical
RAM/disks, reboot guests or restart Nova/API containers. Oversized requests were
chosen above one host's remaining capacity but below its full configured budget
and below project quotas. A busy-GPU request was pinned to the occupied host
while the second host's GPU remained free, so explicit placement and PCI accounting
were tested together. No joins were created after seed scheduling failure.
The four ERROR-only namespaces were removed and repeated cleanup returned
`NOT_FOUND`; three temporary private flavors were removed, existing flavors kept.
This is not a physical disk-full, multi-disk failure or infrastructure outage test.

`openstack spvm cluster delete` performs the project-scoped Nova/Neutron/Barbican
cleanup. It is not permission to remove arbitrary public DNS or host routing.
For an already published cluster, use the separately reviewed root-admin
`deployment/spvm-cluster-retire.py` from the verified driver bundle: it journals
exact route withdrawal, delegates cloud cleanup to a non-root publisher, removes
only owned DNS RRsets, and finally removes the selected enrollment file. Shared
networks, routers, gateway DNS and neighboring clusters are retained.

Legacy retirement policy schema 1 uses the DevStack CLI path. Schema 2 adds the
required `openstack_cli` absolute path, for example
`/home/operator/spvm-client-venv/bin/openstack`; all other policy fields remain
explicit. Set `refresh` to the actual installed publisher policy (portable setup:
`/etc/spvm/ingress-policy.json`), not a guessed legacy filename. The CLI and auth
script execute only as the selected non-root `run_as` account. Keep the policy,
DNS credential file and journal root-owned/private. Never source OpenRC as root
or make `/etc/kolla` world-readable to work around traversal permissions.

With the portable `fallback=reject` listener profile, retirement verifies and
updates both managed HTTP and TLS fragments in **one host HAProxy config/reload**;
it does not require Apache or modify Kolla's API HAProxy container. Exact
before/after proofs reject changed neighboring routes. Legacy `fallback=apache`
retains the separate Apache path. Serialize publication, refresh and retirement;
retirement's internal lock is not authorization for concurrent external writers.

DNS retirement persists full-zone observations before and after each PATCH. An
unrelated RRset change is a durable review condition, even if the owned records
were removed successfully. A lost acknowledgement can be recovered without a
second PATCH only if a GET exactly matches the journalled expected result. Do not
delete a journal to suppress drift. Completed exact retries return
`ALREADY_RETIRED`. Retiring a VM permanently loses its in-memory encryption key;
retained control-plane receipts do not make the old guest state recoverable.

Compare RRsets semantically, not by the order of JSON array members: the live
PowerDNS API returned 151 unchanged NS RRsets in a different member order. The
retirement helper now canonicalizes `records` and `comments` while preserving
all data, flags, TTLs, metadata and duplicate counts. It does not drop NS/SOA
records or allow unrelated content changes. An earlier ordering-only review
was resolved using its original complete before/after observations plus a fresh
GET, retaining the failed journal and source backups; no PATCH was replayed.

The 2026-10-19 two-cluster acceptance used one shared guest network on both
compute hosts and distinct per-cluster security groups. Both public UIs passed
verified HTTPS UI/config/GraphQL checks from the operator machine. After deleting
only the disposable second cluster, its Nova/libvirt/storage/lease/TAP/OVS/
Neutron/Barbican/DNS resources were absent, its GPU was available again and the
first cluster's CA, Nova launch times and QEMU PID/start times were unchanged.
The completed root retirement repeated as `ALREADY_RETIRED`. Keep the corresponding
machine-specific acceptance receipt in the protected local evidence store.
This does not accept resource-exhaustion, service failover or clean-OS installation.

| Symptom | What to inspect |
| --- | --- |
| No instances in Skyline | Selected project and Nova records; native libvirt guests are not automatically imported |
| `NoValidHost` | Per-host CPU/RAM, project quota, traits, state child inventory, PCI alias/availability, host constraints |
| CA/readiness timeout | Actual guest boot, configured API ports, host-to-guest route, SG and delayed PKI startup; not SSH host keys |
| Host audit passes but launch fails | Helper UID/socket permissions, real compute UUID, QGS runtime-user access and launcher/image compatibility |
| Broken Docker preflight | Repair/review existing daemon; do not silently reinstall it |
| TLS failures | Correct DNS/SNI, CA trust in host and container, certificate readiness; never disable validation |
| Guest networking fails after Docker | Scoped forwarding/NAT policy, Geneve peers and MTU |
| State capacity discrepancy | Logical claims plus unrelated disk usage; do not delete claims to free space |
| Ubuntu 26 package failure | Real Ansible facts, matching collection tasks and Docker repository codename; not a noble fact override |

Keep root-only configuration backups and private transaction reports. Kolla DB,
password and config backups protect control-plane metadata, **not ephemeral guest
encryption keys**. Do not publish `/etc/kolla/passwords.yml`, OpenRC credentials,
provider configs, raw domain XML or full logs in issues. Do not use Kolla destroy,
format disks, flush firewalls, purge an old deployment or reboot hosts as implicit
installation recovery. Such operations require a separately reviewed exact scope.

For explicitly authorized DNS retirement, back up the exact selected RRsets and
the complete bounded zone observations before/after PATCH, without API headers or
credentials. Keep these evidence files root-only and exclusive, never overwrite
an earlier attempt. Preserve shared gateway records. A failed full-zone comparison
must remain a review condition even if a subsequent GET confirms removal of the
selected names; do not replay PATCH blindly or claim unrelated records were
verified unchanged without the original complete observation. The lab retirement
helper now saves both observations; its older selected-only backups cannot prove
that retrospectively.

## Complete Skyline overlay and automatic Swarm UI publication

The updated installer exposes five explicit commands for the matching Skyline
extension from the selected swarm-cloud source/bundle:

1. `stage-skyline-console`: compose login, Super image/logo, Super Protocol
   flavors and all Swarm forms/navigation from reviewed original assets; render
   the persistent Kolla nginx proxy override in the same output.
2. `build-skyline-console`: build against an already cached repository digest,
   offline, without overwriting a tag; preserve upstream runtime defaults and
   record the exact image ID plus Kolla/backend overrides.
3. `stage-skyline-runtime`: verify the wheel matches the selected bundle, clone
   an approved dependency SDK without modifying it, install the client offline,
   render matching web/capacity/publisher units and validate imports as the
   non-root service user. Bind dependency bytes, ownership, modes and symlinks.
4. `plan-skyline-runtime`: approve a fresh web runtime's exact inputs and host.
5. `install-skyline-runtime`: install and activate only those approved fresh
   services. Existing targets/loaded units/listeners/non-empty state are refused,
   never silently stopped or overwritten. Mutations are journaled under a host
   install lock; uncertain attempts retain evidence and are not blindly replayed.

Full commands, schemas and recovery instructions live in the selected driver's
`deployment/skyline/README.md`. This keeps UI implementation/documentation with
the driver, rather than duplicating its assets in sp-vm-tools. These commands
require the matching updated sources; the older published 0.2.22 archive does not
gain them merely because a local checkout has changed. Adopt the complete image
and custom nginx into an approved Kolla workspace; staging/building alone does
not reconfigure the console. Fresh service installation is not an upgrade path
for an occupied lab or proof of clean-OS acceptance.

The console build's `backend-image-fragment.json` now includes the image/tag ID
and paired `kolla.skyline_console_context` / `skyline_console_context_sha256`.
Include these in deployment.yaml before render/`init-kolla`. Initialization
validates the context and adopts its nginx override into the new workspace's
actual `node_custom_config` tree, including it in file checksums. There is no
manual proxy-template copy and no mutation of a previously sealed workspace.

Use `repository@sha256:...`, not Docker's bare image configuration ID, as the
Dockerfile base. BuildKit can interpret `sha256:<image-ID>` as a repository name
and attempt a registry lookup even with `--pull=false`; the renderer rejects it.
When cloning an existing private venv as root, preserve non-root read/traversal
through the dedicated primary group. Never make config/secrets world-readable to
work around a service permission error.

Optional automatic publication uses a separate root-owned timer worker, not
root/Docker privileges on the web API. The administrator delegates one project
and DNS suffix. It exports completed creations as a non-root publisher, validates
exact paths/domains, and reuses the existing authoritative Nova/Neutron/Barbican
enrollment, HTTP Host/TLS SNI reconciliers and public UI checker. Guest Swarm
services remain responsible for cloud DNS/certificates. The shared template
`gateway_hostname` must already resolve exclusively to the approved public
ingress IPv4; it is checked, not overwritten automatically. Readable sanitized
reports live outside the private `/var/lib/spvm` tree. Never loosen that tree.

With publication enabled, the UI operation advances from instance readiness
through `WAITING_PUBLICATION`, `WAITING_GATEWAY`, `VERIFYING_PUBLIC_UI` and finally
`UI_READY` with a URL. Three Nova ACTIVE records alone are insufficient. Failure
retains guests and requires publication review; no bootstrap restart or cluster
recreation is attempted. The default without delegation remains explicit
`OPERATOR_REQUIRED`. Inspect old completed operations before enabling a worker:
delegation applies to every eligible creation in its configured state directory.

On 2026-10-21, the existing two-host lab passed a separate CPU-only three-node
release-cloud creation through the authenticated Skyline API and automatic host
publication. Its public UI/config/GraphQL checks and those of the existing cloud
passed, without restarting existing guests. Complete console build and matching
runtime staging were also exercised against real pinned inputs. Fresh-only
service apply is transaction-tested with mocked systemd, not accepted on a reset
host; clean-OS deployment, HA and real-browser acceptance remain separate.

### Common workflow: optional Skyline completion and repeat verification

#### Automatic Skyline image and runtime preparation

Replace individual `stage-skyline-console`, `build-skyline-console` and
`stage-skyline-runtime` calls with one approved preparation transaction. Run it
as root on the controller after Docker/SDK setup and before initializing a new
Kolla workspace. The command never bootstraps CC hardware, installs packages,
reconfigures a running cloud or launches a guest. Its private request contains
exactly the following fields:

```json
{
  "schema_version": 1,
  "deployment_config": "/etc/spvm-install/deployment.yaml",
  "console": {
    "static_dir": "/var/lib/spvm-inputs/upstream-static",
    "nginx_template": "/var/lib/spvm-inputs/nginx.conf.j2",
    "nginx_sha256": "<approved Kolla template SHA256>",
    "base_image": "quay.io/openstack.kolla/skyline-console@sha256:<approved digest>",
    "tag": "spvm/skyline-console:deployment-001"
  },
  "runtime": {
    "sdk_venv": "/opt/spvm/sdk-venv",
    "web_user": "spvm-web",
    "web_config": "/etc/spvm-install/web-config.json",
    "wheel": "/var/lib/spvm-inputs/driver/wheels/nova_spvm-<version>-py3-none-any.whl",
    "wheel_sha256": "<approved wheel SHA256>",
    "publication_policy": "/etc/spvm-install/web-publication-policy.json"
  }
}
```

The exact Linux amd64 upstream repository digest must already be cached. Supply
its original unpatched static files, the pinned Kolla nginx template, a matching
wheel from the selected release's wheels/ directory and a provisioned SDK venv.
Even a same-version wheel is rejected if its code differs from the selected
driver sources. Use `null` for publication_policy only if web-config disables
publication. The non-root service account, trusted API CA, provider/enrollment
policy and web config must already exist; they are not guessed defaults.

```sh
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  plan-skyline-preparation --request /etc/spvm-install/skyline-preparation.json \
  --output /var/lib/spvm-install/skyline-001
# Review preparation-plan.json; approve its exact plan_sha256 within 15 minutes:
sudo ./scripts/install-openstack.sh --driver-dir /srv/swarm-cloud -- \
  prepare-skyline --request /etc/spvm-install/skyline-preparation.json \
  --output /var/lib/spvm-install/skyline-001 \
  --plan-sha256 '<approved SHA256>' --allow-changes
```

The parent /var/lib/spvm-install must exist, be root-owned/protected from non-root
writes, and permit traversal by the web user. The transaction is private. Its
sibling skyline-001-runtime is created with service-group read/traversal by the
runtime stager; neither directory may already exist. Secrets are not made
world-readable. Non-root import/config checks use isolated state, not live state.

Preparation emits deployment.yaml with pinned image/context merged into the
original settings, skyline.json with runtime path/receipt SHA, and preparation.json
with provenance. Use the generated deployment.yaml for render/init-kolla and
point the common fresh workflow's skyline field to the generated skyline.json.
Rendering/workspace initialization and SPVM resource/network/publication
enrollment retain their separate boundaries; this replaces manual image/runtime
assembly, not hardware or all host preparation.

Repeat the approved prepare-skyline command without --allow-changes to verify
outputs, overlay, runtime files/venv and image ID. It returns
SKYLINE_PREPARATION_VERIFIED_NOOP, with no rebuild or reinstall. Changed inputs,
source drift and occupied tags are refused. Interrupted preparations retain
NEEDS_REVIEW and their private logs: no blind resume, overwrite or evidence
deletion. Review before explicitly choosing a new transaction/tag.

On 2026-10-21 this combined path passed real offline build/runtime staging on
.247 plus two verified repeats, with the protected host/container snapshot
unchanged. Evidence: /var/lib/spvm-skyline-preparation-20261021-01/accepted.json.
The new image was not activated over the running console; this is not clean-OS
or authenticated visual-browser acceptance.

#### Common install and existing-state verification

Add an optional `skyline` key to the JSON request passed to `plan-openstack` /
`apply-openstack`. Its value is a canonical absolute path to a private JSON
specification. Omitting it retains the older scoped OpenStack-only verification;
that older result must not be described as GUI acceptance. `add-compute` rejects
this option: controller GUI changes are not compute enrollment.

For `mode: fresh`, the Skyline specification contains exactly:

```json
{
  "runtime_dir": "/var/lib/spvm-install/skyline-runtime",
  "runtime_sha256": "<SHA256 of runtime-stage.json>"
}
```

Prepare the complete console image/context, merge the build's image fragment
into deployment.yaml and initialize its new immutable Kolla workspace first, as
described above. Stage the matching wheel/runtime using `stage-skyline-runtime`.
The common plan validates the staged runtime, vacant systemd targets, non-root
account, empty private state and publication prerequisites. Apply rechecks them,
deploys Kolla, then installs the staged runtime under its own exclusive lock and
write-ahead journal. The parent approval binds the specification, receipt and
installer/backend sources, including Skyline helpers and assets. The delegated
runtime approval is generated immediately before installation so a long Kolla
deployment does not consume its 15-minute TTL.

This joins the existing phases; it is not automatic input discovery or a new
upgrade/recovery mechanism. The publication enrollment/helpers/policy must already
be prepared when publication is enabled. Missing prerequisites block planning;
the workflow does not silently omit them. It still reports
`OPENSTACK_INSTALLED_NOT_SPVM_ENROLLED`, with a separate Skyline runtime result:
installing a web API does not establish compute/resource/network enrollment.
Never label this as verified end-to-end clean-OS installation. After an ambiguous
failure, inspect both parent and runtime journals; do not replay Kolla or delete
the journals to bypass occupied-target protection.

For `mode: existing`, the specification contains exactly these fields:

```json
{
  "origin": "https://<trusted-controller-VIP>:9999",
  "manifest": "/var/lib/spvm-install/overlay/console-context.json",
  "ca_file": "/etc/ssl/certs/ca-certificates.crt",
  "image_id": "sha256:<approved complete console image ID>",
  "files_sha256": {
    "/var/lib/spvm-install/overlay/console-context.json": "<approved manifest SHA256>",
    "/etc/kolla/skyline-console/nginx.conf": "<approved live nginx config SHA256>",
    "/etc/systemd/system/spvm-web-nodes.service": "<approved unit SHA256>"
  },
  "units": ["spvm-web-nodes.service", "spvm-web-capacity.timer", "spvm-web-publish.timer"]
}
```

These are controller paths. Pin all relevant installed units, helpers, API config
and publication policy in `files_sha256`; the manifest must always be pinned.
Use deployment receipts/reviewed baselines, not an automatically trusted checksum
of whatever happens to be live. No file contents or credentials are reported.
The configured controller is reached through the normal strict SSH transport.
Verification requires the exact healthy console image, active/enabled runtime
units, all pinned files, nine HTTPS identity/gzip/logo probes, and HTTP 401 from
both the proxied and loopback cluster API. Redirects, stale gzip and a successful
HTML fallback are failures. Container identities/start times, QEMU processes and
service PIDs must remain unchanged during each check. Completed existing-mode
apply re-runs these checks on every invocation instead of replaying installation.
This is static/service acceptance, not proof of authenticated browser interaction
or every publication operation. Keep the separate authenticated API/public UI
checks in the acceptance procedure.

Lab acceptance on 2026-10-21 used a private copy of the updated coordinator and
read-only backend helpers against the existing .247 controller and both .245 /
.247 computes. One plan and two applies completed with `OPENSTACK_NOOP_COMPLETE`
and `SKYLINE_INSTALLATION_VERIFIED`, nine static browser probes per check and an
identical console/container/QEMU snapshot across applies. The approved complete
console image was
`sha256:7b7c0b11df6a4f9304719d2f4484977c78e919aed0414156d7490d4b56847ddc`.
Private receipts are under
`/home/petr/kolla-spvm/clean-redeploy-20261018-01/installer-gui-workflow-20261021-01`.
This does not upgrade the previously published archive, deploy a reset host or
assert authenticated visual-browser acceptance.
