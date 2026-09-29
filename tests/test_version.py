"""
Tests for the version number derived from the history.

Nobody types this number, which is the whole point, and also why nothing but a
test notices when the derivation goes wrong. A rule that counts the wrong
commits produces a number that looks every bit as plausible as the right one.

The rule, the same one servicereports and pmtool follow:

* minor counts commits that add a window, a new ``ui/*_dialog.py``
* patch counts commits since the last of those
* build counts every commit

And one principle that matters more than any of the digits: an unknown version
says "unknown". A made-up 0.0.0 looks real and sends a bug report the wrong way.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import version
from tests.support import requires_git

ROOT = Path(__file__).resolve().parent.parent


class Repo:
    """
    A throwaway repository to build histories in.
    """

    def __init__(self, root: Path) -> None:
        """
        Args:
            root: Folder to initialise.
        """

        self.root = root
        self.env = dict(os.environ)
        self.env.update(
            {
                "GIT_CONFIG_GLOBAL": str(root / ".gitconfig"),
                "GIT_CONFIG_SYSTEM": str(root / ".gitconfig-system"),
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "t@example.invalid",
            }
        )
        (root / "ui").mkdir(parents=True)
        self.git("init", "-b", "main")

    def git(self, *args: str) -> None:
        """
        Runs one git command.

        Args:
            *args: Arguments after ``git``.

        Returns:
            None
        """

        subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, check=True, capture_output=True
        )

    def commit(self, *paths: str, message: str = "change") -> None:
        """
        Writes or rewrites files and commits them.

        Args:
            *paths: Files relative to the root.
            message: Commit message.

        Returns:
            None
        """

        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            previous = target.read_text(encoding="utf-8") if target.exists() else ""
            target.write_text(previous + "x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-m", message)


def derive(root: Path):  # noqa: ANN201 - returns version.Version
    """
    Derives the version of a repository, bypassing every cache.

    Args:
        root: Working tree.

    Returns:
        Version: The derived version.
    """

    return version.from_git(root, version.history_marker(root))


@requires_git
class DerivationTests(unittest.TestCase):
    """
    How the three numbers come about.
    """

    def test_every_commit_counts_towards_the_build(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            for _index in range(5):
                repo.commit("notes.txt")
            self.assertEqual("5", derive(repo.root).build)

    def test_a_new_window_raises_the_minor_and_resets_the_patch(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("notes.txt")
            repo.commit("notes.txt")
            repo.commit("ui/first_dialog.py")
            self.assertEqual("0.1.0", derive(repo.root).name)

    def test_work_after_a_window_counts_as_patch(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("ui/first_dialog.py")
            repo.commit("notes.txt")
            repo.commit("notes.txt")
            self.assertEqual("0.1.2", derive(repo.root).name)

    def test_changing_an_existing_window_is_not_a_new_one(self) -> None:
        # Only adding the file counts. Editing it is ordinary work.
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("ui/first_dialog.py")
            repo.commit("ui/first_dialog.py")
            self.assertEqual("0.1.1", derive(repo.root).name)

    def test_several_windows_in_one_commit_count_once(self) -> None:
        # A feature counts at the commit that brings it, however many files.
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("ui/one_dialog.py", "ui/two_dialog.py", "ui/three_dialog.py")
            self.assertEqual("0.1.0", derive(repo.root).name)

    def test_a_file_that_is_not_a_window_does_not_count(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("ui/widgets.py")
            repo.commit("services/new_service.py")
            self.assertEqual("0.0.2", derive(repo.root).name)

    def test_the_major_is_the_constant(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("notes.txt")
            self.assertTrue(derive(repo.root).name.startswith(f"{version.MAJOR}."))

    def test_the_newest_commit_is_named(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("notes.txt")
            found = derive(repo.root)
            self.assertEqual(7, len(found.commit))
            self.assertRegex(found.date, r"^\d{4}-\d{2}-\d{2}$")

    def test_no_history_derives_nothing(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            self.assertIsNone(derive(repo.root))


@requires_git
class SourceTests(unittest.TestCase):
    """
    Which of the three sources answers.
    """

    def setUp(self) -> None:
        version.forget()
        self.addCleanup(version.forget)
        self.addCleanup(os.environ.pop, version.ENVIRONMENT_VARIABLE, None)

    def test_the_environment_wins(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("notes.txt")
            os.environ[version.ENVIRONMENT_VARIABLE] = "9.9.9 99 abcdef0 2026-01-01"
            self.assertEqual("9.9.9", version.current(repo.root).name)

    def test_a_checkout_answers_from_its_history(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("ui/first_dialog.py")
            self.assertEqual("0.1.0", version.current(repo.root).name)

    def test_reading_the_history_writes_the_file(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("notes.txt")
            version.current(repo.root)
            self.assertTrue((repo.root / "VERSION").is_file())

    def test_without_git_the_file_is_the_answer(self) -> None:
        # An installation unpacked from an archive has no history at all.
        with TemporaryDirectory() as base:
            root = Path(base)
            (root / "VERSION").write_text("0.4.2 17 1234567 2026-09-01 0\n", encoding="utf-8")
            self.assertEqual("0.4.2", version.current(root).name)

    def test_a_stale_file_is_replaced(self) -> None:
        with TemporaryDirectory() as base:
            repo = Repo(Path(base))
            repo.commit("notes.txt")
            (repo.root / "VERSION").write_text("0.9.9 1 0000000 2020-01-01 1\n", encoding="utf-8")
            self.assertEqual("0.0.1", version.current(repo.root).name)

    def test_nothing_to_go_on_is_called_unknown(self) -> None:
        # No invented 0.0.0: a number that looks real sends reports the wrong way.
        with TemporaryDirectory() as base:
            found = version.current(Path(base))
            self.assertFalse(found.is_known)
            self.assertEqual(version.UNKNOWN, found.label)


class LabelTests(unittest.TestCase):
    """
    How the version is written out.
    """

    def test_the_full_form(self) -> None:
        found = version.Version("0.8.4", "26", "4dda86e", "2026-09-29")
        self.assertEqual("0.8.4 (26) · 4dda86e · 29.09.2026", found.label)

    def test_the_date_is_written_day_first(self) -> None:
        self.assertIn("29.09.2026", version.Version("1.0.0", "1", "a", "2026-09-29").label)

    def test_the_file_line_round_trips(self) -> None:
        original = version.Version("0.8.4", "26", "4dda86e", "2026-09-29", "123")
        self.assertEqual(original, version.parse(original.line(), with_marker=True))


class RepositoryTests(unittest.TestCase):
    """
    The pieces around the module, in Branchly's own checkout.
    """

    def test_the_version_file_is_not_checked_in(self) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/VERSION", ignored.splitlines())

    def test_the_hook_ships_and_can_run(self) -> None:
        hook = ROOT / ".githooks" / "post-commit"
        self.assertTrue(hook.is_file())
        self.assertTrue(os.access(hook, os.X_OK))

    def test_the_constant_is_derived_not_typed(self) -> None:
        # The line that used to hold "0.3.0" by hand must not come back.
        text = (ROOT / "constants.py").read_text(encoding="utf-8")
        self.assertIn("APP_VERSION = _version.name()", text)
        self.assertNotRegex(text, r'APP_VERSION = "\d')


if __name__ == "__main__":
    unittest.main()
