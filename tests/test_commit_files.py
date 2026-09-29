"""
Tests for the files of one commit, and for getting old versions back.

What has to hold, each with its own way of going wrong quietly:

* **The list is what the commit changed.** Added, changed, deleted and renamed
  files each show up once, with their line counts, and a merge is read against
  its first parent, the same way its diff is shown.
* **A download never touches the project** and never mixes two downloads: it
  goes into a new folder named after project and commit, with the paths the
  files have in the project, so two files of the same name cannot collide.
* **"The version in this commit"** is the file after the commit. A file the
  commit deleted has none, so the version just before is used.
* **Replacing writes the working tree only.** Nothing is staged or committed,
  so the result is an ordinary change the changes list shows. A name with
  ``*`` in it replaces that file and no other.
* **What would be lost is found before asking**: uncommitted edits, and files
  on disk git does not track.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from gitops import snapshot
from gitops.history import read_history
from gitops.status import CHANGE_ADDED, CHANGE_DELETED, CHANGE_MODIFIED, CHANGE_RENAMED
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


def two_commits(repo) -> tuple[str, str]:  # noqa: ANN001 - TempRepo
    """
    Builds a first commit with three files and a second that changes all of them.

    Args:
        repo: The fixture.

    Returns:
        tuple[str, str]: Object ids of the first and the second commit.
    """

    repo.write("a.txt", "one\n")
    repo.write("dir/tool.sh", "#!/bin/sh\necho hi\n")
    (repo.root / "dir" / "tool.sh").chmod(0o755)
    repo.write("gone.txt", "bye\n")
    first = repo.commit("first")
    repo.write("a.txt", "two\nthree\n")
    repo.git("rm", "-q", "gone.txt")
    repo.git("mv", "dir/tool.sh", "dir/run.sh")
    repo.write_bytes("logo.bin", b"\x00\x01\x02")
    second = repo.commit("second")
    return first, second


@requires_git
class ListingTests(unittest.TestCase):
    """
    Which files a commit touched.
    """

    def test_every_kind_of_change_is_listed_once(self) -> None:
        with temp_repo() as repo:
            _first, second = two_commits(repo)
            listing = snapshot.list_files(repo.root, second)
            self.assertTrue(listing.ok)
            kinds = {item.path: item.kind for item in listing.files}
            self.assertEqual(
                {
                    "a.txt": CHANGE_MODIFIED,
                    "dir/run.sh": CHANGE_RENAMED,
                    "gone.txt": CHANGE_DELETED,
                    "logo.bin": CHANGE_ADDED,
                },
                kinds,
            )

    def test_counts_and_binary_are_carried(self) -> None:
        with temp_repo() as repo:
            _first, second = two_commits(repo)
            files = {item.path: item for item in snapshot.list_files(repo.root, second).files}
            self.assertEqual((2, 1), (files["a.txt"].added, files["a.txt"].removed))
            self.assertTrue(files["logo.bin"].binary)
            self.assertFalse(files["a.txt"].binary)

    def test_a_rename_remembers_where_it_came_from(self) -> None:
        with temp_repo() as repo:
            _first, second = two_commits(repo)
            renamed = next(
                item for item in snapshot.list_files(repo.root, second).files
                if item.kind == CHANGE_RENAMED
            )
            self.assertEqual("dir/tool.sh", renamed.old_path)
            self.assertEqual(snapshot.MODE_EXECUTABLE, renamed.mode)

    def test_the_very_first_commit_can_be_read(self) -> None:
        with temp_repo() as repo:
            root = repo.git("rev-list", "--max-parents=0", "HEAD").stdout.strip()
            listing = snapshot.list_files(repo.root, root)
            self.assertEqual(["README.md"], [item.path for item in listing.files])

    def test_a_merge_is_read_against_its_first_parent(self) -> None:
        with temp_repo() as repo:
            repo.git("checkout", "-q", "-b", "side")
            repo.commit_file("side.txt", "from the side\n", "side work")
            repo.git("checkout", "-q", "main")
            repo.commit_file("main.txt", "on main\n", "main work")
            repo.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
            listing = snapshot.list_files(repo.root, repo.head())
            self.assertEqual(["side.txt"], [item.path for item in listing.files])

    def test_a_bad_revision_is_an_error_not_a_crash(self) -> None:
        with temp_repo() as repo:
            self.assertFalse(snapshot.list_files(repo.root, "--output=/tmp/x").ok)
            self.assertFalse(snapshot.list_files(repo.root, "0" * 40).ok)


class PathTests(unittest.TestCase):
    """
    Paths from git are joined to a local folder only when that stays inside it.
    """

    def test_ordinary_paths_pass(self) -> None:
        for path in ("a.txt", "dir/sub/file.py", "x*y.txt", "with space.md"):
            with self.subTest(path=path):
                self.assertTrue(snapshot.is_safe_path(path))

    def test_escaping_paths_are_refused(self) -> None:
        for path in ("", "../evil", "a/../../b", "/etc/passwd", "C:/x", "a\\b"):
            with self.subTest(path=path):
                self.assertFalse(snapshot.is_safe_path(path))

    def test_the_folder_name_is_valid_everywhere(self) -> None:
        self.assertEqual("my_proj-abcdef1", snapshot.folder_name("my/proj", "abcdef1234"))
        self.assertEqual("a_b_c-1234567", snapshot.folder_name('a:b?c', "1234567890"))
        self.assertEqual("branchly-1234567", snapshot.folder_name("..", "1234567890"))


@requires_git
class ExportTests(unittest.TestCase):
    """
    Saving old versions somewhere else.
    """

    def test_files_keep_their_paths_in_a_folder_named_after_the_commit(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            first, _second = two_commits(repo)
            files = snapshot.list_files(repo.root, first).files
            outcome = snapshot.export(repo.root, first, files, target, "demo")
            self.assertEqual(Path(target) / f"demo-{first[:7]}", outcome.folder)
            self.assertEqual("one\n", (outcome.folder / "a.txt").read_text())
            self.assertTrue((outcome.folder / "dir" / "tool.sh").is_file())
            self.assertEqual([], outcome.skipped)

    def test_the_project_is_left_alone(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            first, _second = two_commits(repo)
            snapshot.export(
                repo.root, first, snapshot.list_files(repo.root, first).files, target, "demo"
            )
            self.assertEqual("", repo.git("status", "--porcelain").stdout)
            self.assertEqual("two\nthree\n", (repo.root / "a.txt").read_text())

    def test_a_second_download_does_not_overwrite_the_first(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            first, _second = two_commits(repo)
            files = snapshot.list_files(repo.root, first).files
            one = snapshot.export(repo.root, first, files, target, "demo")
            two = snapshot.export(repo.root, first, files, target, "demo")
            self.assertNotEqual(one.folder, two.folder)
            self.assertEqual(f"demo-{first[:7]}-2", two.folder.name)

    def test_a_deleted_file_comes_as_it_was_just_before(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            _first, second = two_commits(repo)
            gone = [
                item for item in snapshot.list_files(repo.root, second).files
                if item.kind == CHANGE_DELETED
            ]
            outcome = snapshot.export(repo.root, second, gone, target, "demo")
            self.assertEqual("bye\n", (outcome.folder / "gone.txt").read_text())

    @unittest.skipIf(os.name == "nt", "file modes are a POSIX matter")
    def test_an_executable_stays_executable(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            first, _second = two_commits(repo)
            outcome = snapshot.export(
                repo.root, first, snapshot.list_files(repo.root, first).files, target, "demo"
            )
            self.assertTrue(os.access(outcome.folder / "dir" / "tool.sh", os.X_OK))
            self.assertFalse(os.access(outcome.folder / "a.txt", os.X_OK))

    def test_binary_content_arrives_unchanged(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            _first, second = two_commits(repo)
            logo = [
                item for item in snapshot.list_files(repo.root, second).files
                if item.path == "logo.bin"
            ]
            outcome = snapshot.export(repo.root, second, logo, target, "demo")
            self.assertEqual(b"\x00\x01\x02", (outcome.folder / "logo.bin").read_bytes())

    def test_links_and_submodules_are_left_out_with_a_reason(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            items = [
                snapshot.CommitFile(path="link", mode=snapshot.MODE_LINK),
                snapshot.CommitFile(path="lib", mode=snapshot.MODE_SUBMODULE),
            ]
            outcome = snapshot.export(repo.root, repo.head(), items, target, "demo")
            self.assertEqual([], outcome.written)
            self.assertEqual(
                {("link", snapshot.ERROR_NOT_A_FILE), ("lib", snapshot.ERROR_NOT_A_FILE)},
                set(outcome.skipped),
            )

    def test_a_path_leading_out_is_never_written(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            outcome = snapshot.export(
                repo.root, repo.head(), [snapshot.CommitFile(path="../escape.txt")], target, "d"
            )
            self.assertEqual([("../escape.txt", snapshot.ERROR_UNSAFE)], outcome.skipped)
            self.assertFalse((Path(target) / "escape.txt").exists())


@requires_git
class RestoreTests(unittest.TestCase):
    """
    Putting an old version back into the project.
    """

    def test_only_the_working_tree_changes(self) -> None:
        with temp_repo() as repo:
            first, _second = two_commits(repo)
            a_file = [
                item for item in snapshot.list_files(repo.root, first).files
                if item.path == "a.txt"
            ]
            outcome = snapshot.restore(repo.root, first, a_file)
            self.assertTrue(outcome.ok)
            self.assertEqual("one\n", (repo.root / "a.txt").read_text())
            # " M": changed on disk, nothing staged, nothing committed.
            self.assertEqual(" M a.txt\n", repo.git("status", "--porcelain").stdout)

    def test_a_file_deleted_by_the_commit_comes_back(self) -> None:
        with temp_repo() as repo:
            _first, second = two_commits(repo)
            gone = [
                item for item in snapshot.list_files(repo.root, second).files
                if item.kind == CHANGE_DELETED
            ]
            self.assertTrue(snapshot.restore(repo.root, second, gone).ok)
            self.assertEqual("bye\n", (repo.root / "gone.txt").read_text())

    def test_a_name_with_a_star_replaces_that_file_only(self) -> None:
        with temp_repo() as repo:
            repo.write("x*y.txt", "star old\n")
            repo.write("xAy.txt", "other old\n")
            old = repo.commit("both")
            repo.write("x*y.txt", "star new\n")
            repo.write("xAy.txt", "other new\n")
            repo.commit("newer")
            outcome = snapshot.restore(repo.root, old, [snapshot.CommitFile(path="x*y.txt")])
            self.assertTrue(outcome.ok)
            self.assertEqual("star old\n", (repo.root / "x*y.txt").read_text())
            self.assertEqual("other new\n", (repo.root / "xAy.txt").read_text())

    def test_one_bad_file_does_not_fail_the_others(self) -> None:
        with temp_repo() as repo:
            first, _second = two_commits(repo)
            items = [
                snapshot.CommitFile(path="a.txt"),
                snapshot.CommitFile(path="not-in-that-commit.txt"),
            ]
            outcome = snapshot.restore(repo.root, first, items)
            self.assertEqual(["a.txt"], outcome.restored)
            self.assertEqual(
                [("not-in-that-commit.txt", snapshot.ERROR_RESTORE)], outcome.failed
            )

    def test_a_submodule_is_refused(self) -> None:
        with temp_repo() as repo:
            outcome = snapshot.restore(
                repo.root, repo.head(), [snapshot.CommitFile(path="lib", mode=snapshot.MODE_SUBMODULE)]
            )
            self.assertEqual([("lib", snapshot.ERROR_NOT_A_FILE)], outcome.failed)


@requires_git
class RiskTests(unittest.TestCase):
    """
    Which files replacing would lose for good.
    """

    def test_uncommitted_edits_and_untracked_files_are_at_risk(self) -> None:
        with temp_repo() as repo:
            _first, _second = two_commits(repo)
            repo.write("a.txt", "my work\n")
            repo.write("gone.txt", "made again by hand\n")
            risky = snapshot.at_risk(repo.root, ["a.txt", "dir/run.sh", "gone.txt"])
            self.assertEqual(["a.txt", "gone.txt"], risky)

    def test_a_clean_project_has_nothing_at_risk(self) -> None:
        with temp_repo() as repo:
            two_commits(repo)
            self.assertEqual([], snapshot.at_risk(repo.root, ["a.txt", "gone.txt"]))


class SettingsTests(unittest.TestCase):
    """
    The last download folder is remembered.
    """

    def test_it_survives_a_round_trip(self) -> None:
        settings = AppSettings(download_folder="/home/x/Downloads")
        again = AppSettings.from_dict(settings.to_dict())
        self.assertEqual("/home/x/Downloads", again.download_folder)

    def test_a_wrong_type_becomes_empty(self) -> None:
        self.assertEqual("", AppSettings(download_folder=5).normalized().download_folder)  # type: ignore[arg-type]


@requires_git
@requires_qt
class DialogTests(unittest.TestCase):
    """
    The window that lists the files and offers the two actions.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _dialog(self, repo, which: int = 0):  # noqa: ANN001, ANN202 - Qt at import time
        """
        Opens the window on one of the fixture's commits.

        Args:
            repo: The fixture, already holding ``two_commits``.
            which: 0 for the newest commit, 1 for the one before.

        Returns:
            CommitFilesDialog: The window, not shown.
        """

        from ui.commit_files_dialog import CommitFilesDialog, ViewOptions

        commit = read_history(repo.root).commits[which]
        dialog = CommitFilesDialog(repo.root, commit, "demo", ViewOptions(mode="unified"))
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_everything_starts_ticked(self) -> None:
        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo)
            self.assertEqual(4, len(dialog.ticked()))
            self.assertEqual(Qt.CheckState.Checked, dialog._select_all.checkState())
            self.assertEqual(
                i18n.t("changes.selected_count", selected=4, total=4), dialog._select_all.text()
            )

    def test_the_box_shows_some_and_switches_all_and_none(self) -> None:
        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo)
            dialog.set_ticked({"a.txt"})
            self.assertEqual(Qt.CheckState.PartiallyChecked, dialog._select_all.checkState())
            dialog._select_all.click()
            self.assertEqual(4, len(dialog.ticked()))
            dialog._select_all.click()
            self.assertEqual([], dialog.ticked())

    def test_nothing_ticked_means_nothing_to_do(self) -> None:
        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo)
            dialog.set_ticked(set())
            self.assertFalse(dialog._download_button.isEnabled())
            self.assertFalse(dialog._restore_button.isEnabled())

    def test_the_file_under_the_cursor_is_shown(self) -> None:
        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo)
            self.assertIn("a.txt", dialog._diff._counts.text())
            dialog._tree.setCurrentItem(dialog._tree.topLevelItem(2))
            self.assertIn("gone.txt", dialog._diff._counts.text())

    def test_the_diff_offers_nothing_about_the_working_tree(self) -> None:
        # "Open file" would open today's file, not the version on screen.
        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo)
            for widget in (dialog._diff._target, dialog._diff._open_button):
                with self.subTest(widget=widget.text() if hasattr(widget, "text") else "target"):
                    self.assertFalse(widget.isVisibleTo(dialog))

    def test_download_writes_and_says_where(self) -> None:
        with temp_repo() as repo, TemporaryDirectory() as target:
            two_commits(repo)
            dialog = self._dialog(repo)
            dialog.set_ticked({"a.txt"})
            outcome = dialog.download_to(target)
            self.assertEqual(["a.txt"], outcome.written)
            self.assertTrue(dialog._notice.isVisibleTo(dialog))
            self.assertEqual(i18n.t("snapshot.saved_one", count=1), dialog._notice._title.text())
            self.assertEqual(target, dialog.download_folder)

    def test_the_question_names_what_would_be_lost_first(self) -> None:
        from ui.commit_files_dialog import RestoreConfirmDialog

        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo)
            confirm = RestoreConfirmDialog(dialog.files, ["logo.bin"], "abc1234")
            self.addCleanup(confirm.deleteLater)
            self.assertTrue(confirm.row_texts[0].startswith("logo.bin"))
            self.assertIn(i18n.t("snapshot.note_risky"), confirm.row_texts[0])

    def test_saying_no_changes_nothing(self) -> None:
        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo, which=1)
            asked: list[list[str]] = []

            def refuse(files, risky) -> bool:  # noqa: ANN001
                asked.append(risky)
                return False

            dialog.confirm_restore = refuse
            repo.write("a.txt", "my work\n")
            dialog._on_restore_clicked()
            self.assertEqual([["a.txt"]], asked)
            self.assertEqual("my work\n", (repo.root / "a.txt").read_text())
            self.assertEqual([], dialog.restored)

    def test_saying_yes_restores_and_closes(self) -> None:
        with temp_repo() as repo:
            two_commits(repo)
            dialog = self._dialog(repo, which=1)
            dialog.confirm_restore = lambda _files, _risky: True
            dialog.set_ticked({"a.txt"})
            dialog._on_restore_clicked()
            self.assertEqual(["a.txt"], dialog.restored)
            self.assertEqual(dialog.DialogCode.Accepted, dialog.result())
            self.assertEqual("one\n", (repo.root / "a.txt").read_text())


