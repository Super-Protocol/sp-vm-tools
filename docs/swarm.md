# Running a Super Protocol Swarm Cluster

## Choosing a path

| Path | When to use |
|---|---|
| **Single-host cluster** | Quick local test — a 3-node cluster on one machine, everything automated. |
| **Full deployment** | Production setup on one or several real hosts. |

---

## Single-host cluster (quick test)

One command brings up a full 3-node cluster on a single bootstrapped host — networking, per-node configs, and ingress are all handled for you.

**Step 1 — get the code** (skip if you already have it):

```bash
git clone https://github.com/Super-Protocol/sp-vm-tools
cd sp-vm-tools
```

All commands below assume you're inside this `sp-vm-tools` folder.

**Step 2 — prepare a config folder.** There's no downloadable template — you create these two files yourself, anywhere on the host. The folder name is up to you (`provider-template` is just an example); the `swarm/` subfolder name inside it is **not** optional:

```
provider-template/
└── swarm/
    ├── config.yaml
    └── openresty.yaml
```

Minimal `config.yaml` to get started (fill in the four placeholders):

```yaml
swarm_db:
  node_name: "my-node-1"
  join_addresses: []          # leave empty — this becomes the bootstrap node

pki_authority:
  networkID: "my-test-cluster"

base_domain: "<your-domain>"                              # a domain you actually own
swarm_domain: "swarm.<your-subdomain>.<your-domain>"
pki_domain:   "ca.swarm.<your-subdomain>.<your-domain>"

# Must resolve to this machine's public IP — either a real DNS A-record you
# control, or (for a purely local test) an /etc/hosts entry on every machine
# that will connect to the cluster.
gateway_hostname: "<this-machine-public-ip-or-hostname>"

github:
  token: "<your-github-token-with-read:packages-scope>"    # see "How to create a GitHub token" below
```

Minimal `openresty.yaml` (required, even for a quick test):

```yaml
EAB_KID: "<eab-kid>"
EAB_HMAC_KEY: "<eab-hmac-key>"
ACME_PROVIDER: zerossl
ACME_URL: https://acme.zerossl.com/v2/DV90
```

