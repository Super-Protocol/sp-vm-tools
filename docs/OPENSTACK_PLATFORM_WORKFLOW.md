# Fresh platform workflow: prepared hosts to Skyline Swarm management

This workflow extends `plan-openstack` / `apply-openstack` with a `platform`
specification. It runs existing owned phases in order and resolves project,
user, network and security-group IDs from the **new** cloud. It never launches
a Swarm, resumes encrypted guest disks, signs a release or reboots hardware.

Live checkpoint (2026-10-21): after another scoped cleanup, the combined path
completed owned hostname/network preparation, native Kolla prechecks/deploy/
post-deploy, shared Neutron topology, profiles, Barbican ACL probe and runtime
request staging. It then refused operator accounting because the auth directory
was not traversable by the operator. The source permission fixes described below
passed regression tests. Reviewed, separate child transactions completed both
compute enrollments, quotas, ingress, template import and the web runtime.

Final strict-TLS HTTP/API acceptance passed: two enabled/up computes, one private
provider template, one image, two profiles, fresh native capacity, nine static
probes, authenticated login/domain/catalog APIs and no guests. The separate web
account cannot read the operator admin OpenRC. This is **not** an uninterrupted
successful run of the common workflow: its failed journals remain `NEEDS_REVIEW`.
Completion required scoped recovery and a separately hash-recorded development
overlay for the publication worker; the published driver bundle was not edited.
It is also not clean-OS, cold-cache, reboot, headed-browser or Swarm-launch proof.

The selected driver must advertise `separate-web-ingress-identities`. The existing
0.2.22 published artifact used in the lab lacks that capability: its worker
required the web exporter and ingress publisher to share a Unix identity. Do not
grant admin OpenRC access to the web service to satisfy that obsolete check.
The new worker validates both root-configured accounts as non-root independently.
Build/select an updated bundle before the next fresh deployment; the new planner
refuses the old artifact before host preparation. Never edit a verified manifest
to bypass this gate. An uninterrupted cleared-host run with those updated artifacts
is still required.

## Prerequisites and current boundary

Hardware bootstrap remains separate. Prepare BIOS/TDX/SEV, VFIO, libvirt/QEMU and
the measured release cache first. Provide a trusted root operator environment on
the selected single controller, strict SSH known-hosts, noninteractive sudo on
the hosts, a non-root operator account and the reviewed driver bundle/SDK environment.
Set `SPVM_INSTALLER_PYTHON` to a Python containing the matching driver client,
OpenStackSDK, keystoneauth and YAML dependencies. No system-wide pip is used.

The existing artifact preparation stages still supply a clean pinned Kolla
source/collections, generated fresh password/TLS workspace, built custom Nova
and Skyline images, compute image delivery, installed bootstrap-deny-all helpers,
and the bound compute workspace. The new coordinator does **not** download these
artifacts, install a missing Docker engine, create a physical management network,
or provision WireGuard peers/keys. Those are explicit earlier preparation stages,
not an implied OS-to-cloud one-liner. See the main deployment guide.

For host network preparation, the approved management transport and its addresses
must already be UP, dedicated and non-default. Existing WireGuard AllowedIPs and
routes must cover the declared guest probes/replies. The optional recipe runs
owned Geneve guards, management VIP/routes and isolated NAT; it does not attach
the public bond to OVS or invent a new per-Swarm network.

Initial platform completion currently enrolls the operator's administrator project
and identity. A multi-project/user onboarding wizard, controller HA and live
adoption/upgrade remain separate work. Run on empty hosts only.

## Generate native compute identities before installing helpers

Do not invent a random compute UUID. Use a recent `audit.json` from the deployment
preflight and the helper request mapping, whose storage/GPU/release settings were
already reviewed:

```bash
scripts/install-openstack.sh --driver-dir /srv/verified-driver-bundle -- \
  stage-kolla-identities --config /srv/operator/deployment.yaml \
  --audit /srv/operator/preflight/audit.json \
  --requests /srv/operator/helper-requests.json \
  --output /srv/operator/native-identities
```

