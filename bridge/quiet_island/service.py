"""Session-bus bridge and optional CAVA output monitor, outside GNOME Shell."""

import json
import logging
import os
import signal
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gi.repository import Gio, GLib

from .model import SessionStore

NAME = "org.quietisland.Bridge"
PATH = "/org/quietisland/Bridge"
INTERFACE = "org.quietisland.Bridge1"
XML = """<node><interface name="org.quietisland.Bridge1">
<method name="GetSnapshot"><arg type="s" direction="out"/></method>
<method name="Publish"><arg name="event" type="s" direction="in"/></method>
<method name="PublishContext"><arg type="s" direction="in"/></method>
<method name="PublishUsage"><arg type="s" direction="in"/></method>
<method name="GetArtwork"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="ay" direction="out"/></method>
<signal name="SnapshotChanged"><arg type="s"/></signal>
<signal name="LevelsChanged"><arg type="ad"/></signal>
</interface></node>"""

# CAVA's Pulse source 'auto' is the default output's monitor, not a microphone.
CAVA_CONFIG = """[general]
bars = 9
framerate = 12
autosens = 1
[input]
method = pulse
source = auto
[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 100
bar_delimiter = 59
frame_delimiter = 10
channels = mono
[smoothing]
noise_reduction = 65
"""


