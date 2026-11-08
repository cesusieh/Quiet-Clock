"""Run with dbus-run-session so no test touches the desktop bus."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(ROOT / "bin/quiet-island")]


@pytest.fixture()
def bridge():
    if not os.environ.get("QUIET_ISLAND_PRIVATE_BUS"):
        pytest.skip("Use QUIET_ISLAND_PRIVATE_BUS=1 dbus-run-session -- python3 -m pytest tests/test_dbus.py")
    process = subprocess.Popen(COMMAND + ["service", "--no-audio", "--no-usage"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(40):
            result = subprocess.run(COMMAND + ["snapshot"], capture_output=True, text=True)
            if result.returncode == 0:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Bridge failed to own its name")
        yield
    finally:
        process.terminate()
        process.communicate(timeout=5)


def command(*args, input=None):
    return subprocess.run(COMMAND + list(args), input=input, capture_output=True, text=True, timeout=5)


def snapshot():
    result = command("snapshot")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_real_bus_publish_roundtrip_counts_sessions_not_tasks(bridge):
    assert snapshot()["sessions"] == []
    for tool, state in [("codex", "idle"), ("claude", "thinking"), ("hermes", "waiting")]:
        result = command("event", "--tool", tool, "--session", "same-id", "--status", state,
                         "--project", "Example")
        assert result.returncode == 0, result.stderr
    sessions = snapshot()["sessions"]
    assert len(sessions) == 3
    assert all(session["connected"] for session in sessions)
    assert command("event", "--tool", "codex", "--session", "same-id",
                   "--status", "disconnected").returncode == 0
    assert len(snapshot()["sessions"]) == 2


def test_observer_hook_transmits_only_metadata_and_never_blocks(bridge):
    payload = {"hook_event_name": "PreToolUse", "session_id": "hook-session", "tool_name": "Edit",
               "cwd": "/home/user/my-project", "prompt": "DO-NOT-LEAK",
               "tool_input": {"new_string": "DO-NOT-LEAK"}}
    result = command("hook", "--tool", "claude", input=json.dumps(payload))
    assert result.returncode == 0 and result.stdout == ""
    state = snapshot()
    assert "DO-NOT-LEAK" not in json.dumps(state)
    assert state["sessions"][0]["project"] == "my-project"
    assert state["sessions"][0]["status"] == "editing"
    assert command("hook", "--tool", "claude", input="invalid JSON").returncode == 0


def test_malformed_bus_event_is_rejected_without_losing_service(bridge):
    from gi.repository import Gio, GLib
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    with pytest.raises(GLib.Error):
        connection.call_sync("org.quietisland.Bridge", "/org/quietisland/Bridge",
                             "org.quietisland.Bridge1", "Publish", GLib.Variant("(s)", ("{}",)),
                             None, Gio.DBusCallFlags.NONE, 1000, None)
    assert snapshot()["sessions"] == []


def test_artwork_rejects_arbitrary_files_and_keeps_bridge_alive(bridge):
    from gi.repository import Gio, GLib
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    with pytest.raises(GLib.Error, match='ArtworkUnavailable'):
        connection.call_sync('org.quietisland.Bridge', '/org/quietisland/Bridge',
            'org.quietisland.Bridge1', 'GetArtwork',
            GLib.Variant('(ss)', ('org.mpris.MediaPlayer2.chromium.instance2', 'file:///etc/passwd')),
            None, Gio.DBusCallFlags.NONE, 3000, None)
    assert snapshot()['sessions'] == []


def test_codex_lifecycle_uses_only_metadata_and_cleans_up(bridge):
    launcher = os.environ.get('QUIET_ISLAND_TEST_LAUNCHER')
    observer = [launcher] if launcher else COMMAND
    payload = {'session_id': 'quiet-island-integration-check', 'cwd': '/workspace/Example',
               'prompt': 'DO-NOT-RETAIN', 'tool_input': {'secret': 'DO-NOT-RETAIN'},
               'tool_response': 'DO-NOT-RETAIN', 'transcript_path': '/private/transcript'}
    phases = [('SessionStart', '', 'idle'), ('UserPromptSubmit', '', 'thinking'),
              ('PreModelRequest', '', 'thinking'), ('PreToolUse', 'Read', 'reading'),
              ('PreToolUse', 'apply_patch', 'editing'), ('PreToolUse', 'Bash', 'running'),
              ('PermissionRequest', 'Bash', 'waiting'), ('PostToolUse', 'Bash', 'thinking'),
              ('Interrupt', '', 'idle'), ('Stop', '', 'done'), ('SessionEnd', '', None)]
    for event, tool_name, expected in phases:
        start = time.monotonic()
        result = subprocess.run(observer + ['hook', '--tool', 'codex', '--event', event],
            input=json.dumps({**payload, 'hook_event_name': event, 'tool_name': tool_name}),
            capture_output=True, text=True, timeout=5)
        assert time.monotonic() - start < 3, 'Observer exceeds the Codex hook timeout'
        assert result.returncode == 0 and result.stdout == ''
        state = snapshot()
        assert 'DO-NOT-RETAIN' not in json.dumps(state)
        if expected is None:
            assert state['sessions'] == []
        else:
            assert len(state['sessions']) == 1
            assert state['sessions'][0]['status'] == expected
            assert state['sessions'][0]['project'] == 'Example'


def test_hook_terminal_context_discards_extra_fields(bridge):
    context = {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654', 'command': 'DO-NOT-RETAIN'}
    result = command('hook', '--tool', 'codex', '--event', 'SessionStart',
        '--terminal-context', json.dumps(context), input=json.dumps({'session_id': 'terminal-test'}))
    assert result.returncode == 0 and result.stdout == ''
    state = snapshot()
    assert state['sessions'][0]['terminal'] == {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654'}
    assert 'DO-NOT-RETAIN' not in json.dumps(state)


def test_claude_lifecycle_and_terminal_use_the_installed_observer(bridge):
    launcher = os.environ.get('QUIET_ISLAND_TEST_LAUNCHER')
    observer = [launcher] if launcher else COMMAND
    terminal = {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654'}
    payload = {'session_id': 'claude-integration', 'cwd': '/workspace/ClaudeProject',
        'prompt': 'DO-NOT-RETAIN', 'last_assistant_message': 'DO-NOT-RETAIN',
        'tool_input': {'contents': 'DO-NOT-RETAIN'}}
    phases = [('SessionStart', '', 'idle'), ('UserPromptSubmit', '', 'thinking'),
        ('PreToolUse', 'Read', 'reading'), ('PreToolUse', 'Edit', 'editing'),
        ('PreToolUse', 'Bash', 'running'), ('PermissionRequest', 'Bash', 'waiting'),
        ('PostToolUse', 'Bash', 'thinking'), ('PostToolUseFailure', 'Bash', 'thinking'),
        ('Elicitation', '', 'waiting'), ('ElicitationResult', '', 'thinking'),
        ('StopFailure', '', 'error'), ('Stop', '', 'done'), ('SessionEnd', '', None)]
    for event, tool, expected in phases:
        result = subprocess.run(observer + ['hook', '--tool', 'claude', '--event', event,
            '--terminal-context', json.dumps(terminal)],
            input=json.dumps({**payload, 'hook_event_name': event, 'tool_name': tool}),
            capture_output=True, text=True, timeout=3)
        assert result.returncode == 0 and result.stdout == ''
        state = snapshot()
        assert 'DO-NOT-RETAIN' not in json.dumps(state)
        if expected is None:
            assert state['sessions'] == []
        else:
            session = state['sessions'][0]
            assert session['status'] == expected and session['tool'] == 'claude'
            assert session['terminal'] == terminal and session['project'] == 'ClaudeProject'


def test_claude_statusline_publishes_only_quota_and_keeps_activity_state(bridge):
    assert command('event', '--tool', 'claude', '--session', 'a', '--status', 'done').returncode == 0
    payload = {'rate_limits': {'five_hour': {'used_percentage': 25, 'resets_at': 9999999999}},
        'session_id': 'DO-NOT-RETAIN', 'transcript_path': 'DO-NOT-RETAIN',
        'workspace': {'current_dir': 'DO-NOT-RETAIN'}, 'context_window': {'remaining_percentage': 99}}
    result = command('claude-statusline', input=json.dumps(payload))
    assert result.returncode == 0 and '75%' in result.stdout
    state = snapshot()
    assert state['usage']['claude']['remaining'] == 75
    assert state['usage']['claude']['window_minutes'] == 300
    assert state['sessions'][0]['status'] == 'done'
    assert 'DO-NOT-RETAIN' not in json.dumps(state)


def test_claude_statusline_without_limits_is_explicitly_unknown(bridge):
    result = command('claude-statusline', input=json.dumps({'context_window': {'remaining_percentage': 99}}))
    assert result.returncode == 0 and '—' in result.stdout
    assert snapshot()['usage']['claude']['kind'] == 'unavailable'




def test_claude_context_is_per_session_and_does_not_change_timer_or_state(bridge):
    for identifier in ['first', 'second']:
        assert command('event', '--tool', 'claude', '--session', identifier, '--status', 'thinking').returncode == 0
    before = {s['id']: s for s in snapshot()['sessions']}
    for identifier, remaining in [('first', 82), ('second', 35)]:
        result = command('claude-statusline', input=json.dumps({'session_id':identifier,
            'context_window':{'remaining_percentage':remaining}, 'transcript_path':'PRIVATE',
            'rate_limits':{'five_hour':{'used_percentage':25,'resets_at':9999999999}}}))
        assert result.returncode == 0
    state = snapshot()
    sessions = {s['id']:s for s in state['sessions']}
    assert sessions['first']['context']['remaining'] == 82
    assert sessions['second']['context']['remaining'] == 35
    assert state['usage']['claude']['remaining'] == 75
    for identifier in sessions:
        assert sessions[identifier]['status'] == 'thinking'
        assert sessions[identifier]['updated_at'] == before[identifier]['updated_at']
    assert 'PRIVATE' not in json.dumps(state)
    command('claude-statusline', input=json.dumps({'session_id':'first','context_window':{'remaining_percentage':None}}))
    sessions = {s['id']:s for s in snapshot()['sessions']}
    assert 'context' not in sessions['first'] and sessions['second']['context']['remaining'] == 35
