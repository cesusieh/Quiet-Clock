import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from quiet_island.model import SessionStore, hook_event, normalize_event


def test_expiration_does_not_invent_task_completion():
    now = [1000]
    store = SessionStore(stale_after=20, clock=lambda: now[0])
    store.publish({"tool": "codex", "id": "a", "status": "thinking"})
    now[0] += 21
    session = store.snapshot()[0]
    assert session["status"] == "unknown"
    assert session["connected"] is False
    store.publish({"tool": "codex", "id": "a", "status": "done"})
    assert store.snapshot()[0]["connected"] is True
    store.publish({"tool": "codex", "id": "a", "status": "disconnected"})
    assert store.snapshot() == []


def test_out_of_order_events_do_not_overwrite_newer_state():
    store = SessionStore(clock=lambda: 1000)
    store.publish({"tool": "claude", "id": "a", "status": "done", "timestamp": 990})
    assert store.publish({"tool": "claude", "id": "a", "status": "thinking", "timestamp": 980}) is False
    assert store.snapshot()[0]["status"] == "done"


@pytest.mark.parametrize("tool", ["codex", "claude"])
def test_hook_payload_content_and_secrets_are_discarded(tool):
    raw = {"hook_event_name": "PreToolUse", "session_id": "abc", "tool_name": "Read",
           "cwd": "/home/user/my-project", "prompt": "SECRET", "tool_input": {"password": "SECRET"},
           "transcript_path": "/home/user/secret.jsonl", "tool_response": "SECRET"}
    event = hook_event(tool, "", raw)
    assert event == {"tool": tool, "id": "abc", "status": "reading", "project": "my-project"}
    assert "SECRET" not in str(normalize_event(event, now=1000))


def test_idle_notification_is_not_a_request_for_user_action():
    raw = {"session_id": "a", "notification_type": "idle_prompt"}
    assert hook_event("claude", "Notification", raw) is None
    raw["notification_type"] = "permission_prompt"
    assert hook_event("claude", "Notification", raw)["status"] == "waiting"


def test_codex_subagent_events_do_not_overwrite_the_parent_session():
    payload = {"session_id": "parent", "agent_id": "child", "tool_name": "Read"}
    assert hook_event("codex", "PreToolUse", payload) is None
    assert hook_event("codex", "Stop", payload) is None
    payload.pop("agent_id")
    assert hook_event("codex", "Stop", payload)["status"] == "done"


def test_codex_user_input_tool_is_waiting_without_reading_arguments():
    payload = {"session_id": "a", "tool_name": "functions.request_user_input",
               "tool_input": {"secret": "DO-NOT-RETAIN"}}
    event = hook_event("codex", "PreToolUse", payload)
    assert event["status"] == "waiting"
    assert "DO-NOT-RETAIN" not in str(event)


def test_claude_failure_waiting_and_subagents_are_distinct():
    payload = {'session_id': 'claude-session', 'tool_name': 'AskUserQuestion'}
    assert hook_event('claude', 'PreToolUse', payload)['status'] == 'waiting'
    assert hook_event('claude', 'Elicitation', payload)['status'] == 'waiting'
    assert hook_event('claude', 'ElicitationResult', payload)['status'] == 'thinking'
    assert hook_event('claude', 'StopFailure', payload)['status'] == 'error'
    assert hook_event('claude', 'PostToolUseFailure', {**payload, 'is_interrupt': True})['status'] == 'idle'
    assert hook_event('claude', 'Stop', {**payload, 'agent_id': 'child'}) is None


def test_hermes_turn_end_and_session_finalize_have_distinct_meanings():
    raw = {"session_id": "a", "cwd": "/example/project"}
    assert hook_event("hermes", "on_session_end", raw)["status"] == "done"
    assert hook_event("hermes", "on_session_finalize", raw)["status"] == "disconnected"
    assert hook_event("hermes", "pre_llm_call", raw)["status"] == "thinking"


def test_ids_are_scoped_by_tool_and_unknown_hook_events_are_ignored():
    store = SessionStore(clock=lambda: 1000)
    for tool in ["codex", "claude", "hermes"]:
        store.publish({"tool": tool, "id": "same-id", "status": "idle"})
    assert len(store.snapshot()) == 3
    assert hook_event("codex", "not-a-real-hook", {"session_id": "a"}) is None
    assert hook_event("claude", "Stop", {}) is None


