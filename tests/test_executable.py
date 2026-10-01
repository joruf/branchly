"""
Tests for the single-file executable: its paths, its children, its updates, its build.

The executable itself is never started here; ``build-exe.py`` does that after
every build. These tests pretend to be frozen and check that nothing in that
mode reaches for a venv, pip, git history or a Python interpreter that is not
there, and that nothing is ever written into the unpacked files.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import install_dependencies
import paths
import run
import version
from gitops import askpass, runner
from services import updater

ROOT = Path(__file__).resolve().parent.parent

# The build of the pretend executable, fixed so the comparisons do not depend on
# how many commits this checkout happens to have.
_BUILD = 31
_VERSION = "0.10.2"


class _Response:
    """
    Stand-in for what ``requests.get`` returns.
    """

    def __init__(self, payload: object = None, body: bytes = b"", status_code: int = 200) -> None:
        """
        Args:
            payload: Object returned by ``json()``.
            body: Bytes handed out by ``iter_content``.
            status_code: HTTP status to report.
        """

        self.status_code = status_code
        self._payload = payload
        self._body = body

    def json(self) -> object:
        """
        Returns the parsed body.

        Returns:
            object: The payload.
        """

        return self._payload

    def iter_content(self, chunk_size: int = 1) -> list[bytes]:
        """
        Yields the body in chunks.

        Args:
            chunk_size: Requested chunk size.

        Returns:
            list[bytes]: The body, split into chunks.
        """

        return [self._body[at : at + chunk_size] for at in range(0, len(self._body), chunk_size)]

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def _release(build: int, asset: str = "") -> dict:
    """
    Returns a fake GitHub release answer.

    Args:
        build: Build number in the tag.
        asset: Name of the one asset; defaults to this platform's.

    Returns:
        dict: The release.
    """

    name = asset or paths.executable_name("9.9.9", build)
    return {
        "tag_name": f"v9.9.9-build{build}",
        "name": f"Branchly 9.9.9 ({build})",
        "target_commitish": "f" * 40,
        "assets": [{"name": name, "browser_download_url": "https://example.invalid/" + name}],
    }


def _serve(release: dict, body: bytes = b"", compare: dict | None = None) -> mock._patch:
    """
    Answers the release API with ``release`` and the download with ``body``.

    Args:
        release: What ``releases/latest`` returns.
        body: What the asset download returns.
        compare: What the compare endpoint returns; a 404 when None.

    Returns:
        mock._patch: The patch of ``requests.get``, not yet started.
    """

    def get(url: str, **kwargs: object) -> _Response:
        if url.endswith("/releases/latest"):
            return _Response(payload=release)
        if "/compare/" in url:
            return _Response(payload=compare, status_code=200 if compare else 404)
        return _Response(body=body)

    return mock.patch.object(updater.requests, "get", side_effect=get)


@contextmanager
def _frozen(home: Path) -> Iterator[Path]:
    """
    Pretends to run as the executable.

    Args:
        home: Throwaway folder for the program file and the user's config.

    Yields:
        Path: The (fake) executable.
    """

    program = home / "bin" / paths.executable_name(_VERSION, _BUILD)
    program.parent.mkdir(parents=True)
    program.write_bytes(b"old")
    with mock.patch.object(paths, "IS_FROZEN", True):
        with mock.patch.object(sys, "executable", str(program)):
            with mock.patch.object(paths, "user_config_dir", return_value=home / "config"):
                with mock.patch.object(updater, "APP_BUILD", _BUILD):
                    with mock.patch.object(updater, "APP_VERSION", _VERSION):
                        with mock.patch.object(updater, "_updated_executable", None):
                            yield program


def _build_script():  # noqa: ANN202 - a module loaded from a file name
    """
    Imports ``build-exe.py``, whose name is not a module name.

    Returns:
        module: The build script.
    """

    spec = importlib.util.spec_from_file_location("build_exe", str(ROOT / "build-exe.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PathTests(unittest.TestCase):
    def test_the_executable_finds_its_files_in_the_unpacked_data(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            with mock.patch.object(sys, "frozen", True, create=True):
                with mock.patch.object(sys, "_MEIPASS", base, create=True):
                    self.assertEqual(Path(base), paths.project_root())

    def test_a_checkout_finds_its_files_next_to_run_py(self) -> None:
        self.assertEqual(ROOT, paths.project_root())

    def test_the_executable_is_named_after_its_platform_and_version(self) -> None:
        cases = [
            ("linux", "x86_64", "branchly-linux-x86_64-0.10.2-build31"),
            ("linux", "aarch64", "branchly-linux-aarch64-0.10.2-build31"),
            ("win32", "AMD64", "branchly-windows-x86_64-0.10.2-build31.exe"),
            ("win32", "ARM64", "branchly-windows-aarch64-0.10.2-build31.exe"),
        ]
        for platform_name, machine, expected in cases:
            with self.subTest(platform=platform_name, machine=machine):
                with mock.patch.object(paths.sys, "platform", platform_name):
                    with mock.patch.object(paths.platform, "machine", return_value=machine):
                        self.assertEqual(expected, paths.executable_name("0.10.2", 31))

    def test_the_executable_name_defaults_to_this_version(self) -> None:
        current = version.current()
        self.assertTrue(paths.executable_name().split("-", 3)[-1].startswith(
            f"{current.name}-build{current.build or 0}"
        ))


class VersionTests(unittest.TestCase):
    def test_the_executable_reads_the_bundled_file_and_never_asks_git(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            root = Path(base)
            (root / ".git").mkdir()
            line = "0.10.2 31 d31b7f0 2026-10-01 1790848446\n"
            (root / "VERSION").write_text(line, encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(version.ENVIRONMENT_VARIABLE, None)
                with mock.patch.object(sys, "frozen", True, create=True):
                    with mock.patch.object(version, "_git", side_effect=AssertionError("git at run time")):
                        found = version.current(root)
            self.assertEqual("0.10.2", found.name)
            self.assertEqual("31", found.build)
            self.assertEqual(line, (root / "VERSION").read_text(encoding="utf-8"), "never rewritten")

    def test_without_the_file_the_executable_says_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            (Path(base) / ".git").mkdir()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(version.ENVIRONMENT_VARIABLE, None)
                with mock.patch.object(sys, "frozen", True, create=True):
                    with mock.patch.object(version, "_git", side_effect=AssertionError("git at run time")):
                        self.assertFalse(version.current(Path(base)).is_known)

    def test_version_answers_before_any_toolkit_is_loaded(self) -> None:
        """What the build's smoke test relies on: no display, no Qt."""
        code = (
            "import runpy, sys\n"
            "sys.frozen = True\n"
            f"sys._MEIPASS = {str(ROOT)!r}\n"
            "sys.argv = ['branchly', '--version']\n"
            "try:\n"
            f"    runpy.run_path({str(ROOT / 'run.py')!r}, run_name='__main__')\n"
            "except SystemExit as done:\n"
            "    sys.stderr.write('qt=%s code=%s' % ('PySide6' in sys.modules, done.code))\n"
        )
        environment = dict(os.environ)
        environment[version.ENVIRONMENT_VARIABLE] = "1.2.3 45 abc1234 2026-01-02"
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=environment, check=False,
            cwd=str(ROOT),
        )
        self.assertIn("Branchly 1.2.3 (45)", done.stdout)
        self.assertIn("qt=False code=0", done.stderr)


