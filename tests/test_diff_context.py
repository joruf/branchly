"""
Tests for showing the unchanged text around a change.

Git's own default is three lines of context, which is enough to place a change
and not enough to read around it. The option switches that to twenty lines above
and below, which is a different thing entirely: the unchanged text stops being a
frame and becomes most of the panel.

Two things follow from that and both are tested. It has to be a fresh reading of
the diff, because the extra lines are simply not in the output Branchly already
has; redrawing what is in memory cannot produce them. And the unchanged lines
have to be drawn more quietly than before, or they compete with the change for
attention, which is the opposite of the point.

The cap matters too, so there is a file long enough for twenty lines to be a
window rather than the whole thing.
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

from config.theme import THEME_DARK, get_theme_colors
from constants import DIFF_CONTEXT_LINES, DIFF_WIDE_CONTEXT_LINES
from gitops import diff as diff_mod
from gitops.diff import LINE_CONTEXT
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

# Long enough that twenty lines of context is a window into the file, not the
# whole file: a change in the middle must leave lines outside the window.
FILE_LINES = 120
CHANGED_LINE = 60


def build_repo(root: Path) -> Path:
    """
    Creates a repository holding one long file with one changed line.

    Args:
        root: Folder to initialise.

    Returns:
        Path: The repository root.
    """

    root.mkdir(parents=True, exist_ok=True)
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

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, env=environment, check=True, capture_output=True)

    lines = [f"line {number:03d}" for number in range(1, FILE_LINES + 1)]
    (root / "notes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git("init", "-b", "main")
    git("add", "-A")
    git("commit", "-m", "first")

    lines[CHANGED_LINE - 1] = "line 060 changed"
    (root / "notes.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def context_line_numbers(diff) -> list[int]:  # noqa: ANN001 - FileDiff needs the import
    """
    Returns the old line numbers of every unchanged line in a diff.

    Args:
        diff: Parsed diff.

    Returns:
        list[int]: Line numbers, in the order they appear.
    """

    found: list[int] = []
    for hunk in diff.hunks:
        for line in hunk.lines:
            if line.kind == LINE_CONTEXT and line.old_lineno:
                found.append(line.old_lineno)
    return found


@requires_git
class ContextWindowTests(unittest.TestCase):
    """
    How much unchanged text git is asked for.
    """

    def test_the_default_is_gits_own_three_lines(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            numbers = context_line_numbers(diff_mod.file_diff(repo, "notes.txt"))
            self.assertEqual(CHANGED_LINE - DIFF_CONTEXT_LINES, min(numbers))
            self.assertEqual(CHANGED_LINE + DIFF_CONTEXT_LINES, max(numbers))

    def test_the_wide_setting_reaches_twenty_lines_either_way(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt", context_lines=DIFF_WIDE_CONTEXT_LINES)
            numbers = context_line_numbers(diff)
            self.assertEqual(CHANGED_LINE - DIFF_WIDE_CONTEXT_LINES, min(numbers))
            self.assertEqual(CHANGED_LINE + DIFF_WIDE_CONTEXT_LINES, max(numbers))

    def test_it_is_a_window_not_the_whole_file(self) -> None:
        # Twenty lines above and below, not "everything". The rest of a long file
        # has nothing to do with the change.
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            diff = diff_mod.file_diff(repo, "notes.txt", context_lines=DIFF_WIDE_CONTEXT_LINES)
            shown = len(context_line_numbers(diff))
            self.assertEqual(DIFF_WIDE_CONTEXT_LINES * 2, shown)
            self.assertLess(shown, FILE_LINES)

    def test_a_change_near_the_top_simply_stops_at_the_top(self) -> None:
        with TemporaryDirectory() as base:
            repo = Path(base) / "work"
            build_repo(repo)
            lines = (repo / "notes.txt").read_text(encoding="utf-8").split("\n")
            lines[1] = "line 002 changed"
            (repo / "notes.txt").write_text("\n".join(lines), encoding="utf-8")

            diff = diff_mod.file_diff(repo, "notes.txt", context_lines=DIFF_WIDE_CONTEXT_LINES)
            self.assertEqual(1, min(context_line_numbers(diff)))

    def test_the_whole_commit_gets_the_same_window(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base) / "work")
            environment = dict(os.environ)
            environment.update(
                {
                    "GIT_CONFIG_GLOBAL": str(repo / ".gitconfig"),
                    "GIT_CONFIG_SYSTEM": str(repo / ".gitconfig-system"),
                    "GIT_AUTHOR_NAME": "Test",
                    "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "Test",
                    "GIT_COMMITTER_EMAIL": "t@example.invalid",
                }
            )
            for args in (["add", "-A"], ["commit", "-m", "second"]):
                subprocess.run(
                    ["git", *args], cwd=repo, env=environment, check=True, capture_output=True
                )

            diffs = diff_mod.commit_diff(
                repo, "HEAD", context_lines=DIFF_WIDE_CONTEXT_LINES
            )
            self.assertEqual(1, len(diffs))
            self.assertEqual(DIFF_WIDE_CONTEXT_LINES * 2, len(context_line_numbers(diffs[0])))


@requires_qt
class QuietRenderingTests(unittest.TestCase):
    """
    How the unchanged text is drawn once there is a lot of it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_the_theme_offers_a_quieter_colour(self) -> None:
        from config.theme import available_themes

        for name in available_themes():
            colors = get_theme_colors(name)
            with self.subTest(theme=name):
                self.assertNotEqual(colors.diff_context_text, colors.diff_context_quiet)

    def test_the_switch_changes_which_colour_is_used(self) -> None:
        from ui.diff_view import _context_colors

        colors = get_theme_colors(THEME_DARK)
        self.assertEqual(colors, _context_colors(colors, False))
        self.assertEqual(
            colors.diff_context_quiet, _context_colors(colors, True).diff_context_text
        )

    def test_nothing_else_about_the_theme_moves(self) -> None:
        # Only the unchanged text gets quieter. A change that also dimmed the
        # additions would defeat the purpose.
        from ui.diff_view import _context_colors

        colors = get_theme_colors(THEME_DARK)
        quiet = _context_colors(colors, True)
        self.assertEqual(colors.diff_added_bg, quiet.diff_added_bg)
        self.assertEqual(colors.diff_removed_bg, quiet.diff_removed_bg)
        self.assertEqual(colors.text, quiet.text)

    def test_the_panel_asks_git_for_the_wider_window(self) -> None:
        from config.app_settings import DIFF_SIDE_BY_SIDE
        from ui.diff_view import DiffView

        view = DiffView(DIFF_SIDE_BY_SIDE, False, True, False)
        self.addCleanup(view.deleteLater)

        self.assertEqual(DIFF_CONTEXT_LINES, view.context_lines)
        view._context.setChecked(True)
        self.assertEqual(DIFF_WIDE_CONTEXT_LINES, view.context_lines)
        self.assertTrue(view.full_context)

    def test_toggling_it_asks_for_a_fresh_diff(self) -> None:
        # The extra lines are not in the diff already in memory, so a redraw
        # cannot produce them.
        from config.app_settings import DIFF_SIDE_BY_SIDE
        from ui.diff_view import DiffView

        view = DiffView(DIFF_SIDE_BY_SIDE, False, True, False)
        self.addCleanup(view.deleteLater)
        reloads: list[int] = []
        view.reload_requested.connect(lambda: reloads.append(1))

        view._context.setChecked(True)
        self.assertEqual([1], reloads)

    def test_the_setting_starts_the_panel_the_way_it_was_left(self) -> None:
        from config.app_settings import DIFF_SIDE_BY_SIDE
        from ui.diff_view import DiffView

        view = DiffView(DIFF_SIDE_BY_SIDE, False, True, True)
        self.addCleanup(view.deleteLater)
        self.assertTrue(view.full_context)
        self.assertEqual(DIFF_WIDE_CONTEXT_LINES, view.context_lines)

    def test_the_rendered_unchanged_lines_carry_the_quiet_colour(self) -> None:
        from gitops.diff import parse_unified
        from ui.diff_view import SIDE_OLD, _context_colors, render_pane

        payload = (
            "diff --git a/notes.txt b/notes.txt\n"
            "--- a/notes.txt\n"
            "+++ b/notes.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " kept above\n"
            "-old line\n"
            "+new line\n"
            " kept below\n"
        )
        diff = parse_unified(payload)
        colors = get_theme_colors(THEME_DARK)

        loud = render_pane(diff, SIDE_OLD, _context_colors(colors, False))
        quiet = render_pane(diff, SIDE_OLD, _context_colors(colors, True))
        self.assertIn(colors.diff_context_text, loud)
        self.assertIn(colors.diff_context_quiet, quiet)
        self.assertNotIn(colors.diff_context_text, quiet)


if __name__ == "__main__":
    unittest.main()
