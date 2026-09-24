"""
Tests for the theme tokens and stylesheet generation.

The completeness test is what keeps a newly added theme from shipping with
missing colors: every theme must define every token, and every token must be a
usable color string.

The disabled-state tests are there for a different failure. A Qt stylesheet
gives an ID selector more weight than a pseudo-class, so a rule for
``QPushButton#Primary`` quietly beats ``QPushButton:disabled`` and the button
keeps its full accent colour while refusing every click. That looks like a
broken program rather than a control waiting for something, and it cannot be
seen by reading the sheet, so it is rendered and compared.
"""

from __future__ import annotations

import os
import re
import unittest
from dataclasses import FrozenInstanceError, fields

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFrame,
        QLabel,
        QLineEdit,
        QPushButton,
        QRadioButton,
        QTabWidget,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - PySide6 missing is a valid environment
    QT_AVAILABLE = False

from config.theme import (
    DEFAULT_THEME,
    THEME_DARK,
    THEME_LIGHT,
    VALID_THEMES,
    ThemeColors,
    available_themes,
    build_application_stylesheet,
    current_theme_name,
    get_theme_colors,
    is_light_theme,
    lane_color,
    normalize_theme_name,
    set_current_theme,
)

HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

requires_qt = unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed")


class ThemeRegistryTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_current_theme(DEFAULT_THEME)

    def test_dark_and_light_exist(self) -> None:
        self.assertIn(THEME_DARK, VALID_THEMES)
        self.assertIn(THEME_LIGHT, VALID_THEMES)

    def test_available_themes_matches_valid_themes(self) -> None:
        self.assertEqual(set(VALID_THEMES), set(available_themes()))

    def test_normalize_accepts_known_names(self) -> None:
        self.assertEqual(THEME_LIGHT, normalize_theme_name("  LIGHT "))

    def test_normalize_rejects_junk(self) -> None:
        for value in (None, "", "solarized", 7, [], object()):
            self.assertEqual(DEFAULT_THEME, normalize_theme_name(value))

    def test_set_and_read_current_theme(self) -> None:
        self.assertEqual(THEME_LIGHT, set_current_theme(THEME_LIGHT))
        self.assertEqual(THEME_LIGHT, current_theme_name())
        self.assertTrue(is_light_theme())
        set_current_theme(THEME_DARK)
        self.assertFalse(is_light_theme())

    def test_invalid_theme_falls_back_instead_of_raising(self) -> None:
        self.assertEqual(DEFAULT_THEME, set_current_theme("nonsense"))


class ThemeCompletenessTests(unittest.TestCase):
    def test_every_theme_defines_every_token(self) -> None:
        for name in available_themes():
            colors = get_theme_colors(name)
            for field in fields(ThemeColors):
                value = getattr(colors, field.name)
                self.assertIsNotNone(value, f"{name}.{field.name} is None")
                if field.name == "graph_lanes":
                    continue
                self.assertIsInstance(value, str, f"{name}.{field.name} is not a string")
                self.assertTrue(value, f"{name}.{field.name} is empty")

    def test_every_color_is_a_hex_string(self) -> None:
        for name in available_themes():
            colors = get_theme_colors(name)
            for field in fields(ThemeColors):
                if field.name == "graph_lanes":
                    continue
                value = getattr(colors, field.name)
                self.assertRegex(value, HEX_COLOR, f"{name}.{field.name} is not a hex color")

    def test_graph_lanes_are_non_empty_hex_tuples(self) -> None:
        for name in available_themes():
            lanes = get_theme_colors(name).graph_lanes
            self.assertIsInstance(lanes, tuple)
            self.assertGreaterEqual(len(lanes), 4, f"{name} has too few lane colors")
            for value in lanes:
                self.assertRegex(value, HEX_COLOR)

    def test_lane_color_cycles_and_never_fails(self) -> None:
        lanes = get_theme_colors(THEME_DARK).graph_lanes
        self.assertEqual(lanes[0], lane_color(0, THEME_DARK))
        self.assertEqual(lanes[0], lane_color(len(lanes), THEME_DARK))
        self.assertRegex(lane_color(-5, THEME_DARK), HEX_COLOR)
        self.assertRegex(lane_color(9999, THEME_DARK), HEX_COLOR)

    def test_themes_are_frozen(self) -> None:
        colors = get_theme_colors(THEME_DARK)
        # A frozen dataclass raises FrozenInstanceError, which is what is
        # being checked: the theme cannot be edited in place.
        with self.assertRaises(FrozenInstanceError):
            colors.accent = "#000000"  # type: ignore[misc]


class StylesheetTests(unittest.TestCase):
    def test_stylesheet_is_generated_for_every_theme(self) -> None:
        for name in available_themes():
            sheet = build_application_stylesheet(name)
            self.assertIn("QWidget", sheet)
            self.assertIn("QPushButton", sheet)
            self.assertGreater(len(sheet), 1000)

    def test_stylesheet_has_no_unresolved_placeholders(self) -> None:
        for name in available_themes():
            sheet = build_application_stylesheet(name)
            self.assertNotIn("{c.", sheet)
            self.assertNotIn("None", sheet)

    def test_stylesheet_braces_are_balanced(self) -> None:
        for name in available_themes():
            sheet = build_application_stylesheet(name)
            self.assertEqual(sheet.count("{"), sheet.count("}"), f"{name} stylesheet is unbalanced")

    def test_themes_produce_different_stylesheets(self) -> None:
        self.assertNotEqual(
            build_application_stylesheet(THEME_DARK),
            build_application_stylesheet(THEME_LIGHT),
        )


