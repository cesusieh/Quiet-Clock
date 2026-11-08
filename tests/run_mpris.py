"""Exercise GJS MPRIS code against a separate fixture process."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not os.environ.get("QUIET_ISLAND_PRIVATE_BUS"):
        raise SystemExit("Run this test with QUIET_ISLAND_PRIVATE_BUS=1 dbus-run-session")
    with tempfile.TemporaryDirectory(prefix="quiet-mpris-test-") as tmp:
        log = Path(tmp) / "calls.json"
        player = subprocess.Popen([sys.executable, str(ROOT / "tests/fixtures/mpris_player.py"), str(log)])
        try:
            subprocess.run(["gjs", "-m", str(ROOT / "tests/mpris.integration.js")], check=True, timeout=15)
            calls = json.loads(log.read_text())
            assert [call["method"] for call in calls] == ["Pause", "SetPosition", "Next", "Play"], calls
        finally:
            player.terminate()
            player.wait(timeout=5)


if __name__ == "__main__":
    main()
