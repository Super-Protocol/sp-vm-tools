#!/usr/bin/env python3
"""Launch one disposable, diskless KVM domain; never fall back to emulation."""
import argparse
import sys
import time
import uuid
from xml.sax.saxutils import escape


def verify(emulator):
    import libvirt

    identity = str(uuid.uuid4())
    # No network, storage, guest agent or persistent definition. AUTODESTROY also
    # removes our domain if this connection closes unexpectedly.
    xml = f'''<domain type="kvm">
      <name>spvm-kvm-probe-{identity}</name><uuid>{identity}</uuid>
      <memory unit="MiB">128</memory><vcpu>1</vcpu>
      <os><type arch="x86_64" machine="q35">hvm</type></os>
      <cpu mode="host-passthrough"/><devices>
      <emulator>{escape(emulator)}</emulator><memballoon model="none"/>
      <vsock model="virtio"><cid auto="yes"/></vsock>
      </devices></domain>'''
    connection = libvirt.open('qemu:///system')
    domain = None
    try:
        domain = connection.createXML(xml, libvirt.VIR_DOMAIN_START_AUTODESTROY)
        time.sleep(1)
        if domain.UUIDString() != identity or not domain.isActive():
            raise RuntimeError('disposable KVM domain did not remain active')
        # Query the live QEMU accelerator, not only the requested XML type.
        import json
        import libvirt_qemu
        result = json.loads(libvirt_qemu.qemuMonitorCommand(
            domain, '{"execute":"query-kvm"}', 0))
        status = result.get('return', {})
        if status.get('enabled') is not True or status.get('present') is not True:
            raise RuntimeError('live QEMU does not have KVM enabled')
        print('Verified: live libvirt domain uses the KVM accelerator (no TCG).')
    finally:
        try:
            if domain is not None and domain.isActive():
                domain.destroy()
        finally:
            connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emulator', required=True)
    args = parser.parse_args()
    try:
        verify(args.emulator)
    except Exception as exc:
        print(f'ERROR: real KVM launch failed: {exc}. Check nested virtualization, /dev/kvm and libvirt permissions. No TCG fallback.', file=sys.stderr)
        sys.exit(1)
