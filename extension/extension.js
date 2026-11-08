// SPDX-License-Identifier: MIT
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GdkPixbuf from 'gi://GdkPixbuf';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import Soup from 'gi://Soup?version=3.0';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';

import {BridgeClient} from './bridge.js';
import {ClockMenu} from './clockMenu.js';
import {MprisManager} from './mpris.js';
import {focusTerminal} from './terminal.js';
import {STATES, taskElapsed, formatTime, sessionSummary, stripAllocation} from './model.js';

const TOOL_NAMES = {codex: 'Codex', claude: 'Claude Code', hermes: 'Hermes'};


function label(text, styleClass = '') {
    return new St.Label({text, style_class: styleClass, y_align: Clutter.ActorAlign.CENTER});
}

function infoItem(text, styleClass = '') {
    const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
    item.add_style_class_name('quiet-island-static');
    const textLabel = label(text, styleClass);
    textLabel.clutter_text.line_wrap = true;
    textLabel.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
    item.add_child(textLabel);
    item.quietLabel = textLabel;
    return item;
}

const BalancedStrip = GObject.registerClass(
class QuietIslandStrip extends St.Widget {
    _init(left, clock, right, sideWidth) {
        super._init({style_class: 'quiet-island-strip', y_expand: true});
        this._left = left;
        this._clock = clock;
        this._right = right;
        this.sideWidth = sideWidth;
        this.add_child(left);
        this.add_child(clock);
        this.add_child(right);
    }

    vfunc_get_preferred_width() {
        const [clockMin, clockNat] = this._clock.get_preferred_width(-1);
        const [, left] = this._left.visible ? this._left.get_preferred_width(-1) : [0, 0];
        const [, right] = this._right.visible ? this._right.get_preferred_width(-1) : [0, 0];
        const side = Math.min(this.sideWidth, Math.max(left, right));
        return [clockMin, clockNat + 2 * side];
    }

    vfunc_get_preferred_height() {
        const sizes = [this._left, this._clock, this._right]
            .map(child => child.get_preferred_height(-1));
        return [Math.max(...sizes.map(size => size[0])), Math.max(...sizes.map(size => size[1]))];
    }

    vfunc_allocate(box) {
        this.set_allocation(box);
        const width = box.get_width();
        const height = box.get_height();
        const [, clock] = this._clock.get_preferred_width(-1);
        const [, left] = this._left.visible ? this._left.get_preferred_width(-1) : [0, 0];
        const [, right] = this._right.visible ? this._right.get_preferred_width(-1) : [0, 0];
        const positions = stripAllocation(width, clock, left, right);
        for (const [child, range] of [
            [this._left, positions.left], [this._clock, positions.clock],
            [this._right, positions.right],
        ]) {
            if (!child.visible)
                continue;
            const childBox = new Clutter.ActorBox();
            childBox.x1 = range[0];
            childBox.x2 = range[1];
            childBox.y1 = 0;
            childBox.y2 = height;
            child.allocate(childBox);
        }
    }
});

const AudioMeter = GObject.registerClass(
class QuietIslandAudioMeter extends St.DrawingArea {
    _init() {
        super._init({style_class: 'quiet-island-meter', y_align: Clutter.ActorAlign.CENTER});
        this._levels = [];
        this.connect('repaint', () => {
            const cr = this.get_context();
            const [width, height] = this.get_surface_size();
            const color = this.get_theme_node().get_foreground_color();
            cr.setSourceRGBA(color.red / 255, color.green / 255, color.blue / 255, color.alpha / 255);
            const barWidth = width * 4 / 68;
            const radius = barWidth / 2;
            for (let i = 0; i < 9; i++) {
                const level = Math.max(0, Math.min(1, this._levels[i] ?? 0));
                const bar = height / 3 + level * height * 2 / 3;
                const x = (i + 0.5) * width / 9;
                const top = (height - bar) / 2;
                cr.newSubPath();
                cr.arc(x, top + radius, radius, Math.PI, 2 * Math.PI);
                cr.arc(x, top + bar - radius, radius, 0, Math.PI);
                cr.closePath();
            }
            cr.fill();
            cr.$dispose();
        });
    }

    setLevels(levels) {
        this._levels = levels;
        this.queue_repaint();
    }
});

