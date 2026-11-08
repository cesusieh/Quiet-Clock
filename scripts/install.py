#!/usr/bin/env python3
"""Install files without enabling extensions, services, or agent hooks."""
import argparse
import shlex
import shutil
import subprocess
from pathlib import Path

from build import ROOT, UUID, build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(), help="Alternate staging home")
    parser.add_argument("--container", default="Code", help="Toolbox container for the bridge")
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    extension = home / ".local/share/gnome-shell/extensions" / UUID
    data = home / ".local/share/quiet-island"
    launch = home / ".local/bin/quiet-island"
    systemd = home / ".config/systemd/user/quiet-island.service"
    if extension.exists() and not (extension / ".quiet-island-owned").exists():
        raise SystemExit(f"Existing unmanaged extension: {extension}")
    if data.exists() and not (data / ".quiet-island-owned").exists():
        raise SystemExit(f"Existing unmanaged integration directory: {data}")
    if launch.exists() and "# Quiet Island launcher" not in launch.read_text():
        raise SystemExit(f"Existing unmanaged command: {launch}")
    if systemd.exists() and "# Quiet Island service" not in systemd.read_text():
        raise SystemExit(f"Existing unmanaged service: {systemd}")
    output = build()
    if extension.exists():
        shutil.rmtree(extension)
    extension.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(output, extension)
    (extension / ".quiet-island-owned").touch()
    data.mkdir(parents=True, exist_ok=True)
    for directory in ("bridge", "bin"):
        shutil.copytree(ROOT / directory, data / directory, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    (data / ".quiet-island-owned").touch()
    command = ["/usr/bin/toolbox", "run", "--container", args.container, "/usr/bin/python3",
               str(data / "bin/quiet-island")]
    launch.parent.mkdir(parents=True, exist_ok=True)
    # Codex may itself run in this toolbox. Avoid nesting toolbox/host portals:
    # use its Python directly, preserving stdin and the caller's session bus.
    marker = shlex.quote('name="' + args.container + '"')
    direct = shlex.join(["/usr/bin/python3", str(data / "bin/quiet-island")])
    discover = shlex.join(["/usr/bin/python3", str(data / "bridge/quiet_island/terminal_context.py")])
    launch.write_text("#!/bin/sh\n# Quiet Island launcher\n"
                     "if [ -f /run/.containerenv ] && /usr/bin/grep -Fqx -- " + marker +
                     " /run/.containerenv; then\n    exec " + direct + ' "$@"\nfi\n' +
                     'if [ "$1" = hook ]; then\n    terminal_context=$(' + discover + ')\n    exec ' +
                     shlex.join(command) + ' "$@" --terminal-context "$terminal_context"\nfi\nexec ' +
                     shlex.join(command) + ' "$@"\n')
    launch.chmod(0o755)
    systemd.parent.mkdir(parents=True, exist_ok=True)
    # systemd ExecStart uses its own quoting, not shell expansion.
    exec_start = " ".join('"' + part.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
                          for part in command + ["service"])
    systemd.write_text(
        "# Quiet Island service\n[Unit]\nDescription=Quiet Island local bridge\nAfter=graphical-session.target\n"
        "PartOf=graphical-session.target\n\n[Service]\nType=dbus\n"
        "BusName=org.quietisland.Bridge\nExecStart=" + exec_start +
        "\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=graphical-session.target\n"
    )
    # Generate hook snippets against the installed command, without changing agent configuration.
    subprocess.run(["python3", str(ROOT / "scripts/hooks.py"), "--command", str(launch),
                    "--output", str(data / "hooks")], check=True)
    print("Files installed. No extension, service or hook was enabled.")
    print(f"Extension: {extension}")
    print(f"Hook snippets: {data / 'hooks'}")


if __name__ == "__main__":
    main()
