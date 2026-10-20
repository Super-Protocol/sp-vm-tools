"""Read-only aggregate for an occupied Kolla cloud; never repair or deploy."""
import argparse
import importlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import yaml
from openstack_compute_image import read
from openstack_container_engine import digest, file_hash, write, collection_hash
import openstack_kolla_workspace as workspace
import openstack_kolla_compute_scope as compute


def backend(path):
    directory = Path(path).parent
    package = directory.parent
    sys.path[:0] = [str(directory), str(package), str(package/'nova-spvm')]
    module = importlib.import_module('installer')
    if Path(module.__file__).resolve() != Path(path).resolve():
        raise ValueError('backend module collision')
    return module


def inputs(args, module):
    cfg = module.deployment_config(args.config)
    root = Path(args.workspace)
    receipt = workspace.check(root)
    binding = json.loads(read(root/'compute-binding.json'))
    body = {k:v for k,v in binding.items() if k != 'binding_sha256'}
    if (binding.get('binding_sha256') != digest(body)
            or receipt.get('compute_binding') != digest(body)
            or receipt['config_sha256'] != digest(cfg)
            or binding['config_sha256'] != digest(cfg)
            or cfg['kolla']['compute_image'] != binding['image_reference']
            or cfg['kolla']['compute_image_id'] != binding['image_id']):
        raise ValueError('config/workspace/image binding mismatch')
    workspace.pinned(receipt['kolla_source'], receipt['kolla_revision'])
    collection_hash(receipt['collections'], receipt['collections_sha256'])
    if file_hash(receipt['kolla_python']) != receipt['kolla_python_sha256']:
        raise ValueError('Kolla interpreter drift')
    trust = Path(args.known_hosts)
    read(trust)
    if (not trust.is_absolute() or any(c.isspace() for c in str(trust))
            or any(ord(c) < 32 for c in str(trust))):
        raise ValueError('absolute literal known-hosts path required')
    selected = {h.get('inventory_name',n):n for n,h in cfg['hosts'].items() if 'compute' in h['roles']}
    if len(selected) != sum('compute' in h['roles'] for h in cfg['hosts'].values()) or not selected:
        raise ValueError('unique compute inventory aliases required')
    specs = {}; hashes = {}
    transport = ('-F /dev/null -o IdentityAgent=none -o IdentitiesOnly=yes '
                 '-o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile='+str(trust))
    for filename in args.compute_request:
        spec = compute.request(json.loads(read(filename)))
        alias = spec['host']
        if alias not in selected or alias in specs or spec['mode'] != 'verify':
            raise ValueError('exactly one verify-only request per configured compute required')
        name = selected[alias]
        expected = {'source':receipt['kolla_source'], 'revision':receipt['kolla_revision'],
                    'kolla_python':receipt['kolla_python'], 'collections':receipt['collections'],
                    'inventory':str(root/'hosts.ini'), 'configdir':str(root/'kolla'),
                    'machine_sha256':binding['machines'][name],
                    'image':binding['image_reference'], 'image_id':binding['image_id']}
        if any(spec[k] != v for k,v in expected.items()):
            raise ValueError('compute request escapes bound deployment')
        profile = yaml.safe_load(read(spec['profile']))
        original = yaml.safe_load(read(root/'host-profile.yml'))
        overrides = {'ansible_python_interpreter','ansible_ssh_common_args'}
        if ({k:v for k,v in profile.items() if k not in overrides}
                != {k:v for k,v in original.items() if k not in overrides}
                or profile.get('ansible_ssh_common_args') != transport):
            raise ValueError('runtime profile changes beyond pinned transport/interpreter')
        interpreter = profile.get('ansible_python_interpreter','/usr/bin/python3')
        if not Path(interpreter).is_absolute() or any(c.isspace() for c in interpreter):
            raise ValueError('explicit host interpreter required')
        compute.inputs(spec)  # Full source/config validation, no remote tasks.
        specs[alias] = spec
        hashes[str(filename)] = file_hash(filename)
        hashes[spec['profile']] = file_hash(spec['profile'])
    if set(specs) != set(selected):
        raise ValueError('missing configured compute requests')
    if args.controller not in cfg['hosts'] or 'control' not in cfg['hosts'][args.controller]['roles']:
        raise ValueError('explicit configured controller required')
    for value in (args.controller_python,args.openrc):
        if not Path(value).is_absolute() or any(ord(c)<32 for c in value):
            raise ValueError('absolute controller interpreter/OpenRC required')
    return cfg, specs, {'workspace':digest(receipt),'binding':digest(body),
                        'known_hosts':file_hash(trust),'requests':hashes}


def remote(module, host, code):
    p = subprocess.run(module.ssh_arguments(host)+['sudo -n /usr/bin/python3 -I -'],
                       input=code, text=True, capture_output=True, timeout=180)
    if p.returncode or len(p.stdout)>4194304:
        raise ValueError('read-only runtime snapshot failed; diagnostics suppressed')
    return json.loads(p.stdout)


