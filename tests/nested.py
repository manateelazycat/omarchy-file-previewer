#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Andy Stewart
"""Exercise real Nautilus/imv in Hyprland nested inside a headless Weston.

Set WESTON_PREFIX to an unpacked Weston /usr prefix, or install Weston. No
application, keyboard event, pointer event or hyprctl command reaches the user's
Wayland socket. Logs and screenshots are retained in test-results/.
"""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "test-results"
APP_ID = "io.github.manateelazycat.file-previewer"


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, capture_output=True,
                          timeout=20, **kwargs).stdout


def wait_for(description, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Timed out: {description}")


def ctl(*args):
    return run(["hyprctl", "--instance", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], *args])


def clients():
    return json.loads(ctl("-j", "clients"))


def active():
    return json.loads(ctl("-j", "activewindow"))


def image_window():
    return next((w for w in clients() if w["class"] == APP_ID), None)


def key(value):
    run(["wtype", "-k", value])
    time.sleep(0.2)


def preview(path, toggle=False, parent=""):
    run(["gdbus", "call", "--session", "--dest", "org.gnome.NautilusPreviewer",
         "--object-path", "/org/gnome/NautilusPreviewer", "--method",
         "org.gnome.NautilusPreviewer2.ShowFile", path.as_uri(), parent,
         "true" if toggle else "false", ""])


