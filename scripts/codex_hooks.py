#!/usr/bin/env python3
"""Install Quiet Island observer hooks, preserving other user hooks and trust."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import shlex
import shutil
import tempfile

EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreModelRequest', 'PreToolUse',
          'PostToolUse', 'PermissionRequest', 'Interrupt', 'Stop', 'SessionEnd')
EVENTS_BY_TOOL = {'codex': EVENTS, 'claude': (
    'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
    'PostToolUseFailure', 'PermissionRequest', 'Notification', 'Stop',
    'StopFailure', 'Elicitation', 'ElicitationResult', 'SessionEnd')}


def merge_hooks(existing, command, tool='codex'):
    result = copy.deepcopy(existing)
    if not isinstance(result, dict) or not isinstance(result.get('hooks', {}), dict):
        raise ValueError('Expected an object with a hooks object')
    hooks = result.setdefault('hooks', {})
    for event in EVENTS_BY_TOOL[tool]:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f'Expected a hook group array for {event}')
        invocation = shlex.quote(str(command)) + ' hook --tool ' + tool + ' --event ' + event
        if any(handler.get('command') == invocation
               for group in groups for handler in group.get('hooks', [])):
            continue
        # Synchronous observers preserve lifecycle order. No stdout, no decisions;
        # the three-second timeout bounds an unavailable bridge/toolbox.
        groups.append({'hooks': [{'type': 'command', 'command': invocation, 'timeout': 3}]})
    return result


def install(path, command, tool='codex'):
    path = Path(path)
    existing = json.loads(path.read_text()) if path.exists() else {}
    result = merge_hooks(existing, command, tool)
    return write_configuration(path, existing, result)


def write_configuration(path, existing, result):
    path = Path(path)
    if result == existing:
        return False, None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = path.with_name(path.name + '.backup-' + stamp)
        shutil.copy2(path, backup)
    fd, temporary = tempfile.mkstemp(prefix='.quiet-island-hooks-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(result, handle, indent=2, ensure_ascii=False)
            handle.write('\n')
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True, backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=Path.home() / '.codex/hooks.json')
    parser.add_argument('--command', type=Path, default=Path.home() / '.local/bin/quiet-island')
    args = parser.parse_args()
    if not args.command.is_file():
        raise SystemExit('Install Quiet Island before connecting hooks')
    changed, backup = install(args.file, args.command)
    print('Hooks installed.' if changed else 'Hooks already configured; no changes.')
    if backup:
        print(f'Backup: {backup}')
    print(f'Configuration: {args.file}')
    print('Codex must review and trust these hooks through /hooks or Settings > Hooks.')


if __name__ == '__main__':
    main()
