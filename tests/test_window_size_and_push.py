"""
Tests for two failures that had nothing to do with each other and one thing in
common: both only show up outside a test window.

**The first push of a branch.** ``git push --set-upstream origin`` reads as
"send this branch and remember where it went", and it is not. With the default
``push.default = simple`` git refuses outright, because a branch with no
upstream leaves it no refspec to work from. Every branch's first push therefore
came back as "Git reported a problem".

**The window's minimum width.** Rows of buttons and drop-downs in plain
horizontal layouts added up to a minimum width of nearly two thousand pixels.
That is not only awkward to drag: a window manager reads the size hints, sees a
window that cannot be resized, and takes the maximise button away. The window
had lost a title-bar button to a layout decision.

Both are checked by measuring rather than by reading the code, because in both
cases the code looked right.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from gitops import remote as remote_mod
from gitops.status import read_state
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

# Room to spare on the smallest screen Branchly is likely to meet. A window that
# cannot go below this loses its maximise button on a 1024 wide display, and on
# a 1920 one it can no longer be tiled to half the screen.
MAX_ALLOWED_MINIMUM_WIDTH = 1024


def sandbox_env(root: Path) -> dict[str, str]:
    """
    Builds a git environment that touches nothing outside the sandbox.

    Args:
        root: Directory the configuration may live in.

    Returns:
        dict[str, str]: Environment for the git calls.
    """

    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": str(root / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(root / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
    )
    return environment


@requires_git
class FirstPushTests(unittest.TestCase):
    """
    Sending a branch the server has never seen.
    """

    def _pair(self, base: Path) -> tuple[Path, Path]:
        """
        Builds a bare repository and a working tree pointed at it.

        Args:
            base: Directory to build them in.

        Returns:
            tuple[Path, Path]: The working tree and the bare repository.
        """

        server = base / "server.git"
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(server)],
            check=True,
            capture_output=True,
            env=sandbox_env(base),
        )
        work = base / "work"
        work.mkdir()
        environment = sandbox_env(base)

        def git(*args: str) -> None:
            subprocess.run(
                ["git", *args], cwd=work, env=environment, check=True, capture_output=True
            )

        git("init", "-b", "main")
        (work / "a.txt").write_text("one\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-m", "first")
        git("remote", "add", "origin", str(server))
        return work, server

    def test_a_branch_with_no_upstream_is_sent_and_remembered(self) -> None:
        with TemporaryDirectory() as base:
            work, _server = self._pair(Path(base))
            state = read_state(work)
            self.assertEqual("", state.upstream, "the test needs a branch with no upstream")

            result = remote_mod.push(work, set_upstream=True)
            self.assertTrue(result.ok, result.stderr)
            self.assertEqual("origin/main", read_state(work).upstream)

    def test_the_branch_name_is_put_on_the_command_line(self) -> None:
        # Without it git answers "The current branch main has no upstream
        # branch" and refuses, which is the whole bug.
        with TemporaryDirectory() as base:
            work, _server = self._pair(Path(base))
            captured: list[list[str]] = []
            real_run = remote_mod.run

            def spy(args, **kwargs):  # noqa: ANN001, ANN003, ANN202 - passthrough
                captured.append(list(args))
                return real_run(args, **kwargs)

            remote_mod.run = spy
            self.addCleanup(setattr, remote_mod, "run", real_run)

            remote_mod.push(work, set_upstream=True)
            self.assertIn(["push", "--set-upstream", "origin", "main"], captured)

    def test_a_branch_name_with_a_slash_survives(self) -> None:
        with TemporaryDirectory() as base:
            work, _server = self._pair(Path(base))
            subprocess.run(
                ["git", "checkout", "-b", "feature/topdesk"],
                cwd=work,
                env=sandbox_env(Path(base)),
                check=True,
                capture_output=True,
            )

            result = remote_mod.push(work, set_upstream=True)
            self.assertTrue(result.ok, result.stderr)
            self.assertEqual("origin/feature/topdesk", read_state(work).upstream)

    def test_a_detached_head_is_refused_in_words(self) -> None:
        with TemporaryDirectory() as base:
            work, _server = self._pair(Path(base))
            subprocess.run(
                ["git", "checkout", "--detach"],
                cwd=work,
                env=sandbox_env(Path(base)),
                check=True,
                capture_output=True,
            )

            result = remote_mod.push(work, set_upstream=True)
            self.assertFalse(result.ok)
            # Not git's wall of advice, a key the window can turn into a sentence.
            self.assertEqual("sync.detached", result.error_key())

    def test_an_explicit_branch_is_left_alone(self) -> None:
        with TemporaryDirectory() as base:
            work, _server = self._pair(Path(base))
            result = remote_mod.push(work, branch="main", set_upstream=True)
            self.assertTrue(result.ok, result.stderr)

    def test_a_second_push_does_not_name_the_branch(self) -> None:
        # With an upstream in place git follows the tracking configuration, which
        # may point at a remote branch that is not called the same thing.
        with TemporaryDirectory() as base:
            work, _server = self._pair(Path(base))
            remote_mod.push(work, set_upstream=True)

            captured: list[list[str]] = []
            real_run = remote_mod.run

            def spy(args, **kwargs):  # noqa: ANN001, ANN003, ANN202 - passthrough
                captured.append(list(args))
                return real_run(args, **kwargs)

            remote_mod.run = spy
            self.addCleanup(setattr, remote_mod, "run", real_run)

            remote_mod.push(work, set_upstream=False)
            self.assertIn(["push", "origin"], captured)


class PushErrorWordingTests(unittest.TestCase):
    """
    Turning git's answers into something the user can act on.
    """

    def _result(self, stderr: str):  # noqa: ANN202 - Qt-free helper
        """
        Builds a failed result carrying one git message.

        Args:
            stderr: What git wrote.

        Returns:
            GitResult: The failed result.
        """

        from gitops.runner import GitResult

        return GitResult(returncode=1, stdout="", stderr=stderr, args=("push",))

    def test_a_missing_upstream_gets_its_own_wording(self) -> None:
        message = "fatal: The current branch main has no upstream branch."
        self.assertEqual("sync.no_upstream", self._result(message).error_key())

    def test_a_rejected_push_still_wins_over_it(self) -> None:
        message = "! [rejected] main -> main (non-fast-forward)"
        self.assertEqual("sync.push_rejected", self._result(message).error_key())

    def test_both_wordings_exist_in_both_languages(self) -> None:
        for language in ("en", "de"):
            i18n.set_language(language)
            for key in (
                "sync.no_upstream",
                "sync.no_upstream_hint",
                "sync.detached",
                "sync.detached_hint",
            ):
                with self.subTest(language=language, key=key):
                    self.assertNotEqual(key, i18n.t(key))
        i18n.set_language("en")


@requires_qt
class WindowCanBeResizedTests(unittest.TestCase):
    """
    The window has to fit on a screen, or the window manager takes its buttons.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, base: Path, language: str):  # noqa: ANN202 - Qt at import time
        """
        Builds a window with no projects in it.

        Args:
            base: Directory to keep the config in.
            language: Language to build the labels in.

        Returns:
            MainWindow: The window.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        i18n.set_language(language)
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language=language)
        )
        self.addCleanup(window.close)
        return window

    def test_the_window_fits_on_a_small_screen(self) -> None:
        # German is the wide case: every label is longer, and it was German that
        # pushed the minimum past the width of the screen.
        for language in ("de", "en"):
            with TemporaryDirectory() as base:
                window = self._window(Path(base), language)
                width = window.minimumSizeHint().width()
                with self.subTest(language=language):
                    self.assertLessEqual(width, MAX_ALLOWED_MINIMUM_WIDTH, f"{width}px")

    def test_it_really_becomes_that_narrow(self) -> None:
        with TemporaryDirectory() as base:
            window = self._window(Path(base), "de")
            window.show()
            window.resize(MAX_ALLOWED_MINIMUM_WIDTH, 700)
            self.app.processEvents()
            self.assertLessEqual(window.width(), MAX_ALLOWED_MINIMUM_WIDTH)

    def test_no_panel_on_its_own_pins_the_window_open(self) -> None:
        from PySide6.QtWidgets import QWidget

        with TemporaryDirectory() as base:
            window = self._window(Path(base), "de")
            oversized = [
                f"{type(child).__name__}({child.objectName()}) {child.minimumSizeHint().width()}px"
                for child in window.findChildren(QWidget)
                if child.objectName() == "PanelHeader"
                and child.minimumSizeHint().width() > 500
            ]
            # A header is a row of controls. One that refuses to be narrow is how
            # the whole window came to refuse it.
            self.assertEqual([], oversized)


@requires_qt
class FlowLayoutTests(unittest.TestCase):
    """
    The layout the headers lean on.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _row(self, widths: tuple[int, ...]):  # noqa: ANN202 - Qt at import time
        """
        Builds a flow row of fixed-width boxes.

        Args:
            widths: Width of each box.

        Returns:
            tuple: The container and its layout.
        """

        from PySide6.QtWidgets import QWidget

        from ui.widgets import FlowLayout

        holder = QWidget()
        self.addCleanup(holder.deleteLater)
        layout = FlowLayout(holder, spacing=8)
        layout.setContentsMargins(0, 0, 0, 0)
        for width in widths:
            box = QWidget(holder)
            box.setFixedSize(width, 20)
            layout.addWidget(box)
        return holder, layout

    def test_it_asks_for_one_row_and_settles_for_the_widest_item(self) -> None:
        # The hint has to be wider than the minimum. A layout whose hint is its
        # minimum stacks its buttons vertically even in a window with room.
        _holder, layout = self._row((100, 120, 140))
        self.assertEqual(100 + 120 + 140 + 16, layout.sizeHint().width())
        self.assertEqual(140, layout.minimumSize().width())

    def test_everything_fits_on_one_row_at_exactly_the_hinted_width(self) -> None:
        # Off by one here and the last item wraps in a window that had the space.
        from PySide6.QtCore import QRect

        holder, layout = self._row((100, 120, 140))
        width = layout.sizeHint().width()
        layout.setGeometry(QRect(0, 0, width, 40))
        self.assertEqual([0, 0, 0], [layout.itemAt(i).geometry().y() for i in range(3)])
        holder.deleteLater()

    def test_it_wraps_once_the_row_is_too_narrow(self) -> None:
        from PySide6.QtCore import QRect

        _holder, layout = self._row((100, 120, 140))
        layout.setGeometry(QRect(0, 0, 240, 100))
        tops = [layout.itemAt(index).geometry().y() for index in range(3)]
        self.assertEqual(tops[0], tops[1])
        self.assertGreater(tops[2], tops[1])


if __name__ == "__main__":
    unittest.main()
