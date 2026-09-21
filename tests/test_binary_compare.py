"""
Tests for comparing files that have no readable diff.

The interesting part is not the widget, it is where each side's facts come from.
The working tree, a commit and the index answer "how big is it" and "when was it
written" in three different ways, and two of those three answers are easy to get
subtly wrong: a commit has no timestamp for a file, only for the commit that
touched it, and the index has one only because git happens to record it.
"""

from __future__ import annotations

import os
import struct
import time
import unittest
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

from gitops import blobs
from gitops.diff import (
    TARGET_BRANCHES,
    TARGET_COMMITS,
    TARGET_INDEX_HEAD,
    TARGET_WORKTREE_HEAD,
    TARGET_WORKTREE_INDEX,
)
from gitops.status import (
    CHANGE_ADDED,
    CHANGE_CONFLICTED,
    CHANGE_DELETED,
    CHANGE_GLYPHS,
    CHANGE_MODIFIED,
    CHANGE_RENAMED,
    CHANGE_UNTRACKED,
    FileChange,
)
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

def _tiny_png(red: int = 0) -> bytes:
    """
    Builds a real one-pixel PNG.

    Written out rather than pasted as a hex blob: a blob with one wrong checksum
    byte still looks like a PNG and still fails to load, and the test that finds
    that out is the one about the preview, which is not what it is testing.

    Args:
        red: Red channel of the single pixel, so two calls give two files.

    Returns:
        bytes: A valid PNG.
    """

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = bytes([0, red, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


TINY_PNG = _tiny_png()
OTHER_PNG = _tiny_png(200)


class SizeFormattingTests(unittest.TestCase):
    """
    Turning a byte count into something readable.
    """

    def test_small_sizes_stay_in_bytes(self) -> None:
        self.assertEqual("0 B", blobs.format_size(0))
        self.assertEqual("1023 B", blobs.format_size(1023))

    def test_it_steps_up_through_the_units(self) -> None:
        self.assertEqual("1.0 KB", blobs.format_size(1024))
        self.assertEqual("1.0 MB", blobs.format_size(1024 * 1024))
        self.assertEqual("1.0 GB", blobs.format_size(1024**3))

    def test_a_decimal_below_ten_and_none_above(self) -> None:
        self.assertEqual("9.8 KB", blobs.format_size(10_000))
        self.assertEqual("98 KB", blobs.format_size(100_000))


class TargetTests(unittest.TestCase):
    """
    Which two versions each comparison target refers to.
    """

    def test_every_target_names_two_sides(self) -> None:
        cases = {
            TARGET_WORKTREE_HEAD: ((blobs.SOURCE_REVISION, "HEAD"), (blobs.SOURCE_WORKTREE, "")),
            TARGET_WORKTREE_INDEX: ((blobs.SOURCE_INDEX, ""), (blobs.SOURCE_WORKTREE, "")),
            TARGET_INDEX_HEAD: ((blobs.SOURCE_REVISION, "HEAD"), (blobs.SOURCE_INDEX, "")),
        }
        for target, expected in cases.items():
            with self.subTest(target=target):
                self.assertEqual(expected, blobs.comparison_sides(target))

    def test_commit_and_branch_targets_carry_their_revisions(self) -> None:
        for target in (TARGET_COMMITS, TARGET_BRANCHES):
            with self.subTest(target=target):
                self.assertEqual(
                    ((blobs.SOURCE_REVISION, "aaa"), (blobs.SOURCE_REVISION, "bbb")),
                    blobs.comparison_sides(target, "aaa", "bbb"),
                )

    def test_an_unknown_target_falls_back_to_the_usual_one(self) -> None:
        self.assertEqual(
            ((blobs.SOURCE_REVISION, "HEAD"), (blobs.SOURCE_WORKTREE, "")),
            blobs.comparison_sides("something-else"),
        )


class ImagePathTests(unittest.TestCase):
    """
    Which paths get a picture preview.
    """

    def test_known_suffixes(self) -> None:
        for path in ("a.png", "dir/b.JPG", "c.webp", "d.gif"):
            with self.subTest(path=path):
                self.assertTrue(blobs.is_image_path(path))

    def test_everything_else(self) -> None:
        for path in ("a.pdf", "b.zip", "Makefile", "", "c.png.bak"):
            with self.subTest(path=path):
                self.assertFalse(blobs.is_image_path(path))


@requires_git
class FactTests(unittest.TestCase):
    """
    Where the size and the date of each side come from.
    """

    def test_the_working_tree_side_reads_the_filesystem(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"12345")
            facts = blobs.worktree_facts(repo.root, "a.bin")
            self.assertTrue(facts.exists)
            self.assertEqual(5, facts.size)
            self.assertIsNotNone(facts.modified)
            self.assertEqual(blobs.SOURCE_WORKTREE, facts.source)

    def test_a_missing_working_tree_file_is_reported_as_absent(self) -> None:
        with temp_repo() as repo:
            facts = blobs.worktree_facts(repo.root, "gone.bin")
            self.assertFalse(facts.exists)
            self.assertEqual(0, facts.size)

    def test_a_directory_is_not_a_file(self) -> None:
        with temp_repo() as repo:
            (repo.root / "folder").mkdir()
            self.assertFalse(blobs.worktree_facts(repo.root, "folder").exists)

    def test_git_reports_the_size_of_a_committed_version(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"1234567890")
            repo.commit("add")
            self.assertEqual(10, blobs.blob_size(repo.root, "HEAD:a.bin"))

    def test_an_unknown_object_has_no_size(self) -> None:
        with temp_repo() as repo:
            repo.write("a.txt", "x\n")
            repo.commit("add")
            self.assertIsNone(blobs.blob_size(repo.root, "HEAD:nothing.bin"))

    def test_the_commit_date_is_the_date_of_the_version(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"one")
            repo.commit("add")
            stamp = blobs.revision_time(repo.root, "HEAD", "a.bin")
            self.assertIsNotNone(stamp)
            self.assertLess(abs(stamp - time.time()), 600)

    def test_a_path_with_no_history_has_no_date(self) -> None:
        with temp_repo() as repo:
            repo.write("a.txt", "x\n")
            repo.commit("add")
            self.assertIsNone(blobs.revision_time(repo.root, "HEAD", "never-existed.bin"))

    def test_the_index_side_reports_a_size_and_usually_a_date(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"12345")
            repo.git("add", "a.bin")
            facts = blobs.side_facts(repo.root, "a.bin", blobs.SOURCE_INDEX, "")
            self.assertTrue(facts.exists)
            self.assertEqual(5, facts.size)
            self.assertEqual(blobs.SOURCE_INDEX, facts.source)

    def test_an_unstaged_path_has_no_index_side(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"12345")
            facts = blobs.side_facts(repo.root, "a.bin", blobs.SOURCE_INDEX, "")
            self.assertFalse(facts.exists)

    def test_an_unsafe_path_is_refused_rather_than_passed_to_git(self) -> None:
        with temp_repo() as repo:
            facts = blobs.side_facts(repo.root, "--upload-pack=x", blobs.SOURCE_REVISION, "HEAD")
            self.assertFalse(facts.exists)


@requires_git
class ComparisonTests(unittest.TestCase):
    """
    The whole comparison, as the view receives it.
    """

    def test_a_changed_binary_has_both_sides(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"1" * 100)
            repo.commit("add")
            repo.write_bytes("a.bin", b"1" * 250)

            comparison = blobs.compare(repo.root, "a.bin")
            self.assertTrue(comparison.before.exists)
            self.assertTrue(comparison.after.exists)
            self.assertEqual(100, comparison.before.size)
            self.assertEqual(250, comparison.after.size)
            self.assertEqual(150, comparison.size_delta)
            self.assertFalse(comparison.is_image)

    def test_a_new_file_has_no_before(self) -> None:
        with temp_repo() as repo:
            repo.write("keep.txt", "x\n")
            repo.commit("first")
            repo.write_bytes("new.bin", b"abc")

            comparison = blobs.compare(repo.root, "new.bin", untracked=True)
            self.assertFalse(comparison.before.exists)
            self.assertTrue(comparison.after.exists)
            # "grew by its whole size" is not a useful thing to say about a new file.
            self.assertEqual(0, comparison.size_delta)

    def test_a_deleted_file_has_no_after(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"abcdef")
            repo.commit("add")
            (repo.root / "a.bin").unlink()

            comparison = blobs.compare(repo.root, "a.bin")
            self.assertTrue(comparison.before.exists)
            self.assertFalse(comparison.after.exists)
            self.assertEqual(6, comparison.before.size)

    def test_an_image_brings_both_pictures_along(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("logo.png", TINY_PNG)
            repo.commit("add")
            repo.write_bytes("logo.png", OTHER_PNG)

            comparison = blobs.compare(repo.root, "logo.png")
            self.assertTrue(comparison.is_image)
            self.assertEqual(TINY_PNG, comparison.before_bytes)
            self.assertIsNotNone(comparison.after_bytes)
            self.assertFalse(comparison.preview_skipped)

    def test_a_non_image_carries_no_payload(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"abc")
            repo.commit("add")
            repo.write_bytes("a.bin", b"abcd")

            comparison = blobs.compare(repo.root, "a.bin")
            self.assertIsNone(comparison.before_bytes)
            self.assertIsNone(comparison.after_bytes)

    def test_an_oversized_image_is_not_read_into_memory(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("big.png", b"x")
            repo.commit("add")
            repo.write_bytes("big.png", b"x" * 64)

            previous = blobs.MAX_PREVIEW_BYTES
            blobs.MAX_PREVIEW_BYTES = 8
            try:
                comparison = blobs.compare(repo.root, "big.png")
            finally:
                blobs.MAX_PREVIEW_BYTES = previous

            self.assertTrue(comparison.preview_skipped)
            self.assertIsNone(comparison.after_bytes)

    def test_the_staged_target_compares_against_the_index(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"1" * 10)
            repo.commit("add")
            repo.write_bytes("a.bin", b"1" * 20)
            repo.git("add", "a.bin")
            repo.write_bytes("a.bin", b"1" * 35)

            staged = blobs.compare(repo.root, "a.bin", target=TARGET_WORKTREE_INDEX)
            self.assertEqual(20, staged.before.size)
            self.assertEqual(35, staged.after.size)

            committed = blobs.compare(repo.root, "a.bin", target=TARGET_INDEX_HEAD)
            self.assertEqual(10, committed.before.size)
            self.assertEqual(20, committed.after.size)


class GlyphTests(unittest.TestCase):
    """
    The markers the file list shows.
    """

    def test_new_files_get_a_plus(self) -> None:
        self.assertEqual("+", FileChange(path="a", untracked=True).glyph)
        self.assertEqual("+", CHANGE_GLYPHS[CHANGE_ADDED])

    def test_a_replaced_file_gets_a_dot(self) -> None:
        self.assertEqual("•", FileChange(path="a", worktree_code="M").glyph)
        self.assertEqual("•", CHANGE_GLYPHS[CHANGE_MODIFIED])

    def test_the_other_kinds_have_their_own(self) -> None:
        self.assertEqual("−", CHANGE_GLYPHS[CHANGE_DELETED])
        self.assertEqual("→", CHANGE_GLYPHS[CHANGE_RENAMED])
        self.assertEqual("!", CHANGE_GLYPHS[CHANGE_CONFLICTED])

    def test_every_kind_has_a_marker(self) -> None:
        for kind in (
            CHANGE_MODIFIED,
            CHANGE_ADDED,
            CHANGE_DELETED,
            CHANGE_RENAMED,
            CHANGE_UNTRACKED,
            CHANGE_CONFLICTED,
        ):
            with self.subTest(kind=kind):
                self.assertTrue(CHANGE_GLYPHS[kind])


@requires_qt
@requires_git
class ViewTests(unittest.TestCase):
    """
    What the comparison view puts on screen.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _view(self):  # noqa: ANN202 - the widget type needs Qt at import time
        """
        Builds a comparison view.

        Returns:
            BinaryComparisonView: A fresh view.
        """

        from ui.diff_view import BinaryComparisonView

        view = BinaryComparisonView()
        self.addCleanup(view.deleteLater)
        return view

    def test_both_sides_show_a_size_and_a_date(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"1" * 2048)
            repo.commit("add")
            repo.write_bytes("a.bin", b"1" * 4096)

            view = self._view()
            view.show_comparison(blobs.compare(repo.root, "a.bin"))
            before, after = view._sides
            self.assertIn("2.0 KB", before["facts"].text())
            self.assertIn("4.0 KB", after["facts"].text())
            self.assertIn("20", before["facts"].text())

    def test_an_absent_side_says_so_instead_of_staying_blank(self) -> None:
        with temp_repo() as repo:
            repo.write("keep.txt", "x\n")
            repo.commit("first")
            repo.write_bytes("new.bin", b"abc")

            view = self._view()
            view.show_comparison(blobs.compare(repo.root, "new.bin", untracked=True))
            before, after = view._sides
            self.assertTrue(before["preview"].text())
            self.assertEqual("", before["facts"].text())
            self.assertIn("3 B", after["facts"].text())

    def test_an_undisplayable_file_gets_its_type_as_a_stand_in(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("handbuch.pdf", b"%PDF-1.4")
            repo.commit("add")
            repo.write_bytes("handbuch.pdf", b"%PDF-1.4 more")

            view = self._view()
            view.show_comparison(blobs.compare(repo.root, "handbuch.pdf"))
            before, _after = view._sides
            self.assertEqual("PDF", before["preview"].text())
            self.assertTrue(before["preview"].pixmap().isNull())

    def test_an_image_gets_a_real_picture(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("logo.png", TINY_PNG)
            repo.commit("add")
            repo.write_bytes("logo.png", OTHER_PNG)

            view = self._view()
            view.show_comparison(blobs.compare(repo.root, "logo.png"))
            before, after = view._sides
            self.assertFalse(before["preview"].pixmap().isNull())
            self.assertFalse(after["preview"].pixmap().isNull())

    def test_the_summary_names_what_happened(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"1" * 100)
            repo.commit("add")
            repo.write_bytes("a.bin", b"1" * 300)

            view = self._view()
            view.show_comparison(blobs.compare(repo.root, "a.bin"))
            self.assertIn("200 B", view._summary.text())

    def test_each_side_names_where_it_came_from(self) -> None:
        with temp_repo() as repo:
            repo.write_bytes("a.bin", b"1")
            repo.commit("add")
            repo.write_bytes("a.bin", b"12")

            view = self._view()
            view.show_comparison(blobs.compare(repo.root, "a.bin"))
            before, after = view._sides
            self.assertIn("HEAD", before["source"].text())
            self.assertTrue(after["source"].text())


if __name__ == "__main__":
    unittest.main()
