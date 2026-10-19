"""Read-only aggregate of prepared initial hosts; never authorize deployment."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback

from openstack_container_engine import digest, file_hash, write
import openstack_kolla_packages as preparation
import openstack_kolla_prechecks as prechecks


def inputs(args,backend_path,backend):
    selection=argparse.Namespace(**{**vars(args),'action':'plan-api-trust'})
    cfg,receipt,source,code,intent=preparation.inputs(selection,backend_path,backend)
    intent=copy.deepcopy(intent)
    intent['preparation_profile']='initial-readiness'
    intent['readiness_installer_sha256']=file_hash(Path(__file__))
    for name,wanted in intent['requests'].items():
        host=cfg['hosts'][name]
        wanted.update(package_profile='initial-readiness',
            management_interface=host['management_interface'],tunnel_interface=host['tunnel_interface'],
            needs_ipv4_forwarding=cfg['network']['mode']=='host-nat' and 'network' in host['roles'])
    return cfg,receipt,source,code,intent


def assess(facts,intent):
    guards=[name+':'+b for name,f in facts.items() for b in f['blockers']]
    if guards:
        return {'status':'INITIAL_HOSTS_BLOCKED','blockers':guards,'stages':[],
            'next_step':{'action':'review-host-blockers','supported':False,'requires_plan_approval':False}}
    missing_base=[];missing_sdk=[];unusable_sdk=[];missing_trust=[];network=[];invalid=[]
    for name,f in facts.items():
        if (not isinstance(f.get('base_packages'),dict) or not isinstance(f.get('host_sdk'),dict)
                or type(f['host_sdk'].get('ready')) is not bool
                or not isinstance(f.get('api_trust'),dict) or not isinstance(f.get('local_network'),dict)
                or not f['local_network'].get('interfaces')
                or not isinstance(f['local_network'].get('blockers'),list)
                or f['local_network'].get('connectivity_accepted') is not False):
            invalid.append(name+':INCOMPLETE_READINESS_OBSERVATION');continue
        packages=set(f['base_packages'])
        if not set(preparation.PACKAGES)<=packages:missing_base.append(name)
        if not set(preparation.SDK_PACKAGES)<=packages:missing_sdk.append(name)
        elif not f['host_sdk']['ready']:unusable_sdk.append(name)
        if not preparation.ready(f,'api-trust',intent):missing_trust.append(name)
        network.extend(name+':'+b for b in f['local_network']['blockers'])
    if invalid:
        return {'status':'INITIAL_HOSTS_BLOCKED','blockers':invalid,'stages':[],
            'next_step':{'action':'review-incomplete-observation','supported':False,'requires_plan_approval':False}}
    stages=[
        {'id':'base-packages','status':'MISSING' if missing_base else 'READY','hosts':missing_base},
        {'id':'host-sdk','status':'BLOCKED' if unusable_sdk else ('MISSING' if missing_sdk else 'READY'),
         'hosts':sorted(set(missing_sdk+unusable_sdk))},
        {'id':'api-trust','status':'MISSING' if missing_trust else 'READY','hosts':missing_trust},
        {'id':'local-network','status':'BLOCKED' if network else 'LOCAL_CHECKS_PASSED_NOT_CONNECTIVITY_ACCEPTED',
         'blockers':network},
        {'id':'kolla-prechecks','status':'REQUIRES_FRESH_APPROVED_RUN'},
        {'id':'deploy','status':'NOT_IMPLEMENTED_OR_AUTHORIZED'},
        {'id':'cloud-enrollment','status':'NOT_CHECKED'},
        {'id':'swarm-ui-acceptance','status':'NOT_CHECKED'}]
    # No package or trust mutation is recommended while local networking is known broken.
    if network:action='manual-network-preparation';supported=False
    elif unusable_sdk:action='review-installed-host-sdk';supported=False
    elif missing_base:action='plan-base-packages';supported=True
    elif missing_sdk:action='plan-host-sdk';supported=True
    elif missing_trust:action='plan-api-trust';supported=True
    else:action='plan-kolla-prechecks';supported=True
    pending=bool(network or unusable_sdk or missing_base or missing_sdk or missing_trust)
    blockers=(network+[n+':INSTALLED_SDK_UNUSABLE_REVIEW_REQUIRED' for n in unusable_sdk]
        +[n+':BASE_PACKAGES_MISSING' for n in missing_base]+[n+':HOST_SDK_PACKAGES_MISSING' for n in missing_sdk]
        +[n+':API_CA_NOT_REGISTERED' for n in missing_trust])
    return {'status':'INITIAL_HOSTS_NEED_PREPARATION' if pending else 'INITIAL_HOSTS_READY_FOR_PRECHECKS_NOT_DEPLOYABLE',
        'blockers':blockers,'stages':stages,
        'next_step':{'action':action,'supported':supported,'requires_plan_approval':supported}}


def execute(args,backend_path,backend):
    started=time.time()
    cfg,receipt,source,code,intent=inputs(args,backend_path,backend)
    facts={name:prechecks.observe(backend,host,code,intent['requests'][name],args.known_hosts)
           for name,host in cfg['hosts'].items()}
    if time.time()-started>900:raise ValueError('readiness observations exceeded freshness window')
    if inputs(args,backend_path,backend)[4]!=intent:raise ValueError('inputs changed during observation')
    result={'schema_version':1,'kind':'initial-host-status-v1','observed_at':int(started),
        'intent_sha256':digest(intent),'workspace_sha256':digest(receipt),'hosts':facts,
        'deploy_authorized':False,'mutations':[],'guests_launched':False,
        'warnings':['LOCAL_NETWORK_CHECKS_DO_NOT_ACCEPT_GENEVE_OR_FIREWALL_CONNECTIVITY',
                    'OBSERVED_INTERFACE_MTU_IS_NOT_END_TO_END_MTU_ACCEPTANCE',
                    'STATUS_REPORT_IS_NOT_AN_APPLY_AUTHORIZATION'],**assess(facts,intent)}
    output=Path(args.output);workspace=Path(args.workspace)
    if (not output.is_absolute() or output.resolve()!=output or output.is_relative_to(workspace)
            or workspace.is_relative_to(output)):raise ValueError('new separate absolute report directory required')
    output.mkdir(mode=0o700,exist_ok=False)
    result['report_sha256']=digest(result)
    write(output/'initial-host-status.json',result)
    return result


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['initial-host-status'])
    for name in ('config','workspace','known-hosts','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--allow-experimental-ubuntu26',action='store_true')
    args=parser.parse_args(argv)
    try:
        sys.path.insert(0,str(Path(backend_path).parent))
        spec=importlib.util.spec_from_file_location('spvm_initial_status_backend',backend_path)
        backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        result=execute(args,backend_path,backend)
        print(json.dumps({k:v for k,v in result.items() if k in
            ('status','blockers','next_step','deploy_authorized','report_sha256')}))
        return 0 if result['status']=='INITIAL_HOSTS_READY_FOR_PRECHECKS_NOT_DEPLOYABLE' else 2
    except Exception as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: initial host status refused; no deployment attempted. '
            +type(exc).__name__+' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
