"""Read only the current PNG artwork published by a known Chromium Flatpak.

The bus owner can be xdg-dbus-proxy rather than Chrome itself. Match its
systemd scope to Flatpak's runtime instance, not an arbitrary Chrome process.
No permissions, browser configuration, profiles or other files are modified.
"""
import configparser
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_ARTWORK = 2_000_000
APPS = {"com.google.Chrome", "org.chromium.Chromium"}
PNG = b"\x89PNG\r\n\x1a\n"


def artwork_path(uri):
    parts = urlsplit(uri)
    if parts.scheme != "file" or parts.netloc not in {"", "localhost"} or parts.query or parts.fragment:
        raise ValueError("Not a local artwork URI")
    path = unquote(parts.path, errors="strict")
    if not re.fullmatch(r"/tmp/\.(?:com\.google\.Chrome|org\.chromium\.Chromium)\.[A-Za-z0-9_-]{4,64}(?:\.(?:png|jpg|jpeg|webp))?", path):
        raise ValueError("Not a Chromium temporary artwork path")
    return path


def png_dimensions(data):
    if (len(data) < 33 or len(data) > MAX_ARTWORK or data[:8] != PNG or
            data[12:16] != b"IHDR"):
        raise ValueError("Invalid or oversized PNG artwork")
    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= 4096 and 1 <= height <= 4096 and width * height <= 16_777_216):
        raise ValueError("PNG dimensions exceed limit")
    return width, height


def locate_instance(pid, runtime_dir=None, proc_dir=Path("/proc")):
    runtime = Path(runtime_dir or os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    cgroup = (proc_dir / str(pid) / "cgroup").read_text()
    pattern = r"(?:^|/)app-flatpak-(com\.google\.Chrome|org\.chromium\.Chromium)-(\d+)\.scope$"
    match = next((re.search(pattern, line) for line in cgroup.splitlines()
                  if re.search(pattern, line)), None)
    if match is None:
        raise ValueError("Bus owner is not a known Chromium Flatpak scope")
    app, instance = match.groups()
    directory = runtime / ".flatpak" / instance
    if directory.stat().st_uid != os.getuid():
        raise ValueError("Wrong instance owner")
    info = configparser.ConfigParser(interpolation=None)
    info.read(directory / "info")
    if info.get("Application", "name", fallback="") != app:
        raise ValueError("Flatpak instance identity mismatch")
    sandbox_pid = int((directory / "pid").read_text())
    if (proc_dir / str(sandbox_pid) / "cgroup").read_text() != cgroup:
        raise ValueError("Sandbox and D-Bus proxy are not in the same scope")
    return app, instance


def read_instance_artwork(instance, path, command=None):
    if command is None:
        if Path("/run/.containerenv").exists() and shutil.which("flatpak-spawn"):
            command = ["flatpak-spawn", "--host", "flatpak"]
        elif shutil.which("flatpak"):
            command = ["flatpak"]
        else:
            raise ValueError("Flatpak artwork helper is unavailable")
    # No shell. Only a bounded read of the exact advertised temporary file.
    result = subprocess.run(command + ["enter", instance, "/usr/bin/head", "-c",
                            str(MAX_ARTWORK + 1), "--", path],
                            capture_output=True, timeout=3, check=True)
    png_dimensions(result.stdout)
    return result.stdout


def resolve_artwork(connection, name, uri):
    from gi.repository import Gio, GLib

    path = artwork_path(uri)
    if not re.fullmatch(r"org\.mpris\.MediaPlayer2\.[A-Za-z0-9_.-]+", name):
        raise ValueError("Invalid MPRIS name")

    def bus_call(method, value):
        return connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", method, GLib.Variant("(s)", (value,)),
            None, Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]

    owner = bus_call("GetNameOwner", name)
    pid = bus_call("GetConnectionUnixProcessID", owner)
    metadata = connection.call_sync(owner, "/org/mpris/MediaPlayer2",
        "org.freedesktop.DBus.Properties", "Get",
        GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "Metadata")),
        None, Gio.DBusCallFlags.NONE, 1500, None).unpack()[0]
    while isinstance(metadata, GLib.Variant):
        metadata = metadata.unpack()
    advertised = metadata.get("mpris:artUrl")
    while isinstance(advertised, GLib.Variant):
        advertised = advertised.unpack()
    if advertised != uri:
        raise ValueError("Artwork changed or was not advertised by this player")
    app, instance = locate_instance(pid)
    expected_prefix = "/tmp/." + app + "."
    if not path.startswith(expected_prefix):
        raise ValueError("Artwork prefix does not match the Flatpak application")
    data = read_instance_artwork(instance, path)
    if bus_call("GetNameOwner", name) != owner:
        raise ValueError("MPRIS owner changed while reading artwork")
    return data