@requires_git
@requires_qt
class GraphTests(unittest.TestCase):
    """
    The way into the window from the graph.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def test_a_double_click_asks_for_the_files(self) -> None:
        from ui.graph_view import GraphView

        with temp_repo() as repo:
            two_commits(repo)
            graph = GraphView()
            self.addCleanup(graph.deleteLater)
            graph.set_history(read_history(repo.root))
            asked: list[str] = []
            graph.files_requested.connect(asked.append)
            graph._on_double_clicked(graph._tree.topLevelItem(0), 1)
            self.assertEqual([repo.head()], asked)
            self.assertEqual("second", graph.commit_of(repo.head()).subject)
            self.assertIsNone(graph.commit_of("0" * 40))

    def test_the_main_window_restores_and_shows_the_changes(self) -> None:
        import ui.main_window as module
        from ui.main_window import TAB_CHANGES, TAB_GRAPH, MainWindow

        with TemporaryDirectory() as base, temp_repo() as repo:
            two_commits(repo)
            os.environ["XDG_CONFIG_HOME"] = str(Path(base) / "config")
            window = MainWindow(
                AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
            )
            self.addCleanup(window.close)
            _outcome, entry = window._registry.add(repo.root)
            window._sidebar.refresh()
            window._activate(entry)
            window._switch_scan_timer.stop()
            window._tabs.setCurrentIndex(TAB_GRAPH)

            class Stub:
                """Stands in for the window, so nothing modal opens."""

                def __init__(self, *_args: object) -> None:
                    self.download_folder = "/tmp/somewhere"
                    self.restored = ["a.txt"]

                def exec(self) -> int:
                    (repo.root / "a.txt").write_text("one\n", encoding="utf-8")
                    return 1

            real = module.CommitFilesDialog
            module.CommitFilesDialog = Stub
            self.addCleanup(setattr, module, "CommitFilesDialog", real)

            window._show_commit_files(repo.head())
            self.assertEqual(TAB_CHANGES, window._tabs.currentIndex())
            self.assertEqual("/tmp/somewhere", window._settings.download_folder)
            self.assertIn("a.txt", [item.path for item in window._state.files])


if __name__ == "__main__":
    unittest.main()
