# Controller-owned cloud management

`controller-cloud-management-v1` removes membership operations' dependency on
the original installation directory or management PC. A matching **fresh install**
creates it; old clouds are not adopted or migrated.

## Another management computer

Prepare local environments as described in [environment preparation](OPENSTACK_ENVIRONMENT.md)
for the cloud's pinned Kolla commit and matching package/collection versions.
Use matching installer sources, administrative SSH and a host-only YAML:

```bash
sudo bash scripts/spvm-openstack.sh add-compute \
  --controller operator@192.0.2.10 \
  --config /srv/private/new-computes.yaml \
  --environment /srv/private/environment-01 \
  --output /var/lib/spvm/add-computes-01
```

For removal, replace `add-compute --config ...` with `remove-compute --host ALIAS`.
Neither command accepts `--installation`; removal needs no deployment YAML.
`--cloud-id UUID` optionally pins the expected cloud. The summary displays UUID,
deployment name and API endpoint. `--yes` skips only interactive approval.
Use a **new output directory** for every check/apply invocation.

SSH with noninteractive sudo is needed to the controller, selected targets **and
existing computes**. This removes local history, not fleet audit/peer-network
requirements. Controller access alone is insufficient. SSH key/trust overrides
remain optional; strict known-host checking is mandatory. The controller admin
may differ from the original installation account; its address must match
inventory. Bootstrap and existing inter-host connectivity remain prerequisites.
No CC downgrade or automatic bootstrap occurs. Only the single-controller profile
is supported; HA/failover is separate.

The prepared Kolla checkout normally belongs to the unprivileged environment
owner, while maintenance runs with sudo. Pinned template, Docker and compute-scope
Git reads use per-command `safe.directory` for that exact canonical checkout.
No global Git configuration is changed and no wildcard trust is installed.
Do not work around an ownership error with `safe.directory=*`; use the matched
installer/backend containing the scoped checks instead.

## State, provenance and secrets

Root-private `/var/lib/spvm/cloud-management` holds a pointer and checksummed
generations: portable infrastructure, Kolla inputs, unchanged passwords/CA/TLS,
prepared collections, launcher, offline compute image and verified driver kit.
Identity binds cloud UUID, controller machine, API endpoint/CA and administrative
project. Each generation retains operation kind, host, ID and approved plan hash;
removal evidence enables returning-host classification without the old PC.

Installer file hashes, Git commit and dirty-source indicator are recorded. Source
snapshots are retained for audit, **not automatically executed** on the new PC.
Mismatch requires matching recorded sources. A commit alone cannot reproduce a
dirty snapshot; use clean, reviewed sources for deployable kits. Local paths are
portable structured references, rebound to the new directory and SSH transport.
The local environment supplies pinned Kolla sources and venvs; venvs are not
transferred. Actual versions/imports/CLI/collections are verified without updates.
Private `cloud.json` records Kolla revision; `environment.json` lists expected
versions for diagnosing an environment mismatch.

Kolla runs on the management PC: necessary passwords and private TLS material
are downloaded to its **secret-bearing** working directory (0700; files 0600,
owner-executable artifacts 0700). Do not publish it, commit it or attach it
wholesale to reports. SSH encrypts transfer, not storage at rest. Administrators
must protect/encrypt backups. Hashes detect corruption, not a malicious controller
root replacing both records and hashes. No operator key/agent is copied/forwarded;
no service SSH identity, new port or arbitrary-command endpoint is introduced.

Current budgets, pools, GPUs and Enable/Disable remain authoritative in protected
host state/typed APIs, not the snapshot. New hosts are UNCONFIGURED/disabled;
existing resources and guests are not reset.

On shared-interface networks membership also updates the existing hosts' owned
management allowlists, including controller-only hosts. Geneve peers and the NAT
owner's probe sources change only by the selected host address. NAT probe rules
are updated under the old management guard, followed by the management guard and
its persistent NAT peer copy. Every step checks the installed runtime and live
rules; IPv6 guards, jumps, terminal drops, other firewall rules, interfaces and
routes are retained. Removal contracts these same lists. No public port is
opened. A partial network change remains NEEDS_REVIEW, not an automatic retry.

Back up the complete controller directory and referenced installed controller
components, plus normal OpenStack databases/credentials/configuration. This kit
is **not database backup or disaster recovery**. Losing it and its backup is a
refusal, not permission for API guesswork, password/CA regeneration or latest
downloads. Full generations contain the offline compute image: allow space for
retained generations and a new transfer on both PCs. Space is checked; successful
staging is renamed into the generation, not duplicated. Partial files remain for
review. Automatic pruning/cleanup is not implemented.

## Failure and acceptance scope

The controller resource lock and generation claims serialize mutations. Check
all targets before approval, commit sequentially, stop at first failure without
rollback. Earlier successes remain; later targets are untouched. Drift aborts
rather than silently rebasing. Unfinished claims, incomplete generations and lost
connections require review. No replay, failover, host/VM recovery or repair through
`--yes` occurs. Local output is evidence, not authority. `--check` retrieves a
private kit and inspects read-only, without persistent controller changes,
enrollment, package installation or credential rotation.

Current acceptance is **synthetic only**: archive/permissions, generations,
concurrency/interruption, batch ordering and portability after old paths disappear.
SSH, dependency execution and TLS checks are substituted in the portability
fixture. A matched new kit and clean stand install are still required for real
another-management-PC remove/add acceptance. Existing clouds without this
capability are deliberately refused, not migrated.
