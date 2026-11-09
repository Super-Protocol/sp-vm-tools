# OpenStack installer follow-up work

This document tracks planned improvements, not deployment instructions or a
record of internal testing. Use [the deployment guide](OPENSTACK_DEPLOYMENT.md)
for the supported operator workflow.

## High availability

Design native Kolla/OpenStack control-plane redundancy separately from public
Swarm ingress/address failover and guest-network availability. Plan quorum,
independent failure domains, a stable API endpoint, durable configuration
authority, worker fencing, database/Barbican persistence and recovery. Multiple
API processes or an external load balancer alone do not remove every single
point of failure. See the backend's `deployment/HA_PLAN.md`.

Controller-backed membership currently supports a single eligible controller.
Multiple-controller selection must not retry ambiguous writes on another host.
Do not introduce automatic recovery or replacement of lost SPVM guests.

## Controlled upgrades

Define compatibility checks and reviewed update procedures for driver, launcher,
helper, GUI and configuration on occupied installations. Preserve guest processes,
resource state, cloud identity, request fencing and exact component provenance.
Fresh install or a new output directory is not an upgrade procedure. No
old-format adapter, silent adoption or workspace migration is planned.

## Durable delivery and offline operation

The current kit supplies prebuilt Nova/Skyline references, not all image layers,
system packages or Python/Ansible dependencies. Define the supported offline
scope, retained registries/mirrors, dependency locks and operator-owned backups.
Keep immutable release/version reservations and anonymous retrieval checks.
A digest binds content but cannot guarantee that a registry retains it.

## Optional post-install resource recommendations

Preview recommended CPU/RAM and state-pool limits after installation, accounting
for OS/controller headroom, filesystem identity and available space. Apply only
on explicit administrator request through the existing typed resource APIs.
Recommendations must never become an ongoing automatic mode, silently replace
manual settings, enable scheduling or change project quotas. Installation and
add-compute continue to finish UNCONFIGURED/disabled.

## Wider supported configurations

Expand reviewed OS/platform and heterogeneous GPU coverage, including physical
multi-GPU/NVSwitch configurations. Preserve full-group allocation, isolation,
release/non-CC admission and the absence of silent GPU substitution. Multi-cell
and generic public/provider-network layouts are not implied by the current
single-controller host-NAT profile.

## Administrative ergonomics

Improve diagnostics for missing controller backups or mismatched local
environments without guessing settings, regenerating cloud secrets or weakening
provenance checks. Retain minimal host-only membership inputs, explicit
approvals, sequential batch results and stop-on-first-failure behavior.
Public/private endpoint reachability must be explained separately from service
health, resource readiness and Swarm application availability.
