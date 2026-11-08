"""Validate a small event schema; never retain arbitrary hook payloads."""

import math
import re
import time
from pathlib import Path

TOOLS = {"codex", "claude", "hermes"}
STATES = {
    "idle", "thinking", "reading", "editing", "testing", "running",
    "waiting", "done", "error", "unknown", "disconnected",
}
MAX_SESSIONS = 128


def terminal_target(value):
    if not isinstance(value, dict) or value.get('kind') != 'ptyxis-window':
        return None
    pid = value.get('pid')
    start = value.get('start')
    if (type(pid) is not int or not 1 < pid <= 4_194_304 or
            not isinstance(start, str) or not re.fullmatch(r'[0-9]{1,20}', start)):
        return None
    return {'kind': 'ptyxis-window', 'pid': pid, 'start': start}


def clean_text(value, maximum):
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c.isprintable())[:maximum]


def normalize_event(raw, now=None):
    if not isinstance(raw, dict):
        raise ValueError("Event must be an object")
    tool = raw.get("tool")
    status = raw.get("status")
    session_id = clean_text(raw.get("id"), 128)
    if (not isinstance(tool, str) or not isinstance(status, str)
            or tool not in TOOLS or status not in STATES or not session_id
            or session_id != raw.get("id")):
        raise ValueError("Invalid tool, status or session id")
    now = time.time() if now is None else now
    timestamp = raw.get("timestamp", now)
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp) or timestamp > now + 30):
        raise ValueError("Invalid event timestamp")
    result = {
        "tool": tool,
        "id": session_id,
        "status": status,
        "project": clean_text(raw.get("project"), 80),
        "timestamp": timestamp,
    }
    target = terminal_target(raw.get('terminal'))
    if target:
        result['terminal'] = target
    action = raw.get('task_action')
    if action is not None:
        if not isinstance(action, str) or action not in {'start', 'finish', 'cancel'}:
            raise ValueError('Invalid task action')
        result['task_action'] = action
    task_id = raw.get('task_id')
    if task_id is not None:
        if not isinstance(task_id, str) or not task_id or clean_text(task_id, 128) != task_id:
            raise ValueError('Invalid task identifier')
        result['task_id'] = task_id
    return result


def tool_state(tool_name):
    name = str(tool_name or "").lower()
    if any(part in name for part in ["edit", "write", "patch", "replace"]):
        return "editing"
    if any(part in name for part in ["read", "grep", "glob", "search", "list", "view"]):
        return "reading"
    # A shell command may build, test or do something else. Do not inspect its
    # arguments (which can contain secrets) or pretend every shell call is a test.
    return "running"


