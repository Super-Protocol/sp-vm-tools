"""Stage secret-free portable SPVM enrollment and per-node flavor intent, without API writes."""
import argparse
import json
from pathlib import Path
import re
import sys
import uuid

from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write
import openstack_kolla_workspace as workspace


def identifiers(value):
    if (not isinstance(value,list) or not 1<=len(value)<=64
            or any(not isinstance(x,str) or not re.fullmatch('[A-Za-z0-9-]{1,64}',x) for x in value)
            or len(set(value))!=len(value)):
        raise ValueError('explicit unique Keystone user IDs required')
    return value


def document(raw, binding):
    if (not isinstance(raw,dict) or set(raw)!={'schema_version','projects'}
            or type(raw['schema_version']) is not int or raw['schema_version']!=1
            or not isinstance(raw['projects'],dict) or not 1<=len(raw['projects'])<=128):
        raise ValueError('explicit project enrollment request required')
    result={**binding['bootstrap_runtime']}
    if result.pop('bootstrap_deny_all',None) is not True or result['projects']!={}:
        raise ValueError('deny-all compute binding required')
    projects={}
    for project,spec in raw['projects'].items():
        if (not re.fullmatch('[A-Za-z0-9_-]{1,64}',project) or not isinstance(spec,dict)
                or set(spec)-{'release_registry_user_ids'}!={'publisher_user_ids','reader_user_ids','network_ids','releases'}):
            raise ValueError('unsupported project enrollment fields')
        networks=spec['network_ids'];releases=spec['releases']
        if (not isinstance(networks,list) or not 1<=len(networks)<=64
                or any(not isinstance(n,str) or str(uuid.UUID(n))!=n for n in networks)
                or len(set(networks))!=len(networks)):
            raise ValueError('canonical unique shared Neutron network UUIDs required')
        if (not isinstance(releases,list) or not 1<=len(releases)<=16
                or any(not isinstance(r,str) or not re.fullmatch('build-[1-9][0-9]*-(debug|release)',r) for r in releases)
                or len(set(releases))!=len(releases)):
            raise ValueError('explicit unique release allowlist required')
        # Portable fresh helper currently has exactly one prepared release cache.
        # Never authorize a release whose measured cache/helper gate is not bound.
        for host in binding['hosts'].values():
            helper=json.loads(read(host['request_file']))
            if 'release_registry_user_ids' in spec and 'registered_release_cache_root' not in helper:
                raise ValueError('registered release caches must be explicitly prepared on every compute')
            if set(releases)!={helper['release']} or (helper['release'].endswith('-debug') and not helper['allow_debug']):
                raise ValueError('release not prepared/authorized on every selected compute')
        if 'release_registry_user_ids' in spec:
            identifiers(spec['release_registry_user_ids'])
            if not set(spec['release_registry_user_ids']) <= set(spec['publisher_user_ids']):
                raise ValueError('release registrars must be explicitly enrolled project publishers')
        projects[project]={**spec,'publisher_user_ids':identifiers(spec['publisher_user_ids']),
            'reader_user_ids':identifiers(spec['reader_user_ids']), 'guest_profile':'swarm-nodes-v1-ms',
            'readiness':{'network_mode':'tap','timeout':900,'interval':3,'request_timeout':10}}
    result['projects']=projects
    return result


def stage(args, backend_path):
    root=Path(args.workspace);receipt=workspace.check(root)
    binding=json.loads(read(root/'compute-binding.json'))
    body={k:v for k,v in binding.items() if k!='binding_sha256'}
    if (binding.get('binding_sha256')!=digest(body) or receipt.get('compute_binding')!=digest(body)
            or not binding.get('hosts')):
        raise ValueError('hash-bound compute workspace required')
    for host in binding['hosts'].values():
        if file_hash(host['request_file'])!=host['request_sha256']:raise ValueError('helper request drift')
    raw=json.loads(read(args.request));runtime=document(raw,binding)
    source=Path(backend_path).parent.parent/'nova-spvm'
    if not source.is_dir():source=Path(backend_path).parent.parent
    sys.path.insert(0,str(source))
    from openstack_spvm import spec
    # Use exactly the CLI's resource validation/flavor encoding, not a second ABI.
    profiles=json.loads(read(args.profiles))
    if not isinstance(profiles,list) or not 1<=len(profiles)<=64:raise ValueError('explicit per-node resource profiles required')
    flavors={}
    for resource in profiles:
        resource=spec.resources(resource)
        flavors[spec.flavor_name(resource)]={'resources':resource,'extra_specs':spec.flavor_properties(resource),
            'disk':0,'is_public':False,'state_disk_accounting':'CUSTOM_SPVM_STATE_GB'}
    if workspace.check(root)!=receipt:raise ValueError('workspace changed during enrollment staging')
    for host in binding['hosts'].values():
        if file_hash(host['request_file'])!=host['request_sha256']:raise ValueError('helper request drift')
    result={'schema_version':1,'status':'SPVM_ENROLLMENT_STAGED_NOT_APPLIED',
            'compute_binding_sha256':binding['binding_sha256'],'runtime':runtime,'flavors':flavors,
            'remaining':['VERIFY_KEYSTONE_IDS_AND_PROJECT_MEMBERSHIP','VERIFY_SHARED_NEUTRON_NETWORK_AND_SECURITY_GROUP',
                'BARBICAN_ACCESS_SMOKE_TEST','INSTALL_RUNTIME_ON_EMPTY_COMPUTES',
                'CREATE_PRIVATE_FLAVORS_AND_GLANCE_MARKERS','VERIFY_PLACEMENT_GPU_AND_STATE_CAPACITY',
                'CONFIGURE_DOMAIN_INGRESS'],'api_writes':False,'guests_launched':False}
    result['enrollment_sha256']=digest(result)
    output=Path(args.output)
    if (not output.is_absolute() or output.resolve()!=output or output.is_relative_to(root)
            or root.is_relative_to(output)):raise ValueError('separate canonical private staging directory required')
    output.mkdir(mode=0o700,exist_ok=False)
    write(output/'enrollment-plan.json',result);write(output/'runtime-services.json',runtime)
    return {k:result[k] for k in ('status','enrollment_sha256','api_writes','guests_launched')}


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage-spvm-enrollment'])
    for name in ('workspace','request','profiles','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args(argv)
    try:print(json.dumps(stage(args,backend_path)));return 0
    except Exception:
        print('ERROR: SPVM enrollment staging refused; no API writes performed.',file=sys.stderr);return 2
