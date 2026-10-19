"""Isolated new-cloud Kolla inputs and local task parsing. Never deploy hosts."""
import argparse
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import sys
import traceback

import yaml
from openstack_container_engine import digest, file_hash, write, collection_hash
from openstack_compute_image import read


def private_file(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as stream:
        os.chmod(path,0o600)
        stream.write(content if isinstance(content,bytes) else content.encode())


def command(args, log, *, cwd, env=None):
    with log.open('ab') as output:
        os.chmod(log,0o600)
        result=subprocess.run(args,cwd=cwd,env=env,stdout=output,stderr=output,timeout=300)
    if result.returncode:
        raise ValueError('local Kolla/OpenSSL preparation failed; private log retained')


def pinned(source, revision):
    source=Path(source)
    if not source.is_absolute() or source.resolve()!=source:
        raise ValueError('absolute resolved Kolla source required')
    head=subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],check=True,capture_output=True,text=True,timeout=30).stdout.strip()
    dirty=subprocess.run(['git','-C',str(source),'status','--porcelain'],check=True,capture_output=True,text=True,timeout=30).stdout
    if head!=revision or dirty:
        raise ValueError('clean pinned Kolla checkout required')
    return source


def runtime_mounts(config):
    """Pinned Kolla owns /run:shared; a child bind feeds mounts back to the host."""
    if not isinstance(config, dict):
        raise ValueError('explicit Nova mount configuration mapping required')
    if 'nova_compute_default_volumes' in config:
        raise ValueError('overriding pinned Nova default mounts requires separate review')
    extra = config.get('nova_compute_extra_volumes', [])
    if not isinstance(extra, list):
        raise ValueError('explicit Nova extra mount list required')
    for value in extra:
        if not isinstance(value, str) or not 1 <= len(value) <= 4096:
            raise ValueError('invalid Nova extra mount')
        parts = value.split(':')
        if len(parts) not in (2, 3):
            raise ValueError('explicit Nova mount destination required')
        target = PurePosixPath(parts[1])
        if not target.is_absolute() or '..' in target.parts or any(c in parts[1] for c in '{}'):
            raise ValueError('literal canonical Nova mount destination required')
        if target == PurePosixPath('/run') or target.is_relative_to('/run'):
            raise ValueError('redundant /run child binds leak host mounts; use pinned parent bind')


def rendered(cfg, root):
    root=Path(root)
    if not root.is_absolute() or root.resolve()!=root:
        raise ValueError('absolute non-symlink render required')
    lock=json.loads((root/'deployment.lock.json').read_text())
    required={'hosts.ini','globals.yml','host-profile.yml','resource-plan.json','basic-preflight.json'}
    if (lock.get('schema_version')!=1 or lock.get('inventory_scope')!='full-pinned-upstream'
            or lock.get('config_digest')!=digest(cfg) or lock.get('kolla')!=cfg['kolla']
            or not required<=set(lock.get('rendered_sha256',{}))):
        raise ValueError('full pinned render and bound basic preflight required')
    files={}
    for name,expected in lock['rendered_sha256'].items():
        part=Path(name)
        if part.is_absolute() or '..' in part.parts or str(part)!=name:
            raise ValueError('unsafe rendered member')
        path=root/part
        if path.resolve()!=path or not path.is_file() or path.stat().st_size>4194304 or file_hash(path)!=expected:
            raise ValueError('rendered file changed')
        files[name]=path.read_bytes()
    preflight=json.loads(files['basic-preflight.json'])
    if (preflight.get('status')!='BASIC_CHECKS_PASSED_NOT_DEPLOYABLE'
            or preflight.get('config_sha256')!=digest(cfg) or preflight.get('blockers')):
        raise ValueError('passing bound basic preflight required')
    profile=yaml.safe_load(files['host-profile.yml'])
    runtime_mounts(profile)
    runtime_mounts(yaml.safe_load(files['globals.yml']))
    if ('spvm_external_docker' in profile or 'enable_docker_repo' in profile
            or profile.get('enable_nova_libvirt_container') is not False
            or profile.get('disable_firewall') is not False
            or profile.get('apparmor_remove_libvirt_profile') is not False
            or profile.get('ubuntu_pkg_removals')!=[]):
        raise ValueError('preserving per-host Docker/runtime profile required')
    return lock,files


