// SPDX-License-Identifier: MIT
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {selectPlayer} from './model.js';

const PATH = '/org/mpris/MediaPlayer2';
const PLAYER = 'org.mpris.MediaPlayer2.Player';
const ROOT = 'org.mpris.MediaPlayer2';
const PREFIX = 'org.mpris.MediaPlayer2.';

function unpack(value) {
    return value instanceof GLib.Variant ? unpack(value.deep_unpack()) : value;
}

export class MprisManager {
    constructor(onChanged) {
        this._changed = onChanged;
        this._players = new Map();
        this._cancellable = new Gio.Cancellable();
        this._selected = null;
        // Some browsers omit Position signals and update metadata in several steps.
        this._refreshTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 5, () => {
            this.refresh();
            return GLib.SOURCE_CONTINUE;
        });
        this._subscription = Gio.DBus.session.signal_subscribe('org.freedesktop.DBus',
            'org.freedesktop.DBus', 'NameOwnerChanged', '/org/freedesktop/DBus', null,
            Gio.DBusSignalFlags.NONE, (_bus, _sender, _path, _iface, _signal, params) => {
                const [name, oldOwner, newOwner] = params.deep_unpack();
                if (!name.startsWith(PREFIX))
                    return;
                if (oldOwner)
                    this._remove(name);
                if (newOwner)
                    this._add(name);
            });
        Gio.DBus.session.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'ListNames', null, null, Gio.DBusCallFlags.NONE,
            3000, this._cancellable, (bus, result) => {
                try {
                    const [names] = bus.call_finish(result).deep_unpack();
                    for (const name of names.filter(name => name.startsWith(PREFIX)))
                        this._add(name);
                } catch (error) {
                    this._log(error);
                }
            });
    }

    _add(name) {
        if (this._players.has(name) || this._cancellable.is_cancelled())
            return;
        const player = {name, ready: false, changedAt: 0, signals: []};
        this._players.set(name, player);
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.GET_INVALIDATED_PROPERTIES,
            null, name, PATH, PLAYER, this._cancellable, (_source, result) => {
                try {
                    const proxy = Gio.DBusProxy.new_for_bus_finish(result);
                    if (this._players.get(name) !== player || this._cancellable.is_cancelled())
                        return;
                    player.proxy = proxy;
                    player.ready = true;
                    player.signals.push(proxy.connect('g-properties-changed', (_proxy, changed) => {
                        player.revision = (player.revision ?? 0) + 1;
                        this._update(player, changed.deep_unpack());
                    }));
                    player.signals.push(proxy.connect('g-signal', (_p, _sender, signal, parameters) => {
                        if (signal === 'Seeked') {
                            player.position = parameters.deep_unpack()[0];
                            player.positionAt = GLib.get_monotonic_time();
                            this._changed();
                        }
                    }));
                    this._update(player);
                } catch (error) {
                    this._log(error);
                }
            });
    }

    _update(player, changed = {}) {
        const previousPosition = this.position(player);
        const prop = name => unpack(player.proxy.get_cached_property(name));
        const metadata = prop('Metadata') ?? {};
        const get = key => unpack(metadata[key]);
        const status = prop('PlaybackStatus') ?? 'Stopped';
        const trackId = get('mpris:trackid') ?? '';
        const trackKey = JSON.stringify([trackId, get('xesam:title'), get('xesam:artist')]);
        const initial = player.trackKey === undefined;
        const newTrack = player.trackKey !== trackKey;
        if (player.status !== status || newTrack)
            player.changedAt = GLib.get_monotonic_time();
        player.status = status;
        player.trackId = trackId;
        player.trackKey = trackKey;
        player.title = get('xesam:title') || 'Sem título';
        player.artist = (get('xesam:artist') ?? []).join(', ');
        player.cover = get('mpris:artUrl') ?? '';
        player.length = Math.max(0, Number(get('mpris:length')) || 0);
        player.rate = Number(prop('Rate')) || 1;
        for (const name of ['CanPlay', 'CanPause', 'CanGoNext', 'CanGoPrevious', 'CanSeek', 'CanControl'])
            player[name] = prop(name) === true;
        const position = prop('Position');
        player.position = initial || 'Position' in changed
            ? Number(position) || 0 : newTrack ? 0 : previousPosition;
        player.positionAt = GLib.get_monotonic_time();
        this._changed();
        if (newTrack && !initial)
            this.refreshPosition();
    }

    get current() {
        const player = selectPlayer([...this._players.values()], this._selected);
        this._selected = player?.name ?? null;
        return player;
    }

    position(player = this.current) {
        if (!player)
            return 0;
        const elapsed = player.status === 'Playing'
            ? (GLib.get_monotonic_time() - (player.positionAt ?? GLib.get_monotonic_time())) * player.rate : 0;
        return Math.min(player.length || Infinity, Math.max(0, (player.position ?? 0) + elapsed));
    }

    refreshPosition() {
        const player = this.current;
        if (!player)
            return;
        const trackKey = player.trackKey;
        Gio.DBus.session.call(player.name, PATH, 'org.freedesktop.DBus.Properties',
            'Get', new GLib.Variant('(ss)', [PLAYER, 'Position']), null,
            Gio.DBusCallFlags.NONE, 2000, this._cancellable, (bus, result) => {
                try {
                    const [value] = bus.call_finish(result).deep_unpack();
                    if (this._players.get(player.name) !== player || player.trackKey !== trackKey)
                        return;
                    player.position = Number(unpack(value));
                    player.positionAt = GLib.get_monotonic_time();
                    this._changed();
                } catch (error) {
                    this._log(error);
                }
            });
    }

    refresh() {
        for (const player of this._players.values()) {
            if (!player.ready || player.refreshing)
                continue;
            player.refreshing = true;
            const revision = player.revision ?? 0;
            Gio.DBus.session.call(player.name, PATH, 'org.freedesktop.DBus.Properties',
                'GetAll', new GLib.Variant('(s)', [PLAYER]), null,
                Gio.DBusCallFlags.NONE, 2000, this._cancellable, (bus, result) => {
                    player.refreshing = false;
                    try {
                        const [properties] = bus.call_finish(result).deep_unpack();
                        if (this._players.get(player.name) !== player ||
                            (player.revision ?? 0) !== revision)
                            return;
                        for (const [name, value] of Object.entries(properties))
                            player.proxy.set_cached_property(name, value);
                        this._update(player, properties);
                    } catch (error) {
                        this._log(error);
                    }
                });
        }
    }

    command(method) {
        const player = this.current;
        if (!player?.CanControl)
            return;
        const capable = method === 'Next' ? player.CanGoNext
            : method === 'Previous' ? player.CanGoPrevious
                : player.status === 'Playing' ? player.CanPause : player.CanPlay;
        if (capable)
            this._call(player, method === 'PlayPause'
                ? player.status === 'Playing' ? 'Pause' : 'Play' : method);
    }

    seek(fraction) {
        const player = this.current;
        if (player?.CanControl && player.CanSeek && player.length > 0 &&
            GLib.Variant.is_object_path(player.trackId) &&
            player.trackId !== '/org/mpris/MediaPlayer2/TrackList/NoTrack') {
            const position = Math.round(Math.max(0, Math.min(1, fraction)) * player.length);
            this._call(player, 'SetPosition', new GLib.Variant('(ox)', [player.trackId, position]));
        }
    }

    _call(player, method, params = null) {
        player.proxy.call(method, params, Gio.DBusCallFlags.NONE, 3000,
            this._cancellable, (proxy, result) => {
                try {
                    proxy.call_finish(result);
                    this.refreshPosition();
                } catch (error) {
                    this._log(error);
                }
            });
    }

    _remove(name) {
        const player = this._players.get(name);
        if (!player)
            return;
        for (const signal of player.signals)
            player.proxy.disconnect(signal);
        this._players.delete(name);
        this._changed();
    }

    _log(error) {
        if (!this._cancellable.is_cancelled())
            console.warn(`Quiet Island MPRIS: ${error.message}`);
    }

    destroy() {
        this._cancellable.cancel();
        GLib.source_remove(this._refreshTimer);
        Gio.DBus.session.signal_unsubscribe(this._subscription);
        for (const player of this._players.values()) {
            for (const signal of player.signals)
                player.proxy.disconnect(signal);
        }
        this._players.clear();
    }
}
