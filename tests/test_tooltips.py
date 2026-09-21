"""
Tests for the explanations attached to the controls.

Two things can go wrong with a tooltip and neither shows up as a crash. A key
that is not in the catalogue renders as the key itself, so the user reads
``tip.pr_merge`` and learns nothing. And a control added later simply has no
tooltip at all, which nobody notices because everything still works.

So this checks both: every key the code asks for exists in both languages, and
every control in the parts of the window that are always on screen carries an
explanation.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QPushButton,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

import i18n

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "locales"

# Keys reached through i18n.t("...") directly.
_CALL = re.compile(r'i18n\.t\(\s*["\']([a-z0-9_.]+)["\']')
# Tooltip keys handed to a helper as a plain string, which the call pattern
# above cannot see.
_TIP_LITERAL = re.compile(r'["\'](tip\.[a-z0-9_]+)["\']')

# Controls whose own label is the entire explanation. Listing them is the point:
# adding a control means deciding whether it needs one, rather than forgetting.
SELF_EXPLANATORY = {
    "",  # unnamed helpers such as a plain OK button
}


def _source_files() -> list[Path]:
    """
    Returns the modules that may ask for a translation.

    Returns:
        list[Path]: Python files of the application, tests excluded.
    """

    found: list[Path] = []
    for folder in ("ui", "gitops", "github_api", "services", "config", "models"):
        found.extend(sorted((ROOT / folder).glob("*.py")))
    found.append(ROOT / "run.py")
    return [item for item in found if item.is_file()]


class CatalogueTests(unittest.TestCase):
    """
    Every key the code asks for has to be in both catalogues.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.english = json.loads((LOCALES / "en.json").read_text(encoding="utf-8"))
        cls.german = json.loads((LOCALES / "de.json").read_text(encoding="utf-8"))
        text = "\n".join(item.read_text(encoding="utf-8") for item in _source_files())
        cls.requested = set(_CALL.findall(text)) | set(_TIP_LITERAL.findall(text))

    def test_every_requested_key_exists(self) -> None:
        missing = sorted(key for key in self.requested if key not in self.english)
        # A missing key is not an error at runtime: it renders as the key itself,
        # which is why it has to be caught here.
        self.assertEqual([], missing)

    def test_both_languages_carry_it(self) -> None:
        missing = sorted(key for key in self.requested if key not in self.german)
        self.assertEqual([], missing)

    def test_no_tooltip_is_declared_and_then_forgotten(self) -> None:
        declared = {key for key in self.english if key.startswith("tip.")}
        self.assertEqual([], sorted(declared - self.requested))

    def test_tooltips_are_sentences_not_labels(self) -> None:
        # A tooltip repeating the button's own label teaches nothing. They are
        # not all full sentences, but none of them is a single word.
        for key, value in self.english.items():
            if not key.startswith("tip."):
                continue
            with self.subTest(key=key):
                self.assertGreaterEqual(len(value.split()), 4, value)


@requires_qt
class CoverageTests(unittest.TestCase):
    """
    The controls that are always on screen have to carry an explanation.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        i18n.set_language("de")

    def _controls(self, widget: object) -> list[object]:
        """
        Collects the interactive children of a widget.

        Args:
            widget: Widget to walk.

        Returns:
            list[object]: Buttons, boxes and fields found below it.
        """

        found: list[object] = []
        for kind in (QPushButton, QCheckBox, QComboBox, QLineEdit, QPlainTextEdit):
            found.extend(widget.findChildren(kind))
        return found

    def _assert_explained(self, widget: object, allow: set[str] | None = None) -> None:
        """
        Fails when a control has no tooltip.

        Args:
            widget: Widget to walk.
            allow: Labels that need none, because the label says everything.

        Returns:
            None
        """

        permitted = (allow or set()) | SELF_EXPLANATORY
        naked: list[str] = []
        for control in self._controls(widget):
            label = control.text() if hasattr(control, "text") else ""
            if not isinstance(label, str):
                label = ""
            if label in permitted:
                continue
            if not control.toolTip().strip():
                naked.append(f"{type(control).__name__}({label!r})")
        self.assertEqual([], naked)

    def test_the_changes_panel_explains_itself(self) -> None:
        from ui.changes_panel import ChangesPanel

        panel = ChangesPanel()
        self.addCleanup(panel.deleteLater)
        self._assert_explained(panel)

    def test_the_diff_toolbar_explains_itself(self) -> None:
        from config.app_settings import DIFF_SIDE_BY_SIDE
        from ui.diff_view import DiffView

        view = DiffView(DIFF_SIDE_BY_SIDE, False, True)
        self.addCleanup(view.deleteLater)
        self._assert_explained(view)

    def test_the_sidebar_explains_itself(self) -> None:
        from services.registry import Registry
        from ui.sidebar import Sidebar

        sidebar = Sidebar(Registry(), "name_asc")
        self.addCleanup(sidebar.deleteLater)
        self._assert_explained(sidebar)

    def test_the_github_panel_explains_itself(self) -> None:
        from github_api.client import GitHubClient
        from ui.github_panel import GitHubPanel

        client = GitHubClient("")
        self.addCleanup(client.close)
        panel = GitHubPanel(client, show_avatars=False)
        self.addCleanup(panel.stop)
        self.addCleanup(panel.deleteLater)
        self._assert_explained(panel)


if __name__ == "__main__":
    unittest.main()
