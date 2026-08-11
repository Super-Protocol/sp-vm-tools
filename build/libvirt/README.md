# Local libvirt packages

This directory contains a parameterized Docker build environment for Ubuntu
24.04 and Ubuntu 26.04. The build uses the Debian libvirt packaging and resolves
all build dependencies inside the target Ubuntu image.

The default source is the `debian/12.5.0-1` tag from the Debian libvirt Salsa
repository. Resulting packages have a local version such as:

```text
12.5.0-1spvm1~ubuntu24.04.1
12.5.0-1spvm1~ubuntu26.04.1
```

The local suffix sorts after older upstream versions but before a future
`12.5.0-1ubuntu*` package, so an official build of the same upstream release
can replace it normally.

## Requirements

- Docker with access to the Docker daemon;
- Internet access for the Ubuntu repositories and Debian Salsa;
- an amd64 host, or a Docker setup capable of building `linux/amd64` images;
- enough free space for two builder images and package artifacts.

## Manual build

Run from the repository root:

```bash
./build/libvirt/build.sh ubuntu24
./build/libvirt/build.sh ubuntu26
```

Build both targets sequentially:

```bash
./build/libvirt/build.sh all
```

Package tests are enabled by default. For a faster development build that only
compiles and packages libvirt:

```bash
./build/libvirt/build.sh all --skip-tests
```

The script supports alternative upstream/local revisions, for example:

```bash
./build/libvirt/build.sh ubuntu24 \
    --libvirt-version 12.5.0 \
    --debian-revision 1 \
    --spvm-revision 2
```

Use `./build/libvirt/build.sh --help` to see all options.

## Artifacts

Packages are placed in a target- and version-specific directory:

```text
build/libvirt/out/ubuntu-24.04/12.5.0-1spvm1~ubuntu24.04.1/
build/libvirt/out/ubuntu-26.04/12.5.0-1spvm1~ubuntu26.04.1/
```

Each directory contains the split libvirt `.deb` packages, debug `.ddeb`
packages, `.changes`, `.buildinfo`, and `SHA256SUMS`. Do not mix packages built
for different Ubuntu releases.

The build script only creates local, unsigned packages. It does not install
them, create an APT repository, or build QEMU and python3-libvirt. Before using
`scripts/start_super_protocol_libvirt.sh`, run the matching host bootstrap; the
runtime also requires `python3-libvirt`, `passt`, `acl`, and libvirt 12.1 or
newer for GPU passthrough.

The `Build packages self-hosted` GitHub Actions workflow can build either
Ubuntu target and publish the result to a prerelease. Select
`libvirt-ubuntu24` or `libvirt-ubuntu26` in the workflow dispatch form. The
release contains one compressed tar archive:

```text
libvirt-ubuntu24.tar.gz
libvirt-ubuntu26.tar.gz
```

Each archive preserves the target and package-version directories and contains
the complete package output together with its `SHA256SUMS` file.
