"""Install per-compute API executors during fresh deployment, never runtime SSH.

Operator SSH is used only for this explicit installation phase. Each host gets
its own Keystone member identity and application credential, never an SSH key.
"""
import hashlib
import json
from pathlib import Path
import subprocess

from openstack_container_engine import write


def prepare(value, path, backend, connection, cfg, project, user, output, *, operations=None):
    from host_control_credentials import provision
    from host_control_install import source_files
    from openstack_spvm.host_control import binding
    output=Path(output);output.mkdir(mode=0o700)
    receipt=json.loads((Path(value['workspace'])/'compute-binding.json').read_bytes())
    if set(receipt['hosts'])!={n for n,h in cfg['hosts'].items() if 'compute' in h['roles']}:
        raise ValueError('executor enrollment requires the exact bound compute fleet')
    deployment=Path(path).parent
    installer=(deployment/'host_control_install.py').read_text()
    sources=source_files(deployment)
    targets={}
    def remote(host,body):
        # This function is installation-only. Neither coordinator nor executor
        # imports it, stores operator keys or has a runtime SSH fallback.
        result=subprocess.run(backend.ssh_arguments(cfg['hosts'][host])+['sudo -n /usr/bin/python3 -I -B -'],
            input=body,text=True,capture_output=True,timeout=180)
        if result.returncode or len(result.stdout)>65536:
            raise ValueError('host executor installation failed; inspect private installation journal; no retry')
        return json.loads(result.stdout)
    # Check every host for conflicting/partial installations before creating
    # any Keystone identities or writing an executor onto any host.
    for name in sorted(receipt['hosts']):
        if operations is None:
            remote(name,installer+'\nfresh()\nprint(json.dumps({"status":"FRESH"}))\n')
        else:
            result=operations('executor-probe',{'host':name})
            if result.get('status')!='FRESH':raise ValueError('fresh executor probe not confirmed')
    auth_url=receipt['bootstrap_runtime']['auth_url']
    region=receipt['bootstrap_runtime']['region_name']
    dispatcher_path=Path('/etc/spvm/host-control-dispatcher-credentials.json')
    if dispatcher_path.exists() or dispatcher_path.is_symlink():
        raise ValueError('dispatcher credentials already exist; no adoption or implicit rotation')
    dispatcher_journal=output/'dispatcher-transaction.json'
    label='spvm-dispatcher-'+hashlib.sha256((project+cfg['deployment_id']).encode()).hexdigest()[:24]
    dispatch=provision(connection,label,auth_url,region,'/etc/ssl/certs/ca-certificates.crt',
        lambda state:write(dispatcher_journal,state),purpose='dispatcher')
    from runtime_maintenance import atomic
    atomic(dispatcher_path,json.dumps(dispatch,sort_keys=True).encode(),0o600)
    for name, item in sorted(receipt['hosts'].items()):
        root=output/name;root.mkdir(mode=0o700)
        request=json.loads(Path(item['request_file']).read_bytes())
        machine=request['machine_sha256']
        if machine!=receipt['machines'][name] or item['compute_uuid']!=request['compute_uuid']:
            raise ValueError('compute installation binding changed before executor enrollment')
        journal=root/'transaction.json'
        def checkpoint(state):write(journal,{'host':name,**state})
        checkpoint({'phase':'PROVISIONING_API_IDENTITY'})
        # Domain-wide name includes project and deployment fingerprints, not
        # an arbitrary administrator label that could collide across projects.
        label='spvm-host-'+hashlib.sha256((project+cfg['deployment_id']+item['compute_uuid']).encode()).hexdigest()[:24]
        private=provision(connection,label,auth_url,region,'/etc/ssl/certs/ca-certificates.crt',checkpoint)
        target=binding({'host':cfg['hosts'][name].get('inventory_name',name),
            'compute_uuid':item['compute_uuid'],'machine_sha256':machine,'user_id':private['user_id']})
        policy={'schema_version':1,'project_id':project,'target':target,'dispatcher_user_ids':[dispatch['user_id']],
            'credentials':'/etc/spvm/host-control-credentials.json'}
        # Secrets travel over the operator's verified installation SSH channel,
        # never argv, terminal output, reports, deployment YAML or a reusable
        # staging file. Remote completion uses only its encrypted framed stdio
        # transport; those frames are not printed or persisted by the operator.
        body=installer+'\nSPVM_INSTALL_POLICY='+repr(policy)+'\nSPVM_INSTALL_CREDENTIALS='+repr(private)+'\nSPVM_INSTALL_SOURCES='+repr(sources)+\
            '\nprint(json.dumps(install(SPVM_INSTALL_POLICY,SPVM_INSTALL_CREDENTIALS,SPVM_INSTALL_SOURCES)))\n'
        result=(remote(name,body) if operations is None else
                operations('executor-install',{'host':name,'policy':policy,'credentials':private}))
        if result['status']!='HOST_EXECUTOR_INSTALLED' or result['user_id']!=target['user_id']:
            raise ValueError('host executor installation acknowledgement differs')
        targets[name]=target
        checkpoint({'phase':'INSTALLED','user_id':private['user_id'],'credential_id':private['credential_id']})
    return {'executors':targets,'dispatcher_credentials':str(dispatcher_path),'publisher_user_id':dispatch['user_id']}
