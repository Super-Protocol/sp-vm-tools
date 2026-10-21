"""Read-only firewall ownership check; never stop/disable a firewall manager."""
import shlex


def require_unmanaged(command, managers=('ufw.service','firewalld.service','nftables.service')):
    for manager in managers:
        state=command(['systemctl','show',manager,'--property=ActiveState','--value']).strip()
        if state in ('inactive','failed'):continue
        if manager!='ufw.service' or state!='active':
            raise ValueError('active or unknown firewall manager requires manual integration')
        # Ubuntu starts the UFW oneshot even with ENABLED=no. Do not confuse
        # that boot hook with an enabled firewall, nor trust its status alone.
        substate=command(['systemctl','show',manager,'--property=SubState','--value']).strip()
        if substate!='exited' or command(['ufw','status']).strip()!='Status: inactive':
            raise ValueError('effective UFW policy is active or unknown')
        for binary in ('iptables','ip6tables'):
            lines=command([binary,'-w','5','-t','filter','-S']).splitlines()
            rows=[shlex.split(line) for line in lines if line.strip()]
            if not any(row[:2]==['-P','INPUT'] for row in rows):
                raise ValueError('complete firewall rules required to verify inactive UFW')
            if any(row[0]=='-A' and any(token.lower().startswith('ufw-') for token in row[1:]) for row in rows):
                raise ValueError('UFW-owned rules remain despite inactive userspace status')
