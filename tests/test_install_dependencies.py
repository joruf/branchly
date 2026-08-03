"""
Tests for the dependency installer API.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import install_dependencies as install_mod


class PinParsingTests(unittest.TestCase):
    def test_reads_exact_pins_and_skips_comments(self) -> None:
        text = "# comment\nPySide6==6.11.1\n\nrequests==2.34.2\n"
        with mock.patch.object(Path, "read_text", return_value=text):
            pins = install_mod.parse_pinned_requirements(Path("fake.txt"))
        self.assertEqual(
            [
                install_mod.PinnedRequirement("PySide6", "6.11.1"),
                install_mod.PinnedRequirement("requests", "2.34.2"),
            ],
            pins,
        )

    def test_real_requirements_file_has_pins(self) -> None:
        pins = install_mod.parse_pinned_requirements()
        names = {pin.name for pin in pins}
        self.assertIn("PySide6", names)
        self.assertTrue(all(pin.version for pin in pins))


class PackageStatusTests(unittest.TestCase):
    def test_missing_venv_marks_everything_as_not_ok(self) -> None:
        with mock.patch.object(install_mod, "parse_pinned_requirements") as parse:
            parse.return_value = [
                install_mod.PinnedRequirement("PySide6", "6.11.1"),
                install_mod.PinnedRequirement("requests", "2.34.2"),
            ]
            statuses = install_mod.probe_package_status(Path("/nonexistent/python"))
        self.assertEqual(2, len(statuses))
        self.assertTrue(all(not status.ok for status in statuses))
        self.assertTrue(all(status.installed_version == "" for status in statuses))

    def test_needs_install_when_venv_missing(self) -> None:
        with mock.patch("install_dependencies.paths.venv_python_path") as venv:
            venv.return_value = Path("/nonexistent/python")
            self.assertTrue(install_mod.needs_install())


class RunInstallTests(unittest.TestCase):
    def test_refuses_without_git(self) -> None:
        lines: list[str] = []
        with mock.patch("install_dependencies.which", return_value=None):
            code = install_mod.run_install(log=lines.append)
        self.assertEqual(2, code)
        self.assertTrue(any("git" in line.lower() for line in lines))

    def test_runs_venv_pip_and_verify(self) -> None:
        lines: list[str] = []
        fake_python = Path("/tmp/branchly-fake-python")
        with mock.patch("install_dependencies.which", return_value="/usr/bin/git"):
            with mock.patch.object(install_mod, "create_venv", return_value=fake_python) as create:
                with mock.patch.object(install_mod, "install_python_packages") as pip:
                    with mock.patch.object(install_mod, "install_system_packages") as system:
                        with mock.patch.object(install_mod, "verify", return_value=True) as verify:
                            code = install_mod.run_install(
                                install_mod.InstallOptions(skip_system=False, assume_yes=True),
                                log=lines.append,
                            )
        self.assertEqual(0, code)
        create.assert_called_once()
        pip.assert_called_once_with(fake_python, log=lines.append)
        system.assert_called_once()
        verify.assert_called_once_with(fake_python, log=lines.append)

    def test_skip_system_does_not_touch_package_manager(self) -> None:
        fake_python = Path("/tmp/branchly-fake-python")
        with mock.patch("install_dependencies.which", return_value="/usr/bin/git"):
            with mock.patch.object(install_mod, "create_venv", return_value=fake_python):
                with mock.patch.object(install_mod, "install_python_packages"):
                    with mock.patch.object(install_mod, "install_system_packages") as system:
                        with mock.patch.object(install_mod, "verify", return_value=True):
                            install_mod.run_install(
                                install_mod.InstallOptions(skip_system=True),
                                log=lambda _line: None,
                            )
        system.assert_not_called()


class CliFlagTests(unittest.TestCase):
    def test_cli_flag_skips_gui(self) -> None:
        with mock.patch.object(install_mod, "run_install", return_value=0) as run_install:
            with mock.patch.object(install_mod, "launch_gui") as gui:
                code = install_mod.main(["--cli", "--skip-system", "--yes"])
        self.assertEqual(0, code)
        run_install.assert_called_once()
        gui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
