"""A real session-bus MPRIS fixture with capability checks and a call log."""
import json
import os
import signal
import sys
from pathlib import Path

from gi.repository import Gio, GLib

LOG = Path(sys.argv[1]) if len(sys.argv) > 1 else None
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = ROOT_IFACE + ".Player"
PATH = "/org/mpris/MediaPlayer2"
XML = """<node>
<interface name="org.quietisland.TestPlayer">
<method name="SetTrack"><arg type="s" direction="in"/><arg type="x" direction="in"/><arg type="b" direction="in"/></method>
</interface>
<interface name="org.mpris.MediaPlayer2">
<property name="Identity" type="s" access="read"/>
<property name="DesktopEntry" type="s" access="read"/>
<property name="CanRaise" type="b" access="read"/>
</interface>
<interface name="org.mpris.MediaPlayer2.Player">
<method name="Play"/><method name="Pause"/><method name="PlayPause"/>
<method name="Next"/><method name="Previous"/>
<method name="SetPosition"><arg type="o" direction="in"/><arg type="x" direction="in"/></method>
<signal name="Seeked"><arg type="x"/></signal>
<property name="PlaybackStatus" type="s" access="read"/>
<property name="Metadata" type="a{sv}" access="read"/>
<property name="Position" type="x" access="read"/>
<property name="Rate" type="d" access="read"/>
<property name="CanPlay" type="b" access="read"/>
<property name="CanPause" type="b" access="read"/>
<property name="CanGoNext" type="b" access="read"/>
<property name="CanGoPrevious" type="b" access="read"/>
<property name="CanSeek" type="b" access="read"/>
<property name="CanControl" type="b" access="read"/>
</interface></node>"""


class Player:
    def __init__(self):
        self.status = "Playing"
        self.position = 5_000_000
        self.calls = []
        self.loop = GLib.MainLoop()
        self.track = "/org/mpris/MediaPlayer2/Track/1"
        self.audio = None
        self.duration = 120_000_000
        self.title = "Faixa de teste"
        if os.environ.get("QUIET_ISLAND_DEMO_AUDIO"):
            import gi
            import wave
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            self.Gst = Gst
            path = Path(os.environ["QUIET_ISLAND_DEMO_AUDIO"])
            with wave.open(str(path)) as wav:
                self.duration = int(wav.getnframes() / wav.getframerate() * 1_000_000)
            self.audio = Gst.ElementFactory.make("playbin", "quiet-island-demo")
            self.audio.set_property("uri", path.as_uri())
            self.audio.set_property("volume", 0.35)
            bus = self.audio.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._loop_audio)
            bus.connect("message::error", lambda _bus, message: print(
                "Demo audio:", message.parse_error()[0], file=sys.stderr, flush=True))
            self.audio.set_state(Gst.State.PLAYING)

    def _loop_audio(self, *_args):
        self.audio.seek_simple(self.Gst.Format.TIME, self.Gst.SeekFlags.FLUSH, 0)
        if hasattr(self, "connection"):
            self.connection.emit_signal(None, PATH, PLAYER_IFACE, "Seeked", GLib.Variant("(x)", (0,)))

    def property(self, connection, sender, path, interface, name):
        if name in {"CanPlay", "CanPause", "CanGoNext", "CanSeek", "CanControl", "CanRaise"}:
            return GLib.Variant("b", True)
        if name == "CanGoPrevious":
            return GLib.Variant("b", False)
        values = {
            "Identity": GLib.Variant("s", "Quiet Island test player"),
            "DesktopEntry": GLib.Variant("s", "quiet-island-test"),
            "PlaybackStatus": GLib.Variant("s", self.status),
            "Position": GLib.Variant("x", self.current_position()),
            "Rate": GLib.Variant("d", 1.0),
            "Metadata": GLib.Variant("a{sv}", {
                "mpris:trackid": GLib.Variant("o", self.track),
                "mpris:length": GLib.Variant("x", self.duration),
                "xesam:title": GLib.Variant("s", "Quiet Island · demo" if self.audio else self.title),
                "xesam:artist": GLib.Variant("as", ["Áudio sintético de teste" if self.audio else "Artista de teste"]),
                "mpris:artUrl": GLib.Variant("s", os.environ.get("QUIET_ISLAND_ART_URL", "")),
            }),
        }
        return values[name]

    def current_position(self):
        if self.audio:
            success, position = self.audio.query_position(self.Gst.Format.TIME)
            return position // 1000 if success else 0
        return self.position

    def method(self, connection, sender, path, interface, method, params, invocation):
        if interface == "org.quietisland.TestPlayer" and method == "SetTrack":
            self.title, self.duration, notify = params.unpack()
            self.position = 0
            if notify:
                metadata = self.property(connection, sender, path, PLAYER_IFACE, "Metadata")
                connection.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                    GLib.Variant("(sa{sv}as)", (PLAYER_IFACE, {"Metadata": metadata}, [])))
            invocation.return_value(None)
            return
        self.calls.append({"method": method, "params": list(params.unpack())})
        if LOG:
            LOG.write_text(json.dumps(self.calls))
        if method == "Pause":
            self.status = "Paused"
        elif method == "Play":
            self.status = "Playing"
        elif method == "PlayPause":
            self.status = "Paused" if self.status == "Playing" else "Playing"
        elif method == "SetPosition":
            track, position = params.unpack()
            if track != self.track:
                invocation.return_dbus_error("org.mpris.InvalidTrack", "Wrong track id")
                return
            self.position = position
            if self.audio:
                self.audio.seek_simple(self.Gst.Format.TIME, self.Gst.SeekFlags.FLUSH, position * 1000)
            connection.emit_signal(None, PATH, PLAYER_IFACE, "Seeked", GLib.Variant("(x)", (position,)))
        elif method == "Next":
            self.track = "/org/mpris/MediaPlayer2/Track/2"
            self.position = 0
            if self.audio:
                self.audio.seek_simple(self.Gst.Format.TIME, self.Gst.SeekFlags.FLUSH, 0)
        if self.audio and method in {"Play", "Pause", "PlayPause"}:
            self.audio.set_state(self.Gst.State.PLAYING if self.status == "Playing" else self.Gst.State.PAUSED)
        changed = {"PlaybackStatus": GLib.Variant("s", self.status),
                   "Metadata": self.property(connection, sender, path, interface, "Metadata")}
        connection.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                               GLib.Variant("(sa{sv}as)", (PLAYER_IFACE, changed, [])))
        invocation.return_value(None)

    def acquired(self, connection, name):
        self.connection = connection
        for interface in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
            connection.register_object(PATH, interface, self.method, self.property, None)

    def run(self):
        Gio.bus_own_name(Gio.BusType.SESSION, "org.mpris.MediaPlayer2.QuietIslandTest",
                         Gio.BusNameOwnerFlags.NONE, self.acquired, None, None)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.loop.quit)
        try:
            self.loop.run()
        finally:
            if self.audio:
                self.audio.set_state(self.Gst.State.NULL)


if __name__ == "__main__":
    Player().run()
