"""
Tests for taking single blocks out of the next commit.

This is the one feature where a mistake is silent and expensive. Everything on
screen can look right while the commit contains something else entirely, and
nobody finds out until the commit is already pushed. So the tests here are
mostly about what actually ends up in the repository, read back out of git
rather than out of Branchly.

Three things carry the feature and each one can fail on its own:

* **The block's identity.** A selection has to survive the diff being read again
  and has to stop meaning anything once the file underneath has moved. The key
  is built from the four line numbers, which does both by itself.
* **The patch.** Several chosen blocks go into one patch, because their line
  numbers are all counted against the same original. One patch per block would
  land each one against a file the previous patch had already moved.
* **The commit path.** The index is cleared first, so the patch applies against
  the last saved version, and files with no block selection are staged whole.
"""

from __future__ import annotations

import os
import subprocess
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

from gitops import diff as diff_mod
from gitops import stage as stage_mod
from gitops.commit import CommitDraft
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

# Far enough apart that the two edits cannot end up in one block.
FILE_LINES = 40
TOP_LINE = 5
BOTTOM_LINE = 35


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
    Creates a repository with one file changed in two separate places.

    Args:
        root: Folder to initialise.

    Returns:
        Path: The repository root.
    """

    root.mkdir(parents=True, exist_ok=True)
    environment = sandbox(root)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, env=environment, check=True, capture_output=True)

    lines = [f"line {number:02d}" for number in range(1, FILE_LINES + 1)]
    (root / "notes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git("init", "-b", "main")
    git("add", "-A")
    git("commit", "-m", "first")

    lines[TOP_LINE - 1] = "line 05 TOP changed"
    lines[BOTTOM_LINE - 1] = "line 35 BOTTOM changed"
    (root / "notes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def show(repo: Path, revision: str, path: str) -> str:
    """
    Reads a file as one revision has it.

    Args:
        repo: Working tree path.
        revision: Revision to read.
        path: File to read.

    Returns:
        str: The file's contents at that revision.
    """

    done = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo,
        env=sandbox(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout


@requires_git
class HunkIdentityTests(unittest.TestCase):
    """
    What makes one block tellable from another.
    """

    def test_a_change_in_two_places_is_two_blocks(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt")
            self.assertEqual(2, len(diff.hunks))

    def test_every_block_has_its_own_key(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            keys = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            self.assertEqual(len(keys), len(set(keys)))

    def test_the_key_survives_reading_the_diff_again(self) -> None:
        # Without this the selection would be lost on every refresh, and a
        # refresh happens whenever the window is given focus.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            first = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            second = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            self.assertEqual(first, second)

    def test_the_key_changes_when_the_file_does(self) -> None:
        # The other half of the same property: a selection made against an older
        # version of the file must stop matching rather than apply to the wrong
        # lines.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            before = {hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks}

            lines = (repo / "notes.txt").read_text(encoding="utf-8").split("\n")
            lines.insert(0, "a brand new first line")
            (repo / "notes.txt").write_text("\n".join(lines), encoding="utf-8")

            after = {hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks}
            self.assertEqual(set(), before & after)

    def test_blocks_start_out_in_the_commit(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            for hunk in diff_mod.file_diff(repo, "notes.txt").hunks:
                self.assertTrue(hunk.selected)

    def test_a_diff_is_not_selectable_until_somebody_says_so(self) -> None:
        # It depends on what is being compared, which the parser cannot know.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            self.assertFalse(diff_mod.file_diff(repo, "notes.txt").selectable)


@requires_git
class PatchBuildingTests(unittest.TestCase):
    """
    Turning a choice of blocks into something git can apply.
    """

    def test_one_patch_carries_every_chosen_block(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt")
            patch = stage_mod.build_patch(diff, diff.hunks)

            # One file header, two block headers. Two separate patches would each
            # be counted against a file the other had already moved.
            self.assertEqual(1, patch.count("diff --git"))
            self.assertEqual(2, patch.count("@@ -"))

    def test_choosing_one_block_leaves_the_other_out(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt")
            patch = stage_mod.build_patch(diff, [diff.hunks[0]])

            self.assertIn("TOP changed", patch)
            self.assertNotIn("BOTTOM changed", patch)

    def test_choosing_nothing_builds_nothing(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            self.assertEqual("", stage_mod.build_patch(diff_mod.file_diff(repo, "notes.txt"), []))

    def test_the_single_block_helper_still_works(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt")
            self.assertEqual(
                stage_mod.build_patch(diff, [diff.hunks[1]]),
                stage_mod.build_hunk_patch(diff, diff.hunks[1]),
            )

    def test_staging_the_chosen_blocks_puts_only_those_in_the_index(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt")
            diff.hunks[1].selected = False

            result = stage_mod.stage_selected_hunks(repo, diff)
            self.assertTrue(result.ok, result.stderr)

            staged = subprocess.run(
                ["git", "diff", "--cached"],
                cwd=repo,
                env=sandbox(repo),
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("TOP changed", staged)
            self.assertNotIn("BOTTOM changed", staged)

    def test_staging_nothing_is_not_a_failure(self) -> None:
        # "None of this file" is a thing a user can mean.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt")
            for hunk in diff.hunks:
                hunk.selected = False
            self.assertTrue(stage_mod.stage_selected_hunks(repo, diff).ok)


@requires_qt
@requires_git
class PartialCommitTests(unittest.TestCase):
    """
    What ends up in the repository, read back out of git.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, base: Path):  # noqa: ANN202 - Qt at import time
        """
        Builds a window on a repository with two changed places.

        Args:
            base: Directory to work in.

        Returns:
            tuple: The window, the entry and the repository path.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        repo = build_repo(base / "work")
        window = MainWindow(
            AppSettings(auto_check_minutes=0, github_enabled=False, language="en")
        )
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(repo)
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        window._changes._list.setCurrentRow(0)
        return window, entry, repo

    def test_a_deselected_block_stays_out_of_the_commit(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            keys = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            window._on_hunk_toggled(keys[1])

            window._do_commit(CommitDraft(summary="only the top"), ["notes.txt"])

            committed = show(repo, "HEAD", "notes.txt")
            self.assertIn("TOP changed", committed)
            self.assertNotIn("BOTTOM changed", committed)

    def test_what_stayed_out_is_still_on_disk(self) -> None:
        # Leaving a block out of a commit must not throw it away.
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            keys = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            window._on_hunk_toggled(keys[1])

            window._do_commit(CommitDraft(summary="only the top"), ["notes.txt"])

            on_disk = (repo / "notes.txt").read_text(encoding="utf-8")
            self.assertIn("TOP changed", on_disk)
            self.assertIn("BOTTOM changed", on_disk)

    def test_a_file_with_no_choice_made_is_committed_whole(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            window._do_commit(CommitDraft(summary="everything"), ["notes.txt"])

            committed = show(repo, "HEAD", "notes.txt")
            self.assertIn("TOP changed", committed)
            self.assertIn("BOTTOM changed", committed)

    def test_the_tick_goes_half_way(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            keys = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            window._on_hunk_toggled(keys[1])

            item = window._changes._list.item(0)
            self.assertEqual(Qt.CheckState.PartiallyChecked, item.checkState())

    def test_a_half_ticked_file_is_still_part_of_the_commit(self) -> None:
        # Counting it as unticked would silently drop the blocks that were picked.
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            keys = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            window._on_hunk_toggled(keys[1])

            self.assertEqual(["notes.txt"], window._changes.checked_paths())

    def test_taking_every_block_out_empties_the_tick(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            for hunk in diff_mod.file_diff(repo, "notes.txt").hunks:
                window._on_hunk_toggled(hunk.key)

            item = window._changes._list.item(0)
            self.assertEqual(Qt.CheckState.Unchecked, item.checkState())

    def test_clicking_the_tick_drops_the_block_selection(self) -> None:
        # Otherwise the box says one thing and the commit does another.
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            keys = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            window._on_hunk_toggled(keys[1])
            self.assertIn("notes.txt", window._hunk_selection)

            window._on_partial_cleared("notes.txt")
            self.assertNotIn("notes.txt", window._hunk_selection)

    def test_the_selection_is_forgotten_once_it_is_committed(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))
            keys = [hunk.key for hunk in diff_mod.file_diff(repo, "notes.txt").hunks]
            window._on_hunk_toggled(keys[1])

            window._do_commit(CommitDraft(summary="only the top"), ["notes.txt"])
            self.assertNotIn("notes.txt", window._hunk_selection)

    def test_a_key_that_matches_nothing_is_ignored(self) -> None:
        # A file changed outside Branchly leaves keys behind that point at
        # nothing. They must not turn the file into a partial commit.
        with TemporaryDirectory() as base:
            window, _entry, _repo = self._window(Path(base))
            window._hunk_selection["notes.txt"] = {"999:9:999:9"}
            self.assertEqual({}, window._partial_paths())

    def test_the_ticks_are_only_offered_where_they_mean_something(self) -> None:
        with TemporaryDirectory() as base:
            window, _entry, repo = self._window(Path(base))

            parsed = diff_mod.file_diff(repo, "notes.txt")
            window._apply_hunk_selection(parsed)
            self.assertTrue(parsed.selectable)

            # Two commits compared against each other cannot be turned into one.
            window._diff.set_target_choices([diff_mod.TARGET_COMMITS])
            window._diff.set_target(diff_mod.TARGET_COMMITS)
            other = diff_mod.file_diff(repo, "notes.txt")
            window._apply_hunk_selection(other)
            self.assertFalse(other.selectable)


if __name__ == "__main__":
    unittest.main()
