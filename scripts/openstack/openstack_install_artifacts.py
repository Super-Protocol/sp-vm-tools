"""Build and transfer the matched, provenance-bound installer artifacts."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace as R

from openstack_container_engine import digest, file_hash, write
from openstack_errors import InstallerError
from openstack_install_phases import BASES, document


def console_builder(i, arguments):
    # The reviewed builder imports sibling patch modules. Run it in a separate
    # isolated interpreter with only its verified source directory added; never
    # mutate the coordinator's import path or resolve modules from the cwd.
    builder = i.path.parent / 'skyline/build_context.py'
    code = ('import runpy,sys; from pathlib import Path; '
            'p=Path(sys.argv[1]); sys.path.insert(0,str(p.parent)); '
            'sys.argv=sys.argv[1:]; runpy.run_path(str(p),run_name="__main__")')
    command([sys.executable, '-I', '-B', '-c', code, str(builder), *arguments],
            i.output / 'private-console-context.log')


def command(argv, log, *, timeout=1800, stdin=None):
    from openstack_compute_image import ENV
    with Path(log).open('ab') as stream:
        os.fchmod(stream.fileno(), 0o600)
        result = subprocess.run(argv, stdin=stdin, stdout=stream, stderr=stream, timeout=timeout,env=ENV)
    if result.returncode:
        raise InstallerError('Artifact operation failed; inspect private log ' + str(log) + '. No automatic retry.')


def prepare(installation):
    import openstack_compute_image as compute
    import openstack_skyline as skyline
    import openstack_launcher as launcher
    i = installation
    i.run_id = i.cfg['deployment_id'] + '-' + digest(str(i.output))[:12]
    log = i.output / 'private-artifacts.log'
    bases = {key: i.options.get(key + '_base_image', value) for key, value in BASES.items()}
    for reference in bases.values():
        if not compute.BASE_RE.fullmatch(reference):
            raise InstallerError('Base images must be immutable repository@sha256 references.')
        command(['docker', 'pull', '--platform=linux/amd64', reference], log)
    wheel_root = i.path.parent.parent / 'wheels'
    wheels = list(wheel_root.glob('nova_spvm-*.whl'))
    if len(wheels) != 1:
        raise InstallerError('A single matched driver wheel is required in the selected bundle.')
    context = compute.stage(i.path, R(base_image=bases['compute'], wheel=str(wheels[0]),
        wheel_sha256=file_hash(wheels[0]), output=str(i.output / 'compute-context')))
    i.compute_context_sha = context['context_sha256']
    compute_tag = 'spvm/nova-compute:' + i.run_id
    built = compute.build(R(output=str(i.output / 'compute-context'),
        context_sha256=context['context_sha256'], tag=compute_tag, journal=str(i.output / 'compute-build')))
    static = i.output / 'upstream-static'; static.mkdir(mode=0o700)
    name = 'spvm-static-' + digest(i.run_id)[:24]
    created = False
    try:
        command(['docker', 'create', '--name', name, '--network=none', bases['console']], log)
        created = True
        command(['docker', 'cp', name + ':/var/lib/kolla/venv/lib/python3/site-packages/skyline_console/static/.', str(static)], log)
    finally:
        if created:
            # Only the stopped container created by this transaction; never a cloud container.
            command(['docker', 'rm', name], log)
    template = i.kolla_source / 'ansible/roles/skyline/templates/nginx.conf.j2'
    console_builder(i, ['--static-dir', str(static), '--nginx-template', str(template),
        '--nginx-sha256', file_hash(template), '--base-image', bases['console'],
        '--output', str(i.output / 'console-context')])
    console_sha = file_hash(i.output / 'console-context/console-context.json')
    console_tag = 'spvm/skyline-console:' + i.run_id
    console = skyline.build(R(output=str(i.output / 'console-context'), context_sha256=console_sha,
        tag=console_tag, journal=str(i.output / 'console-build')))
    recipe, _, _ = launcher.recipe(i.path)
    tools = Path(__file__).resolve().parents[2]
    staged = launcher.stage(i.path, str(tools), recipe['tools_commit'], str(i.output / 'launcher'))
    i.launcher_files = launcher.verify(i.path, str(i.output / 'launcher'), staged['receipt_sha256'])
    raw = json.loads(json.dumps(yaml_config(i.config)))
    raw['kolla'].update(compute_image=compute_tag, compute_image_id=built['image_id'],
        skyline_console_image=console_tag, skyline_console_image_id=console['image_id'],
        skyline_console_context=str(i.output / 'console-context'), skyline_console_context_sha256=console_sha)
    i.config = document(i.output / 'deployment.artifacts.yaml', raw)
    i.refresh()
    return {'status': 'MATCHED_ARTIFACTS_BUILT', 'bases': bases,
            'compute_image_id': built['image_id'], 'console_image_id': console['image_id'],
            'wheel_sha256': file_hash(wheels[0]), 'launcher_sha256': staged['receipt_sha256']}


def yaml_config(path):
    import yaml
    return yaml.safe_load(Path(path).read_bytes())


def deliver_console(i):
    import openstack_compute_image as image
    import openstack_image_transfer as transfer
    archive = i.output / 'console-image.tar'
    tag = i.cfg['kolla']['skyline_console_image']
    with archive.open('xb') as stream, (i.output / 'private-console-export.log').open('xb') as log:
        os.fchmod(stream.fileno(), 0o600); os.fchmod(log.fileno(), 0o600)
        result = subprocess.run(['docker', 'image', 'save', tag], stdout=stream, stderr=log,
                                env=image.ENV, timeout=1800)
        stream.flush(); os.fsync(stream.fileno())
    if result.returncode:
        raise InstallerError('Console image export failed; preserve the journal.')
    transfer.archive_layout(archive, tag)
    expected = {'machine': i.audits[i.controller]['host_identity_sha256'], 'sha256': file_hash(archive),
                'size': archive.stat().st_size, 'tag': tag, 'image_id': i.cfg['kolla']['skyline_console_image_id'],
                'directory': '/var/lib/spvm-installer/' + i.run_id + '/console-image'}
    code = 'WANTED=' + repr(expected) + '\n' + '''
import hashlib,json,os,subprocess,sys
from pathlib import Path
os.umask(0o077)
if hashlib.sha256(Path('/etc/machine-id').read_bytes().strip()).hexdigest()!=WANTED['machine']:
 raise ValueError('wrong console target')
root=Path(WANTED['directory'])
for p in (root.parent,*root.parent.parents):
 if p.exists() and (p.is_symlink() or p.stat().st_uid!=0 or p.stat().st_mode&0o022):
  raise ValueError('unprotected console staging parent')
root.parent.mkdir(mode=0o700,parents=True,exist_ok=True);root.mkdir(mode=0o700)
archive=root/'image.tar';h=hashlib.sha256();size=0
with archive.open('xb') as stream:
 while True:
  block=sys.stdin.buffer.read(1024*1024)
  if not block:break
  size+=len(block)
  if size>WANTED['size']:raise ValueError('oversized archive')
  stream.write(block);h.update(block)
 stream.flush();os.fsync(stream.fileno())
if size!=WANTED['size'] or h.hexdigest()!=WANTED['sha256']:raise ValueError('image transfer checksum mismatch')
log=root/'private-load.log'
with log.open('xb') as stream:
 p=subprocess.run(['docker','image','load','--input',str(archive)],stdout=stream,stderr=stream,timeout=1800)
if p.returncode:raise ValueError('console import failed')
p=subprocess.run(['docker','image','inspect',WANTED['tag']],check=True,capture_output=True,text=True,timeout=30)
images=json.loads(p.stdout)
if len(images)!=1 or images[0]['Id']!=WANTED['image_id']:raise ValueError('console image identity differs')
print(json.dumps({'status':'CONSOLE_IMAGE_DELIVERED','image_id':images[0]['Id']}))
'''
    argv = i.backend.ssh_arguments(i.cfg['hosts'][i.controller]) + [
        'sudo -n /usr/bin/python3 -I -B -c ' + shlex.quote(code)]
    with archive.open('rb') as source, (i.output / 'private-console-transfer.log').open('xb') as log:
        os.fchmod(log.fileno(), 0o600)
        result = subprocess.run(argv, stdin=source, stdout=subprocess.PIPE, stderr=log, timeout=2400)
    if result.returncode or json.loads(result.stdout).get('status') != 'CONSOLE_IMAGE_DELIVERED':
        raise InstallerError('Console image transfer did not confirm completion; inspect the private phase log.')