def inside():
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    # This private bus was started after the nested environment was prepared.
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] != os.environ.get("FPV_HOST_BUS")
    assert str(runtime / os.environ["WAYLAND_DISPLAY"]) != str(
        Path(os.environ["FPV_HOST_RUNTIME"]) / os.environ.get("FPV_HOST_DISPLAY", ""))
    ctl("reload")
    assert not ctl("configerrors").strip(), ctl("configerrors")
    service_config = runtime / "service"
    service_config.mkdir()
    for name in ("Service.qml", "hyprland.lua"):
        shutil.copy2(ROOT / name, service_config / name)
    shutil.copy2(ROOT / "tests/service.qml", service_config / "shell.qml")
    service_log = (RESULTS / "service.log").open("w")
    service = subprocess.Popen(["quickshell", "-p", str(service_config), "--no-color"],
                               stdout=service_log, stderr=service_log)

    def service_ready():
        if service.poll() is not None:
            raise AssertionError((RESULTS / "service.log").read_text())
        try:
            status = json.loads(run(["quickshell", "-p", str(service_config), "ipc", "call",
                APP_ID, "status"]))
            return status["rulesApplied"]
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            return False

    wait_for("Omarchy service applies focus rules", service_ready)
    ctl("reload")
    wait_for("Omarchy service restores rules after reload", lambda:
        service_ready() and ctl("repl", "type(_G.omarchy_file_previewer)").strip() == "table")
    print("PASS: Omarchy service entry loads and restores rules after config reload", flush=True)
    run([sys.executable, str(ROOT / "install.py")])
    run([sys.executable, str(ROOT / "install.py")])  # idempotent
    # Reproduce an already-installed first version, so updating a running
    # session also proves that its pointer-capturing rule is disabled.
    ctl("eval", '_G.omarchy_file_previewer_rule = hl.window_rule({'
        'match={class="^io.github.manateelazycat.file-previewer$"},stay_focused=true})')
    folder = runtime / "images"
    folder.mkdir()
    for name, color in (("1 中文 space.png", 0xE05040FF),
                        ("2.png", 0x20B050FF), ("10.png", 0x3060E0FF)):
        pixels = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 640, 480)
        pixels.fill(color)
        pixels.savev(str(folder / name), "png", [], [])
    (folder / "note.txt").write_text("Non-image preview still uses Sushi.\n")
    first = folder / "1 中文 space.png"
    nautilus_log = (RESULTS / "nautilus.log").open("w")
    nautilus = subprocess.Popen(["nautilus", "--new-window", str(folder)],
                                stdout=nautilus_log, stderr=nautilus_log)
    wait_for("Nautilus", lambda: next((w for w in clients()
             if w["class"] == "org.gnome.Nautilus"), None))
    run(["nautilus", "--select", str(first)])
    time.sleep(1)
    (RESULTS / "before-space.json").write_text(json.dumps(clients(), indent=2))
    key("space")
    window = wait_for("Space opens imv", image_window)
    assert window["floating"], window
    wait_for("new image gets keyboard focus", lambda: active().get("class") == APP_ID)
    print("PASS: actual Nautilus Space launches and focuses imv", flush=True)
    # Read state through IPC, independent of the desktop's window title format.
    pid = window["pid"]
    state_file = runtime / "image-state"

    def state():
        state_file.unlink(missing_ok=True)
        command = 'exec printf "%s\\n%s\\n%s\\n" "$imv_current_file" "$imv_scale" "$imv_file_count" > ' + shlex.quote(str(state_file))
        run(["imv-msg", str(pid), command])
        wait_for("imv state", lambda: state_file.exists())
        time.sleep(0.1)
        values = state_file.read_text().splitlines()
        return values[0], float(values[1]), int(values[2])

    assert state()[0] == str(first), state()
    assert state()[2] == 3, state()
    key("j")
    assert state()[0] == str(folder / "2.png"), state()
    key("j")
    assert state()[0] == str(folder / "10.png"), state()
    key("k")
    assert state()[0] == str(folder / "2.png"), state()
    key("0")
    assert state()[1] == 100, state()
    key("minus")
    assert state()[1] < 100, state()
    before = state()[1]
    key("equal")
    assert state()[1] > before, state()
    key("0")
    assert state()[1] == 100, state()
    print("PASS: j/k natural image order, -/= zoom and 0 actual size", flush=True)
    pointer = str(RESULTS / "pointer")
    for x, y in ((5, 5), (1270, 950), (100, 600), (640, 480)):
        run([pointer, "move", str(x), str(y)])
        assert active().get("pid") == pid, active()
    print("PASS: moving the pointer outside imv retains focus", flush=True)
    from side_panel import prepare
    panel_root = prepare(runtime)
    if panel_root:
        panel_log = (RESULTS / "side-panel.log").open("w")
        panel = subprocess.Popen(["quickshell", "-p", str(panel_root), "--no-color"],
                                 stdout=panel_log, stderr=panel_log)

        def panel_state():
            if panel.poll() is not None:
                raise AssertionError((RESULTS / "side-panel.log").read_text())
            try:
                return json.loads(run(["quickshell", "-p", str(panel_root),
                    "ipc", "call", "previewerDockTest", "status"]))
            except subprocess.CalledProcessError:
                return None

        wait_for("side-panel layers", panel_state)
        try:
            for side, x in (("left", 0), ("right", 1279)):
                run([pointer, "move", str(x), "480"])
                wait_for(f"{side} edge reveals side-panel over image", lambda: any(
                    surface["shown"] and surface["side"] == side and surface["progress"] == 1
                    for surface in (panel_state() or [])))
                assert active().get("pid") == pid, active()
                # Enter the revealed panel, then leave it and continue typing
                # into imv without clicking the image again.
                run([pointer, "move", "20" if side == "left" else "1260", "480"])
                assert active().get("pid") == pid, active()
                key("j")
                key("k")
                assert state()[0] == str(folder / "2.png"), state()
                run([pointer, "move", "640", "480"])
                wait_for("side-panel hides on departure", lambda: all(
                    not surface["shown"] and surface["progress"] == 0
                    for surface in (panel_state() or [])))
                assert active().get("pid") == pid, active()
            print("PASS: real side-panel reveals at both edges while imv keeps keyboard focus", flush=True)
        finally:
            panel.terminate()
            panel.wait(timeout=5)
    run([pointer, "click", "1150", "850"])
    wait_for("explicit click focuses Nautilus", lambda: active().get("class") == "org.gnome.Nautilus")
    assert ctl("repl", "_G.omarchy_file_previewer.focused").strip() == "false"
    ctl("dispatch", f'hl.dsp.focus({{window="pid:{pid}"}})')
    run([pointer, "move", "100", "600"])
    assert active().get("pid") == pid, active()
    assert ctl("repl", "_G.omarchy_file_previewer_rule == nil").strip() == "true"
    print("PASS: explicit window switching restores normal hover rules; old focus rule migrated", flush=True)
    for direction, delta in (("Left", (-40, 0)), ("Right", (40, 0)),
                             ("Up", (0, -40)), ("Down", (0, 40))):
        before = image_window()["at"]
        key(direction)
        after = image_window()["at"]
        assert after == [before[0] + delta[0], before[1] + delta[1]], (direction, before, after)
        assert active().get("pid") == pid
    print("PASS: arrow keys move the imv window in all four directions", flush=True)
    screenshot = RESULTS / "space-preview.png"
    run(["/usr/bin/grim", str(screenshot)])
    key("Escape")
    wait_for("Escape closes preview", lambda: image_window() is None)
    assert not json.loads(ctl("repl", "_G.omarchy_file_previewer.focused").lower()), "Hover rule remained enabled"
    # Double-click the selected first thumbnail in our fixed-size Nautilus view.
    run([pointer, "doubleclick", "340", "120"])
    wait_for("desktop image activation", image_window)
    wait_for("desktop activation gets focus", lambda: active().get("class") == APP_ID)
    pid = image_window()["pid"]
    key("j")
    assert state()[0] == str(folder / "2.png"), state()
    key("Escape")
    wait_for("desktop image closes", lambda: image_window() is None)
    print("PASS: double-click desktop entry uses the same imv behavior", flush=True)
    preview(first, parent="wayland:test-parent")
    first_preview = wait_for("DBus ShowFile opens image", image_window)
    preview(folder / "2.png")
    wait_for("DBus ShowFile switches images", lambda: image_window() and
             image_window()["pid"] != first_preview["pid"])
    preview(folder / "note.txt")
    wait_for("image-to-text Sushi preview", lambda: next((w for w in clients()
             if w["class"] == "org.gnome.NautilusPreviewer"), None))
    wait_for("image closes on text preview", lambda: image_window() is None)
    preview(first)
    wait_for("text-to-image imv preview", image_window)
    wait_for("Sushi closes on image preview", lambda: not any(w["class"] == "org.gnome.NautilusPreviewer"
             for w in clients()))
    preview(first, toggle=True)
    wait_for("DBus ShowFile toggle closes image", lambda: image_window() is None)
    preview(folder / "note.txt")
    wait_for("non-image Sushi preview", lambda: next((w for w in clients()
             if w["class"] == "org.gnome.NautilusPreviewer"), None))
    run(["gdbus", "call", "--session", "--dest", "org.gnome.NautilusPreviewer",
         "--object-path", "/org/gnome/NautilusPreviewer", "--method",
         "org.gnome.NautilusPreviewer2.Close"])
    print("PASS: D-Bus toggle/Close and original non-image Sushi preview", flush=True)
    assert not ctl("configerrors").strip(), ctl("configerrors")
    run([sys.executable, str(ROOT / "install.py"), "--uninstall"])
    assert not (Path(os.environ["XDG_DATA_HOME"]) / "applications/imv.desktop").exists()
    print("PASS: uninstall restores original entry points", flush=True)


