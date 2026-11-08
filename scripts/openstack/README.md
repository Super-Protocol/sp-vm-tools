# OpenStack installer implementation

Local operator tooling is prepared by `prepare-environment.sh` and its stdlib-only
`prepare_environment.py` module. They check (never install) system prerequisites,
create separate installer/Kolla venvs and local collections, and do not contact
deployment hosts. See `../../docs/OPENSTACK_ENVIRONMENT.md` for commands, repeat
semantics and the distinction between environment readiness and deployment.

Public entry point: `../spvm-openstack.sh`. All Python installer modules live
here; hardware bootstrap and VM launcher scripts remain separate in `scripts/`.
Keep these modules together: they use sibling imports and source-hash bindings.
No compatibility copies are maintained in the parent directory.

`spvm-openstack.sh add-compute --environment ... --config ...
--controller USER@IP --output ...` accepts a host-only YAML and checks all selected
computes before adding them sequentially. Optional `--host` selects one entry.
Cloud/kit settings come from a completed controller-owned generation, not this
YAML or local installation history.
The batch stops on its first failure without rolling back earlier successes.
Each target can be new or previously retired. Classification is automatic;
owned leftovers are archived, not migrated. A registered exact-ready host is a
read-only NOOP. Use `--check` for inspection and `--yes` for explicit apply.
See `../../docs/OPENSTACK_COMPUTE_ADDITION.md` for version pins, workload refusal,
credential boundaries and the verified acceptance scope.

`spvm-openstack.sh remove-compute --environment ... --controller USER@IP
--host ... --output ...` retires a reachable empty compute-only host from any
matching prepared management machine. Use a new output for each check/apply.
Controller state is authoritative; the local Kolla working copy contains secrets
and must remain private. No operator keys are distributed or listener installed.
See `../../docs/OPENSTACK_CLOUD_MANAGEMENT.md` and
`../../docs/OPENSTACK_COMPUTE_REMOVAL.md` for the complete safety/retained-state
contract. Do not use fresh `install` or Nova service-disable as retirement.

`spvm-openstack.sh install` is the integrated fresh-install entry point.
`openstack_install_entry.py` selects the explicit READY Python environment;
`openstack_install.py` owns the durable transaction/approval; fixed phases live
in `openstack_install_phases.py`, with artifact and controller-completion helpers.
There are no executable YAML recipes or persistent SSH execution endpoints.
The current test profile requires explicit `--use-test-images`; `--yes` does
not imply acknowledgement. `openstack_install_continue.py` provides only the
reviewed test-image prechecks refusal continuation into a new transaction,
never a generic failed-deploy retry or replay of host preparation.
`openstack_install_platform_continue.py` separately reviews only the observed
OVN router-default refusal after successful Kolla. It keeps the driver wheel,
SDK, credentials and prepared resources, and runs only platform completion in
new one-shot controller/management journals. Neither mode is generic recovery.
`openstack_install_resume.py` selects these reviewed boundaries automatically
when the administrator repeats the ordinary command with the same `--output`.
It retains private attempt history within that directory; no public phase or
continuation flag is needed. Check-only/approval-only runs can be scanned again;
unknown partial mutations are refused, not replayed. Completed installations
are not installed again. No workspace/configuration migration is performed.
`openstack_install_compute_continue.py` additionally reconciles only a first
compute stopped/quarantined at runtime enrollment with unchanged bootstrap
state. `openstack_platform_checkpoint.py` preserves completed platform evidence
without replaying auth/network/Barbican writes. Native claims precede activation;
unknown/partially applied runtime or start actions still fail closed.
See [one-command fresh installation](../../docs/OPENSTACK_SINGLE_INSTALL.md) for
the supported profile, management Docker prerequisite, error/retry rules and
the pending live acceptance boundary.

GPU preparation and explicit empty-host GPU configuration are described in
[GPU profiles](../../docs/OPENSTACK_GPU_PROFILES.md). Control-plane restarts are
part of the explicit technical-settings transaction, not display-name updates.

`openstack_workflow.py` provides the common `plan-openstack` / `apply-openstack`
entry points: explicit fresh installation, existing scoped NOOP, or prepared
add-compute NOOP/initial enrollment and explicit new-compute scoped Kolla deployment.
Source/input-bound approval, durable parent
journal, explicit mutation opt-in and child recovery guards are retained.
An optional source-bound new-compute preparation recipe connects compute-only
network, Docker, helper/launcher and driver-image phases before registration.
It includes insert-only owned peer allowlist expansion, audited management
hostnames and target-only dependencies/public API trust. See guide §11.2 for
transport, filesystem and acceptance limits.
It never guesses a scenario or silently redeploys a working cloud. See guide
§10.28 for schemas, completion semantics and the separate preparation/enrollment
phases; bare-host provisioning and general upgrades are not implied.

