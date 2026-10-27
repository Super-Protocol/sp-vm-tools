"""Stage fresh helper UUIDs using pinned Kolla's native Ansible to_uuid rule.

Never rewrite a live Nova identity or adopt a populated compute. Existing cloud
adoption must resolve the registered hypervisor UUID through its own workflow.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import uuid

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
from openstack_host_helper import request as helper_request

ANSIBLE_NAMESPACE = uuid.UUID('361e6d51-faec-444a-9079-341386da8e2e')


def verify_rule(receipt,wanted):
    source=Path(receipt['kolla_source'])/'ansible/roles/nova-cell/tasks/config.yml'
    if 'content: "{{ hypervisor_nodename | to_uuid }}"' not in read(source).decode():
        raise ValueError('unsupported pinned Kolla identity rule')
    names=[v['nodename'] for v in wanted.values()]
    code='import json,sys;from ansible.plugins.filter.core import to_uuid;print(json.dumps([to_uuid(n) for n in sys.argv[1:]]))'
    result=subprocess.run([receipt['kolla_python'],'-I','-B','-c',code,*names],capture_output=True,text=True,timeout=30)
    if result.returncode or json.loads(result.stdout)!=[native_uuid(n) for n in names]:
        raise ValueError('installed pinned Ansible filter differs from native UUID policy')


def native_uuid(nodename):
    if (not isinstance(nodename, str) or len(nodename)>253
            or not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', nodename)
            or any(not label or len(label)>63 or label.startswith('-') or label.endswith('-')
                   for label in nodename.split('.'))):
        raise ValueError('audited kernel nodename required; not an inventory alias')
    return str(uuid.uuid5(ANSIBLE_NAMESPACE, nodename))


def identities(cfg, audits):
    selected={n for n,h in cfg['hosts'].items() if 'compute' in h['roles']}
    if not selected or not selected<=set(audits):raise ValueError('complete compute audit required')
    result={};seen=set()
    for name in sorted(selected):
        audit=audits[name]
        nodename=audit.get('hostname')
        identifier=native_uuid(nodename)
        if identifier in seen:raise ValueError('duplicate physical compute nodename')
        seen.add(identifier)
        result[name]={'nodename':nodename,'compute_uuid':identifier}
    return result


def stage(args, backend):
    cfg=backend.deployment_config(args.config);audits=json.loads(read(args.audit))
    wanted=identities(cfg,audits);mapping=json.loads(read(args.requests))
    if (set(mapping)!={'schema_version','hosts'} or type(mapping['schema_version']) is not int
            or mapping['schema_version']!=1 or set(mapping['hosts'])!=set(wanted)):
        raise ValueError('exact compute helper request mapping required')
    requests={};hashes={args.config:file_hash(args.config),args.audit:file_hash(args.audit),args.requests:file_hash(args.requests)}
    for name,identity in wanted.items():
        path=mapping['hosts'][name];raw=json.loads(read(path));hashes[path]=file_hash(path)
        if raw.get('pools')=='from-deployment':
            pools=cfg['hosts'][name]['storage_pools']
            if pools=='auto':raise ValueError('resolve storage before staging identities')
            raw['pools']=[{k:p[k] for k in ('id','mount','budget_gib','reserve_gib')}|{'root':p['path']}
                |({'allow_root':True} if p.get('allow_root') else {}) for p in pools if p.get('enabled',True)]
        spec=helper_request(raw)
        # source audit uses the same machine-id SHA as resource validation.
        machine=audits[name].get('host_identity_sha256')
        if machine is None:machine=audits[name].get('machine_sha256')
        if machine!=spec['machine_sha256']:raise ValueError('helper request and audited machine differ')
        requests[name]={**spec,'compute_uuid':identity['compute_uuid']}
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('new canonical staging directory required')
    output.mkdir(mode=0o700,exist_ok=False)
    paths={}
    for name,spec in requests.items():
        target=output/(name+'.json');write(target,spec);paths[name]=str(target)
    write(output/'helper-requests.json',{'schema_version':1,'hosts':paths})
    result={'schema_version':1,'status':'NATIVE_COMPUTE_IDENTITIES_STAGED_NOT_INSTALLED',
        'rule':'ansible-to_uuid-kernel-nodename-v1','hosts':wanted,'source_sha256':hashes,
        'hosts_changed':False,'guests_launched':False}
    result['identity_sha256']=digest(result);write(output/'identities.json',result)
    return result


def main(path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage-kolla-identities'])
    for key in ('config','audit','requests','output'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args(argv)
    try:
        from openstack_kolla_existing import backend
        result=stage(args,backend(path))
        print(json.dumps({k:result[k] for k in ('status','identity_sha256','hosts_changed','guests_launched')}));return 0
    except Exception:
        print(json.dumps({'status':'NATIVE_IDENTITY_STAGING_REFUSED','hosts_changed':False}));return 2