def outer():
    RESULTS.mkdir(exist_ok=True)
    runtime = Path(tempfile.mkdtemp(prefix="fpv"))
    runtime.chmod(0o700)
    prefix = Path(os.environ.get("WESTON_PREFIX", "/usr"))
    weston = prefix / "bin/weston"
    if not weston.exists():
        raise RuntimeError("Set WESTON_PREFIX to an unpacked Weston /usr prefix")
    compat = RESULTS / "weston-compat.so"
    run(["cc", "-shared", "-fPIC", str(ROOT / "tests/weston-compat.c"),
         "-o", str(compat), *shlex.split(run(["pkg-config", "--cflags", "--libs", "wayland-server"])), "-ldl"])
    run(["cc", str(ROOT / "tests/pointer.c"), "-o", str(RESULTS / "pointer"),
         *shlex.split(run(["pkg-config", "--cflags", "--libs", "wayland-client"]))])
    env = os.environ.copy()
    env["FPV_HOST_BUS"] = env.get("DBUS_SESSION_BUS_ADDRESS", "")
    env["FPV_HOST_DISPLAY"] = env.get("WAYLAND_DISPLAY", "")
    env["FPV_HOST_RUNTIME"] = env["XDG_RUNTIME_DIR"]
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE",
                 "DBUS_SESSION_BUS_ADDRESS", "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE"):
        env.pop(name, None)
    env.update(XDG_RUNTIME_DIR=str(runtime), XDG_CONFIG_HOME=str(runtime / "config"),
               XDG_DATA_HOME=str(runtime / "data"), XDG_STATE_HOME=str(runtime / "state"),
               XDG_CACHE_HOME=str(runtime / "cache"), GSETTINGS_BACKEND="memory",
               NO_AT_BRIDGE="1", GTK_A11Y="none",
               LD_LIBRARY_PATH=f"{prefix}/lib:{prefix}/lib/weston", LD_PRELOAD=str(compat),
               WESTON_DATA_DIR=str(prefix / "share/weston"),
               WESTON_MODULE_MAP=";".join(f"{p.name}={p}" for p in (prefix / "lib").glob("libweston-*/*.so")))
    parent_log = (RESULTS / "weston.log").open("w")
    parent = subprocess.Popen([str(weston), "--no-config", "--backend=headless", "--renderer=gl",
        "--shell=" + str(prefix / "lib/weston/kiosk-shell.so"), "--socket=parent", "--idle-time=0",
        "--width=1280", "--height=960", "--fake-seat"], env=env, stdout=parent_log, stderr=parent_log)
    nested = None
    session = None
    try:
        wait_for("headless parent Wayland", lambda: (runtime / "parent").exists())
        env.pop("LD_PRELOAD", None)
        env.pop("LD_LIBRARY_PATH", None)
        env.update(WAYLAND_DISPLAY="parent", AQ_DRM_DEVICES="/dev/null", LIBSEAT_BACKEND="noop")
        config = runtime / "hyprland.lua"
        config.write_text('''hl.monitor({output="",mode="1280x960@60",position="0x0",scale=1})
hl.config({debug={enable_stdout_logs=true},general={gaps_in=0,gaps_out=0},input={follow_mouse=1},animations={enabled=false},misc={disable_hyprland_logo=true,disable_splash_rendering=true},ecosystem={enforce_permissions=false,no_update_news=true,no_donation_nag=true},xwayland={enabled=false}})
''')
        nested_log = (RESULTS / "hyprland.log").open("w")
        nested = subprocess.Popen(["Hyprland", "-c", str(config)], env=env,
                                  stdout=nested_log, stderr=nested_log)
        socket = wait_for("nested Hyprland socket", lambda: next((runtime / "hypr").glob("*/.socket.sock"), None))
        env["HYPRLAND_INSTANCE_SIGNATURE"] = socket.parent.name
        run(["hyprctl", "--instance", socket.parent.name, "output", "create", "headless"], env=env)
        run(["hyprctl", "--instance", socket.parent.name, "output", "remove", "WAYLAND-1"], env=env)
        try:
            monitors = wait_for("nested monitor", lambda: json.loads(run(
                ["hyprctl", "--instance", socket.parent.name, "-j", "monitors"], env=env)))
        except AssertionError:
            (RESULTS / "rolling.log").write_text(run(
                ["hyprctl", "--instance", socket.parent.name, "rollinglog"], env=env))
            raise
        assert monitors, "Nested output is missing"
        instance_lock = runtime / "hypr" / socket.parent.name / "hyprland.lock"
        # The compositor creates wayland-N in our private runtime directory.
        wayland_socket = wait_for("nested Wayland socket", lambda: next((p for p in runtime.glob("wayland-*")
                     if not p.name.endswith(".lock")), None))
        env["WAYLAND_DISPLAY"] = wayland_socket.name
        (RESULTS / "environment.json").write_text(json.dumps({k: env[k] for k in
            ("XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE")}, indent=2))
        imv_config = runtime / "config/imv/config"
        imv_config.parent.mkdir(parents=True)
        imv_config.write_text('[options]\n')
        run([sys.executable, str(ROOT / "install.py")], env=env)
        session = subprocess.Popen(["dbus-run-session", "--", sys.executable,
            str(Path(__file__).resolve()), "--inside"], env=env, start_new_session=True)
        code = session.wait(timeout=90)
        if code:
            raise RuntimeError(f"Nested integration tests failed ({code}); see test-results/")
    finally:
        for log in (runtime / "hypr").glob("*/hyprland.log"):
            shutil.copyfile(log, RESULTS / "hyprland-internal.log")
        if session:
            try:
                os.killpg(session.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for proc in (nested, parent):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        try:
            inside()
        except Exception:
            (RESULTS / "failure-clients.json").write_text(json.dumps(clients(), indent=2))
            run(["/usr/bin/grim", str(RESULTS / "failure.png")])
            raise
    else:
        outer()
