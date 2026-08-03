"""
Tests for ref-name validation.

Half of these are ordinary validity checks; the other half are the reason this
module exists — a branch name is user input that ends up on a command line.
"""

from __future__ import annotations

import unittest

from gitops.refname import (
    MAX_REF_LENGTH,
    invalid_reason_key,
    is_safe_argument,
    is_valid_branch_name,
    is_valid_revision,
    suggest_branch_name,
)


class SafeArgumentTests(unittest.TestCase):
    def test_plain_values_are_safe(self) -> None:
        for value in ("main", "feature/login", "v1.2.3", "a"):
            self.assertTrue(is_safe_argument(value), value)

    def test_leading_dash_is_refused(self) -> None:
        for value in ("-f", "--force", "--upload-pack=/bin/sh", "-"):
            self.assertFalse(is_safe_argument(value), value)

    def test_control_characters_are_refused(self) -> None:
        for value in ("ma\rin", "ma\nin", "ma\tin", "ma\x00in", "ma\x1bin"):
            self.assertFalse(is_safe_argument(value), repr(value))

    def test_empty_and_non_strings_are_refused(self) -> None:
        for value in ("", None, 5, [], object()):
            self.assertFalse(is_safe_argument(value))  # type: ignore[arg-type]


class BranchNameTests(unittest.TestCase):
    def test_accepts_ordinary_names(self) -> None:
        for name in (
            "main",
            "master",
            "develop",
            "feature/login",
            "feature/JIRA-123/fix",
            "release-1.2.3",
            "user.name/topic",
            "ümlaut-branch",
        ):
            self.assertTrue(is_valid_branch_name(name), name)

    def test_refuses_git_forbidden_characters(self) -> None:
        for name in (
            "with space",
            "tilde~1",
            "caret^",
            "colon:name",
            "question?",
            "star*",
            "bracket[1]",
            "back\\slash",
        ):
            self.assertFalse(is_valid_branch_name(name), name)

    def test_refuses_double_dot_and_at_brace(self) -> None:
        self.assertFalse(is_valid_branch_name("a..b"))
        self.assertFalse(is_valid_branch_name("a@{1}"))
        self.assertFalse(is_valid_branch_name("@"))

    def test_refuses_bad_slash_placement(self) -> None:
        for name in ("/leading", "trailing/", "double//slash", "a//b"):
            self.assertFalse(is_valid_branch_name(name), name)

    def test_refuses_dot_and_lock_edges(self) -> None:
        for name in ("ends.", "feature/.hidden", ".hidden", "thing.lock", "a/b.lock"):
            self.assertFalse(is_valid_branch_name(name), name)

    def test_refuses_option_lookalikes(self) -> None:
        for name in ("--force", "-b", "--upload-pack=x"):
            self.assertFalse(is_valid_branch_name(name), name)

    def test_length_limit(self) -> None:
        self.assertTrue(is_valid_branch_name("a" * MAX_REF_LENGTH))
        self.assertFalse(is_valid_branch_name("a" * (MAX_REF_LENGTH + 1)))

    def test_reason_key_only_for_invalid_names(self) -> None:
        self.assertIsNone(invalid_reason_key("main"))
        self.assertEqual("branch.invalid_name_hint", invalid_reason_key("bad name"))


class RevisionTests(unittest.TestCase):
    def test_accepts_object_ids(self) -> None:
        for value in ("abcd", "1234567", "a" * 40, "b" * 64):
            self.assertTrue(is_valid_revision(value), value)

    def test_accepts_special_heads(self) -> None:
        for value in ("HEAD", "ORIG_HEAD", "MERGE_HEAD", "FETCH_HEAD"):
            self.assertTrue(is_valid_revision(value), value)

    def test_accepts_navigation_suffixes(self) -> None:
        for value in ("HEAD~1", "HEAD^", "HEAD~10", "main^", "main~3", "v1.0^{}"):
            self.assertTrue(is_valid_revision(value), value)

    def test_refuses_unsafe_revisions(self) -> None:
        for value in ("--all", "-1", "rev with space", "a\rb", ""):
            self.assertFalse(is_valid_revision(value), repr(value))


class SuggestionTests(unittest.TestCase):
    def test_turns_prose_into_a_branch_name(self) -> None:
        self.assertEqual("fix-login-bug", suggest_branch_name("Fix login bug"))

    def test_collapses_separators(self) -> None:
        self.assertEqual("a-b", suggest_branch_name("a  ---  b"))

    def test_keeps_slashes(self) -> None:
        self.assertEqual("feature/login", suggest_branch_name("feature/Login"))

    def test_strips_unusable_edges(self) -> None:
        self.assertEqual("topic", suggest_branch_name("...topic..."))

    def test_every_suggestion_is_valid_or_empty(self) -> None:
        for text in (
            "Fix login bug",
            "...",
            "   ",
            "--force",
            "a" * 400,
            "feature//double",
            "ends.lock",
            "@{1}",
            "~^:?*[",
        ):
            suggestion = suggest_branch_name(text)
            if suggestion:
                self.assertTrue(is_valid_branch_name(suggestion), f"{text!r} -> {suggestion!r}")

    def test_non_string_input(self) -> None:
        self.assertEqual("", suggest_branch_name(None))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
