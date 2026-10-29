"""Reproduce a reviewed Nova launcher in a new private directory, without running it."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from openstack_container_engine import digest, write

SCRIPTS=('start_super_protocol.sh','start_super_protocol_libvirt.sh','libvirt_launcher.py')
ENV={'PATH':'/usr/bin:/bin','LC_ALL':'C','GIT_CONFIG_NOSYSTEM':'1',
     'GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0'}


def read(path):
    path=Path(path)
    if path.resolve()!=path or not path.is_file() or path.stat().st_size>1048576:
        raise ValueError('bounded regular non-symlink launcher input required')
    return path.read_bytes()


def sha(body):return hashlib.sha256(body).hexdigest()


def recipe(backend):
    path=Path(backend).parent/'launcher-recipe.json'
    raw=read(path)
    value=json.loads(raw)
    if (not isinstance(value,dict) or set(value)!={'schema_version','tools_commit',
            'source_sha256','patches','result_sha256'} or type(value['schema_version']) is not int
            or value['schema_version']!=1 or not re.fullmatch('[a-f0-9]{40}',str(value['tools_commit']))):
        raise ValueError('versioned pinned launcher recipe required')
    for key in ('source_sha256','result_sha256'):
        if (not isinstance(value[key],dict) or set(value[key])!=set(SCRIPTS)
                or any(not re.fullmatch('[a-f0-9]{64}',str(h)) for h in value[key].values())):
            raise ValueError('complete reviewed launcher hashes required')
    if not isinstance(value['patches'],list) or not 1<=len(value['patches'])<=32:
        raise ValueError('bounded ordered patch recipe required')
    names=set();patches=[]
    for entry in value['patches']:
        if (not isinstance(entry,dict) or set(entry)!={'name','sha256'}
                or not re.fullmatch('[a-z0-9-]+[.]patch',str(entry['name'])) or entry['name'] in names
                or not re.fullmatch('[a-f0-9]{64}',str(entry['sha256']))):
            raise ValueError('invalid or duplicate launcher patch')
        names.add(entry['name'])
        body=read(path.parent/'patches'/entry['name'])
        if sha(body)!=entry['sha256']:raise ValueError('launcher patch checksum mismatch')
        patches.append(body)
    return value,sha(raw),patches


def command(argv,*,cwd=None,body=None):
    # No inherited Git/Python configuration, hooks, filters, credentials or guest execution.
    p=subprocess.run(argv,cwd=cwd,input=body,capture_output=True,env=ENV,timeout=45)
    if p.returncode or len(p.stdout)>1048576:
        raise ValueError('launcher assembly/syntax check failed; subprocess output suppressed')
    return p.stdout


def syntax(files):
    ast.parse(files['libvirt_launcher.py'],filename='libvirt_launcher.py')
    for name in SCRIPTS[:2]:command(['bash','--noprofile','--norc','-n'],body=files[name])
    wrapper=files['start_super_protocol_libvirt.sh'].decode()
    if not all(m in wrapper for m in ('--nova-state-disk)','--nova-interface-xml)','--nova-aux-pci)',
                                     'Nova requires explicit host launch policy')):
        raise ValueError('Nova measured-launch contract missing')


def verify(backend,stage,approved=None):
    value,recipe_sha,_=recipe(backend)
    stage=Path(stage)
    if not stage.is_absolute() or stage.resolve()!=stage or not stage.is_dir():
        raise ValueError('absolute non-symlink staging directory required')
    receipt=json.loads(read(stage/'launcher-receipt.json'))
    expected={'schema_version':1,'status':'LAUNCHER_STAGED_NOT_INSTALLED',
              'tools_commit':value['tools_commit'],'recipe_sha256':recipe_sha,
              'result_sha256':value['result_sha256']}
    expected['receipt_sha256']=digest(expected)
    if receipt!=expected or (approved is not None and approved!=expected['receipt_sha256']):
        raise ValueError('launcher receipt does not match reviewed recipe/approval')
    if {p.relative_to(stage).as_posix() for p in stage.rglob('*')}!={
            'scripts','launcher-receipt.json',*('scripts/'+n for n in SCRIPTS)}:
        raise ValueError('unexpected launcher staging content')
    files={n:read(stage/'scripts'/n) for n in SCRIPTS}
    if {n:sha(b) for n,b in files.items()}!=value['result_sha256']:
        raise ValueError('staged launcher checksum mismatch')
    syntax(files)
    return receipt,files


def stage(backend,source,revision,output):
    value,recipe_sha,patches=recipe(backend)
    if revision!=value['tools_commit']:raise ValueError('tools revision must match reviewed recipe exactly')
    source=Path(source)
    if not source.is_absolute() or source.resolve()!=source or not source.is_dir():
        raise ValueError('absolute non-symlink local tools checkout required')
    resolved=command(['git','-C',str(source),'rev-parse','--verify',revision+'^{commit}']).decode().strip()
    if resolved!=revision:raise ValueError('pinned tools commit missing')
    files={}
    for name in SCRIPTS:
        object_name=revision+':scripts/'+name
        size=command(['git','-C',str(source),'cat-file','-s',object_name]).decode().strip()
        if not size.isdigit() or not 0<int(size)<=1048576:
            raise ValueError('pinned launcher blob exceeds size limit')
        files[name]=command(['git','-C',str(source),'cat-file','blob',object_name])
    if {n:sha(b) for n,b in files.items()}!=value['source_sha256']:
        raise ValueError('pinned launcher source mismatch')
    output=Path(output)
    if not output.is_absolute() or output.parent.resolve()!=output.parent or output.name in ('.','..'):
        raise ValueError('absolute staging path with existing non-symlink parent required')
    output.mkdir(mode=0o700,exist_ok=False)
    # Any failure leaves this directory for review; never overwrite or clean it automatically.
    scripts=output/'scripts';scripts.mkdir(mode=0o700)
    for name,body in files.items():
        with (scripts/name).open('xb') as stream:stream.write(body)
        (scripts/name).chmod(0o600)
    for body in patches:
        command(['git','apply','--no-index','--check','-'],cwd=output,body=body)
        command(['git','apply','--no-index','-'],cwd=output,body=body)
    files={n:read(scripts/n) for n in SCRIPTS}
    if {n:sha(b) for n,b in files.items()}!=value['result_sha256']:
        raise ValueError('patched launcher differs from reviewed output')
    syntax(files)
    receipt={'schema_version':1,'status':'LAUNCHER_STAGED_NOT_INSTALLED',
             'tools_commit':revision,'recipe_sha256':recipe_sha,'result_sha256':value['result_sha256']}
    receipt['receipt_sha256']=digest(receipt)
    write(output/'launcher-receipt.json',receipt)
    verify(backend,output,receipt['receipt_sha256'])
    return receipt


def main(backend,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['stage-launcher','check-launcher'])
    parser.add_argument('--output',required=True,help='New staging directory (existing for check)')
    parser.add_argument('--tools-source',help='Local checkout; dirty worktree is never copied')
    parser.add_argument('--tools-revision',help='Full commit from selected driver launcher recipe')
    parser.add_argument('--receipt-sha256',help='Optional approved receipt digest for check')
    args=parser.parse_args(argv)
    try:
        if args.action=='stage-launcher':
            if not args.tools_source or not args.tools_revision or args.receipt_sha256:
                raise ValueError('source and pinned revision required for staging')
            result=stage(backend,args.tools_source,args.tools_revision,args.output)
        else:
            if args.tools_source or args.tools_revision:raise ValueError('check uses staged inputs only')
            result,_=verify(backend,args.output,args.receipt_sha256)
        print(json.dumps(result))
        return 0
    except (OSError,ValueError,SyntaxError,subprocess.SubprocessError):
        print('ERROR: launcher preparation refused; partial staging preserved. No launcher/guest was executed.',file=sys.stderr)
        return 2
