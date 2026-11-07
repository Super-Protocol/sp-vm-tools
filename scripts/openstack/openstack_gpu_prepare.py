"""Read-only hardware discovery for installation, never GPU enablement."""
import json
from pathlib import Path
import subprocess


def detect(module,backend,host):
    """Read-only hardware detection. Never enable PCI devices or install tools."""
    source=(Path(backend).parent/'gpu_discovery.py').read_text()
    code='import types,json\nm=types.ModuleType("gpu_discovery");exec('+repr(source)+',m.__dict__)\nprint(json.dumps(m.discover(probe=False)))\n'
    process=subprocess.run(module.ssh_arguments(host)+['sudo','-n','/usr/bin/python3','-I','-'],
        input=code,text=True,capture_output=True,timeout=90)
    if process.returncode or len(process.stdout)>1048576:raise ValueError('GPU hardware detection failed')
    value=json.loads(process.stdout)
    from nova_spvm.gpu_profiles import normalize
    normalize(value)
    if any(d['enabled'] for d in value['devices']):raise ValueError('hardware discovery must not enable GPU devices')
    return value
