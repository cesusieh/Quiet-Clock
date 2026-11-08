#!/usr/bin/env python3
"""Connect the official Claude Code status-line quota to Quiet Island."""
import argparse
import copy
import json
from pathlib import Path
import shlex
from codex_hooks import write_configuration


def configure(path, command):
    path = Path(path)
    existing = json.loads(path.read_text()) if path.exists() else {}
    invocation = shlex.quote(str(command)) + ' claude-statusline'
    current = existing.get('statusLine')
    if current and (not isinstance(current, dict) or current.get('command') != invocation):
        raise ValueError('An existing custom status line is configured; it was not modified')
    result = copy.deepcopy(existing)
    result['statusLine'] = {**(current or {}), 'type': 'command',
        'command': invocation, 'refreshInterval': 60}
    return write_configuration(path, existing, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=Path.home() / '.claude/settings.json')
    parser.add_argument('--command', type=Path, default=Path.home() / '.local/bin/quiet-island')
    args = parser.parse_args()
    if not args.command.is_file():
        raise SystemExit('Install Quiet Island first')
    changed, backup = configure(args.file, args.command)
    print('Claude quota status line connected.' if changed else 'Claude quota status line already configured.')
    if backup:
        print(f'Backup: {backup}')


if __name__ == '__main__':
    main()