> Every field with `<angle brackets>` above must be replaced — none of them work left as-is. For the full, annotated field list (including PowerDNS credentials and what each field does) see the [Full deployment](#2-prepare-the-provider-configuration) config reference below.

**You also need:**
- A host already bootstrapped for confidential computing (TDX or SEV-SNP) — see the [main README](../README.md).
- `tmux`, `nftables`, `curl`, `nc` installed: `apt install tmux nftables curl netcat-openbsd`

> Keep `provider-template/` in its own folder — not inside `sp-vm-tools` and not inside any cache folder.

**Run it:**

```bash
# Start the cluster
sudo ./scripts/swarm-cluster.sh up --provider-config-template ./provider-template

# Check status
./scripts/swarm-cluster.sh status

# Stop everything
sudo ./scripts/swarm-cluster.sh down
```

That's it — the script generates per-node configs, starts the VMs, and sets up the gateway automatically. `--release` is optional; omit it to get the latest build, or add `--release build-370` to pin a specific one (see [releases](https://github.com/Super-Protocol/sp-vm/releases)).

<details>
<summary>Advanced: all flags and what happens under the hood</summary>

### Key flags

| Flag | Default | Description |
|---|---|---|
| `--provider-config-template` | _(required)_ | Template directory containing a `swarm/` subdirectory with `config.yaml` (and `openresty.yaml`, optionally `auth-service.yaml`). |
| `--join-cores` | `4` | vCPUs per join node. |
| `--join-mem` | `4` | RAM (GiB) per join node. |
| `--host-reserve-cores` | `4` | Cores left for the host OS. |
| `--host-reserve-mem` | `8` | RAM (GiB) left for the host OS. |
| `--state-disk-size` | auto (proportional) | State disk size per node in GiB. Auto-split from 90% of free space if omitted. |
| `--release` | latest | Pin a specific `Super-Protocol/sp-vm` release. |
| `--mode` | auto-detect | `tdx`, `sev-snp`, or `untrusted`. |
| `--debug` | `false` | Enable verbose boot log + SSH port forwards per node. |
| `--gpu-target` | `bootstrap` | Where to pass the GPU: `bootstrap` or `none`. |

The bootstrap node gets all remaining host resources after subtracting the host reserve and join nodes. Join nodes get the fixed minimums above.

### What happens under the hood

1. Detects host resources (cores, RAM, free disk) and computes allocation.
2. Generates a cluster-wide `networkID` (UUID) and a `global_id` for DNS.
3. Creates the bridge `swarmbr0` (`10.0.0.0/24`) and NAT rules via `swarm-network.sh bridge-only`.
4. Generates per-node provider configs:
   - **Bootstrap**: `join_addresses: []`, `pki_authority.servers: []`.
   - **Join nodes**: `join_addresses: ["10.0.0.10:7946"]`, `caBundle` fetched automatically from bootstrap PKI.
5. Starts each VM in its own `tmux` session (`swarm-bootstrap`, `swarm-join-1`, `swarm-join-2`), attached to the bridge via tap interfaces.
6. Waits for bootstrap gossip (7946) and PKI (9443) to become ready.
7. Fetches the CA bundle from bootstrap and injects it into join-node configs.
8. Launches join nodes.
9. Sets up HAProxy ingress: `gw.dyn.<global_id>.superprotocol.io` → bootstrap ports 80/443.

Attach to any VM's console with `tmux attach -t swarm-bootstrap` (or `swarm-join-1` / `swarm-join-2`).

</details>

---

## Full deployment

### 1. Prepare the host

SSH into the host, then clone the tooling:

```bash
ssh gp-ws-01
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/Super-Protocol/sp-vm-tools
```

The host must already be bootstrapped for confidential computing — see the main [README](../README.md).

### 2. Prepare the provider configuration

Create a folder anywhere on the host (e.g. `~/swarm/provider-configs/`) with a `swarm/` subfolder inside it. There's no downloadable template — you create `config.yaml` and `openresty.yaml` yourself:

```
provider-configs/
└── swarm/
    ├── config.yaml       # required
    └── openresty.yaml    # required
```

- `config.yaml` — the main settings: your domain, DNS API credentials, and (for a multi-node cluster) whether this is the bootstrap node or one joining an existing cluster.
- `openresty.yaml` — required credentials for issuing TLS certificates.

See the minimal fill-in-the-blanks example in [Single-host cluster](#single-host-cluster-quick-test) above, or the full annotated reference just below.

> Keep this folder separate — not inside `sp-vm-tools`, and not inside the cache folder used in step 3.
>
> **Watch the path carefully:** `start_super_protocol.sh` below takes `--provider_config` pointing at `provider-configs` (the folder **containing** `swarm/`). The GCP script in [Advanced](#advanced) instead takes `--provider-config` pointing directly at `provider-configs/swarm` — one level deeper. Copy-pasting a path between the two commands is the most common mistake here.

<details>
<summary>Full <code>config.yaml</code> example and field reference</summary>

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
  join_addresses: []                # bootstrap vs joining: see table below

pki_authority:
  networkID: "aaa-bbb-ccc"          # bootstrap vs joining: see table below
  caBundle: |                       # bootstrap vs joining: see table below
    -----BEGIN CERTIFICATE-----
    .....
    -----END CERTIFICATE-----
  servers:
    - "ca.swarm.<your-subdomain>.<your-domain>"

powerdns_api_url: "https://<your-powerdns-api-host>"
powerdns_api_key: "<your-powerdns-api-key>"

base_domain: "<your-domain>"
swarm_domain: "swarm.<your-subdomain>.<your-domain>"
pki_domain:   "ca.swarm.<your-subdomain>.<your-domain>"

# For a single-host cluster (swarm-cluster.sh), point this to your machine's IP.
gateway_hostname: "mygw.mysite.com"
```

Replace `<your-domain>`, `<your-subdomain>`, and the PowerDNS credentials with your own DNS API details.

#### Bootstrap vs joining nodes

A single bootstrap node is fine for a quick test, but a real cluster needs at least **3 nodes** (odd number, for quorum). Start the bootstrap node first, then join the rest.

| Field | Bootstrap | Joining |
|---|---|---|
| `swarm_db.advertise_addr` | this node's public IP | this node's public IP; must match `--ip_address` |
| `swarm_db.join_addresses` | `[]` | `["<bootstrap-ip>:7946"]` |
| `pki_authority.caBundle` | omit | required (fetched from bootstrap) |
| `pki_authority.servers` | omit | required (`ca.swarm.<sub>.<domain>`) |
| `pki_authority.networkID` | set | same value as bootstrap |
| `--swarm-init` (launch flag) | `true` | omit |

Fetch the CA bundle from the bootstrap node (requires `--pki_port 9443` at launch) and paste it under `caBundle: |`, keeping the PEM block indented:

```bash
curl -k https://<bootstrap-ip>:9443/api/v1/pki/certs/ca
```

#### Component images

Tags in `config.yaml` select image versions:

| Component | Image |
|---|---|
| `swarm_node` | `ghcr.io/super-protocol/swarm-cloud/swarm-node:<tag>` |
| `swarm_cloud_api` | `ghcr.io/super-protocol/swarm-cloud/swarm-cloud-backend:<tag>` |
| `swarm_cloud_ui` | `ghcr.io/super-protocol/swarm-cloud/swarm-cloud-ui:<tag>` |
| `auth_service` | `ghcr.io/super-protocol/swarm-cloud/auth-service:<tag>` |
| `gatekeeper_s3_image` / `gatekeeper_harbor_image` | `ghcr.io/super-protocol/swarm-cloud/swarm-gatekeeper:<tag>` |
| `pki_authority` | `ghcr.io/super-protocol/tee-pki-authority-service:<tag>` |

`github.token` needs `read:packages` scope to pull these from GHCR.

</details>

<details>
<summary>How to create a GitHub token for <code>github.token</code></summary>

- Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic), or open https://github.com/settings/tokens directly.
- Click Generate new token → Generate new token (classic).
- Give it a descriptive name and an expiration.
- Under scopes, select `read:packages` and the whole `repo` scope.
- Generate and copy it immediately — GitHub shows it only once. Classic PATs start with `ghp_`.
- Make sure your account has access to the `super-protocol` organization's packages, or the pull will fail with 403 even with the right scope.

</details>

<details>
<summary><code>openresty.yaml</code> and <code>auth-service.yaml</code> contents</summary>

`openresty.yaml` (required) — ACME/TLS credentials:

```yaml
EAB_KID: "<eab-kid>"
EAB_HMAC_KEY: "<eab-hmac-key>"
ACME_PROVIDER: zerossl
ACME_URL: https://acme.zerossl.com/v2/DV90
```

`auth-service.yaml` (optional) — OAuth2 login providers, all disabled by default:

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
  huggingface:
    enabled: false
  microsoft:
    enabled: false
    # tenantId: "common"
    # authority: "https://login.microsoftonline.com"
```

If you edit these locally, copy the whole folder to the host:

```bash
scp -r ./provider-configs gp-ws-01:~/swarm/
```

Expected layout on the host: `~/swarm/provider-configs/swarm/{config.yaml, openresty.yaml, auth-service.yaml}`.

</details>

### 3. Start the VM

```bash
sudo mkdir -p /data/sp-vm/cache
sudo chown -R "$USER:$USER" /data/sp-vm

tmux new -s swarm

sudo ~/projects/sp-vm-tools/scripts/start_super_protocol.sh \
  --cores 10 \
  --mem 20 \
  --provider_config ~/swarm/provider-configs \
  --state_disk_size 50 \
  --cache /data/sp-vm/cache \
  --ip_address <public-ip> \
  --swarm_db_gossip_port 7946 \
  --pki_port 9443 \
  --swarm-init true        # only on the bootstrap node; omit on joining nodes
```

The first run downloads the VM image (~11 GB), so it takes a while — that's why it runs inside `tmux`, so it survives an SSH disconnect. Set `--ip_address` to the host's real public IP (must match `advertise_addr` in `config.yaml`).

<details>
<summary>All launch flags</summary>

| Flag | Example | Default | Description |
|---|---|---|---|
| `--cores` | `10` | `nproc − 2` | vCPUs assigned to the VM. |
| `--mem` | `20` | total RAM − 8 (GiB) | RAM in GiB. |
| `--state_disk_size` | `50` | auto (≥512) | State disk size in GiB. |
| `--provider_config` | `~/swarm/provider-configs` | _(required)_ | Path to the folder containing the `swarm/` subdirectory. |
| `--release` | `build-344` | latest | Pin a specific release. |
| `--build_dir` | `./out` | _(downloads release)_ | Use a locally built image instead (see "Building the VM image locally" below). |
| `--cache` | `/data/sp-vm/cache` | `~/.cache/superprotocol` | Host-side download/build cache. |
| `--ip_address` | `<public-ip>` | `0.0.0.0` | Host interface for forwarded ports; must match `advertise_addr`. |
| `--wg_port` | `51821` | `51820` | Host WireGuard port. |
| `--swarm_db_gossip_port` | `7946` | `7946` | Swarm DB gossip port. |
| `--pki_port` | `9443` | _(not forwarded)_ | Needed so joining nodes can fetch the `caBundle`. |
| `--dns_port` | `53` | `53` | DNS port. |
| `--http_port` | `80` | _(not forwarded)_ | HTTP port. |
| `--https_port` | `443` | _(not forwarded)_ | HTTPS port. |
| `--guest-cid` | `122` | `3` | Guest CID for vsock. |
| `--gpu` | `<id>` / `none` | all available | GPU(s) to pass through. |
| `--mode` | `tdx` | auto-detected | `tdx`, `sev-snp`, or `untrusted`. |
| `--swarm-init` | `true` | `false` | Bootstrap a new cluster (see table above). |
| `--allow-untrusted` | `true` | `false` | Only combined with `--swarm-init true`. |

</details>

### 4. Check it's running

Watch the serial console in your terminal for services starting up. Once running, the Swarm Cloud UI and API become reachable at your `gateway_hostname`.

### 5. Stop the VM

```bash
ps aux | grep qemu-system-x86_64 | grep -v grep
sudo kill <PID>          # graceful
sudo kill -9 <PID>       # force, if needed
```

---

## Advanced

<details>
<summary>Building the VM image locally (only if you need a custom/unreleased image)</summary>

**Clone `sp-vm`** (recursively — it depends on `swarm-cloud` via submodules):

```bash
cd ~/projects
git clone https://github.com/Super-Protocol/sp-vm
cd sp-vm
git submodule update --init --recursive
```

**Create a privileged buildx builder:**

```bash
docker buildx ls
# If insecure-builder is missing:
sudo docker buildx create --use --name insecure-builder \
  --buildkitd-flags '--allow-insecure-entitlement security.insecure'
```

**Build:**

```bash
cd ~/projects/sp-vm
docker buildx build -t sp-vm-swarm-test \
  --allow security.insecure \
  --output type=local,dest=./out \
  --build-arg SP_VM_IMAGE_VERSION=build-1 \
  --build-arg S3_BUCKET=test src | cat
```

Takes 5–15 minutes. `./out/` will contain `sp-vm-build-1.img` (~11 GB), `vmlinuz`, `OVMF.fd` / `OVMF_AMD.fd`, `vm.json`, `rootfs_hash.txt`.

Use it with `--build_dir ~/projects/sp-vm/out` on the launch command from step 3.

</details>

<details>
<summary>Running on GCP via Terraform</summary>

```bash
git clone git@github.com:Super-Protocol/swarm-cloud.git
cd swarm-cloud/examples/test-cluster/terraform-gcp-sp-vm
```

Create `terraform.tfvars`:

```hcl
project_id       = "supa-swarm"
credentials_file = "~/.gcp/supa-swarm-dd5c0c157684.json"
image            = "sp-vm-build-344"
region           = "europe-west4"
zone             = "europe-west4-c"

auth_service_oauth_secrets_file = "./auth-service.yaml"     # optional
openresty_secrets_file          = "./openresty.yaml"        # required
name_prefix                     = "my-swarm-test"
serial_port_enable              = false
vm_provision_model              = "spot"

machine_type              = "c3-standard-4"
confidential_compute_type = "TDX"
# confidential_compute_type = "SEV_SNP"
# machine_type              = "c3d-standard-4"

data_disk_size_gb = 1000
data_disk_type    = "pd-balanced"

node_count             = 3
gpu_node_count         = 0
gpu_machine_type       = "a3-highgpu-1g"
gpu_vm_provision_model = "flex-start"

github_token        = "ghp_w...H"

powerdns_api_url    = "https://<your-powerdns-api-host>"
powerdns_api_key    = "<your-powerdns-api-key>"
base_domain         = "<your-domain>"
swarm_domain        = "swarm.<your-subdomain>.<your-domain>"

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

# Only honored in debug images; ignored in production.
ssh_public_keys = [
  "ssh-ed25519 AAAA... user@host",
]
```

```bash
terraform apply -auto-approve -var-file ./terraform.tfvars
```

</details>

<details>
<summary>Running on GCP via <code>run_custom_conf_vm.sh</code> (alternative to Terraform)</summary>

Requires a locally built raw image (see "Building the VM image locally" above) — a downloaded release is a packaged QEMU image, not a raw disk.

**Prerequisites:** `gcloud`/`gsutil` authenticated, `tar` and `jq` installed, `pigz` recommended.

```bash
cd ~/projects/sp-vm-tools/scripts

./run_custom_conf_vm.sh \
  --image sp-cloud-image \
  --vm sev-snp-swarm-test \
  --zone us-central1-a \
  --machine-type n2d-standard-4 \
  --confidential-compute-type SEV_SNP \
  --run-type spot \
  --data-disk-size 50GB \
  --data-disk-type pd-standard \
  --provider-config ~/swarm/provider-configs/swarm
```

`PROJECT_ID` defaults to `supa-swarm` (override via env var). Image tarball goes to `--bucket` (default `gs://supa-swarm-bucket-conf-vms`).

| Flag | Description |
|---|---|
| `--raw` | Locally built raw disk to pack into a GCE image. |
| `--image` | GCE image name (reused if it exists). |
| `--vm` | Instance name. |
| `--zone` | Confidential machines aren't in every zone. |
| `--machine-type` | `n2d-*` for SEV-SNP, `c3-*` for TDX. |
| `--confidential-compute-type` | `SEV_SNP`, `TDX`, `SEV`, or `NONE`. |
| `--run-type` | `spot` or `flex-start`. |
| `--accelerator-type` | Optional GPU type. |
| `--data-disk-size` | State disk size, e.g. `50GB`. |
| `--provider-config` | Local `swarm/` folder; uploaded to GCS, mounted via s3fs. |
| `--force-overwrite` | Recreate image, VM, and disk. |
| `--dry-run` | Print commands without executing. |

After launch:

```bash
gcloud compute connect-to-serial-port <vm> --project <project> --port 1 --zone <zone>
gcloud compute ssh root@<vm> --project <project> --zone <zone>   # debug images only
```

</details>

<details>
<summary>Debug mode (interactive shell / verbose logs)</summary>

Not needed for normal use — only for development/troubleshooting. In debug mode, SSH into the VM is enabled and boot logs are verbose.

**Add your SSH key:**

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N '' -C "$USER@gp-ws-01"
touch ~/swarm/provider-configs/swarm/authorized_keys
cat ~/.ssh/id_ed25519.pub | tee -a ~/swarm/provider-configs/swarm/authorized_keys
```

**Launch with debug flags added** (`--debug true` requires `--log_file`):

```bash
sudo ~/projects/sp-vm-tools/scripts/start_super_protocol.sh \
  --cores 10 --mem 20 \
  --provider_config ~/swarm/provider-configs \
  --state_disk_size 50 \
  --cache /data/sp-vm/cache \
  --ip_address <public-ip> \
  --swarm_db_gossip_port 7946 \
  --pki_port 9443 \
  --debug true \
  --log_file log.log \
  --ssh_port 2222 \
  --swarm-init true
```

**Connect and check:**

```bash
ssh -p 2222 root@localhost
systemctl status
ls -la /sp/   # config files mounted here: api.yaml, gatekeeper-keys.yaml, node-db.yaml, sp-swarm-services.yaml, authorized_keys
```

</details>