#!/usr/bin/env python3

import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import libvirt_launcher as launcher  # noqa: E402


class DomainXMLTests(unittest.TestCase):
    def config(self, mode="untrusted", netdev_mode="user", **overrides):
        values = dict(
            name="super-protocol-3",
            mode=mode,
            emulator="/usr/bin/qemu-system-x86_64",
            memory_gib=16,
            vcpus=8,
            bios="/var/lib/super protocol/bios.fd",
            kernel="/var/lib/super protocol/vmlinuz",
            kernel_cmdline="root=LABEL=rootfs hash=a&b",
            rootfs="/var/lib/super protocol/rootfs.img",
            state_disk="/var/lib/super protocol/state.qcow2",
            provider_config_disk="/var/lib/super protocol/provider.img",
            guest_cid=3,
            qgs_cid=2,
            mac_address="52:54:00:12:34:56",
            netdev_mode=netdev_mode,
            debug=False,
            ssh_port=2222,
            wg_port=51821,
            http_port=8080,
            https_port=8443,
            pki_port=9443,
            pki_vm_measure_port=9181,
            swarm_db_gossip_port=17946,
            dns_port=1053,
        )
        if mode == "sev-snp":
            values.update(cpu_model="EPYC-v4", phys_bits=48, cbitpos=51)
        values.update(overrides)
        return launcher.DomainConfig(**values)

    def root(self, config):
        return ET.fromstring(launcher.build_domain_xml(config))

    def test_untrusted_cpu_and_direct_boot(self):
        root = self.root(self.config())
        self.assertEqual(root.findtext("name"), "super-protocol-3")
        self.assertEqual(root.findtext("os/kernel"), "/var/lib/super protocol/vmlinuz")
        self.assertEqual(root.findtext("os/cmdline"), "root=LABEL=rootfs hash=a&b")
        self.assertEqual(root.find("cpu").get("mode"), "host-passthrough")
        self.assertIsNotNone(root.find("features/ioapic[@driver='qemu']"))
        self.assertIsNotNone(root.find("features/pmu[@state='off']"))
        self.assertIsNotNone(root.find("cpu/feature[@name='kvm-steal-time']"))
        self.assertIsNone(root.find("launchSecurity"))

    def test_reserved_ovmf_fw_cfg_uses_qemu_commandline(self):
        root = self.root(self.config())
        self.assertIsNone(root.find("sysinfo[@type='fwcfg']"))
        args = [
            element.get("value")
            for element in root.findall(
                f"{{{launcher.QEMU_NS}}}commandline/{{{launcher.QEMU_NS}}}arg"
            )
        ]
        self.assertEqual(
            args,
            ["-fw_cfg", "name=opt/ovmf/X-PciMmio64,string=262144"],
        )

    def test_sev_snp_launch_security(self):
        root = self.root(self.config(mode="sev-snp"))
        launch_security = root.find("launchSecurity")
        self.assertEqual(launch_security.get("type"), "sev-snp")
        self.assertEqual(launch_security.get("kernelHashes"), "yes")
        self.assertEqual(launch_security.findtext("cbitpos"), "51")
        self.assertEqual(launch_security.findtext("reducedPhysBits"), "1")
        self.assertEqual(launch_security.findtext("policy"), "0x30000")
        self.assertEqual(root.findtext("cpu/model"), "EPYC-v4")
        self.assertEqual(root.find("cpu/maxphysaddr").get("bits"), "48")
        self.assertIsNotNone(root.find("features/vmport[@state='off']"))

    def test_tdx_vsock_qgs_uses_qemu_namespace(self):
        root = self.root(self.config(mode="tdx"))
        args = [
            element.get("value")
            for element in root.findall(f"{{{launcher.QEMU_NS}}}commandline/{{{launcher.QEMU_NS}}}arg")
        ]
        self.assertIn("memory-backend-ram,id=sp-mem,size=16G", args)
        tdx_arg = next(value for value in args if '"qom-type":"tdx-guest"' in value)
        self.assertIn('"type":"vsock"', tdx_arg)
        self.assertIn('"cid":"2"', tdx_arg)
        self.assertIn('"port":"4050"', tdx_arg)
        self.assertIn(
            "confidential-guest-support=sp-tdx,memory-backend=sp-mem",
            args,
        )

    def test_user_network_uses_passt_and_all_forwards(self):
        root = self.root(self.config(debug=True, log_file="/tmp/serial.log"))
        interface = root.find("devices/interface[@type='user']")
        self.assertEqual(interface.find("backend").get("type"), "passt")
        forwards = {
            (
                element.get("proto"),
                element.get("address"),
                element.find("range").get("start"),
                element.find("range").get("to"),
            )
            for element in interface.findall("portForward")
        }
        self.assertIn(("tcp", None, "8080", "80"), forwards)
        self.assertIn(("udp", None, "51821", "51820"), forwards)
        self.assertIn(("tcp", "127.0.0.1", "2222", "22"), forwards)
        self.assertIn(("udp", None, "1053", "53"), forwards)
        self.assertIn(("tcp", None, "1053", "53"), forwards)

    def test_tap_debug_has_precreated_tap_and_secondary_passt(self):
        config = self.config(
            netdev_mode="tap",
            bridge="swarmbr0",
            tap_iface="sw-tap7",
            debug=True,
            log_file="/tmp/serial.log",
        )
        root = self.root(config)
        interfaces = root.findall("devices/interface")
        self.assertEqual(len(interfaces), 2)
        self.assertEqual(interfaces[0].get("type"), "ethernet")
        self.assertEqual(interfaces[0].find("target").get("dev"), "sw-tap7")
        self.assertEqual(interfaces[0].find("target").get("managed"), "no")
        self.assertEqual(interfaces[1].find("backend").get("type"), "passt")
        ssh_range = interfaces[1].find("portForward/range")
        self.assertEqual((ssh_range.get("start"), ssh_range.get("to")), ("2222", "22"))

    def test_host_devices_get_iommufd_and_separate_root_ports(self):
        config = self.config(
            host_devices=[
                launcher.HostDevice("gpu", "65:00.0"),
                launcher.HostDevice("aux", "0000:66:00.1"),
            ]
        )
        root = self.root(config)
        hostdevs = root.findall("devices/hostdev")
        root_ports = root.findall("devices/controller[@model='pcie-root-port']")
        self.assertEqual(len(hostdevs), 2)
        self.assertEqual(len(root_ports), 2)
        self.assertTrue(all(item.get("managed") == "no" for item in hostdevs))
        self.assertTrue(
            all(item.find("driver").get("iommufd") == "yes" for item in hostdevs)
        )
        self.assertIsNotNone(hostdevs[0].find("rom[@bar='off']"))
        self.assertIsNone(hostdevs[1].find("rom"))
        self.assertEqual(hostdevs[1].find("source/address").get("function"), "0x1")

    def test_gpu_none_produces_no_hostdev_or_extra_root_port(self):
        root = self.root(self.config(host_devices=[]))
        self.assertEqual(root.findall("devices/hostdev"), [])
        self.assertEqual(root.findall("devices/controller[@model='pcie-root-port']"), [])

    def test_invalid_bdf_and_debug_without_log_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid PCI BDF"):
            launcher.build_domain_xml(
                self.config(host_devices=[launcher.HostDevice("gpu", "bad")])
            )
        with self.assertRaisesRegex(ValueError, "requires a log file"):
            launcher.build_domain_xml(self.config(debug=True))
        with self.assertRaisesRegex(ValueError, "ports must be between"):
            launcher.build_domain_xml(self.config(http_port=70000))


