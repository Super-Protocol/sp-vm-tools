# Running a Super Protocol Swarm VM

This guide walks through the full Swarm VM lifecycle on a confidential host: preparing the environment, populating the provider configuration, building the VM image, starting and verifying the VM, and stopping it. It is written around the `gp-ws-01` build server but works on any host that satisfies the prerequisites.

It also includes a short section on launching the same image in Google Cloud via Terraform.

> Throughout the guide, replace `fixcik` with your own username on the build host.

## Prerequisites

- SSH access to the build host (e.g. `gp-ws-01`).
- Docker with `buildx` enabled and your user in the `docker` group.
- The host bootstrapped for confidential computing — see the main [README](../README.md) (`scripts/bootstrap_tdx.sh` or `scripts/bootstrap_snp.sh`).
- A populated provider config directory, e.g. `~/swarm/provider-configs/swarm` (see below).

## 1. Connect to the host

```bash
ssh gp-ws-01
```

## 2. Get the launch tooling

For a normal launch (downloading the released image) you only need
`sp-vm-tools` — it contains `start_super_protocol.sh`. The `sp-vm` repository is
the image source and is only needed if you build the image yourself (see
[Building the VM image locally](#building-the-vm-image-locally-optional)).

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/Super-Protocol/sp-vm-tools
# `main` already contains the Swarm bits
```

## 3. Provider configuration

The provider configuration is a directory you create anywhere on the host (this
guide uses `~/swarm/provider-configs/swarm/`). You pass its path to the launch
script via `--provider_config`. It contains:

- `config.yaml` — main Swarm configuration (required).
- `openresty.yaml` — credentials for a custom ACME provider, if you don't use Let's Encrypt (optional).
- `auth-service.yaml` — OAuth2 provider credentials for the Auth Service (optional).
- `authorized_keys` — SSH public keys to authorize for the VM (debug mode only; see the debug section near the end).


### Bootstrap node vs joining nodes

A Swarm cluster has two kinds of nodes, and the `config.yaml` differs slightly between them:

- **Bootstrap node** — the first node in the cluster. Start it on its own:
  - `swarm_db.join_addresses: []` (empty).
  - `pki_authority.caBundle` and `pki_authority.servers` are **not** required.
  - Launch `start_super_protocol.sh` with `--swarm-init true` so the VM bootstraps a new Swarm cluster instead of trying to join one.
  - After it is up, note its external IP (and the gossip port, default `7946`).
- **Joining nodes** — every subsequent node:
  - `swarm_db.join_addresses: ["<bootstrap-ip>:7946"]` (you can list several gossip endpoints).
  - `pki_authority.caBundle` and `pki_authority.servers` **must** point to the bootstrap node's PKI (e.g. `ca.swarm.<your-subdomain>.<your-domain>`).
  - `pki_authority.networkID` must match the value used on the bootstrap node.

#### Fetching `caBundle` from the bootstrap node

The bootstrap node exposes its PKI authority on port `9443`. Pull the CA bundle and paste it under `pki_authority.caBundle` on every joining node:

```bash
curl -k https://<bootstrap-ip>:9443/api/v1/pki/certs/ca
```

> To forward port `9443` from the host into the VM, start the bootstrap node
> with `--pki_port 9443` (see the flags table in section 4). Without it the PKI
> port is not forwarded.

Indent the PEM block under `caBundle: |` so the YAML stays valid, e.g.:

```yaml
pki_authority:
  caBundle: |
    -----BEGIN CERTIFICATE-----
    MIIB...
    -----END CERTIFICATE-----
```

### Component images

The images below are pulled by the VM at runtime. Tags in `config.yaml` (`swarm_node`, `swarm_cloud_api`, `swarm_cloud_ui`, `auth_service`, `pki_authority`, etc.) select which version of each image is used. The `gatekeeper_*_image` fields take a fully-qualified image reference.

| Component | Image |
|---|---|
| `swarm_node` | `ghcr.io/super-protocol/swarm-cloud/swarm-node:<tag>` |
| `swarm_cloud_api` | `ghcr.io/super-protocol/swarm-cloud/swarm-cloud-backend:<tag>` |
| `swarm_cloud_ui` | `ghcr.io/super-protocol/swarm-cloud/swarm-cloud-ui:<tag>` |
| `auth_service` | `ghcr.io/super-protocol/swarm-cloud/auth-service:<tag>` |
| `gatekeeper_s3_image` / `gatekeeper_harbor_image` | `ghcr.io/super-protocol/swarm-cloud/swarm-gatekeeper:<tag>` |
| `pki_authority` | `ghcr.io/super-protocol/tee-pki-authority-service:<tag>` (e.g. `v5.0.1`) |

The `github.token` from `config.yaml` is used to pull these images from GHCR, so it must have `read:packages` scope.

How to get the token

The token is a GitHub Personal Access Token (PAT). To create one:

- Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) (or open https://github.com/settings/tokens directly).
- Click Generate new token → Generate new token (classic).
- Give it a descriptive name (e.g. swarm-cloud-ghcr-pull) and set an expiration.
- Under scopes, select read:packages (sufficient for pulling images from GHCR).
- Click Generate token and copy the value immediately — GitHub shows it only once. Classic PATs start with ghp_.
- Make sure your account has access to the super-protocol organization's packages; otherwise the pull will fail with a 403/denied error even with the correct scope.

>Note: Fine-grained PATs can also work but require explicit per-package or per-org permission configuration; classic tokens with read:packages are simpler for this use case.

### `config.yaml` example

```yaml
github:
  token: "ghp_...H"  # token used to pull artifacts and images

tags:
  swarm_db: "v1.1.4"
  host_agent: "release-v0.15.0"
  swarm_node: "0.15.0"
  sdk: "develop"
  services: "v0.15.0"
  pki_authority: "v5.0.1"
  swarm_cloud_api: "0.15.0"
  swarm_cloud_ui: "0.15.0"
  auth_service: "0.15.0"
  gatekeeper_s3_image: "ghcr.io/super-protocol/swarm-cloud/swarm-gatekeeper:0.15.0"
  gatekeeper_harbor_image: "ghcr.io/super-protocol/swarm-cloud/swarm-gatekeeper:0.15.0"

swarm_db:
  node_name: "my-awesome-swarm-node-1"
  # advertise_addr: "11.22.33.44"   # public IP other Swarm nodes use to reach this one;
                                    # required if the host has multiple interfaces or sits behind NAT

  # Bootstrap node: leave empty.
  # Joining nodes: list one or more gossip endpoints of an already-running node, e.g.
  #   join_addresses: ["<bootstrap-ip>:7946"]
  join_addresses: []

pki_authority:
  networkID: "aaa-bbb-ccc"          # Swarm network key (must be the same on every node in the cluster)
  # caBundle and servers are required for joining nodes only.
  # On the bootstrap node you can omit them.
  caBundle: |
    -----BEGIN CERTIFICATE-----
    .....
    -----END CERTIFICATE-----
  servers:
    - "ca.swarm.<your-subdomain>.<your-domain>"

# PowerDNS (or any compatible) API used to manage DNS records for the Swarm domain.
# Point this at your own DNS API; the values below are placeholders.
powerdns_api_url: "https://<your-powerdns-api-host>"
powerdns_api_key: "<your-powerdns-api-key>"

# Base zone you control in DNS. All Swarm records are created under it.
base_domain: "<your-domain>"
# Main Swarm entry point. Must resolve to this VM (DNS is managed via the PowerDNS API above).
swarm_domain: "swarm.<your-subdomain>.<your-domain>"
pki_domain:   "ca.swarm.<your-subdomain>.<your-domain>"
```

> Replace `<your-domain>` with a domain you own, `<your-subdomain>` with the Swarm-specific label,
> and `<your-powerdns-api-host>` / `<your-powerdns-api-key>` with credentials for **your** DNS API.

### `openresty.yaml` (optional)

```yaml
EAB_KID: "<eab-kid>"
EAB_HMAC_KEY: "<eab-hmac-key>"
ACME_PROVIDER: zerossl
ACME_URL: https://acme.zerossl.com/v2/DV90
```

### `auth-service.yaml` (optional)

```yaml
oauth:
  github:
    enabled: false
    # clientId: "github-client-id"
    # clientSecret: "github-client-secret"
    # callbackUrl: "https://github-callback.com"
  google:
    enabled: false
    # clientId: "your-google-client-id"
    # clientSecret: "your-google-client-secret"
    # callbackUrl: "http://localhost:3000/auth/google/callback"
    # accessType: "offline"
    # prompt: "select_account consent"
  huggingface:
    enabled: false
  microsoft:
    enabled: false
    # tenantId: "common"
    # authority: "https://login.microsoftonline.com"
```

### Copy provider configuration to the host

If you maintain the configuration locally:

```bash
# On your local machine
scp -r ./provider-configs gp-ws-01:~/swarm/
```

Expected layout on the host:

```
~/swarm/provider-configs/swarm/
├── config.yaml
├── openresty.yaml
└── auth-service.yaml
```

## 4. Start the Swarm VM

By default the script downloads the latest already-built VM image (release) from
the Super Protocol release storage — you do **not** need to build anything.
The first launch on a fresh host therefore downloads ~11 GB into the cache
directory before the VM starts.

> If you need a locally built image instead of the released one, see
> [Building the VM image locally](#building-the-vm-image-locally-optional) at the
> end of this guide and pass `--build_dir ~/projects/sp-vm/out`.

### Create a cache directory

```bash
sudo mkdir -p /data/fixcik/sp-vm/cache
sudo chown -R "$USER:$USER" /data/fixcik/sp-vm
```

### Start a persistent terminal session

> Run the launch inside a `tmux` (or `screen`) session. The image download and
> the VM itself are long-running; if your SSH connection drops you'll lose the
> VM (and any in-progress download) together with it.
>
> ```bash
> tmux new -s swarm        # or: tmux attach -t swarm
> ```

### Launch (production)

This is the normal, production launch — the latest released VM image is
downloaded automatically, no debug flags, SSH into the VM disabled:

```bash
sudo ~/projects/sp-vm-tools/scripts/start_super_protocol.sh \
  --cores 10 \
  --mem 20 \
  --provider_config ~/swarm/provider-configs/swarm \
  --state_disk_size 50 \
  --cache /data/fixcik/sp-vm/cache \
  --ip_address <public-ip> \
  --swarm_db_gossip_port 7946 \
  --guest-cid 122 \
  --wg_port 51821 \
  --pki_port 9443 \
  --swarm-init true        # only on the bootstrap node; omit on joining nodes
```

> By default the latest release is fetched. Pin a specific build with
> `--release <name>`. Use a locally built image with `--build_dir ~/projects/sp-vm/out`
> (see the build section at the end).


> Pass `--swarm-init true` **only when starting the very first (bootstrap) node** of a new
> Swarm cluster. Joining nodes must be started without this flag (default `false`) so they
> connect to the gossip endpoints listed in `swarm_db.join_addresses`.

> Set `--ip_address` to the host's public IPv4 (the one other Swarm nodes will reach you
> on). The default `0.0.0.0` binds every interface, which is fine on a single-homed host
> but unsafe on multi-NIC machines. Whichever IP you pick must match `swarm_db.advertise_addr`
> in `config.yaml` and be the address the other nodes' `join_addresses` point to.

> The script auto-detects the confidential-computing mode from the host CPU
> (`tdx`, `sev-snp`, or `untrusted`). Override it with `--mode <tdx|sev-snp|untrusted>`
> only if auto-detection picks the wrong one.

### Key flags

| Flag | Example | Default | Description |
|---|---|---|---|
| `--cores` | `10` | `nproc − 2` | vCPUs assigned to the VM. |
| `--mem` | `20` | total RAM − 8 (GiB) | RAM in GiB. |
| `--state_disk_size` | `50` | auto (≥512) | State disk size in GiB. Auto-detected from the mount if omitted. |
| `--provider_config` | `~/swarm/provider-configs/swarm` | _(required)_ | Path to the Swarm provider config directory. |
| `--release` | `build-344` | latest | Release name to download. Omit to fetch the latest released image. |
| `--build_dir` | `./out` | _(none — downloads release)_ | Use a locally built VM image instead of downloading a release (see build section). |
| `--cache` | `/data/fixcik/sp-vm/cache` | `~/.cache/superprotocol` | Host-side cache directory (replace `fixcik`). |
| `--ip_address` | `<public-ip>` | `0.0.0.0` | Host interface to bind forwarded ports to. Set to the public IPv4 reachable by other Swarm nodes (must match `swarm_db.advertise_addr`). |
| `--wg_port` | `51821` | `51820` | Host WireGuard port forwarded to the VM (`:51820` inside). |
| `--swarm_db_gossip_port` | `7946` | `7946` | Swarm DB gossip port for inter-node clustering (requires the Swarm branch of `sp-vm-tools`). |
| `--pki_port` | `9443` | _(not forwarded)_ | Host port forwarded to the VM's PKI authority (`:9443` inside). Needed so joining nodes can fetch the `caBundle`. |
| `--dns_port` | `53` | `53` | DNS port forwarded to the VM. |
| `--http_port` | `80` | _(not forwarded)_ | Host port forwarded to the VM's HTTP (`:80`). |
| `--https_port` | `443` | _(not forwarded)_ | Host port forwarded to the VM's HTTPS (`:443`). |
| `--guest-cid` | `122` | `3` | Guest CID for vsock. |
| `--gpu` | `<id>` / `none` | all available | GPU(s) to pass through. Repeat per GPU; `none` disables passthrough. |
| `--mode` | `tdx` | auto-detected | Confidential mode: `tdx`, `sev-snp`, or `untrusted`. |
| `--swarm-init` | `true` | `false` | Bootstrap a new Swarm cluster. Pass `true` **only on the first (bootstrap) node**; omit on joining nodes. |
| `--allow-untrusted` | `true` | `false` | Allow untrusted mode. Can only be combined with `--swarm-init true`. |

## 5. Verify the VM is up

### Check the VM boot output

In production mode the VM logs to the serial console attached to your terminal
(the script runs QEMU with `-serial stdio`). Watch that output for the services
coming up.

### Verify provider configuration is mounted (from inside the VM)

> Direct shell access to the VM is only available in debug mode (see the debug
> section below). In production you observe the VM through the serial console
> and the Swarm Cloud UI / API endpoints rather than an interactive shell.

When you do have a shell (debug mode), the provider configuration is mounted under `/sp/`:

```bash
ls -la /sp/
```

Expected files in `/sp/`:

- `api.yaml`
- `gatekeeper-keys.yaml`
- `node-db.yaml`
- `sp-swarm-services.yaml`
- `authorized_keys`

## 6. Stop the VM

Find the QEMU process and shut it down:

```bash
ps aux | grep qemu-system-x86_64 | grep -v grep

# Graceful shutdown (preferred)
sudo kill <PID>

# Force kill if graceful shutdown fails
sudo kill -9 <PID>
```

## Debug mode (optional)

Debug mode is **not** needed for a normal production launch. Enable it only when
you need an interactive shell inside the VM or verbose boot logging — for
example while developing or troubleshooting.

Debug mode changes behaviour in a few ways:

- **SSH into the VM** is only available in debug mode. The `authorized_keys` /
  `ssh_public_keys` fields are honored only here; in a non-debug (production)
  image the SSH server is disabled and the keys are ignored.
- `--log_file` is **required** when `--debug true` is set, and is **only**
  allowed in debug mode (passing it without `--debug true` is an error).
- The boot output is more verbose (`systemd.log_level=trace`, `sp-debug=true`).

### Add an SSH key for the VM

Generate a key on the build host and add the public part to the provider configuration:

```bash
# On gp-ws-01
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C "$USER@gp-ws-01"

touch ~/swarm/provider-configs/swarm/authorized_keys
cat ~/.ssh/id_ed25519.pub | tee -a ~/swarm/provider-configs/swarm/authorized_keys
```

`authorized_keys` is consumed automatically when the VM starts.

### Launch in debug mode

Same as the production launch, plus `--debug true`, a `--log_file`, and an
`--ssh_port` to forward into the VM:

```bash
sudo ~/projects/sp-vm-tools/scripts/start_super_protocol.sh \
  --cores 10 \
  --mem 20 \
  --provider_config ~/swarm/provider-configs/swarm \
  --state_disk_size 50 \
  --cache /data/fixcik/sp-vm/cache \
  --ip_address <public-ip> \
  --swarm_db_gossip_port 7946 \
  --guest-cid 122 \
  --wg_port 51821 \
  --pki_port 9443 \
  --debug true \
  --log_file log.log \
  --ssh_port 2222 \
  --swarm-init true        # only on the bootstrap node; omit on joining nodes
```

Additional debug flags:

| Flag | Example | Default | Description |
|---|---|---|---|
| `--debug` | `true` | `false` | Enable verbose logging / debug image. |
| `--log_file` | `log.log` | _(none)_ | Host-side log file with VM startup output. Required in debug mode. |
| `--ssh_port` | `2222` | `2222` | Host port forwarded to VM SSH (`:22` inside, bound to `127.0.0.1` in debug mode). |
| `--argo_branch` | `main` | `main` | Argo branch used to init SP components. |
| `--argo_sp_env` | `main` | `main` | Argo environment used to init SP components. |

### SSH into the VM (debug only)

```bash
ssh -p 2222 root@localhost
```

### Check system services

```bash
systemctl status
```

## Building the VM image locally (optional)

By default the launch script downloads the latest released VM image, so most
users never need this section. Build locally only when you need a custom or
unreleased image; then pass `--build_dir ./out` to the launch command.

### Clone `sp-vm`

The image is built from the `sp-vm` repository. It depends on `swarm-cloud` via
git submodules, so a recursive clone (or `git submodule update --init
--recursive`) is mandatory.

```bash
cd ~/projects
git clone https://github.com/Super-Protocol/sp-vm
cd sp-vm
git checkout swarm                          # check the current Swarm branch
git submodule update --init --recursive
```

### Create a buildx builder with `security.insecure`

The build needs privileged operations inside Docker, so a custom builder is required.

```bash
docker buildx ls
# If insecure-builder is missing:
sudo docker buildx create --use --name insecure-builder \
  --buildkitd-flags '--allow-insecure-entitlement security.insecure'
```

### Build

```bash
cd ~/projects/sp-vm
docker buildx build -t sp-vm-swarm-test-fixcik \
  --allow security.insecure \
  --output type=local,dest=./out \
  --build-arg SP_VM_IMAGE_VERSION=build-1 \
  --build-arg S3_BUCKET=test src | cat
```

Notes:

- `--allow security.insecure` is required.
- The build typically takes 5–15 minutes depending on host performance.
- The resulting `./out/` directory contains the VM image (~11 GB) and supporting artifacts.

### Verify build artifacts

```bash
sudo ls -lh ~/projects/sp-vm/out/
```

Expected files:

- `sp-vm-build-1.img` — VM disk image (~11 GB)
- `vmlinuz` — Linux kernel for the VM
- `OVMF.fd` and `OVMF_AMD.fd` — UEFI firmware images
- `vm.json` — VM metadata
- `rootfs_hash.txt` — rootfs integrity hash

### Use the built image

Pass the build directory to the launch command from section 4 (it lives at
`~/projects/sp-vm/out`):

```bash
sudo ~/projects/sp-vm-tools/scripts/start_super_protocol.sh \
  ... \
  --build_dir ~/projects/sp-vm/out
```

## Running on GCP via Terraform

To deploy the same image to a GCP confidential VM, clone `swarm-cloud` and use the GCP example:

```bash
git clone git@github.com:Super-Protocol/swarm-cloud.git
cd swarm-cloud/examples/test-cluster/terraform-gcp-sp-vm
```

Create `terraform.tfvars`:

```hcl
project_id       = "supa-swarm"
credentials_file = "~/.gcp/supa-swarm-dd5c0c157684.json"   # GCP service account JSON
image            = "sp-vm-build-344"                        # check the latest build
region           = "europe-west4"
zone             = "europe-west4-c"                         # confidential machines aren't in every zone

auth_service_oauth_secrets_file = "./auth-service.yaml"     # optional
openresty_secrets_file          = "./openresty.yaml"        # optional
name_prefix                     = "my-swarm-test"
serial_port_enable              = false                     # enable for SSH debugging via serial (debug only)
vm_provision_model              = "spot"

# Confidential TDX machines
machine_type              = "c3-standard-4"
confidential_compute_type = "TDX"

# Confidential SEV-SNP machines (alternative)
# confidential_compute_type = "SEV_SNP"
# machine_type              = "c3d-standard-4"

data_disk_size_gb = 1000
data_disk_type    = "pd-balanced"

node_count             = 3
gpu_node_count         = 0
gpu_machine_type       = "a3-highgpu-1g"      # confidential H100
gpu_vm_provision_model = "flex-start"          # spot or flex-start

github_token        = "ghp_w...H"                       # classic token, scope: read:packages

# DNS — provide your own DNS API and a domain you control.
powerdns_api_url    = "https://<your-powerdns-api-host>"
powerdns_api_key    = "<your-powerdns-api-key>"
base_domain         = "<your-domain>"
swarm_domain        = "swarm.<your-subdomain>.<your-domain>"   # Swarm entry point

swarm_db_tag        = "v1.1.4"
swarm_node_tag      = "develop"
swarm_cloud_api_tag = "develop"
swarm_cloud_ui_tag  = "develop"
auth_service_tag    = "develop"
sdk_tag             = "develop"
host_agent_tag      = "develop"
services_tag        = "develop"
pki_authority_tag   = "v5.0.0"

gatekeeper_s3_image     = "ghcr.io/super-protocol/swarm-cloud/swarm-gatekeeper:develop"
gatekeeper_harbor_image = "ghcr.io/super-protocol/swarm-cloud/swarm-gatekeeper:develop"

# ssh_public_keys are honored only in debug images (serial_port_enable = true
# with a debug image). For a production (non-debug) deployment the SSH server
# inside the VM is disabled and these keys are ignored.
ssh_public_keys = [
  "ssh-ed25519 AAAA... user@host",
  # add additional keys here
]
```

Apply:

```bash
terraform apply -auto-approve -var-file ./terraform.tfvars
```
