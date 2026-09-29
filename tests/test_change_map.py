"""
Tests for the bar that shows where the changes are.

The bar only appears when the comparison shows a whole file, which is exactly
the situation where its correctness cannot be checked by looking. In a
thousand-line file, a mark two pixels off is indistinguishable from a mark in
the right place, and a click that lands on the wrong change is indistinguishable
from one that landed on the right one until you read what is on screen.

Two things therefore have to hold and neither is visible:

* **The renderer and the map have to agree.** Both walk the same diff and both
  number the runs of changed lines, one to place an anchor and one to place a
  mark. If the two walks ever disagree, every mark points somewhere else and
  nothing looks wrong. They share ``_row_changed`` for that reason, and these
  tests compare what they produce.
* **One anchor per run, not per block.** With the whole file on screen, git
  produces a single block covering everything. Anchors per block would all point
  at the top of the file, which is the bug this was written after.
"""

from __future__ import annotations

import os
import re
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
from constants import DIFF_CONTEXT_LINES, DIFF_WHOLE_FILE_CONTEXT
from gitops import diff as diff_mod
from tests.support import requires_git

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

FILE_LINES = 400
# Three changes, far enough apart that they stay separate runs and land in
# clearly different thirds of the bar.
EDIT_LINE = 40
INSERT_LINE = 200
DELETE_LINE = 340


