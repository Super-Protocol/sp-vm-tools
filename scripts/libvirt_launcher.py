#!/usr/bin/env python3
"""Build and launch a Super Protocol VM through libvirt.

The XML builder intentionally has no dependency on python-libvirt so it can be
unit tested on development machines.  The binding is imported only when a VM
is actually launched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import sys
import termios
import threading
import tty
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO, Iterable, Optional


QEMU_NS = "http://libvirt.org/schemas/domain/qemu/1.0"
LIBVIRT_IOMMUFD_VERSION = 12_001_000
DOMAIN_NAME_RE = re.compile(r"^[A-Za-z0-9_.+:-]+$")
BDF_RE = re.compile(
    r"^(?:(?P<domain>[0-9A-Fa-f]{4}):)?"
    r"(?P<bus>[0-9A-Fa-f]{2}):(?P<slot>[0-9A-Fa-f]{2})\."
    r"(?P<function>[0-7])$"
)
MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


@dataclass(frozen=True)
class HostDevice:
    kind: str
    bdf: str


@dataclass
class DomainConfig:
    name: str
    mode: str
    emulator: str
    memory_gib: int
    vcpus: int
    bios: str
    kernel: str
    kernel_cmdline: str
    rootfs: str
    state_disk: str
    provider_config_disk: str
    guest_cid: int
    qgs_cid: int
    mac_address: str
    netdev_mode: str
    debug: bool = False
    log_file: Optional[str] = None
    cpu_model: Optional[str] = None
    phys_bits: Optional[int] = None
    cbitpos: Optional[int] = None
    bridge: Optional[str] = None
    tap_iface: Optional[str] = None
    ip_address: str = "0.0.0.0"
    ssh_port: Optional[int] = None
    wg_port: Optional[int] = None
    http_port: Optional[int] = None
    https_port: Optional[int] = None
    pki_port: Optional[int] = None
    pki_vm_measure_port: Optional[int] = None
    swarm_db_gossip_port: Optional[int] = None
    dns_port: Optional[int] = None
    host_devices: list[HostDevice] = field(default_factory=list)


def _sub(parent: ET.Element, tag: str, text: Optional[str] = None, **attrs: Any) -> ET.Element:
    element = ET.SubElement(
        parent,
        tag,
        {key.rstrip("_"): str(value) for key, value in attrs.items() if value is not None},
    )
    if text is not None:
        element.text = str(text)
    return element


def _parse_bdf(bdf: str) -> dict[str, str]:
    match = BDF_RE.fullmatch(bdf)
    if not match:
        raise ValueError(f"invalid PCI BDF: {bdf!r}")
    parts = match.groupdict(default="0000")
    return {
        "domain": f"0x{parts['domain'].lower()}",
        "bus": f"0x{parts['bus'].lower()}",
        "slot": f"0x{parts['slot'].lower()}",
        "function": f"0x{parts['function'].lower()}",
    }


def _validate_config(config: DomainConfig) -> None:
    if not DOMAIN_NAME_RE.fullmatch(config.name):
        raise ValueError(
            "domain name may contain only letters, digits, '.', '_', '+', ':', and '-'"
        )
    if config.mode not in {"untrusted", "tdx", "sev-snp"}:
        raise ValueError(f"unsupported VM mode: {config.mode}")
    if config.netdev_mode not in {"user", "tap"}:
        raise ValueError(f"unsupported network mode: {config.netdev_mode}")
    if config.memory_gib < 1 or config.vcpus < 1:
        raise ValueError("memory and vCPU count must be positive")
    if config.guest_cid < 3 or config.qgs_cid < 2:
        raise ValueError("guest CID must be >= 3 and QGS CID must be >= 2")
    if not MAC_RE.fullmatch(config.mac_address):
        raise ValueError(f"invalid MAC address: {config.mac_address!r}")
    for path in (
        config.emulator,
        config.bios,
        config.kernel,
        config.rootfs,
        config.state_disk,
        config.provider_config_disk,
    ):
        if not Path(path).is_absolute():
            raise ValueError(f"libvirt resource path must be absolute: {path!r}")
    ports = (
        config.ssh_port,
        config.wg_port,
        config.http_port,
        config.https_port,
        config.pki_port,
        config.pki_vm_measure_port,
        config.swarm_db_gossip_port,
        config.dns_port,
    )
    if any(port is not None and not 1 <= port <= 65535 for port in ports):
        raise ValueError("network ports must be between 1 and 65535")
    if config.netdev_mode == "tap" and (not config.bridge or not config.tap_iface):
        raise ValueError("tap mode requires bridge and tap interface")
    if config.mode == "sev-snp":
        if config.cbitpos is None or config.phys_bits is None or not config.cpu_model:
            raise ValueError("SEV-SNP requires cbitpos, phys_bits, and cpu_model")
    if config.debug and not config.log_file:
        raise ValueError("debug mode requires a log file")
    for device in config.host_devices:
        if device.kind not in {"gpu", "aux"}:
            raise ValueError(f"unsupported host device kind: {device.kind}")
        _parse_bdf(device.bdf)


def _add_disk(
    devices: ET.Element,
    path: str,
    target: str,
    image_format: str,
    readonly: bool,
) -> None:
    disk = _sub(devices, "disk", type="file", device="disk")
    _sub(disk, "driver", name="qemu", type=image_format)
    _sub(disk, "source", file=path)
    _sub(disk, "target", dev=target, bus="virtio")
    if readonly:
        _sub(disk, "readonly")


def _add_port_forward(
    interface: ET.Element,
    protocol: str,
    host_port: Optional[int],
    guest_port: int,
    address: Optional[str] = None,
) -> None:
    if host_port is None:
        return
    attrs: dict[str, Any] = {"proto": protocol}
    if address and address != "0.0.0.0":
        attrs["address"] = address
    forward = _sub(interface, "portForward", **attrs)
    range_attrs: dict[str, Any] = {"start": host_port}
    if host_port != guest_port:
        range_attrs["to"] = guest_port
    _sub(forward, "range", **range_attrs)


def _add_passt_interface(
    devices: ET.Element,
    config: DomainConfig,
    *,
    debug_only: bool = False,
) -> ET.Element:
    interface = _sub(devices, "interface", type="user")
    _sub(interface, "backend", type="passt")
    if not debug_only:
        _sub(interface, "mac", address=config.mac_address)
    _sub(interface, "model", type="virtio")

    if debug_only:
        _add_port_forward(interface, "tcp", config.ssh_port, 22, "127.0.0.1")
        return interface

    _add_port_forward(interface, "tcp", config.http_port, 80, config.ip_address)
    _add_port_forward(interface, "tcp", config.https_port, 443, config.ip_address)
    _add_port_forward(interface, "tcp", config.pki_port, 9443, config.ip_address)
    _add_port_forward(
        interface,
        "tcp",
        config.pki_vm_measure_port,
        9180,
        config.ip_address,
    )
    _add_port_forward(interface, "udp", config.wg_port, 51820, config.ip_address)
    _add_port_forward(
        interface,
        "udp",
        config.swarm_db_gossip_port,
        7946,
        config.ip_address,
    )
    _add_port_forward(
        interface,
        "tcp",
        config.swarm_db_gossip_port,
        7946,
        config.ip_address,
    )
    _add_port_forward(interface, "udp", config.dns_port, 53, config.ip_address)
    _add_port_forward(interface, "tcp", config.dns_port, 53, config.ip_address)
    if config.debug:
        _add_port_forward(interface, "tcp", config.ssh_port, 22, "127.0.0.1")
    return interface


def _add_network(devices: ET.Element, config: DomainConfig) -> None:
    if config.netdev_mode == "user":
        _add_passt_interface(devices, config)
        return

    interface = _sub(devices, "interface", type="ethernet")
    _sub(interface, "mac", address=config.mac_address)
    _sub(interface, "target", dev=config.tap_iface, managed="no")
    _sub(interface, "model", type="virtio")
    if config.debug:
        _add_passt_interface(devices, config, debug_only=True)


def _add_host_devices(devices: ET.Element, config: DomainConfig) -> None:
    for index, host_device in enumerate(config.host_devices, start=1):
        controller = _sub(
            devices,
            "controller",
            type="pci",
            index=index,
            model="pcie-root-port",
        )
        _sub(controller, "target", chassis=index, port=hex(0x0F + index))

        hostdev = _sub(devices, "hostdev", mode="subsystem", type="pci", managed="no")
        _sub(hostdev, "driver", name="vfio", iommufd="yes")
        source = _sub(hostdev, "source")
        _sub(source, "address", **_parse_bdf(host_device.bdf))
        if host_device.kind == "gpu":
            _sub(hostdev, "rom", bar="off")
        _sub(
            hostdev,
            "address",
            type="pci",
            domain="0x0000",
            bus=hex(index),
            slot="0x00",
            function="0x0",
        )


def _add_cpu_and_features(domain: ET.Element, config: DomainConfig) -> None:
    features = _sub(domain, "features")
    _sub(features, "acpi")
    if config.mode in {"untrusted", "tdx"}:
        _sub(features, "ioapic", driver="qemu")
    if config.mode == "untrusted":
        _sub(features, "pmu", state="off")
    if config.mode == "sev-snp":
        _sub(features, "vmport", state="off")

    if config.mode == "sev-snp":
        cpu = _sub(domain, "cpu", mode="custom", match="exact", check="none")
        _sub(cpu, "model", config.cpu_model, fallback="forbid")
        _sub(cpu, "maxphysaddr", mode="emulate", bits=config.phys_bits)
    else:
        cpu = _sub(domain, "cpu", mode="host-passthrough", migratable="off")
        if config.mode == "untrusted":
            _sub(cpu, "feature", policy="disable", name="kvm-steal-time")
    _sub(
        cpu,
        "topology",
        sockets="1",
        dies="1",
        clusters="1",
        cores=config.vcpus,
        threads="1",
    )


def _qemu_commandline(domain: ET.Element) -> ET.Element:
    ET.register_namespace("qemu", QEMU_NS)
    commandline = domain.find(f"{{{QEMU_NS}}}commandline")
    if commandline is None:
        commandline = _sub(domain, f"{{{QEMU_NS}}}commandline")
    return commandline


def _add_fw_cfg_qemu_args(domain: ET.Element) -> None:
    # Libvirt deliberately rejects opt/ovmf/* through native fwcfg XML because
    # that namespace is reserved for OVMF.  The direct launcher needs this
    # existing OVMF knob, so pass it through QEMU's command line namespace.
    commandline = _qemu_commandline(domain)
    _sub(commandline, f"{{{QEMU_NS}}}arg", value="-fw_cfg")
    _sub(
        commandline,
        f"{{{QEMU_NS}}}arg",
        value="name=opt/ovmf/X-PciMmio64,string=262144",
    )


def _add_tdx_qemu_args(domain: ET.Element, config: DomainConfig) -> None:
    commandline = _qemu_commandline(domain)
    _sub(commandline, f"{{{QEMU_NS}}}arg", value="-object")
    _sub(
        commandline,
        f"{{{QEMU_NS}}}arg",
        value=f"memory-backend-ram,id=sp-mem,size={config.memory_gib}G",
    )
    tdx_object = {
        "qom-type": "tdx-guest",
        "id": "sp-tdx",
        "quote-generation-socket": {
            "type": "vsock",
            "cid": str(config.qgs_cid),
            "port": "4050",
        },
    }
    _sub(commandline, f"{{{QEMU_NS}}}arg", value="-object")
    _sub(
        commandline,
        f"{{{QEMU_NS}}}arg",
        value=json.dumps(tdx_object, separators=(",", ":")),
    )
    _sub(commandline, f"{{{QEMU_NS}}}arg", value="-machine")
    _sub(
        commandline,
        f"{{{QEMU_NS}}}arg",
        value="confidential-guest-support=sp-tdx,memory-backend=sp-mem",
    )


def build_domain_xml(config: DomainConfig) -> str:
    """Return a complete transient libvirt domain definition."""
    _validate_config(config)

    domain = ET.Element("domain", {"type": "kvm"})
    _sub(domain, "name", config.name)
    _sub(domain, "memory", config.memory_gib, unit="GiB")
    _sub(domain, "currentMemory", config.memory_gib, unit="GiB")
    _sub(domain, "vcpu", config.vcpus, placement="static")

    os_element = _sub(domain, "os")
    _sub(os_element, "type", "hvm", arch="x86_64", machine="q35")
    _sub(os_element, "loader", config.bios, readonly="yes", type="rom")
    _sub(os_element, "kernel", config.kernel)
    _sub(os_element, "cmdline", config.kernel_cmdline)

    _add_cpu_and_features(domain, config)
    _sub(domain, "clock", offset="utc")
    _sub(domain, "on_poweroff", "destroy")
    _sub(domain, "on_reboot", "restart")
    _sub(domain, "on_crash", "destroy")

    devices = _sub(domain, "devices")
    _sub(devices, "emulator", config.emulator)
    _add_disk(devices, config.rootfs, "vda", "raw", True)
    _add_disk(devices, config.state_disk, "vdb", "qcow2", False)
    _add_disk(devices, config.provider_config_disk, "vdc", "raw", True)
    _sub(devices, "controller", type="pci", index="0", model="pcie-root")
    _sub(devices, "controller", type="usb", model="none")
    _add_network(devices, config)

    serial = _sub(devices, "serial", type="pty")
    _sub(serial, "target", type="isa-serial", port="0")
    console = _sub(devices, "console", type="pty")
    _sub(console, "target", type="serial", port="0")
    video = _sub(devices, "video")
    _sub(video, "model", type="none")
    _sub(devices, "audio", id="1", type="none")
    _sub(devices, "memballoon", model="none")
    vsock = _sub(devices, "vsock", model="virtio")
    _sub(vsock, "cid", auto="no", address=config.guest_cid)
    _add_host_devices(devices, config)
    _add_fw_cfg_qemu_args(domain)

    if config.mode == "sev-snp":
        launch_security = _sub(
            domain,
            "launchSecurity",
            type="sev-snp",
            kernelHashes="yes",
        )
        _sub(launch_security, "cbitpos", config.cbitpos)
        _sub(launch_security, "reducedPhysBits", "1")
        _sub(launch_security, "policy", "0x30000")
    elif config.mode == "tdx":
        _add_tdx_qemu_args(domain, config)

    ET.indent(domain, space="  ")
    return ET.tostring(domain, encoding="unicode")


def _version_string(version: int) -> str:
    return f"{version // 1_000_000}.{(version // 1_000) % 1_000}.{version % 1_000}"


def _iommufd_advertised(domain_capabilities: str) -> bool:
    try:
        root = ET.fromstring(domain_capabilities)
    except ET.ParseError:
        return False
    for enum in root.findall(".//devices/hostdev/enum[@name='iommufd']"):
        if any((value.text or "").strip() == "yes" for value in enum.findall("value")):
            return True
    return False


def check_connection_capabilities(conn: Any, config: Any) -> None:
    if conn.getType().upper() != "QEMU":
        raise RuntimeError(f"qemu:///system returned unexpected driver {conn.getType()!r}")
    if not config.host_devices:
        return

    version = conn.getLibVersion()
    if version < LIBVIRT_IOMMUFD_VERSION:
        raise RuntimeError(
            "GPU passthrough requires libvirt >= 12.1.0; "
            f"the daemon reports {_version_string(version)}"
        )
    try:
        capabilities = conn.getDomainCapabilities(
            config.emulator,
            "x86_64",
            "q35",
            "kvm",
            0,
        )
    except Exception as exc:
        raise RuntimeError(f"failed to query libvirt domain capabilities: {exc}") from exc
    if not _iommufd_advertised(capabilities):
        raise RuntimeError(
            "libvirt domain capabilities do not advertise hostdev iommufd support"
        )


def ensure_domain_name_available(conn: Any, libvirt_module: Any, name: str) -> None:
    try:
        conn.lookupByName(name)
    except libvirt_module.libvirtError as exc:
        if exc.get_error_code() == libvirt_module.VIR_ERR_NO_DOMAIN:
            return
        raise RuntimeError(f"failed to check domain name {name!r}: {exc}") from exc
    raise RuntimeError(
        f"a libvirt domain named {name!r} already exists; stop it or choose --name"
    )


def preflight_connection(emulator: str, name: str, require_iommufd: bool) -> None:
    """Check the daemon, domain name, and optional IOMMUFD support without mutation."""
    try:
        import libvirt  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "python3-libvirt is not installed; install the Ubuntu package "
            "'python3-libvirt'"
        ) from exc

    try:
        conn = libvirt.open("qemu:///system")
    except libvirt.libvirtError as exc:
        raise RuntimeError(f"failed to connect to qemu:///system: {exc}") from exc
    if conn is None:
        raise RuntimeError("failed to connect to qemu:///system")
    try:
        probe = SimpleNamespace(
            emulator=str(Path(emulator).resolve()),
            host_devices=[object()] if require_iommufd else [],
        )
        check_connection_capabilities(conn, probe)
        ensure_domain_name_available(conn, libvirt, name)
    finally:
        conn.close()


def _write_console_output(data: bytes, log: BinaryIO) -> None:
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    log.write(data)
    log.flush()


def attach_serial_console(conn: Any, domain: Any, libvirt_module: Any, log_path: str) -> None:
    """Attach a bidirectional console; Ctrl-C or Ctrl-] only detaches."""
    stream = conn.newStream(0)
    domain.openConsole(None, stream, 0)
    stopped = threading.Event()
    receiver_error: list[BaseException] = []
    log_file = open(log_path, "ab", buffering=0)

    def receive() -> None:
        try:
            while not stopped.is_set():
                chunk = stream.recv(65536)
                if not chunk:
                    break
                _write_console_output(chunk, log_file)
        except BaseException as exc:  # propagated after terminal restoration
            if not stopped.is_set():
                receiver_error.append(exc)
        finally:
            stopped.set()

    receiver = threading.Thread(target=receive, name="libvirt-console-recv", daemon=True)
    receiver.start()

    stdin_fd = sys.stdin.fileno()
    old_terminal = None
    if os.isatty(stdin_fd):
        old_terminal = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)

    print(
        "\nConnected to serial console. Press Ctrl-C or Ctrl-] to detach; "
        "the VM will keep running.\r",
        file=sys.stderr,
    )
    try:
        while not stopped.is_set():
            readable, _, _ = select.select([stdin_fd], [], [], 0.25)
            if not readable:
                continue
            data = os.read(stdin_fd, 4096)
            if not data:
                break
            if b"\x03" in data or b"\x1d" in data:
                break
            sent = 0
            while sent < len(data):
                sent += stream.send(data[sent:])
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        if old_terminal is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_terminal)
        try:
            stream.abort()
        except libvirt_module.libvirtError:
            pass
        receiver.join(timeout=1)
        log_file.close()
        try:
            active = bool(domain.isActive())
        except libvirt_module.libvirtError:
            active = False
        if active:
            message = f"Detached from {domain.name()}; VM is still managed by libvirt."
        else:
            message = "Serial console closed because the VM stopped."
        print(f"\n{message}", file=sys.stderr)
    if receiver_error and active:
        raise RuntimeError(f"serial console failed: {receiver_error[0]}")


def launch(config: DomainConfig) -> None:
    try:
        import libvirt  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "python3-libvirt is not installed; install the Ubuntu package "
            "'python3-libvirt'"
        ) from exc

    try:
        conn = libvirt.open("qemu:///system")
    except libvirt.libvirtError as exc:
        raise RuntimeError(f"failed to connect to qemu:///system: {exc}") from exc
    if conn is None:
        raise RuntimeError("failed to connect to qemu:///system")
    try:
        try:
            check_connection_capabilities(conn, config)
            ensure_domain_name_available(conn, libvirt, config.name)
            xml = build_domain_xml(config)
            flags = getattr(libvirt, "VIR_DOMAIN_START_VALIDATE", 0)
            domain = conn.createXML(xml, flags)
            if domain is None:
                raise RuntimeError("libvirt did not return a domain after createXML()")
            name = domain.name()
            uuid = domain.UUIDString()
            print(f"Started transient libvirt domain: {name} ({uuid})")
            print(f"  console: virsh -c qemu:///system console {name}")
            print(f"  shutdown: virsh -c qemu:///system shutdown {name}")
            print(f"  force stop: virsh -c qemu:///system destroy {name}")
            if config.debug:
                attach_serial_console(conn, domain, libvirt, str(config.log_file))
        except libvirt.libvirtError as exc:
            raise RuntimeError(f"libvirt failed to start the domain: {exc}") from exc
    finally:
        conn.close()


def _host_device(value: str) -> HostDevice:
    try:
        kind, bdf = value.split(":", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("hostdev must be KIND:BDF") from exc
    try:
        _parse_bdf(bdf)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if kind not in {"gpu", "aux"}:
        raise argparse.ArgumentTypeError("hostdev kind must be 'gpu' or 'aux'")
    return HostDevice(kind, bdf)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--mode", choices=("untrusted", "tdx", "sev-snp"), required=True)
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--memory-gib", type=int, required=True)
    parser.add_argument("--vcpus", type=int, required=True)
    parser.add_argument("--bios", required=True)
    parser.add_argument("--kernel", required=True)
    parser.add_argument("--kernel-cmdline", required=True)
    parser.add_argument("--rootfs", required=True)
    parser.add_argument("--state-disk", required=True)
    parser.add_argument("--provider-config-disk", required=True)
    parser.add_argument("--guest-cid", type=int, required=True)
    parser.add_argument("--qgs-cid", type=int, required=True)
    parser.add_argument("--mac-address", required=True)
    parser.add_argument("--netdev-mode", choices=("user", "tap"), required=True)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--log-file")
    parser.add_argument("--cpu-model")
    parser.add_argument("--phys-bits", type=int)
    parser.add_argument("--cbitpos", type=int)
    parser.add_argument("--bridge")
    parser.add_argument("--tap-iface")
    parser.add_argument("--ip-address", default="0.0.0.0")
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--wg-port", type=int)
    parser.add_argument("--http-port", type=int)
    parser.add_argument("--https-port", type=int)
    parser.add_argument("--pki-port", type=int)
    parser.add_argument("--pki-vm-measure-port", type=int)
    parser.add_argument("--swarm-db-gossip-port", type=int)
    parser.add_argument("--dns-port", type=int)
    parser.add_argument("--hostdev", type=_host_device, action="append", default=[])
    return parser


def _preflight_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check libvirt before preparing VM resources")
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--require-iommufd", action="store_true")
    return parser


def _config_from_args(args: argparse.Namespace) -> DomainConfig:
    return DomainConfig(
        name=args.name,
        mode=args.mode,
        emulator=str(Path(args.emulator).resolve()),
        memory_gib=args.memory_gib,
        vcpus=args.vcpus,
        bios=str(Path(args.bios).resolve()),
        kernel=str(Path(args.kernel).resolve()),
        kernel_cmdline=args.kernel_cmdline,
        rootfs=str(Path(args.rootfs).resolve()),
        state_disk=str(Path(args.state_disk).resolve()),
        provider_config_disk=str(Path(args.provider_config_disk).resolve()),
        guest_cid=args.guest_cid,
        qgs_cid=args.qgs_cid,
        mac_address=args.mac_address,
        netdev_mode=args.netdev_mode,
        debug=args.debug,
        log_file=args.log_file,
        cpu_model=args.cpu_model,
        phys_bits=args.phys_bits,
        cbitpos=args.cbitpos,
        bridge=args.bridge,
        tap_iface=args.tap_iface,
        ip_address=args.ip_address,
        ssh_port=args.ssh_port,
        wg_port=args.wg_port,
        http_port=args.http_port,
        https_port=args.https_port,
        pki_port=args.pki_port,
        pki_vm_measure_port=args.pki_vm_measure_port,
        swarm_db_gossip_port=args.swarm_db_gossip_port,
        dns_port=args.dns_port,
        host_devices=args.hostdev,
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments[:1] == ["preflight"]:
        args = _preflight_parser().parse_args(arguments[1:])
        try:
            if not DOMAIN_NAME_RE.fullmatch(args.name):
                raise ValueError(
                    "domain name may contain only letters, digits, '.', '_', '+', ':', and '-'"
                )
            preflight_connection(args.emulator, args.name, args.require_iommufd)
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    args = _parser().parse_args(arguments)
    try:
        config = _config_from_args(args)
        _validate_config(config)
        launch(config)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
