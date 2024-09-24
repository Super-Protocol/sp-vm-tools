# SP-TDX-Deploy

This repository contains scripts for building and installing software to support Intel TDX on servers running Ubuntu 24.04.

## Steps for Deployment

### 1. Build the Package

To build the package, use the `build_in_docker.sh` script. If you haven't installed Docker yet, follow the installation guide [here](https://docs.docker.com/engine/install/ubuntu/).

Once the build is complete, the resulting package will be located at `build/package.tar.gz`. Alternatively, you can download the latest pre-built package from the [releases page](https://github.com/Super-Protocol/sp-tdx-deploy/releases).

### 2. Run the Installation Script

As `root`, execute the `scripts/bootstrap.sh` installation script, passing the path to the `package.tar.gz` file as an argument.

### 3. Reboot the Server

After the installation completes, reboot the server to apply the changes.
