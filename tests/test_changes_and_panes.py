"""
Tests for the remembered file selection and for the two-pane comparison.

Both are behaviours that only show up over time: a tick box that forgets what
the user decided is only wrong on the next start, and two views that drift apart
are only wrong once someone scrolls. So both are driven here rather than looked
at.
"""

from __future__ import annotations

import os
import unittest
from typing import TYPE_CHECKING

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

from gitops.diff import parse_unified
from gitops.status import RepositoryState
from models.repository import RepoEntry
from tests.support import TempRepo, requires_git, temp_repo

if TYPE_CHECKING:  # pragma: no cover - imported for the annotations only
    from ui.changes_panel import ChangesPanel
    from ui.diff_view import ComparisonPanes

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

WIDE_DIFF = (
    "diff --git a/wide.txt b/wide.txt\n"
    "--- a/wide.txt\n"
    "+++ b/wide.txt\n"
    "@@ -1,3 +1,3 @@\n"
    " keep\n"
    "-" + "old " * 200 + "\n"
    "+" + "new " * 200 + "\n"
    " tail\n"
)


class EntryMemoryTests(unittest.TestCase):
    """
    The deselection as it is stored and read back.
    """

    def test_it_survives_a_round_trip(self) -> None:
        entry = RepoEntry(path="/tmp/x", deselected_paths={"b.txt", "a.txt"})
        restored = RepoEntry.from_dict(entry.to_dict())
        self.assertEqual({"a.txt", "b.txt"}, restored.deselected_paths)

    def test_an_old_file_without_the_field_reads_as_empty(self) -> None:
        restored = RepoEntry.from_dict({"path": "/tmp/x", "name": "x"})
        self.assertEqual(set(), restored.deselected_paths)

    def test_rubbish_entries_are_dropped(self) -> None:
        restored = RepoEntry.from_dict(
            {"path": "/tmp/x", "deselected_paths": ["ok.txt", 7, "", None]}
        )
        self.assertEqual({"ok.txt"}, restored.deselected_paths)

    def test_committing_forgets_what_went_in(self) -> None:
        entry = RepoEntry(path="/tmp/x", deselected_paths={"a.txt", "b.txt"})
        entry.forget_selection(["a.txt", "never-was-there.txt"])
        self.assertEqual({"b.txt"}, entry.deselected_paths)


