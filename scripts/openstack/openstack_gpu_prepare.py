"""Explicit empty-host GPU detection before initial Kolla/helper registration."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import yaml
from openstack_workflow import context
from openstack_container_engine import write
from openstack_skyline_runtime import trusted


def execute(backend,args):
    with context(backend,{'known_hosts':args.known_hosts}) as module:
        cfg=module.deployment_config(args.config)
        output=Path(args.output);trusted(output.parent)
        if not output.is_absolute() or output.resolve()!=output or output.exists():
            raise ValueError('new canonical GPU preparation output required')
        output.mkdir(mode=0o700)
        journal=output/'gpu-preparation.json';state={'status':'PREPARING','hosts':{}};write(journal,state)
        source=(Path(backend).parent/'gpu_discovery.py').read_text()
        resolved=yaml.safe_load(Path(args.config).read_text())
        try:
            for name,h in cfg['hosts'].items():
                if 'compute' not in h['roles']:continue
                # Explicit invocation only. The remote process independently
                # rejects every existing domain/QEMU process before MMIO.
                code='import types,sys,json\nm=types.ModuleType("gpu_discovery");exec('+repr(source)+',m.__dict__)\n'+\
                    'previous='+repr(h.get('gpu_inventory'))+'\n'+'''
# Verify emptiness before installing/querying the tool. discover(probe=True)
# repeats this immediately before MMIO; no VM is stopped or restarted.
import subprocess
p=subprocess.run(['virsh','-c','qemu:///system','list','--all','--uuid'],capture_output=True,text=True,timeout=20)
q=subprocess.run(['pgrep','-f',r'^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$)'],capture_output=True,text=True,timeout=10)
if p.returncode or p.stdout.strip() or q.returncode!=1:raise ValueError('GPU preparation requires an empty host')
m.prepare_tools()
print(json.dumps(m.discover(probe=True,previous=previous)))
'''
                proc=subprocess.run(module.ssh_arguments(h)+['sudo','-n','/usr/bin/python3','-I','-'],input=code,
                    text=True,capture_output=True,timeout=900)
                if proc.returncode or len(proc.stdout)>1048576:raise ValueError('GPU preparation refused; inspect retained transaction')
                raw=json.loads(proc.stdout)
                from nova_spvm.gpu_profiles import catalog
                current=catalog(raw)
                resolved['hosts'][name]['gpu_inventory']=raw
                state['hosts'][name]={'profiles':current['profiles'],
                    'blocked_devices':{d['address']:d['blocker'] for d in current['devices'] if d['blocker']},
                    'groups':current['groups']};write(journal,state)
            candidate=output/'deployment.resolved.yaml';candidate.write_text(yaml.safe_dump(resolved,sort_keys=False))
            module.deployment_config(candidate)
            state.update(status='GPU_INVENTORY_PREPARED_NOT_ENROLLED',resolved_config=str(candidate));write(journal,state)
            return state
        except BaseException:
            state['status']='REVIEW_REQUIRED';write(journal,state);raise


def main(backend,argv):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare-gpus'])
    for name in ('config','known-hosts','output'):p.add_argument('--'+name,required=True)
    args=p.parse_args(argv)
    if os.geteuid()!=0:raise ValueError('root GPU preparation coordinator required')
    print(json.dumps(execute(backend,args)));return 0
