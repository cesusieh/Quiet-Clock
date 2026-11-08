#!/usr/bin/env python3
"""Connect Claude Code CLI to Quiet Island without replacing user settings."""
import argparse
from pathlib import Path
from codex_hooks import install


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=Path.home() / '.claude/settings.json')
    parser.add_argument('--command', type=Path, default=Path.home() / '.local/bin/quiet-island')
    args = parser.parse_args()
    if not args.command.is_file():
        raise SystemExit('Install Quiet Island first')
    changed, backup = install(args.file, args.command, tool='claude')
    print('Claude Code hooks installed.' if changed else 'Claude Code hooks already configured.')
    if backup:
        print(f'Backup: {backup}')
    print(f'Configuration: {args.file}')
    print('Restart Claude Code, then inspect the observers in /hooks.')


if __name__ == '__main__':
    main()
