// SPDX-License-Identifier: MIT
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {selectPlayer, stripAllocation, sessionSummary, formatTime} from '../extension/model.js';

test('clock remains at monitor center with asymmetric, absent, or clipped modules', () => {
    for (const [width, clock, left, right] of [
        [480, 72, 120, 204], [480, 72, 0, 204], [480, 72, 120, 0],
        [160, 72, 400, 400], [40, 72, 200, 10],
    ]) {
        const result = stripAllocation(width, clock, left, right);
        assert.equal((result.clock[0] + result.clock[1]) / 2, width / 2);
        assert.ok(result.left[0] >= 0 && result.right[1] <= width);
    }
});

test('automatic player selection is stable while the selected source plays', () => {
    const music = {name: 'music', ready: true, status: 'Playing', changedAt: 1};
    const browser = {name: 'browser', ready: true, status: 'Playing', changedAt: 2};
    assert.equal(selectPlayer([music, browser], 'music'), music);
    music.status = 'Paused';
    assert.equal(selectPlayer([music, browser], 'music'), browser);
    assert.equal(selectPlayer([], 'music'), null);
    assert.equal(selectPlayer([{...music, ready: false}]), null);
});

test('session count includes idle sessions, excludes stale ones and favors attention', () => {
    const sessions = [
        {tool: 'codex', connected: true, status: 'idle'},
        {tool: 'claude', connected: true, status: 'thinking'},
        {tool: 'hermes', connected: true, status: 'waiting'},
        {tool: 'codex', connected: false, status: 'unknown'},
    ];
    assert.deepEqual(sessionSummary(sessions), {count: 3, display: sessions[2]});
    assert.equal(sessionSummary([sessions[3]]).display.status, 'unknown');
    assert.deepEqual(sessionSummary([]), {count: 0, display: null});
});

test('MPRIS microseconds are displayed as time without fabricated percentages', () => {
    assert.equal(formatTime(134_000_000), '2:14');
    assert.equal(formatTime(-1), '0:00');
    assert.equal(formatTime(undefined), '0:00');
});
