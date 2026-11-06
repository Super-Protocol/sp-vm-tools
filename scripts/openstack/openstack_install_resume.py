"""Select a verified continuation behind one stable, administrator-owned output.

Attempts are append-only evidence, not retries of an ambiguous child operation.
Only existing reviewed reconcilers may authorize resuming host mutations.
"""
from pathlib import Path
import json
import os
import stat

from openstack_container_engine import file_hash, write
from openstack_errors import InstallerError
from openstack_install_continue import document


def directory(path):
    from openstack_install import protected_parent
    protected_parent(path)
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or value.st_mode & 0o077:
        raise InstallerError('Installation history must be a root-private directory: ' + str(path))


def journal(path):
    from openstack_install import PHASES, SCHEMA
    directory(path)
    record = document(path / 'installation.json')
    done = record.get('completed')
    if (record.get('schema_version') != SCHEMA or not isinstance(done, list)
            or done != list(PHASES[:len(done)]) or record.get('guests_created') is not False
            or not isinstance(record.get('inputs'), dict)):
        raise InstallerError('Unrecognized installation journal; no state was adopted: ' + str(path))
    return record


def permitted(root, path):
    # An already recorded continuation may be a sibling; new attempts live only
    # below this output. No arbitrary directory from an unchecked pointer.
    if not path.is_absolute() or path.resolve() != path or not (
            path.parent == root.parent or path.is_relative_to(root / 'attempts')):
        raise InstallerError('Invalid continuation history path.')
    return path


def select(root):
    try:
        return inspect_history(Path(root))
    except InstallerError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise InstallerError('Installation history is missing, partial or invalid under ' + str(root)
            + '. Inspect installation.json and attempts/current.json; no files were replaced and no hosts were changed.') from None


def inspect_history(root):
    """Read history only. Never create files or contact a host on refusal."""
    from openstack_install import PHASES
    root = Path(root)
    directory(root)
    origin = root
    seen = set()
    while True:
        if origin in seen or len(seen) >= 128:
            raise InstallerError('Cyclic or excessive installation history.')
        seen.add(origin)
        record = journal(origin)
        marker = origin / 'continued-by.json'
        if not marker.exists() and not marker.is_symlink():
            break
        continuation = document(marker)
        target = permitted(root, Path(continuation.get('output', '')))
        target_record = journal(target)
        plan = document(target / 'plan.json')
        from openstack_container_engine import digest
        if (target_record.get('continued_from') != str(origin)
                or target_record.get('plan_sha256') != continuation.get('plan_sha256')
                or digest(plan) != continuation.get('plan_sha256')):
            raise InstallerError('Continuation marker and child journal disagree.')
        origin = target

    history = root / 'attempts'
    if history.exists() or history.is_symlink():
        directory(history)
        pointer = history / 'current.json'
        if pointer.exists() or pointer.is_symlink():
            item = document(pointer)
            target = permitted(root, Path(item.get('output', '')))
            if target.parent != history or not target.name.startswith('attempt-'):
                raise InstallerError('Invalid attempt pointer.')
            child = journal(target)
            if (child.get('automatic_origin') != item.get('origin')
                    or child.get('automatic_mode') != item.get('mode')
                    or Path(item.get('origin', '')) not in seen):
                raise InstallerError('Attempt history and journal disagree.')
            if target != origin:
                if child.get('mutation_started') is True:
                    raise InstallerError('A mutating attempt has no matching continuation claim; inspect ' + str(target))
                elif child.get('status') not in {
                        'SCANNING', 'AWAITING_APPROVAL', 'CHECKED_NOT_INSTALLED', 'NEEDS_REVIEW'}:
                    raise InstallerError('Unknown non-mutating attempt state.')
                elif child.get('active_phase') not in (None, 'scan'):
                    raise InstallerError('An unclaimed attempt may have changed hosts; inspect ' + str(target))

    if record.get('status') == 'OPENSTACK_READY_NO_SWARM_CREATED':
        if record['completed'] != list(PHASES):
            raise InstallerError('Incomplete success journal.')
        from openstack_install import RESULTS
        own_phases = document(origin / 'plan.json').get('remaining_phases', list(PHASES))
        for name in PHASES:
            result = document(origin / (name + '-result.json')) if (
                origin / (name + '-result.json')).exists() else None
            # Continuations inherit earlier results. Their reviewed lineage is
            # not a second full installation and must not invent missing receipts.
            if result is None and record.get('continued_from') and name not in own_phases:
                continue
            if not result or result.get('status') != RESULTS[name]:
                raise InstallerError('Completed installation receipt is missing or inconsistent: ' + name)
        for filename, wanted in record['inputs'].items():
            if file_hash(Path(filename)) != wanted:
                raise InstallerError('Completed installation input changed: ' + filename)
        return origin, 'ready'
    if (not record['completed'] and record.get('active_phase') in (None, 'scan')
            and record.get('status') in {'SCANNING', 'AWAITING_APPROVAL', 'CHECKED_NOT_INSTALLED', 'NEEDS_REVIEW'}
            and not record.get('mutation_started')):
        return origin, 'scan'
    if record.get('status') == 'NEEDS_REVIEW' and record.get('active_phase') == 'kolla':
        from openstack_install_continue import reviewed
        reviewed(origin)
        return origin, 'kolla'
    if record.get('status') == 'NEEDS_REVIEW' and record.get('active_phase') == 'platform':
        if (origin/'reconciled-compute.json').exists():
            from openstack_install_channel_continue import reviewed
            reviewed(origin)
            return origin, 'channel'
        if any(origin.glob('activate-*')):
            from openstack_install_compute_continue import reviewed
            reviewed(origin)
            return origin, 'compute'
        from openstack_install_platform_continue import reviewed
        reviewed(origin)
        return origin, 'platform'
    raise InstallerError('Cannot safely continue phase ' + str(record.get('active_phase'))
        + ' from ' + str(origin) + '. Its result has not been reconciled; completed phases will not be replayed. '
        'Inspect installation.json and the phase log. Do not delete journals or choose a new output to bypass this check.')


def attempt(root):
    """Reserve new private local evidence only, under the coordinator lock."""
    history = root / 'attempts'
    if not history.exists():
        history.mkdir(mode=0o700)
    directory(history)
    for number in range(1, 100001):
        output = history / ('attempt-%06d' % number)
        if not output.exists() and not output.is_symlink():
            return output
    raise InstallerError('Too many installation attempts; inspect the history.')


def remember(root, origin, mode, tx):
    tx.record.update(automatic_origin=str(origin), automatic_mode=mode)
    if mode == 'scan':
        tx.record['continued_from'] = str(origin)
        tx.inputs[str(origin / 'installation.json')] = file_hash(origin / 'installation.json')
    tx.save()
    write(root / 'attempts/current.json', {'output': str(tx.output), 'origin': str(origin), 'mode': mode})


def claim_scan(origin, tx, plan):
    """Bind a previously check-only output before the first installation phase."""
    if select(origin) != (origin, 'scan'):
        raise InstallerError('The checked installation is no longer at an unstarted boundary.')
    tx.unchanged()
    from openstack_container_engine import digest
    with (origin / 'continued-by.json').open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump({'output': str(tx.output), 'plan_sha256': digest(plan)}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(origin, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