Operator guide: [`../../docs/OPENSTACK_DEPLOYMENT.md`](../../docs/OPENSTACK_DEPLOYMENT.md).

`openstack_skyline.py` provides only `stage-skyline-runtime`: source-verified
client wheel, isolated SDK and non-root web/capacity/publisher unit rendering,
without live unit changes. The frontend and nginx artifacts come from the
coordinated CI kit; no local image build or compiled JavaScript modifier remains.
Runtime installation and root-delegated publication retain their reviewed
permissions, provenance and WAL boundaries.

`openstack_compute_identity.py` adds native Kolla/Ansible helper-request UUID
generation (`stage-kolla-identities`), using audited kernel nodenames, not aliases.
`openstack_hostnames.py` adds the owned `plan-hostnames` / `prepare-hostnames`
phase. Fresh `plan-openstack` / `apply-openstack` may include a `platform` file
to drive new-cloud enrollment through verified Skyline Swarm management, with an
optional hostname/Geneve/management/NAT preparation recipe. No guest is created.
See `docs/OPENSTACK_PLATFORM_WORKFLOW.md` for the exact prerequisite boundary and
pending combined live acceptance. Existing/adoption modes are not widened.

`openstack_skyline_runtime.py` adds `plan-skyline-runtime` /
`install-skyline-runtime` for fresh web/capacity/publisher services. It requires
root-protected staged inputs, hashes the copied dependency environment, refuses
occupied targets/loaded units/listeners/non-empty state, binds approval to source
and machine, and journals before mutations under a global install lock. It does
not replace an occupied API or reconfigure Kolla/Nova. On failure it stops only
the new units from its own attempt and retains files/journal for operator review.
Full fresh-OS installation acceptance remains separate. The selected bundle's
Skyline README provides commands, prerequisites and explicit acceptance boundaries.

`init-kolla` adopts an approved complete Skyline proxy context when deployment
config supplies paired `kolla.skyline_console_context` and
`skyline_console_context_sha256` with the pinned console image. The initializer
copies the template into the new workspace's actual `node_custom_config` and
binds it in file checksums, rather than leaving a disconnected override nearby.
Existing sealed workspaces are not edited.

`openstack_kolla_existing.py` implements `verify-kolla-existing` for an occupied
cloud: bound config/source checks, host/cloud accounting, guarded compute-only
NOOPs and before/after runtime continuity. Exactly one verify-only request per
compute and explicit private SSH/auth inputs are required. No repair, restart,
fresh bootstrap or general deploy/reconfigure is performed. Success is explicitly
scoped; full control-plane config/functionality and installer idempotency remain
outside its acceptance. See guide §10.27. Old backend `verify-existing` remains
DevStack-only.

`openstack_container_engine.py` fingerprints the local Docker daemon, selected
host services and sanitized container lifecycle state. PRESERVE never runs
Ansible; INSTALL requires an empty prepared host and records its attempt before
the bounded role invocation. Timeout/interruption leaves remote state unconfirmed,
never an automatic retry. See guide §5.1–5.2 for live PRESERVE evidence and the
still-untested Ubuntu26 missing-Docker path.

`openstack_network_prepare.py` provides fresh root-local management/NAT plan/apply;
`openstack_network_runtime.py` is its standalone installed systemd runtime.
Geneve protection remains a separate command/module. Neither workflow deploys
OpenStack or launches/restarts a guest.

Guest probe routes must select the host's management address as their preferred
source, including on the NAT/controller host. The transit veth address is not an
approved cluster API client in Neutron security groups. Using it makes healthy
measurement/PKI endpoints appear unreachable. Do not widen ingress to compensate;
keep the management source in the persistent host-network policy.
Forwarded peer-compute probes must also SNAT to that approved management
address, never the transit address. Otherwise local controller probes succeed
while remote compute observers still time out. Conntrack restores the peer
destination for replies; the source/guest/port allowlist remains unchanged.

Fresh-host preflight refuses active/enabled native `openvswitch-switch`,
`ovsdb-server` or `ovs-vswitchd` services and existing `/run/openvswitch` contents.
Kolla's OVS containers share this runtime namespace: an old native database can
leave a PID/socket that makes the new container abort. Review ownership, bridges,
processes and any workloads before stopping a native service. Never disable it
automatically or delete its sockets while a process is still using them. During
an explicitly approved cleared-cloud reset, stop/disable only attributed empty
native OVS services, then quarantine stale runtime on the same filesystem.
This preparation does not require touching libvirt/QEMU or the public bond.

`openstack_kolla_workflow.py` provides the explicitly approved fresh-cloud
`prechecks → deploy → post-deploy` transaction. It never bootstraps hardware,
installs Docker implicitly, upgrades an existing cloud, or launches guests.
`openstack_spvm_enrollment.py` stages portable runtime/project/shared-network
and flavor intent; installing this intent and validating its OpenStack IDs is
still a separate, unfinished integration step.

