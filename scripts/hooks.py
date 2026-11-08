#!/usr/bin/env python3
"""Generate opt-in hook snippets. Never edits Codex, Claude or Hermes settings."""
import argparse
import json
import shlex
from pathlib import Path
from codex_hooks import EVENTS_BY_TOOL


def generate(command, output):
    output.mkdir(parents=True, exist_ok=True)
    for tool in ("codex", "claude"):
        events = EVENTS_BY_TOOL[tool]
        hooks = {event: [{"hooks": [{
            "type": "command", "command": shlex.quote(command) +
            " hook --tool " + tool + " --event " + event, "timeout": 3,
        }]}] for event in events}
        (output / f"{tool}.json").write_text(json.dumps({"hooks": hooks}, indent=2) + "\n")
    events = ["on_session_start", "pre_llm_call", "pre_tool_call", "post_tool_call",
              "on_session_end", "on_session_finalize", "on_session_reset"]
    yaml = "# Merge into your Hermes profile after reviewing these observer commands.\nhooks:\n"
    for event in events:
        # JSON string quoting is a valid YAML scalar and preserves spaces in paths.
        hook_command = shlex.quote(command) + " hook --tool hermes --event " + event
        yaml += f"  {event}:\n    - command: {json.dumps(hook_command)}\n      timeout: 3\n"
    (output / "hermes.yaml").write_text(yaml)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default=str(Path.home() / ".local/bin/quiet-island"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    generate(args.command, args.output)
