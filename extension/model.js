// SPDX-License-Identifier: MIT
// Pure functions shared with the tests: no Shell or GI imports.
export const STATES = Object.freeze({
    idle: 'Ocioso', thinking: 'Pensando', reading: 'Lendo arquivos',
    editing: 'Editando código', testing: 'Rodando testes', running: 'Executando',
    waiting: 'Aguardando você', done: 'Concluído', error: 'Erro', unknown: 'Sem sinal',
});

export function selectPlayer(players, currentName = null) {
    const available = players.filter(player => player.ready);
    const current = available.find(player => player.name === currentName);
    // Keep the current source while it plays; otherwise prefer the most recently active one.
    if (current?.status === 'Playing')
        return current;
    const playing = available.filter(player => player.status === 'Playing');
    return playing.sort((a, b) => b.changedAt - a.changedAt)[0] ??
        current ?? available.sort((a, b) => b.changedAt - a.changedAt)[0] ?? null;
}

export function formatTime(microseconds) {
    const seconds = Math.max(0, Math.floor(Number(microseconds) / 1_000_000) || 0);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

export function elapsed(timestamp, now = Date.now() / 1000) {
    const seconds = Math.max(0, Math.floor(now - timestamp));
    return seconds < 60 ? `${seconds} s`
        : `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, '0')} s`;
}

export function taskElapsed(session, now = Date.now() / 1000) {
    if (!Number.isFinite(session.task_started_at))
        return '—';
    if (!Number.isFinite(session.task_finished_at) && session.connected === false)
        return '—';
    const end = Number.isFinite(session.task_finished_at) ? session.task_finished_at
        : now;
    return elapsed(session.task_started_at, end);
}

export function sessionSummary(sessions) {
    const connected = sessions.filter(session => session.connected);
    const display = connected.find(session => session.status === 'waiting') ??
        connected.find(session => !['idle', 'done', 'error'].includes(session.status)) ??
        connected[0] ?? sessions[0] ?? null;
    return {count: connected.length, display};
}

export function stripAllocation(width, clockWidth, leftWidth, rightWidth) {
    const center = Math.min(clockWidth, width);
    const side = Math.max(0, (width - center) / 2);
    return {
        left: [Math.max(0, side - leftWidth), side],
        clock: [side, side + center],
        right: [side + center, Math.min(width, side + center + rightWidth)],
    };
}