The command writes a new `helper-requests.json` mapping and per-host requests;
it changes no host. It uses the audited **kernel nodename**, not the inventory
alias, with the pinned Kolla/Ansible `to_uuid` namespace. Feed those generated
requests to helper installation and `stage-kolla-computes`. Never rewrite a
sealed workspace or a live Nova UUID. The platform planner checks the identities
again against fresh host audits and refuses mismatches before deployment.
Existing-cloud adoption must use the already registered Nova identity instead.

## Prepare input files

Copy and edit these English examples; placeholder paths/IPs are not lab defaults:

- `openstack-platform.example.json`: operator/web/ingress settings and file paths.
- `openstack-platform-network.example.json`: shared Neutron topology, deliberately
  **without** `project_id`. The new API supplies it after Kolla deployment.
- `openstack-host-preparation.example.json`: kernel-hostname address mappings,
  plus the optional owned Geneve/management/NAT request paths from the main guide.
- `openstack-resource-profiles.example.json`: per-node resource/flavor profiles.

Copy the initial inactive HAProxy config into an immutable operator file, verify
its checksum against the host baseline and select that copy as
`base_haproxy_config`. Do not point this field at `/etc/haproxy/haproxy.cfg`,
because ingress installation intentionally changes that live file.

The `operator_user` must match the controller SSH user. Its private auth file is
not readable by the separate `web_user`; privileged ingress delegation uses the
operator account, while the web API uses only the logged-in user's token.
If the selected web account is absent, the reviewed plan declares creation of a
dedicated system account/group with no login shell/home. An explicitly selected
existing distinct account may be reused; shared operator UID/group access is
refused. No existing user's groups, home or shell are modified.

Keep the provider directory private. Its `base_domain` supplies the allowed DNS
zone and its `gateway_hostname` supplies the shared publication gateway. The
coordinator imports a named immutable template into private Barbican records;
credentials are neither placed in the web config nor printed in a plan. It does
not register or change external DNS during empty-platform installation.

`quota_from_host_budgets: true` explicitly authorizes project CPU/RAM quotas from
the audited configured ceilings, after subtracting host reserves. State capacity
and GPUs remain host-local Placement/native PCI resources. This does not promise
that a large VM can span two hosts or authorize thin-disk overcommit.

## Review and apply the common workflow

Example parent request:

```json
{
  "schema_version": 1,
  "mode": "fresh",
  "config": "/srv/operator/deployment.yaml",
  "workspace": "/srv/operator/compute-bound",
  "known_hosts": "/srv/operator/known_hosts",
  "ack_new_cloud": true,
  "allow_experimental_ubuntu26": false,
  "use_testing_images": false,
  "preparation": "/srv/operator/host-preparation.json",
  "platform": "/srv/operator/platform.json"
}
```

`preparation` is optional if hostnames and the owned host network are already
prepared. In it, `network_request` and `geneve_request` must be present together
or both omitted for hostname-only preparation. Hostname/IP agreement and machine
fingerprints are checked before changes. Unrelated `/etc/hosts` aliases and
comments are preserved; conflicting non-loopback mappings are refused.

`repair_stale_networkd_cache` defaults to `false`. Set it to `true` only after
reviewing the networkd reload in the plan. Repair is limited to a positively
identified deleted `.network` file cached for an existing dedicated dummy.
Active files, physical interfaces, default-route interfaces and foreign
addresses are never rewritten. Only the dummy receives a reconfigure command;
the preceding manager-wide reload is an explicit opt-in, so review pending
networkd configuration changes first. Unrelated IPv4/IPv6 addresses, routes and
rules must remain unchanged. This option is not a general network-manager
conflict override.