def hook_event(tool, event_name, payload):
    """Translate explicit lifecycle signals, discarding content fields."""
    if tool not in TOOLS or not isinstance(payload, dict):
        return None
    # Codex gives subagents the parent's session_id. Their Stop/tool events must
    # not complete or overwrite the root session displayed in the panel.
    if tool in {"codex", "claude"} and payload.get("agent_id"):
        return None
    event_name = event_name or payload.get("hook_event_name", "")
    session_id = payload.get("session_id") or payload.get("thread_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    if tool == "hermes":
        mapping = {
            "on_session_start": "idle", "pre_llm_call": "thinking",
            "pre_tool_call": tool_state(payload.get("tool_name")),
            "post_tool_call": "thinking", "on_session_end": "done",
            "on_session_finalize": "disconnected", "on_session_reset": "disconnected",
        }
    else:
        mapping = {
            "SessionStart": "idle", "UserPromptSubmit": "thinking",
            "PreToolUse": tool_state(payload.get("tool_name")),
            "PostToolUse": "thinking", "PostToolUseFailure": "error",
            "PermissionRequest": "waiting", "Stop": "done",
            "SessionEnd": "disconnected", "agent-turn-complete": "done",
            "PreModelRequest": "thinking", "Interrupt": "idle",
            "StopFailure": "error", "Elicitation": "waiting", "ElicitationResult": "thinking",
        }
        if event_name == "Notification":
            # idle_prompt is a reminder, not evidence of a blocked permission.
            if payload.get("notification_type") not in {"permission_prompt", "elicitation_dialog",
                    "elicitation_url_dialog", "agent_needs_input"}:
                return None
            mapping[event_name] = "waiting"
    status = mapping.get(event_name)
    if tool == 'claude' and event_name == 'PostToolUseFailure':
        # A failed tool returns control to Claude; it does not end the turn.
        status = 'idle' if payload.get('is_interrupt') is True else 'thinking'
    if tool == 'claude' and event_name == 'PreToolUse' and payload.get('tool_name') == 'AskUserQuestion':
        status = 'waiting'
    if (tool == "codex" and event_name == "PreToolUse" and
            str(payload.get("tool_name", "")).rsplit(".", 1)[-1] in
            {"request_user_input", "request_user_input_async"}):
        status = "waiting"
    if status is None:
        return None
    project = payload.get("cwd", "")
    project = Path(project).name if isinstance(project, str) and project else ""
    event = {"tool": tool, "id": session_id, "status": status, "project": project}
    if event_name == 'UserPromptSubmit':
        event['task_action'] = 'start'
    elif event_name in {'Stop', 'agent-turn-complete', 'on_session_end'}:
        event['task_action'] = 'finish'
    elif event_name in {'Interrupt', 'StopFailure'} or (
            tool == 'claude' and event_name == 'PostToolUseFailure' and payload.get('is_interrupt') is True):
        event['task_action'] = 'cancel'
    task_id = payload.get('turn_id') or payload.get('prompt_id')
    if isinstance(task_id, str) and task_id and clean_text(task_id, 128) == task_id:
        event['task_id'] = task_id
    return event


class SessionStore:
    def __init__(self, stale_after=900, clock=time.time):
        self.sessions = {}
        self.stale_after = stale_after
        self.clock = clock

    def publish(self, raw):
        now = self.clock()
        event = normalize_event(raw, now)
        key = event["tool"] + ":" + event["id"]
        previous = self.sessions.get(key)
        if previous and event["timestamp"] < previous["event_at"]:
            return False
        action = event.get('task_action')
        if (previous and action != 'start' and event.get('task_id') and previous.get('task_id') and
                event['task_id'] != previous['task_id']):
            return False
        if (previous and action == 'start' and event.get('task_id') and
                event['task_id'] == previous.get('task_id') and previous.get('task_started_at') is not None):
            return False
        if event["status"] == "disconnected":
            return self.sessions.pop(key, None) is not None
        if previous is None and len(self.sessions) >= MAX_SESSIONS:
            # Remove the oldest stale session first, never a live one silently.
            stale = [item for item in self.sessions.values()
                     if now - item["updated_at"] > self.stale_after]
            if not stale:
                raise ValueError("Session limit reached")
            self.sessions.pop(min(stale, key=lambda item: item["updated_at"])["key"])
        changed = previous is None or previous["status"] != event["status"]
        started = (previous or {}).get('task_started_at')
        finished = (previous or {}).get('task_finished_at')
        task_id = (previous or {}).get('task_id')
        if action == 'start':
            started, finished, task_id = event['timestamp'], None, event.get('task_id')
        elif (action in {'finish', 'cancel'} or event['status'] == 'done') and started is not None and finished is None:
            finished = max(started, event['timestamp'])
        self.sessions[key] = {
            "key": key, "tool": event["tool"], "id": event["id"],
            "status": event["status"],
            "project": event["project"] or (previous or {}).get("project", ""),
            "status_since": now if changed else previous["status_since"],
            "updated_at": now, "event_at": event["timestamp"],
            'task_started_at': started, 'task_finished_at': finished,
        }
        if task_id:
            self.sessions[key]['task_id'] = task_id
        target = event.get('terminal') or (previous or {}).get('terminal')
        if target:
            self.sessions[key]['terminal'] = target
        return True

    def snapshot(self):
        now = self.clock()
        result = []
        for session in self.sessions.values():
            connected = now - session["updated_at"] <= self.stale_after
            result.append({
                **session, "connected": connected,
                "status": session["status"] if connected else "unknown",
            })
        return sorted(result, key=lambda item: -item["updated_at"])