def build_repo(root: Path) -> Path:
    """
    Creates a long file with three changes of different kinds.

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

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, env=environment, check=True, capture_output=True)

    lines = [f"line {number:04d}" for number in range(1, FILE_LINES + 1)]
    (repo / "long.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    git("init", "-b", "main")
    git("add", "-A")
    git("commit", "-m", "first")

    lines[EDIT_LINE - 1] = "line 0040 CHANGED"
    lines.insert(INSERT_LINE - 1, "a brand new line")
    del lines[DELETE_LINE]
    (repo / "long.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return repo


def whole_file_diff(repo: Path):  # noqa: ANN201 - FileDiff needs the import
    """
    Reads the file's diff with the whole file as context.

    Args:
        repo: Working tree path.

    Returns:
        FileDiff: The parsed diff.
    """

    return diff_mod.file_diff(repo, "long.txt", context_lines=DIFF_WHOLE_FILE_CONTEXT)


@requires_git
class SpanTests(unittest.TestCase):
    """
    Where the map says the changes are.
    """

    def test_three_changes_become_three_marks(self) -> None:
        from ui.diff_view import change_spans

        with TemporaryDirectory() as base:
            spans, _rows = change_spans(whole_file_diff(build_repo(Path(base))))
            self.assertEqual(3, len(spans))

    def test_each_mark_carries_the_kind_of_change(self) -> None:
        from ui.change_map import KIND_ADDED, KIND_MIXED, KIND_REMOVED
        from ui.diff_view import change_spans

        with TemporaryDirectory() as base:
            spans, _rows = change_spans(whole_file_diff(build_repo(Path(base))))
            self.assertEqual([KIND_MIXED, KIND_ADDED, KIND_REMOVED], [s.kind for s in spans])

    def test_the_bar_covers_the_whole_file(self) -> None:
        from ui.diff_view import change_spans

        with TemporaryDirectory() as base:
            _spans, rows = change_spans(whole_file_diff(build_repo(Path(base))))
            # One row per line plus the block's heading, give or take the pairing
            # of the inserted and deleted lines.
            self.assertGreater(rows, FILE_LINES)

    def test_every_run_gets_its_own_anchor(self) -> None:
        # The bug this was written after: with the whole file on screen git
        # produces one block, so anchors per block all point at the top.
        from ui.diff_view import change_spans

        with TemporaryDirectory() as base:
            diff = whole_file_diff(build_repo(Path(base)))
            self.assertEqual(1, len(diff.hunks), "the whole file should be one block")

            spans, _rows = change_spans(diff)
            anchors = [span.anchor for span in spans]
            self.assertEqual(len(anchors), len(set(anchors)))

    def test_the_marks_are_spread_across_the_file(self) -> None:
        from ui.diff_view import change_spans

        with TemporaryDirectory() as base:
            spans, rows = change_spans(whole_file_diff(build_repo(Path(base))))
            thirds = [int(span.first / rows * 3) for span in spans]
            self.assertEqual([0, 1, 2], thirds)


@requires_qt
@requires_git
class AnchorTests(unittest.TestCase):
    """
    The renderer and the map, against each other.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_every_anchor_the_map_points_at_exists_in_the_document(self) -> None:
        # The half nothing else can catch: two walks over the same diff that
        # disagree produce marks pointing at nothing, silently.
        from ui.diff_view import SIDE_NEW, change_spans, render_pane

        with TemporaryDirectory() as base:
            diff = whole_file_diff(build_repo(Path(base)))
            document = render_pane(diff, SIDE_NEW, get_theme_colors(THEME_DARK))
            present = set(re.findall(r'<a name="(branchly-change-[^"]+)"', document))

            spans, _rows = change_spans(diff)
            for span in spans:
                with self.subTest(anchor=span.anchor):
                    self.assertIn(span.anchor, present)

    def test_the_old_side_carries_them_too(self) -> None:
        # Both panes are scrolled to the anchor, so both documents need it.
        from ui.diff_view import SIDE_OLD, change_spans, render_pane

        with TemporaryDirectory() as base:
            diff = whole_file_diff(build_repo(Path(base)))
            document = render_pane(diff, SIDE_OLD, get_theme_colors(THEME_DARK))
            spans, _rows = change_spans(diff)
            for span in spans:
                with self.subTest(anchor=span.anchor):
                    self.assertIn(f'name="{span.anchor}"', document)

    def test_the_one_column_view_carries_them_as_well(self) -> None:
        from ui.diff_view import change_spans, render_unified

        with TemporaryDirectory() as base:
            diff = whole_file_diff(build_repo(Path(base)))
            document = render_unified(diff, get_theme_colors(THEME_DARK))
            spans, _rows = change_spans(diff)
            for span in spans:
                with self.subTest(anchor=span.anchor):
                    self.assertIn(f'name="{span.anchor}"', document)

    def test_a_document_of_several_files_keeps_them_apart(self) -> None:
        from ui.diff_view import SIDE_NEW, many_change_spans, render_many_panes

        with TemporaryDirectory() as base:
            diff = whole_file_diff(build_repo(Path(base)))
            document = render_many_panes([diff, diff], SIDE_NEW, get_theme_colors(THEME_DARK))
            spans, _rows = many_change_spans([diff, diff])

            anchors = [span.anchor for span in spans]
            self.assertEqual(len(anchors), len(set(anchors)))
            for anchor in anchors:
                with self.subTest(anchor=anchor):
                    self.assertIn(f'name="{anchor}"', document)


