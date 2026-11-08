// Run against fixtures/mpris_player.py in a private D-Bus session.
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import System from 'system';
import {MprisManager} from '../extension/mpris.js';

const loop = new GLib.MainLoop(null, false);
const sleep = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
    resolve();
    return GLib.SOURCE_REMOVE;
}));
function assert(value, message) {
    if (!value)
        throw new Error(message);
}
async function waitFor(predicate, message) {
    for (let i = 0; i < 80; i++) {
        if (predicate())
            return;
        await sleep(25);
    }
    throw new Error(message);
}
const manager = new MprisManager(() => {});
async function run() {
    await waitFor(() => manager.current !== null, 'Player discovery failed');
    assert(manager.current.title === 'Faixa de teste', 'Metadata did not unwrap');
    assert(manager.current.artist === 'Artista de teste', 'Artist metadata did not unwrap');
    assert(manager.current.length === 120_000_000, 'Wrong length');
    assert(manager.current.CanGoPrevious === false, 'Capability not read');
    manager.command('Previous'); // Must not reach the service.
    manager.command('PlayPause');
    await waitFor(() => manager.current.status === 'Paused', 'Pause failed');
    manager.seek(0.5);
    await waitFor(() => manager.position() === 60_000_000, 'SetPosition/Seeked failed');
    manager.command('Next');
    await waitFor(() => manager.current.trackId.endsWith('/2'), 'Next failed');
    assert(manager.position() === 0, 'Track change retained old position');
    manager.command('PlayPause');
    await waitFor(() => manager.current.status === 'Playing', 'Play failed');
    assert(manager.position() >= 0, 'Position invalid');
    const oldTrackId = manager.current.trackId;
    async function setTrack(title, length, notify) {
        await new Promise((resolve, reject) => Gio.DBus.session.call(
            manager.current.name, '/org/mpris/MediaPlayer2', 'org.quietisland.TestPlayer',
            'SetTrack', new GLib.Variant('(sxb)', [title, length, notify]), null,
            Gio.DBusCallFlags.NONE, 2000, null, (bus, result) => {
                try { bus.call_finish(result); resolve(); } catch (error) { reject(error); }
            }));
    }
    await setTrack('New browser video', 240_000_000, true);
    await waitFor(() => manager.current.title === 'New browser video', 'Same-ID track change missed');
    assert(manager.current.trackId === oldTrackId, 'Fixture should retain the browser track ID');
    assert(manager.position() < 2_000_000, 'Same-ID track change retained previous playback time');
    await setTrack('Missed browser signal', 300_000_000, false);
    manager.refresh();
    await waitFor(() => manager.current.title === 'Missed browser signal', 'Full metadata refresh failed');
    assert(manager.current.length === 300_000_000, 'Duration was not refreshed with metadata');
    print('MPRIS: discovery, metadata, capabilities, pause, seek, next, play and position OK');
}
let exitCode = 0;
run().catch(error => {
    printerr(error.stack);
    exitCode = 1;
}).finally(() => {
    manager.destroy();
    loop.quit();
});
loop.run();
System.exit(exitCode);
