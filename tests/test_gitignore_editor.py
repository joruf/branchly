"""
Tests for editing .gitignore and for the menu that leads there.

Three separate things, each with its own way of going wrong quietly:

* **New folders are listed file by file.** By default git folds a folder it has
  never seen into one line, ``folder/``. That line cannot be ticked sensibly,
  compared or ignored one file at a time, and the files in it are exactly what
  somebody wants to see.
* **Saving keeps the file the way it was.** Comments, blank lines between
  groups and the order are the author's; a file written with Windows line
  endings goes back with them, or the next diff shows every line as changed.
* **An unreadable file is never replaced.** It shows as an empty editor, and an
  empty editor saved removes the file. The save refuses in that case whatever
  state the button is in.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QDialogButtonBox

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from gitops import ignore as ignore_mod
from gitops.status import read_state
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def build_repo(root: Path) -> Path:
    """
    Creates a repository with one commit.

    Args:
        root: Folder to build in.

    Returns:
        Path: The working tree.
    """

    repo = root / "work"
    repo.mkdir(parents=True)
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
    for args in (["init", "-b", "main"], ["commit", "--allow-empty", "-m", "first"]):
        subprocess.run(["git", *args], cwd=repo, env=environment, check=True, capture_output=True)
    return repo


@requires_git
class UntrackedFolderTests(unittest.TestCase):
    """
    A folder git has never seen.
    """

    def test_its_files_are_listed_one_by_one(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            (repo / ".githooks").mkdir()
            (repo / ".githooks" / "post-commit").write_text("x\n", encoding="utf-8")
            (repo / ".githooks" / "pre-push").write_text("y\n", encoding="utf-8")

            paths = sorted(change.path for change in read_state(repo).files)
            self.assertEqual([".githooks/post-commit", ".githooks/pre-push"], paths)

    def test_nested_folders_are_opened_too(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            (repo / "a" / "b" / "c").mkdir(parents=True)
            (repo / "a" / "b" / "c" / "deep.txt").write_text("x\n", encoding="utf-8")
            self.assertEqual(["a/b/c/deep.txt"], [c.path for c in read_state(repo).files])

    def test_ignored_files_stay_out(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
            (repo / "build").mkdir()
            (repo / "build" / "out.bin").write_text("x\n", encoding="utf-8")
            self.assertEqual([".gitignore"], [c.path for c in read_state(repo).files])


class FileTests(unittest.TestCase):
    """
    Reading and writing the whole file.
    """

    def test_a_missing_file_reads_as_empty_and_new(self) -> None:
        with TemporaryDirectory() as base:
            loaded = ignore_mod.read_file(Path(base))
            self.assertFalse(loaded.exists)
            self.assertEqual("", loaded.text)

    def test_what_is_typed_is_what_is_written(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            typed = "# build output\n/build/\n\n# logs\n*.log\n"
            self.assertTrue(ignore_mod.write_file(root, typed).ok)
            self.assertEqual(typed, (root / ".gitignore").read_text(encoding="utf-8"))

    def test_trailing_blank_lines_become_one_final_newline(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            ignore_mod.write_file(root, "*.log\n\n\n\n")
            self.assertEqual(b"*.log\n", (root / ".gitignore").read_bytes())

    def test_windows_line_endings_survive_a_round_trip(self) -> None:
        # Otherwise the next diff shows every line of the file as changed.
        with TemporaryDirectory() as base:
            root = Path(base)
            (root / ".gitignore").write_bytes(b"# old\r\n*.log\r\n")
            loaded = ignore_mod.read_file(root)
            self.assertEqual("# old\n*.log\n", loaded.text)
            self.assertEqual("\r\n", loaded.newline)

            ignore_mod.write_file(root, loaded.text + "/build/\n", loaded.newline)
            self.assertEqual(b"# old\r\n*.log\r\n/build/\r\n", (root / ".gitignore").read_bytes())

    def test_emptying_the_editor_removes_the_file(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
            self.assertTrue(ignore_mod.write_file(root, "  \n\n").ok)
            self.assertFalse((root / ".gitignore").exists())

    def test_an_unreadable_file_says_so(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            (root / ".gitignore").write_bytes(b"\xff\xfe\x00broken")
            loaded = ignore_mod.read_file(root)
            self.assertTrue(loaded.exists)
            self.assertEqual("ignore.unreadable", loaded.error_key)


@requires_qt
class DialogTests(unittest.TestCase):
    """
    The editor window.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _dialog(self, root: Path):  # noqa: ANN202 - Qt at import time
        """
        Opens the editor on one folder.

        Args:
            root: Working tree.

        Returns:
            GitignoreDialog: The window.
        """

        from ui.gitignore_dialog import GitignoreDialog

        dialog = GitignoreDialog(root)
        self.addCleanup(lambda: dialog.done(0))
        return dialog

    def _save_button(self, dialog):  # noqa: ANN001, ANN202 - Qt at import time
        """
        Returns the dialog's save button.

        Args:
            dialog: The editor window.

        Returns:
            QPushButton: The button.
        """

        return dialog._buttons.button(QDialogButtonBox.StandardButton.Save)

    def test_it_shows_the_file_as_it_is(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            (root / ".gitignore").write_text("# logs\n*.log\n", encoding="utf-8")
            self.assertEqual("# logs\n*.log\n", self._dialog(root).text)

    def test_saving_waits_for_a_change(self) -> None:
        with TemporaryDirectory() as base:
            dialog = self._dialog(Path(base))
            self.assertFalse(self._save_button(dialog).isEnabled())
            dialog._editor.setPlainText("*.log\n")
            self.assertTrue(self._save_button(dialog).isEnabled())

    def test_saving_writes_the_file_and_reports_it(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            dialog = self._dialog(root)
            dialog._editor.setPlainText("*.log\n")
            dialog._save()
            self.assertTrue(dialog.saved)
            self.assertEqual("*.log\n", (root / ".gitignore").read_text(encoding="utf-8"))

    def test_an_unreadable_file_is_left_alone(self) -> None:
        # It shows as an empty editor, and an empty editor saved removes the file.
        with TemporaryDirectory() as base:
            root = Path(base)
            (root / ".gitignore").write_bytes(b"\xff\xfe\x00broken")
            before = (root / ".gitignore").read_bytes()

            dialog = self._dialog(root)
            self.assertTrue(dialog._editor.isReadOnly())
            self.assertFalse(self._save_button(dialog).isEnabled())

            dialog._save()
            self.assertEqual(before, (root / ".gitignore").read_bytes())
            self.assertFalse(dialog.saved)


@requires_qt
@requires_git
class MenuTests(unittest.TestCase):
    """
    The menu for a click that lands on no file.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, base: Path, changed: bool = True):  # noqa: ANN202 - Qt at import time
        """
        Builds a window on one project.

        Args:
            base: Directory to work in.
            changed: Whether the project should have an uncommitted change.

        Returns:
            MainWindow: The window.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        repo = build_repo(base)
        if changed:
            (repo / "notes.txt").write_text("x\n", encoding="utf-8")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        )
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(repo)
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        return window

    def _entries(self, window) -> list[str]:  # noqa: ANN001
        """
        Returns the labels of the project menu.

        Args:
            window: The main window.

        Returns:
            list[str]: The entries, separators left out.
        """

        return [a.text() for a in window._changes.project_menu().actions() if a.text()]

    def test_editing_gitignore_comes_first(self) -> None:
        with TemporaryDirectory() as base:
            entries = self._entries(self._window(Path(base)))
            self.assertEqual(i18n.t("gitignore.menu"), entries[0])

    def test_the_rest_is_about_the_project_as_a_whole(self) -> None:
        with TemporaryDirectory() as base:
            entries = self._entries(self._window(Path(base)))
            for key in ("repo.open_folder", "action.refresh", "revert.menu_all"):
                with self.subTest(entry=key):
                    self.assertIn(i18n.t(key), entries)

    def test_reverting_is_greyed_out_when_there_is_nothing_to_revert(self) -> None:
        with TemporaryDirectory() as base:
            window = self._window(Path(base), changed=False)
            revert = next(
                a for a in window._changes.project_menu().actions()
                if a.text() == i18n.t("revert.menu_all")
            )
            self.assertFalse(revert.isEnabled())

    def test_a_click_below_the_files_opens_it(self) -> None:
        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            routed: list[object] = []
            window._changes._show_project_menu = lambda at: routed.append(at)

            below = QPoint(5, 10_000)
            self.assertIsNone(window._changes._list.itemAt(below))
            window._changes._show_context_menu(below)
            self.assertEqual(1, len(routed))

    def test_a_clean_project_offers_it_too(self) -> None:
        # With no changes the list is replaced by a note, which is exactly when
        # somebody would want to edit .gitignore.
        from PySide6.QtCore import Qt

        with TemporaryDirectory() as base:
            window = self._window(Path(base), changed=False)
            self.assertEqual(
                Qt.ContextMenuPolicy.CustomContextMenu,
                window._changes._empty.contextMenuPolicy(),
            )

    def test_the_menu_entry_opens_the_editor(self) -> None:
        import ui.main_window as module
        from ui import changes_panel as panel_module

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            opened: list[Path] = []

            class Stub:
                """Stands in for the editor, so nothing modal opens."""

                def __init__(self, repo: Path, *_args: object) -> None:
                    opened.append(repo)
                    self.saved = False

                def exec(self) -> int:
                    return 0

            real = module.GitignoreDialog
            module.GitignoreDialog = Stub
            self.addCleanup(setattr, module, "GitignoreDialog", real)

            window._on_project_action(panel_module.ACTION_EDIT_GITIGNORE)
            self.assertEqual(1, len(opened))


    def _menu_action(self, window):  # noqa: ANN001, ANN202 - Qt at import time
        """
        Finds the entry in the project menu, as the menu shows it.

        Args:
            window: The main window.

        Returns:
            QAction: The entry, after the menu refreshed itself.
        """

        # The menu bar's actions are held on to: PySide lets go of a submenu
        # whose action wrapper was only a temporary.
        entries = window.menuBar().actions()
        holder = next(item for item in entries if item.text() == i18n.t("menu.repository"))
        menu = holder.menu()
        menu.aboutToShow.emit()
        return next(a for a in menu.actions() if a.text() == i18n.t("gitignore.menu"))

    def test_the_project_menu_offers_it_for_the_selected_project(self) -> None:
        import ui.main_window as module

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            action = self._menu_action(window)
            self.assertTrue(action.isEnabled())

            opened: list[Path] = []

            class Stub:
                """Stands in for the editor, so nothing modal opens."""

                def __init__(self, repo: Path, *_args: object) -> None:
                    opened.append(repo)
                    self.saved = False

                def exec(self) -> int:
                    return 0

            real = module.GitignoreDialog
            module.GitignoreDialog = Stub
            self.addCleanup(setattr, module, "GitignoreDialog", real)
            action.trigger()
            self.assertEqual([Path(window._entry.path)], opened)

    def test_without_a_project_it_is_greyed_out(self) -> None:
        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        with TemporaryDirectory() as base:
            os.environ["XDG_CONFIG_HOME"] = str(Path(base) / "config")
            window = MainWindow(
                AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
            )
            self.addCleanup(window.close)
            self.assertIsNone(window._entry)
            self.assertFalse(self._menu_action(window).isEnabled())


if __name__ == "__main__":
    unittest.main()
