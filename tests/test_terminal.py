import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
from quiet_island.terminal_context import discover, codex_terminal
from quiet_island.model import SessionStore, terminal_target


def process(proc, pid, parent, name, tty=0, start='100'):
    directory = proc / str(pid)
    directory.mkdir(parents=True)
    fields = ['0'] * 20
    fields[0] = 'S'
    fields[1] = str(parent)
    fields[4] = str(tty)
    fields[19] = start
    (directory / 'stat').write_text(f'{pid} ({name}) ' + ' '.join(fields))
    (directory / 'comm').write_text(name + '\n')


def test_context_follows_ancestors_without_using_titles_or_command_lines(tmp_path):
    process(tmp_path, 40, 30, 'python3')
    process(tmp_path, 30, 20, 'codex', tty=123)
    process(tmp_path, 20, 10, 'ptyxis-agent')
    process(tmp_path, 10, 1, 'ptyxis', start='7654')
    assert discover(40, tmp_path) == {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654'}


def test_no_terminal_is_guessed_for_daemon_or_broken_process_tree(tmp_path):
    process(tmp_path, 30, 20, 'codex')
    process(tmp_path, 20, 10, 'ptyxis-agent')
    process(tmp_path, 10, 1, 'ptyxis')
    assert discover(30, tmp_path) is None
    assert discover(999, tmp_path) is None






def test_only_terminal_identity_fields_are_retained_and_validated():
    target = {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654', 'command': 'PRIVATE'}
    assert terminal_target(target) == {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654'}
    for invalid in [{**target, 'pid': True}, {**target, 'start': '../bad'},
                    {**target, 'kind': 'arbitrary-command'}, None]:
        assert terminal_target(invalid) is None


def test_shared_daemon_does_not_choose_between_different_terminals(tmp_path):
    process(tmp_path, 30, 20, 'codex', tty=123)
    process(tmp_path, 20, 10, 'ptyxis-agent')
    process(tmp_path, 10, 1, 'ptyxis', start='7654')
    process(tmp_path, 40, 1, 'codex')  # Shared daemon is not a terminal client.
    assert codex_terminal(tmp_path) == {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654'}
    process(tmp_path, 80, 70, 'codex', tty=456)
    process(tmp_path, 70, 60, 'ptyxis-agent')
    process(tmp_path, 60, 1, 'ptyxis', start='9999')
    assert codex_terminal(tmp_path) is None




def test_session_preserves_terminal_across_events_and_removes_it_on_end():
    store = SessionStore(clock=lambda: 1000)
    target = {'kind': 'ptyxis-window', 'pid': 10, 'start': '7654'}
    store.publish({'tool': 'codex', 'id': 'a', 'status': 'idle', 'terminal': target})
    store.publish({'tool': 'codex', 'id': 'a', 'status': 'thinking'})
    assert store.snapshot()[0]['terminal'] == target
    store.publish({'tool': 'codex', 'id': 'a', 'status': 'disconnected'})
    assert store.snapshot() == []
