"""Identify a Ptyxis ancestor without reading command lines or terminal content.

Runs on the host before crossing into the toolbox. PIDs are deliberately host
PIDs, since the GNOME Shell window belongs to that namespace.
"""
import json
import os
import selectors
import subprocess
import time
from pathlib import Path


def discover(pid=None, proc=Path('/proc')):
    pid = os.getpid() if pid is None else pid
    seen = set()
    has_terminal = False
    for _ in range(32):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        directory = proc / str(pid)
        try:
            if directory.stat().st_uid != os.getuid():
                break
            stat = (directory / 'stat').read_text()
            name = stat[stat.index('(') + 1:stat.rindex(')')]
            fields = stat[stat.rindex(')') + 2:].split()
            has_terminal |= int(fields[4]) != 0
            if name == 'ptyxis' and has_terminal:
                return {'kind': 'ptyxis-window', 'pid': pid, 'start': fields[19]}
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return None


def codex_terminal(proc=Path('/proc')):
    targets = {}
    for directory in proc.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if directory.stat().st_uid != os.getuid() or (directory / 'comm').read_text().strip() != 'codex':
                continue
            stat = (directory / 'stat').read_text()
            fields = stat[stat.rindex(')') + 2:].split()
            if int(fields[4]) == 0:
                continue
            target = discover(int(directory.name), proc)
            if target:
                targets[(target['pid'], target['start'])] = target
        except (OSError, ValueError, IndexError):
            continue
    return next(iter(targets.values())) if len(targets) == 1 else None


def shared_codex_terminal(session_id, cwd, proc=Path('/proc')):
    """Resolve a shared-server hook only for a verified terminal client.

    Read metadata without turns, tools or transcripts. Require the Codex TUI
    originator and exactly one distinct terminal target among matching clients.
    No thread is resumed, subscribed to, or sent a prompt.
    """
    codex = Path.home() / '.local/bin/codex'
    if not codex.is_file():
        return None
    child = subprocess.Popen([str(codex), 'app-server', '--stdio'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    pending = bytearray()
    deadline = time.monotonic() + 1

    def request(method, params, identifier):
        child.stdin.write((json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n').encode())
        child.stdin.flush()
        while time.monotonic() < deadline:
            while b'\n' in pending:
                line, _, remainder = pending.partition(b'\n')
                pending[:] = remainder
                message = json.loads(line)
                if message.get('id') == identifier:
                    if 'error' in message:
                        raise ValueError('Thread metadata unavailable')
                    return message['result']
            if not selector.select(timeout=0.1):
                continue
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk or len(pending) + len(chunk) > 1_000_000:
                break
            pending.extend(chunk)
        raise ValueError('Thread metadata timeout')

    try:
        request('initialize', {'clientInfo': {'name': 'quiet-island-terminal', 'version': '0.2.0'}}, 1)
        child.stdin.write(b'{"method":"initialized"}\n')
        child.stdin.flush()
        thread = request('thread/read', {'threadId': session_id, 'includeTurns': False}, 2)['thread']
        if thread.get('originator') != 'codex-tui' or thread.get('cwd') != cwd:
            return None
        return codex_terminal(proc)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    finally:
        selector.close()
        child.terminate()
        try:
            child.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--session')
    parser.add_argument('--cwd', default='')
    args = parser.parse_args()
    target = shared_codex_terminal(args.session, args.cwd) if args.session else discover()
    print(json.dumps(target, separators=(',', ':')))
