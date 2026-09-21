"""
Tests for writing ``.gitignore`` entries.

Two things are worth testing hard here, and neither is the file write. The first
is that a file name becomes a pattern matching *that* name: a file called
``report[2].txt`` holds gitignore syntax, and an entry that quietly matches
something else is worse than no entry. The second is that adding a line leaves
the rest of the file exactly as it was, line endings included.
"""

from __future__ import annotations

import unittest

from gitops import ignore as ignore_mod
from tests.support import requires_git, temp_repo


class SuggestionTests(unittest.TestCase):
    """
    What the context menu offers for a given path.
    """

    def test_a_top_level_file_is_anchored(self) -> None:
        offers = ignore_mod.suggestions("notes.txt")
        patterns = [item.pattern for item in offers]
        # Without the leading slash this would also ignore docs/notes.txt, which
        # is not what pointing at one row meant.
        self.assertIn("/notes.txt", patterns)
        self.assertIn("*.txt", patterns)

    def test_a_nested_file_offers_its_folder(self) -> None:
        offers = ignore_mod.suggestions("build/output/app.log")
        kinds = {item.kind: item.pattern for item in offers}
        self.assertEqual("/build/output/app.log", kinds[ignore_mod.KIND_FILE])
        self.assertEqual("*.log", kinds[ignore_mod.KIND_EXTENSION])
        self.assertEqual("/build/output/", kinds[ignore_mod.KIND_FOLDER])

    def test_a_file_without_a_suffix_gets_no_extension_offer(self) -> None:
        kinds = {item.kind for item in ignore_mod.suggestions("Makefile")}
        self.assertNotIn(ignore_mod.KIND_EXTENSION, kinds)

    def test_a_dotfile_is_not_treated_as_an_extension(self) -> None:
        # ".env" is all suffix and no stem. Offering "*.env" would be a
        # different thing from ignoring that one file.
        kinds = {item.kind for item in ignore_mod.suggestions(".env")}
        self.assertNotIn(ignore_mod.KIND_EXTENSION, kinds)

    def test_glob_characters_in_a_name_are_escaped(self) -> None:
        offers = ignore_mod.suggestions("reports/report[2]*.txt")
        pattern = next(item.pattern for item in offers if item.kind == ignore_mod.KIND_FILE)
        self.assertEqual("/reports/report\\[2\\]\\*.txt", pattern)

    def test_a_leading_hash_is_escaped(self) -> None:
        offers = ignore_mod.suggestions("#draft.md")
        pattern = next(item.pattern for item in offers if item.kind == ignore_mod.KIND_FILE)
        # Unescaped this would be a comment and ignore nothing at all.
        self.assertEqual("/\\#draft.md", pattern)

    def test_backslashes_and_windows_separators(self) -> None:
        offers = ignore_mod.suggestions("build\\out.log")
        pattern = next(item.pattern for item in offers if item.kind == ignore_mod.KIND_FILE)
        self.assertEqual("/build/out.log", pattern)

    def test_nonsense_paths_offer_nothing(self) -> None:
        for path in ("", "   ", ".", "../escape"):
            with self.subTest(path=path):
                self.assertEqual([], ignore_mod.suggestions(path))


@requires_git
class WritingTests(unittest.TestCase):
    """
    Appending to the file without disturbing what is already there.
    """

    def test_the_file_is_created_when_missing(self) -> None:
        with temp_repo() as repo:
            outcome = ignore_mod.add_pattern(repo.root, "/secret.txt")
            self.assertTrue(outcome.ok)
            self.assertTrue(outcome.created)
            self.assertEqual("/secret.txt\n", (repo.root / ".gitignore").read_text())

    def test_an_entry_is_appended_below_what_is_there(self) -> None:
        with temp_repo() as repo:
            target = repo.root / ".gitignore"
            target.write_text("# comment\n*.pyc\n", encoding="utf-8")
            outcome = ignore_mod.add_pattern(repo.root, "/build/")
            self.assertTrue(outcome.ok)
            self.assertFalse(outcome.created)
            self.assertEqual("# comment\n*.pyc\n/build/\n", target.read_text())

    def test_a_missing_final_newline_is_added_first(self) -> None:
        with temp_repo() as repo:
            target = repo.root / ".gitignore"
            target.write_text("*.pyc", encoding="utf-8")
            ignore_mod.add_pattern(repo.root, "/build/")
            self.assertEqual("*.pyc\n/build/\n", target.read_text())

    def test_crlf_files_stay_crlf(self) -> None:
        with temp_repo() as repo:
            target = repo.root / ".gitignore"
            target.write_bytes(b"*.pyc\r\n")
            ignore_mod.add_pattern(repo.root, "/build/")
            # Mixing line endings would show the whole file as changed next time.
            self.assertEqual(b"*.pyc\r\n/build/\r\n", target.read_bytes())

    def test_a_duplicate_is_reported_and_not_written_twice(self) -> None:
        with temp_repo() as repo:
            target = repo.root / ".gitignore"
            target.write_text("/build/\n", encoding="utf-8")
            outcome = ignore_mod.add_pattern(repo.root, "/build/")
            self.assertTrue(outcome.ok)
            self.assertTrue(outcome.already_present)
            self.assertEqual("/build/\n", target.read_text())

    def test_an_empty_pattern_is_refused(self) -> None:
        with temp_repo() as repo:
            outcome = ignore_mod.add_pattern(repo.root, "   ")
            self.assertFalse(outcome.ok)
            self.assertFalse((repo.root / ".gitignore").exists())

    def test_existing_patterns_skip_comments_and_blanks(self) -> None:
        with temp_repo() as repo:
            (repo.root / ".gitignore").write_text("# a\n\n*.pyc\n  /build/  \n", encoding="utf-8")
            self.assertEqual(["*.pyc", "/build/"], ignore_mod.existing_patterns(repo.root))

    def test_a_repository_that_is_gone_fails_cleanly(self) -> None:
        outcome = ignore_mod.add_pattern("/nonexistent/path/for/branchly", "/x")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.error_key)


@requires_git
class CheckIgnoreTests(unittest.TestCase):
    """
    Asking git whether something is already ignored.
    """

    def test_git_confirms_a_written_entry(self) -> None:
        with temp_repo() as repo:
            repo.write("build/out.log", "noise\n")
            self.assertFalse(ignore_mod.is_ignored(repo.root, "build/out.log"))
            ignore_mod.add_pattern(repo.root, "/build/")
            self.assertTrue(ignore_mod.is_ignored(repo.root, "build/out.log"))

    def test_an_escaped_name_matches_the_real_file(self) -> None:
        with temp_repo() as repo:
            name = "report[2].txt"
            repo.write(name, "x\n")
            offer = next(
                item
                for item in ignore_mod.suggestions(name)
                if item.kind == ignore_mod.KIND_FILE
            )
            ignore_mod.add_pattern(repo.root, offer.pattern)
            # The escaping is only right if git agrees the file is now ignored.
            self.assertTrue(ignore_mod.is_ignored(repo.root, name))

    def test_an_empty_path_is_not_ignored(self) -> None:
        with temp_repo() as repo:
            self.assertFalse(ignore_mod.is_ignored(repo.root, "  "))


if __name__ == "__main__":
    unittest.main()
