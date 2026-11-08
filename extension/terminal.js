// SPDX-License-Identifier: MIT
import GLib from 'gi://GLib';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export function focusTerminal(target, timestamp = global.get_current_time()) {
    if (target?.kind !== 'ptyxis-window' || !Number.isInteger(target.pid) ||
        target.pid <= 1 || !/^[0-9]{1,20}$/.test(target.start ?? ''))
        throw new Error('Esta sessão ainda não identificou seu terminal.');
    // Do not activate a new process that reused the saved PID.
    try {
        const [ok, bytes] = GLib.file_get_contents(`/proc/${target.pid}/stat`);
        const stat = new TextDecoder().decode(bytes);
        const name = stat.slice(stat.indexOf('(') + 1, stat.lastIndexOf(')'));
        const fields = stat.slice(stat.lastIndexOf(')') + 2).trim().split(/\s+/);
        if (!ok || name !== 'ptyxis' || fields[19] !== target.start)
            throw new Error();
    } catch (_) {
        throw new Error('O terminal desta sessão já foi fechado.');
    }
    const windows = global.get_window_actors().map(actor => actor.meta_window)
        .filter(window => window.get_pid() === target.pid && !window.is_override_redirect());
    if (windows.length === 0)
        throw new Error('A janela do terminal desta sessão não foi encontrada.');
    if (windows.length > 1)
        throw new Error('Há várias janelas do Ptyxis. Esta versão não identifica a janela de cada aba.');
    Main.activateWindow(windows[0], timestamp);
}
