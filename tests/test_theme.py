"""
Tests for the theme tokens and stylesheet generation.

The completeness test is what keeps a newly added theme from shipping with
missing colors: every theme must define every token, and every token must be a
usable color string.
"""

from __future__ import annotations

import re
import unittest
from dataclasses import fields

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
        with self.assertRaises(Exception):
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


if __name__ == "__main__":
    unittest.main()
