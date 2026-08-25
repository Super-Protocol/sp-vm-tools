import unittest
import xml.etree.ElementTree as ET

from scripts.libvirt_launcher import DomainConfig, build_domain_xml


class DomainUuidTest(unittest.TestCase):
    def config(self, domain_uuid):
        return DomainConfig(
            name="instance-00000001",
            uuid=domain_uuid,
            mode="untrusted",
            emulator="/usr/bin/qemu-system-x86_64",
            memory_gib=64,
            vcpus=16,
            bios="/tmp/OVMF.fd",
            kernel="/tmp/vmlinuz",
            kernel_cmdline="console=ttyS0",
            rootfs="/tmp/rootfs.img",
            state_disk="/tmp/state.qcow2",
            provider_config_disk="/tmp/provider.img",
            guest_cid=10,
            qgs_socket="/run/tdx-qgs/qgs.socket",
            mac_address="52:54:00:77:00:0a",
            netdev_mode="user",
        )

    def test_nova_uuid_is_written_to_domain_xml(self):
        expected = "ed89b02d-ad43-441b-9923-8f1eb0484f91"
        root = ET.fromstring(build_domain_xml(self.config(expected)))
        self.assertEqual(expected, root.findtext("uuid"))
        self.assertEqual("instance-00000001", root.findtext("name"))

    def test_invalid_uuid_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid domain UUID"):
            build_domain_xml(self.config("------------------------------------"))


if __name__ == "__main__":
    unittest.main()
