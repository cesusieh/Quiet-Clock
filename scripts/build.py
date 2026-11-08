#!/usr/bin/env python3
"""Build the extension and a local installable zip. Run inside toolbox Code."""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UUID = "quiet-island@local"


def build():
    source = ROOT / "extension"
    output = ROOT / "build" / UUID
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("gschemas.compiled"))
    shutil.copytree(ROOT / "licenses", output / "licenses")
    subprocess.run(["glib-compile-schemas", "--strict", str(output / "schemas")], check=True)
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["uuid"] == UUID
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"{UUID}.shell-extension.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(output))
    print(archive)
    return output


if __name__ == "__main__":
    build()