class BridgeService:
    def __init__(self, audio=True, stale_after=900, usage=True):
        self.store = SessionStore(stale_after)
        self.audio_enabled = audio
        self.audio_status = "unavailable"
        self.connection = None
        self.registration = 0
        self.process = None
        self.audio_config = None
        self.audio_stream = None
        self.audio_cancel = Gio.Cancellable()
        self.audio_retry = 0
        self._stopping = False
        self.loop = GLib.MainLoop()
        self._last_snapshot = None
        self._artwork_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quiet-artwork")
        self._artwork_slots = threading.BoundedSemaphore(2)
        self.usage = {}
        self.contexts = {}
        self._context_pending = False
        self._usage_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='quiet-usage')
        self._usage_pending = False
        self._next_usage = 0
        self._usage_enabled = usage

    def snapshot(self):
        usage = {tool: value for tool, value in self.usage.items()
                 if time.time() - value['updated_at'] <= 180}
        sessions = self.store.snapshot()
        for session in sessions:
            value = self.contexts.get(session['key'])
            if value and time.time() - value['updated_at'] <= 180:
                session['context'] = value
        return {"schema": 1, "sessions": sessions, "audio": self.audio_status, 'usage': usage}

    def _emit_snapshot(self):
        snapshot = json.dumps(self.snapshot(), ensure_ascii=False)
        if self.connection and snapshot != self._last_snapshot:
            self._last_snapshot = snapshot
            self.connection.emit_signal(None, PATH, INTERFACE, "SnapshotChanged",
                                        GLib.Variant("(s)", (snapshot,)))
        return GLib.SOURCE_CONTINUE

    def _on_method(self, connection, sender, path, interface, method, parameters, invocation):
        try:
            if method == "GetSnapshot":
                invocation.return_value(GLib.Variant("(s)", (json.dumps(self.snapshot()),)))
            elif method == "GetArtwork":
                self._get_artwork(parameters.unpack(), invocation)
            elif method == 'PublishUsage':
                from .usage import published_claude_usage
                raw = parameters.unpack()[0]
                if len(raw.encode('utf-8')) > 1024:
                    raise ValueError('Usage input too large')
                value = published_claude_usage(json.loads(raw))
                previous = self.usage.get('claude', {})
                if not (value['kind'] == 'unavailable' and previous.get('kind') == 'quota' and
                        time.time() - previous['updated_at'] <= 180):
                    self.usage['claude'] = value
                self._emit_snapshot()
                invocation.return_value(None)
            elif method == 'PublishContext':
                from .usage import context_summary
                from .model import clean_text
                raw = parameters.unpack()[0]
                if len(raw.encode('utf-8')) > 1024:
                    raise ValueError('Context input too large')
                data = json.loads(raw)
                identifier = data.get('id')
                if (data.get('tool') != 'claude' or not isinstance(identifier, str) or
                        not identifier or clean_text(identifier, 128) != identifier):
                    raise ValueError('Invalid context source')
                key = 'claude:' + identifier
                value = context_summary(data.get('remaining'))
                if value:
                    self.contexts[key] = value
                else:
                    self.contexts.pop(key, None)
                # Bound early status-line entries, including ones before SessionStart.
                if len(self.contexts) > 128:
                    self.contexts.pop(min(self.contexts, key=lambda k: self.contexts[k]['updated_at']))
                self._emit_snapshot()
                invocation.return_value(None)
            elif method == "Publish":
                raw = parameters.unpack()[0]
                if len(raw.encode("utf-8")) > 4096:
                    raise ValueError("Event too large")
                if self.store.publish(json.loads(raw)):
                    self._emit_snapshot()
                    self._refresh_usage()
                invocation.return_value(None)
            else:
                invocation.return_dbus_error(INTERFACE + ".UnknownMethod", "Unknown method")
        except (ValueError, TypeError, KeyError) as error:
            invocation.return_dbus_error(INTERFACE + ".InvalidEvent", str(error))

    def _get_artwork(self, parameters, invocation):
        from .artwork import resolve_artwork
        name, uri = parameters
        if len(name) > 255 or len(uri) > 2048 or not self._artwork_slots.acquire(blocking=False):
            invocation.return_dbus_error(INTERFACE + ".ArtworkUnavailable", "Artwork request unavailable")
            return

        def work():
            try:
                data = resolve_artwork(self.connection, name, uri)
            except Exception:
                data = None
            finally:
                self._artwork_slots.release()

            def deliver():
                if not self._stopping:
                    if data is None:
                        invocation.return_dbus_error(INTERFACE + ".ArtworkUnavailable", "Artwork is unavailable")
                    else:
                        invocation.return_value(GLib.Variant("(ay)", (data,)))
                return GLib.SOURCE_REMOVE

            GLib.idle_add(deliver)

        self._artwork_pool.submit(work)

    def _bus_acquired(self, connection, name):
        self.connection = connection
        interface = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self.registration = connection.register_object(PATH, interface, self._on_method, None, None)

    def _name_acquired(self, connection, name):
        logging.info("Quiet Island bridge ready")
        if self.audio_enabled:
            self._start_audio()

    def _refresh_usage(self):
        if (not self._usage_enabled or self._stopping or self._usage_pending or time.monotonic() < self._next_usage or
                not any(s['tool'] == 'codex' and s['connected'] for s in self.store.snapshot())):
            return GLib.SOURCE_CONTINUE
        self._next_usage = time.monotonic() + 60
        self._usage_pending = True

        def work():
            try:
                from .usage import read_codex_quota
                value = read_codex_quota()
            except Exception:
                value = None

            def deliver():
                self._usage_pending = False
                if not self._stopping:
                    if value:
                        self.usage['codex'] = value
                    self._emit_snapshot()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(deliver)

        self._usage_pool.submit(work)
        return GLib.SOURCE_CONTINUE

    def _refresh_contexts(self):
        if not self._usage_enabled or self._stopping or self._context_pending:
            return GLib.SOURCE_CONTINUE
        ids = [s['id'] for s in self.store.snapshot() if s['tool'] == 'codex' and s['connected']]
        if not ids:
            return GLib.SOURCE_CONTINUE
        self._context_pending = True
        def work():
            try:
                from .usage import read_codex_quota
                values = read_codex_quota(ids) or {}
            except Exception:
                values = {}
            def deliver():
                self._context_pending = False
                if not self._stopping:
                    for identifier in ids:
                        self.contexts.pop('codex:' + identifier, None)
                    self.contexts.update({'codex:' + k: v for k, v in values.items()})
                    self._emit_snapshot()
                return GLib.SOURCE_REMOVE
            GLib.idle_add(deliver)
        self._usage_pool.submit(work)
        return GLib.SOURCE_CONTINUE

    def _name_lost(self, connection, name):
        logging.error("Quiet Island: session bus unavailable or bridge already running")
        self.stop()

    def _start_audio(self):
        if self._stopping:
            return GLib.SOURCE_REMOVE
        self.audio_retry = 0
        if not shutil.which("cava"):
            self.audio_status = "unavailable"
            self._emit_snapshot()
            return GLib.SOURCE_REMOVE
        if self.audio_config is None:
            handle = tempfile.NamedTemporaryFile(mode="w", prefix="quiet-island-cava-", delete=False)
            with handle:
                handle.write(CAVA_CONFIG)
            self.audio_config = Path(handle.name)
        try:
            self.process = Gio.Subprocess.new(
                ["cava", "-p", str(self.audio_config)],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
            self.audio_stream = Gio.DataInputStream.new(self.process.get_stdout_pipe())
            self.audio_status = "starting"
            self.process.wait_async(None, self._audio_exited)
            self._read_audio()
        except GLib.Error:
            self.audio_status = "unavailable"
            self._schedule_audio_retry()
        self._emit_snapshot()
        return GLib.SOURCE_REMOVE

    def _read_audio(self):
        if not self._stopping:
            self.audio_stream.read_line_async(GLib.PRIORITY_DEFAULT, self.audio_cancel, self._audio_line)

    def _audio_line(self, stream, result):
        try:
            line, length = stream.read_line_finish(result)
            if line is None or self._stopping:
                return
            if length <= 128:
                fields = line.decode("ascii").strip(";\r\n").split(";")
                levels = [max(0, min(100, int(value))) / 100 for value in fields]
                if len(levels) == 9:
                    if self.audio_status != "available":
                        self.audio_status = "available"
                        self._emit_snapshot()
                    self.connection.emit_signal(None, PATH, INTERFACE, "LevelsChanged",
                                                GLib.Variant("(ad)", (levels,)))
        except (GLib.Error, ValueError, UnicodeError):
            if self._stopping:
                return
        self._read_audio()

    def _audio_exited(self, process, result):
        try:
            process.wait_finish(result)
        except GLib.Error:
            pass
        if self._stopping:
            return
        self.audio_status = "unavailable"
        self._emit_snapshot()
        self.connection.emit_signal(None, PATH, INTERFACE, "LevelsChanged", GLib.Variant("(ad)", ([],)))
        self._schedule_audio_retry()

    def _schedule_audio_retry(self):
        if not self.audio_retry and not self._stopping:
            self.audio_retry = GLib.timeout_add_seconds(30, self._start_audio)

    def stop(self):
        self._stopping = True
        self.audio_cancel.cancel()
        self._artwork_pool.shutdown(wait=False, cancel_futures=True)
        self._usage_pool.shutdown(wait=False, cancel_futures=True)
        if self.process:
            self.process.force_exit()
        if self.audio_retry:
            GLib.source_remove(self.audio_retry)
            self.audio_retry = 0
        if self.audio_config:
            self.audio_config.unlink(missing_ok=True)
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def run(self):
        owner = Gio.bus_own_name(Gio.BusType.SESSION, NAME, Gio.BusNameOwnerFlags.NONE,
                                self._bus_acquired, self._name_acquired, self._name_lost)
        timer = GLib.timeout_add_seconds(15, self._emit_snapshot)
        usage_timer = GLib.timeout_add_seconds(60, self._refresh_usage)
        context_timer = GLib.timeout_add_seconds(5, self._refresh_contexts)
        signals = [GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self.stop)
                   for signum in (signal.SIGTERM, signal.SIGINT)]
        try:
            self.loop.run()
        finally:
            GLib.source_remove(timer)
            GLib.source_remove(usage_timer)
            GLib.source_remove(context_timer)
            for source in signals:
                if GLib.MainContext.default().find_source_by_id(source):
                    GLib.source_remove(source)
            if self.registration and self.connection:
                self.connection.unregister_object(self.registration)
            Gio.bus_unown_name(owner)


def publish(event):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    connection.call_sync(NAME, PATH, INTERFACE, "Publish",
                         GLib.Variant("(s)", (json.dumps(event),)), None,
                         Gio.DBusCallFlags.NONE, 1200, None)


def get_snapshot():
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    result = connection.call_sync(NAME, PATH, INTERFACE, "GetSnapshot", None,
                                  GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE,
                                  1200, None)
    return json.loads(result.unpack()[0])


def publish_claude_usage(usage):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    connection.call_sync(NAME, PATH, INTERFACE, 'PublishUsage',
        GLib.Variant('(s)', (json.dumps({'tool': 'claude', 'usage': usage}),)),
        None, Gio.DBusCallFlags.NONE, 1200, None)


def publish_claude_context(identifier, remaining):
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    connection.call_sync(NAME, PATH, INTERFACE, 'PublishContext',
        GLib.Variant('(s)', (json.dumps({'tool': 'claude', 'id': identifier, 'remaining': remaining}),)),
        None, Gio.DBusCallFlags.NONE, 1200, None)