class ChildEnvironmentTests(unittest.TestCase):
    def _frozen_env(self) -> mock._patch:
        return mock.patch.object(sys, "_MEIPASS", "/tmp/_MEIabc", create=True)

    def test_children_get_no_paths_into_the_unpacked_files(self) -> None:
        env = {
            "LD_LIBRARY_PATH": "/tmp/_MEIabc",
            "LD_LIBRARY_PATH_ORIG": "/opt/lib",
            "QT_PLUGIN_PATH": "/tmp/_MEIabc/PySide6/plugins",
            "XDG_DATA_DIRS": "/tmp/_MEIabc/share:/usr/share",
            "_PYI_ARCHIVE_FILE": "/home/me/branchly",
            "HOME": "/home/me",
        }
        with mock.patch.object(paths, "IS_FROZEN", True), self._frozen_env():
            self.assertEqual(
                {"LD_LIBRARY_PATH": "/opt/lib", "XDG_DATA_DIRS": "/usr/share", "HOME": "/home/me"},
                paths.child_environment(env),
            )

    def test_without_an_original_the_library_path_is_dropped(self) -> None:
        with mock.patch.object(paths, "IS_FROZEN", True), self._frozen_env():
            self.assertNotIn(
                "LD_LIBRARY_PATH", paths.child_environment({"LD_LIBRARY_PATH": "/tmp/_MEIabc"})
            )

    def test_a_checkout_hands_its_environment_on_unchanged(self) -> None:
        env = {"LD_LIBRARY_PATH": "/x", "_PYI_X": "y"}
        self.assertEqual(env, paths.child_environment(env))

    def test_git_runs_without_the_bundled_libraries(self) -> None:
        with mock.patch.object(paths, "IS_FROZEN", True), self._frozen_env():
            with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEIabc"}, clear=False):
                os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
                env = runner.build_environment({"EXTRA": "1"})
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertEqual("0", env["GIT_TERMINAL_PROMPT"])
        self.assertEqual("1", env["EXTRA"])

    def test_every_popen_gets_the_clean_environment(self) -> None:
        with mock.patch.object(subprocess, "Popen", subprocess.Popen):
            with mock.patch.object(paths, "IS_FROZEN", True), self._frozen_env():
                with mock.patch.dict(os.environ, {"BRANCHLY_PROBE": "/tmp/_MEIabc/lib"}, clear=False):
                    paths.use_system_environment_for_children()
                    paths.use_system_environment_for_children()
                    output = subprocess.run(
                        [sys.executable, "-c", "import os; print(os.environ.get('BRANCHLY_PROBE'))"],
                        stdout=subprocess.PIPE,
                        check=True,
                    ).stdout.decode().strip()
                    patched = subprocess.Popen
            self.assertEqual("None", output)
            self.assertFalse(
                getattr(patched.__bases__[0], "_branchly_clean_env", False), "patched only once"
            )

    def test_a_checkout_leaves_popen_alone(self) -> None:
        original = subprocess.Popen
        with mock.patch.object(subprocess, "Popen", original):
            paths.use_system_environment_for_children()
            self.assertIs(original, subprocess.Popen)


