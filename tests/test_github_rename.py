"""
Tests for following a repository that was renamed or moved on GitHub.

GitHub answers a request for the old name with the repository under its new
one, and git is redirected too, so nothing breaks at first. It breaks the day
somebody creates a repository under the old name. What has to hold:

* **The URL keeps its shape.** HTTPS stays HTTPS, SSH stays SSH, a user name in
  front of the host and a trailing ``.git`` survive. Only owner and name change.
* **Only a real difference counts.** The same name means nothing to do; a name
  the API did not deliver, or one that would not make a valid URL, is ignored.
* **A remote somebody changed meanwhile is left alone**, and a separate push URL
  moves only when it pointed at the old name.
* **The display name follows only when it was the old repository name.** One the
  user chose stays; the folder on disk is never renamed.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.app_settings import AppSettings
from gitops.remote_url import with_github_slug
from services import github_rename
from tests.support import requires_git, temp_repo

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

OLD = "https://github.com/joruf/mindwright.git"
NEW = "https://github.com/joruf/mindor.git"


class RewriteTests(unittest.TestCase):
    """
    Pointing a URL at a new name without changing how it is reached.
    """

    def test_every_form_keeps_its_shape(self) -> None:
        cases = {
            "https://github.com/joruf/mindwright.git": "https://github.com/joruf/mindor.git",
            "https://github.com/joruf/mindwright": "https://github.com/joruf/mindor",
            "https://joruf@github.com/joruf/mindwright.git": "https://joruf@github.com/joruf/mindor.git",
            "git@github.com:joruf/mindwright.git": "git@github.com:joruf/mindor.git",
            "ssh://git@github.com/joruf/mindwright.git": "ssh://git@github.com/joruf/mindor.git",
            "https://github.com/joruf/mindwright/": "https://github.com/joruf/mindor/",
        }
        for before, after in cases.items():
            with self.subTest(url=before):
                self.assertEqual(after, with_github_slug(before, "joruf", "mindor"))

    def test_a_new_owner_is_carried(self) -> None:
        self.assertEqual(
            "git@github.com:loresoft/mindor.git",
            with_github_slug("git@github.com:joruf/mindwright.git", "loresoft", "mindor"),
        )

    def test_other_hosts_are_not_touched(self) -> None:
        self.assertEqual("", with_github_slug("https://gitlab.com/joruf/x.git", "joruf", "y"))

    def test_a_name_github_would_not_allow_is_refused(self) -> None:
        for name in ("", "a/b", "x y", "..;rm", "-" * 101):
            with self.subTest(name=name):
                self.assertEqual("", with_github_slug(OLD, "joruf", name))


class DetectTests(unittest.TestCase):
    """
    Comparing the remote with what GitHub reported.
    """

    def test_a_rename_is_noticed(self) -> None:
        rename = github_rename.detect(OLD, "joruf/mindor")
        self.assertIsNotNone(rename)
        self.assertEqual("joruf/mindwright", rename.old_slug)
        self.assertEqual("joruf/mindor", rename.new_slug)
        self.assertEqual(NEW, rename.new_url)
        self.assertEqual(("mindwright", "mindor"), (rename.old_name, rename.new_name))

    def test_the_same_name_is_nothing_to_do(self) -> None:
        self.assertIsNone(github_rename.detect(OLD, "joruf/mindwright"))

    def test_a_changed_case_is_a_rename(self) -> None:
        rename = github_rename.detect(OLD, "joruf/MindWright")
        self.assertEqual("https://github.com/joruf/MindWright.git", rename.new_url)

    def test_missing_or_odd_answers_are_ignored(self) -> None:
        for reported in ("", "mindor", "joruf/", "/mindor", "a/b/c"):
            with self.subTest(reported=reported):
                self.assertIsNone(github_rename.detect(OLD, reported))

    def test_a_remote_elsewhere_is_ignored(self) -> None:
        self.assertIsNone(github_rename.detect("https://gitlab.com/joruf/mindwright.git", "joruf/mindor"))
        self.assertIsNone(github_rename.detect("", "joruf/mindor"))


@requires_git
class ApplyTests(unittest.TestCase):
    """
    Moving the remote itself.
    """

    def test_the_fetch_url_moves(self) -> None:
        with temp_repo() as repo:
            repo.git("remote", "add", "origin", OLD)
            outcome = github_rename.apply(repo.root, github_rename.detect(OLD, "joruf/mindor"))
            self.assertTrue(outcome.ok)
            self.assertEqual(NEW, repo.git("remote", "get-url", "origin").stdout.strip())

    def test_a_push_url_at_the_old_name_moves_too(self) -> None:
        with temp_repo() as repo:
            repo.git("remote", "add", "origin", OLD)
            repo.git("remote", "set-url", "--push", "origin", "git@github.com:joruf/mindwright.git")
            outcome = github_rename.apply(repo.root, github_rename.detect(OLD, "joruf/mindor"))
            self.assertTrue(outcome.push_moved)
            self.assertEqual(
                "git@github.com:joruf/mindor.git",
                repo.git("remote", "get-url", "--push", "origin").stdout.strip(),
            )

    def test_a_push_url_elsewhere_stays(self) -> None:
        with temp_repo() as repo:
            repo.git("remote", "add", "origin", OLD)
            repo.git("remote", "set-url", "--push", "origin", "https://example.invalid/mirror.git")
            outcome = github_rename.apply(repo.root, github_rename.detect(OLD, "joruf/mindor"))
            self.assertFalse(outcome.push_moved)
            self.assertEqual(
                "https://example.invalid/mirror.git",
                repo.git("remote", "get-url", "--push", "origin").stdout.strip(),
            )

    def test_a_remote_changed_meanwhile_wins(self) -> None:
        with temp_repo() as repo:
            repo.git("remote", "add", "origin", "https://github.com/joruf/other.git")
            outcome = github_rename.apply(repo.root, github_rename.detect(OLD, "joruf/mindor"))
            self.assertFalse(outcome.ok)
            self.assertEqual(
                "https://github.com/joruf/other.git",
                repo.git("remote", "get-url", "origin").stdout.strip(),
            )


@requires_git
@requires_qt
class WindowTests(unittest.TestCase):
    """
    What the window does once GitHub reported a new name.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def _window(self, base: Path, repo, name: str = ""):  # noqa: ANN001, ANN202 - Qt at import time
        """
        Builds a window on one project whose remote carries the old name.

        Args:
            base: Directory for the configuration.
            repo: The fixture.
            name: Display name to give the project, empty for the default.

        Returns:
            tuple: The window and the project's entry.
        """

        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        repo.git("remote", "add", "origin", OLD)
        window = MainWindow(AppSettings(auto_check_minutes=0, github_enabled=False, language="en"))
        self.addCleanup(window.close)
        _outcome, entry = window._registry.add(repo.root)
        if name:
            window._registry.rename(entry, name)
        entry.remote_url = OLD
        window._sidebar.refresh()
        window._activate(entry)
        window._switch_scan_timer.stop()
        return window, entry

    def test_the_remote_follows_and_the_user_is_told(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo, name="mindwright")
            result = window._follow_github_rename(entry.key, "joruf", "mindwright", "joruf/mindor")
            self.assertEqual(("joruf", "mindor"), result)
            self.assertEqual(NEW, repo.git("remote", "get-url", "origin").stdout.strip())
            self.assertEqual(NEW, entry.remote_url)
            self.assertEqual("mindor", entry.name)
            self.assertTrue(window._notice.isVisibleTo(window))
            self.assertEqual(
                i18n.t("github.renamed", new="joruf/mindor"), window._notice._title.text()
            )

    def test_a_chosen_display_name_stays(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo, name="Notes app")
            window._follow_github_rename(entry.key, "joruf", "mindwright", "joruf/mindor")
            self.assertEqual("Notes app", entry.name)

    def test_the_folder_is_not_renamed(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            window._follow_github_rename(entry.key, "joruf", "mindwright", "joruf/mindor")
            self.assertTrue(repo.root.is_dir())
            self.assertEqual(repo.root, entry.path)

    def test_nothing_happens_without_a_rename(self) -> None:
        with TemporaryDirectory() as base, temp_repo() as repo:
            window, entry = self._window(Path(base), repo)
            before = window._notice.isVisibleTo(window)
            result = window._follow_github_rename(
                entry.key, "joruf", "mindwright", "joruf/mindwright"
            )
            self.assertEqual(("joruf", "mindwright"), result)
            self.assertEqual(OLD, repo.git("remote", "get-url", "origin").stdout.strip())
            self.assertEqual(before, window._notice.isVisibleTo(window))


if __name__ == "__main__":
    unittest.main()