const ModuleButton = GObject.registerClass(
class QuietIslandModuleButton extends PanelMenu.Button {
    _init(name) {
        super._init(0.5, name);
        this.add_style_class_name('quiet-island-module');
        this.menu.box.add_style_class_name('quiet-island-menu');
        this.box = new St.BoxLayout({style_class: 'quiet-island-module-box'});
        this.add_child(this.box);
    }
});

const UsageRing = GObject.registerClass(
class QuietIslandUsageRing extends St.Widget {
    _init(usage) {
        super._init({layout_manager: new Clutter.BinLayout(),
            style_class: 'quiet-island-usage-ring', y_align: Clutter.ActorAlign.CENTER});
        const remaining = usage?.kind === 'context' && Number.isFinite(usage.remaining)
            ? Math.max(0, Math.min(100, usage.remaining)) : null;
        const drawing = new St.DrawingArea({x_expand: true, y_expand: true});
        this.add_child(drawing);
        const text = label(remaining === null ? '—' : `${Math.round(remaining)}%`, 'quiet-island-usage-value');
        text.x_align = Clutter.ActorAlign.CENTER;
        this.add_child(text);
        this.accessible_name = remaining === null ? 'Contexto disponível: sem dados'
            : `Contexto disponível nesta sessão: ${Math.round(remaining)} por cento`;
        drawing.connect('repaint', () => {
            const cr = drawing.get_context();
            const [width, height] = drawing.get_surface_size();
            const color = this.get_theme_node().get_foreground_color();
            const radius = Math.max(0, (Math.min(width, height) - 4) / 2);
            cr.setLineWidth(3);
            cr.setSourceRGBA(1, 1, 1, 0.16);
            cr.arc(width / 2, height / 2, radius, 0, Math.PI * 2);
            cr.stroke();
            if (remaining !== null && remaining > 0) {
                cr.setSourceRGBA(color.red / 255, color.green / 255, color.blue / 255, 1);
                cr.arc(width / 2, height / 2, radius, -Math.PI / 2,
                    -Math.PI / 2 + remaining / 100 * Math.PI * 2);
                cr.stroke();
            }
            cr.$dispose();
        });
    }
});

