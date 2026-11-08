"""Small original synthetic audio and a notification, only for the isolated preview."""
import array
import math
import sys
import time
import wave


def create_audio(path):
    rate = 22050
    duration = 16
    chords = [(261.63, 329.63, 392.00), (220.00, 261.63, 329.63),
              (174.61, 220.00, 261.63), (196.00, 246.94, 293.66)]
    samples = array.array("h")
    for index in range(rate * duration):
        t = index / rate
        chord = chords[int(t // 4) % len(chords)]
        beat = t % 4
        envelope = min(1.0, beat / 0.08) * min(1.0, (4 - beat) / 0.15)
        value = sum(math.sin(2 * math.pi * frequency * t) for frequency in chord) / 3
        samples.append(int(value * envelope * 6500))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.tobytes())


def notify():
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for _ in range(80):
        try:
            # Wait for the Shell to own Notifications; never launch a second daemon.
            result = bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)", (
                    "Quiet Island · demonstração", 0, "audio-x-generic-symbolic",
                    "Música de demonstração pronta",
                    "Uma faixa sintética está tocando em loop. Teste pausa, progresso e o menu do relógio.",
                    [], {"suppress-sound": GLib.Variant("b", True)}, 0)),
                None, Gio.DBusCallFlags.NO_AUTO_START, 1000, None)
            print("Demo notification:", result.unpack()[0], flush=True)
            return
        except GLib.Error:
            time.sleep(0.25)
    print("Notification demo: Shell notification service did not become ready", file=sys.stderr)