class CapabilityTests(unittest.TestCase):
    def config(self, host_devices=True):
        return DomainXMLTests().config(
            host_devices=[launcher.HostDevice("gpu", "65:00.0")]
            if host_devices
            else []
        )

    def test_requires_libvirt_12_1_for_host_devices(self):
        conn = mock.Mock()
        conn.getType.return_value = "QEMU"
        conn.getLibVersion.return_value = 12_000_000
        with self.assertRaisesRegex(RuntimeError, ">= 12.1.0"):
            launcher.check_connection_capabilities(conn, self.config())

    def test_requires_iommufd_domain_capability(self):
        conn = mock.Mock()
        conn.getType.return_value = "QEMU"
        conn.getLibVersion.return_value = 12_001_000
        conn.getDomainCapabilities.return_value = "<domainCapabilities/>"
        with self.assertRaisesRegex(RuntimeError, "do not advertise"):
            launcher.check_connection_capabilities(conn, self.config())

    def test_accepts_advertised_iommufd(self):
        conn = mock.Mock()
        conn.getType.return_value = "QEMU"
        conn.getLibVersion.return_value = 12_001_000
        conn.getDomainCapabilities.return_value = """
            <domainCapabilities><devices><hostdev supported='yes'>
              <enum name='iommufd'><value>yes</value><value>no</value></enum>
            </hostdev></devices></domainCapabilities>
        """
        launcher.check_connection_capabilities(conn, self.config())

    def test_no_hostdev_does_not_require_iommufd(self):
        conn = mock.Mock()
        conn.getType.return_value = "QEMU"
        launcher.check_connection_capabilities(conn, self.config(False))
        conn.getLibVersion.assert_not_called()

    def test_existing_domain_name_is_rejected(self):
        conn = mock.Mock()
        conn.lookupByName.return_value = object()
        libvirt_module = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            launcher.ensure_domain_name_available(
                conn, libvirt_module, "super-protocol-3"
            )

    def test_preflight_subcommand_dispatches_without_building_domain(self):
        with mock.patch.object(launcher, "preflight_connection") as preflight:
            result = launcher.main(
                [
                    "preflight",
                    "--emulator",
                    "/usr/bin/qemu-system-x86_64",
                    "--name",
                    "super-protocol-3",
                    "--require-iommufd",
                ]
            )
        self.assertEqual(result, 0)
        preflight.assert_called_once_with(
            "/usr/bin/qemu-system-x86_64", "super-protocol-3", True
        )


class SourceSafetyTests(unittest.TestCase):
    def test_new_bash_launcher_has_no_eval(self):
        source = (REPO_ROOT / "scripts" / "start_super_protocol_libvirt.sh").read_text()
        self.assertIsNone(re.search(r"^\s*eval\b", source, re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