`openstack_spvm_resources.py` exposes approved project-scoped flavor/Glance marker
plan/apply and an opt-in disposable private-ACL Barbican smoke test. The portable
API logic remains in spvm-openstack's `deployment/spvm_resources.py`. Neither command
creates networks, changes runtime enrollment, restarts services or launches VMs.

`openstack_spvm_network.py` adds explicit `plan-spvm-network` /
`apply-spvm-network` for the single shared guest network, transit subnet and fixed
router address. API implementation lives in spvm-openstack `deployment/spvm_network.py`.
It never edits host routes/firewall, widens cluster security groups or replaces
conflicting Neutron objects. API readiness is not host-routing acceptance.

`openstack_runtime_enrollment.py` stages per-compute requests, optionally checks
the pinned image schema in an isolated validator, and plans/installs only the
initial deny-all-to-enrolled runtime transition. Installation requires an already
stopped empty compute and explicit operator quarantine acknowledgement. It never
stops/starts Nova or VMs. Backend host logic is in spvm-openstack
`deployment/runtime_enrollment.py`; activation remains a separate operation.

`openstack_ingress.py` stages/checks/plans/installs an empty initial host HAProxy
scaffold plus reviewed publication helpers, using spvm-openstack `ingress_setup.py`.
The host proxy must already be inactive/disabled with a reviewed route-free base;
no start/reload, DNS write, cluster enrollment or Kolla proxy mutation occurs.
Existing generic TLS/HTTP renderers and root-authorized exporters remain reused.

The separate `plan-ingress-activation` / `activate-ingress` approval starts and
enables only the exact empty installed host scaffold, verifies listeners and
negative Host/SNI probes, and refuses existing publications. This is distinct
from installation and from Kolla's container HAProxy.

`openstack_publication.py` provides `plan-spvm-publication` / `publish-spvm-ui`:
fixed root-protected admission/readiness helpers, an exact root permit,
hash-bound approval, bounded gateway wait, WAL and no blind replay. It may
reconcile/reload authorized host routes, but never creates/reboots guests,
exports CA keys or edits DNS. English guide §10.23–10.24 describes recovery and
the remaining fresh-compute activation boundary.

`openstack_compute_activation.py` now coordinates the prepared empty-host
quarantine/stop/enroll/start/accept/enable path using spvm-openstack
`initial_compute.py` and the durable compute transaction engine. Fresh requests
and explicit preserve-enrollment mode are separate. No image/Kolla/hardware/guest
changes are implicit. See guide §10.25 for API/host guards and ambiguous-action
recovery; preserve-mode success is not fresh enrollment-write acceptance.

The opt-in `tests/accept_openstack_publication.py` now saves private before/final
receipts for new apply runs and exposes `--check-report` for read-only validation
after a lost SSH acknowledgement. It never replays publication automatically;
legacy reports explicitly say when the original baseline is unavailable.
See guide §10.24 for the scope of that verification and transport limitations.

Prepared all-in-one activation is supported without broadening the stop scope:
only `nova_compute` may change lifecycle. Every other Docker container and host
HAProxy lifecycle is fingerprinted before/after; role-required API/network
containers must be running/healthy. The older `add-compute` genconfig workflow
retains its separate compute-only restriction.

`openstack_kolla_compute_scope.py` owns `plan-kolla-compute-container` /
`reconcile-kolla-compute-container`: pinned native `nova-cell-deploy`, one literal
compute host, untouched API service definitions and an isolated guarded module.
Verify mode refuses every mutation, even an unexpected handler. Replacement
requires an empty quarantined registered compute and remains quarantined until
separate runtime/Placement/network acceptance. See guide §10.26 for requests,
approval/recovery and the precise live-NOOP versus fixture-replacement boundary.

Current images are supplied by a coordinated CI kit. `openstack_prebuilt.py`
verifies manifests and source compatibility without manager Docker, then target
hosts pull exact digests and confirm config IDs. CI-owned console/nginx metadata
is sealed into the workspace and controller kit. Old local builders, archive
transport and console build-context commands have been removed. See
[prebuilt images](../../docs/OPENSTACK_PREBUILT_IMAGES.md).

`openstack_host_budgets.py` processes explicit private administrator requests on
empty hosts only, including for increases. It checks all projects, Placement,
PCI and host-local state before and after scheduling quarantine, persists native
reserves and state ceilings, and leaves ambiguous failures gated for manual
review. `openstack_budget_service.py` stages and explicitly activates its optional
fresh-only service/timer plus web delegation; source/config drift and unsupported
web backends are refused. Neither path performs VM recovery or guest lifecycle.
See English deployment guide §7.1 for one-shot and managed activation commands.
