import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys


specification = importlib.util.spec_from_file_location('wenmai_revision_index', Path(__file__).with_name('revision_index.py'))
locator = importlib.util.module_from_spec(specification)
specification.loader.exec_module(locator)


class BatchError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BatchError(message)


def inside_path(workspace, value, boundary=None):
    require(isinstance(value, str) and bool(value.strip()), 'A relative path is required.')
    relative = Path(value)
    require(not relative.is_absolute() and '..' not in relative.parts, 'Paths must remain inside the workspace.')
    target = workspace / relative
    for component in (target, *target.parents):
        if component == workspace.parent:
            break
        try:
            information = component.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(information.st_mode) and not getattr(information, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400), 'Linked or reparse paths are not allowed.')
    target = target.resolve()
    require(target.is_relative_to(workspace), 'Path escaped the workspace.')
    if boundary is not None:
        require(target.is_relative_to(boundary), 'Output escaped its assigned directory.')
    return target


def checked_file(workspace, entry, boundary=None):
    require(isinstance(entry, dict), 'File entries require path and SHA256.')
    path = inside_path(workspace, entry.get('path'), boundary)
    require(path.is_file(), f'Missing file: {entry.get("path")}')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == entry.get('sha256'), f'File version changed: {entry.get("path")}')
    return path, raw


def json_object(raw):
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=locator.unique_object, parse_constant=locator.reject_constant)
    except (ValueError, RecursionError) as error:
        raise BatchError(f'Invalid JSON: {error}') from error
    require(isinstance(value, dict), 'External JSON must be an object.')
    return value


def check_identity(record, task, run_id):
    require(all(record.get(field) == expected for field, expected in (
        ('run_id', run_id), ('task_id', task['task_id']),
        ('attempt_id', task['attempt_id']), ('agent_id', task['agent_id']))), 'Receipt belongs to another run, task, attempt or agent.')


def verify_delivery(task, workspace, work_dir, source, run_id):
    receipt_snapshot = checked_file(workspace, task.get('delivery'), work_dir)
    receipt = json_object(receipt_snapshot[1])
    check_identity(receipt, task, run_id)
    require(receipt.get('source_sha256') == task['source']['sha256'], 'Delivery used another source version.')
    report = checked_file(workspace, receipt.get('report'), work_dir)
    require(bool(report[1].decode('utf-8-sig').strip()), 'Empty report is not a delivery.')
    reading_snapshot = checked_file(workspace, receipt.get('read_record'), work_dir)
    readings = json_object(reading_snapshot[1])
    locator.validate_record(readings.get('source'), locator.build_index(source[1]))
    snapshots = [receipt_snapshot, report, reading_snapshot]
    if task['kind'] == 'editor':
        candidate = checked_file(workspace, receipt.get('candidate'), work_dir)
        locator.validate_record(readings.get('candidate'), locator.build_index(candidate[1]))
        snapshots.append(candidate)
    return snapshots


def verify_closure(task, workspace, control_root, run_id):
    snapshot = checked_file(workspace, task.get('close_receipt'), control_root)
    close = json_object(snapshot[1])
    check_identity(close, task, run_id)
    require(close.get('operation') == 'close_agent', 'Closure requires a control-plane close_agent event.')
    result = close.get('tool_result')
    require(isinstance(result, dict) and set(result) == {'previous_status'}, 'Closure needs the recorded tool result.')
    previous = result['previous_status']
    if isinstance(previous, dict):
        require(set(previous) in ({'completed'}, {'errored'}), 'Unknown previous agent status.')
        value = next(iter(previous.values()))
        require(isinstance(value, str) or 'completed' in previous and value is None, 'Malformed previous agent status.')
    else:
        require(previous in {'pending_init', 'running', 'interrupted', 'shutdown'}, 'Unknown or unconfirmed closure result.')
    if task['status'] == 'returned':
        require(isinstance(previous, dict) and 'completed' in previous, 'A returned task must have completed before closure.')
    return snapshot


IDENTITY_FIELDS = {'run_id', 'task_id', 'attempt_id', 'agent_id'}
TERMINAL_STATUSES = {'completed', 'failed', 'cancelled'}


def exact_object(value, fields, description):
    require(isinstance(value, dict) and set(value) == fields, f'Malformed {description}.')


