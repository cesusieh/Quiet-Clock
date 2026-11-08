import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('codex_hooks', ROOT / 'scripts/codex_hooks.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_install_preserves_other_hooks_and_is_idempotent(tmp_path):
    path = tmp_path / 'hooks.json'
    original = {'description': 'Personal hooks', 'hooks': {
        'Stop': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': 'my-observer'}]}]}}
    path.write_text(json.dumps(original))
    changed, backup = module.install(path, '/path with spaces/quiet-island')
    assert changed
    assert json.loads(backup.read_text()) == original
    installed = json.loads(path.read_text())
    assert installed['description'] == original['description']
    assert installed['hooks']['Stop'][0] == original['hooks']['Stop'][0]
    assert len(installed['hooks']['Stop']) == 2
    assert module.install(path, '/path with spaces/quiet-island') == (False, None)


def test_invalid_configuration_is_never_overwritten(tmp_path):
    path = tmp_path / 'hooks.json'
    path.write_text('{broken')
    try:
        module.install(path, '/example/quiet-island')
    except json.JSONDecodeError:
        pass
    else:
        raise AssertionError('Malformed configuration must be rejected')
    assert path.read_text() == '{broken'


def test_claude_preserves_settings_and_existing_hooks(tmp_path):
    path = tmp_path / 'settings.json'
    existing = {'theme': 'dark', 'permissions': {'allow': ['Read']},
                'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'personal-observer'}]}]}}
    path.write_text(json.dumps(existing))
    changed, backup = module.install(path, '/example/quiet-island', tool='claude')
    assert changed and json.loads(backup.read_text()) == existing
    result = json.loads(path.read_text())
    assert result['theme'] == existing['theme']
    assert result['permissions'] == existing['permissions']
    assert result['hooks']['Stop'][0] == existing['hooks']['Stop'][0]
    assert len(result['hooks']) == 12
    assert module.install(path, '/example/quiet-island', tool='claude') == (False, None)
