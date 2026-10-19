#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Andy Stewart
"""Install user-owned Nautilus and imv entry points, or restore the originals."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from gi.repository import Gio

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy-file-previewer"
MANIFEST = STATE / "install.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def quoted(value):
    # Desktop Exec and D-Bus Exec both support double-quoted arguments.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".file-previewer.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def install():
    for program in ("imv", "gjs", "hyprctl", "python"):
        if not shutil.which(program):
            raise RuntimeError(f"Missing dependency: {program}")
    if not Path("/usr/share/sushi/org.gnome.NautilusPreviewer.src.gresource").exists():
        raise RuntimeError("Sushi is required for Nautilus and non-image previews")
    state = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    desktop_path = DATA / "applications/imv.desktop"
    # Use the original entry so existing MIME associations, icon and translations
    # survive. Keep its ID: the default app remains imv.desktop.
    old = state.get(str(desktop_path), {})
    if old.get("original") is not None:
        desktop = base64.b64decode(old["original"]).decode()
    else:
        info = Gio.DesktopAppInfo.new("imv.desktop")
        if info is None:
            raise RuntimeError("imv.desktop was not found")
        desktop = Path(info.get_filename()).read_text()
    exec_line = f"Exec={quoted(sys.executable)} {quoted(ROOT / 'viewer.py')} open %F"
    # %% is a literal percent in a desktop Exec argument.
    exec_line = exec_line.replace(str(ROOT), str(ROOT).replace('%', '%%'))
    desktop, count = re.subn(r"^Exec=.*$", lambda _: exec_line, desktop, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError("imv.desktop has no Exec entry")
    service = ("[D-BUS Service]\nName=org.gnome.NautilusPreviewer\n"
               f"Exec=/usr/bin/gjs -m {quoted(ROOT / 'previewer.js')}\n")
    files = {desktop_path: desktop.encode(),
             DATA / "dbus-1/services/org.gnome.NautilusPreviewer.service": service.encode()}
    for path, data in files.items():
        key = str(path)
        current = path.read_bytes() if path.exists() else None
        if key in state:
            if current is not None and digest(current) != state[key]["installed"]:
                raise RuntimeError(f"File was edited after installation; preserving it: {path}")
        else:
            state[key] = {"original": base64.b64encode(current).decode() if current is not None else None}
    for path, data in files.items():
        state[str(path)]["installed"] = digest(data)
        atomic_write(path, data)
    atomic_write(MANIFEST, json.dumps(state, indent=2).encode())
    refresh_desktop_database()
    print("Installed Nautilus image previews and the shared imv launcher.")


def uninstall():
    if not MANIFEST.exists():
        return
    state = json.loads(MANIFEST.read_text())
    remaining = {}
    for name, entry in state.items():
        path = Path(name)
        if path.exists() and digest(path.read_bytes()) != entry["installed"]:
            remaining[name] = entry
            print(f"Preserving a file edited after installation: {path}", file=sys.stderr)
            continue
        if entry["original"] is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, base64.b64decode(entry["original"]))
    if remaining:
        atomic_write(MANIFEST, json.dumps(remaining, indent=2).encode())
    else:
        MANIFEST.unlink()
    refresh_desktop_database()
    print("Restored the previous Nautilus previewer and imv desktop entry.")


def refresh_desktop_database():
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(DATA / "applications")], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    try:
        uninstall() if args.uninstall else install()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"file-previewer: {error}", file=sys.stderr)
        sys.exit(1)