def control_snapshot(workspace, entry, control_root):
    exact_object(entry, {'path', 'sha256'}, 'control file reference')
    return checked_file(workspace, entry, control_root)


def evidence_snapshot(record, workspace, control_root):
    # These are steward-normalized records, not a universal host response format.
    # Check the raw transcript bytes without claiming to authenticate their origin.
    snapshot = control_snapshot(workspace, record['evidence'], control_root)
    require(bool(snapshot[1].strip()), 'A raw evidence transcript is required.')
    return snapshot


def verify_host_lifecycle(document, workspace, control_root):
    if 'host_lifecycle' not in document:
        return 'close_agent', []
    contract = document['host_lifecycle']
    require(isinstance(contract, dict), 'Host lifecycle must be an explicit object.')
    mode = contract.get('mode')
    require(isinstance(mode, str) and mode in {'close_agent', 'terminal_inventory'}, 'Unknown host lifecycle mode.')
    if mode == 'close_agent':
        exact_object(contract, {'mode'}, 'close_agent host contract')
        return mode, []
    exact_object(contract, {'mode', 'close_agent_available', 'terminal_releases_slot', 'capability_receipt'}, 'terminal_inventory host contract')
    require(contract['close_agent_available'] is False and contract['terminal_releases_slot'] is True, 'Host must explicitly lack close_agent and release slots on terminal state.')
    snapshot = control_snapshot(workspace, contract['capability_receipt'], control_root)
    capabilities = json_object(snapshot[1])
    exact_object(capabilities, {'schema', 'run_id', 'mode', 'close_agent_available', 'terminal_releases_slot', 'evidence'}, 'host capability receipt')
    require(capabilities['schema'] == 'wenmai.host-lifecycle.v1' and capabilities['run_id'] == document['run_id'], 'Host capability receipt belongs to another run or schema.')
    require(capabilities['mode'] == mode and capabilities['close_agent_available'] is False and capabilities['terminal_releases_slot'] is True, 'Host capability receipt does not support terminal slot release.')
    return mode, [snapshot, evidence_snapshot(capabilities, workspace, control_root)]


def verify_release(task, workspace, control_root, run_id, registered_identities):
    snapshot = control_snapshot(workspace, task.get('release_receipt'), control_root)
    release = json_object(snapshot[1])
    exact_object(release, IDENTITY_FIELDS | {'schema', 'operation', 'observation_id', 'terminal_event', 'inventory'}, 'slot release receipt')
    check_identity(release, task, run_id)
    require(release['schema'] == 'wenmai.slot-release.v1' and release['operation'] == 'terminal_inventory', 'Slot release requires terminal_inventory evidence.')
    observation = release['observation_id']
    require(isinstance(observation, str) and bool(observation.strip()), 'A release observation identifier is required.')
    terminal_snapshot = control_snapshot(workspace, release['terminal_event'], control_root)
    terminal = json_object(terminal_snapshot[1])
    exact_object(terminal, IDENTITY_FIELDS | {'schema', 'observation_id', 'status', 'evidence'}, 'terminal event')
    check_identity(terminal, task, run_id)
    require(terminal['schema'] == 'wenmai.terminal-event.v1' and terminal['observation_id'] == observation, 'Terminal event has another schema or observation.')
    terminal_status = terminal['status']
    require(isinstance(terminal_status, str) and terminal_status in TERMINAL_STATUSES, 'A known terminal agent state is required.')
    expected = {'returned': {'completed'}, 'failed': {'failed'}, 'cancelled': {'cancelled'}, 'superseded': TERMINAL_STATUSES}
    require(task['status'] in expected and terminal_status in expected[task['status']], 'Terminal event contradicts the recorded task outcome.')
    terminal_evidence = evidence_snapshot(terminal, workspace, control_root)
    inventory_snapshot = control_snapshot(workspace, release['inventory'], control_root)
    inventory = json_object(inventory_snapshot[1])
    exact_object(inventory, {'schema', 'run_id', 'observation_id', 'complete', 'agents', 'evidence'}, 'agent inventory')
    require(inventory['schema'] == 'wenmai.agent-inventory.v1' and inventory['run_id'] == run_id and inventory['observation_id'] == observation, 'Inventory has another run, schema or observation.')
    require(inventory['complete'] is True, 'A complete recorded run inventory is required.')
    agents = inventory['agents']
    require(isinstance(agents, list), 'Inventory agents must be an array.')
    seen_agents, matching = set(), []
    for agent in agents:
        exact_object(agent, IDENTITY_FIELDS | {'status'}, 'inventory agent')
        require(all(isinstance(agent[field], str) and bool(agent[field].strip()) for field in IDENTITY_FIELDS), 'Inventory identities must be nonempty strings.')
        require(agent['run_id'] == run_id and agent['agent_id'] not in seen_agents, 'Inventory agent belongs to another run or is duplicated.')
        require(tuple(agent[field] for field in ('run_id', 'task_id', 'attempt_id', 'agent_id')) in registered_identities,
                'Inventory contains an unregistered task, attempt or agent; reconcile the registry before release.')
        seen_agents.add(agent['agent_id'])
        require(isinstance(agent['status'], str) and agent['status'] in TERMINAL_STATUSES | {'running'}, 'Inventory contains an unknown agent state.')
        if agent['agent_id'] == task['agent_id']:
            check_identity(agent, task, run_id)
            matching.append(agent)
    require(len(matching) == 1 and matching[0]['status'] == terminal_status, 'Inventory must positively confirm the same terminal agent and state.')
    inventory_evidence = evidence_snapshot(inventory, workspace, control_root)
    return [snapshot, terminal_snapshot, terminal_evidence, inventory_snapshot, inventory_evidence]


