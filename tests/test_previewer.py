# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Andy Stewart
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("viewer", ROOT / "viewer.py")
viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(viewer)


class ImageListTest(unittest.TestCase):
    def test_directory_navigation_filters_and_sorts_naturally(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name in ("1 中文 space.png", "10.jpg", "2.png", ".hidden.png", "notes.txt"):
                (folder / name).touch()
            first, images = viewer.image_list([(folder / "2.png").as_uri()])
            self.assertEqual(first, folder / "2.png")
            self.assertEqual([p.name for p in images], ["1 中文 space.png", "2.png", "10.jpg"])

    def test_explicit_hidden_image_is_included(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".hidden.png"
            path.touch()
            self.assertEqual(viewer.image_list([str(path)]), (path, [path]))

    def test_multi_selection_preserves_order_and_removes_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "2.png", Path(directory) / "1.png"
            first.touch()
            second.touch()
            self.assertEqual(viewer.image_list([str(first), str(second), str(first)]),
                             (first, [first, second]))

    def test_remote_images_do_not_become_local_paths(self):
        with self.assertRaisesRegex(ValueError, "local"):
            viewer.image_path("smb://example/images/test.png")


class InstallTest(unittest.TestCase):
    def test_reinstall_and_uninstall_preserve_original_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = os.environ | {"XDG_DATA_HOME": str(root / "data"),
                "XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state")}
            desktop = root / "data/applications/imv.desktop"
            service = root / "data/dbus-1/services/org.gnome.NautilusPreviewer.service"
            desktop.parent.mkdir(parents=True)
            service.parent.mkdir(parents=True)
            original = b"[Desktop Entry]\nName=My Image Viewer\nExec=imv %F\nType=Application\nIcon=imv\n# User setting\n"
            original_service = b"[D-BUS Service]\nName=org.gnome.NautilusPreviewer\nExec=/usr/lib/org.gnome.NautilusPreviewer\n"
            desktop.write_bytes(original)
            service.write_bytes(original_service)
            for _ in range(2):
                subprocess.run([sys.executable, str(ROOT / "install.py")], env=env,
                               check=True, capture_output=True)
            self.assertIn("viewer.py", desktop.read_text())
            self.assertIn("# User setting", desktop.read_text())
            self.assertIn("Exec=/usr/bin/gjs -m ", service.read_text())
            subprocess.run([sys.executable, str(ROOT / "install.py"), "--uninstall"],
                           env=env, check=True, capture_output=True)
            self.assertEqual(desktop.read_bytes(), original)
            self.assertEqual(service.read_bytes(), original_service)

    def test_uninstall_preserves_settings_edited_after_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = os.environ | {"XDG_DATA_HOME": str(root / "data"),
                "XDG_CONFIG_HOME": str(root / "config"), "XDG_STATE_HOME": str(root / "state")}
            subprocess.run([sys.executable, str(ROOT / "install.py")], env=env,
                           check=True, capture_output=True)
            desktop = root / "data/applications/imv.desktop"
            edited = desktop.read_bytes() + b"# A new user setting\n"
            desktop.write_bytes(edited)
            subprocess.run([sys.executable, str(ROOT / "install.py"), "--uninstall"],
                           env=env, check=True, capture_output=True)
            self.assertEqual(desktop.read_bytes(), edited)


if __name__ == "__main__":
    unittest.main()
