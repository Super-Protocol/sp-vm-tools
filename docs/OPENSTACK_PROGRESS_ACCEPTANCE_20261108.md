# OpenStack progress: cleared-stand acceptance, 2026-11-08

## Source and delivery identity

The presentation change is implemented in both repositories. Human progress is
written to stderr; existing stdout summaries/JSON and RPC/archive streams are
unchanged. See [progress behavior](OPENSTACK_PROGRESS.md).

| Component | Verified identity |
| --- | --- |
| Frontend progress | `sp-vm-tools` `8c444b0156885b38a05f1a8341c80745888e25fe` |
| Frontend used on the stand | `b6119b9b9da837533a1e5f770b75f0d6029d7b32` |
| Driver progress bridge | `swarm-cloud` `f3ddfd2dcd251e2f129f3502d80485a7212a970f` |
| Driver used on the stand | `ba7f67eb68b97c840a88da68841f4acb31a5dd13`, version `0.2.39` |
| Kolla source | `689533989dc27670e4fde6e0b313c5150b8efe7f`, 2026.2 / Ubuntu Noble containers |
| Driver CI | [run 42469572000](https://github.com/Super-Protocol/swarm-cloud/actions/runs/42469572000) |
| Release tag | `openstack-feature-spvm-bare-metal-cloud-1325095077dd` |
| Archive | `nova-spvm-openstack-feature-spvm-bare-metal-cloud-1325095077dd.tar.gz` |
| Archive SHA256 | `8d1a4a3f29248049d9ff30a9b53ee117e595828bffb5d7d1cc167d07491b1f35` |

Both branches were pushed; the downloaded published archive passed SHA256 and
strict source/wheel/bundle checks, including the deployment presentation module.
The rolling release tag alone is not an immutable build identity.

## Focused local tests

173 focused tests passed: 20 progress tests, 31 single-install tests, 29 cloud
management tests, 22 add-manager tests, 13 add-batch tests, 13 remove-manager
tests, 13 compute-onboarding tests, 14 Kolla workflow tests and 18 driver
transaction tests. Tests remain outside the commits.

Fake clocks/processes cover the 60-second interval, short operations, nested
steps, host switching, audit coalescing with unchanged audit-call counts,
transferred byte counters, local-log metadata and quiet response waits. Failure,
nonzero return and Ctrl+C stop the reporter without a false successful root
message; confirmation pauses it. Tests also cover stderr/stdout separation,
unchanged binary data, no secret/log-body disclosure and no extra SSH probes.
This is focused coverage, not a claim that every repository test suite passed.

## Reviewed blockers before the successful installation

1. `install-01` refused during preflight because the previously pinned official
   Nova and Skyline manifests returned `404 MANIFEST_UNKNOWN`, even though old
   local cache entries existed. No installation phase ran. Manually reviewed
   replacements retain Kolla 2026.2 / Noble, Linux/amd64 and Kolla version 23.0.1;
   all 46 generated Skyline assets passed the existing exact patch/source
   checks. The two additional source commits above pin those replacements;
   no floating-tag selection, local-cache bypass or relaxed source check was
   introduced. See the exact pins and retention limitation in the progress guide.
2. `install-02` reached image export and correctly refused insufficient local
   disk space: about 3.8 GiB free, below the existing reserve and export-space
   requirement. Kolla/guest deployment had not started. Its failed journal was
   preserved, not replayed. Two completed local CC-test histories were moved
   from `/var/lib/spvm-cloud-test-20261107[-v2]` into the existing mounted
   `/data/spvm-test-archives-20261108/` under their original directory names.
   Relative paths, types, sizes, ownership and modes were compared; this was
   not a full content rehash. Root free space increased to about 35 GiB.
   No global Docker prune, Clonezilla deletion or disk formatting was performed.
   The exact pre-Kolla partial preparation on super7 was separately audited and
   cleared, preserving the failed transaction, then `install-03` started fresh.

The initial reset was recorded under `/var/backups/spvm-progress-20261108-01`
on both physical hosts. The separate pre-Kolla super7 reset was recorded under
`/var/backups/spvm-progress-20261108-02`. These private records are not Git data.

## Live acceptance

The fresh cloud UUID is `c667af79-c528-4111-8916-ba6c671b7093`;
deployment name is `openstack-progress-01`. The private evidence root on petr-dev
is `/var/lib/spvm-progress-test-20261108`. Public stdout/stderr captures and
monotonic event timestamps were kept separately from private installation logs.

- `install-03` completed normally in **68:12**. Independent checks confirmed
  super7 `UNCONFIGURED`, Nova disabled, no assigned CPU/RAM/pools, no workload
  readiness traits and `reserved == total` for VCPU/MEMORY_MB. The initial catalog
  observation was transient; the read-only assertion passed after normal refresh,
  without repeating an installation phase or changing protected state.
- Ordinary Nova requests through Auto, explicit host selection and a direct
  `Nova service enable` attempt with spoofed user metadata all ended in `ERROR`.
  Their temporary servers, image and flavor were removed. Nova service was
  returned to disabled before typed configuration.
- Typed APIs applied **8 vCPU / 32 GiB RAM**, then one `/data` state pool with
  **500 GiB budget / 200 GiB reserve**. GPU use remained disabled. The intermediate
  state was explicitly checked as `CONFIGURED_DISABLED`; separate Enable then
  succeeded. No installation baseline or automatic quota allocation was used.
- An ordinary CirrOS 0.6.3 test VM reached Nova `ACTIVE` and an actual console
  login prompt. Its UUID was `695d8133-9d1f-4296-8b02-5a2f90d2c658` and QEMU PID
  `1341573`. PID/start ticks and the exact protected resource object were saved.
  The test has no NIC; it verifies boot/process preservation, not guest networking.
- New-host `add-super6-01` completed normally in **24:34**, classification `new`,
  using controller discovery and minimal YAML without the initial installation
  history. No manual completion or transaction replay was needed. Following
  normal post-publication catalog refresh, super7 was `ENABLED` and super6 was
  `UNCONFIGURED`/disabled. During deployment and after completion, the test VM
  retained its process/resources and all **36 controller containers** retained
  their IDs, images, running state, start times and restart counts.
- Normal `remove-super6-01` completed in **09:13**, `COMPUTE_REMOVED`, no inflight
  step and `guests_changed: false`. The same VM/process/resources and all 36
  controller containers passed the preservation assertions afterwards.
- Normal `return-super6-01` completed in **23:44**, classification `returning`,
  `COMPUTES_ADDED`, `guests_changed: false`, without a recovery procedure. The
  guest's original PID/start ticks and resource object were preserved throughout
  the entire cycle. All 36 controller containers passed the same unchanged-state
  assertion again before test cleanup.
- Only the saved test server, private image and flavor were deleted afterwards.
  Final independent acceptance confirmed **zero servers across all projects**,
  controller generation **4**, clean pinned frontend provenance, retained
  retirement evidence, unchanged CA binding and no incomplete resource
  transaction. Super7 is **ENABLED: 8 vCPU / 32 GiB RAM / 500 GiB state, 200 GiB
  reserve, no GPUs enabled**. Super6 is technically ready but
  **UNCONFIGURED/disabled**, with no CPU/RAM/pools assigned, ready for the
  administrator's own first configuration.

Installation produced **137 progress lines**, all on stderr, including **57 WAIT
lines**. Measured silence before each WAIT was **60.008–60.749 seconds**; the
largest gap between any two progress events was 60.749 seconds. Real observations
included all three cases:

```text
[WAIT] Deploying OpenStack services — elapsed 01:00; local log updated 00:02 ago
[WAIT] super7: Stopping managed services on the empty target — elapsed 01:00; awaiting response; last confirmed update 01:00 ago
[WAIT] Sending and committing the controller cloud kit — elapsed 01:00; sent/received 796528640 bytes; last transfer update 00:00 ago
```

All four successful captures passed the stderr/stdout, no-ANSI and no-legacy
`audit:` checks. Together they contained **350 progress lines / 84 WAIT lines**;
the largest observed silence between progress events was **61.008 seconds**
(60-second threshold, one-second timer polling and process/output scheduling).
There were no early/duplicated heartbeats or false ERROR/success messages.
The displayed command durations and real wait counts were:

| Command | CLI duration | Progress lines | WAIT lines |
| --- | --- | --- | --- |
| Fresh install | 68:12 | 137 | 57 |
| Add new super6 | 24:34 | 88 | 12 |
| Remove super6 | 09:13 | 36 | 4 |
| Return super6 | 23:44 | 89 | 11 |

The wrapper's timestamps also include container startup/exit and can be slightly
longer than the CLI's displayed command duration. During maintenance, a host
prefix identifies the logical maintenance target; controller-kit substeps still
transfer to/from the authenticated controller, not to that compute host.

The deployed GUI passed authenticated Chromium checks of Host resource budgets
and both native hypervisor detail pages: correct final states, no stale
empty-host blocking warning after refresh, no JavaScript errors, Placement
failures or false failure notifications. Screenshots were retained privately in
`/tmp/spvm-progress-browser-results/`. The generated administrator password was
absent from every captured public stdout/stderr log. Credentials remain in the
controller's protected `/etc/spvm/auth/admin-openrc.sh`; none are included here.
The existing UI address is **https://92.223.119.247:9999**, under the existing
network restrictions. The temporary loopback-only GUI tunnel was closed.

Final host preservation comparisons passed: **60 protected files on super7 and
59 on super6**, including bootstrap/libvirt/QEMU/network configuration, plus
unchanged default routes. Each host retained **34 Clonezilla files**, compared
by relative names, sizes and modification times, **not by full content rehash**.
No disk was formatted and no Clonezilla/unrelated data was removed.

Catalog publication is asynchronous. Immediately following the long kit
publication, a read-only observation temporarily reported
`LIFECYCLE_OBSERVATION_STALE`. Normal refresh cleared it; no service restart,
forced refresh, TTL relaxation or admission override was used. The acceptance
waits read-only for a fresh catalog before asserting final state.

## Scope and limits

This was a cleared managed-OpenStack stand on existing Ubuntu 26.04 TDX hosts,
not a fresh OS installation. Bootstrap, libvirt/QEMU, local OS networking and
Clonezilla/unrelated data were retained. Shared-interface management is explicitly
unencrypted; upstream Kolla images are acknowledged test images, not a certified
production kit. No new public ports or service SSH keys were introduced.

The isolated maintenance manager is a container on petr-dev with the prepared
environment, clean matching frontend source, an operator key/trust file and a
minimal target YAML. Neither the original install history nor the installation
workspace is mounted. This verifies absence of that dependency, not operation
from a second physical management machine or controller failover.

The workload acceptance uses an ordinary Nova/CirrOS VM to verify admission and
preservation; it is not a new Swarm, CC guest attestation, GPU/multi-GPU or NVSwitch
acceptance. Azure/non-CC stands were not touched. Browser verification uses a
temporary loopback SOCKS tunnel and tolerates the lab certificate in Chromium;
SDK/API checks use the installed CA. No credentials are included in this report.
