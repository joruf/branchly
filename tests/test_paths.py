"""
Tests for the cross-platform path helpers.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import paths


class OsFamilyTests(unittest.TestCase):
    def test_family_matches_platform(self) -> None:
        with mock.patch.object(paths.sys, "platform", "linux"):
            self.assertEqual("linux", paths.os_family())
            self.assertTrue(paths.is_linux())
            self.assertFalse(paths.is_windows())
        with mock.patch.object(paths.sys, "platform", "win32"):
            self.assertEqual("windows", paths.os_family())
            self.assertTrue(paths.is_windows())
        with mock.patch.object(paths.sys, "platform", "darwin"):
            self.assertEqual("darwin", paths.os_family())
            self.assertTrue(paths.is_macos())

    def test_unknown_platform_is_passed_through(self) -> None:
        with mock.patch.object(paths.sys, "platform", "freebsd14"):
            self.assertEqual("freebsd14", paths.os_family())


class ConfigDirTests(unittest.TestCase):
    def test_linux_honours_xdg_config_home(self) -> None:
        with mock.patch.object(paths.sys, "platform", "linux"):
            with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg-cfg"}, clear=False):
                self.assertEqual(Path("/tmp/xdg-cfg/branchly"), paths.user_config_dir())

    def test_linux_falls_back_to_dot_config(self) -> None:
        with mock.patch.object(paths.sys, "platform", "linux"):
            env = {key: value for key, value in os.environ.items() if key != "XDG_CONFIG_HOME"}
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(Path.home() / ".config" / "branchly", paths.user_config_dir())

    def test_windows_uses_appdata(self) -> None:
        with mock.patch.object(paths.sys, "platform", "win32"):
            with mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\x\AppData\Roaming"}, clear=False):
                self.assertEqual(Path(r"C:\Users\x\AppData\Roaming") / "branchly", paths.user_config_dir())

    def test_settings_and_registry_sit_in_the_config_dir(self) -> None:
        parent = paths.user_config_dir()
        self.assertEqual(parent, paths.settings_path().parent)
        self.assertEqual(parent, paths.registry_path().parent)
        self.assertEqual("settings.json", paths.settings_path().name)
        self.assertEqual("repos.json", paths.registry_path().name)

    def test_avatar_cache_lives_under_the_cache_dir(self) -> None:
        self.assertEqual(paths.user_cache_dir(), paths.avatar_cache_dir().parent)


class EnsureDirTests(unittest.TestCase):
    def test_creates_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a" / "b" / "c"
            self.assertTrue(paths.ensure_dir(target))
            self.assertTrue(target.is_dir())

    def test_existing_directory_is_fine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(paths.ensure_dir(Path(tmp)))

    def test_failure_returns_false_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "file"
            blocker.write_text("x", encoding="utf-8")
            # A path whose parent is a regular file cannot become a directory.
            self.assertFalse(paths.ensure_dir(blocker / "child"))


class VenvPathTests(unittest.TestCase):
    def test_linux_layout(self) -> None:
        with mock.patch.object(paths.sys, "platform", "linux"):
            self.assertEqual(Path("/proj/.venv/bin/python"), paths.venv_python_path(Path("/proj")))

    def test_windows_layout(self) -> None:
        with mock.patch.object(paths.sys, "platform", "win32"):
            self.assertEqual(Path("/proj/.venv/Scripts/python.exe"), paths.venv_python_path(Path("/proj")))


class ProjectRootTests(unittest.TestCase):
    def test_root_contains_run_py(self) -> None:
        self.assertTrue((paths.project_root() / "run.py").exists())

    def test_default_clone_parent_exists(self) -> None:
        self.assertTrue(paths.default_clone_parent().is_dir())


if __name__ == "__main__":
    unittest.main()
