"""Follow explicit successful workspace supersessions; never adopt partial state."""
import copy
from pathlib import Path

from openstack_install_continue import document,private
import openstack_kolla_workspace as workspace


def portable(cfg):
    result=copy.deepcopy(cfg)
    for host in result['hosts'].values():
        host.pop('ssh_transport',None);host.pop('identity_file',None)
    return result


def current(base):
    root=Path(base)/'kolla-bound';config=Path(base)/'deployment.artifacts.yaml';seen=set()
    while True:
        if root in seen or len(seen)>=256:raise ValueError('invalid active workspace lineage')
        seen.add(root)
        receipt=document(root/'workspace.json');journal=document(root/'journal.json')
        if receipt['files']!=workspace.file_set(root):raise ValueError('superseded workspace content changed')
        status=journal.get('status')
        if status==receipt['status']:
            workspace.check(root);private(config)
            return root,config
        if status not in ('SUPERSEDED_COMPUTE_REMOVED','SUPERSEDED_COMPUTE_ADDED'):
            raise ValueError('active workspace has an unfinished or unsupported operation')
        successor=Path(journal['successor'])
        transaction=document(successor.parent/'transaction.json')
        expected='COMPUTE_REMOVED' if status=='SUPERSEDED_COMPUTE_REMOVED' else 'COMPUTE_ADDED'
        if transaction.get('status')!=expected or transaction.get('plan_sha256')!=journal.get('plan_sha256'):
            raise ValueError('workspace successor transaction is not complete')
        root=successor
        config=Path(journal.get('config',root.parent/'remaining-deployment.yaml'))
