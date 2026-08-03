"""
Tests for the entry point.

The re-exec test earns its place: the obvious way to write that check —
comparing the interpreter paths — silently never fires, because ``.venv/bin/python``
is usually a symlink to the system interpreter and both resolve to the same file.
The environment has to be compared instead.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import run


class ArgumentTests(unittest.TestCase):
    def test_defaults_are_empty(self) -> None:
        args = run.parse_args([])
        self.assertIsNone(args.language)
        self.assertIsNone(args.theme)
        self.assertIsNone(args.repo)

    def test_language_and_theme_are_read(self) -> None:
        args = run.parse_args(["--language", "de", "--theme", "light"])
        self.assertEqual("de", args.language)
        self.assertEqual("light", args.theme)

    def test_unknown_theme_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            run.parse_args(["--theme", "neon"])

    def test_repo_is_read(self) -> None:
        self.assertEqual("/tmp/demo", run.parse_args(["--repo", "/tmp/demo"]).repo)


class PreferenceTests(unittest.TestCase):
    def test_command_line_overrides_stored_values(self) -> None:
        from config.app_settings import AppSettings

        settings = AppSettings(language="en", theme="dark")
        args = run.parse_args(["--language", "de", "--theme", "light"])
        applied = run.apply_preferences(settings, args)
        self.assertEqual("de", applied.language)
        self.assertEqual("light", applied.theme)

    def test_stored_values_are_used_without_overrides(self) -> None:
        from config.app_settings import AppSettings

        settings = AppSettings(language="de", theme="light")
        applied = run.apply_preferences(settings, run.parse_args([]))
        self.assertEqual("de", applied.language)
        self.assertEqual("light", applied.theme)

    def test_unknown_language_falls_back_without_raising(self) -> None:
        from config.app_settings import AppSettings

        applied = run.apply_preferences(AppSettings(), run.parse_args(["--language", "xx"]))
        self.assertIn(applied.language, {"en", "de"})


class ReexecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.venv_python = Path(run._ROOT) / ".venv" / "bin" / "python"

    def test_marker_prevents_a_second_jump(self) -> None:
        with mock.patch.dict(os.environ, {run._REEXEC_MARKER: "1"}, clear=False):
            with mock.patch("run.os.execve") as execve:
                run.reexec_into_venv_if_available()
                execve.assert_not_called()

    def test_nothing_happens_without_a_virtual_environment(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != run._REEXEC_MARKER}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("run.paths.venv_python_path", return_value=Path("/nonexistent/python")):
                with mock.patch("run.os.execve") as execve:
                    run.reexec_into_venv_if_available()
                    execve.assert_not_called()

    def test_already_inside_the_environment_does_not_jump(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != run._REEXEC_MARKER}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("run.paths.venv_python_path", return_value=self.venv_python):
                with mock.patch.object(run.Path, "exists", return_value=True):
                    with mock.patch("run.os.execve") as execve:
                        with mock.patch.object(sys, "prefix", str(Path(run._ROOT) / ".venv")):
                            run.reexec_into_venv_if_available()
                        execve.assert_not_called()

    def test_system_interpreter_jumps_into_the_environment(self) -> None:
        # The regression this guards: a symlinked venv interpreter resolves to the
        # same file as the system one, so only comparing sys.prefix works.
        env = {key: value for key, value in os.environ.items() if key != run._REEXEC_MARKER}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("run.paths.venv_python_path", return_value=self.venv_python):
                with mock.patch("run.paths.is_windows", return_value=False):
                    with mock.patch.object(run.Path, "exists", return_value=True):
                        with mock.patch("run.os.execve") as execve:
                            with mock.patch.object(sys, "prefix", "/usr"):
                                run.reexec_into_venv_if_available()
                            execve.assert_called_once()
                            called_interpreter = execve.call_args.args[0]
                            self.assertIn(".venv", str(called_interpreter))
                            passed_env = execve.call_args.args[2]
                            self.assertEqual("1", passed_env[run._REEXEC_MARKER])

    def test_a_failed_exec_is_not_fatal(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != run._REEXEC_MARKER}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("run.paths.venv_python_path", return_value=self.venv_python):
                with mock.patch("run.paths.is_windows", return_value=False):
                    with mock.patch.object(run.Path, "exists", return_value=True):
                        with mock.patch("run.os.execve", side_effect=OSError("boom")):
                            with mock.patch.object(sys, "prefix", "/usr"):
                                # Returns instead of raising; the import check reports it.
                                run.reexec_into_venv_if_available()


class MissingDependencyTests(unittest.TestCase):
    def test_message_names_the_launcher_when_a_venv_exists(self) -> None:
        with mock.patch("run.paths.venv_python_path", return_value=self.__class__._existing()):
            with mock.patch("run.paths.is_windows", return_value=False):
                with mock.patch("sys.stderr") as stderr:
                    code = run._report_missing_dependencies(ImportError(name="PySide6"))
        self.assertEqual(3, code)
        printed = "".join(str(call.args[0]) for call in stderr.write.call_args_list)
        self.assertIn("branchly.sh", printed)
        self.assertIn("PySide6", printed)

    def test_message_names_the_installer_without_a_venv(self) -> None:
        with mock.patch("run.paths.venv_python_path", return_value=Path("/nonexistent/python")):
            with mock.patch("run.paths.is_windows", return_value=False):
                with mock.patch("sys.stderr") as stderr:
                    run._report_missing_dependencies(ImportError(name="PySide6"))
        printed = "".join(str(call.args[0]) for call in stderr.write.call_args_list)
        self.assertIn("install_dependencies.py", printed)

    @staticmethod
    def _existing() -> Path:
        """
        Returns a path that certainly exists, standing in for the venv interpreter.

        Returns:
            Path: An existing file path.
        """

        return Path(sys.executable)


class GitAvailabilityTests(unittest.TestCase):
    def test_reports_missing_git(self) -> None:
        with mock.patch("run.shutil.which", return_value=None):
            self.assertFalse(run.git_is_available())

    def test_reports_present_git(self) -> None:
        with mock.patch("run.shutil.which", return_value="/usr/bin/git"):
            self.assertTrue(run.git_is_available())


if __name__ == "__main__":
    unittest.main()