class NoInterpreterTests(unittest.TestCase):
    """
    ``sys.executable`` is Branchly itself in the executable: ``-m venv`` or a
    script path would start another Branchly.
    """

    def test_no_re_exec_into_a_venv(self) -> None:
        with mock.patch.object(paths, "IS_FROZEN", True):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(run.REEXEC_MARKER, None)
                with mock.patch("run.os.execve", side_effect=AssertionError("re-exec")):
                    with mock.patch("run.subprocess.run", side_effect=AssertionError("re-exec")):
                        run.reexec_into_venv_if_available()

    def test_a_missing_package_never_opens_the_installer(self) -> None:
        with mock.patch.object(paths, "IS_FROZEN", True):
            with mock.patch.object(
                install_dependencies, "launch_gui", side_effect=AssertionError("installer started")
            ):
                with mock.patch("sys.stderr"):
                    self.assertEqual(3, run._report_missing_dependencies(ImportError("PySide6")))

    def test_the_installer_refuses_to_build_a_venv(self) -> None:
        with mock.patch.object(paths, "IS_FROZEN", True):
            with mock.patch.object(
                install_dependencies, "run_command", side_effect=AssertionError("command started")
            ):
                with mock.patch.object(install_dependencies, "which", return_value="/usr/bin/git"):
                    lines: list[str] = []
                    self.assertEqual(1, install_dependencies.run_install(log=lines.append))
        self.assertTrue(lines)

    def test_the_repair_action_starts_nothing(self) -> None:
        try:
            import installer_ui
        except ImportError:  # pragma: no cover - tkinter missing is a valid environment
            self.skipTest("tkinter is not installed")
        with mock.patch.object(paths, "IS_FROZEN", True):
            with mock.patch.object(installer_ui.subprocess, "Popen", side_effect=AssertionError("started")):
                installer_ui.launch_installer_subprocess()


