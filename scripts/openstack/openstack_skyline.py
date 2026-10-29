"""Offline complete Skyline image assembly from the selected driver bundle.

No container replacement, Kolla reconfigure, host bootstrap or guest lifecycle.
The customizations themselves belong exclusively to swarm-cloud.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from openstack_compute_image import docker, ENV, TAG_RE, read, wheel_bytes
from openstack_container_engine import write

STATIC_ASSETS = {
    'superprotocol-logo.jpg', 'swarm-node-launcher.js',
    'swarm-nodes.html', 'swarm-nodes.css', 'swarm-nodes.js',
    'swarm-clusters.html', 'swarm-clusters.css', 'swarm-clusters.js',
    'swarm-create.html', 'swarm-create.js', 'swarm-releases.html', 'swarm-releases.js',
    'spvm-flavors.html', 'spvm-flavors.js', 'spvm-host-budgets.html',
    'spvm-host-budgets.js', 'spvm-host-budgets.css', 'spvm-host-pools.js',
    'spvm-host-gpus.js',
}
EXPECTED_ASSETS = STATIC_ASSETS | {
    name + '.gz' for name in STATIC_ASSETS if name.endswith(('.html', '.js', '.css'))
} | {'index.html'} | {
    kind + '.bundle.1663167892.js' + suffix
    for kind in ('auth', 'main', 'common', 'compute') for suffix in ('', '.gz')
}


def context(directory, expected):
    root = Path(directory)
    body = read(root/'console-context.json')
    if hashlib.sha256(body).hexdigest() != expected:
        raise ValueError('explicit approved console context SHA256 required')
    value = json.loads(body)
    if (set(value) != {'schema_version', 'base_image', 'asset_sha256', 'nginx_source_sha256',
                      'nginx_sha256', 'dockerfile_sha256'} or value['schema_version'] != 1
            or not re.fullmatch(r'[a-z0-9][a-z0-9.:/_-]*@sha256:[0-9a-f]{64}', value['base_image'])
            or not isinstance(value['asset_sha256'], dict)
            or set(value['asset_sha256']) != EXPECTED_ASSETS):
        raise ValueError('recognized complete Skyline context required')
    for name, digest in value['asset_sha256'].items():
        if (not re.fullmatch(r'[a-zA-Z0-9._-]+', name)
                or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or hashlib.sha256(read(root/'context'/name, 32*1024*1024)).hexdigest() != digest):
            raise ValueError('console static asset drift')
    if {p.name for p in (root/'context').iterdir()} != set(value['asset_sha256']) | {'Dockerfile'}:
        raise ValueError('unexpected build context member')
    for kind in ('auth', 'main', 'common', 'compute'):
        name = kind+'.bundle.1663167892.js'
        if name not in value['asset_sha256'] or name+'.gz' not in value['asset_sha256']:
            raise ValueError('all patched JS and gzip siblings required; base may contain stale gzip')
        raw = read(root/'context'/name, 32*1024*1024)
        with gzip.GzipFile(fileobj=io.BytesIO(read(root/'context'/(name+'.gz'), 32*1024*1024))) as stream:
            if stream.read(len(raw)+1) != raw:
                raise ValueError('compressed Skyline asset differs from patched JS')
    for path, digest in ((root/'context/Dockerfile', value['dockerfile_sha256']),
                         (root/'kolla-custom/skyline/nginx.conf', value['nginx_sha256'])):
        if hashlib.sha256(read(path)).hexdigest() != digest:
            raise ValueError('console Dockerfile or persistent nginx drift')
    return value


def build(args):
    value = context(args.output, args.context_sha256)
    if not TAG_RE.fullmatch(args.tag) or args.tag.rsplit(':', 1)[-1] in ('latest', 'main', 'master'):
        raise ValueError('non-floating local console tag required')
    tags = docker(['image', 'ls', '--format', '{{.Repository}}:{{.Tag}}']).stdout.decode().splitlines()
    if args.tag in tags:
        raise ValueError('console tag exists; never overwrite an approved image')
    base = json.loads(docker(['image', 'inspect', value['base_image']]).stdout)
    if (len(base) != 1 or base[0].get('Os') != 'linux' or base[0].get('Architecture') != 'amd64'
            or value['base_image'] not in (base[0].get('RepoDigests') or [])):
        raise ValueError('exact Linux amd64 base must already be cached')
    journal = Path(args.journal)
    output = Path(args.output)
    if (not journal.is_absolute() or journal.parent.resolve() != journal.parent
            or journal.is_relative_to(output) or output.is_relative_to(journal)):
        raise ValueError('separate canonical new build journal required')
    journal.mkdir(mode=0o700)
    receipt = {'schema_version': 1, 'status': 'BUILDING_SKYLINE_IMAGE',
        'context_sha256': args.context_sha256, 'base_image_id': base[0]['Id'], 'tag': args.tag,
        'containers_deployed': False, 'guests_changed': False}
    write(journal/'console-build.json', receipt)
    try:
        with (journal/'private-build.log').open('xb') as log:
            os.chmod(log.name, 0o600)
            result = subprocess.run(['docker', 'build', '--pull=false', '--network=none',
                '--tag', args.tag, str(output/'context')], env=ENV,
                stdout=log, stderr=log, timeout=1800)
        if result.returncode:
            raise ValueError('offline console build failed; inspect private log')
        context(args.output, args.context_sha256)
        image = json.loads(docker(['image', 'inspect', args.tag]).stdout)
        fields = ('User', 'Entrypoint', 'Cmd', 'Env', 'WorkingDir', 'Healthcheck',
                  'StopSignal', 'ExposedPorts', 'Volumes')
        if (len(image) != 1 or not re.fullmatch(r'sha256:[a-f0-9]{64}', image[0]['Id'])
                or any(image[0]['Config'].get(k) != base[0]['Config'].get(k) for k in fields)):
            raise ValueError('console image changed upstream runtime defaults')
        receipt.update(status='SKYLINE_IMAGE_BUILT_NOT_DEPLOYED', image_id=image[0]['Id'])
        (journal/'kolla-image-overrides.yml').write_text('skyline_console_image_full: '+args.tag+'\n')
        write(journal/'backend-image-fragment.json', {'kolla': {
            'skyline_console_image': args.tag, 'skyline_console_image_id': image[0]['Id'],
            'skyline_console_context': str(output), 'skyline_console_context_sha256': args.context_sha256}})
    except BaseException:
        receipt['status'] = 'NEEDS_REVIEW_SKYLINE_BUILD'
        write(journal/'console-build.json', receipt)
        raise
    write(journal/'console-build.json', receipt)
    return receipt


def main(backend, argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stage-skyline-console', 'build-skyline-console', 'stage-skyline-runtime'))
    parser.add_argument('--output', required=True)
    for name in ('static-dir', 'nginx-template', 'nginx-sha256', 'base-image', 'context-sha256', 'tag', 'journal',
                 'sdk-venv', 'web-user', 'web-config', 'wheel', 'wheel-sha256', 'publication-policy', 'retirement-policy'):
        parser.add_argument('--'+name)
    args = parser.parse_args(argv)
    try:
        runtime = (args.sdk_venv, args.web_user, args.web_config, args.wheel, args.wheel_sha256)
        console = (args.static_dir, args.nginx_template, args.nginx_sha256, args.base_image,
                   args.context_sha256, args.tag, args.journal)
        if args.action == 'stage-skyline-runtime':
            if not all(runtime) or any(console):
                raise ValueError('runtime staging requires SDK, user, config and approved wheel only')
            wheel_bytes(backend, args.wheel, args.wheel_sha256)
            command = [sys.executable, str(Path(backend).parent/'skyline/stage_runtime.py'),
                '--output', args.output, '--sdk-venv', args.sdk_venv, '--web-user', args.web_user,
                '--web-config', args.web_config, '--wheel', args.wheel, '--wheel-sha256', args.wheel_sha256]
            if args.publication_policy:
                command += ['--publication-policy', args.publication_policy]
            if args.retirement_policy:
                command += ['--retirement-policy', args.retirement_policy]
            subprocess.run(command, check=True)
        elif any(runtime) or args.publication_policy or args.retirement_policy:
            raise ValueError('runtime arguments are valid only for stage-skyline-runtime')
        elif args.action == 'stage-skyline-console':
            if not all((args.static_dir, args.nginx_template, args.nginx_sha256, args.base_image)) or any((args.context_sha256, args.tag, args.journal)):
                raise ValueError('staging needs reviewed static inputs, nginx and base only')
            builder = Path(backend).parent/'skyline/build_context.py'
            subprocess.run([sys.executable, str(builder), '--static-dir', args.static_dir,
                '--nginx-template', args.nginx_template, '--nginx-sha256', args.nginx_sha256,
                '--base-image', args.base_image, '--output', args.output], check=True)
        else:
            if not all((args.context_sha256, args.tag, args.journal)) or any((args.static_dir, args.nginx_template, args.nginx_sha256, args.base_image)):
                raise ValueError('build needs approved context hash, tag and separate journal only')
            print(json.dumps(build(args), sort_keys=True))
        return 0
    except Exception:
        print('ERROR: Skyline assembly incomplete; inspect private context/journal. No containers deployed.', file=sys.stderr)
        return 2
