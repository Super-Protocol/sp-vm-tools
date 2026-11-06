"""Read/copy immutable evidence for the reviewed first-compute enrollment stop."""
from pathlib import Path, PurePosixPath
import hashlib
import os

from openstack_container_engine import file_hash
from openstack_install_continue import document, private

PREFIX = ('operator-auth', 'shared-network', 'stage-enrollment',
          'project-resources-and-barbican', 'runtime-requests')


def protected_source(source):
    from openstack_install import protected_parent
    protected_parent(source)
    if source.stat().st_mode & 0o077:
        raise ValueError('private platform checkpoint source required')


def inspect(source, hosts, *, claim_root=None, ready=False):
    source = Path(source)
    protected_source(source)
    names = sorted(hosts)
    if not names:
        raise ValueError('explicit compute fleet required')
    journal = document(source / 'platform-transaction.json')
    completed = list(PREFIX) + (['compute-'+n for n in names[:-1]] if ready else [])
    if (journal.get('status') not in (('RUNNING','NEEDS_REVIEW') if ready else ('NEEDS_REVIEW',))
            or journal.get('completed') != completed
            or journal.get('active_phase') != 'compute-' + (names[-1] if ready else names[0])
            or journal.get('guests_launched') is not False):
        raise ValueError('only the first runtime enrollment boundary can be continued')
    marker=source/'continued-by.json'
    if marker.exists() or marker.is_symlink():
        if claim_root is None or document(marker).get('controller')!=str(claim_root):
            raise ValueError('platform continuation is already claimed')
    elif claim_root is not None:
        raise ValueError('platform continuation claim is missing')
    # The controller made only this empty directory before its callback failed.
    target = source / ('compute-' + (names[-1] if ready else names[0]))
    if (target.is_symlink() or not target.is_dir() or list(target.iterdir())):
        raise ValueError('unrecognized controller compute activation evidence')
    protected_source(target)
    if ready:
        allowed = {'platform-transaction.json','network-request.json','network','resources','barbican',
                   'enrollment','runtime-requests','continued-by.json'} | {'compute-'+n for n in names[1:]}
        if any(p.name not in allowed for p in source.iterdir()):
            raise ValueError('later platform work or unexpected evidence exists; no replay')
        for name in names[1:]:
            target=source/('compute-'+name)
            if target.is_symlink() or not target.is_dir() or list(target.iterdir()):
                raise ValueError('unrecognized completed controller callback directory')
            protected_source(target)
    paths = ['platform-transaction.json', 'network-request.json',
             'network/network-plan.json', 'network/network-transaction.json',
             'resources/resources-plan.json', 'resources/resources-transaction.json',
             'barbican/probe-transaction.json', 'enrollment/enrollment-plan.json',
             'enrollment/runtime-services.json']
    paths += ['runtime-requests/' + name + '.json' for name in names]
    network = document(source / 'network/network-transaction.json')
    net_plan = document(source / 'network/network-plan.json')
    resources = document(source / 'resources/resources-transaction.json')
    res_plan = document(source / 'resources/resources-plan.json')
    from openstack_container_engine import digest
    for record, plan in ((network, net_plan), (resources, res_plan)):
        if (record.get('plan_sha256') != plan.get('plan_sha256')
                or plan.get('plan_sha256') != digest({k:v for k,v in plan.items() if k!='plan_sha256'})):
            raise ValueError('completed platform child plan binding differs')
    if (network.get('status') != 'SPVM_SHARED_NETWORK_READY_NOT_HOST_ROUTING_ACCEPTED'
            or network.get('result', {}).get('guests_launched') is not False
            or network['result'].get('host_network_changed') is not False
            or resources.get('status') != 'SPVM_PROJECT_RESOURCES_READY_NOT_RUNTIME_ENROLLED'
            or resources.get('guests_launched') is not False):
        raise ValueError('network/project resource completion is not confirmed')
    probe = document(source / 'barbican/probe-transaction.json')
    if (probe.get('status') != 'BARBICAN_PROBE_PASSED'
            or probe.get('result', {}).get('passed') is not True
            or probe['result'].get('cleanup_complete') is not True):
        raise ValueError('completed clean Barbican probe required')
    results = {'operator-auth': {}, 'shared-network': network['result'],
                        'stage-enrollment': {'status':'SPVM_ENROLLMENT_STAGED_NOT_APPLIED'},
                        'project-resources-and-barbican': probe['result'],
                        'runtime-requests': {'status':'RUNTIME_REQUESTS_STAGED'}}
    if ready:
        results.update({'compute-'+n:{'status':'COMPUTE_READY','guests_restarted':False} for n in names})
    return {'source': str(source), 'files': {name:file_hash(private(source/name)) for name in paths},
            'results': results}


def restore(checkpoint, output, reconciled_host):
    """Create NEW evidence; never edit or repair the original failed journal."""
    source, output = Path(checkpoint['source']), Path(output)
    protected_source(source)
    names = [reconciled_host] if isinstance(reconciled_host,str) else list(reconciled_host or [])
    expected = list(PREFIX) + (['compute-'+n for n in names] if not isinstance(reconciled_host,str) else [])
    if list(checkpoint['results']) != expected or not names or len(set(names))!=len(names):
        raise ValueError('exact reviewed platform prefix required')
    for name, wanted in checkpoint['files'].items():
        part = PurePosixPath(name)
        if (part.is_absolute() or '..' in part.parts or str(part)!=name
                or part.suffix!='.json' or file_hash(private(source/name))!=wanted):
            raise ValueError('checkpoint file changed or has an unsafe path')
    output.mkdir(mode=0o700, exist_ok=False)
    for name in checkpoint['files']:
        if name == 'platform-transaction.json':
            continue
        target = output/name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        body=private(source/name).read_bytes()
        if hashlib.sha256(body).hexdigest()!=checkpoint['files'][name]:
            raise ValueError('checkpoint changed before copying; partial evidence preserved')
        with target.open('xb') as stream:
            os.fchmod(stream.fileno(),0o600);stream.write(body);stream.flush();os.fsync(stream.fileno())
        directory=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
    # Detect concurrent source changes, including changes during copying.
    if any(file_hash(private(source/name))!=wanted for name,wanted in checkpoint['files'].items()):
        raise ValueError('checkpoint changed during copying; partial evidence preserved')
    results = dict(checkpoint['results'])
    for name in names:
        results['compute-'+name] = {'status':'COMPUTE_READY', 'guests_restarted':False}
    return results
