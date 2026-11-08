import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('claude_statusline', ROOT / 'scripts/claude_statusline.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_status_line_preserves_theme_and_hooks_and_is_idempotent(tmp_path):
    path = tmp_path / 'settings.json'
    existing = {'theme': 'dark', 'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'mine'}]}]}}
    path.write_text(json.dumps(existing))
    changed, backup = module.configure(path, '/example/quiet-island')
    assert changed and json.loads(backup.read_text()) == existing
    result = json.loads(path.read_text())
    assert result['theme'] == existing['theme'] and result['hooks'] == existing['hooks']
    assert result['statusLine']['command'] == '/example/quiet-island claude-statusline'
    assert module.configure(path, '/example/quiet-island') == (False, None)


def test_custom_status_line_is_never_replaced(tmp_path):
    path = tmp_path / 'settings.json'
    existing = {'statusLine': {'type': 'command', 'command': 'personal-statusline'}}
    path.write_text(json.dumps(existing))
    with pytest.raises(ValueError):
        module.configure(path, '/example/quiet-island')
    assert json.loads(path.read_text()) == existing
