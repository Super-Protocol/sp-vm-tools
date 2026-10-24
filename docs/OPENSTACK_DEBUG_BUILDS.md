# SPVM debug builds in OpenStack

Debug builds require explicit administrator opt-in. They may expose diagnostic
access that release builds intentionally disable. Use them only in an isolated
test environment; do not expose guest SSH/debug ports to the public Internet.

Registering a debug image does not start a VM. Selecting a debug image does not
convert or restart an existing release VM. Measured guest state is not resumable:
never reboot bootstrap merely to switch builds.

## Defaults and independent gates

| Setting | Default / generated behavior | Purpose |
| --- | --- | --- |
| Platform `release_registration` | Omitted or `false`: web release registration disabled | Enables administrator registration of additional official releases |
| Helper request `allow_debug` | Shipped example is `false`; this required boolean cannot be omitted | Administrator approval for debug launches on that compute host |
| Installed helper `execution.allow_debug_launch` | Copied from the helper request's `allow_debug` | Host-side enforcement, independently of the web UI |
| Web `release_registration.allow_debug` | Fresh platform generation enables it only if **all selected helper requests** have `allow_debug: true` | Allows the enrolled administrator to register debug images |

If a web `release_registration` section is supplied manually, its `allow_debug`
must be an explicit boolean. An absent section disables registration; an absent
boolean inside an existing section is invalid, not an implicit opt-in.
The compute runtime has no top-level `allow_debug` switch. Do not add an unknown
field to `/etc/spvm/runtime-services.json`.

The fresh installer's conservative all-host rule prevents registering a build
that Nova might schedule onto a host that rejects it. Manually enabling only the
web gate does not grant host launch permission. Conversely, a host opt-in alone
does not enable web registration.

## Enable during a fresh deployment

Before staging/binding helper requests, explicitly set this on every selected
compute host's request (merge into the complete request, not a replacement):

```json
{
  "allow_debug": true,
  "registered_release_cache_root": "/data/spvm-registered-releases"
}
```

The cache root must be isolated from state-disk pools, tools, execution journals
and the default release cache. The pinned default `release` may remain a release
build: allowing additional debug images does not change that default.

In the fresh platform specification, enable dynamic registration:

```json
{
  "release_registration": true
}
```

Then run the normal reviewed platform plan/apply workflow described in
[OPENSTACK_PLATFORM_WORKFLOW.md](OPENSTACK_PLATFORM_WORKFLOW.md). Use a matching
verified driver bundle with `administrator-release-registration` and
`private-publication-input-handoff`. The installer prepares the isolated helper
cache, enrolls registrar IDs in compute policy, generates the web delegation,
and installs the services. These opt-ins do not bypass administrator enrollment,
project/port/grant checks, official GitHub release validation, or cluster CA
readiness. Debug builds skip the release-build signature-presence gate.

After deployment, use **Images → SPVM releases** to register `build-N-debug`,
then select it in **Create Swarm cluster**. First launch may download its files
into the selected host's registered-release cache.

## Enable on an existing, already registration-enabled installation

This procedure assumes dynamic release registration is already enrolled and its
isolated compute caches exist. Otherwise perform the full reviewed enrollment
described in the platform guide; toggling these two booleans is insufficient.

Choose a maintenance window with no active create/join/delete operations or
helper requests. Existing guests do not need to be stopped or rebooted. Record
the change and keep root-only backups of the current files before editing.
Configuration files may contain sensitive paths or material: never paste their
complete contents into a ticket or chat. Preserve file ownership and permissions.

1. On **each eligible compute host**, set only
   `execution.allow_debug_launch` to `true` in `/etc/spvm/host-helper.json`.
   Validate with the installed helper before restarting it:

   ```bash
   sudo python3 - <<'PY'
   import runpy, sys
   sys.path.insert(0, '/usr/local/libexec/spvm')
   config = runpy.run_path('/usr/local/libexec/spvm/host_helper.py')['load_config'](
       '/etc/spvm/host-helper.json')
   print('Debug launches allowed:', config['execution']['allow_debug_launch'])
   PY
   sudo systemctl restart host-helper.service
   sudo systemctl is-active host-helper.service
   ```

2. On the controller, locate the web configuration from the installed unit:

   ```bash
   sudo systemctl show spvm-web-nodes.service -p ExecStart
   ```

   In the file selected by `--config`, set only
   `release_registration.allow_debug` to `true`. Preserve the enrolled project,
   registrar/reader IDs and all other fields. Restart the web process after
   checking there are no active operations:

   ```bash
   sudo systemctl restart spvm-web-nodes.service
   sudo systemctl is-active spvm-web-nodes.service
   ```

   The web process reads this configuration at startup. Editing the file without
   restarting that process does not change its active policy.

3. Reload **SPVM releases**. An enrolled administrator should see
   `Debug releases: allowed`. Retry the same previously rejected build. The
   specific `Debug registration is not enabled by the operator` rejection occurs
   before image/approval writes; no VM was created by that registration request.
   For an ambiguously accepted request, preserve its original request ID and
   reconcile it instead of issuing a different registration.

Neither a Nova compute restart nor a libvirt/QEMU restart is required for this
helper-only flag change. Do not edit a Docker-bound compute runtime file or
rewrite sealed historical installation requests to perform this maintenance.
Keep a separate reviewed policy-change record, and set `allow_debug` explicitly
in the input requests for any subsequent fresh deployment.

## Disable after diagnostics

First disable web debug registration, then disable debug launch admission on the
compute hosts using the same validated maintenance procedure. If a helper's
default release or static `release_caches` includes a debug build, prepare a valid
release-only policy before setting its flag to `false`; the validator refuses
inconsistent static debug enrollment. Do not remove unrelated release caches.

Disabling admission does **not** stop an existing debug VM or remove registered
Glance images. Retire diagnostic clusters separately through normal cluster
deletion. Restoring a policy backup never restores a deleted Swarm cluster or its
randomly encrypted guest state.
