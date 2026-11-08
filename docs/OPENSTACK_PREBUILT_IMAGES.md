# Coordinated prebuilt Nova and Skyline images

The installer consumes `prebuilt-openstack-images-v1` CI kits. It does not build
containers, extract upstream JavaScript, apply frontend patches, or export/load
Docker archives. **Docker is not required on the management machine.** The
management prerequisites remain the prepared installer/Kolla Python environment,
Git, OpenSSH, OpenSSL and outbound HTTPS access to GitHub/GHCR and dependencies.
Select the complete kit through `installation_kit.directory` in deployment YAML.
Advanced staging also accepts `installation_kit.archive` or `.release` with
`.sha256`; diagnostic flags use `--installation-kit-dir/archive/release`.
The old `driver` selector is rejected, not migrated. This naming contract requires
`installation-kit-selection-v1`; it does not change the pinned-image integrity
checks or introduce additional installation steps.

## Trust and identity

The operator verifies the downloaded archive with its independently reviewed
SHA256. Inside it, `manifest.json` binds source files, the exact driver wheel and
the four CI artifacts: `installation-kit.json`, `console-component.json`,
`console-manifest.json`, and `nginx.conf.j2`.

`installation-kit.json` records exact commits of `spvm-openstack`, `sp-vm-tools`
and `skyline-console`, the Kolla revision, launcher recipe, driver/wheel hashes,
and immutable Linux/amd64 image references with their **distinct** registry
manifest digests and Docker config/image IDs. Before changing servers the
installer verifies these files, the actual installer source bytes, component
agreement, public registry manifests/configs, source/wheel labels and layer
availability. Missing, damaged, inaccessible or mixed components fail closed.
Preflight also assembles the exact helper source snapshot and checks its
bootstrap capabilities locally, before host audits or mutations. CI performs
the same consumer check with the pinned installer against the extracted release
kit; obsolete standalone orchestration scripts are not helper dependencies.
Integrity checks are not cryptographic signatures and are not guest attestation.

Do not override `installation.compute_base_image` or
`installation.console_base_image`: these old parameters are rejected. Different
components require a separately built and reviewed coordinated CI kit. Old
bundles and workspaces are not converted, adopted or migrated.

## Deployment and maintenance

After approval, the existing operator SSH transport prepares Docker on the
selected target hosts. Each compute retrieves its exact Nova digest; the
controller retrieves the console digest. The installer checks the returned
image/config ID, platform and repository digest. No service SSH account/key,
Docker TCP endpoint, new public port or background SSH observer is introduced.
An interrupted or failed pull retains the existing failure/review semantics;
there is no automatic retry, cleanup or transaction replay.

CI-owned nginx and console file metadata are sealed into the Kolla workspace.
The controller cloud kit retains manifests and image references, **not** image
tar archives or obsolete build contexts. Controller-backed add/remove uses this
same immutable kit from another management machine without original installation
history. Supported continuation boundaries recheck the new contract; completed
steps are not replayed and old kits are not adapted.

Installation and add-compute still finish UNCONFIGURED/disabled. Manual CPU/RAM,
state-pool/GPU configuration, explicit Enable, admission, WAL fencing and empty
host restrictions are unchanged.

## UI ownership and publication

The coordinated Nova/driver kit is now owned and built by
`Super-Protocol/spvm-openstack`, not swarm-cloud. The operator installer stays
in this repository. Select a standalone spvm-openstack checkout or its verified
release archive; the former monorepo backend path and source key are rejected,
not migrated. The source map pins spvm-openstack, this installer and Skyline
independently. See the new repository's `REPOSITORY.md` for extraction provenance
and the package Actions access required by its publishing workflow.

OCI config identity and Docker runtime identity are distinct. The kit and the
verified registry manifest always pin the config SHA256. With the classic image
store Docker uses that config ID; with the containerd image store Docker uses the
image manifest digest as its runtime ID. Target acceptance checks the exact
Linux/amd64 RepoDigest and single-platform descriptor, then binds containers to
that observed runtime ID. It does not accept a floating tag, an image index or
an arbitrary second ID. No old installation format is converted.

Frontend React changes and standalone SPVM pages are owned by the
`SuperProtocol/` directory of the `Super-Protocol/skyline-console` fork.
Authenticated APIs, workers, authorization and privileged operations remain in
`spvm-openstack`. Existing public URLs and the `/spvm-api/` loopback proxy remain.
The installer neither changes JavaScript nor requires a local console checkout.

Trusted feature-branch CI first mirrors the reviewed Nova/Skyline bases into
public GHCR packages without changing config or layers. Source builds use those
mirror digests. Node 22/Yarn Classic and the frozen lockfile produce a complete
new static tree, content-hashed chunks, gzip siblings and a file manifest; no
base static files are retained. Kolla runtime defaults are preserved and checked.
PR validation has no publishing permissions or secrets.

Every release/tag has component version, full source SHA, CI run and attempt.
Published releases and tags are never overwritten. Installation uses digests,
not rolling/latest tags. Publishing completes only after full anonymous image
downloads pass. New packages must be public; authenticated pushes alone do not
prove this. Existing package visibility is not changed automatically.

There is no automatic GHCR version deletion. Retain every digest referenced by
installed kits, recovery inputs or approved future deployments. A digest pins
content, not availability: deleting registry data breaks future install/add and
image retrieval. Separate retention backups remain an operator responsibility.

## Online dependencies and acceptance limits

This is **not a full offline kit**. Other Kolla service images, OS packages,
Python wheels/collections, registries and release downloads may still need
Internet. Ubuntu 26 remains the explicit experimental Noble-container profile.
This does not change inter-host network security requirements or encrypt the
OpenStack management network.

CI production-build/static checks, synthetic negative cases, and live cloud
acceptance are separate evidence. A successful build is not proof of a clean
installation, authenticated GUI operation, guest readiness, scheduling or HA.
