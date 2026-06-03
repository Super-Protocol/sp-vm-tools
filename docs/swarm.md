# Running a Super Protocol Swarm VM

This guide walks through the full Swarm VM lifecycle on a confidential host: preparing the environment, populating the provider configuration, building the VM image, starting and verifying the VM, and stopping it. It is written around the `gp-ws-01` build server but works on any host that satisfies the prerequisites.

It also includes a short section on launching the same image in Google Cloud via Terraform.

> Throughout the guide, replace `fixcik` with your own username on the build host.

## Prerequisites

- SSH access to the build host (e.g. `gp-ws-01`).
- Docker with `buildx` enabled and your user in the `docker` group.
- The host bootstrapped for confidential computing — see the main [README](../README.md) (`scripts/bootstrap_tdx.sh` or `scripts/bootstrap_snp.sh`).
- A populated `provider-configs/swarm` directory (see below).

## 1. Connect to the host

```bash
ssh gp-ws-01
```

> Run the rest of this guide inside a `tmux` (or `screen`) session. The image
> build and the VM itself are long-running; if your SSH connection drops you'll
> lose the build or the VM together with it.
>
> ```bash
> tmux new -s swarm        # or: tmux attach -t swarm
> ```

## 2. Clone the repositories

`sp-vm` depends on `swarm-cloud` via git submodules, so a recursive clone (or `git submodule update --init --recursive`) is mandatory.

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/Super-Protocol/sp-vm
git clone https://github.com/Super-Protocol/sp-vm-tools

cd sp-vm
git checkout swarm                          # check the current Swarm branch
git submodule update --init --recursive

cd ../sp-vm-tools
# `main` already contains the Swarm bits
```

## 3. Provider configuration

The Swarm provider configuration lives in `sp-vm/provider-configs/swarm/` and contains:

- `config.yaml` — main Swarm configuration (required).
- `openresty.yaml` — credentials for a custom ACME provider, if you don't use Let's Encrypt (optional).
- `auth-service.yaml` — OAuth2 provider credentials for the Auth Service (optional).
- `authorized_keys` — SSH public keys to authorize for the VM (created in the next step).

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
scp -r ./provider-configs gp-ws-01:~/projects/sp-vm/
```

Expected layout on the host:

```
~/projects/sp-vm/provider-configs/swarm/
├── config.yaml
├── openresty.yaml
└── auth-service.yaml
```

## 4. SSH access to the VM

> **Debug mode only.** SSH into the VM (and therefore the `authorized_keys` /
> `ssh_public_keys` fields) is only available when the VM is started in debug
> mode — i.e. with `--debug true` for `start_super_protocol.sh`, or
> `serial_port_enable = true` together with a debug image in the Terraform
> flow. In a non-debug (production) image the SSH server is disabled and the
> keys are ignored.

Generate a key on the build host and add the public part to the provider configuration:

```bash
# On gp-ws-01
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C "$USER@gp-ws-01"

sudo touch ~/projects/sp-vm/provider-configs/swarm/authorized_keys
sudo chown "$USER:$USER" ~/projects/sp-vm/provider-configs/swarm/authorized_keys
cat ~/.ssh/id_ed25519.pub | tee -a ~/projects/sp-vm/provider-configs/swarm/authorized_keys
```

`authorized_keys` is consumed automatically when the VM starts.

## 5. Build the Swarm VM image

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

## 6. Start the Swarm VM

### Create a cache directory

```bash
sudo mkdir -p /data/fixcik/sp-vm/cache
sudo chown -R "$USER:$USER" /data/fixcik/sp-vm
```

### Launch

Run from `~/projects/sp-vm`:

```bash
sudo ../sp-vm-tools/scripts/start_super_protocol.sh \
  --cores 10 \
  --mem 20 \
  --provider_config ./provider-configs \
  --state_disk_size 50 \
  --build_dir ./out \
  --cache /data/fixcik/sp-vm/cache \
  --ip_address <public-ip> \
  --ssh_port 2222 \
  --swarm_db_gossip_port 7946 \
  --guest-cid 122 \
  --wg_port 51821 \
  --swarm-init true        # only on the bootstrap node; omit on joining nodes
```

> Pass `--swarm-init true` **only when starting the very first (bootstrap) node** of a new
> Swarm cluster. Joining nodes must be started without this flag (default `false`) so they
> connect to the gossip endpoints listed in `swarm_db.join_addresses`.

> Set `--ip_address` to the host's public IPv4 (the one other Swarm nodes will reach you
> on). The default `0.0.0.0` binds every interface, which is fine on a single-homed host
> but unsafe on multi-NIC machines. Whichever IP you pick must match `swarm_db.advertise_addr`
> in `config.yaml` and be the address the other nodes' `join_addresses` point to.

### Key flags

| Flag | Value | Description |
|---|---|---|
| `--cores` | `10` | vCPUs assigned to the VM. |
| `--mem` | `20` | RAM in GiB. |
| `--state_disk_size` | `50` | State disk size in GiB. |
| `--provider_config` | `./provider-configs/swarm` | Path to the Swarm provider config inside `sp-vm`. |
| `--build_dir` | `./out` | Directory with the built VM image and artifacts. |
| `--cache` | `/data/fixcik/sp-vm/cache` | Host-side cache directory (replace `fixcik`). |
| `--debug` | `true` | Verbose logging / debug mode. |
| `--log_file` | `log.log` | Host-side log file with VM startup output. |
| `--ssh_port` | `2222` | Host port forwarded to VM SSH. |
| `--guest-cid` | `122` | Guest CID for vsock. |
| `--wg_port` | `51821` | Host WireGuard port forwarded to the VM. |
| `--swarm_db_gossip_port` | `7946` | Swarm DB gossip port for inter-node clustering (requires the Swarm branch of `sp-vm-tools`). |
| `--ip_address` | `<public-ip>` | Host interface to bind forwarded ports to. Default `0.0.0.0` binds every interface; set to the public IPv4 reachable by other Swarm nodes (must match `swarm_db.advertise_addr`). |
| `--swarm-init` | `true` | Bootstrap a new Swarm cluster. Pass `true` **only on the first (bootstrap) node**; omit (or set `false`) on joining nodes. |

## 7. Verify the VM is up

### SSH into the VM

```bash
ssh -p 2222 root@localhost
```

### Check system services

```bash
systemctl status
```

### Verify provider configuration is mounted

```bash
ls -la /sp/
```

Expected files in `/sp/`:

- `api.yaml`
- `gatekeeper-keys.yaml`
- `node-db.yaml`
- `sp-swarm-services.yaml`
- `authorized_keys`

## 8. Stop the VM

Find the QEMU process and shut it down:

```bash
ps aux | grep qemu-system-x86_64 | grep -v grep

# Graceful shutdown (preferred)
sudo kill <PID>

# Force kill if graceful shutdown fails
sudo kill -9 <PID>
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
serial_port_enable              = false                     # enable for SSH debugging via serial
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

ssh_public_keys = [
  "ssh-ed25519 AAAA... user@host",
  # add additional keys here
]
# NOTE: ssh_public_keys are honored only in debug images. For a production
# (non-debug) deployment the SSH server inside the VM is disabled and these
# keys are ignored.
```

Apply:

```bash
terraform apply -auto-approve -var-file ./terraform.tfvars
```
