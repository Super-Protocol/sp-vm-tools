"""Secret-free infrastructure intent shared by install and cloud membership."""
import copy


def portable(cfg):
    result=copy.deepcopy(cfg)
    for host in result['hosts'].values():
        host.pop('ssh_transport',None);host.pop('identity_file',None)
    return result
