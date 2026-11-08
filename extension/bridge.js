// SPDX-License-Identifier: MIT
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const NAME = 'org.quietisland.Bridge';
const PATH = '/org/quietisland/Bridge';
const IFACE = 'org.quietisland.Bridge1';

export class BridgeClient {
    constructor(onSnapshot, onLevels) {
        this._onSnapshot = onSnapshot;
        this._onLevels = onLevels;
        this._cancellable = new Gio.Cancellable();
        this._generation = 0;
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, NAME,
            Gio.BusNameWatcherFlags.NONE,
            (connection, name, owner) => this._appeared(connection, owner),
            () => {
                this._owner = null;
                this._generation++;
                this._onSnapshot({connected: false, sessions: [], audio: 'disconnected'});
                this._onLevels([]);
            });
    }

    _appeared(connection, owner) {
        this._owner = owner;
        const generation = ++this._generation;
        if (this._signal)
            connection.signal_unsubscribe(this._signal);
        this._connection = connection;
        this._signal = connection.signal_subscribe(owner, IFACE, null, PATH, null,
            Gio.DBusSignalFlags.NONE, (_bus, _sender, _path, _iface, signal, params) => {
                if (generation !== this._generation)
                    return;
                try {
                    if (signal === 'SnapshotChanged')
                        this._onSnapshot({...JSON.parse(params.deep_unpack()[0]), connected: true});
                    else if (signal === 'LevelsChanged')
                        this._onLevels(params.deep_unpack()[0]);
                } catch (error) {
                    console.warn(`Quiet Island: invalid bridge signal: ${error.message}`);
                }
            });
        connection.call(owner, PATH, IFACE, 'GetSnapshot', null, null,
            Gio.DBusCallFlags.NONE, 3000, this._cancellable, (bus, result) => {
                try {
                    const [json] = bus.call_finish(result).deep_unpack();
                    if (generation === this._generation)
                        this._onSnapshot({...JSON.parse(json), connected: true});
                } catch (error) {
                    if (!this._cancellable.is_cancelled())
                        console.warn(`Quiet Island: ${error.message}`);
                }
            });
    }

    destroy() {
        this._generation++;
        this._cancellable.cancel();
        Gio.bus_unwatch_name(this._watch);
        if (this._signal)
            this._connection.signal_unsubscribe(this._signal);
    }

    getArtwork(playerName, uri, cancellable, callback) {
        Gio.DBus.session.call(NAME, PATH, IFACE, 'GetArtwork',
            new GLib.Variant('(ss)', [playerName, uri]), null,
            Gio.DBusCallFlags.NONE, 6000, cancellable, (bus, result) => {
                try {
                    const [data] = bus.call_finish(result).deep_unpack();
                    if (!cancellable.is_cancelled() && data.length <= 2_000_000)
                        callback(GLib.Bytes.new(data));
                } catch (_) {
                    // Keep the normal cover fallback; no permissions are changed.
                }
            });
    }
}