@requires_qt
class WidgetTests(unittest.TestCase):
    """
    The bar itself.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _map(self):  # noqa: ANN202 - Qt at import time
        """
        Builds a bar with three marks in a hundred rows.

        Returns:
            ChangeMap: The bar.
        """

        from ui.change_map import KIND_ADDED, KIND_MIXED, KIND_REMOVED, ChangeMap, ChangeSpan

        bar = ChangeMap()
        self.addCleanup(bar.deleteLater)
        bar.resize(20, 300)
        bar.set_spans(
            [
                ChangeSpan(10, 12, KIND_MIXED, "top"),
                ChangeSpan(50, 51, KIND_ADDED, "middle"),
                ChangeSpan(90, 95, KIND_REMOVED, "bottom"),
            ],
            100,
        )
        return bar

    def test_it_is_twenty_pixels_wide(self) -> None:
        from ui.change_map import BAR_WIDTH

        self.assertEqual(20, BAR_WIDTH)
        self.assertEqual(20, self._map().width())

    def test_a_click_finds_the_change_at_that_height(self) -> None:
        bar = self._map()
        self.assertEqual("top", bar.anchor_at(int(0.11 * bar.height())))
        self.assertEqual("middle", bar.anchor_at(int(0.50 * bar.height())))
        self.assertEqual("bottom", bar.anchor_at(int(0.92 * bar.height())))

    def test_a_click_beside_a_mark_takes_the_nearest(self) -> None:
        # A single changed line in a long file is a two pixel mark. Asking
        # somebody to hit that is asking them to use the scrollbar instead.
        bar = self._map()
        self.assertEqual("top", bar.anchor_at(int(0.20 * bar.height())))
        self.assertEqual("bottom", bar.anchor_at(bar.height()))

    def test_an_empty_bar_has_nothing_to_jump_to(self) -> None:
        from ui.change_map import ChangeMap

        bar = ChangeMap()
        self.addCleanup(bar.deleteLater)
        bar.resize(20, 300)
        self.assertEqual("", bar.anchor_at(150))

    def test_it_draws_without_complaint_when_empty(self) -> None:
        from ui.change_map import ChangeMap

        bar = ChangeMap()
        self.addCleanup(bar.deleteLater)
        bar.resize(20, 300)
        self.assertFalse(bar.grab().isNull())

    def test_the_viewport_stays_inside_the_bar(self) -> None:
        bar = self._map()
        bar.set_viewport(-1.0, 5.0)
        self.assertFalse(bar.grab().isNull())


@requires_qt
@requires_git
class PanelTests(unittest.TestCase):
    """
    When the bar is on screen and when it is not.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _view(self, context: int):  # noqa: ANN202 - Qt at import time
        """
        Builds a comparison panel showing a long file.

        Args:
            context: Surrounding lines to show.

        Returns:
            tuple: The panel and the repository.
        """

        from config.app_settings import DIFF_SIDE_BY_SIDE
        from ui.diff_view import DiffView

        view = DiffView(DIFF_SIDE_BY_SIDE, False, True, False, context)
        self.addCleanup(view.deleteLater)
        return view

    def test_the_bar_only_appears_for_the_whole_file(self) -> None:
        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))

            view = self._view(DIFF_CONTEXT_LINES)
            view.show_diff(diff_mod.file_diff(repo, "long.txt"))
            # ``isVisibleTo``: the panel itself is never shown in a test, so
            # ``isVisible`` would be False either way and prove nothing.
            self.assertFalse(view._panes.change_map.isVisibleTo(view._panes))

            view.set_context_lines(DIFF_WHOLE_FILE_CONTEXT)
            view.show_diff(whole_file_diff(repo))
            self.assertTrue(view._panes.change_map.isVisibleTo(view._panes))

    def test_the_one_column_view_gets_one_too(self) -> None:
        from config.app_settings import DIFF_UNIFIED

        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            view = self._view(DIFF_WHOLE_FILE_CONTEXT)
            view._mode = DIFF_UNIFIED
            view.show_diff(whole_file_diff(repo))

            self.assertTrue(view._single_map.isVisibleTo(view))
            self.assertEqual(3, len(view._single_map.spans))

    def test_jumping_moves_both_columns_together(self) -> None:
        from ui.diff_view import change_spans

        with TemporaryDirectory() as base:
            repo = build_repo(Path(base))
            view = self._view(DIFF_WHOLE_FILE_CONTEXT)
            diff = whole_file_diff(repo)
            view.show_diff(diff)
            view.resize(900, 500)
            view.show()
            self.app.processEvents()

            spans, _rows = change_spans(diff)
            view._panes.jump_to(spans[-1].anchor)
            self.app.processEvents()

            old_pane, new_pane = view._panes.panes
            self.assertGreater(new_pane.verticalScrollBar().value(), 0)
            self.assertEqual(
                new_pane.verticalScrollBar().value(),
                old_pane.verticalScrollBar().value(),
            )


if __name__ == "__main__":
    unittest.main()