@requires_qt
@requires_git
class SelectionTests(unittest.TestCase):
    """
    The commit box remembering what the user unticked.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self) -> ChangesPanel:
        """
        Builds a changes panel.

        Returns:
            ChangesPanel: A fresh panel.
        """

        from ui.changes_panel import ChangesPanel

        panel = ChangesPanel()
        self.addCleanup(panel.deleteLater)
        return panel

    def _state(self, repo: TempRepo) -> RepositoryState:
        """
        Reads a repository with three untracked files.

        Args:
            repo: The fixture repository.

        Returns:
            RepositoryState: Its state.
        """

        from gitops.status import read_state

        for name in ("one.txt", "two.txt", "three.txt"):
            repo.write(name, "x\n")
        return read_state(repo.root)

    def test_everything_starts_ticked(self) -> None:
        with temp_repo() as repo:
            panel = self._panel()
            panel.set_state(self._state(repo), "main", deselected=set())
            self.assertEqual(3, len(panel.checked_paths()))
            self.assertEqual(set(), panel.deselected_paths())

    def test_a_stored_deselection_is_applied(self) -> None:
        with temp_repo() as repo:
            panel = self._panel()
            panel.set_state(self._state(repo), "main", deselected={"two.txt"})
            self.assertNotIn("two.txt", panel.checked_paths())
            self.assertEqual({"two.txt"}, panel.deselected_paths())

    def test_unticking_is_remembered(self) -> None:
        with temp_repo() as repo:
            panel = self._panel()
            state = self._state(repo)
            panel.set_state(state, "main", deselected=set())

            row = next(
                index
                for index in range(panel._list.count())
                if panel._list.item(index).data(int(Qt.ItemDataRole.UserRole)) == "two.txt"
            )
            panel._list.item(row).setCheckState(Qt.CheckState.Unchecked)
            self.assertEqual({"two.txt"}, panel.deselected_paths())

            # A plain refresh must not undo the decision.
            panel.set_state(state, "main")
            self.assertNotIn("two.txt", panel.checked_paths())

    def test_a_deselection_outlives_the_file_leaving_the_list(self) -> None:
        with temp_repo() as repo:
            panel = self._panel()
            panel.set_state(self._state(repo), "main", deselected={"two.txt"})

            # The file is reverted, so it drops out of the change list entirely.
            (repo.root / "two.txt").unlink()
            from gitops.status import read_state

            panel.set_state(read_state(repo.root), "main")
            self.assertEqual({"two.txt"}, panel.deselected_paths())

            # It comes back, and it is still unticked.
            repo.write("two.txt", "again\n")
            panel.set_state(read_state(repo.root), "main")
            self.assertNotIn("two.txt", panel.checked_paths())

    def test_select_none_and_select_all_go_through_the_memory(self) -> None:
        with temp_repo() as repo:
            panel = self._panel()
            panel.set_state(self._state(repo), "main", deselected=set())

            panel._set_all_checked(False)
            self.assertEqual(3, len(panel.deselected_paths()))

            panel._set_all_checked(True)
            self.assertEqual(set(), panel.deselected_paths())

    def test_every_row_carries_its_status_marker(self) -> None:
        from ui.changes_panel import _ROLE_GLYPH, _ROLE_GLYPH_TOKEN

        with temp_repo() as repo:
            repo.write("kept.txt", "one\n")
            repo.commit("first")
            repo.write("kept.txt", "one\ntwo\n")
            repo.write("brandnew.txt", "x\n")

            from gitops.status import read_state

            panel = self._panel()
            panel.set_state(read_state(repo.root), "main", deselected=set())

            markers = {
                panel._list.item(index).data(int(Qt.ItemDataRole.UserRole)): (
                    panel._list.item(index).data(_ROLE_GLYPH),
                    panel._list.item(index).data(_ROLE_GLYPH_TOKEN),
                )
                for index in range(panel._list.count())
            }
            # A plus for what was not there before, a dot for what was replaced.
            self.assertEqual("+", markers["brandnew.txt"][0])
            self.assertEqual("\u2022", markers["kept.txt"][0])
            self.assertTrue(markers["brandnew.txt"][1])

    def test_committing_clears_the_memory_for_those_files(self) -> None:
        with temp_repo() as repo:
            panel = self._panel()
            panel.set_state(self._state(repo), "main", deselected={"one.txt", "two.txt"})
            panel.forget_selection(["one.txt"])
            self.assertEqual({"two.txt"}, panel.deselected_paths())


@requires_qt
class ComparisonPaneTests(unittest.TestCase):
    """
    The two views of the side-by-side comparison.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _panes(self) -> ComparisonPanes:
        """
        Builds a filled, laid-out pair of panes.

        Returns:
            ComparisonPanes: The widget, already showing a wide diff.
        """

        from config.theme import THEME_DARK, get_theme_colors
        from ui.diff_view import SIDE_NEW, SIDE_OLD, ComparisonPanes, render_pane

        diff = parse_unified(WIDE_DIFF)
        colors = get_theme_colors(THEME_DARK)
        widget = ComparisonPanes()
        self.addCleanup(widget.deleteLater)
        # A real size, so the content is genuinely wider than the viewport and
        # the horizontal scrollbars have something to scroll.
        widget.resize(400, 300)
        widget.set_documents(
            render_pane(diff, SIDE_OLD, colors), render_pane(diff, SIDE_NEW, colors)
        )
        widget.show()
        self.app.processEvents()
        return widget

    def test_each_side_says_which_version_it_is(self) -> None:
        # Left and right on their own say nothing. Reading the old file as the
        # new one turns every addition into a deletion.
        import i18n

        widget = self._panes()
        old_caption, new_caption = widget.captions
        self.assertEqual(i18n.t("diff.pane_old"), old_caption)
        self.assertEqual(i18n.t("diff.pane_new"), new_caption)
        self.assertNotEqual(old_caption, new_caption)

    def test_the_captions_are_words_not_symbols(self) -> None:
        widget = self._panes()
        for caption in widget.captions:
            self.assertTrue(caption.strip())
            self.assertTrue(any(char.isalpha() for char in caption), caption)

    def test_the_captions_stay_put_while_the_content_scrolls(self) -> None:
        # Put inside the documents they would scroll off the top, which is
        # exactly when the reader needs them.
        widget = self._panes()
        before = widget.captions
        for pane in widget.panes:
            pane.verticalScrollBar().setValue(pane.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertEqual(before, widget.captions)

    def test_the_captions_follow_the_theme(self) -> None:
        from config.theme import THEME_DARK, THEME_LIGHT

        widget = self._panes()
        widget.refresh(THEME_DARK)
        dark = widget._old_caption.styleSheet()
        widget.refresh(THEME_LIGHT)
        self.assertNotEqual(dark, widget._old_caption.styleSheet())

    def test_both_sides_are_shown(self) -> None:
        widget = self._panes()
        old_pane, new_pane = widget.panes
        self.assertIn("old", old_pane.toPlainText())
        self.assertIn("new", new_pane.toPlainText())
        # Neither side may be the one that disappears when space runs out.
        self.assertFalse(old_pane.isHidden())
        self.assertFalse(new_pane.isHidden())

    def test_both_sides_can_scroll_sideways(self) -> None:
        widget = self._panes()
        for pane in widget.panes:
            self.assertGreater(pane.horizontalScrollBar().maximum(), 0)

    def test_the_line_numbers_stay_in_a_narrow_column(self) -> None:
        widget = self._panes()
        pane = widget.panes[0]
        table = pane.document().rootFrame().childFrames()[0]
        cell = table.cellAt(1, 1)
        # A heading row spanning both columns makes Qt give up on the widths and
        # hand a third of the pane to the line numbers, pushing the code off the
        # right edge. The pane headings therefore use two real cells.
        self.assertLess(pane.cursorRect(cell.firstCursorPosition()).x(), 80)

    def test_scrolling_the_left_side_moves_the_right(self) -> None:
        widget = self._panes()
        old_pane, new_pane = widget.panes
        old_pane.horizontalScrollBar().setValue(42)
        self.app.processEvents()
        self.assertEqual(42, new_pane.horizontalScrollBar().value())

    def test_scrolling_the_right_side_moves_the_left(self) -> None:
        widget = self._panes()
        old_pane, new_pane = widget.panes
        new_pane.horizontalScrollBar().setValue(37)
        self.app.processEvents()
        self.assertEqual(37, old_pane.horizontalScrollBar().value())

    def test_vertical_scrolling_is_shared_too(self) -> None:
        widget = self._panes()
        old_pane, new_pane = widget.panes
        new_pane.verticalScrollBar().setValue(new_pane.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertEqual(
            new_pane.verticalScrollBar().value(), old_pane.verticalScrollBar().value()
        )

    def test_only_one_vertical_bar_is_drawn(self) -> None:
        widget = self._panes()
        old_pane, new_pane = widget.panes
        # Two identical vertical bars side by side invite the question which one
        # does what, so the left one is not drawn.
        self.assertEqual(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff, old_pane.verticalScrollBarPolicy()
        )
        self.assertNotEqual(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff, new_pane.verticalScrollBarPolicy()
        )

    def test_neither_side_can_be_dragged_away(self) -> None:
        from ui.diff_view import PANE_MIN_WIDTH

        widget = self._panes()
        old_pane, new_pane = widget.panes
        # The splitter is asked to give one side everything. Both must survive,
        # which is the point of the two-pane layout in the first place.
        widget._split.setSizes([10_000, 0])
        self.app.processEvents()
        self.assertGreaterEqual(old_pane.minimumWidth(), PANE_MIN_WIDTH)
        self.assertGreaterEqual(new_pane.minimumWidth(), PANE_MIN_WIDTH)
        self.assertFalse(widget._split.childrenCollapsible())

    def test_a_new_file_starts_at_the_beginning(self) -> None:
        widget = self._panes()
        old_pane, new_pane = widget.panes
        new_pane.horizontalScrollBar().setValue(50)
        widget.scroll_to_top()
        self.app.processEvents()
        self.assertEqual(0, old_pane.horizontalScrollBar().value())
        self.assertEqual(0, new_pane.horizontalScrollBar().value())


if __name__ == "__main__":
    unittest.main()
