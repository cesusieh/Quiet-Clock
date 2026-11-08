"""Commands for the bridge, event publishers, and non-blocking observer hooks."""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

from .model import TOOLS, STATES, hook_event, normalize_event, terminal_target


def parser():
    root = argparse.ArgumentParser(prog="quiet-island")
    commands = root.add_subparsers(dest="command", required=True)
    service = commands.add_parser("service", help="Run the local session-bus bridge")
    service.add_argument("--no-audio", action="store_true")
    service.add_argument("--no-usage", action="store_true")
    service.add_argument("--stale-after", type=int, default=900)
    event = commands.add_parser("event", help="Publish an explicit session event")
    event.add_argument("--tool", required=True, choices=sorted(TOOLS))
    event.add_argument("--session", required=True)
    event.add_argument("--status", required=True, choices=sorted(STATES))
    event.add_argument("--project", default="")
    hook = commands.add_parser("hook", help="Observe a hook JSON payload from stdin")
    hook.add_argument("--tool", required=True, choices=sorted(TOOLS))
    hook.add_argument("--event", default="")
    hook.add_argument("--terminal-context", default="", help=argparse.SUPPRESS)
    commands.add_parser("snapshot", help="Read the bridge snapshot")
    commands.add_parser('claude-statusline', help='Send Claude Code status-line quota to the bridge')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        from .service import BridgeService, publish, get_snapshot, publish_claude_usage

        if args.command == "service":
            if not 10 <= args.stale_after <= 86400:
                raise ValueError("stale-after must be between 10 and 86400 seconds")
            BridgeService(audio=not args.no_audio, stale_after=args.stale_after, usage=not args.no_usage).run()
        elif args.command == "snapshot":
            print(json.dumps(get_snapshot(), ensure_ascii=False, indent=2))
        elif args.command == "event":
            publish(normalize_event({"tool": args.tool, "id": args.session,
                                     "status": args.status, "project": args.project}))
        elif args.command == 'claude-statusline':
            from .usage import claude_quota_summary, claude_context_summary
            raw = sys.stdin.buffer.read(65537)
            if len(raw) > 65536:
                raise ValueError('Status line input too large')
            payload = json.loads(raw)
            quota = claude_quota_summary(payload)
            context = claude_context_summary(payload)
            try:
                from .service import publish_claude_context
                publish_claude_context(payload.get("session_id"), context["remaining"] if context else None)
            except Exception:
                pass
            if quota:
                print(f"Claude · cota 5 h: {int(quota['remaining'] + 0.5)}% restante", flush=True)
            else:
                print('Claude · cota 5 h: —', flush=True)
            try:
                publish_claude_usage(quota)
            except Exception:
                pass
        elif args.command == "hook":
            raw = sys.stdin.buffer.read(65537)
            if len(raw) > 65536:
                raise ValueError("Hook input too large")
            event = hook_event(args.tool, args.event, json.loads(raw))
            if event:
                if args.terminal_context and len(args.terminal_context) <= 256:
                    target = terminal_target(json.loads(args.terminal_context))
                    if (target is None and args.tool == 'codex' and
                            args.event != 'SessionEnd' and args.terminal_context == 'null'):
                        # Shared-daemon hooks have no terminal ancestry. Only
                        # resolve once; subsequent events retain the saved route.
                        previous = next((s for s in get_snapshot()['sessions']
                            if s['tool'] == 'codex' and s['id'] == event['id']), {})
                        if not previous.get('terminal'):
                            try:
                                helper = Path(__file__).with_name('terminal_context.py')
                                result = subprocess.run(['flatpak-spawn', '--host', '/usr/bin/python3',
                                    str(helper), '--session', event['id'], '--cwd', payload_cwd(raw)],
                                    capture_output=True, timeout=1.5, check=True)
                                target = terminal_target(json.loads(result.stdout))
                            except (OSError, ValueError, subprocess.SubprocessError):
                                pass
                    if target:
                        event['terminal'] = target
                publish(event)
    except Exception as error:
        # Hooks are observers: no stdout, no approval output, no agent failures.
        # Deliberately do not log payloads, exception contents, commands or prompts.
        if args.command == 'claude-statusline':
            print('Claude · cota 5 h: —')
            return 0
        if args.command == "hook":
            logging.warning("Quiet Island: observer unavailable (%s)", type(error).__name__)
            return 0
        logging.error("Quiet Island: %s", error)
        return 1
    return 0


def payload_cwd(raw):
    value = json.loads(raw).get('cwd', '')
    return value if isinstance(value, str) and len(value) <= 4096 else ''
