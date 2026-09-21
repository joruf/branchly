"""
Tests for the translation layer.

The key-parity test is the important one: it is the only thing that stops the
German and English catalogs from drifting apart as the UI grows.
"""

from __future__ import annotations

import json
import re
import unittest

import i18n

PLACEHOLDER_PATTERN = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _load(code: str) -> dict[str, str]:
    """
    Reads a locale file directly, bypassing the translator.

    Args:
        code: Language code.

    Returns:
        dict[str, str]: Raw catalog.
    """

    path = i18n.LOCALES_DIR / f"{code}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _duplicate_keys(code: str) -> list[str]:
    """
    Finds keys a locale file lists more than once.

    Args:
        code: Language code.

    Returns:
        list[str]: Duplicated keys, sorted.
    """

    seen: list[str] = []

    def collect(pairs: list[tuple[str, object]]) -> dict:
        seen.extend(key for key, _value in pairs)
        return dict(pairs)

    path = i18n.LOCALES_DIR / f"{code}.json"
    json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=collect)
    return sorted({key for key in seen if seen.count(key) > 1})


class LocaleFileTests(unittest.TestCase):
    def test_locales_directory_exists(self) -> None:
        self.assertTrue(i18n.LOCALES_DIR.is_dir())

    def test_english_and_german_exist(self) -> None:
        codes = {code for code, _ in i18n.available_languages()}
        self.assertIn("en", codes)
        self.assertIn("de", codes)

    def test_every_locale_has_a_label(self) -> None:
        for code, label in i18n.available_languages():
            self.assertTrue(label, f"{code} has no display label")

    def test_no_key_is_written_twice(self) -> None:
        # JSON keeps the last of two identical keys and says nothing, so a
        # duplicate silently replaces an earlier text with an unrelated one.
        # Only the raw file can show it; the parsed catalog cannot.
        for code in ("en", "de"):
            with self.subTest(code=code):
                self.assertEqual([], _duplicate_keys(code))

    def test_key_sets_are_identical(self) -> None:
        english = set(_load("en"))
        german = set(_load("de"))
        missing_in_german = sorted(english - german)
        missing_in_english = sorted(german - english)
        self.assertEqual([], missing_in_german, "keys missing from de.json")
        self.assertEqual([], missing_in_english, "keys missing from en.json")

    def test_placeholders_match_between_languages(self) -> None:
        english = _load("en")
        german = _load("de")
        for key, template in english.items():
            expected = set(PLACEHOLDER_PATTERN.findall(template))
            actual = set(PLACEHOLDER_PATTERN.findall(german.get(key, "")))
            self.assertEqual(expected, actual, f"placeholder mismatch for {key}")

    def test_no_empty_values(self) -> None:
        for code in ("en", "de"):
            for key, value in _load(code).items():
                self.assertTrue(value.strip(), f"{code}.json has an empty value for {key}")


class TranslatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.translator = i18n.Translator()

    def test_defaults_to_english(self) -> None:
        self.assertEqual("en", self.translator.language)

    def test_switching_language_changes_output(self) -> None:
        english = self.translator.get("action.cancel")
        self.translator.set_language("de")
        self.assertEqual("de", self.translator.language)
        self.assertNotEqual(english, self.translator.get("action.cancel"))

    def test_unknown_language_is_ignored(self) -> None:
        self.translator.set_language("de")
        self.translator.set_language("xx")
        self.assertEqual("de", self.translator.language)

    def test_unknown_key_returns_the_key(self) -> None:
        self.assertEqual("no.such.key", self.translator.get("no.such.key"))

    def test_placeholders_are_filled(self) -> None:
        text = self.translator.get("sidebar.no_match", query="pmtool")
        self.assertIn("pmtool", text)

    def test_missing_placeholder_does_not_raise(self) -> None:
        text = self.translator.get("sidebar.no_match")
        self.assertIsInstance(text, str)

    def test_german_falls_back_to_english_for_missing_keys(self) -> None:
        self.translator.set_language("de")
        self.translator._catalog = {}
        self.assertEqual("Cancel", self.translator.get("action.cancel"))

    def test_plural_picks_the_right_form(self) -> None:
        i18n.set_language("en")
        one = i18n.plural(1, "status.changes_one", "status.changes_many")
        many = i18n.plural(4, "status.changes_one", "status.changes_many")
        self.assertNotIn("{count}", one)
        self.assertIn("4", many)


if __name__ == "__main__":
    unittest.main()