def verify_batch(document, workspace):
    try:
        return check_batch(document, Path(workspace).resolve())
    except (locator.LocatorError, KeyError, TypeError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BatchError(str(error)) from error


def check_batch(document, workspace):
    require(isinstance(document, dict) and isinstance(document.get('run_id'), str) and bool(document['run_id'].strip()), 'A run identifier is required.')
    limit = document.get('max_open_agents')
    require(type(limit) is int and limit > 0, 'Open-agent limit must be a positive integer.')
    tasks = document.get('tasks')
    require(isinstance(tasks, list), 'Tasks must be a list.')
    output_root = inside_path(workspace, document.get('output_root'))
    control_root = inside_path(workspace, document.get('control_root'))
    require(not output_root.is_relative_to(control_root) and not control_root.is_relative_to(output_root), 'Control and agent output directories must be disjoint.')
    lifecycle_mode, lifecycle_snapshots = verify_host_lifecycle(document, workspace, control_root)
    registered_identities = {
        (document['run_id'], item['task_id'], item['attempt_id'], item['agent_id'])
        for item in tasks if isinstance(item, dict)
        and all(isinstance(item.get(field), str) for field in ('task_id', 'attempt_id', 'agent_id'))
    }
    task_ids, attempt_ids, agent_ids, editor_chapters = set(), set(), set(), set()
    work_dirs, sources, outputs, snapshots = [], [], [], list(lifecycle_snapshots)
    open_agents, delivered, tool_closures, released_slots = 0, 0, 0, 0
    for task in tasks:
        require(isinstance(task, dict), 'Each task must be an object.')
        task_id = task.get('task_id')
        require(isinstance(task_id, str) and task_id and task_id not in task_ids, 'Task identifiers must be unique.')
        task_ids.add(task_id)
        attempt = task.get('attempt_id')
        require(isinstance(attempt, str) and bool(attempt.strip()) and attempt not in attempt_ids, 'Attempt identifiers must be unique.')
        attempt_ids.add(attempt)
        kind = task.get('kind')
        require(kind in {'editor', 'reader'}, 'Use editor or reader tasks; outline work needs its own bounded plan.')
        chapters = task.get('chapters')
        require(isinstance(chapters, list) and bool(chapters) and all(type(number) is int and number > 0 for number in chapters) and len(set(chapters)) == len(chapters), 'Explicit unique chapter numbers are required.')
        require(kind != 'editor' or len(chapters) == 1, 'Single-chapter mode permits only one chapter per editor.')
        work_dir = inside_path(workspace, task.get('work_dir'), output_root)
        require(work_dir != output_root, 'Each task needs its own output subdirectory.')
        require(not any(work_dir.is_relative_to(other) or other.is_relative_to(work_dir) for other in work_dirs), 'Task output directories overlap.')
        work_dirs.append(work_dir)
        source = checked_file(workspace, task.get('source'))
        sources.append(source[0])
        snapshots.append(source)
        status = task.get('status')
        require(status in {'queued', 'running', 'returned', 'failed', 'cancelled', 'superseded'}, 'Unknown task status.')
        require(type(task.get('closed')) is bool, 'The closed flag must be boolean.')
        if lifecycle_mode != 'terminal_inventory':
            require('slot_released' not in task and 'release_receipt' not in task, 'Slot release fields require an explicit terminal_inventory host contract.')
        released = task.get('slot_released', False)
        require(type(released) is bool, 'The slot_released flag must be boolean.')
        require(not (task['closed'] and released), 'A task cannot claim both tool closure and terminal slot release.')
        require('close_receipt' not in task or task['closed'], 'A close receipt requires the closed flag; do not mix lifecycle evidence.')
        require('release_receipt' not in task or released, 'A release receipt requires the slot_released flag.')
        agent = task.get('agent_id')
        if status == 'queued':
            require(agent is None and not task['closed'] and not released and 'delivery' not in task and 'release_receipt' not in task, 'Queued tasks cannot claim an agent, delivery or slot release.')
        else:
            require(isinstance(agent, str) and agent and agent not in agent_ids, 'Agent context was missing or reused for another task.')
            agent_ids.add(agent)
        if status == 'returned' or lifecycle_mode == 'terminal_inventory' and 'delivery' in task:
            delivery_snapshots = verify_delivery(task, workspace, work_dir, source, document['run_id'])
            snapshots.extend(delivery_snapshots)
            outputs.extend((path, work_dir) for path, raw in delivery_snapshots)
            delivered += status == 'returned'
        if task['closed']:
            require(status != 'running', 'A running task cannot claim closure without recording failure or cancellation.')
            snapshots.append(verify_closure(task, workspace, control_root, document['run_id']))
            tool_closures += 1
        if released:
            require(status in {'returned', 'failed', 'cancelled', 'superseded'}, 'Only terminal tasks can release a slot.')
            snapshots.extend(verify_release(task, workspace, control_root, document['run_id'], registered_identities))
            released_slots += 1
        require(status != 'superseded' or task['closed'] or released, 'Superseded attempts must have verified closure or release before replacement.')
        if kind == 'editor' and not (status in {'failed', 'cancelled', 'superseded'} and (task['closed'] or released)):
            require(chapters[0] not in editor_chapters, 'An editor chapter already has a current attempt.')
            editor_chapters.add(chapters[0])
        if status != 'queued' and not task['closed'] and not released:
            open_agents += 1
    require(not any(source.is_relative_to(directory) for source in sources for directory in work_dirs), 'An input is inside a writable task directory.')
    require(not any(source.samefile(output) for source in sources for output, directory in outputs), 'Writable delivery file is an input alias.')
    require(not any(first.samefile(second) for first, first_dir in outputs for second, second_dir in outputs if first_dir != second_dir), 'Writable files in different tasks are aliases.')
    controls = [path for path, raw in snapshots if path.is_relative_to(control_root)]
    require(not any(control.samefile(other) for control in controls for other in [*sources, *(path for path, directory in outputs)]), 'Control event is a writable file or input alias.')
    for path, raw in snapshots:
        require(path.read_bytes() == raw, f'File changed during verification: {path.relative_to(workspace)}')
    require(open_agents <= limit, 'Open-agent limit exceeded; returned and failed agents still count until verified closure or release.')
    return {'status': 'integrity_only', 'run_id': document['run_id'], 'tasks': len(tasks), 'open_agents': open_agents, 'max_open_agents': limit, 'deliveries_verified': delivered, 'tool_closures_verified': tool_closures, 'released_slots_verified': released_slots, 'declarations_only': True, 'live_inventory_verified': False, 'literary_certification': False}


def main():
    parser = argparse.ArgumentParser(description='Verify frozen inputs, isolated outputs, declared reading and recorded agent lifecycle; does not spawn, close or adopt.')
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--registry', required=True, type=Path)
    options = parser.parse_args()
    try:
        print(json.dumps(verify_batch(json_object(options.registry.read_bytes()), options.workspace), ensure_ascii=False))
    except (BatchError, locator.LocatorError, OSError, UnicodeError, json.JSONDecodeError) as error:
        print(json.dumps({'status': 'fail', 'error': str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