class AskpassTests(unittest.TestCase):
    def test_the_executable_is_its_own_helper(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            with _frozen(Path(base)) as program:
                self.assertEqual(program.resolve(), askpass.helper_path())
                environment = askpass.environment("x-access-token", "secret-value")
        self.assertEqual(str(program.resolve()), environment["GIT_ASKPASS"])
        self.assertEqual("1", environment[askpass.HELPER_MODE_VARIABLE])

    def test_a_checkout_keeps_the_script(self) -> None:
        environment = askpass.environment("x-access-token", "secret-value")
        self.assertNotIn(askpass.HELPER_MODE_VARIABLE, environment)
        self.assertTrue(environment["GIT_ASKPASS"].endswith(askpass.HELPER_NAME))

    def _answer(self, prompt: str) -> subprocess.CompletedProcess[str]:
        """
        Starts run.py the way git starts the executable as its askpass helper.

        Args:
            prompt: What git asks.

        Returns:
            subprocess.CompletedProcess[str]: The finished helper.
        """

        code = (
            "import runpy, sys\n"
            "sys.frozen = True\n"
            f"sys._MEIPASS = {str(ROOT)!r}\n"
            f"sys.argv = ['branchly', {prompt!r}]\n"
            "try:\n"
            f"    runpy.run_path({str(ROOT / 'run.py')!r}, run_name='__main__')\n"
            "except SystemExit:\n"
            "    sys.stderr.write('qt=%s' % ('PySide6' in sys.modules))\n"
            "    raise\n"
        )
        environment = dict(os.environ)
        environment.update(
            {
                askpass.HELPER_MODE_VARIABLE: "1",
                askpass.USERNAME_VARIABLE: "x-access-token",
                askpass.PASSWORD_VARIABLE: "secret-value",
            }
        )
        return subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=environment, check=False,
            cwd=str(ROOT),
        )

    def test_started_by_git_it_answers_the_password_and_opens_nothing(self) -> None:
        done = self._answer("Password for 'https://x-access-token@github.com': ")
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("secret-value\n", done.stdout)
        self.assertIn("qt=False", done.stderr)

    def test_started_by_git_it_answers_the_username(self) -> None:
        done = self._answer("Username for 'https://github.com': ")
        self.assertEqual("x-access-token\n", done.stdout)


