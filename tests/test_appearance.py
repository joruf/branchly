"""
Tests for what the window says about itself: the project tooltip and the
appearance menu.

Both replace something that was almost right. The sidebar used to put the path
and the last check into a plain two-line tooltip, which left out the one thing
people ask about a project they did not set up themselves, namely which server
it belongs to. And light and dark used to live in a settings dialog, three
clicks and an OK away from the thing being changed, which is a strange place for
a switch whose whole effect is visible immediately.

The tooltip is checked as text rather than by hovering: Qt decides on its own
whether a string is rich text, so what matters is that the string really is
markup and really carries both facts, with anything that came off disk escaped.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMenu

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n
from config.theme import DEFAULT_THEME, THEME_DARK, THEME_LIGHT, set_current_theme
from models.repository import RepoEntry, RepoStatus

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


@requires_qt
class RepositoryTooltipTests(unittest.TestCase):
    """
    The hover text of a project row.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def setUp(self) -> None:
        set_current_theme(DEFAULT_THEME)
        # A real folder, because a missing one is a different tooltip.
        holder = TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.folder = Path(holder.name) / "reminders"
        (self.folder / ".git").mkdir(parents=True)

    def _entry(self, **overrides: object) -> RepoEntry:
        """
        Builds an entry to describe.

        Args:
            **overrides: Fields to change.

        Returns:
            RepoEntry: The entry.
        """

        fields: dict = {
            "path": self.folder,
            "name": "reminders",
            "remote_url": "https://github.com/someone/reminders.git",
            "status": RepoStatus(checked_at=1_700_000_000.0),
        }
        fields.update(overrides)
        return RepoEntry(**fields)

    def _tooltip(self, **overrides: object) -> str:
        """
        Builds the tooltip for an entry.

        Args:
            **overrides: Entry fields to change.

        Returns:
            str: The tooltip.
        """

        from ui.sidebar import repo_tooltip

        return repo_tooltip(self._entry(**overrides), 1_700_000_060.0)

    def test_it_names_the_server_the_project_belongs_to(self) -> None:
        tooltip = self._tooltip()
        self.assertIn("https://github.com/someone/reminders.git", tooltip)
        self.assertIn(i18n.t("repo.tip_remote"), tooltip)

    def test_it_names_the_local_copy(self) -> None:
        tooltip = self._tooltip()
        self.assertIn(str(self.folder), tooltip)
        self.assertIn(i18n.t("repo.tip_local"), tooltip)

    def test_the_two_facts_sit_under_each_other(self) -> None:
        # A table, not one run-on line. Qt renders a tooltip as rich text as soon
        # as the string looks like markup, so no custom popup is needed.
        tooltip = self._tooltip()
        self.assertIn("<table", tooltip)
        self.assertLess(tooltip.index(i18n.t("repo.tip_remote")), tooltip.index("</table>"))
        self.assertLess(tooltip.index(i18n.t("repo.tip_local")), tooltip.index("</table>"))

    def test_it_still_says_when_the_project_was_last_looked_at(self) -> None:
        from ui.widgets import format_relative_check_time

        expected = format_relative_check_time(1_700_000_000.0, 1_700_000_060.0)
        self.assertIn(expected, self._tooltip())

    def test_a_project_without_a_server_says_so(self) -> None:
        tooltip = self._tooltip(remote_url="")
        self.assertIn(i18n.t("repo.tip_no_remote"), tooltip)

    def test_a_folder_that_is_gone_says_so_instead_of_a_check_time(self) -> None:
        tooltip = self._tooltip(path=self.folder.parent / "gone-for-good")
        self.assertIn(i18n.t("repo.missing"), tooltip)
        # The path is still worth showing: it is how the user finds out what
        # moved where.
        self.assertIn("gone-for-good", tooltip)

    def test_a_name_that_looks_like_markup_is_escaped(self) -> None:
        # Names are user input and paths come off disk. Either can hold an angle
        # bracket, and rich text would swallow everything after it.
        tooltip = self._tooltip(name="<b>weird</b> & co")
        self.assertIn("&lt;b&gt;weird&lt;/b&gt; &amp; co", tooltip)
        self.assertNotIn("<b>weird", tooltip)

    def test_the_whole_row_carries_it_not_only_the_name(self) -> None:
        from ui.sidebar import RepoRow

        row = RepoRow(self._entry())
        self.addCleanup(row.deleteLater)
        self.assertIn("https://github.com/someone/reminders.git", row.toolTip())
        self.assertEqual(row.toolTip(), row._name.toolTip())


@requires_qt
class AppearanceMenuTests(unittest.TestCase):
    """
    Light and dark, now a menu entry rather than a settings field.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("en")

    def tearDown(self) -> None:
        set_current_theme(DEFAULT_THEME)

    def _window(self, base: Path):  # noqa: ANN202 - the type needs Qt at import time
        """
        Builds a window with no projects in it.

        Args:
            base: Directory to keep the config in.

        Returns:
            MainWindow: The window.
        """

        from config.app_settings import AppSettings
        from ui.main_window import MainWindow

        os.environ["XDG_CONFIG_HOME"] = str(base / "config")
        window = MainWindow(
            AppSettings(
                theme=THEME_DARK,
                auto_check_minutes=0,
                github_enabled=False,
                language="en",
            )
        )
        self.addCleanup(window.close)
        return window

    def test_the_menu_bar_offers_it(self) -> None:
        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            titles = [menu.title() for menu in window.menuBar().findChildren(QMenu)]
            self.assertIn(i18n.t("menu.view"), titles)
            self.assertIn(i18n.t("menu.appearance"), titles)

    def test_every_theme_is_listed_and_the_active_one_is_ticked(self) -> None:
        from config.theme import available_themes

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            self.assertEqual(set(available_themes()), set(window._theme_actions))
            for name, action in window._theme_actions.items():
                with self.subTest(theme=name):
                    self.assertTrue(action.isCheckable())
                    self.assertEqual(name == THEME_DARK, action.isChecked())
                    self.assertTrue(action.toolTip().strip())

    def test_picking_one_switches_and_stores_it(self) -> None:
        from config.app_settings import load_settings

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            window._choose_theme(THEME_LIGHT)

            self.assertEqual(THEME_LIGHT, window._settings.theme)
            self.assertTrue(window._theme_actions[THEME_LIGHT].isChecked())
            self.assertFalse(window._theme_actions[THEME_DARK].isChecked())
            # Stored right away, because a menu entry has no OK button to press.
            self.assertEqual(THEME_LIGHT, load_settings().theme)

    def test_it_repaints_at_once_rather_than_on_the_next_start(self) -> None:
        from config.theme import get_theme_colors

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            window._choose_theme(THEME_LIGHT)
            self.assertIn(get_theme_colors(THEME_LIGHT).surface, self.app.styleSheet())

    def test_picking_the_theme_already_on_does_nothing(self) -> None:
        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            painted: list[str] = []
            window._apply_theme = lambda name: painted.append(name)

            window._choose_theme(THEME_DARK)
            self.assertEqual([], painted)

    def test_settings_no_longer_offers_it(self) -> None:
        from ui.settings_dialog import SettingsDialog

        with TemporaryDirectory() as base:
            window = self._window(Path(base))
            window._choose_theme(THEME_LIGHT)
            dialog = SettingsDialog(window._settings, window)
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(lambda: dialog.done(0))

            self.assertFalse(hasattr(dialog, "_theme"))
            # And a trip through the dialog must not undo the menu's choice.
            self.assertEqual(THEME_LIGHT, dialog.result_settings().theme)


if __name__ == "__main__":
    unittest.main()
