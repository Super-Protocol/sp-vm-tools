"""Ship approved stdlib phase code over strict SSH; never run operator shell text."""
import json
from pathlib import Path
import subprocess

from openstack_compute_image import read
from openstack_container_engine import write


def prepare(value,path,backend,output):
    request=json.loads(read(value['preparation']))
    if not {'network_request','geneve_request'}<=set(request):return
    cfg=backend.deployment_config(value['config'])
    network=json.loads(read(request['network_request']));geneve=json.loads(read(request['geneve_request']))
    tools={str(Path('tools')/p.name):read(p).decode() for p in Path(__file__).parent.glob('*.py')}
    driver={str(Path('driver/deployment')/p.relative_to(Path(path).parent)):read(p).decode()
            for p in Path(path).parent.rglob('*.py') if '__pycache__' not in p.parts}
    sources={**tools,**driver}
    stages=[('geneve',n) for n in sorted(geneve['hosts'])]
    stages += [('management',n) for n in sorted(cfg['hosts'])]
    stages += [('nat',network['nat']['host'])]
    for phase,name in stages:
        remote_output='/var/lib/spvm-installer/'+cfg['deployment_id']+'/network-'+output.name+'-'+name+'-'+phase
        data={'cfg':cfg,'network':network,'geneve':geneve,'phase':phase,'host':name,
              'allow_experimental_ubuntu26':value['allow_experimental_ubuntu26'],'output':remote_output,
              'repair_stale_networkd_cache':request.get('repair_stale_networkd_cache',False)}
        source='SOURCES='+repr(sources)+'\nDATA='+repr(data)+'\n'+'''
import json,sys,tempfile,os
from pathlib import Path
from types import SimpleNamespace as R
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='spvm-network-stage-') as directory:
 root=Path(directory)
 for name,text in SOURCES.items():
  p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
 sys.path.insert(0,str(root/'tools'))
 from openstack_container_engine import write
 phase=DATA['phase']
 if phase=='geneve':import openstack_geneve_guard as module
 else:import openstack_network_prepare as module
 config=root/'config.json';write(config,DATA['cfg'])
 network=root/'request.json';write(network,DATA['geneve'] if phase=='geneve' else DATA['network'])
 backend=R(__file__=str(root/'driver/deployment/installer.py'),deployment_config=lambda p:DATA['cfg'])
 args=R(action='plan-geneve-guard' if phase=='geneve' else 'plan-'+phase,
  config=str(config),network_request=str(network),host=DATA['host'],
  allow_experimental_ubuntu26=DATA['allow_experimental_ubuntu26'],output=DATA['output'],plan_sha256=None)
 target=Path(args.output);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 for parent in (target.parent,*target.parent.parents):
  if parent.is_symlink() or parent.stat().st_uid!=0 or parent.stat().st_mode & 0o022:raise ValueError('untrusted transaction parent')
 cfg,policy,binding=module.inputs(args,backend);unit,files=module.artifacts(cfg,policy)
 host=module.Host()
 if phase=='management':
  installed=host.path('/etc/systemd/system/'+unit).exists()
  for name,(body,mode) in files.items():
   p=host.trusted(name)
   if p.exists() and (not p.is_file() or p.read_bytes()!=body or p.stat().st_mode & 0o777!=mode
                     or not installed and name!=module.RUNTIME):
    raise ValueError('occupied or conflicting management artifact before cache preparation')
  from openstack_management_cache import prepare as prepare_cache
  prepare_cache({'machine_sha256':binding['machine_sha256'],'interface':policy['management_interface'],
   'address':policy['management_address'],'vip':policy.get('vip'),
   'allow_reload':DATA['repair_stale_networkd_cache']},target.parent/(target.name+'-address'))
 if host.path('/etc/systemd/system/'+unit).exists():
  host.inspect(cfg,policy,binding,files,unit,installed=True)
  target.mkdir(mode=0o700);write(target/'verified.json',{'status':'OWNED_NETWORK_PHASE_VERIFIED','phase':phase})
  print(json.dumps({'status':'OWNED_NETWORK_PHASE_VERIFIED','phase':phase}))
 else:
  plan=module.execute(args,backend);args.plan_sha256=plan['plan_sha256']
  args.action='prepare-geneve-guard' if phase=='geneve' else 'prepare-'+phase
  result=module.execute(args,backend)
  print(json.dumps({'status':result['status'],'phase':phase}))
'''
        result=subprocess.run(backend.ssh_arguments(cfg['hosts'][name])+['sudo -n /usr/bin/python3 -'],
                              input=source,text=True,capture_output=True,timeout=600)
        if result.returncode:raise ValueError('owned host network stage refused; inspect remote journal')
        record=json.loads(result.stdout)
        write(output/('network-'+name+'-'+phase+'.json'),record)
