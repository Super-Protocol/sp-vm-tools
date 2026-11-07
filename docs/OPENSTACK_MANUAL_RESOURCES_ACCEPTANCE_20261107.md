# Manual host resources: CC stand acceptance, 2026-11-07

## Delivered source and artifact

- `sp-vm-tools`, branch `feature/openstack`: main contract `9e0a5b7`, final
  acceptance fixes `a94914d`, `4b259df`, `124b88e`, and addition-journal isolation
  `0ed51c1e707c9751b3b5db87a44018105520a9ba`.
- `swarm-cloud`, branch `feature/spvm-bare-metal-cloud`: main contract
  `49ada83f`; final driver **0.2.36**, commit
  `9cd7693caee6ce017d08d7aae25cae326a433a9d`.
- Successful [CI build](https://github.com/Super-Protocol/swarm-cloud/actions/runs/42396323487).
  Published asset:
  `nova-spvm-openstack-feature-spvm-bare-metal-cloud-1325095077dd.tar.gz`.
  SHA256: `424fb091e1d0ea47bb0fae4343910b9b315e19d6a906e5d81243c77d731a8fca`.
  The branch release tag is rolling; the commit and digest identify this test.
- Archive SHA256, clean source manifest, source/wheel checksums and the complete
  33-module executor were verified. Its isolated `python -I -S` import probe
  cannot depend on an installed SDK, user site packages or sibling checkout.
- Kolla 2026.2, commit `689533989dc27670e4fde6e0b313c5150b8efe7f`, Noble
  containers on Ubuntu 26.04 hosts with the explicit experimental opt-in.
  Upstream Kolla test-image opt-in was explicit; this is not a production
  certification of those images.

Reviewed immutable bases:

```text
quay.io/openstack.kolla/nova-compute@sha256:e10ae261dc29e538b4543d2ea72f7712d8e93e8c20cbcb2e637693af7e3f7178
quay.io/openstack.kolla/skyline-console@sha256:ab9b7c6d361948f6ee01337a00a3304f4d0be8049738e3007d7b2dd2db09fda4
```

## Installation boundary and preserved data

The two authorized CC hosts were cleared of managed OpenStack containers,
volumes, configuration, enrollment and runtime state before installation from
`petr-dev`. Public installation YAML contained no resource fields. Host OS,
installed system packages and Docker image cache were retained: this was a
cleared OpenStack installation, not a new OS or Clonezilla restore.

Old managed data was archived under
`/var/backups/spvm-manual-resources-20261107-08`. Disks were not formatted;
existing `/data` workload directories and unrelated data were not deleted.
Post-test comparison preserved 60 protected bootstrap/libvirt/QEMU/network file
hashes on super7 and 59 on super6, plus size/mtime inventories of all 34 Clonezilla
files on each host and the original default routes. This is not a new full
checksum verification of every large Clonezilla image. Azure/non-CC was untouched.

The old entry point is absent. Infrastructure and membership use
`scripts/spvm-openstack.sh`; resource allocation is a separate GUI/CLI/API task.

## Live acceptance results

| Check | Result |
| --- | --- |
| Initial install, both hosts | Technically ready, Nova up/disabled, protected `UNCONFIGURED`, CPU/RAM null, no pools or enabled GPUs |
| Initial Placement | Real positive VCPU/RAM totals, `reserved == total`, ratio 1, no workload child providers or SPVM-ready traits |
| Initial GUI and CLI | Both unconfigured, `Not configured` values, Enable blocked, no Automatic selector |
| Ordinary/SPVM direct Nova API, Auto and explicit host | All four attempts rejected; no guest became ACTIVE |
| First CPU/RAM through CLI | Explicit 8 vCPU / 32768 MiB applied to super7; missing pool kept it `UNCONFIGURED`/disabled |
| First pool through GUI | Explicit 500 GiB budget / 200 GiB reserve on `/data`; stayed `CONFIGURED_DISABLED` |
| Manual native Nova service enable and forged tenant metadata | Ordinary and SPVM launches still rejected; UUID-bound compute logs confirmed protected driver admission |
| Separate GUI Enable | Succeeded only after complete resources and technical checks |
| Real ordinary guest | ACTIVE/RUNNING and actually booted CirrOS 0.6.3 to a login prompt |
| Busy-host Disable/Enable | Same QEMU PID/start ticks and unchanged native inventories; no guest restart |
| Busy-host CPU increase | Refused before submitting a mutation |
| Service restart persistence and lifecycle CLI | Enabled and disabled states survived Nova/helper/executor restarts; guest PID preserved; real CLI Disable/Enable succeeded |
| Remove/add with busy peer | super6 removed/re-added while super7 guest kept its PID and manual budgets |
| No restored budgets on returning compute | Explicit pre-removal 4 vCPU / 8192 MiB on super6 became null/null with no pools or enabled GPUs after re-add |
| First manual GPU configuration | Unknown descriptive metadata did not block standalone PCI registration; one native GPU inventory published, CPU/RAM/pool retained, host stayed disabled until separate Enable |
| Final GUI | Both resource cards, narrow-screen layout and both native hypervisor detail pages passed; no JavaScript errors or false VGPU/Placement notifications |
| Cleanup | All acceptance guests, images and flavors deleted; no Nova guests in any project or libvirt domains on either host |

The positive guest UUID was `456b36bf-ce63-4ab6-8d9b-8f78d6de997d`; its QEMU PID
`1742926` and start ticks `34041298` remained unchanged throughout busy-host,
service-restart and peer remove/add checks. It was then deleted through Nova.

The deliberately misclassified CirrOS image was used **only for denied SPVM
launches**, never as a positive SPVM/CC guest. This acceptance does not claim a
new Swarm deployment, GPU guest launch or successful CC attestation. Multi-GPU,
NVSwitch, root-only/multiple filesystems, partial configurations, request-ID/CAS
and interruption/concurrency behavior were checked synthetically, not on new
multi-GPU hardware. The stand exercised CPU-only operation with physical GPUs
disabled before explicit selection.

Focused synthetic suites passed: manual resources/admission/executor (58),
lifecycle coordinator (5), compute onboarding (13), initial platform verification
(8), final coordinator audit (5), addition-journal namespace (1), and controller
GPU transactions (7). These are focused results, not a full repository-suite claim.
Tests were intentionally excluded from commits, as requested.

## Defects found and corrected

- Fresh executor packaging had incomplete imports/fallbacks. Complete declared
  dependency closure and isolated import checks now fail before deployment.
- Launcher readiness now verifies actual installed provenance rather than
  assuming bootstrap files match the supplied bundle.
- Final acceptance previously expected enabled hosts. It now verifies healthy
  deny-all enrollment, with a bounded read-only wait for missing publications.
- GPU configuration assumed `/etc/kolla/config` existed on the controller.
  Runtime changes now update verified live files, not operator workspaces.
- The local coordinator still expected an obsolete controller audit callback.
  It now explicitly performs and identity-checks the final operator-side audit.
  The stand's controller had genuinely completed all phases and authenticated
  API/GUI verification; the local rejection was a false negative. An explicitly
  reviewed, read-only completion receipt was appended after rechecking live
  state and identities. The original failed WAL was preserved, and no install
  operation was replayed or reset.
- Add-compute credential receipts shared the resource WAL directory and caused
  false global review blockers. New receipts use the separate private
  `/var/lib/spvm-compute-additions` namespace. After verifying successful add and
  the installed executor identity, the stand's one completed receipt was moved
  there intact under the worker lock. No resource transaction was deleted,
  repaired or replayed. Resource/GPU operations subsequently passed.

## Final state and access

| Host | Final state | Explicit applied test limits |
| --- | --- | --- |
| super7 / `92.223.119.247` | `ENABLED`, technically ready, Nova enabled/up | 8 vCPU, 32 GiB RAM, one `/data` pool with 500 GiB budget and 200 GiB reserve; one GPU |
| super6 / `92.223.119.245` | Freshly re-added `UNCONFIGURED`, technically ready, Nova disabled/up | CPU/RAM unset, no workload pools, all GPUs disabled |

These super7 values were explicit test administrator choices, not automatic
recommendations, installation baselines or hardware guarantees. There are no
unresolved resource transactions or false review blockers on either host.

UI: `https://92.223.119.247:9999`, then
**Compute → Hypervisors → Host resource budgets**. Controller-local HTTPS and
authenticated browser access were verified; the browser used a temporary
loopback-only operator SSH SOCKS proxy because direct access from `petr-dev`
to port 9999 was unavailable. No additional public firewall opening or management
listener was introduced for these tests or for host control. The temporary proxy
is stopped at handoff. Use the approved
management network/SSH path and trust the managed CA; do not globally disable
certificate verification. Credentials remain in the root-private installed
OpenStack auth files, not Git or this report.

Private evidence is under `/var/lib/spvm-openstack-manual-final-05` (including
the append-only completion review), `/var/lib/spvm-openstack-manual-remove-super6-01`,
`/var/lib/spvm-openstack-manual-add-super6-01`, and the controller's
`/var/lib/spvm-manual-acceptance`. It contains operator-specific paths and must
not be committed wholesale. See [the resource guide](OPENSTACK_HOST_RESOURCES.md)
for first assignment and Enable/Disable semantics.
