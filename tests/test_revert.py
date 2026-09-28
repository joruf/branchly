"""
Tests for throwing uncommitted work away.

This is the only thing Branchly does that nothing can undo. A commit survives in
the reflog, a deleted branch too, a discarded change does not: no copy of it
exists anywhere. So the tests are less about "does it work" and more about two
properties that decide whether the user can give informed consent.

**The plan has to be exact.** What the dialog lists is what the user agrees to.
A file shown as "goes back to the last saved version" that is actually deleted
would be a lie with no way back from it, and the two cases genuinely differ:

* A tracked file has a committed version to return to. The file stays.
* An untracked file has none. It is deleted, and that is final.
* A file only *added* to the next commit is the second case wearing the first
  one's clothes: dropping it from the commit leaves an untracked file, which
  then has to go too, or "revert everything" would leave it behind.

**It has to leave nothing behind.** Restoring only the working tree would leave
a staged change in place, and the file would still be listed as changed right
after somebody asked for it not to be.
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

from gitops.status import read_state
from services import revert as revert_mod
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def sandbox(root: Path) -> dict[str, str]:
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


def build_repo(root: Path) -> Path:
    """
    Creates a repository holding one of every kind of change at once.

    Args:
        root: Folder to build in.

    Returns:
        Path: The working tree.
    """

    repo = root / "work"
    repo.mkdir(parents=True)
    environment = sandbox(root)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=environment, check=True, capture_output=True)

    (repo / "edited.txt").write_text("original\n", encoding="utf-8")
    (repo / "removed.txt").write_text("there\n", encoding="utf-8")
    git("init", "-b", "main")
    git("add", "-A")
    git("commit", "-m", "first")

    (repo / "edited.txt").write_text("broken\n", encoding="utf-8")
    (repo / "removed.txt").unlink()
    (repo / "scratch.txt").write_text("never saved\n", encoding="utf-8")
    (repo / "added.txt").write_text("added\n", encoding="utf-8")
    git("add", "added.txt")
    return repo


@requires_git
class PlanTests(unittest.TestCase):
    """
    Working out what would happen to each file.
    """

    def _actions(self, repo: Path, paths: list[str] | None = None) -> dict[str, str]:
        """
        Returns the planned action per path.

        Args:
            repo: Working tree path.
            paths: Restrict the plan to these paths.

        Returns:
            dict[str, str]: Path mapped to its action.
        """

        plan = revert_mod.plan(read_state(repo).files, paths)
        return {item.path: item.action for item in plan.items}

    def test_a_changed_file_goes_back_to_the_committed_version(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            self.assertEqual(revert_mod.ACTION_RESTORE, self._actions(repo)["edited.txt"])

    def test_a_deleted_file_is_brought_back(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            self.assertEqual(revert_mod.ACTION_RESTORE, self._actions(repo)["removed.txt"])

    def test_an_untracked_file_is_deleted(self) -> None:
        # Git holds no copy, so there is nothing to go back to.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            self.assertEqual(revert_mod.ACTION_DELETE, self._actions(repo)["scratch.txt"])

    def test_a_newly_added_file_is_deleted_too(self) -> None:
        # Dropping it from the commit leaves exactly an untracked file, and
        # leaving that behind after "revert everything" would be inexplicable.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            self.assertEqual(revert_mod.ACTION_DELETE, self._actions(repo)["added.txt"])

    def test_the_two_halves_are_counted_apart(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            plan = revert_mod.plan(read_state(repo).files)
            self.assertEqual(2, len(plan.restored))
            self.assertEqual(2, len(plan.deleted))

    def test_a_selection_covers_only_what_was_selected(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            self.assertEqual(["edited.txt"], list(self._actions(repo, ["edited.txt"])))

    def test_a_clean_project_plans_nothing(self) -> None:
        with TemporaryDirectory() as base:
            root = Path(base)
            repo = root / "clean"
            repo.mkdir()
            environment = sandbox(root)
            for args in (["init", "-b", "main"],):
                subprocess.run(
                    ["git", *args], cwd=repo, env=environment, check=True, capture_output=True
                )
            self.assertTrue(revert_mod.plan(read_state(repo).files).is_empty)


@requires_git
class ApplyTests(unittest.TestCase):
    """
    What the working tree looks like afterwards.
    """

    def test_everything_named_in_the_plan_is_carried_out(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            plan = revert_mod.plan(read_state(repo).files)

            outcome = revert_mod.apply(repo, plan)
            self.assertTrue(outcome.ok, outcome.error)
            self.assertEqual("original\n", (repo / "edited.txt").read_text(encoding="utf-8"))
            self.assertTrue((repo / "removed.txt").exists())
            self.assertFalse((repo / "scratch.txt").exists())
            self.assertFalse((repo / "added.txt").exists())

    def test_the_project_is_clean_afterwards(self) -> None:
        # Not "mostly clean": a staged change left behind would put the file
        # straight back into the list the user just emptied.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            revert_mod.apply(repo, revert_mod.plan(read_state(repo).files))
            self.assertTrue(read_state(repo).is_clean)

    def test_a_staged_edit_is_undone_as_well(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            subprocess.run(
                ["git", "add", "edited.txt"],
                cwd=repo,
                env=sandbox(Path(base)),
                check=True,
                capture_output=True,
            )

            revert_mod.apply(repo, revert_mod.plan(read_state(repo).files, ["edited.txt"]))
            self.assertEqual("original\n", (repo / "edited.txt").read_text(encoding="utf-8"))

    def test_files_outside_the_selection_are_left_alone(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            revert_mod.apply(repo, revert_mod.plan(read_state(repo).files, ["edited.txt"]))

            self.assertEqual("original\n", (repo / "edited.txt").read_text(encoding="utf-8"))
            self.assertTrue((repo / "scratch.txt").exists())
            self.assertFalse((repo / "removed.txt").exists())

    def test_an_empty_plan_does_nothing_and_says_so(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            self.assertTrue(revert_mod.apply(repo, revert_mod.RevertPlan()).ok)
            self.assertEqual("broken\n", (repo / "edited.txt").read_text(encoding="utf-8"))


@requires_qt
@requires_git
class DialogTests(unittest.TestCase):
    """
    What the user is shown before agreeing.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, repo: Path, project: str = ""):  # noqa: ANN202 - Qt at import time
        """
        Builds the confirmation for a repository's full revert.

        Args:
            repo: Working tree path.
            project: Project name.

        Returns:
            RevertDialog: The dialog.
        """

        from ui.revert_dialog import RevertDialog

        dialog = RevertDialog(revert_mod.plan(read_state(repo).files), project)
        self.addCleanup(lambda: dialog.done(0))
        return dialog

    def test_every_file_is_listed_by_name(self) -> None:
        # A count alone leaves the user guessing at what they are agreeing to.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            dialog = self._dialog(repo)
            shown = {dialog._list.item(i).text() for i in range(dialog._list.count())}

            self.assertEqual(4, len(shown))
            for name in ("edited.txt", "removed.txt", "scratch.txt", "added.txt"):
                with self.subTest(name=name):
                    self.assertTrue(any(name in line for line in shown))

    def test_each_line_says_what_happens_to_that_file(self) -> None:
        import i18n

        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            dialog = self._dialog(repo)
            lines = [dialog._list.item(i).text() for i in range(dialog._list.count())]

            deleted = [line for line in lines if i18n.t("revert.action_delete") in line]
            restored = [line for line in lines if i18n.t("revert.action_restore") in line]
            self.assertEqual(2, len(deleted))
            self.assertEqual(2, len(restored))

    def test_the_irreversible_half_is_called_out_separately(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            dialog = self._dialog(repo)
            # Bold, so the two kinds cannot be read as one list of names.
            bold = [
                dialog._list.item(index).font().bold()
                for index in range(dialog._list.count())
            ]
            self.assertEqual(2, sum(bold))

    def test_the_window_opens_tall_enough_to_read(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            self.assertGreaterEqual(self._dialog(repo).height(), 340)

    def test_cancel_is_the_default_button(self) -> None:
        # A stray Return key must not throw work away.
        from PySide6.QtWidgets import QDialogButtonBox

        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            dialog = self._dialog(repo)
            cancel = dialog._buttons.button(QDialogButtonBox.StandardButton.Cancel)
            self.assertTrue(cancel.isDefault())


@requires_qt
@requires_git
class SetupTests(unittest.TestCase):
    """
    Adding a folder that is not a project yet.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, base: Path):  # noqa: ANN202 - Qt at import time
        """
        Builds a window with no projects.

        Args:
            base: Directory to keep the config in.

        Returns:
            MainWindow: The window.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        )
        self.addCleanup(window.close)
        window._switch_scan_timer.stop()
        return window

    def _answer(self, folder: Path, agree: bool):  # noqa: ANN202 - Qt at import time
        """
        Makes the folder dialog and the question answer themselves.

        Args:
            folder: Folder the file dialog should return.
            agree: Whether the question is answered with yes.

        Returns:
            None
        """

        from PySide6.QtWidgets import QFileDialog, QMessageBox

        real_dir = QFileDialog.getExistingDirectory
        real_question = QMessageBox.question
        answer = (
            QMessageBox.StandardButton.Yes if agree else QMessageBox.StandardButton.No
        )
        QFileDialog.getExistingDirectory = staticmethod(lambda *_a, **_k: str(folder))
        QMessageBox.question = staticmethod(lambda *_a, **_k: answer)
        self.addCleanup(setattr, QFileDialog, "getExistingDirectory", real_dir)
        self.addCleanup(setattr, QMessageBox, "question", real_question)

    def test_a_plain_folder_becomes_a_project(self) -> None:
        with TemporaryDirectory() as base:
            folder = Path(base) / "plain"
            folder.mkdir()
            (folder / "notes.txt").write_text("already here\n", encoding="utf-8")

            window = self._window(Path(base))
            window._link_remote = lambda _key: None
            self._answer(folder, agree=True)

            window._prompt_add_repo()
            self.assertTrue((folder / ".git").is_dir())
            self.assertTrue(any(e.path == folder for e in window._registry.entries))

    def test_nothing_in_the_folder_is_touched(self) -> None:
        with TemporaryDirectory() as base:
            folder = Path(base) / "plain"
            folder.mkdir()
            (folder / "notes.txt").write_text("already here\n", encoding="utf-8")

            window = self._window(Path(base))
            window._link_remote = lambda _key: None
            self._answer(folder, agree=True)

            window._prompt_add_repo()
            self.assertEqual("already here\n", (folder / "notes.txt").read_text(encoding="utf-8"))

    def test_the_address_is_asked_for_next(self) -> None:
        with TemporaryDirectory() as base:
            folder = Path(base) / "plain"
            folder.mkdir()

            window = self._window(Path(base))
            asked: list[str] = []
            window._link_remote = lambda key: asked.append(key)
            self._answer(folder, agree=True)

            window._prompt_add_repo()
            self.assertEqual(1, len(asked))

    def test_saying_no_creates_nothing(self) -> None:
        with TemporaryDirectory() as base:
            folder = Path(base) / "plain"
            folder.mkdir()

            window = self._window(Path(base))
            window._link_remote = lambda _key: None
            self._answer(folder, agree=False)

            window._prompt_add_repo()
            self.assertFalse((folder / ".git").exists())
            self.assertEqual([], list(window._registry.entries))


if __name__ == "__main__":
    unittest.main()
