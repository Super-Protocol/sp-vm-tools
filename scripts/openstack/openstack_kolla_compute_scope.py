"""Pinned compute-container reconciliation, never a global Nova deployment.

The guard is embedded in an isolated copy of the upstream container module.
Verify mode refuses *every* mutating action, even an unexpected handler caused
by drift. Replacement additionally requires an empty, quarantined compute.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

import yaml
from openstack_container_engine import digest, file_hash, write
from openstack_compute_image import read, TAG_RE

GUARD=r'''
def spvm_compute_guard(params, spec, root=None, runner=None):
    import hashlib,json,os,stat,subprocess
    from pathlib import Path
    root=Path('/') if root is None else Path(root)
    runner=subprocess.run if runner is None else runner
    owner=0 if root==Path('/') else os.getuid()
    def trusted(name):
        p=root/name.lstrip('/')
        for a in (p,*p.parents):
            st=a.lstat()
            if a.is_symlink() or st.st_uid!=owner or st.st_mode & 0o022:
                raise ValueError('untrusted admission input')
            if a==root:break
        return p
    def run(args,allowed=(0,)):
        p=runner(args,check=False,capture_output=True,text=True,timeout=45)
        if p.returncode not in allowed or len(p.stdout)>4194304:raise ValueError('admission observation failed')
        return p
    if params.get('name')!='nova_compute' or params.get('container_engine')!='docker':
        raise ValueError('only Docker nova_compute is permitted')
    action=params.get('action')
    if action not in ('compare_container','recreate_or_restart_container'):raise ValueError('action outside compute scope')
    if action!='compare_container' and spec['mode']!='replace-empty':
        raise ValueError('verify mode forbids all container mutations')
    if hashlib.sha256(trusted('/etc/machine-id').read_bytes().strip()).hexdigest()!=spec['machine_sha256']:
        raise ValueError('wrong compute machine')
    # Nova owns this data file; root-owned helper independently binds its UUID.
    identity=root/'var/lib/nova/compute_id'
    for a in (identity,*identity.parents):
        st=a.lstat();owners=(owner,42436) if a in (identity,identity.parent) else (owner,)
        if a.is_symlink() or st.st_uid not in owners or st.st_mode & 0o022:raise ValueError('untrusted compute identity')
        if a==root:break
    if identity.stat().st_nlink!=1 or identity.stat().st_size>64 or identity.read_text().strip()!=spec['compute_uuid']:
        raise ValueError('compute identity differs')
    helper=json.loads(trusted('/etc/spvm/host-helper.json').read_text())
    if helper['storage']['compute_uuid']!=spec['compute_uuid']:raise ValueError('helper identity differs')
    if params.get('image')!=spec['image']:raise ValueError('rendered image differs')
    image=json.loads(run(['docker','image','inspect',spec['image']]).stdout)
    if len(image)!=1 or image[0]['Id']!=spec['image_id']:raise ValueError('cached image differs; never pull implicitly')
    if action=='compare_container' and spec['mode']=='verify':return
    if run(['virsh','-c','qemu:///system','list','--all','--uuid']).stdout.strip():raise ValueError('compute has domains')
    p=run(['pgrep','-f',r'^(/[^ ]*/)?(qemu-system-[^ ]+|qemu-kvm|cloud-hypervisor)( |$)'],(0,1))
    if p.returncode!=1:raise ValueError('direct guest present')
    for pool in helper['storage']['pools']:
        p=trusted(pool['root']);names={x.name for x in p.iterdir()}
        if names- {'.spvm-state.lock','.spvm-claims.json'}:raise ValueError('pool contains disks/unknown state')
        if names and (names!={'.spvm-state.lock','.spvm-claims.json'} or
                json.loads(trusted(str(Path(pool['root'])/'.spvm-claims.json')).read_text())!={}):
            raise ValueError('state reservations remain')
    for name in ('network','providers','execution','clusters'):
        p=root/'var/lib/spvm'/name
        if p.exists() and any(trusted('/var/lib/spvm/'+name).iterdir()):raise ValueError('host launch state remains')
    leases=root/'var/lib/spvm/runtime'
    if leases.exists() and any(leases.iterdir()):
        if ({p.name for p in leases.iterdir()}!={'.leases.lock','leases.json'} or
                json.loads(trusted('/var/lib/spvm/runtime/leases.json').read_text())!={} or
                trusted('/var/lib/spvm/runtime/.leases.lock').stat().st_size):raise ValueError('GPU/runtime leases remain')

def spvm_compute_snapshot(spec):
    import hashlib,json,subprocess
    from pathlib import Path
    def run(*args):
        p=subprocess.run(args,check=True,capture_output=True,text=True,timeout=45)
        if len(p.stdout)>4194304:raise ValueError('bounded snapshot required')
        return p.stdout
    ids=run('docker','ps','--all','--no-trunc','--format','{{.ID}}').split()
    containers={}
    for c in json.loads(run('docker','inspect',*ids)) if ids else []:
        if spec['mode']=='replace-empty' and c['Name']=='/nova_compute':continue
        containers[c['Name']]={'id':c['Id'],'image':c['Image'],'running':c['State']['Running'],
          'started':c['State']['StartedAt'],'restarts':c['RestartCount'],
          'config':hashlib.sha256(json.dumps({k:c[k] for k in ('Config','HostConfig','Mounts')},sort_keys=True).encode()).hexdigest()}
    services={u:run('systemctl','show',u,'--property=MainPID,ActiveEnterTimestampMonotonic,NRestarts')
      for u in ('docker.service','libvirtd.service','qgs.service','qgsd.service','pccs.service','haproxy.service','host-helper.socket')}
    paths=[Path(n) for n in ('/etc/spvm/host-helper.json','/etc/spvm/compute-pools.json','/etc/spvm/runtime-services.json',
      '/etc/libvirt/qemu.conf','/etc/libvirt/libvirtd.conf','/etc/docker/daemon.json')]
    paths += list(Path('/usr/local/libexec/spvm').glob('*.py'))
    files={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}
    return {'containers':containers,'services':services,'files':files,
            'domains':run('virsh','-c','qemu:///system','list','--all')}
'''


def guarded_module(original,spec):
    anchor='def main():\n    module = generate_module()\n'
    if original.count(anchor)!=1:raise ValueError('unsupported pinned container module entry point')
    guard=('\nSPVM_COMPUTE_SCOPE='+repr(spec)+'\n'+GUARD+'\n'+anchor+
        "    try:\n        spvm_compute_guard(module.params, SPVM_COMPUTE_SCOPE)\n"
        "    except Exception:\n        module.fail_json(changed=False, msg='SPVM compute admission refused before container action')\n")
    result=original.replace(anchor,guard)
    action="        result = bool(getattr(cw, module.params.get('action'))())\n"
    if result.count(action)!=1:raise ValueError('unsupported native container dispatch')
    guarded=("        def spvm_no_pull(*args, **kwargs):\n            raise ValueError('SPVM requires a preloaded verified image; implicit pull refused')\n"
      "        cw.pull_image = spvm_no_pull\n        spvm_before = spvm_compute_snapshot(SPVM_COMPUTE_SCOPE)\n"+action+
      "        if module.params['action'] == 'recreate_or_restart_container':\n"
      "            spvm_current = cw.get_container_info()\n"
      "            if not spvm_current or spvm_current['Image'] != SPVM_COMPUTE_SCOPE['image_id'] or not spvm_current['State']['Running']:\n"
      "                module.fail_json(changed=cw.changed, msg='SPVM resulting compute image/state differs')\n"
      "        if spvm_compute_snapshot(SPVM_COMPUTE_SCOPE) != spvm_before:\n"
      "            module.fail_json(changed=cw.changed, msg='SPVM protected container/service/domain drift')\n"
      "        cw.result['spvm_scope_verified'] = True\n")
    return result.replace(action,guarded)


def request(value):
    fields={'schema_version','source','revision','inventory','profile','kolla_python','collections','configdir',
            'host','hostname','machine_sha256','compute_uuid','image','image_id','mode'}
    if (not isinstance(value,dict) or set(value)!=fields or type(value['schema_version']) is not int
            or value['schema_version']!=1):raise ValueError('exact compute-scope request required')
    for key in ('source','inventory','profile','kolla_python','collections','configdir'):
        if not Path(value[key]).is_absolute() or '\n' in value[key]:raise ValueError('absolute reviewed controller paths required')
    for key in ('host','hostname'):
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value[key]):raise ValueError('one literal inventory host required')
    if value['host'] in ('all','ungrouped','compute','control','network'):raise ValueError('inventory group is not a host')
    import uuid
    if str(uuid.UUID(value['compute_uuid']))!=value['compute_uuid']:raise ValueError('canonical compute UUID required')
    if (value['mode'] not in ('verify','replace-empty') or not re.fullmatch('[a-f0-9]{40}',value['revision'])
        or not re.fullmatch('[a-f0-9]{64}',value['machine_sha256'])
        or not re.fullmatch('sha256:[a-f0-9]{64}',value['image_id'])
        or not TAG_RE.fullmatch(value['image']) or value['image'].rsplit(':',1)[-1] in ('latest','main','master')):
        raise ValueError('pinned compute identity/image/mode required')
    return value


def inventory_binding(spec):
    code='''import json,sys
from ansible.inventory.data import InventoryData
from ansible.parsing.dataloader import DataLoader
from ansible.plugins.inventory.ini import InventoryModule
v=json.load(sys.stdin);data=InventoryData();InventoryModule().parse(data,DataLoader(),v['inventory'])
if v['host'] not in data.hosts or v['host'] in data.groups:raise ValueError('literal host, not group, required')
if data.hosts[v['host']] not in data.groups['compute'].get_hosts():raise ValueError('compute group membership required')
print(json.dumps({'inventory_target_bound':True}))
'''
    r=subprocess.run([spec['kolla_python'],'-c',code],input=json.dumps(spec),capture_output=True,text=True,timeout=30)
    if r.returncode or r.stdout.strip()!='{"inventory_target_bound": true}':raise ValueError('inventory target binding failed')


def inputs(spec):
    request(spec);source=Path(spec['source'])
    inventory_binding(spec)
    def git(*a):return subprocess.run(['git','-C',str(source),*a],check=True,capture_output=True,text=True,timeout=30).stdout.strip()
    if git('rev-parse','HEAD')!=spec['revision'] or git('status','--porcelain','--untracked-files=no'):
        raise ValueError('clean exact pinned Kolla source required')
    defaults=yaml.safe_load(read(source/'ansible/roles/nova-cell/defaults/main.yml'))
    plays=yaml.safe_load(read(source/'ansible/nova.yml'))
    selected=[p for p in plays if 'nova-cell-deploy' in p.get('tags',[])]
    if len(selected)!=1 or selected[0].get('roles')!=[{'role':'nova-cell','when':'enable_nova | bool'}]:
        raise ValueError('reviewed nova-cell-deploy play required')
    service=defaults['nova_cell_services']['nova-compute']
    if service.get('container_name')!='nova_compute':raise ValueError('upstream compute definition differs')
    # Do not empty nova_services: API/post-play references must remain defined.
    overrides={'nova_cell_services':{'nova-compute':service},'nova_compute_image_full':spec['image']}
    paths=[Path(spec[k]) for k in ('inventory','profile')]
    config=Path(spec['configdir']);paths += [config/'globals.yml',config/'passwords.yml']
    paths += sorted((config/'globals.d').glob('*')) if (config/'globals.d').exists() else []
    paths += [p for p in sorted((config/'nova-compute').rglob('*')) if p.is_file()]
    if yaml.safe_load(read(config/'globals.yml')).get('nova_compute_image_full')!=spec['image']:
        raise ValueError('prepare persistent Kolla image configuration first')
    protected={str(p):file_hash(p) for p in paths if p.is_file()}
    # Bind the full tracked source, not just HEAD; reject copied symlinks.
    tracked=git('ls-files','ansible','kolla_ansible').splitlines()
    for relative in tracked:
        p=source/relative
        if p.is_symlink() or not p.is_file():raise ValueError('regular tracked Kolla files required')
        protected[str(p)]=file_hash(p)
    runner_sources={p.name:file_hash(p) for p in (Path(__file__),Path(__file__).with_name('openstack_container_engine.py'),
                   Path(__file__).with_name('openstack_compute_image.py'))}
    return {'request':spec,'files':protected,'overrides':overrides,'runner_sources':runner_sources}


def cloud_admission(spec,connection):
    access=connection.session.auth.get_access(connection.session)
    if 'admin' not in access.role_names:raise ValueError('admin token required for replacement admission')
    node=connection.compute.get_hypervisor(spec['compute_uuid'])
    if node.id!=spec['compute_uuid'] or node.name!=spec['hostname']:raise ValueError('Nova host/UUID binding differs')
    services=list(connection.compute.services(binary='nova-compute',host=spec['hostname']))
    if len(services)!=1:raise ValueError('exact compute service required')
    for server in connection.compute.servers(all_projects=True,details=True):
        if server.compute_host==spec['hostname'] or server.status=='BUILD' or server.task_state:
            raise ValueError('assigned/inflight Nova instance: replacement refused')
    return services[0]


def command(spec,output):
    loader='import sys;sys.path.insert(0,sys.argv.pop(1));from kolla_ansible.cmd.kolla_ansible import main;sys.exit(main())'
    return [spec['kolla_python'],'-c',loader,spec['source'],'deploy-containers','--configdir',spec['configdir'],
            '-i',spec['inventory'],'-e','@'+spec['profile'],'-e','@'+str(output/'compute-only.json'),
            '--tags','nova-cell-deploy','--limit',spec['host'],'-v']


def stage(intent,output):
    spec=intent['request'];source=Path(spec['source']);output=Path(output)
    for name in intent['files']:
        p=Path(name)
        if not p.is_relative_to(source/'ansible'):continue
        destination=output/'run'/p.relative_to(source)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,destination)
    path=output/'run/ansible/library/kolla_container.py'
    path.write_text(guarded_module(path.read_text(),{k:spec[k] for k in
        ('mode','machine_sha256','compute_uuid','image','image_id')}))
    write(output/'compute-only.json',intent['overrides'])
    plugin=output/'run/ansible'
    config='[defaults]\nhost_key_checking=True\nretry_files_enabled=False\n'
    for option,folder in (('roles_path','roles'),('library','library'),('module_utils','module_utils'),
                          ('filter_plugins','filter_plugins'),('action_plugins','action_plugins')):
        config+=option+'='+str(plugin/folder)+'\n'
    (output/'ansible.cfg').write_text(config)
    return {str(p.relative_to(output)):file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}


def _execute(args,connection=None,runner=subprocess.run):
    # Own only the SDK session created by this invocation. Admission failure
    # and interrupted container runs must release it too; callers keep theirs.
    owned=None
    def connect():
        nonlocal owned
        import openstack
        owned=openstack.connect(verify='/etc/ssl/certs/ca-certificates.crt',api_timeout=20)
        return owned
    try:
        return _execute_operation(args,connection,runner,connect)
    finally:
        if owned is not None:owned.close()


def _execute_operation(args,connection,runner,connect):
    spec=request(json.loads(read(args.request)));intent=inputs(spec);output=Path(args.output)
    if not output.is_absolute() or output.resolve()!=output:raise ValueError('canonical private output required')
    if args.action=='plan-kolla-compute-container':
        service=None
        if spec['mode']=='replace-empty':
            if connection is None:
                connection=connect()
            service=cloud_admission(spec,connection)
        output.mkdir(mode=0o700,exist_ok=False)
        staged=stage(intent,output)
        plan={'schema_version':1,'intent':intent,'staged':staged,'created_at':int(time.time()),'guests_restarted':False,
              'service_id':service.id if service else None}
        plan['plan_sha256']=digest(plan);write(output/'compute-container-plan.json',plan)
        return {'status':'COMPUTE_CONTAINER_PLAN_READY','plan_sha256':plan['plan_sha256'],'mode':spec['mode']}
    if output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077:raise ValueError('private owned operation required')
    plan=json.loads(read(output/'compute-container-plan.json'))
    staged_names={str(p.relative_to(output)) for p in (output/'run').rglob('*') if p.is_file()}
    staged_names.update(('ansible.cfg','compute-only.json'))
    if (plan.get('plan_sha256')!=args.plan_sha256 or digest({k:v for k,v in plan.items() if k!='plan_sha256'})!=args.plan_sha256
        or plan['intent']!=intent or not 0<=time.time()-plan['created_at']<=900
        or any(p.is_symlink() for p in (output/'run').rglob('*'))
        or staged_names!=set(plan['staged'])
        or any(file_hash(output/p)!=h for p,h in plan['staged'].items())):raise ValueError('fresh unchanged scope approval required')
    journal=output/'compute-container-transaction.json'
    if journal.exists():raise ValueError('existing transaction: inspect outcome, never replay a container action')
    record={'status':'RUNNING','mode':spec['mode'],'plan_sha256':args.plan_sha256,'guests_restarted':False}
    write(journal,record)
    service=None;enable_after=False
    try:
        if spec['mode']=='replace-empty':
            if connection is None:
                connection=connect()
            service=cloud_admission(spec,connection);enable_after=service.status=='enabled'
            if service.id!=plan['service_id']:raise ValueError('Nova service identity changed since approval')
            record.update(service_id=service.id,enable_after=enable_after);write(journal,record)
            if enable_after:connection.compute.disable_service(service,disabled_reason='SPVM compute image '+args.plan_sha256[:16])
            after=cloud_admission(spec,connection)
            if after.id!=service.id or after.status!='disabled':raise ValueError('compute quarantine not confirmed')
        env={k:v for k,v in os.environ.items() if not k.startswith(('ANSIBLE_','KOLLA_','PYTHON','OS_'))}
        env.update(ANSIBLE_CONFIG=str(output/'ansible.cfg'),ANSIBLE_COLLECTIONS_PATH=spec['collections'],
                   KOLLA_ANSIBLE_DATA_FILES_PATH=str(output/'run'),PYTHONDONTWRITEBYTECODE='1',
                   PATH=str(Path(spec['kolla_python']).parent)+':/usr/local/bin:/usr/bin:/bin')
        with (output/'private-kolla.log').open('xb') as log:
            os.chmod(log.name,0o600)
            result=runner(command(spec,output),env=env,cwd=output,stdout=log,stderr=log,timeout=1200)
        record['kolla_exit']=result.returncode
        if result.returncode:raise ValueError('scoped Kolla operation failed; inspect private log')
        if '"spvm_scope_verified": true' not in (output/'private-kolla.log').read_text():
            raise ValueError('no proof that the guarded compute module executed')
        if inputs(spec)!=intent:raise ValueError('controller inputs changed during Kolla operation')
        if service is not None:
            after=cloud_admission(spec,connection)
            if after.id!=service.id or after.status!='disabled':raise ValueError('compute escaped quarantine during image operation')
        # Re-enable is deliberately separate: native exit0 is not runtime/PCI/
        # Placement/network acceptance. Use the guarded activation workflow.
        record['status']='COMPUTE_CONTAINER_VERIFIED' if spec['mode']=='verify' else 'COMPUTE_CONTAINER_APPLIED_QUARANTINED'
    except BaseException:
        record['status']='NEEDS_REVIEW';write(journal,record);raise
    write(journal,record);return record


def execute(args,connection=None,runner=subprocess.run):
    if args.action=='plan-kolla-compute-container':return _execute(args,connection,runner)
    output=Path(args.output)
    if (not output.is_absolute() or output.resolve()!=output or not output.is_dir()
            or output.stat().st_uid!=os.getuid() or output.stat().st_mode & 0o077):
        raise ValueError('private canonical operation directory required')
    fd=os.open(output/'.scope.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        st=os.fstat(fd)
        if st.st_uid!=os.getuid() or st.st_nlink!=1 or st.st_mode & 0o777!=0o600:raise ValueError('untrusted operation lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _execute(args,connection,runner)
    finally:os.close(fd)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('plan-kolla-compute-container','reconcile-kolla-compute-container'))
    for name in ('request','output'):p.add_argument('--'+name,required=True)
    p.add_argument('--plan-sha256');args=p.parse_args(argv)
    try:print(json.dumps(execute(args)));return 0
    except Exception as error:
        print(json.dumps({'status':'REFUSED_OR_NEEDS_REVIEW','error_class':type(error).__name__,
                          'output':args.output,'replay_permitted':False}));return 2


if __name__=='__main__':raise SystemExit(main())
