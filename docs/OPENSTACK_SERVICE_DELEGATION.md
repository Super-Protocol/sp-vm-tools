# Service authentication and automatic cluster deletion

This guide covers OpenStack integration, not services inside Swarm guests.
Driver implementation and full policy documentation live in the selected
swarm-cloud bundle: `deployment/SERVICE_DELEGATION.md` and
`deployment/SERVICE_IDENTITIES.md`.

## Fresh installation

Use a matching bundle advertising `restricted-service-credentials` and
`automatic-web-retirement-permits` and `matched-web-client-wheel`. The controller-completion installer refuses
older bundles rather than copying the UI administrator password into a service.
The hardware bootstrap and its BIOS/reboot requirements remain a separate step.

After deploying the pinned Kolla APIs, the operator session provisions separate
Keystone application credentials for ingress and retirement. Both are scoped
to the enrolled project with only member/implied-reader roles, no admin role,
and `unrestricted=False`. Their publisher user ID is preserved for existing
Barbican ownership checks; credentials do not share the UI password.

- Ingress: GET only on compute/network/key-manager APIs.
- Retirement: the same GET operations, deletion of owned servers/groups/ports/
  security groups/secrets, and creation/update of private Barbican cleanup
  journals/ACLs. No VM creation/restart/migration or shared-network deletion.
- The optional root readiness observer uses another GET-only credential and
  never uses the UI administrator's clouds.yaml for normal observation.

The installer proves allowed reads and denied writes before activating each
credential. Consuming services must enforce access rules via the matching
`keystone_authtoken.service_type`: compute, network and key-manager. Merely
checking token roles is insufficient. A 400 invalid-input response is not proof
of authorization denial; require 401/403 for the negative checks.

Protected files are separate:

| Path | Ownership/mode |
| --- | --- |
| `/etc/spvm/auth/admin-openrc.sh` | root:root 0640; administrative operations only |
| `/etc/spvm/ingress-auth/openrc.sh` | root:spvm-ingress 0640, parent 0750 |
| `/etc/spvm/retirement-auth/openrc.sh` | root:spvm-ingress 0640, parent 0750 |
| `/etc/spvm/retirement-dns.json` | root:root 0600 |
| `/etc/spvm/automatic-retirement-policy.json` | root:root 0600 |

`spvm-ingress` is a dedicated non-login Unix account, not the SSH transport
user. The web user receives neither those credentials nor sudo/Docker rights.
Root-only operator credentials and standard native Nova service credentials
still exist where administrative/control-plane work requires them; they are
not copied into this non-root service account.

## Deletion from the UI

The administrator confirms the exact cluster name. Root automatically prepares
the per-UUID permit for an eligible new request: it checks the original local
birth/enrollment, project/publisher/network/domain ceiling and independently
verifies the original Barbican intent with restricted credentials. It runs the
existing no-mutation retirement preview before activating the permit. Names and
Nova metadata alone are never deletion authority.

No manual per-cluster permit is needed after a matching fresh deployment.
Partial publication, changed input binding, missing intent or failed ownership
checks require review; there is no admin-credential fallback. Completed minimal
journals remain for audit/idempotency but disappear from the live cluster list.

An automatic authorization failure is shown as `PERMIT_REVIEW_REQUIRED` (cleanup
has not started). A failure after cleanup starts is `CLEANUP_REVIEW_REQUIRED`.
The worker continues processing other eligible clusters and does not blindly
retry the failed request. After inspecting the original intent, exact root policy
and retained journal, the operator can resume that same UUID:

```bash
sudo /usr/bin/python3 /usr/local/sbin/spvm-web-retire \
  --policy /etc/spvm/web-retirement-policy.json --resume CLUSTER_UUID
```

This cannot bypass ownership checks or changed policy bindings. Never remove
journals to make a request look new.

## Repeats, rotation and matching releases

Credential configuration reuses an existing protected OpenRC after verifying its
exact API profile, publisher/project and allowed/denied API probes. It returns
`reused: true` without creating another credential or rewriting the secret. A
lost secret with an existing Keystone name is an operator-review condition.
New credential activation failure revokes only that new credential; failed reuse
does not revoke the existing one. A process killed after API creation may leave
an ambiguous result: inspect the exact Keystone name before proceeding.

Rotation is explicit: pause the affected timer and finish its in-flight job,
acquire the shared publication lock, prepare/test a separately named replacement
in a protected sibling file, preserve a root-private rollback copy, atomically
switch the active file, verify reuse and normal service work, then revoke only
the old credential ID. Do not terminate an in-flight deletion, revoke the
publisher account or restart a measured VM. Full procedure and helper API:
`deployment/SERVICE_DELEGATION.md` in the driver bundle.

This does **not** make fresh platform deployment a generic occupied-host upgrade.
The fresh coordinator refuses occupied state and ambiguous previous transactions;
a completed repeat reports `ALREADY_APPLIED_NOT_REVERIFIED`, not a health check.
Enroll additional computes through the scoped compute workflow using the same
approved driver artifact, not another fresh-controller transaction.

Runtime staging now verifies the client wheel's exact modules and version against
the selected bundle before writing its output. Keep Nova images, client/bridge
and Skyline assets from that same verified artifact. The bundle contains
`deployment/RELEASE_CANDIDATE_CHECKLIST.md` with pre-build checks and the remaining
published-artifact, disposable deletion and clean-OS acceptance boundaries.

## Existing occupied installations

The fresh installer must not be used as an implicit upgrade of an occupied host.
Perform a scoped migration with backups and matching worker/exporter/client code:

1. Prove the new credentials' positive/negative API permissions.
2. Switch ingress `run_as` from the SSH login to `spvm-ingress`; fix only the
   group of root-sealed input copies, not private web-state access.
3. Replace service-readable admin-password copies with the restricted OpenRCs;
   retain rollback credentials only in a root-private archive.
4. Configure automatic project/zone/network limits and restart only the relevant
   background workers. Never reboot a measured guest to migrate credentials.
5. Use optional integer Unix `not_before` to leave already queued confirmations
   waiting for explicit approval. Otherwise enabling automatic permits may
   execute a historical queue. The lab migration intentionally used this fence.
6. Verify timers, unchanged VM processes, and an isolated no-VM permit-preparation
   smoke test with a disposable Barbican intent. Do not fake a confirmation in
   the live queue to test deletion.

API allowlists are a project/action boundary, not a per-secret-name or UUID
boundary. Keep the root exact-resource checks and filesystem isolation. Rotate
application credentials independently of UI password changes, and revoke old
credentials only after successful cutover. Additional publishers/projects need
their own reviewed enrollment and credential delegation.
