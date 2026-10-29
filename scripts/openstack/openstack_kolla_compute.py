"""Bind fresh compute intent to an isolated Kolla workspace. No host deployment."""
import argparse
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import sys
import traceback

import yaml
from openstack_container_engine import digest, file_hash, write
from openstack_compute_image import context, wheel_bytes, read, TAG_RE, BASE_RE
from openstack_host_helper import request, bootstrap_policy, source_manifest
import openstack_kolla_workspace as workspace


def selected_requests(cfg,physical,path):
    value=json.loads(read(path))
    computes={n for n,h in cfg['hosts'].items() if 'compute' in h['roles']}
    if (not isinstance(value,dict) or set(value)!={'schema_version','hosts'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or not isinstance(value['hosts'],dict) or set(value['hosts'])!=computes
            or physical.get('status')!='RESOURCE_FACTS_VALIDATED_NOT_DEPLOYABLE'
            or set(physical.get('hosts',{}))!=computes):
        raise ValueError('one explicit helper request per audited compute required')
    specs={};gpus={};identities=set();uuids=set()
    for name in sorted(computes):
        spec=request(value['hosts'][name]);actual=physical['hosts'][name]
        # Bind the helper/cache bootstrap build to explicit root-owned host
        # intent. A mixed cloud may use a release default while a new non-CC
        # compute explicitly bootstraps from a debug build. Never accept an
        # arbitrary helper-request build or infer it from detected hardware.
        selected_release=cfg['hosts'][name].get('bootstrap_release',cfg['guest_release'])
        if not re.fullmatch(r'build-[1-9][0-9]*-(debug|release)',str(selected_release)):
            raise ValueError('explicit pinned compute bootstrap release required')
        if cfg['hosts'][name]['launch_mode']=='untrusted' and not selected_release.endswith('-debug'):
            raise ValueError('non-CC bootstrap release must be debug')
        if (spec['launch_mode']!=cfg['hosts'][name]['launch_mode']
                or spec['machine_sha256']!=actual['host_identity_sha256'] or spec['machine_sha256'] in identities
                or spec['compute_uuid'] in uuids or spec['release']!=selected_release):
            raise ValueError('helper request must bind unique audited machine/compute/release')
        identities.add(spec['machine_sha256']);uuids.add(spec['compute_uuid'])
        pools={p['id']:p for p in spec['pools']}
        if set(pools)!=set(actual['storage_pools']):raise ValueError('helper and deployment pools differ')
        for identifier,pool in pools.items():
            audited=actual['storage_pools'][identifier]
            if (pool['root']!=audited['path'] or pool['mount']!=audited['mount']
                    or pool['reserve_gib']!=audited['reserve_gib']
                    or pool['budget_gib']>audited['physical_budget_ceiling_gib']):
                raise ValueError('state pool path/reserve/budget differs from audited intent')
        candidates={g['address']:g for g in actual['gpu_candidates']}
        if not set(spec['gpus'])<=set(candidates):raise ValueError('selected GPU is not an audited VFIO candidate')
        gpus[name]=[candidates[bdf] for bdf in spec['gpus']]
        specs[name]=spec
    return specs,gpus


def stage(args,backend_path,backend,nova_inputs):
    cfg=backend.deployment_config(args.config)
    before=workspace.check(args.workspace)
    lock,files=workspace.rendered(cfg,args.rendered)
    if (before['config_sha256']!=digest(cfg) or before['render_lock_sha256']!=digest(lock)
            or 'compute_binding' in before):
        raise ValueError('unbound workspace from this exact config/render required')
    physical=json.loads(files['resource-plan.json'])
    specs,gpus=selected_requests(cfg,physical,args.requests)
    for spec in specs.values():source_manifest(backend_path,spec,bootstrap=True)
    image=context(args.image_context,args.image_context_sha256)
    _,version,manifest_sha=wheel_bytes(backend_path,Path(args.image_context)/'driver.whl',image['files']['driver.whl'])
    if image['manifest_sha256']!=manifest_sha or image['driver_version']!=version:
        raise ValueError('image context belongs to a different driver manifest')
    image_name=cfg['kolla'].get('compute_image','')
    if (not (TAG_RE.fullmatch(image_name) or BASE_RE.fullmatch(image_name))
            or (TAG_RE.fullmatch(image_name) and image_name.rsplit(':',1)[-1] in ('latest','main','master'))
            or not re.fullmatch('sha256:[a-f0-9]{64}',str(cfg['kolla'].get('compute_image_id','')))):
        raise ValueError('explicit target compute image reference and immutable ID required')
    vip=ipaddress.ip_address(cfg['network']['api_vip'])
    endpoint='https://'+('['+str(vip)+']' if vip.version==6 else str(vip))+':5000/v3'
    policy=bootstrap_policy(endpoint,args.region)
    preflight=json.loads(files['basic-preflight.json'])
    machines={n:preflight.get('hosts',{}).get(n,{}).get('machine_sha256',specs.get(n,{}).get('machine_sha256'))
              for n in cfg['hosts']}
    if (any(not re.fullmatch('[a-f0-9]{64}',str(m)) for m in machines.values())
            or len(set(machines.values()))!=len(machines)
            or any(machines[n]!=s['machine_sha256'] for n,s in specs.items())):
        raise ValueError('unique audited machine fingerprints required for every host role')
    fragments=nova_inputs.nova_files(cfg,physical,gpus)
    expected={'config/nova.conf'}|{
        'config/nova/'+cfg['hosts'][n].get('inventory_name',n)+'/nova-compute.conf' for n in specs}
    if (set(fragments)!=expected or any(not isinstance(body,str) or len(body)>1048576 for body in fragments.values())):
        raise ValueError('exact Nova override allowlist required')
    origin=Path(args.workspace)
    output=Path(args.output)
    if (not output.is_absolute() or output.parent.resolve()!=output.parent
            or output.is_relative_to(origin) or origin.is_relative_to(output)):
        raise ValueError('new non-overlapping absolute private workspace required')
    output.mkdir(mode=0o700,exist_ok=False)
    journal={'schema_version':1,'status':'BINDING_KOLLA_COMPUTES','hosts_changed':False}
    write(output/'journal.json',journal)
    try:
        overrides={'kolla/'+n:body for n,body in fragments.items()}
        for name in before['files']:
            if name!='kolla/globals.yml' and name not in overrides:
                workspace.private_file(output/name,read(origin/name))
        globals_=yaml.safe_load(read(origin/'kolla/globals.yml'))
        globals_.update(nova_compute_image_full=image_name,
            openstack_region_name=args.region,
            node_custom_config=str(output/'kolla/config'),kolla_certificates_dir=str(output/'kolla/certificates'),
            kolla_internal_fqdn_cert=str(output/'kolla/certificates/haproxy-internal.pem'),
            kolla_external_fqdn_cert=str(output/'kolla/certificates/haproxy.pem'),
            nova_instance_datadir_volume='/var/lib/nova',enable_nova_libvirt_container=False,
            # Pinned Kolla already binds /run shared. Nested binds below it
            # propagate self-mounts back to the host and accumulate on recreate.
            nova_compute_extra_volumes=['/etc/spvm/compute-pools.json:/etc/spvm/compute-pools.json:ro',
                '/etc/spvm/launch-policy.json:/etc/spvm/launch-policy.json:ro',
                '/etc/spvm/runtime-services.json:/etc/spvm/runtime-services.json:ro'])
        workspace.private_file(output/'kolla/globals.yml',yaml.safe_dump(globals_,sort_keys=True))
        for name,body in overrides.items():workspace.private_file(output/name,body)
        binding={'schema_version':1,'status':'COMPUTE_INTENT_BOUND_NOT_DEPLOYABLE',
            'image_reference':image_name,'image_id':cfg['kolla']['compute_image_id'],
            'image_context_sha256':args.image_context_sha256,'driver_manifest_sha256':manifest_sha,
            'source_workspace_sha256':digest(before),'config_sha256':digest(cfg),
            'bootstrap_runtime':policy,'hosts':{},'hosts_changed':False,'guests_launched':False,
            'machines':machines,
            'remaining':['FRESH_HOST_HELPER_APPLY_AND_IDENTITY_VERIFICATION','PER_HOST_IMAGE_DELIVERY_VERIFICATION',
                'API_CA_TRUST_DISTRIBUTION','NETWORK_HOST_PREREQUISITES','KOLLA_REAL_PRECHECKS_DEPLOY_ACCEPTANCE',
                'PROJECT_NETWORK_BARBICAN_ENROLLMENT']}
        for name,spec in specs.items():
            relative='helper-requests/'+name+'.json'
            workspace.private_file(output/relative,json.dumps(spec,indent=2)+'\n')
            binding['hosts'][name]={'inventory_name':cfg['hosts'][name].get('inventory_name',name),
                'machine_sha256':spec['machine_sha256'],'compute_uuid':spec['compute_uuid'],
                'request_file':str(output/relative),'request_sha256':file_hash(output/relative),
                'bootstrap_auth_url':endpoint,'bootstrap_region':args.region,
                'registered_gpus':spec['gpus'],'state_budgets_gib':{p['id']:p['budget_gib'] for p in spec['pools']}}
        binding['binding_sha256']=digest(binding)
        workspace.private_file(output/'compute-binding.json',json.dumps(binding,indent=2)+'\n')
        # Credentials/TLS keys are copied unchanged; never rerun genpwd/certificates.
        workspace.check(origin)
        for name in ('kolla/passwords.yml','private-api-ca.key','kolla/certificates/api.key'):
            if (origin/name).is_file() and file_hash(origin/name)!=file_hash(output/name):
                raise ValueError('private credential material changed during binding')
        receipt={**before,'files':workspace.file_set(output),'compute_binding':binding['binding_sha256'],
                 'remaining':binding['remaining']}
        write(output/'workspace.json',receipt)
        journal['status']=receipt['status'];write(output/'journal.json',journal)
        workspace.check(output)
        return binding
    except BaseException:
        journal['status']='NEEDS_REVIEW_KOLLA_COMPUTE_BINDING';write(output/'journal.json',journal)
        raise


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage-kolla-computes'])
    for field in ('config','rendered','workspace','requests','image-context','image-context-sha256','region','output'):
        parser.add_argument('--'+field,required=True)
    args=parser.parse_args(argv)
    try:
        sys.path.insert(0,str(Path(backend_path).parent))
        spec=importlib.util.spec_from_file_location('spvm_compute_backend',backend_path)
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        import kolla_inputs
        result=stage(args,backend_path,backend,kolla_inputs)
        print(json.dumps({k:v for k,v in result.items() if k in
            ('status','binding_sha256','hosts_changed','guests_launched')}))
        return 0
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: compute intent binding refused; private artifacts preserved. '+type(exc).__name__
            +' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
