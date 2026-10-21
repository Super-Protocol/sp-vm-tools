"""Optional, explicit Skyline completion/verification in the common workflow."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from openstack_compute_image import read
import openstack_kolla_existing as existing
import openstack_skyline_runtime as runtime


def specification(filename, mode):
    value = json.loads(read(filename))
    if mode == 'fresh':
        if set(value) != {'runtime_dir', 'runtime_sha256'}:
            raise ValueError('exact staged runtime specification required')
        if not re.fullmatch(r'[0-9a-f]{64}', value['runtime_sha256']):
            raise ValueError('pinned runtime receipt required')
        if not Path(value['runtime_dir']).is_absolute() or Path(value['runtime_dir']).resolve() != Path(value['runtime_dir']):
            raise ValueError('canonical runtime directory required')
        receipt = Path(value['runtime_dir'])/'runtime-stage.json'
        if hashlib.sha256(receipt.read_bytes()).hexdigest() != value['runtime_sha256']:
            raise ValueError('staged runtime receipt drift')
        return value
    fields = {'origin', 'manifest', 'ca_file', 'image_id', 'files_sha256', 'units'}
    if mode != 'existing' or not isinstance(value, dict) or set(value) != fields:
        raise ValueError('exact existing Skyline expectations required')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', value['image_id']):
        raise ValueError('immutable Skyline image required')
    required = {'spvm-web-nodes.service', 'spvm-web-capacity.timer', 'spvm-web-publish.timer'}
    if not isinstance(value['units'], list) or set(value['units']) != required or len(value['units']) != len(required):
        raise ValueError('complete runtime unit set required')
    hashes = value['files_sha256']
    if not isinstance(hashes, dict) or not hashes or value['manifest'] not in hashes:
        raise ValueError('manifest must be explicitly pinned')
    for name, sha in hashes.items():
        if not Path(name).is_absolute() or '..' in Path(name).parts or not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError('absolute pinned installed files required')
    for name in ('manifest', 'ca_file'):
        if not isinstance(value[name], str) or not Path(value[name]).is_absolute():
            raise ValueError('absolute controller path required')
    return value


def verify(value, backend, module):
    spec = specification(value['skyline'], 'existing')
    helper = Path(backend).parent/'skyline'
    assets = (helper/'verify_assets.py').read_text().replace("if __name__ == '__main__':", "if False:")
    installed = (helper/'verify_installation.py').read_text()
    code = assets+'\nassets_verify = verify\n'+installed+'\ninstallation_verify = verify\n'
    code += '\nSPEC = '+repr(spec)+'''
opener = build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=SPEC['ca_file'])), NoRedirect())
def fetch(url, encoding):
    with opener.open(Request(url, headers={'Accept-Encoding': encoding}), timeout=20) as response:
        return response.headers.get('Content-Encoding'), response.read(LIMIT+1)
def probe(url):
    with opener.open(Request(url), timeout=20) as response:
        response.read(1)
print(json.dumps(installation_verify(SPEC, assets_verify, fetch, probe)))
'''
    cfg = module.deployment_config(value['config'])
    result = existing.remote(module, cfg['hosts'][value['controller']], code)
    if result.get('status') != 'SKYLINE_INSTALLATION_VERIFIED':
        raise ValueError('Skyline verification failed')
    return result


def install(value, backend, output):
    spec = specification(value['skyline'], 'fresh')
    # Called only after Kolla and under the parent's approved, fingerprinted
    # intent. Recheck fresh-only preconditions immediately before installation;
    # do not let a long Kolla deployment consume the runtime approval TTL.
    args = argparse.Namespace(action='plan-skyline-runtime', output=str(output),
        runtime_dir=spec['runtime_dir'], runtime_sha256=spec['runtime_sha256'], plan_sha256=None)
    lock = runtime.lock_install()
    try:
        planned = runtime.execute(backend, args)
        args.action = 'install-skyline-runtime'; args.plan_sha256 = planned['plan_sha256']
        return runtime.execute(backend, args)
    finally:
        import os
        os.close(lock)
