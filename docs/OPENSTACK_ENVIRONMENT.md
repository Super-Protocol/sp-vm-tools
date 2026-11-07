# Local installer and Kolla-Ansible environments

Run this preparation on the **operator/deployment Linux machine**, which may be
separate from the OpenStack controller. It creates local tooling, not a cloud.
It does not connect to target hosts, prepare their network, install Docker or
configure hardware. Host bootstrap and administrator-provided management
connectivity remain separate prerequisites for subsequent deployment phases.
Completing this step does not establish that every deployment phase supports a
remote workstation/controller split; follow each phase's documented boundary.

## Prerequisites: checked, never installed automatically

Run as an ordinary user, without `sudo`. The preparer requires:

- Linux; Python 3.11+ with `venv`, `ensurepip` and standard-library `tomllib`.
  Python must also satisfy the selected Kolla checkout's `requires-python`.
- Git, OpenSSH client commands `ssh`/`scp`, and OpenSSL on `PATH`.
- A readable, clean Kolla-Ansible Git checkout at an explicitly selected full
  commit SHA; its collection requirement files must be present.
- A writable parent for a new, isolated environment directory, outside the source
  checkout. The environment directory must not contain the source checkout either.
- For actual installation, outbound access to Python indexes and the collection
  sources declared by Kolla. Credentials must not be embedded in source files.

Missing prerequisites are reported together. On Ubuntu, the error includes an
example package-installation command for the administrator; it is **not executed**.
For example, an administrator may need `python3`, `python3-venv`, `git`,
`openssh-client` and `openssl`. Installing a newer Python or build prerequisites,
if a dependency cannot use an available wheel, is an administrator task. A failed
dependency build is an installation failure, not permission to install OS packages.

No local KVM/TDX, GPU, libvirt, Docker daemon, SSH key or forwarded agent is needed
for this preparation. Those are not checks for deployment target readiness.

## Select the source explicitly

Obtain and review the appropriate Kolla-Ansible checkout independently. For an
upstream commit available from OpenDev, the administrator can use:

```bash
git clone https://opendev.org/openstack/kolla-ansible.git /srv/operator/kolla-ansible
git -C /srv/operator/kolla-ansible checkout --detach FULL_REVIEWED_COMMIT_SHA
git -C /srv/operator/kolla-ansible status --porcelain
```

Replace the placeholder with the full reviewed commit, not a branch or `latest`.
Prepare writable parent directories first. A historical lab commit may belong to
a separate reviewed checkout and not exist upstream; do not substitute a newer
upstream commit just to make a fetch succeed. The preparer neither fetches nor
modifies the supplied checkout. It creates a local copy for package builds, since
Python build backends may write into their input directory.

## Check, then prepare

From the `sp-vm-tools` checkout, substitute real absolute paths and the commit:

```bash
bash scripts/openstack/prepare-environment.sh \
  --directory /srv/operator/openstack-environment \
  --kolla-source /srv/operator/kolla-ansible \
  --kolla-revision FULL_REVIEWED_COMMIT_SHA \
  --check
```

`--check` validates system prerequisites and input metadata without creating
directories or downloading packages. It is not a network-access test and does
not run pip or validate installed packages. Remove `--check` to prepare:

```bash
bash scripts/openstack/prepare-environment.sh \
  --directory /srv/operator/openstack-environment \
  --kolla-source /srv/operator/kolla-ansible \
  --kolla-revision FULL_REVIEWED_COMMIT_SHA
```

The shell wrapper uses `python3` from `PATH`; select an appropriate interpreter
there before invoking it. Alternatively invoke `prepare_environment.py` directly
with the desired Python using `-I -B`. The initial entrypoint needs no PyYAML or
other third-party Python package, including for `--help` and `--check`.

The private output directory contains:

| Path | Purpose |
| --- | --- |
| `installer-venv/` | Python with `PyYAML==6.0.3` for `spvm-openstack.sh`. |
| `kolla-venv/` | Non-editable selected Kolla installation and its Python dependencies, including Ansible. |
| `collections/` | Collections installed from Kolla's `requirements.yml` and `requirements-core.yml`. |
| `kolla-source/` | Local source copy used for builds and later Kolla operations. |
| `ansible.cfg`, `ansible-home/` | Isolated Ansible configuration and working state. |
| `prepare.log` | Private subprocess output with URLs hidden. |
| `environment.json` | READY receipt with source/interpreter identity, installed versions and collection-tree SHA256. |

Root directory permissions are `0700`; log and receipt are `0600`. Do not commit
the environment or logs. The receipt is provenance, not an attestation or a
lockfile guaranteeing identical fresh resolutions.

User pip configuration, `PIP_*`, `PYTHON*`, `VIRTUAL_ENV` and `ANSIBLE_*` overrides
are not inherited into installation commands. Pip cache use is disabled; Ansible
uses local configuration, home and collection search paths. Network proxies and
normal TLS trust remain system/operator concerns; custom pip index configuration
and offline wheelhouses are not supported by this command in this iteration.
Never disable certificate verification to work around a download failure.

Local `constraints.txt` and `upper-constraints.txt`, when present in the selected
checkout, are passed to pip. External constraints referenced only in development
tools such as tox are not fetched automatically. Upstream package/collection
ranges may resolve differently on a new installation, and collection requirements
may themselves reference branches. The resulting package versions, collection
versions and tree digest are recorded; selecting a Kolla commit alone does not
pin every transitive artifact. Preserve/review the receipt for reproducibility.

## Use the result

Copy the printed exports (no shell profile is modified):

```bash
export SPVM_INSTALLER_PYTHON=/srv/operator/openstack-environment/installer-venv/bin/python
export ANSIBLE_COLLECTIONS_PATH=/srv/operator/openstack-environment/collections
bash scripts/spvm-openstack.sh --help
```

Use the printed `kolla-source` and `kolla-venv/bin/python` paths for phases taking
`--kolla-source` / `--kolla-python`; the Ansible executable is
`kolla-venv/bin/ansible-playbook`. **No driver wheel, OpenStackSDK, credentials,
passwords, inventory or API trust is provisioned here.** Commands needing the
driver/SDK require that separate documented preparation; the minimal installer
venv is not advertised as a complete cloud client.

The collection digest records the installed upstream tree. It is **not** approval
of SPVM-specific Kolla/collection changes. Existing deployment stages may require
reviewed preservation patches or experimental Ubuntu 26 adjustments. Do not use
this receipt to bypass those checks. In particular, Ubuntu 26 target support
remains experimental; this preparer changes neither host facts nor target policy.

## Repeated execution and failures

A normal repeat with matching inputs performs pip/import/CLI and collection
checks and compares installed state with the receipt. It installs nothing and
does not update dependencies. Source commit/path, interpreter, preparer code or
dependency-input changes require a new directory. Keep the old environment until
you have deliberately switched every consumer; there is no migration mechanism.

Existing foreign, incomplete, non-private or mismatched directories are rejected.
If creation fails, no READY receipt is issued; inspect `prepare.log` when present
and choose a new directory after correcting the cause. Nothing is automatically
deleted or repaired, and there is no implicit resume. Download errors and failed
pip/CLI/collection checks return a nonzero exit code. No target host or global
Python/Ansible installation is modified.

## Verification boundary

Synthetic tests cover successful preparation with fake command executors,
missing prerequisites, Python/root/source/path rejection, failed preparation
phases, collection changes and repeat-without-install behavior. Local syntax,
dependency-free help and read-only preflight checks are separate checks.
They are **not** evidence of a real online Kolla installation, target-host
deployment, Ansible task compatibility or a working OpenStack/Swarm cloud.
