"""Prepare matched CI components; no container building on the manager."""
import json
from pathlib import Path

from openstack_container_engine import digest, file_hash
from openstack_install_phases import document
import openstack_prebuilt as prebuilt
import openstack_progress as progress


def prepare(i):
    import openstack_launcher as launcher
    kit = prebuilt.selected(i.path)
    i.run_id = i.cfg['deployment_id'] + '-' + digest(str(i.output))[:12]
    recipe, _, _ = launcher.recipe(i.path)
    tools = Path(__file__).resolve().parents[2]
    with progress.step('prepare-launcher'):
        staged = launcher.stage(i.path, str(tools), recipe['tools_commit'], str(i.output / 'launcher'))
    i.launcher_files = launcher.verify(i.path, str(i.output / 'launcher'), staged['receipt_sha256'])
    root = i.path.parent.parent
    raw = json.loads(json.dumps(yaml_config(i.config)))
    raw['kolla'].update(
        compute_image=kit['images']['compute']['reference'],
        compute_image_id=kit['images']['compute']['config_id'],
        skyline_console_image=kit['images']['console']['reference'],
        skyline_console_image_id=kit['images']['console']['config_id'],
        installation_kit=str(root / 'installation-kit.json'),
        installation_kit_sha256=file_hash(root / 'installation-kit.json'))
    i.config = document(i.output / 'deployment.artifacts.yaml', raw)
    i.refresh()
    return {'status': 'MATCHED_PREBUILT_COMPONENTS_VERIFIED', 'images': kit['images'],
            'installation_kit_sha256': file_hash(root / 'installation-kit.json'),
            'wheel_sha256': kit['driver']['wheel_sha256'], 'launcher_sha256': staged['receipt_sha256']}


def yaml_config(path):
    import yaml
    return yaml.safe_load(Path(path).read_bytes())
