# Platform installation and subsequent administration

## Current contract: infrastructure first

Use [`spvm-openstack.sh install`](OPENSTACK_SINGLE_INSTALL.md) for a fresh cloud.
The integrated coordinator owns phase ordering, all-host checks and approval;
administrators do not need to invoke each internal preparation command manually.
Hardware bootstrap, existing inter-host connectivity and a
[prepared management environment](OPENSTACK_ENVIRONMENT.md) are prerequisites.

Deployment YAML contains infrastructure only. It must not contain guest builds,
provider credentials, Swarm domains, CPU/RAM budgets, state pools or GPU inventory.
The installer prepares cloud access, shared Neutron networking, helpers,
restricted management and the source-built GUI. It does not create an image,
flavor, provider template, guest or Swarm cluster.

`installation-kit.json` supplies exact component revisions and immutable image
references. Use the unchanged matching installer and Kolla environment. Targets
retrieve the prebuilt images by digest; no local Docker build or JavaScript
patching is involved. See [kit delivery](OPENSTACK_PREBUILT_IMAGES.md).

Fresh installation is not an upgrade, reset or occupied-host repair workflow.
Ambiguous partial operations require review. Original private journals and
ownership/provenance checks must not be reset or edited to bypass refusal.

## Configure and enable compute hosts

Installation and add-compute finish with healthy **UNCONFIGURED/disabled** hosts.
Detected hardware is informational: no CPU/RAM budgets, workload pools or GPUs
are allocated. The technical cache `/var/lib/spvm/registered-releases` is separate
from workload state and still needs sufficient filesystem space.

Through the GUI or typed CLI/API, assign explicit CPU/RAM limits, at least one
enabled state pool and optional GPU devices. Then separately Enable the host.
Changes require an empty host across all projects; Disable only blocks new
admissions and does not stop running guests. See
[manual resources](OPENSTACK_HOST_RESOURCES.md) and [GPU profiles](OPENSTACK_GPU_PROFILES.md).
Current resource state is protected host data, not an installation baseline.

## Register images and create workload inputs

Administrators register approved images, create private resource flavors and
import named provider templates through the matching CLI/UI. Registration is
explicitly delegated; the GUI alone does not authorize a build or prove its
measurement/signature. Static empty allowlists permit no images.

Every launch supplies an explicitly authorized build. The helper independently
checks admission and uses that build's isolated registered cache; there is no
default release or fallback. Debug requires independent host and registration
opt-ins. Non-CC compute rejects release images, including direct Nova requests.
See [debug policy](OPENSTACK_DEBUG_BUILDS.md) and [non-CC hosts](OPENSTACK_NON_CC.md).

Provider configuration contains secrets and belongs in approved protected
storage/Barbican, not Git or installation YAML. Template upload does not grant
root publication/retirement authority. Administrators must separately configure
domain-scoped DNS/ingress and retirement delegation with restricted service
credentials. See [service delegation](OPENSTACK_SERVICE_DELEGATION.md).

## Readiness, networking and failures

The base project enrollment selects a shared Neutron network; each cluster still
needs its scoped security group and approved readiness-probe sources. All
compute hosts need the required connectivity, including routes back from guests.
Do not open probe/API ports unrestricted to the Internet to fix a routing issue.

Nova ACTIVE, SPVM readiness and public HTTPS UI reachability are separate.
Release readiness retains signature-presence and original-CA checks. Untrusted
debug does not fabricate measurement or attestation. An installed ingress
scaffold or successful template upload is not proof of public DNS/TLS access.

No automatic guest recovery, bootstrap restart or CA replacement is performed.
SPVM state uses ephemeral per-boot keys; preserving an encrypted disk does not
make a stopped guest resumable. Inspect private operation results by their
original request IDs after failures, without replaying ambiguous mutations.