```bash
export SPVM_INSTALLER_PYTHON=/srv/operator/client-venv/bin/python
scripts/install-openstack.sh --driver-dir /srv/verified-driver-bundle -- \
  plan-openstack --request /srv/operator/workflow.json \
  --output /srv/operator/platform-install-01

# Review the plan and exact source/input checksums before authorizing changes.
scripts/install-openstack.sh --driver-dir /srv/verified-driver-bundle -- \
  apply-openstack --request /srv/operator/workflow.json \
  --output /srv/operator/platform-install-01 \
  --plan-sha256 REPLACE_WITH_APPROVED_SHA256 --allow-changes
```

The initial approval expires after 900 seconds. The parent binds request files,
provider hashes, SDK code, driver/wheel and installer sources. Child plans are
generated immediately before their phase, when prerequisite state and new API
IDs are available; their desired policy is already bound by the parent intent.
An installed cloud is refused before preparatory hostname/network writes.
When the request includes the owned network recipe, the early guard defers only
its not-yet-created API VIP and the NAT host's `spvm-ext0` interface. Management
and tunnel interfaces must already exist. The later native Kolla plan checks all
interfaces and the VIP again after the owned network phase has created them.
Hostname-only preparation does not defer these requirements.

The installer runs with a private `umask 077`. Atomic replacement of `/etc/hosts`
must explicitly restore its approved original owner and mode using `fchown` and
`fchmod`, not just pass the mode to `open`. Otherwise a normal `0644` file becomes
`0600`: root lookup succeeds while Kolla's unprivileged RabbitMQ `getent` task
cannot read the new mappings and may fall back to a stale/loopback DNS answer.
This was reproduced during cleared-host acceptance; the replacement and rollback
paths now have a regression test under `umask 077`. Recovery must verify the
approved content checksum and original metadata before restoring permissions.
The same rule applies to the operator auth directory: explicitly restore `0750`
after creation/chown under `umask 077`; the auth file remains root/operator-group
`0640`. Merely calling `mkdir(mode=0o750)` creates `0700` in that context, so the
operator cannot traverse it and native accounting fails before compute mutation.
A separate regression test covers this directory/file permission contract.

## Ordered phases and result

1. Explicitly planned dedicated web account creation/reuse, followed by optional
   owned hostname and host network preparation/verification.
2. Base packages, host Docker SDK and API CA trust checks/preparation.
3. Native Kolla prechecks, deploy and post-deploy.
4. Strict-TLS API scope validation and new private operator auth file.
5. Shared Neutron network/router, project-private flavors/release marker and
   private-ACL Barbican probe with cleanup.
6. Runtime request staging and guarded quarantine/stop/enroll/start/accept/enable
   of each empty compute. Other control-plane containers remain protected.
7. Project quotas from configured host budgets.
8. Empty domain-based ingress scaffold and activation.
9. Private provider template import, matching web environment staging, capacity
   collector and delegated publication worker installation.
10. Authenticated Skyline login/profile/domain/catalog checks, anonymous access
    rejection, pinned JS/logo/gzip checks, and an empty healthy Nova cloud.

Success returns `PLATFORM_READY_NO_SWARM_CREATED`. It is an empty-platform HTTP/
API acceptance boundary, **not** a headed-browser test or a successful Swarm
launch. Creating a three-node release Swarm is a separate authorized operation.

## Failure handling

Every mutating stage writes intent before execution and retains its own plan,
journal and available backup. A failed parent becomes `NEEDS_REVIEW`; the new
platform path deliberately refuses automatic resume or ambiguous child replay.
Inspect actual side effects and finish an explicitly scoped recovery before
starting a new reviewed transaction. Repeating a completed parent returns
`ALREADY_APPLIED_NOT_REVERIFIED`, not a reinstall and not a new health claim.

No automatic rollback can recover encrypted guest disks, restore a deleted cloud
database or reverse external DNS writes. This workflow does none of those things.
Keep root/private transaction directories out of git and HTTP document roots.
