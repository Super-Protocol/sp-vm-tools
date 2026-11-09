# OpenStack management implementation

Public entry point: [`../spvm-openstack.sh`](../spvm-openstack.sh).
The Python modules live together here because sibling imports and exact source
hashes are part of the coordinated kit. Hardware bootstrap and the VM launcher
remain separate. No compatibility copies or old script aliases are maintained.

Operator instructions:

- [Deployment and prerequisites](../../docs/OPENSTACK_DEPLOYMENT.md)
- [Local environment preparation](../../docs/OPENSTACK_ENVIRONMENT.md)
- [Fresh installation](../../docs/OPENSTACK_SINGLE_INSTALL.md)
- [Controller-owned management](../../docs/OPENSTACK_CLOUD_MANAGEMENT.md)
- [Compute addition](../../docs/OPENSTACK_COMPUTE_ADDITION.md)
- [Compute retirement](../../docs/OPENSTACK_COMPUTE_REMOVAL.md)
- [Host resources](../../docs/OPENSTACK_HOST_RESOURCES.md)
- [Prebuilt images](../../docs/OPENSTACK_PREBUILT_IMAGES.md)
- [Progress and long operations](../../docs/OPENSTACK_PROGRESS.md)

## Workflow ownership

`prepare-environment.sh` / `prepare_environment.py` check system prerequisites
without installing packages, then prepare isolated Python/Kolla tooling and
collections. They do not contact or alter target hosts.

`openstack_install_entry.py`, `openstack_install.py` and
`openstack_install_phases.py` implement fresh install: environment selection,
all-host scan, approval, fixed phase order and durable journals.
`openstack_install_resume.py` selects only supported reviewed continuation
boundaries, with private attempt history and no arbitrary phase replay.
Completed installations return their recorded result, not a new health audit.

`openstack_add_entry.py`, `openstack_add_batch.py` and
`openstack_add_manager.py` implement host-only sequential addition using a
controller-owned cloud generation. New and returning hosts share the command.
`openstack_remove_manager.py` retires reachable empty compute-only hosts.
Both commands retain all-target checks, ownership/identity fencing, cloud
generation claims and stop-on-first-failure behavior. Neither requires original
local installation history or distributes operator keys/agents to servers.

`openstack_prebuilt.py` verifies kit manifests, immutable registry identities,
source compatibility and the generated host-worker entrypoint. Target hosts
retrieve exact prebuilt digests; the manager requires no Docker binary/socket.
Console/nginx metadata is sealed into the workspace. There are no local image
builders, Docker archive transfer or compiled-JavaScript modifiers.

## Host preparation and resources

Container-engine preparation preserves a working Docker installation, installs
only under its approved empty-host conditions and refuses unknown/broken state.
Network modules prepare owned management/NAT/Geneve rules and routes on existing
administrator-provided interfaces. They do not create a VPN, change arbitrary
firewalls or grant public access to internal services. Hostname preparation
preserves unrelated mappings and uses the audited management identities.

Kolla operations use the kit's exact source and privately prepared collections,
with prechecks/deploy/post-deploy and target-only compute scope where applicable.
Initial runtime enrollment and compute activation are separately journaled
internal steps; neither permits automatic guest recovery or hardware bootstrap.

Installation and addition leave computes UNCONFIGURED/disabled. Budget, pool,
GPU and lifecycle modules expose explicit administrator operations through the
protected typed control channel. All-project empty-host checks apply to technical
resource edits, including increases. Configure resources first, then separately
Enable. Disable never stops existing VMs. Runtime resource state is not an
installation baseline; project quotas remain independent.

## Platform and runtime boundaries

Platform completion prepares cloud access, shared networking, restricted runtime
management and an empty GUI/catalog. Images, flavors, provider templates and
domain-scoped publication/retirement delegation are subsequent administrator
operations. Source-bound SDK and protected auth are distinct from the minimal
installer environment. Service identities never inherit operator SSH authority.

Probe routes and permitted source addresses must agree with guest security
groups on both controller and peer computes. Do not widen groups or remove guards
to conceal missing routing. An installed UI does not prove guest readiness or
public ingress availability. Runtime staging refuses occupied targets; it is
not an implicit upgrade or takeover.

## Safety and diagnostics

Each mutating step records intent before execution. Unknown failures, lost
acknowledgements, changed sources/identities or partial writes require review;
there is no automatic rollback, retry, scheduling re-enable or migration.
Private journals and backups must retain their permissions and remain outside
Git. Progress is sanitized stderr presentation only; JSON/stdout, timeouts,
admission, approval and transaction ordering remain unchanged.

Documentation covers supported procedures and limitations, not internal test
reports or stand history. Do not commit run transcripts, test counts, lab
addresses, dated checkpoints or private operational evidence. Component pins
belong in the selected kit's generated metadata and maintained source locks.