def passwords_valid(value, template):
    if not isinstance(value,dict) or set(value)!=set(template):
        raise ValueError('generated password schema differs from upstream')
    for key, original in template.items():
        if key=='docker_registry_password':
            continue
        actual=value[key]
        if original is None and (not actual or (isinstance(actual,dict) and
                (not actual.get('public_key') or not actual.get('private_key')))):
            raise ValueError('generated credential is missing; details suppressed')


def certificates(output,vip,log):
    root=output/'kolla/certificates'
    ca=root/'ca'
    ca.mkdir(parents=True,mode=0o700)
    subject='/CN=SPVM private OpenStack API CA'
    command(['openssl','req','-x509','-newkey','rsa:3072','-nodes','-days','3650',
        '-keyout',str(output/'private-api-ca.key'),'-out',str(ca/'ca.crt'),'-subj',subject,
        '-addext','basicConstraints=critical,CA:TRUE',
        '-addext','keyUsage=critical,keyCertSign,cRLSign'],log,cwd=output)
    command(['openssl','req','-new','-newkey','rsa:3072','-nodes',
        '-keyout',str(root/'api.key'),'-out',str(root/'api.csr'),'-subj','/CN='+vip,
        '-addext','subjectAltName=IP:'+vip,'-addext','basicConstraints=critical,CA:FALSE',
        '-addext','keyUsage=critical,digitalSignature,keyEncipherment',
        '-addext','extendedKeyUsage=serverAuth'],log,cwd=output)
    command(['openssl','x509','-req','-in',str(root/'api.csr'),'-CA',str(ca/'ca.crt'),
        '-CAkey',str(output/'private-api-ca.key'),'-set_serial',str(secrets.randbits(159) or 1),
        '-days','397','-copy_extensions','copy','-out',str(root/'api.crt')],log,cwd=output)
    # Kolla's trust-anchor copy directory must never contain the CA private key.
    pem=(root/'api.crt').read_bytes()+(root/'api.key').read_bytes()
    private_file(root/'haproxy-internal.pem',pem)
    private_file(root/'haproxy.pem',pem)
    database_frontend_tls(output)
    for item in root.rglob('*'):
        if item.is_file():os.chmod(item,0o600)
    os.chmod(output/'private-api-ca.key',0o600)
    verify_tls(output,vip)


def database_frontend_tls(output):
    """Match upstream's internal frontend certificate reuse for ProxySQL TLS."""
    root=output/'kolla/certificates'
    for target,source in (('proxysql-ca.pem','ca/ca.crt'),
                          ('proxysql-cert.pem','api.crt'),('proxysql-key.pem','api.key')):
        private_file(root/target,(root/source).read_bytes())


def verify_tls(output,vip):
    root=output/'kolla/certificates'
    for args in (['verify','-CAfile',str(root/'ca/ca.crt'),'-verify_ip',vip,'-purpose','sslserver',str(root/'api.crt')],
                 ['x509','-in',str(root/'api.crt'),'-noout','-checkend','86400']):
        result=subprocess.run(['openssl',*args],capture_output=True,timeout=30)
        if result.returncode:raise ValueError('private API certificate validation failed')
    cert=subprocess.run(['openssl','x509','-in',str(root/'api.crt'),'-pubkey','-noout'],check=True,capture_output=True,timeout=30).stdout
    key=subprocess.run(['openssl','pkey','-in',str(root/'api.key'),'-pubout'],check=True,capture_output=True,timeout=30).stdout
    if cert!=key:raise ValueError('private API certificate/key mismatch')
    for target,source in (('proxysql-ca.pem','ca/ca.crt'),
                          ('proxysql-cert.pem','api.crt'),('proxysql-key.pem','api.key')):
        if read(root/target)!=read(root/source):
            raise ValueError('ProxySQL frontend TLS must match the approved internal certificate/CA/key')