export default class QuietIsland extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._signals = [];
        this._lastSessions = new Map();
        this._snapshot = {connected: false, sessions: [], audio: 'disconnected'};
        this._levels = [];
        this._coverSession = new Soup.Session({timeout: 8});
        try {
            this._build();
            this._mpris = new MprisManager(() => this._updateMusic());
            this._bridge = new BridgeClient(snapshot => this._onSnapshot(snapshot), levels => {
                this._levels = levels;
                this._updateMeter();
            });
            this._signals.push([this._settings, this._settings.connect('changed', () => {
                this._strip.sideWidth = this._settings.get_int('side-width');
                this._strip.queue_relayout();
                this._musicSignature = null;
                this._updateMusic();
                this._updateAI();
            })]);
            for (const signal of ['monitors-changed', 'workareas-changed']) {
                const object = signal === 'monitors-changed' ? Main.layoutManager : global.display;
                this._signals.push([object, object.connect(signal, () => this._alignToMonitor())]);
            }
            this._tick = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                this._updateClock();
                this._updateProgress();
                this._updateAITimes();
                return GLib.SOURCE_CONTINUE;
            });
            this._updateClock();
            this._updateMusic();
            this._updateAI();
            this._alignToMonitor();
        } catch (error) {
            this.disable();
            throw error;
        }
    }

    _build() {
        this._dateMenu = Main.panel.statusArea.dateMenu;
        if (!this._dateMenu?.container)
            throw new Error('Quiet Island precisa do relógio nativo do GNOME 50.');
        this._dateParent = this._dateMenu.container.get_parent();
        this._dateIndex = this._dateParent.get_children().indexOf(this._dateMenu.container);
        this._originalClock = this._dateMenu.get_first_child();
        this._originalLabelActor = this._dateMenu.label_actor;
        this._dateMenu.remove_child(this._originalClock);
        this._clockLabel = label('', 'clock quiet-island-clock');
        this._dateMenu.add_child(this._clockLabel);
        this._dateMenu.label_actor = this._clockLabel;
        this._dateMenu.add_style_class_name('quiet-island-clock-button');
        this._clockMenu = new ClockMenu(this._dateMenu);

        this._music = new ModuleButton('Música');
        this._music.menu.box.add_style_class_name('quiet-island-music-menu');
        this._musicIcon = new St.Icon({icon_name: 'audio-x-generic-symbolic', style_class: 'system-status-icon'});
        this._meter = new AudioMeter();
        this._music.box.add_child(this._musicIcon);
        this._music.box.add_child(this._meter);
        this._music.menu.connect('open-state-changed', (_menu, open) => {
            if (open) {
                this._mpris?.refresh();
                this._mpris?.refreshPosition();
            }
        });
        this._ai = new ModuleButton('Atividade de IA');
        this._aiStack = new St.Widget({layout_manager: new Clutter.FixedLayout(),
            y_align: Clutter.ActorAlign.CENTER, width: 22, height: 22});
        this._aiStackSignature = null;
        this._aiLabel = label('Sem conexão', 'quiet-island-title');
        this._aiCount = label('', 'quiet-island-count');
        this._ai.box.add_child(this._aiStack);
        this._ai.box.add_child(this._aiLabel);
        this._ai.box.add_child(this._aiCount);
        for (const button of [this._music, this._ai])
            Main.panel.menuManager.addMenu(button.menu);

        this._dateParent.remove_child(this._dateMenu.container);
        this._strip = new BalancedStrip(this._music.container, this._dateMenu.container,
            this._ai.container, this._settings.get_int('side-width'));
        this._dateParent.insert_child_at_index(this._strip, this._dateIndex);
        global.focus_manager.add_group(this._strip);
    }

    _alignToMonitor() {
        // GNOME centers _centerBox in the workarea. Cancel that offset so the clock
        // stays at the physical monitor center even with a side dock.
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._strip)
            return;
        const area = Main.layoutManager.getWorkAreaForMonitor(monitor.index);
        this._strip.translation_x = monitor.x + monitor.width / 2 - area.x - area.width / 2;
    }

    _updateClock() {
        if (!this._clockLabel)
            return;
        const now = GLib.DateTime.new_now_local();
        this._clockLabel.text = now.format('%H:%M');
        this._clockMenu?.updateDate();
        this._dateMenu.accessible_name = now.format('%A, %d de %B, %H:%M');
    }

    _updateMusic() {
        if (!this._music)
            return;
        const player = this._mpris?.current;
        this._music.container.visible = this._settings.get_boolean('show-music') &&
            !!player && ['Playing', 'Paused'].includes(player.status);
        if (!this._music.container.visible)
            this._music.menu.close();
        this._music.accessible_name = player
            ? `Música: ${player.title}, ${player.status === 'Playing' ? 'tocando' : 'pausado'}`
            : 'Música: nenhum player conectado';
        const signature = JSON.stringify(player ? [player.name, player.title, player.artist,
            player.cover, player.status, player.length, player.CanPlay, player.CanPause,
            player.CanGoNext, player.CanGoPrevious, player.CanSeek, player.CanControl] : []);
        if (signature !== this._musicSignature) {
            this._musicSignature = signature;
            this._rebuildMusicMenu(player);
        }
        this._updateMeter();
        this._updateProgress();
    }

    _rebuildMusicMenu(player) {
        this._coverCancel?.cancel();
        this._music.menu.removeAll();
        this._dragging = false;
        this._progress = null;
        this._timeLabel = null;
        if (!player) {
            this._music.menu.addMenuItem(infoItem('Nenhum player MPRIS conectado.'));
            return;
        }
        const details = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        details.add_style_class_name('quiet-island-static');
        details.add_style_class_name('quiet-island-music-content');
        if (this._settings.get_boolean('show-cover')) {
            this._cover = new St.Icon({icon_name: 'audio-x-generic-symbolic', icon_size: 68,
                style_class: 'quiet-island-cover', y_align: Clutter.ActorAlign.CENTER});
            details.add_child(this._cover);
            this._loadCover(player.cover, player.name);
        }
        const texts = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            style_class: 'quiet-island-track-info', x_expand: true});
        texts.add_child(label(player.title, 'quiet-island-track-title'));
        texts.add_child(label(player.artist || 'Artista não informado', 'quiet-island-secondary'));
        details.add_child(texts);
        const buttons = new St.BoxLayout({style_class: 'quiet-island-controls',
            y_align: Clutter.ActorAlign.CENTER, x_align: Clutter.ActorAlign.START});
        const canToggle = player.CanControl && (player.status === 'Playing' ? player.CanPause : player.CanPlay);
        for (const [method, icon, text, sensitive] of [
            ['Previous', 'media-skip-backward-symbolic', 'Faixa anterior', player.CanControl && player.CanGoPrevious],
            ['PlayPause', player.status === 'Playing' ? 'media-playback-pause-symbolic'
                : 'media-playback-start-symbolic', player.status === 'Playing' ? 'Pausar' : 'Reproduzir', canToggle],
            ['Next', 'media-skip-forward-symbolic', 'Próxima faixa', player.CanControl && player.CanGoNext],
        ]) {
            const button = new St.Button({style_class: 'quiet-island-control', accessible_name: text,
                can_focus: sensitive, reactive: sensitive,
                child: new St.Icon({icon_name: icon, icon_size: 16})});
            if (!sensitive)
                button.add_style_pseudo_class('insensitive');
            button.connect('clicked', () => this._mpris.command(method));
            buttons.add_child(button);
        }
        texts.add_child(buttons);
        this._music.menu.addMenuItem(details);
        if (this._settings.get_boolean('show-progress')) {
            if (player.length > 0) {
                const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
                this._progress = new Slider.Slider(0);
                this._progress.accessible_name = 'Posição na faixa';
                this._progress.reactive = player.CanControl && player.CanSeek;
                this._progress.can_focus = this._progress.reactive;
                this._progress.connect('drag-begin', () => { this._dragging = true; });
                this._progress.connect('drag-end', slider => {
                    this._dragging = false;
                    this._mpris.seek(slider.value);
                });
                this._progress.connect('notify::value', slider => {
                    if (!this._settingProgress && !this._dragging)
                        this._mpris.seek(slider.value);
                });
                item.add_child(this._progress);
                item.add_style_class_name('quiet-island-static');
                this._timeLabel = label('', 'quiet-island-secondary quiet-island-progress-time');
                const timeItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
                timeItem.add_style_class_name('quiet-island-static');
                this._timeLabel.x_expand = true;
                this._timeLabel.x_align = Clutter.ActorAlign.END;
                timeItem.add_child(this._timeLabel);
                this._music.menu.addMenuItem(item);
                this._music.menu.addMenuItem(timeItem);
            } else {
                this._music.menu.addMenuItem(infoItem('Duração não informada / transmissão ao vivo.', 'quiet-island-secondary'));
            }
        }
    }

    _updateProgress() {
        const player = this._mpris?.current;
        if (!player || !this._progress || this._dragging)
            return;
        this._settingProgress = true;
        this._progress.value = Math.max(0, Math.min(1, this._mpris.position(player) / player.length));
        this._settingProgress = false;
        if (this._timeLabel)
            this._timeLabel.text = `${formatTime(this._mpris.position(player))} / ${formatTime(player.length)}`;
    }

    _loadCover(uri, playerName) {
        this._coverCancel?.cancel();
        this._coverCancel = new Gio.Cancellable();
        const cover = this._cover;
        if (uri.startsWith('file://')) {
            const cancellable = this._coverCancel;
            Gio.File.new_for_uri(uri).read_async(GLib.PRIORITY_DEFAULT, cancellable, (file, result) => {
                try {
                    this._readCover(file.read_finish(result), cancellable, cover);
                } catch (_) {
                    if (!cancellable.is_cancelled()) {
                        this._bridge?.getArtwork(playerName, uri, cancellable, bytes => {
                            this._setCover(bytes, cancellable, cover);
                        });
                    }
                }
            });
        } else if (/^https?:\/\//.test(uri)) {
            const message = Soup.Message.new('GET', uri);
            const cancellable = this._coverCancel;
            this._coverSession.send_async(message, GLib.PRIORITY_DEFAULT, cancellable,
                (session, result) => {
                    try {
                        const stream = session.send_finish(result);
                        if (!cancellable.is_cancelled() && message.status_code === 200)
                            this._readCover(stream, cancellable, cover);
                        else
                            stream.close_async(GLib.PRIORITY_DEFAULT, null, null);
                    } catch (_) {
                        // Native fallback stays visible for unavailable artwork.
                    }
                });
        }
    }

    _readCover(stream, cancellable, cover) {
        const chunks = [];
        let size = 0;
        const close = () => stream.close_async(GLib.PRIORITY_DEFAULT, null, null);
        const read = () => stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, cancellable,
            (_stream, result) => {
                try {
                    const bytes = stream.read_bytes_finish(result);
                    if (cancellable.is_cancelled()) {
                        close();
                        return;
                    }
                    if (bytes.get_size() === 0) {
                        const data = new Uint8Array(size);
                        let offset = 0;
                        for (const chunk of chunks) {
                            data.set(chunk, offset);
                            offset += chunk.length;
                        }
                        if (size)
                            this._setCover(GLib.Bytes.new(data), cancellable, cover);
                        close();
                        return;
                    }
                    size += bytes.get_size();
                    if (size > 2_000_000) {
                        close();
                        return;
                    }
                    chunks.push(bytes.get_data());
                    read();
                } catch (_) {
                    close();
                }
            });
        read();
    }

    _setCover(bytes, cancellable, cover) {
        // Browser artwork is often a wide thumbnail. Crop it before St.Icon,
        // which otherwise stretches a rectangular image into the square slot.
        const stream = Gio.MemoryInputStream.new_from_bytes(bytes);
        GdkPixbuf.Pixbuf.new_from_stream_at_scale_async(stream, 512, 512, true,
            cancellable, (_source, result) => {
                try {
                    const image = GdkPixbuf.Pixbuf.new_from_stream_finish(result);
                    if (cancellable.is_cancelled())
                        return;
                    const side = Math.min(image.width, image.height);
                    const cropped = image.new_subpixbuf(Math.floor((image.width - side) / 2),
                        Math.floor((image.height - side) / 2), side, side);
                    const square = cropped.scale_simple(136, 136, GdkPixbuf.InterpType.BILINEAR)
                        .add_alpha(false, 0, 0, 0);
                    const pixels = new Uint8Array(square.get_pixels());
                    const stride = square.get_rowstride();
                    // Bake the 12 logical pixel radius into the 2x image itself.
                    // This avoids dependence on an offscreen shader's texture padding.
                    for (let y = 0; y < 136; y++) {
                        for (let x = 0; x < 136; x++) {
                            const dx = Math.max(24 - x - 0.5, x + 0.5 - 112, 0);
                            const dy = Math.max(24 - y - 0.5, y + 0.5 - 112, 0);
                            const alpha = Math.max(0, Math.min(1, 24.5 - Math.hypot(dx, dy)));
                            pixels[y * stride + x * 4 + 3] *= alpha;
                        }
                    }
                    const rounded = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels),
                        GdkPixbuf.Colorspace.RGB, true, 8, 136, 136, stride);
                    const [ok, data] = rounded.save_to_bufferv('png', [], []);
                    if (ok) {
                        cover.gicon = new Gio.BytesIcon({bytes: GLib.Bytes.new(data)});
                        cover._quietRounded = true;
                    }
                } catch (_) {
                    // Keep the fallback if the image cannot be decoded.
                } finally {
                    stream.close_async(GLib.PRIORITY_DEFAULT, null, null);
                }
            });
    }

    _updateMeter() {
        if (!this._meter)
            return;
        const enabled = this._settings.get_boolean('show-visualizer');
        const active = this._mpris?.current?.status === 'Playing';
        const available = this._snapshot.audio === 'available';
        this._meter.visible = enabled;
        this._musicIcon.visible = !enabled;
        this._meter.setLevels(enabled && active && available ? this._levels : []);
        this._meter.accessible_name = available && active
            ? 'Visualizador de áudio da saída padrão' : 'Visualizador de áudio sem sinal';
    }

    _toolIcon(tool) {
        const file = {codex: 'codex.svg', claude: 'claude.svg', hermes: 'hermes.ico'}[tool];
        return file ? new Gio.FileIcon({file: this.dir.get_child('icons').get_child(file)})
            : new Gio.ThemedIcon({name: 'system-run-symbolic'});
    }

    _onSnapshot(snapshot) {
        const reconnected = snapshot.connected && !this._snapshot.connected;
        if (snapshot.connected && this._snapshot.connected &&
            this._settings.get_boolean('notify-completion')) {
            for (const session of snapshot.sessions) {
                const previous = this._lastSessions.get(session.key);
                if (session.status === 'done' && session.connected &&
                    previous && !['done', 'idle', 'unknown'].includes(previous)) {
                    const project = this._settings.get_boolean('show-project') && session.project
                        ? ` · ${session.project}` : '';
                    Main.notify('Quiet Island', `${TOOL_NAMES[session.tool] ?? 'IA'}${project}: tarefa concluída.`);
                }
            }
        }
        this._lastSessions = new Map(snapshot.sessions.map(session => [session.key, session.status]));
        this._snapshot = snapshot;
        if (reconnected) {
            this._musicSignature = null;
            this._updateMusic();
        }
        this._updateAI();
        this._updateMeter();
    }

    _updateAI() {
        if (!this._ai)
            return;
        this._aiTimes = [];
        const connected = this._snapshot.sessions.filter(session => session.connected);
        const tools = ['codex', 'claude', 'hermes'].filter(tool => connected.some(s => s.tool === tool));
        const {count} = sessionSummary(this._snapshot.sessions);
        const {display} = sessionSummary(tools.length
            ? connected.filter(session => session.tool === tools[0]) : this._snapshot.sessions);
        this._ai.container.visible = this._settings.get_boolean('show-ai') &&
            this._snapshot.connected && count > 0;
        if (!this._ai.container.visible)
            this._ai.menu.close();
        this._updateAIStack(tools);
        const state = display ? STATES[display.status] ?? 'Sem sinal'
            : this._snapshot.connected ? 'Sem sessões' : 'Sem conexão';
        this._aiLabel.text = state;
        this._aiCount.text = String(count);
        this._aiCount.visible = count > 0;
        this._ai.accessible_name = `${display ? TOOL_NAMES[display.tool] : 'IA'}: ${state}. ${count} sessões conectadas.`;
        this._ai.menu.removeAll();
        const heading = infoItem('Atividade de IA', 'quiet-island-heading');
        if (this._snapshot.connected) {
            const countLabel = label(`${count} ${count === 1 ? 'sessão conectada' : 'sessões conectadas'}`,
                'quiet-island-secondary quiet-island-session-count');
            countLabel.x_expand = true;
            countLabel.x_align = Clutter.ActorAlign.END;
            heading.add_child(countLabel);
        }
        this._ai.menu.addMenuItem(heading);
        if (!this._snapshot.connected) {
            this._ai.menu.addMenuItem(infoItem('O serviço local da Quiet Island não está conectado.'));
            this._ai.menu.addMenuItem(infoItem('Inicie o serviço para receber eventos das integrações.', 'quiet-island-secondary'));
            return;
        }
        for (const tool of ['codex', 'claude']) {
            const usage = this._snapshot.usage?.[tool];
            const remaining = usage?.kind === 'quota' && usage.window_minutes === 300 &&
                Number.isFinite(usage.remaining) ? Math.max(0, Math.min(100, usage.remaining)) : null;
            const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
            item.add_style_class_name('quiet-island-static');
            const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
                x_expand: true, style_class: 'quiet-island-quota'});
            const title = new St.BoxLayout({x_expand: true, style_class: 'quiet-island-quota-title'});
            title.add_child(new St.Icon({gicon: this._toolIcon(tool), icon_size: 16,
                style_class: 'quiet-island-ai-icon'}));
            title.add_child(label(`${tool === 'claude' ? 'Claude' : 'Codex'} · 5 h`, 'quiet-island-secondary'));
            const value = label(remaining === null ? '—' : `${Math.round(remaining)}% restante`,
                'quiet-island-secondary quiet-island-quota-value');
            value.x_expand = true;
            value.x_align = Clutter.ActorAlign.END;
            title.add_child(value);
            box.add_child(title);
            const bar = new St.DrawingArea({x_expand: true, height: 6,
                style_class: 'quiet-island-quota-bar'});
            bar.accessible_name = `${TOOL_NAMES[tool]}: cota restante de 5 horas, ${remaining === null ? 'sem dados' : Math.round(remaining) + ' por cento'}`;
            bar.connect('repaint', () => {
                const cr = bar.get_context();
                const [width, height] = bar.get_surface_size();
                cr.setLineWidth(height);
                cr.setLineCap(1);
                cr.setSourceRGBA(1, 1, 1, 0.14);
                cr.moveTo(height / 2, height / 2);
                cr.lineTo(width - height / 2, height / 2);
                cr.stroke();
                if (remaining !== null && remaining > 0) {
                    const color = bar.get_theme_node().get_foreground_color();
                    cr.setSourceRGBA(color.red / 255, color.green / 255, color.blue / 255, 1);
                    cr.moveTo(height / 2, height / 2);
                    cr.lineTo(height / 2 + (width - height) * remaining / 100, height / 2);
                    cr.stroke();
                }
                cr.$dispose();
            });
            box.add_child(bar);
            item.add_child(box);
            this._ai.menu.addMenuItem(item);
        }
        if (!this._snapshot.sessions.length) {
            this._ai.menu.addMenuItem(infoItem('Nenhuma sessão reportada. Conecte os hooks da sua ferramenta.', 'quiet-island-secondary'));
            return;
        }
        const scrollItem = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
        scrollItem.add_style_class_name('quiet-island-static');
        const list = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true,
            style_class: 'quiet-island-session-list'});
        const scroll = new St.ScrollView({style_class: 'quiet-island-session-scroll',
            x_expand: true, overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER, vscrollbar_policy: St.PolicyType.AUTOMATIC,
            child: list});
        for (const session of this._snapshot.sessions.slice(0, 64)) {
            const row = new St.BoxLayout({style_class: 'quiet-island-session', x_expand: true});
            row.add_child(new St.Icon({gicon: this._toolIcon(session.tool), icon_size: 22,
                style_class: 'quiet-island-ai-icon'}));
            const texts = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
                x_expand: true, style_class: 'quiet-island-session-text'});
            texts.add_child(label(STATES[session.status] ?? 'Sem sinal'));
            const project = this._settings.get_boolean('show-project') && session.project
                ? `${session.project} · ` : '';
            const timeLabel = label(`${project}${taskElapsed(session)}`, 'quiet-island-secondary');
            texts.add_child(timeLabel);
            this._aiTimes.push({actor: timeLabel, prefix: project, session});
            row.add_child(texts);
            row.add_child(new UsageRing(session.context));
            row.accessible_name = `${TOOL_NAMES[session.tool]}: ${STATES[session.status]}. ${project}`;
            if (session.terminal) {
                const button = new St.Button({child: row, can_focus: true,
                    style_class: 'quiet-island-session-button',
                    accessible_name: `${row.accessible_name} Abrir janela do terminal.`});
                button.connect('clicked', () => {
                    const timestamp = global.get_current_time();
                    this._ai.menu.close();
                    try {
                        focusTerminal(session.terminal, timestamp);
                    } catch (error) {
                        Main.notify('Quiet Island', error.message);
                    }
                });
                list.add_child(button);
            } else {
                list.add_child(row);
            }
        }
        scrollItem.add_child(scroll);
        this._ai.menu.addMenuItem(scrollItem);
    }

    _updateAITimes() {
        for (const {actor, prefix, session} of this._aiTimes ?? [])
            actor.text = `${prefix}${taskElapsed(session)}`;
    }

    _updateAIStack(tools) {
        const signature = tools.join(',');
        if (signature === this._aiStackSignature)
            return;
        this._aiStackSignature = signature;
        this._aiStack.destroy_all_children();
        this._aiStack.width = 22 + Math.max(0, tools.length - 1) * 8;
        this._aiStack.accessible_name = tools.map(tool => TOOL_NAMES[tool]).join(', ');
        // Add the rear icons first so the first provider paints on top.
        for (let i = tools.length - 1; i >= 0; i--) {
            const icon = new St.Icon({gicon: this._toolIcon(tools[i]), icon_size: 16,
                width: 22, height: 22,
                style_class: 'quiet-island-ai-icon quiet-island-ai-stack-icon'});
            icon.set_position(i * 8, 0);
            this._aiStack.add_child(icon);
        }
    }

    disable() {
        if (this._tick)
            GLib.source_remove(this._tick);
        this._tick = 0;
        this._aiTimes = [];
        for (const [object, signal] of this._signals ?? [])
            object.disconnect(signal);
        this._signals = [];
        this._bridge?.destroy();
        this._bridge = null;
        this._mpris?.destroy();
        this._mpris = null;
        this._coverCancel?.cancel();
        this._coverSession?.abort();
        this._clockMenu?.destroy();
        this._clockMenu = null;
        for (const button of [this._music, this._ai]) {
            if (button)
                Main.panel.menuManager.removeMenu(button.menu);
        }
        if (this._strip) {
            global.focus_manager.remove_group(this._strip);
            this._strip.remove_child(this._dateMenu.container);
            this._dateParent.insert_child_at_index(this._dateMenu.container, this._dateIndex);
        }
        if (this._originalClock) {
            this._clockLabel?.destroy();
            this._dateMenu.add_child(this._originalClock);
            this._dateMenu.label_actor = this._originalLabelActor;
            this._dateMenu.remove_style_class_name('quiet-island-clock-button');
            this._dateMenu.accessible_name = this._originalLabelActor?.text ?? '';
        }
        this._music?.destroy();
        this._ai?.destroy();
        this._strip?.destroy();
        this._strip = this._music = this._ai = this._clockLabel = this._originalClock = null;
        this._settings = null;
    }
}