@requires_qt
class DisabledStateTests(unittest.TestCase):
    """
    A control that cannot be used has to look like it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        set_current_theme(DEFAULT_THEME)

    def _render(self, make, enabled: bool):  # noqa: ANN001, ANN202 - Qt types
        """
        Renders one control inside a painted panel.

        Args:
            make: Callable building the control.
            enabled: Whether the control is usable.

        Returns:
            QImage: What the panel looks like.
        """

        shell = QFrame()
        self.addCleanup(shell.deleteLater)
        shell.setObjectName("Panel")
        box = QVBoxLayout(shell)
        box.setContentsMargins(6, 6, 6, 6)
        widget = make()
        widget.setEnabled(enabled)
        box.addWidget(widget)
        shell.resize(240, 48)
        return shell.grab().toImage()

    def _changed_pixels(self, make) -> int:  # noqa: ANN001 - Qt types
        """
        Counts how many pixels move when the control is switched off.

        Args:
            make: Callable building the control.

        Returns:
            int: Number of differing pixels.
        """

        on = self._render(make, True)
        off = self._render(make, False)
        return sum(
            1
            for y in range(on.height())
            for x in range(on.width())
            if on.pixelColor(x, y) != off.pixelColor(x, y)
        )

    def _controls(self) -> dict:
        """
        Returns every control kind that can end up unusable.

        Returns:
            dict: Names mapped to builders.
        """

        return {
            "QPushButton": lambda: QPushButton("Commit to main"),
            "QPushButton#Primary": lambda: _named(QPushButton("Commit to main"), "Primary"),
            "QPushButton#Danger": lambda: _named(QPushButton("Delete branch"), "Danger"),
            "QPushButton#Link": lambda: _named(QPushButton("Show more"), "Link"),
            "QToolButton": _tool_button,
            "QCheckBox": lambda: QCheckBox("Include this file"),
            "QCheckBox checked": _checked_box,
            "QRadioButton": lambda: QRadioButton("Merge"),
            "QLabel": lambda: QLabel("Ready to commit"),
            "QComboBox": _combo,
            "QLineEdit": lambda: QLineEdit("Summary"),
            "QTabWidget": _tabs,
        }

    def test_every_control_shows_that_it_cannot_be_used(self) -> None:
        for theme in available_themes():
            set_current_theme(theme)
            self.app.setStyleSheet(build_application_stylesheet(theme))
            for name, make in self._controls().items():
                with self.subTest(theme=theme, control=name):
                    self.assertGreater(self._changed_pixels(make), 0)

    def test_an_accented_button_greys_out_rather_than_staying_blue(self) -> None:
        # The case that started this: "Save 3 files to main" stayed a solid
        # accent button while it refused every click.
        for theme in available_themes():
            set_current_theme(theme)
            self.app.setStyleSheet(build_application_stylesheet(theme))
            colors = get_theme_colors(theme)
            image = self._render(
                lambda: _named(QPushButton("Commit to main"), "Primary"), False
            )
            accent = image.pixelColor(20, 24).name().lower()
            with self.subTest(theme=theme):
                self.assertNotEqual(colors.accent.lower(), accent)
                self.assertEqual(colors.disabled_bg.lower(), accent)

    def test_the_sheet_spells_out_the_accented_buttons(self) -> None:
        # An ID selector outranks a pseudo-class, so the plain
        # ``QPushButton:disabled`` rule never reaches these.
        for theme in available_themes():
            sheet = build_application_stylesheet(theme)
            for selector in (
                "QPushButton#Primary:disabled",
                "QPushButton#Danger:disabled",
                "QPushButton#Link:disabled",
            ):
                with self.subTest(theme=theme, selector=selector):
                    self.assertIn(selector, sheet)

    def test_the_disabled_text_is_dimmer_than_muted_text(self) -> None:
        # Muted text is still meant to be read. This is meant to be skipped.
        for theme in available_themes():
            colors = get_theme_colors(theme)
            with self.subTest(theme=theme):
                self.assertNotEqual(colors.text_muted, colors.disabled_text)


def _named(widget, name: str):  # noqa: ANN001, ANN202 - Qt types
    """
    Sets an object name and returns the widget.

    Args:
        widget: Widget to name.
        name: Object name to apply.

    Returns:
        QWidget: The same widget.
    """

    widget.setObjectName(name)
    return widget


def _tool_button():  # noqa: ANN202 - Qt types
    """
    Builds a labelled tool button.

    Returns:
        QToolButton: The button.
    """

    button = QToolButton()
    button.setText("Filter")
    return button


def _checked_box():  # noqa: ANN202 - Qt types
    """
    Builds a ticked checkbox.

    Returns:
        QCheckBox: The box.
    """

    box = QCheckBox("Include this file")
    box.setChecked(True)
    return box


def _combo():  # noqa: ANN202 - Qt types
    """
    Builds a combo box with one entry.

    Returns:
        QComboBox: The box.
    """

    combo = QComboBox()
    combo.addItem("Newest first")
    return combo


def _tabs():  # noqa: ANN202 - Qt types
    """
    Builds a tab widget with two tabs.

    Returns:
        QTabWidget: The widget.
    """

    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Changes")
    tabs.addTab(QWidget(), "History")
    return tabs


if __name__ == "__main__":
    unittest.main()