def file_set(output):
    result={}
    for item in output.rglob('*'):
        if item.is_symlink():raise ValueError('symlink in private workspace refused')
        relative=str(item.relative_to(output))
        if item.is_file() and relative not in ('workspace.json','journal.json','.lock'):
            if item.stat().st_mode & 0o077 or item.stat().st_uid!=os.getuid():
                raise ValueError('private workspace file ownership/permissions invalid')
            result[str(item.relative_to(output))]=file_hash(item)
    return result


def init(args,backend):
    if not args.ack_new_cloud:
        raise ValueError('explicit new-cloud acknowledgement required; never use for add-compute')
    cfg=backend.deployment_config(args.config)
    source=pinned(args.kolla_source,cfg['kolla']['revision'])
    lock,files=rendered(cfg,args.rendered)
    collection_hash(args.collections,args.collections_sha256)
    python=Path(args.kolla_python)
    if not python.is_absolute() or not python.is_file():raise ValueError('absolute existing Kolla Python required')
    output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:
        raise ValueError('absolute non-symlink new output directory required')
    output.mkdir(mode=0o700,parents=False,exist_ok=False)
    journal={'schema_version':1,'status':'PREPARING_LOCAL_INPUTS','hosts_changed':False}
    write(output/'journal.json',journal)
    log=output/'private-init.log'
    environment={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','PYTHON'))}
    environment['PYTHONDONTWRITEBYTECODE']='1'
    try:
        for name,content in files.items():
            # Only config files are materialized in Kolla's private configdir.
            if name in ('hosts.ini','host-profile.yml'):
                private_file(output/name,content)
            elif name.startswith('config/'):
                private_file(output/'kolla'/name,content)
        globals_=yaml.safe_load(files['globals.yml'])
        if globals_.get('enable_nova_libvirt_container') is not False:
            raise ValueError('host libvirt must remain external')
        globals_.update(node_custom_config=str(output/'kolla/config'),
            kolla_certificates_dir=str(output/'kolla/certificates'),
            kolla_internal_fqdn_cert=str(output/'kolla/certificates/haproxy-internal.pem'),
            kolla_external_fqdn_cert=str(output/'kolla/certificates/haproxy.pem'),
            kolla_enable_tls_internal=True,kolla_copy_ca_into_containers=True)
        private_file(output/'kolla/globals.yml',yaml.safe_dump(globals_,sort_keys=True))
        template=subprocess.run(['git','-C',str(source),'show',cfg['kolla']['revision']+':etc/kolla/passwords.yml'],
                                capture_output=True,check=True,timeout=30).stdout
        private_file(output/'kolla/passwords.yml',template)
        command([str(python),str(source/'kolla_ansible/cmd/genpwd.py'),'-p',str(output/'kolla/passwords.yml')],
                log,cwd=output,env=environment)
        os.chmod(output/'kolla/passwords.yml',0o600)
        passwords_valid(yaml.safe_load((output/'kolla/passwords.yml').read_bytes()),yaml.safe_load(template))
        certificates(output,cfg['network']['api_vip'],log)
        receipt={'schema_version':1,'status':'KOLLA_INPUTS_INITIALIZED_NOT_DEPLOYED',
            'deployment_id':cfg['deployment_id'],'config_sha256':digest(cfg),
            'render_lock_sha256':digest(lock),'kolla_revision':cfg['kolla']['revision'],
            'kolla_source':str(source),'kolla_python':str(python),'kolla_python_sha256':file_hash(python),
            'collections':str(Path(args.collections).resolve()),'collections_sha256':args.collections_sha256,
            'password_template_sha256':__import__('hashlib').sha256(template).hexdigest(),
            'vip':cfg['network']['api_vip'],'files':file_set(output),'hosts_changed':False,
            'remaining':['API_CA_TRUST_DISTRIBUTION','FRESH_HOST_DEPENDENCIES',
                         'SPVM_HELPER_AND_COMPUTE_IDENTITY','STATE_GPU_REGISTRATION',
                         'KOLLA_REAL_PRECHECKS_DEPLOY_ACCEPTANCE']}
        write(output/'workspace.json',receipt)
        journal['status']=receipt['status']
        write(output/'journal.json',journal)
        return receipt
    except BaseException:
        journal['status']='NEEDS_REVIEW_LOCAL_INPUTS'
        write(output/'journal.json',journal)
        raise


def check(output):
    output=Path(output)
    if (not output.is_absolute() or output.resolve()!=output or not output.is_dir()
            or output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077):
        raise ValueError('private owned non-symlink workspace required')
    for name in ('workspace.json','journal.json'):
        p=output/name
        if p.is_symlink() or p.stat().st_mode & 0o077:raise ValueError('untrusted private receipt')
    receipt=json.loads((output/'workspace.json').read_text())
    journal=json.loads((output/'journal.json').read_text())
    if (receipt.get('schema_version')!=1 or receipt.get('status')!='KOLLA_INPUTS_INITIALIZED_NOT_DEPLOYED'
            or journal.get('status')!=receipt['status'] or receipt['files']!=file_set(output)):
        raise ValueError('workspace input drift or incomplete initialization')
    runtime_mounts(yaml.safe_load((output/'kolla/globals.yml').read_bytes()))
    runtime_mounts(yaml.safe_load((output/'host-profile.yml').read_bytes()))
    verify_tls(output,receipt['vip'])
    return receipt


def list_tasks(output):
    output=Path(output)
    receipt=check(output)
    source=pinned(receipt['kolla_source'],receipt['kolla_revision'])
    collection_hash(receipt['collections'],receipt['collections_sha256'])
    if file_hash(receipt['kolla_python'])!=receipt['kolla_python_sha256']:
        raise ValueError('Kolla Python changed')
    # Fresh private child directory; list-tasks never executes a remote task.
    import tempfile
    run=Path(tempfile.mkdtemp(prefix='spvm-kolla-list-',dir=output.parent))
    environment={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','PYTHON'))}
    environment.update(PYTHONDONTWRITEBYTECODE='1',ANSIBLE_COLLECTIONS_PATH=receipt['collections'],
        KOLLA_ANSIBLE_DATA_FILES_PATH=str(source),PATH=str(Path(receipt['kolla_python']).parent)+':'+os.environ.get('PATH',''))
    private_file(run/'ansible.cfg','[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n')
    environment['ANSIBLE_CONFIG']=str(run/'ansible.cfg')
    code='import sys;sys.path.insert(0,sys.argv.pop(1));from kolla_ansible.cmd.kolla_ansible import main;sys.exit(main())'
    command([receipt['kolla_python'],'-c',code,str(source),'prechecks','--list-tasks',
             '--configdir',str(output/'kolla'),'-i',str(output/'hosts.ini'),'-e','@'+str(output/'host-profile.yml')],
             run/'private-task-list.log',cwd=run,env=environment)
    check(output)
    result={'schema_version':1,'status':'KOLLA_TASK_PARSING_PASSED_NOT_PRECHECKED',
            'workspace_sha256':digest(receipt),'hosts_changed':False,'remote_tasks_executed':False}
    write(run/'result.json',result)
    return result


def main(backend_path,argv):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init-kolla','check-kolla-workspace','list-kolla-tasks'])
    parser.add_argument('--output',required=True,help='New private workspace for init, existing workspace for check/list')
    for name in ('config','rendered','kolla-source','kolla-python','collections','collections-sha256'):
        parser.add_argument('--'+name)
    parser.add_argument('--ack-new-cloud',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action=='init-kolla':
            if not all(getattr(args,n) for n in ('config','rendered','kolla_source','kolla_python','collections','collections_sha256')):
                parser.error('init requires config/rendered, pinned Kolla source/Python and reviewed collections')
            sys.path.insert(0,str(Path(backend_path).parent))
            spec=importlib.util.spec_from_file_location('spvm_workspace_backend',backend_path)
            backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
            result=init(args,backend)
        elif args.action=='check-kolla-workspace':result=check(args.output)
        else:result=list_tasks(args.output)
        print(json.dumps({k:v for k,v in result.items() if k in ('status','hosts_changed','remote_tasks_executed')}))
        return 0
    except (OSError,ValueError,KeyError,yaml.YAMLError,subprocess.SubprocessError) as exc:
        frame=traceback.extract_tb(exc.__traceback__)[-1]
        print('ERROR: isolated Kolla inputs refused; private artifacts preserved. '+type(exc).__name__
              +' at '+frame.name+':'+str(frame.lineno),file=sys.stderr)
        return 2