def snapshots(module, cfg):
    code = compute.GUARD + r'''
import json,hashlib
from pathlib import Path
value=spvm_compute_snapshot({'mode':'verify'});guests={}
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  argv=(p/'cmdline').read_bytes().split(b'\0')
  if not argv or b'qemu-system' not in argv[0] or b'-uuid' not in argv:continue
  guests[argv[argv.index(b'-uuid')+1].decode()]={'pid':int(p.name),'start_ticks':(p/'stat').read_text().rsplit(')',1)[1].split()[19]}
 except (FileNotFoundError,ProcessLookupError):continue
value['qemu']=guests
value['compute_config']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/etc/kolla/nova-compute').rglob('*') if p.is_file()}
print(json.dumps(value))
'''
    return {n:remote(module,h,code) for n,h in cfg['hosts'].items()}


def execute(args, path):
    started = time.time()
    output = Path(args.output); root = Path(args.workspace)
    if (not output.is_absolute() or output.resolve()!=output or output == root
            or output.is_relative_to(root) or root.is_relative_to(output)):
        raise ValueError('new separate canonical private output required')
    output.mkdir(mode=0o700, exist_ok=False)
    result = {'schema_version':1,'kind':'existing-kolla-verification-v1',
              'status':'NEEDS_REVIEW','observed_at':int(started),'checks':[],
              'mutations':[],'deploy_executed':False,'repair_authorized':False,
              'scope':['bound inputs and pinned source','native compute container NOOP',
                       'physical/host/Nova/Placement/PCI/state accounting','runtime continuity'],
              'not_accepted':['full control-plane rendered-to-installed configuration comparison',
                              'all service/API functional readiness','loaded Python code identity',
                              'full deploy/reconfigure idempotency','fresh OS installation']}
    module = backend(path); original_ssh = module.ssh_arguments
    def ssh(host):
        argv = original_ssh(host)
        argv[1:1] = ['-F','/dev/null','-o','UserKnownHostsFile='+str(args.known_hosts),'-o','ForwardAgent=no']
        return argv
    module.ssh_arguments = ssh
    stage = 'inputs'; before = None; cfg = None
    try:
        cfg, specs, fingerprint = inputs(args,module)
        result['inputs_sha256'] = digest(fingerprint)
        result['checks'].append({'stage':stage,'passed':True})
        stage = 'runtime-before'; before = snapshots(module,cfg)
        write(output/'runtime-before.json',before)
        stage = 'accounting'
        accounting = importlib.import_module('deployment_accounting')
        account = accounting.collect(cfg,args.controller,args.controller_python,args.openrc,output/'accounting')
        if account['status'] != 'ACCOUNTING_RECONCILED' or account['blockers'] or account['mutations']:
            result['accounting_blockers'] = account['blockers']
            raise ValueError('accounting drift')
        result['checks'].append({'stage':stage,'passed':True,'report_sha256':account['report_sha256']})
        for alias in sorted(specs):
            stage = 'compute:'+alias
            spec = specs[alias]; filename = output/(alias+'-request.json'); write(filename,spec)
            selection = argparse.Namespace(action='plan-kolla-compute-container',request=str(filename),
                        output=str(output/alias),plan_sha256=None)
            planned = compute.execute(selection)
            selection.action = 'reconcile-kolla-compute-container'
            selection.plan_sha256 = planned['plan_sha256']
            observed = compute.execute(selection)
            if observed.get('status')!='COMPUTE_CONTAINER_VERIFIED' or observed.get('kolla_exit')!=0:
                raise ValueError('native compute NOOP refused')
            result['checks'].append({'stage':stage,'passed':True,'plan_sha256':planned['plan_sha256']})
        stage = 'input-continuity'
        if inputs(args,module)[2] != fingerprint or time.time()-started>900:
            raise ValueError('input drift or stale observation')
        result['checks'].append({'stage':stage,'passed':True})
        result['status'] = 'EXISTING_KOLLA_SCOPED_VERIFIED'
    except Exception as error:
        result['checks'].append({'stage':stage,'passed':False,'error_type':type(error).__name__})
    finally:
        if before is not None:
            try:
                after = snapshots(module,cfg); write(output/'runtime-after.json',after)
                equal = before == after
                result['checks'].append({'stage':'runtime-continuity','passed':equal})
                if not equal:result['status']='NEEDS_REVIEW'
            except Exception as error:
                result['status']='NEEDS_REVIEW'
                result['checks'].append({'stage':'runtime-continuity','passed':False,'error_type':type(error).__name__})
        module.ssh_arguments = original_ssh
        result['report_sha256'] = digest(result)
        write(output/'existing-kolla.json',result)
    return result


def main(path, argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['verify-kolla-existing'])
    for name in ('config','workspace','known-hosts','controller','controller-python','openrc','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--compute-request',required=True,action='append')
    args = parser.parse_args(argv)
    try:
        result = execute(args,path)
        print(json.dumps(result))
        return 0 if result['status']=='EXISTING_KOLLA_SCOPED_VERIFIED' else 2
    except Exception:
        print('ERROR: existing Kolla verification refused; no repair/deploy attempted.',file=sys.stderr)
        return 2