class DesktopEntryTests(unittest.TestCase):
    def _install(self, home: Path) -> str:
        """
        Writes the menu entry the way the executable does.

        Args:
            home: Throwaway home directory.

        Returns:
            str: The entry's text.
        """

        with mock.patch.object(Path, "home", return_value=home):
            with mock.patch.object(install_dependencies.platform, "system", return_value="Linux"):
                with mock.patch.object(install_dependencies, "which", return_value=None):
                    self.assertTrue(install_dependencies.install_desktop_entry(log=lambda line: None))
                    return install_dependencies.desktop_entry_path().read_text(encoding="utf-8")

    def test_the_menu_entry_starts_the_executable(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            with _frozen(Path(base)) as program:
                text = self._install(Path(base) / "home")
                icon = Path(base) / "home" / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
                self.assertTrue((icon / "branchly.png").is_file(), "the icon outlives the unpacked files")
        self.assertIn(f"Exec={program.resolve()} %f", text)
        self.assertNotIn("branchly.sh", text)
        self.assertIn("Icon=branchly\n", text)

    def test_the_first_start_adds_the_entry_and_later_ones_leave_it(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            entry = Path(base) / "branchly.desktop"
            with mock.patch.object(paths, "use_system_environment_for_children") as clean:
                with mock.patch.object(updater, "finish_executable_update") as finish:
                    with mock.patch.object(install_dependencies, "desktop_entry_path", return_value=entry):
                        with mock.patch.object(install_dependencies, "install_desktop_entry") as install:
                            run.start_frozen()
                            entry.write_text("[Desktop Entry]\n", encoding="utf-8")
                            run.start_frozen()
        self.assertEqual(2, clean.call_count)
        self.assertEqual(2, finish.call_count)
        self.assertEqual(1, install.call_count)


class UpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._base = tempfile.TemporaryDirectory()
        self.addCleanup(self._base.cleanup)
        self.base = Path(self._base.name)
        frozen = _frozen(self.base)
        self.program = frozen.__enter__()
        self.addCleanup(frozen.__exit__, None, None, None)
        self.target = self.program.with_name(paths.executable_name("9.9.9", _BUILD + 1))

    def test_the_release_tag_carries_the_build_number(self) -> None:
        self.assertEqual(8, updater.release_build("v2.4.0-build8"))
        self.assertIsNone(updater.release_build("v2.4.0"))
        self.assertIsNone(updater.release_build("nightly"))

    def test_the_workflow_tags_releases_the_way_the_updater_reads_them(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release-exe.yml").read_text(encoding="utf-8")
        self.assertIn('TAG="v${VERSION}-build${BUILD}"', workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertTrue(updater.RELEASE_TAG.match("v0.10.2-build31"))

    def test_a_higher_build_is_an_update_with_its_changes(self) -> None:
        commits = [{"commit": {"message": "Older"}}, {"commit": {"message": "Newer"}}]
        compare = {"ahead_by": 2, "commits": commits}
        with _serve(_release(_BUILD + 1), compare=compare):
            info = updater.check()
        self.assertTrue(info.available and info.known)
        self.assertEqual(f"{_VERSION} ({_BUILD})", info.local)
        self.assertEqual(f"9.9.9 ({_BUILD + 1})", info.remote)
        self.assertEqual(2, info.count)
        self.assertEqual(("Newer", "Older"), info.changes)

    def test_the_same_build_is_current(self) -> None:
        with _serve(_release(_BUILD)):
            info = updater.check()
        self.assertTrue(info.known)
        self.assertFalse(info.available)

    def test_a_release_without_this_platform_is_an_error(self) -> None:
        with _serve(_release(_BUILD + 1, asset="branchly-plan9-mips")):
            info = updater.check()
        self.assertTrue(info.error_key)
        self.assertFalse(info.available)

    def test_the_update_arrives_under_its_own_versioned_name(self) -> None:
        new = b"N" * (2 * 1024 * 1024)
        with _serve(_release(_BUILD + 1), new):
            outcome = updater.apply()
        self.assertTrue(outcome.ok, outcome.detail)
        self.assertEqual(updater.METHOD_EXECUTABLE, outcome.method)
        self.assertEqual(new, self.target.read_bytes())
        self.assertEqual(b"old", self.program.read_bytes(), "the running file is left alone")
        if not paths.is_windows():
            self.assertTrue(self.target.stat().st_mode & 0o111)
        self.assertEqual([str(self.target)], updater.restart_command())

    def test_a_truncated_download_leaves_the_program_alone(self) -> None:
        with _serve(_release(_BUILD + 1), b"<html>error</html>"):
            outcome = updater.apply()
        self.assertFalse(outcome.ok)
        self.assertEqual(b"old", self.program.read_bytes())
        self.assertEqual([self.program], list(self.program.parent.iterdir()))

    def test_the_new_program_removes_the_old_one_and_moves_the_menu_entry(self) -> None:
        with _serve(_release(_BUILD + 1), b"N" * (2 * 1024 * 1024)):
            self.assertTrue(updater.apply().ok)
        entry = self.base / "branchly.desktop"
        entry.write_text(f"[Desktop Entry]\nExec={self.program.resolve()} %f\n", encoding="utf-8")
        with mock.patch.object(sys, "executable", str(self.target)):
            with mock.patch.object(install_dependencies, "desktop_entry_path", return_value=entry):
                with mock.patch.object(install_dependencies, "install_desktop_entry") as install:
                    updater.finish_executable_update()
                    self.assertFalse(self.program.exists())
                    self.assertEqual(1, install.call_count)
                    updater.finish_executable_update()
                    self.assertEqual(1, install.call_count, "done once, not on every start")

    def test_a_menu_entry_of_something_else_is_left_alone(self) -> None:
        with _serve(_release(_BUILD + 1), b"N" * (2 * 1024 * 1024)):
            self.assertTrue(updater.apply().ok)
        entry = self.base / "branchly.desktop"
        entry.write_text("[Desktop Entry]\nExec=/home/me/branchly/branchly.sh %f\n", encoding="utf-8")
        with mock.patch.object(sys, "executable", str(self.target)):
            with mock.patch.object(install_dependencies, "desktop_entry_path", return_value=entry):
                with mock.patch.object(install_dependencies, "install_desktop_entry") as install:
                    updater.finish_executable_update()
        self.assertEqual(0, install.call_count)
        self.assertFalse(self.program.exists())

    def test_the_old_program_started_again_never_deletes_itself(self) -> None:
        with _serve(_release(_BUILD + 1), b"N" * (2 * 1024 * 1024)):
            self.assertTrue(updater.apply().ok)
        with mock.patch.object(install_dependencies, "install_desktop_entry") as install:
            updater.finish_executable_update()
        self.assertTrue(self.program.exists())
        self.assertEqual(0, install.call_count)
        state = json.loads(paths.update_state_path().read_text(encoding="utf-8"))
        self.assertEqual(str(self.program.resolve()), state["replaced_executable"])

    def test_the_restart_starts_a_fresh_executable_without_the_bundled_libraries(self) -> None:
        seen: dict[str, object] = {}

        def execve(program: str, command: list[str], environment: dict[str, str]) -> None:
            seen.update(program=program, command=command, env=environment)

        with mock.patch.object(sys, "_MEIPASS", "/tmp/_MEIabc", create=True):
            with mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/tmp/_MEIabc"}, clear=False):
                os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
                with mock.patch.object(paths, "is_windows", return_value=False):
                    with mock.patch.object(updater.os, "execve", execve):
                        updater.restart()
        self.assertEqual([str(self.program.resolve())], seen["command"])
        environment = seen["env"]
        assert isinstance(environment, dict)
        self.assertEqual("1", environment["PYINSTALLER_RESET_ENVIRONMENT"])
        self.assertNotIn("LD_LIBRARY_PATH", environment)
        self.assertNotIn(updater.REEXEC_MARKER, environment)

    def test_a_remembered_update_is_compared_with_the_build(self) -> None:
        self.assertEqual(f"{_VERSION} ({_BUILD})", updater.installed_revision())


class FrozenUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
            raise unittest.SkipTest("PySide6 is not installed") from None
        cls.app = QApplication.instance() or QApplication([])

    def _repair_button(self, frozen: bool) -> tuple[bool, str]:
        """
        Builds the settings dialog and reads the repair control.

        Args:
            frozen: Whether to pretend to be the executable.

        Returns:
            tuple[bool, str]: Whether the button is enabled, and the hint below it.
        """

        from PySide6.QtWidgets import QLabel, QPushButton

        import i18n
        from config.app_settings import AppSettings
        from github_api.token import TokenState
        from ui.settings_dialog import SettingsDialog

        with mock.patch.object(paths, "IS_FROZEN", frozen):
            with mock.patch("ui.settings_dialog.token_store.state", return_value=TokenState(available=False)):
                dialog = SettingsDialog(AppSettings())
        self.addCleanup(dialog.deleteLater)
        button = next(
            item for item in dialog.findChildren(QPushButton) if item.text() == i18n.t("settings.repair_deps")
        )
        hints = [item.text() for item in dialog.findChildren(QLabel)]
        wanted = i18n.t("settings.repair_deps_frozen" if frozen else "settings.repair_deps_hint")
        return button.isEnabled(), wanted if wanted in hints else ""

    def test_the_repair_action_is_greyed_out_and_explained(self) -> None:
        enabled, hint = self._repair_button(frozen=True)
        self.assertFalse(enabled)
        self.assertTrue(hint)

    def test_a_checkout_keeps_the_repair_action(self) -> None:
        enabled, hint = self._repair_button(frozen=False)
        self.assertTrue(enabled)
        self.assertTrue(hint)

    def test_the_manual_is_copied_out_of_the_unpacked_files(self) -> None:
        from ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as base:
            bundle = Path(base) / "bundle" / "docs"
            (bundle / "screenshots" / "en").mkdir(parents=True)
            (bundle / "MANUAL.en.md").write_text("![x](screenshots/en/a.png)\n", encoding="utf-8")
            (bundle / "screenshots" / "en" / "a.png").write_bytes(b"png")
            with mock.patch.object(paths, "user_cache_dir", return_value=Path(base) / "cache"):
                copy = MainWindow._lasting_manual(bundle / "MANUAL.en.md")
            self.assertEqual(Path(base) / "cache" / "docs" / "MANUAL.en.md", copy)
            self.assertTrue((copy.parent / "screenshots" / "en" / "a.png").is_file())


class BuildScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = _build_script()

    def test_the_build_carries_the_pinned_packages_and_pyinstaller(self) -> None:
        packages = self.build.bundled_packages()
        self.assertTrue(packages[0].startswith("pyinstaller"))
        self.assertIn("PySide6==6.11.1", packages)
        self.assertTrue(any(item.startswith("keyring==") for item in packages))

    def test_the_build_carries_what_the_program_reads_at_run_time(self) -> None:
        files = {item.as_posix() for item in self.build.data_files()}
        for needed in (
            "locales/en.json",
            "locales/de.json",
            "resources/branchly.png",
            "resources/branchly.desktop",
            "resources/branchly-askpass.py",
            "docs/MANUAL.en.md",
            "docs/MANUAL.de.md",
        ):
            self.assertIn(needed, files)
        self.assertTrue(any(item.startswith("docs/screenshots/") for item in files))
        self.assertFalse(any(item.endswith(".py") and not item.startswith("resources/") for item in files))

    def test_the_spec_file_compiles_and_names_the_platform(self) -> None:
        text = self.build.spec_text(self.build.STAGE_DIR)
        compile(text, "branchly.spec", "exec")
        expected = paths.executable_name()
        if expected.endswith(".exe"):
            expected = expected[: -len(".exe")]
        self.assertIn(f"name={expected!r}", text)
        self.assertIn("console=False", text)
        self.assertIn("'keyring.backends'", text)
        self.assertIn("copy_metadata", text)
        self.assertIn("'tkinter'", text)

    def test_the_build_output_is_ignored_by_git(self) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/build/", ignored)
        self.assertIn("/dist/", ignored)


if __name__ == "__main__":
    unittest.main()