@pytest.mark.parametrize("raw", [None, [], {}, {"tool": "bad", "id": "x", "status": "done"},
                                 {"tool": "codex", "id": "x", "status": "done", "timestamp": float("nan")}])
def test_invalid_events_are_rejected(raw):
    with pytest.raises(ValueError):
        normalize_event(raw, now=1000)


@pytest.mark.parametrize('tool', ['codex', 'claude'])
def test_task_clock_counts_prompt_to_stop_and_freezes_across_state_changes(tool):
    now = [1000]
    store = SessionStore(clock=lambda: now[0])
    def send(event, **extra):
        return store.publish(hook_event(tool, event, {'session_id': 'session', 'turn_id': 'turn-a', **extra}))
    send('UserPromptSubmit')
    now[0] = 1005
    send('PreToolUse', tool_name='Read')
    assert store.snapshot()[0]['task_started_at'] == 1000
    now[0] = 1008
    send('PermissionRequest')
    assert store.snapshot()[0]['task_finished_at'] is None
    now[0] = 1010
    send('PostToolUse')
    now[0] = 1016
    send('Stop')
    task = store.snapshot()[0]
    assert task['task_finished_at'] - task['task_started_at'] == 16
    now[0] = 1060
    send('Stop')
    assert store.snapshot()[0]['task_finished_at'] == 1016
    now[0] = 1100
    send('UserPromptSubmit', turn_id='turn-b')
    task = store.snapshot()[0]
    assert task['task_started_at'] == 1100 and task['task_finished_at'] is None
    assert send('Stop', turn_id='turn-a') is False
    assert store.snapshot()[0]['status'] == 'thinking'


def test_repeated_prompt_for_same_turn_does_not_reset_or_restart_a_task():
    now = [1000]
    store = SessionStore(clock=lambda: now[0])
    event = {'tool': 'codex', 'id': 'a', 'status': 'thinking', 'task_action': 'start', 'task_id': 'turn'}
    store.publish(event)
    now[0] += 5
    assert store.publish(event) is False
    now[0] += 5
    store.publish({**event, 'status': 'done', 'task_action': 'finish'})
    now[0] += 20
    assert store.publish(event) is False
    assert store.snapshot()[0]['status'] == 'done'
    assert store.snapshot()[0]['task_finished_at'] == 1010


@pytest.mark.parametrize('tool,event,payload', [
    ('codex', 'Interrupt', {}), ('claude', 'StopFailure', {}),
    ('claude', 'PostToolUseFailure', {'is_interrupt': True})])
def test_task_clock_freezes_on_cancellation_or_terminal_failure(tool, event, payload):
    now = [1000]
    store = SessionStore(clock=lambda: now[0])
    store.publish(hook_event(tool, 'UserPromptSubmit', {'session_id': 'a'}))
    now[0] = 1007
    store.publish(hook_event(tool, event, {'session_id': 'a', **payload}))
    assert store.snapshot()[0]['task_finished_at'] == 1007


def test_task_duration_is_not_invented_for_sessions_observed_after_completion():
    store = SessionStore(clock=lambda: 1000)
    store.publish({'tool': 'codex', 'id': 'a', 'status': 'done'})
    session = store.snapshot()[0]
    assert session['task_started_at'] is None and session['task_finished_at'] is None


@pytest.mark.parametrize('failure', [{}, {'is_interrupt': False}])
def test_claude_tool_failure_keeps_task_running_until_stop(failure):
    store = SessionStore(clock=lambda: 1100)
    payload = {'session_id': 'claude-recovery', 'tool_name': 'Bash',
               'error': 'PRIVATE TOOL ERROR', 'tool_input': {'command': 'PRIVATE COMMAND'}}
    for name, timestamp in [('UserPromptSubmit', 1000), ('PreToolUse', 1002),
                            ('PostToolUseFailure', 1004)]:
        event = hook_event('claude', name, {**payload, **failure})
        assert 'PRIVATE' not in str(event)
        store.publish({**event, 'timestamp': timestamp})
    session = store.snapshot()[0]
    assert session['status'] == 'thinking'
    assert session['task_started_at'] == 1000
    assert session['task_finished_at'] is None
    store.publish({**hook_event('claude', 'Stop', payload), 'timestamp': 1016})
    session = store.snapshot()[0]
    assert session['status'] == 'done'
    assert session['task_finished_at'] - session['task_started_at'] == 16
