"""Stage one new compute in an existing workspace; never regenerate credentials.

This is input preparation, not host enrollment or Kolla execution. Existing
hosts, resource budgets, launch modes and controller PCI aliases cannot change.
"""
import argparse
import json
from pathlib import Path
import tempfile

import yaml
from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
from openstack_compute_identity import native_uuid
from openstack_host_helper import source_manifest
from openstack_kolla_compute import selected_requests
import openstack_kolla_workspace as workspace


def addition(previous, desired, host):
    if (set(desired['hosts']) != set(previous['hosts']) | {host}
            or host in previous['hosts'] or desired['hosts'][host]['roles'] != ['compute']
            or {k:v for k,v in previous.items() if k != 'hosts'} !=
               {k:v for k,v in desired.items() if k != 'hosts'}
            or any(desired['hosts'][n] != h for n,h in previous['hosts'].items())):
        raise ValueError('add exactly one compute-only host; existing deployment intent must not change')


def stage(args, backend_path, backend, nova_inputs):
    previous=backend.deployment_config(args.config)
    cfg=backend.deployment_config(args.new_config)
    addition(previous,cfg,args.host)
    origin=Path(args.workspace);before=workspace.check(origin)
    binding=json.loads(read(origin/'compute-binding.json'))
    body={k:v for k,v in binding.items() if k!='binding_sha256'}
    if (before['config_sha256']!=digest(previous) or binding['config_sha256']!=digest(previous)
            or binding.get('binding_sha256')!=digest(body) or before.get('compute_binding')!=digest(body)
            or set(binding['machines'])!=set(previous['hosts'])):
        raise ValueError('original bound workspace must match the unchanged existing deployment')
    workspace.pinned(before['kolla_source'],cfg['kolla']['revision'])
    lock,files=workspace.rendered(cfg,args.rendered)
    audits=json.loads(read(args.audit))
    if set(audits)!=set(cfg['hosts']):raise ValueError('exact current host audit set required')
    import basic_preflight
    basic_preflight.policies(cfg,audits,json.loads(files['basic-preflight.json']))
    physical=json.loads(files['resource-plan.json'])
    wanted={n:f['host_identity_sha256'] for n,f in audits.items()}
    if (any(wanted.get(n)!=v for n,v in binding['machines'].items())
            or len(set(wanted.values()))!=len(wanted)):
        raise ValueError('existing host identities must be preserved and new identity must be unique')
    with tempfile.TemporaryDirectory(prefix='spvm-new-compute-intent-') as temporary:
        mapping=Path(temporary)/'requests.json'
        write(mapping,{'schema_version':1,'hosts':{args.host:args.request}})
        selected={'hosts':{args.host:cfg['hosts'][args.host]},'guest_release':cfg['guest_release']}
        selected_physical={**physical,'hosts':{args.host:physical['hosts'][args.host]}}
        specs,_=selected_requests(selected,selected_physical,mapping)
    spec=specs[args.host]
    if (spec['machine_sha256']!=wanted[args.host]
            or spec['compute_uuid']!=native_uuid(audits[args.host]['hostname'])
            or spec['compute_uuid'] in {h['compute_uuid'] for h in binding['hosts'].values()}):
        raise ValueError('new helper must bind the audited native Nova compute identity')
    source_manifest(backend_path,spec,bootstrap=True)
    gpus={}
    for n,host in binding['hosts'].items():
        if file_hash(host['request_file'])!=host['request_sha256']:
            raise ValueError('existing helper request changed')
        raw=json.loads(read(host['request_file']))
        selected_gpus=set(raw['gpus'])
        gpus[n]=[g for g in physical['hosts'][n]['gpu_candidates'] if g['address'] in selected_gpus]
        if {g['address'] for g in gpus[n]}!=selected_gpus:
            raise ValueError('existing registered GPU identity changed')
    gpus[args.host]=[g for g in physical['hosts'][args.host]['gpu_candidates'] if g['address'] in spec['gpus']]
    fragments=nova_inputs.nova_files(cfg,physical,gpus)
    for n,content in fragments.items():
        if (origin/'kolla'/n).exists() and read(origin/'kolla'/n)!=content.encode():
            raise ValueError('existing Nova overrides/PCI aliases changed; separate controlled update required')
    output=Path(args.output)
    if (not output.is_absolute() or output.parent.resolve()!=output.parent
            or output.is_relative_to(origin) or origin.is_relative_to(output)):
        raise ValueError('new non-overlapping canonical workspace required')
    output.mkdir(mode=0o700)
    journal={'status':'STAGING_NEW_COMPUTE_WORKSPACE','hosts_changed':False,'guests_changed':False}
    write(output/'journal.json',journal)
    try:
        for name in before['files']:
            if name not in ('hosts.ini','host-profile.yml','compute-binding.json','kolla/globals.yml'):
                workspace.private_file(output/name,read(origin/name))
        for name in ('hosts.ini','host-profile.yml'):
            workspace.private_file(output/name,files[name])
        for name,content in fragments.items():
            target=output/'kolla'/name
            if not target.exists():workspace.private_file(target,content)
        globals_=yaml.safe_load(read(origin/'kolla/globals.yml'))
        globals_.update(node_custom_config=str(output/'kolla/config'),
            kolla_certificates_dir=str(output/'kolla/certificates'),
            kolla_internal_fqdn_cert=str(output/'kolla/certificates/haproxy-internal.pem'),
            kolla_external_fqdn_cert=str(output/'kolla/certificates/haproxy.pem'))
        workspace.private_file(output/'kolla/globals.yml',yaml.safe_dump(globals_,sort_keys=True))
        request=output/'helper-requests'/(args.host+'.json')
        request.parent.mkdir(mode=0o700,exist_ok=True);write(request,spec)
        member={'inventory_name':cfg['hosts'][args.host].get('inventory_name',args.host),
            'machine_sha256':spec['machine_sha256'],'compute_uuid':spec['compute_uuid'],
            'request_file':str(request),'request_sha256':file_hash(request),
            'bootstrap_auth_url':binding['bootstrap_runtime']['auth_url'],
            'bootstrap_region':binding['bootstrap_runtime']['region_name'],
            'registered_gpus':spec['gpus'],'state_budgets_gib':{p['id']:p['budget_gib'] for p in spec['pools']}}
        body.update(config_sha256=digest(cfg),machines=wanted,
            source_workspace_sha256=digest(before),hosts={**body['hosts'],args.host:member})
        body['binding_sha256']=digest(body)
        write(output/'compute-binding.json',body)
        receipt={**before,'config_sha256':digest(cfg),'render_lock_sha256':digest(lock),
            'compute_binding':body['binding_sha256'],'files':workspace.file_set(output)}
        write(output/'workspace.json',receipt)
        journal['status']=receipt['status'];write(output/'journal.json',journal)
        if workspace.check(origin)!=before:raise ValueError('original workspace changed')
        for name in before['files']:
            if name.startswith(('kolla/certificates/','private-api-ca.')) or name=='kolla/passwords.yml':
                if file_hash(output/name)!=before['files'][name]:raise ValueError('credential or CA drift')
        workspace.check(output)
        return {'status':'NEW_COMPUTE_WORKSPACE_STAGED_NOT_ENROLLED','host':args.host,
            'workspace':str(output),'binding_sha256':body['binding_sha256'],
            'credentials_regenerated':False,'hosts_changed':False,'guests_changed':False}
    except BaseException:
        journal['status']='NEEDS_REVIEW_NEW_COMPUTE_WORKSPACE';write(output/'journal.json',journal);raise


def main(backend_path,argv):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['stage-add-compute-workspace'])
    for name in ('config','new-config','workspace','rendered','audit','request','host','output'):
        p.add_argument('--'+name,required=True)
    args=p.parse_args(argv)
    from openstack_kolla_existing import backend
    module=backend(backend_path)
    import kolla_inputs
    print(json.dumps(stage(args,backend_path,module,kolla_inputs)));return 0
