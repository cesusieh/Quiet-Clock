"""Read aggregate Codex quota through its local CLI; never request model output."""
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import time


def claude_quota_summary(payload, now=None):
    if not isinstance(payload, dict):
        return None
    limits = payload.get('rate_limits')
    window = limits.get('five_hour') if isinstance(limits, dict) else None
    if not isinstance(window, dict):
        return None
    used, reset = window.get('used_percentage'), window.get('resets_at')
    if (type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100 or
            type(reset) not in (int, float) or not math.isfinite(reset) or reset <= 0):
        return None
    return {'kind': 'quota', 'remaining': 100 - used, 'window_minutes': 300,
            'resets_at': int(reset), 'updated_at': time.time() if now is None else now}


def published_claude_usage(raw, now=None):
    if not isinstance(raw, dict) or raw.get('tool') != 'claude':
        raise ValueError('Unsupported usage source')
    now = time.time() if now is None else now
    quota = raw.get('usage')
    if quota is None:
        return {'kind': 'unavailable', 'updated_at': now}
    if not isinstance(quota, dict) or quota.get('kind') != 'quota' or quota.get('window_minutes') != 300:
        raise ValueError('Invalid quota')
    remaining, reset = quota.get('remaining'), quota.get('resets_at')
    if (type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 <= remaining <= 100 or
            type(reset) is not int or reset <= 0):
        raise ValueError('Invalid quota')
    return {'kind': 'quota', 'remaining': remaining, 'window_minutes': 300,
            'resets_at': reset, 'updated_at': now}


def quota_summary(response, now=None):
    if not isinstance(response, dict):
        return None
    limits = response.get('rateLimitsByLimitId') or {}
    if not isinstance(limits, dict):
        return None
    quota = limits.get('codex') or response.get('rateLimits') or {}
    if not isinstance(quota, dict):
        return None
    window = quota.get('primary') or {}
    if not isinstance(window, dict):
        return None
    used, duration, reset = (window.get(k) for k in ['usedPercent', 'windowDurationMins', 'resetsAt'])
    if (type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100 or
            type(duration) is not int or duration <= 0 or type(reset) is not int):
        return None
    return {'kind': 'quota', 'remaining': 100 - used, 'window_minutes': duration,
            'resets_at': reset, 'updated_at': time.time() if now is None else now}


def read_codex_quota(session_ids=None):
    command = Path.home() / '.local/bin/codex'
    if not command.is_file():
        return None
    child = subprocess.Popen([str(command), 'app-server', '--stdio'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    pending = bytearray()
    deadline = time.monotonic() + 8

    def request(method, params, identifier):
        child.stdin.write((json.dumps({'id': identifier, 'method': method, 'params': params}) + '\n').encode())
        child.stdin.flush()
        while time.monotonic() < deadline:
            while b'\n' in pending:
                line, _, rest = pending.partition(b'\n')
                pending[:] = rest
                message = json.loads(line)
                if message.get('id') == identifier:
                    if 'error' in message:
                        raise ValueError('Quota unavailable')
                    return message['result']
            if not selector.select(0.2):
                continue
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk or len(pending) + len(chunk) > 1_000_000:
                break
            pending.extend(chunk)
        raise ValueError('Quota timeout')

    try:
        request('initialize', {'clientInfo': {'name': 'quiet-island-usage', 'version': '0.2.5'}}, 1)
        child.stdin.write(b'{"method":"initialized"}\n')
        child.stdin.flush()
        if session_ids is None:
            return quota_summary(request('account/rateLimits/read', {}, 2))
        result = {}
        for number, session_id in enumerate(session_ids[:64], 2):
            try:
                thread = request('thread/read', {'threadId': session_id, 'includeTurns': False}, number)['thread']
                value = codex_context_file(thread.get('path'))
                if value:
                    result[session_id] = value
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None
    finally:
        selector.close()
        child.terminate()
        try:
            child.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def context_summary(remaining, now=None):
    if type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 <= remaining <= 100:
        return None
    return {'kind': 'context', 'remaining': remaining,
            'updated_at': time.time() if now is None else now}


def claude_context_summary(payload, now=None):
    window = payload.get('context_window') if isinstance(payload, dict) else None
    return context_summary(window.get('remaining_percentage'), now) if isinstance(window, dict) else None


def codex_context_file(filename, root=None):
    # Read only bounded token_count telemetry from the thread's own rollout.
    # No message content, prompt or tool output is parsed or retained.
    if not isinstance(filename, str):
        return None
    root = (Path.home() / '.codex/sessions' if root is None else root).resolve()
    path = Path(filename).resolve()
    if not path.is_relative_to(root) or path.suffix != '.jsonl':
        return None
    with path.open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        start = max(0, size - 2_000_000)
        stream.seek(start)
        if start:
            stream.readline()
        lines = stream.read(2_000_000).splitlines()
    for line in reversed(lines):
        if b'"token_count"' not in line or len(line) > 65536:
            continue
        try:
            event = json.loads(line)
            payload = event.get('payload', {})
            if event.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                continue
            info = payload.get('info')
            if not isinstance(info, dict):
                continue
            window = info.get('model_context_window')
            last = info.get('last_token_usage')
            used = last.get('total_tokens') if isinstance(last, dict) else None
            if (type(window) is not int or window <= 0 or type(used) is not int or used < 0):
                return None
            return context_summary(max(0, 100 * (1 - used / window)))
        except (ValueError, TypeError, AttributeError):
            continue
    return None
