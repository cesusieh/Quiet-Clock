#!/usr/bin/env python3
"""Run Quiet Island in an isolated GNOME 50 profile, never the live desktop."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from build import ROOT, UUID, build

DASH_UUID = "dash-to-panel@jderose9.github.com"


def prepare(state, smoke, with_dash=False):
    if state.exists() and any(state.iterdir()) and not (state / ".quiet-island-test").exists():
        raise SystemExit(f"Refusing to reuse unrelated directory: {state}")
    state.mkdir(parents=True, exist_ok=True)
    (state / ".quiet-island-test").touch()
    for name in ("data", "config", "cache", "state", "runtime"):
        (state / name).mkdir(exist_ok=True)
    extension = state / "data/gnome-shell/extensions" / UUID
    if extension.exists():
        shutil.rmtree(extension)
    shutil.copytree(build(), extension)
    dash = state / "data/gnome-shell/extensions" / DASH_UUID
    if dash.exists():
        shutil.rmtree(dash)
    if with_dash:
        installed = Path.home() / ".local/share/gnome-shell/extensions" / DASH_UUID
        if not installed.is_dir():
            raise SystemExit("Dash to Panel is not installed for this user")
        shutil.copytree(installed, dash)
    if smoke:
        # Enable Eval only in this temporary copy and its private D-Bus session.
        path = extension / "extension.js"
        with path.open("a") as handle:
            handle.write("\nconst quietIslandEnable = QuietIsland.prototype.enable;\n"
                         "QuietIsland.prototype.enable = function () {\n"
                         " quietIslandEnable.call(this);\n"
                         " global.context.unsafe_mode = true;\n"
                         " globalThis.__quietIslandSmoke = {extension: this, Main, focusTerminal};\n};\n")
    env = os.environ.copy()
    env.update(XDG_DATA_HOME=str(state / "data"), XDG_CONFIG_HOME=str(state / "config"),
               XDG_CACHE_HOME=str(state / "cache"), XDG_STATE_HOME=str(state / "state"),
               GSETTINGS_BACKEND="keyfile", GNOME_SHELL_SESSION_MODE="user",
               NO_AT_BRIDGE="1")
    env.pop("GTK_THEME", None)
    return env


def eval_shell(connection, code):
    from gi.repository import Gio, GLib
    result = connection.call_sync("org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell",
                                  "Eval", GLib.Variant("(s)", (code,)),
                                  None, Gio.DBusCallFlags.NONE, 3000, None).unpack()
    if not result[0]:
        raise RuntimeError(f"Shell evaluation failed: {result[1]}")
    return json.loads(result[1]) if result[1] else None


def terminal_smoke_test(connection, shell, state):
    from gi.repository import Gio, GLib
    from quiet_island.service import publish
    env = os.environ.copy()
    env['WAYLAND_DISPLAY'] = shell.args[shell.args.index('--wayland-display') + 1]
    env['GDK_BACKEND'] = 'wayland'
    with (state / 'terminal.log').open('w') as log:
        terminal = subprocess.Popen(['ptyxis', '--standalone', '--new-window',
            '--title', 'Quiet Island terminal test', '--', '/usr/bin/sleep', '45'],
            env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(60):
                if terminal.poll() is not None:
                    raise RuntimeError('Test terminal exited; see terminal.log')
                count = eval_shell(connection,
                    f'global.get_window_actors().filter(a => a.meta_window.get_pid() === {terminal.pid}).length')
                if count == 1:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError('Test terminal window was not mapped')
            stat = Path(f'/proc/{terminal.pid}/stat').read_text()
            start = stat[stat.rindex(')') + 2:].split()[19]
            publish({'tool': 'codex', 'id': 'nested-test-codex', 'status': 'thinking',
                'terminal': {'kind': 'ptyxis-window', 'pid': terminal.pid, 'start': start}})
            time.sleep(0.2)
            eval_shell(connection, 'globalThis.__quietIslandSmoke.Main.overview.show(); true')
            time.sleep(0.3)
            clicked = eval_shell(connection, """(() => {
                const e = globalThis.__quietIslandSmoke.extension;
                e._ai.menu.open();
                const find = actor => actor.has_style_class_name?.('quiet-island-session-button')
                    ? actor : actor.get_children().map(find).find(Boolean);
                const button = find(e._ai.menu.box);
                if (!button)
                    return false;
                button.emit('clicked', 1);
                return !e._ai.menu.isOpen;
            })()""")
            time.sleep(0.4)
            focused = eval_shell(connection,
                f'global.display.focus_window?.get_pid() === {terminal.pid} && !globalThis.__quietIslandSmoke.Main.overview.visible')
            if not clicked or not focused:
                raise RuntimeError(f'Terminal activity click failed: clicked={clicked}, focused={focused}')
            stale = eval_shell(connection, """(() => {
                try {
                    globalThis.__quietIslandSmoke.focusTerminal({kind:'ptyxis-window',pid:%s,start:'0'});
                    return false;
                } catch (_) { return true; }
            })()""" % terminal.pid)
            identity = eval_shell(connection, """(() => {
                const w = global.get_window_actors().find(a => a.meta_window.get_pid() === %s).meta_window;
                return [w.get_gtk_unique_bus_name(), w.get_gtk_application_object_path()];
            })()""" % terminal.pid)
            connection.call_sync(identity[0], identity[1], 'org.gtk.Actions', 'Activate',
                GLib.Variant('(sava{sv})', ('new-window', [], {})), None,
                Gio.DBusCallFlags.NONE, 2000, None)
            for _ in range(40):
                count = eval_shell(connection,
                    f'global.get_window_actors().filter(a => a.meta_window.get_pid() === {terminal.pid}).length')
                if count == 2:
                    break
                time.sleep(0.1)
            ambiguous = eval_shell(connection, """(() => {
                try {
                    globalThis.__quietIslandSmoke.focusTerminal({kind:'ptyxis-window',pid:%s,start:%s});
                    return false;
                } catch (error) { return error.message.includes('várias janelas'); }
            })()""" % (terminal.pid, json.dumps(start)))
            if not stale or count != 2 or not ambiguous:
                raise RuntimeError(f'Terminal identity guards failed: stale={stale}, windows={count}, ambiguous={ambiguous}')
            return {'sessionClick': True, 'focusedExistingWindow': True, 'overviewClosed': True,
                'staleIdentityRejected': True, 'ambiguousWindowsRejected': True}
        finally:
            terminal.terminate()
            terminal.wait(timeout=5)




def smoke_test(shell, state, player=None):
    from gi.repository import Gio, GLib
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    deadline = time.monotonic() + 45
    errors = None
    while time.monotonic() < deadline:
        if shell.poll() is not None:
            raise RuntimeError("Nested Shell exited; see nested.log")
        try:
            result = connection.call_sync(
                "org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions",
                "org.gnome.Shell.Extensions", "GetExtensionInfo", GLib.Variant("(s)", (UUID,)),
                None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
            info = {key: value.unpack() if isinstance(value, GLib.Variant) else value
                    for key, value in result.items()}
            if info.get("state") == 1:
                break
            if info.get("state") == 3:
                errors = connection.call_sync("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions",
                    "org.gnome.Shell.Extensions", "GetExtensionErrors", GLib.Variant("(s)", (UUID,)),
                    None, Gio.DBusCallFlags.NONE, 1000, None).unpack()
                info["errors"] = errors
                raise RuntimeError(f"Extension failed: {info}")
        except GLib.Error:
            pass
        time.sleep(0.2)
    else:
        raise RuntimeError(f"Timed out waiting for extension: {errors}")
    time.sleep(1)
    eval_shell(connection, "globalThis.__quietIslandSmoke.Main.messageTray.getSources().flatMap(s => [...s.notifications]).forEach(n => n.destroy()); true")
    time.sleep(0.3)
    # Everything below runs in the nested Shell, not the user's session.
    script = """(() => {
        const {extension: e, Main} = globalThis.__quietIslandSmoke;
        const [x] = e._dateMenu.container.get_transformed_position();
        const [width] = e._dateMenu.container.get_transformed_size();
        const monitor = Main.layoutManager.primaryMonitor;
        return {clock: e._clockLabel.text, center: x + width / 2,
            monitorCenter: monitor.x + monitor.width / 2,
            nativeCalendar: !!e._dateMenu._calendar,
            compactCalendar: !!e._clockMenu?.calendar,
            musicMenu: !!e._music.menu, aiMenu: !!e._ai.menu,
            sessions: e._snapshot.sessions.length};
    })()"""
    result = eval_shell(connection, script)
    dash_state = eval_shell(connection, "globalThis.__quietIslandSmoke.Main.extensionManager.lookup('dash-to-panel@jderose9.github.com')?.state ?? null")
    if dash_state is not None and dash_state != 1:
        raise RuntimeError(f"Dash to Panel did not activate: {dash_state}")
    result["dashToPanelActive"] = dash_state == 1
    if abs(result["center"] - result["monitorCenter"]) > 1:
        raise RuntimeError(f"Clock not centered: {result}")
    if not result["nativeCalendar"] or not result["musicMenu"] or not result["aiMenu"]:
        raise RuntimeError(f"Menus missing: {result}")
    sys.path.insert(0, str(ROOT / "bridge"))
    from quiet_island.service import publish
    for tool, status in [("codex", "thinking"), ("claude", "idle"), ("hermes", "waiting")]:
        publish({"tool": tool, "id": "nested-test-" + tool, "status": status,
                 "project": "Projeto de teste", 'task_action': 'start'})
    for _ in range(30):
        details = eval_shell(connection, """(() => {
            const e = globalThis.__quietIslandSmoke.extension;
            return {sessions: e._snapshot.sessions.length, player: e._mpris.current?.title,
                duration: e._mpris.current?.length, progress: !!e._progress,
                time: e._timeLabel?.text, ai: e._aiLabel.text};
        })()""")
        if details["sessions"] == 3 and details.get("progress"):
            break
        time.sleep(0.1)
    if details["sessions"] != 3 or details.get("player") != "Faixa de teste" or not details["progress"]:
        raise RuntimeError(f"Live MPRIS/bridge integration failed: {details}")
    if details.get("time") is None or "/ 2:00" not in details["time"]:
        raise RuntimeError(f"Progress label missing: {details}")
    for _ in range(30):
        cover_loaded = eval_shell(connection,
            "globalThis.__quietIslandSmoke.extension._cover?.gicon?.to_string()?.startsWith('data:') ?? false")
        if cover_loaded:
            break
        time.sleep(0.1)
    # A BytesIcon serialized by GIO may not have a data URI on every version;
    # inspect its GType instead, still on the real Shell.
    if not cover_loaded:
        cover_loaded = eval_shell(connection,
            "globalThis.__quietIslandSmoke.extension._cover?.gicon?.constructor?.name?.includes('BytesIcon') ?? false")
    if not cover_loaded:
        raise RuntimeError("HTTP artwork did not load into a BytesIcon")
    details["httpArtwork"] = True
    result['terminalActivation'] = terminal_smoke_test(connection, shell, state)
    # Fixture quota is only injected into this isolated test copy. Production
    # obtains aggregate quota from Codex; missing provider metrics stay unknown.
    before_age = eval_shell(connection, """(() => {
        const e = globalThis.__quietIslandSmoke.extension;
        e._snapshot.usage = {codex:{kind:'quota',remaining:64,window_minutes:300},
            claude:{kind:'quota',remaining:75,window_minutes:300}};
        e._snapshot.sessions = e._snapshot.sessions.map(s => ({...s, context:
            s.tool === 'codex' ? {kind:'context',remaining:82} :
            s.tool === 'claude' ? {kind:'context',remaining:91} : undefined}));
        e._updateAI();
        globalThis.__quietIslandTimeLabel = e._aiTimes[0].actor;
        return e._aiTimes[0].actor.text;
    })()""")
    time.sleep(2.2)
    timer_check = eval_shell(connection, """(() => {
        const e = globalThis.__quietIslandSmoke.extension;
        const collect = actor => [actor, ...actor.get_children().flatMap(collect)];
        return {text:e._aiTimes[0].actor.text,
            sameActor:e._aiTimes[0].actor === globalThis.__quietIslandTimeLabel,
            hashesHidden:e._aiTimes.every(t => e._snapshot.sessions.every(s => !t.actor.text.includes(s.id.slice(-8)))),
            bars:collect(e._ai.menu.box).filter(a => a.has_style_class_name?.('quiet-island-quota-value')).map(a=>a.text),
            rings:collect(e._ai.menu.box).filter(a => a.has_style_class_name?.('quiet-island-usage-value')).map(a=>a.text)};
    })()""")
    if (timer_check['text'] == before_age or not timer_check['sameActor'] or not timer_check['hashesHidden'] or
            timer_check['bars'] != ['64% restante', '75% restante'] or
            timer_check['rings'].count('82%') != 1 or timer_check['rings'].count('91%') != 1 or
            timer_check['rings'].count('—') != 1):
        raise RuntimeError(f'Activity timer or quota ring failed: {timer_check}')
    result['activityTimer'] = {'advancesWithoutEvents': True, 'keepsRowActor': True, 'hashesHidden': True}
    result['usageDisplay'] = {'quotaBars': [64, 75], 'sessionContextRings': [82, 91], 'hermesUnknown': True}
    result["live"] = details
    for which in ("music", "ai", "clock"):
        check = eval_shell(connection, """(() => {
            const e = globalThis.__quietIslandSmoke.extension;
            const buttons = {music: e._music, ai: e._ai, clock: e._dateMenu};
            buttons[%s].menu.open();
            return Object.fromEntries(Object.entries(buttons).map(([key, button]) => [key, button.menu.isOpen]));
        })()""" % json.dumps(which))
        if sum(check.values()) != 1 or not check[which]:
            raise RuntimeError(f"Menus are not independent: {check}")
        time.sleep(0.12)
    time.sleep(0.3)
    calendar_layout = eval_shell(connection, """(() => {
        const e = globalThis.__quietIslandSmoke.extension;
        const c = e._clockMenu;
        const [, calendarY] = c.calendar.get_transformed_position();
        const [calendarX] = c.calendar.get_transformed_position();
        const [calendarWidth] = c.calendar.get_transformed_size();
        const first = c.calendar._buttons[0];
        const last = c.calendar._buttons[6];
        const [firstX] = first.get_transformed_position();
        const [lastX] = last.get_transformed_position();
        const [firstWidth] = first.get_transformed_size();
        const [lastWidth] = last.get_transformed_size();
        const leftGap = firstX + firstWidth / 2 - calendarX;
        const rightGap = calendarX + calendarWidth - lastX - lastWidth / 2;
        const [, notificationsY] = c.root.get_children()[2].get_transformed_position();
        return {heading:c.heading.text, width:e._dateMenu.menu.actor.width,
            height:e._dateMenu.menu.actor.height,
            oneColumn:notificationsY > calendarY,
            gridWidth:calendarWidth, leftGap, rightGap,
            nativePanelDetached:c._originalChildren.every(child => child.get_parent() === null)};
    })()""")
    if not calendar_layout["oneColumn"] or not calendar_layout["nativePanelDetached"]:
        raise RuntimeError(f"Calendar is not the compact form layout: {calendar_layout}")
    if abs(calendar_layout["leftGap"] - calendar_layout["rightGap"]) > 1:
        raise RuntimeError(f"Calendar columns do not fill the width symmetrically: {calendar_layout}")
    result["calendarLayout"] = calendar_layout
    music_layout = eval_shell(connection, """(() => {
        const e = globalThis.__quietIslandSmoke.extension;
        const texts = e._cover.get_parent().get_last_child();
        const buttons = texts.get_last_child().get_children();
        return {width:e._music.menu.actor.width,height:e._music.menu.actor.height,
            panelHasText:e._music.box.get_children().some(child => child.text !== undefined),
            meterVisible:e._meter.visible,
            musicIconVisible:e._musicIcon.visible,
            controlWidths:buttons.map(button => button.width),
            coverSize:[e._cover.width,e._cover.height],
            controlsBelowArtist:buttons[0].get_transformed_position()[1] > texts.get_children()[1].get_transformed_position()[1],
            progressWidth:e._progress.width,
            clippedCover:e._cover._quietRounded === true};
    })()""")
    if music_layout["height"] <= 0 or music_layout["width"] / music_layout["height"] < 2.5:
        raise RuntimeError(f"Music menu is not compact and horizontal: {music_layout}")
    if music_layout["panelHasText"] or not music_layout["clippedCover"] or max(music_layout["controlWidths"]) > 34:
        raise RuntimeError(f"Music controls or cover differ from requested layout: {music_layout}")
    if music_layout["coverSize"] != [68, 68] or not music_layout["controlsBelowArtist"] or music_layout["progressWidth"] < 400:
        raise RuntimeError(f"Cover dimensions or expanded music layout failed: {music_layout}")
    result["musicLayout"] = music_layout
    if not music_layout["meterVisible"] or music_layout["musicIconVisible"]:
        raise RuntimeError(f"Visualizer was replaced with an icon without audio: {music_layout}")
    navigation = eval_shell(connection, """(() => {
        const c = globalThis.__quietIslandSmoke.extension._clockMenu.calendar;
        const before = c._selectedDate.getMonth();
        c._forwardButton.emit('clicked', 1);
        const next = c._selectedDate.getMonth();
        c._backButton.emit('clicked', 1);
        const button = c._buttons.find(b => b._date.getMonth() === before && b._date.getDate() === 4);
        button.emit('clicked', 1);
        return {next:next === (before + 1) % 12,
            back:c._selectedDate.getMonth() === before, selected:c._selectedDate.getDate(),
            unpadded:button.label === '4'};
    })()""")
    if not navigation["next"] or not navigation["back"] or navigation["selected"] != 4 or not navigation["unpadded"]:
        raise RuntimeError(f"Calendar navigation failed: {navigation}")
    result["calendarNavigation"] = navigation
    # Capture the extension running in the isolated GNOME session.
    eval_shell(connection, """(() => {
        const e = globalThis.__quietIslandSmoke.extension;
        e._dateMenu.menu.close();
        globalThis.__quietIslandSmoke.Main.overview.hide();
        e._ai.menu.open();
        return true;
    })()""")
    time.sleep(0.5)
    try:
        (state / "quiet-island.png").unlink(missing_ok=True)
        screenshot = connection.call_sync("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot",
            "org.gnome.Shell.Screenshot", "Screenshot",
            GLib.Variant("(bbs)", (False, False, str(state / "quiet-island.png"))),
            None, Gio.DBusCallFlags.NONE, 5000, None).unpack()
        result["screenshot"] = screenshot[1] if screenshot[0] else None
        for which in ("music", "clock"):
            eval_shell(connection, """(() => {
                const e = globalThis.__quietIslandSmoke.extension;
                ({music:e._music, clock:e._dateMenu})[%s].menu.open();
                return true;
            })()""" % json.dumps(which))
            time.sleep(0.3)
            (state / f"quiet-island-{which}.png").unlink(missing_ok=True)
            connection.call_sync("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot",
                "org.gnome.Shell.Screenshot", "Screenshot",
                GLib.Variant("(bbs)", (False, False, str(state / f"quiet-island-{which}.png"))),
                None, Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error:
        result["screenshot"] = None
    count_notifications = "globalThis.__quietIslandSmoke.Main.messageTray.getSources().flatMap(s => s.notifications).filter(n => n.title === 'Quiet Island').length"
    before = eval_shell(connection, count_notifications)
    publish({"tool": "codex", "id": "nested-test-codex", "status": "done"})
    time.sleep(0.2)
    publish({"tool": "codex", "id": "nested-test-codex", "status": "done"})
    time.sleep(0.2)
    after = eval_shell(connection, count_notifications)
    if after != before + 1:
        raise RuntimeError(f"Completion notification duplicated or missing: {before} -> {after}")
    result["completionNotificationOnce"] = True
    frozen = eval_shell(connection, "globalThis.__quietIslandSmoke.extension._aiTimes.find(t=>t.session.tool==='codex').actor.text")
    time.sleep(1.3)
    frozen_after = eval_shell(connection, "globalThis.__quietIslandSmoke.extension._aiTimes.find(t=>t.session.tool==='codex').actor.text")
    if frozen != frozen_after or '—' in frozen:
        raise RuntimeError(f'Completed task clock did not freeze: {frozen} -> {frozen_after}')
    result['taskDurationFrozenAfterCompletion'] = True
    size_with_notification = eval_shell(connection, """(() => {
        const c = globalThis.__quietIslandSmoke.extension._dateMenu.menu.actor;
        return {width:c.width,height:c.height};
    })()""")
    if (abs(size_with_notification["width"] - calendar_layout["width"]) > 1 or
            size_with_notification["height"] <= calendar_layout["height"] or
            size_with_notification["height"] > 572):
        raise RuntimeError(f"Clock menu did not grow within its limit: {calendar_layout} -> {size_with_notification}")
    result["adaptiveCalendarSize"] = size_with_notification
    cards = eval_shell(connection, "[...globalThis.__quietIslandSmoke.extension._clockMenu._messages.keys()].filter(n => n.title === 'Quiet Island').length")
    if cards != after:
        raise RuntimeError(f"Clock menu did not receive real notifications: {cards} != {after}")
    action = eval_shell(connection, """(() => {
        const {extension:e, Main} = globalThis.__quietIslandSmoke;
        Main.notify('Quiet Island action test', 'Ação de notificação de teste');
        const n = Main.messageTray.getSources().flatMap(s => s.notifications)
            .find(n => n.title === 'Quiet Island action test');
        let invoked = false;
        n.addAction('Ação de teste', () => {invoked = true;});
        const card = e._clockMenu._messages.get(n);
        card._buttonBox.get_first_child().emit('clicked', 1);
        return {invoked, removed:!e._clockMenu._messages.has(n)};
    })()""")
    if not action["invoked"] or not action["removed"]:
        raise RuntimeError(f"Native notification actions were lost: {action}")
    result["notificationActions"] = action
    eval_shell(connection, """(() => {
        const {extension:e, Main} = globalThis.__quietIslandSmoke;
        for (let i = 0; i < 12; i++)
            Main.notify('Quiet Island height test ' + i, 'Notificação para verificar o limite e a rolagem.');
        e._dateMenu.menu.open();
        return true;
    })()""")
    time.sleep(0.4)
    crowded = eval_shell(connection, """(() => {
        const e = globalThis.__quietIslandSmoke.extension;
        const c = e._clockMenu;
        return {height:e._dateMenu.menu.actor.height, width:e._dateMenu.menu.actor.width,
            viewport:c._scroll.height, content:c._list.get_preferred_height(320)[1],
            scrollUpper:c._scroll.vadjustment?.upper ?? null,
            scrollPage:c._scroll.vadjustment?.page_size ?? null};
    })()""")
    if crowded["height"] > 572 or crowded["content"] <= crowded["viewport"]:
        raise RuntimeError(f"Notification height limit or scrolling failed: {crowded}")
    result["notificationOverflow"] = crowded
    eval_shell(connection, """(() => {
        const {Main} = globalThis.__quietIslandSmoke;
        Main.messageTray.getSources().flatMap(s => [...s.notifications]).forEach(n => n.destroy());
        return true;
    })()""")
    time.sleep(0.3)
    compact = eval_shell(connection, """(() => {
        const e = globalThis.__quietIslandSmoke.extension;
        const c = e._clockMenu;
        const header = c.root.get_children()[2];
        const [, y] = c._empty.get_transformed_position();
        const [, headerY] = header.get_transformed_position();
        return {height:e._dateMenu.menu.actor.height, gap:y-headerY-header.height};
    })()""")
    if compact["height"] > calendar_layout["height"] + 1 or compact["gap"] > 17:
        raise RuntimeError(f"Empty notifications left unused space: {compact}")
    result["compactEmptyCalendar"] = compact
    for tool in ("codex", "claude", "hermes"):
        publish({"tool": tool, "id": "nested-test-" + tool, "status": "disconnected"})
    if player and player.poll() is None:
        player.terminate()
        player.wait(timeout=5)
    time.sleep(0.3)
    empty = eval_shell(connection, """(() => {
        const {extension: e, Main} = globalThis.__quietIslandSmoke;
        const [x] = e._dateMenu.container.get_transformed_position();
        const [width] = e._dateMenu.container.get_transformed_size();
        const monitor = Main.layoutManager.primaryMonitor;
        return {musicVisible:e._music.container.visible, aiVisible:e._ai.container.visible,
            center:x+width/2, monitorCenter:monitor.x+monitor.width/2};
    })()""")
    if empty["musicVisible"] or empty["aiVisible"] or abs(empty["center"] - empty["monitorCenter"]) > 1:
        raise RuntimeError(f"Empty modules did not hide or clock moved: {empty}")
    result["emptyModulesHidden"] = empty
    # Capture the compact calendar once more after all activity has stopped,
    # matching the initial state of the interactive Devkit window.
    eval_shell(connection, "globalThis.__quietIslandSmoke.extension._dateMenu.menu.open(); true")
    time.sleep(0.7)
    (state / "quiet-island-clock.png").unlink(missing_ok=True)
    connection.call_sync("org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot",
        "org.gnome.Shell.Screenshot", "Screenshot",
        GLib.Variant("(bbs)", (False, False, str(state / "quiet-island-clock.png"))),
        None, Gio.DBusCallFlags.NONE, 5000, None)
    time.sleep(0.5)
    (state / "smoke-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    # Exercise disable/enable against a real Shell to verify restoration.
    before_reload = eval_shell(connection, count_notifications)
    for method in ("DisableExtension", "EnableExtension"):
        connection.call_sync("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions",
                             "org.gnome.Shell.Extensions", method, GLib.Variant("(s)", (UUID,)),
                             None, Gio.DBusCallFlags.NONE, 3000, None)
        time.sleep(0.5)
        if method == "DisableExtension":
            original = eval_shell(connection, "globalThis.__quietIslandSmoke.Main.panel.statusArea.dateMenu.menu.box.has_style_class_name('datemenu-popover')")
            if not original:
                raise RuntimeError("Original calendar panel was not restored on disable")
            result["originalCalendarRestored"] = True
    restored = eval_shell(connection, script)
    if not restored["nativeCalendar"] or abs(restored["center"] - restored["monitorCenter"]) > 1:
        raise RuntimeError(f"Re-enable failed: {restored}")
    if eval_shell(connection, count_notifications) != before_reload:
        raise RuntimeError("Re-enable replayed a completion notification")
    (state / "smoke-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Nested Shell: enable, native calendar, clock center, disable and re-enable OK", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".test-session")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--with-dash-to-panel", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Play synthetic audio through MPRIS and create a sample notification")
    parser.add_argument("--silent-demo", action="store_true", help="Show a sample MPRIS player without playing audio")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.smoke and not Path('/usr/libexec/mutter-devkit').is_file():
        raise SystemExit('Missing mutter-devkit: install it inside toolbox Code before opening a preview window.')
    state = args.state_dir.expanduser().resolve()
    if not args.inside:
        env = prepare(state, args.smoke, args.with_dash_to_panel)
        command = ["dbus-run-session", "--", sys.executable, str(Path(__file__).resolve()),
                   "--inside", "--state-dir", str(state)]
        if args.smoke:
            command.append("--smoke")
        if args.with_dash_to_panel:
            command.append("--with-dash-to-panel")
        if args.demo:
            command.append("--demo")
        if args.silent_demo:
            command.append("--silent-demo")
        return subprocess.call(command, env=env)
    extensions = [UUID]
    if args.with_dash_to_panel:
        dash = state / "data/gnome-shell/extensions" / DASH_UUID
        subprocess.run(["gsettings", "--schemadir", str(dash / "schemas"), "set",
                        "org.gnome.shell.extensions.dash-to-panel", "stockgs-keep-top-panel", "true"], check=True)
        extensions.insert(0, DASH_UUID)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", json.dumps(extensions)], check=True)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "disable-user-extensions", "false"], check=True)
    subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", "prefer-dark"], check=True)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "welcome-dialog-last-shown-version", "50.6"], check=True)
    if args.smoke:
        subprocess.run(["gsettings", "set", "org.gnome.desktop.notifications", "show-banners", "false"], check=True)
    command = ["gnome-shell", "--wayland", "--no-x11", "--wayland-display", f"quiet-island-{os.getpid()}"]
    command += ["--headless", "--virtual-monitor", "1280x720"] if args.smoke else ["--devkit"]
    bridge_command = [sys.executable, str(ROOT / "bin/quiet-island"), "service"]
    if args.smoke:
        bridge_command.append("--no-audio")
        bridge_command.append("--no-usage")
    bridge = subprocess.Popen(bridge_command)
    art_server = None
    player = None
    if args.smoke or args.demo or args.silent_demo:
        handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT / "tests/fixtures"))
        art_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=art_server.serve_forever, daemon=True).start()
        player_env = os.environ.copy()
        player_env["QUIET_ISLAND_ART_URL"] = f"http://127.0.0.1:{art_server.server_port}/cover.svg"
        if args.demo:
            from demo import create_audio
            audio_path = state / "demo.wav"
            create_audio(audio_path)
            player_env["QUIET_ISLAND_DEMO_AUDIO"] = str(audio_path)
        player = subprocess.Popen([sys.executable, str(ROOT / "tests/fixtures/mpris_player.py")], env=player_env)
    try:
        with (state / "nested.log").open("w") as log:
            shell = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            try:
                if args.demo:
                    from demo import notify
                    threading.Thread(target=notify, daemon=True).start()
                if args.smoke:
                    smoke_test(shell, state, player)
                    return 0
                return shell.wait()
            finally:
                if shell.poll() is None:
                    shell.terminate()
                    try:
                        shell.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        shell.kill()
    finally:
        if player and player.poll() is None:
            player.terminate()
            player.wait(timeout=5)
        if art_server:
            art_server.shutdown()
            art_server.server_close()
        bridge.terminate()
        bridge.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
