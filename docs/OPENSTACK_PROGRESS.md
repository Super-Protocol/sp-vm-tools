# Installer progress and long operations

`spvm-openstack.sh install`, `add-compute` and `remove-compute` share a compact
English progress display. There are no new CLI options or Python dependencies.
Human progress is appended to **stderr**, without colors, cursor movement,
spinners, estimated completion times or a synthetic overall percentage.
Existing stdout summaries, approval prompts, status codes and JSON results retain
their existing formats; stderr must not be merged into a machine-parsed stream.

```text
[RUN] super6: Deploying Nova compute
[WAIT] super6: Deploying Nova compute — elapsed 04:00; local log updated 00:08 ago
[OK] super6: Deploying Nova compute — 04:32
[RUN] super6: Registering and checking compute
```

The display includes the current host when applicable, major preparation steps,
Kolla prechecks/deploy/post-deploy, protected host management, compute enrollment
and acceptance, catalogs and the durable controller kit. Each announced step
finishes with elapsed monotonic time. A failed step reports its action, host,
duration and the available private log or operation journal path. Inspect the
transaction before further action; a message is not permission to replay it.

## Waiting is not a health guarantee

After **60 seconds without another public progress message**, one WAIT line
reports the current step and its duration. Nested work uses the same reporter;
there is no independent timer per host/child. Approval input pauses the timer.
Completion, failure and interruption stop it.

- Owned archive streams count the actual bytes read/written without modifying
  their contents. A total is shown only when known. Once archive streaming ends,
  the display returns to waiting for verification/acknowledgement.
- For explicitly registered local logs, only inode/size/mtime changes are
  observed. The message says when a local log update was observed, not that a
  remote process is healthy. An old log is not treated as newly updated merely
  because the reporter started.
- Without such evidence, the message says **awaiting response** and gives time
  since the last confirmed update. A live local coordinator does not prove that
  a remote command is making progress. SSH/SCP, quiet hashing/fsync and remote
  verification can legitimately produce no new output for several minutes.

No raw Ansible tasks, command lines, HTTP/RPC payloads, credentials or private-log
contents are echoed. Repeated host audits are coalesced in the display, but every
original audit and safety check still executes. Progress performs no additional
SSH requests and does not change timeouts, keepalive, WAL ordering, scheduling,
admission, automatic-retry rules or controller dialogue/archive framing.

## Artifacts and verification

The frontend owns the stdlib reporter. The matching driver contains only an
optional in-process presentation hook. Standalone controller/helper processes
do not start an operator reporter or add text to their JSON/stdout channels.
Both repositories participate in the checksummed kit; use a matched build.
Do not edit a verified kit or weaken source checks to test a new frontend against
an old cloud. No source-snapshot migration or compatibility adapter is provided.

Synthetic tests use fake clocks/processes for the minute interval, nested steps,
errors/Ctrl+C, audit counts, stream byte preservation and stderr/stdout isolation.
These do not replace live installation or network acceptance. Dated stand
reports identify the exact commits, CI archive digest and live cases exercised.
