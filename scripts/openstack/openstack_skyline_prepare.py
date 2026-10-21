"""Plan and assemble the complete Skyline image/runtime without deployment."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import time

import yaml
from openstack_compute_image import read, wheel_bytes
from openstack_container_engine import digest, file_hash, write
import openstack_skyline as skyline
import openstack_skyline_runtime as runtime


def helper(backend):
    source = Path(backend).parent/'skyline/stage_runtime.py'
    spec = importlib.util.spec_from_file_location('selected_skyline_stager', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def request(filename):
    value = json.loads(read(filename))
    if (not isinstance(value, dict) or set(value) != {'schema_version','deployment_config','console','runtime'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('exact Skyline preparation request required')
    console, web = value['console'], value['runtime']
    if (not isinstance(console, dict) or set(console) != {'static_dir','nginx_template','nginx_sha256','base_image','tag'}
            or not isinstance(web, dict) or set(web) != {'sdk_venv','web_user','web_config','wheel','wheel_sha256','publication_policy'}):
        raise ValueError('exact console and runtime inputs required')
    paths = [value['deployment_config'], console['static_dir'], console['nginx_template'],
             web['sdk_venv'], web['web_config'], web['wheel']]
    if web['publication_policy'] is not None: paths.append(web['publication_policy'])
    for name in paths:
        if not isinstance(name, str) or not Path(name).is_absolute() or Path(name).resolve() != Path(name):
            raise ValueError('canonical absolute preparation inputs required')
    for sha in (console['nginx_sha256'], web['wheel_sha256']):
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise ValueError('explicit input checksums required')
    if not re.fullmatch('[a-z0-9][a-z0-9.:/_-]*@sha256:[0-9a-f]{64}', console['base_image']):
        raise ValueError('immutable repository digest required')
    if not skyline.TAG_RE.fullmatch(console['tag']) or console['tag'].rsplit(':',1)[-1] in ('latest','main','master'):
        raise ValueError('non-floating image tag required')
    return value


def intent(value, backend):
    console, web = value['console'], value['runtime']
    wheel_bytes(backend, web['wheel'], web['wheel_sha256'])
    if file_hash(console['nginx_template']) != console['nginx_sha256']:
        raise ValueError('nginx source drift')
    static = Path(console['static_dir']); files = list(static.iterdir())
    if not files or any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError('flat regular static input directory required')
    sources = Path(backend).parent
    paths = [Path(value['deployment_config']), Path(console['nginx_template']),
             Path(web['web_config']), Path(web['wheel']), *files]
    if web['publication_policy']: paths.append(Path(web['publication_policy']))
    paths += [p for p in sources.rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += list(Path(__file__).parent.glob('*.py'))
    return {'request':value, 'files':{str(p):file_hash(p) for p in sorted(set(paths))},
            'sdk_sha256':helper(backend).tree_digest(Path(web['sdk_venv']))}


def verify_result(backend, root, receipt):
    for name, expected in receipt['outputs_sha256'].items():
        if file_hash(root/name) != expected: raise ValueError('prepared output drift')
    context = root/'console'
    skyline.context(context, file_hash(context/'console-context.json'))
    staged = Path(receipt['runtime_dir'])
    value = json.loads(read(staged/'runtime-stage.json'))
    if file_hash(staged/'runtime-stage.json') != receipt['runtime_sha256']:
        raise ValueError('staged receipt drift')
    for name, expected in value['files_sha256'].items():
        if file_hash(staged/name) != expected: raise ValueError('staged runtime drift')
    if helper(backend).tree_digest(staged/'venv') != value['venv_sha256']:
        raise ValueError('staged environment drift')
    image = json.loads(skyline.docker(['image','inspect',receipt['tag']]).stdout)
    if len(image) != 1 or image[0]['Id'] != receipt['image_id']:
        raise ValueError('prepared image drift')
    return {'status':'SKYLINE_PREPARATION_VERIFIED_NOOP','changes':False,
            'deployment_config':str(root/'deployment.yaml'), 'skyline':str(root/'skyline.json'),
            'image_id':receipt['image_id']}


def assemble(value, backend, root):
    console, web = value['console'], value['runtime']
    context = root/'console'; staged = root.with_name(root.name+'-runtime')
    args = ['stage-skyline-console','--output',str(context)]
    for key in ('static_dir','nginx_template','nginx_sha256','base_image'):
        args += ['--'+key.replace('_','-'), console[key]]
    if skyline.main(backend,args): raise ValueError('console staging failed')
    built = skyline.build(argparse.Namespace(output=str(context), context_sha256=file_hash(context/'console-context.json'),
        tag=console['tag'], journal=str(root/'build')))
    args = ['stage-skyline-runtime','--output',str(staged)]
    for key in ('sdk_venv','web_user','web_config','wheel','wheel_sha256','publication_policy'):
        if web[key] is not None: args += ['--'+key.replace('_','-'), web[key]]
    if skyline.main(backend,args): raise ValueError('runtime staging failed')
    fragment = json.loads(read(root/'build/backend-image-fragment.json'))
    config = yaml.safe_load(read(value['deployment_config']))
    if not isinstance(config,dict) or not isinstance(config.get('kolla'),dict):
        raise ValueError('deployment configuration with Kolla section required')
    config['kolla'].update(fragment['kolla'])
    with (root/'deployment.yaml').open('x') as stream:
        os.fchmod(stream.fileno(),0o600); yaml.safe_dump(config,stream,sort_keys=False)
    runtime_sha = file_hash(staged/'runtime-stage.json')
    write(root/'skyline.json',{'runtime_dir':str(staged),'runtime_sha256':runtime_sha})
    outputs = ['deployment.yaml','skyline.json','console/console-context.json',
               'build/console-build.json','build/backend-image-fragment.json']
    return {'status':'SKYLINE_PREPARATION_COMPLETE','runtime_dir':str(staged),'runtime_sha256':runtime_sha,
            'tag':console['tag'],'image_id':built['image_id'],
            'outputs_sha256':{name:file_hash(root/name) for name in outputs},
            'containers_deployed':False,'guests_changed':False}


def execute(backend, args):
    if os.geteuid() != 0: raise ValueError('root-local preparation required')
    value = request(args.request); root = Path(args.output)
    if not root.is_absolute() or root.resolve() != root: raise ValueError('canonical output required')
    runtime.trusted(root.parent)
    if args.action == 'plan-skyline-preparation': root.mkdir(mode=0o700,exist_ok=False)
    runtime.trusted(root)
    fd = os.open(root/'.prepare.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o777 != 0o600:
            raise ValueError('protected single-link preparation lock required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        inputs = intent(value,backend)
        if args.action == 'plan-skyline-preparation':
            if root.with_name(root.name+'-runtime').exists(): raise ValueError('runtime output occupied')
            plan={'schema_version':1,'created_at':int(time.time()),'intent':inputs}
            plan['plan_sha256']=digest(plan);write(root/'preparation-plan.json',plan)
            return {'status':'SKYLINE_PREPARATION_PLAN_READY','plan_sha256':plan['plan_sha256']}
        plan=json.loads(read(root/'preparation-plan.json'))
        if (plan.get('plan_sha256') != args.plan_sha256 or plan.get('intent') != inputs
                or digest({k:v for k,v in plan.items() if k != 'plan_sha256'}) != args.plan_sha256):
            raise ValueError('preparation inputs/source or approval changed')
        journal=root/'preparation.json'
        if journal.exists():
            receipt=json.loads(read(journal))
            if receipt.get('status') != 'SKYLINE_PREPARATION_COMPLETE':
                raise ValueError('unfinished preparation requires review, never automatic replay')
            return verify_result(backend,root,receipt)
        if not args.allow_changes or not 0 <= time.time()-plan['created_at'] <= 900:
            raise ValueError('fresh explicit preparation approval required')
        if root.with_name(root.name+'-runtime').exists(): raise ValueError('runtime output occupied')
        write(journal,{'status':'PREPARING_SKYLINE','guests_changed':False})
        try:
            receipt=assemble(value,backend,root)
            if intent(value,backend) != inputs: raise ValueError('input drift during preparation')
            verify_result(backend,root,receipt)
        except BaseException as error:
            write(journal,{'status':'NEEDS_REVIEW','error_type':type(error).__name__,'guests_changed':False});raise
        write(journal,receipt)
        return {'status':receipt['status'],'image_id':receipt['image_id'],
                'deployment_config':str(root/'deployment.yaml'),'skyline':str(root/'skyline.json'),
                'containers_deployed':False,'guests_changed':False}
    finally: os.close(fd)


def main(backend, argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan-skyline-preparation','prepare-skyline'))
    for name in ('request','output'): parser.add_argument('--'+name,required=True)
    parser.add_argument('--plan-sha256');parser.add_argument('--allow-changes',action='store_true')
    args=parser.parse_args(argv)
    try: print(json.dumps(execute(backend,args),sort_keys=True));return 0
    except Exception:
        print(json.dumps({'status':'SKYLINE_PREPARATION_REFUSED','next_step':'inspect private journal; no automatic deployment or replay'}));return 2
