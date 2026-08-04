"""
Tests for settings validation and persistence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from config.app_settings import (
    AUTO_CHECK_PRESETS,
    DEFAULT_AUTO_CHECK_MINUTES,
    DEFAULT_DIFF_MODE,
    DEFAULT_UPDATE_CHECK_HOURS,
    DIFF_SIDE_BY_SIDE,
    DIFF_UNIFIED,
    MAX_AUTO_CHECK_MINUTES,
    MAX_UPDATE_CHECK_HOURS,
    AppSettings,
    load_settings,
    normalize_auto_check_minutes,
    normalize_diff_mode,
    normalize_timestamp,
    normalize_update_check_hours,
    save_settings,
)
from config.theme import DEFAULT_THEME, THEME_LIGHT
from models.sort import DEFAULT_SORT_MODE, SORT_NEWEST_COMMIT


class NormalizationTests(unittest.TestCase):
    def test_diff_mode_accepts_known_values(self) -> None:
        self.assertEqual(DIFF_UNIFIED, normalize_diff_mode(DIFF_UNIFIED))
        self.assertEqual(DIFF_SIDE_BY_SIDE, normalize_diff_mode("  SIDE_BY_SIDE "))

    def test_diff_mode_rejects_junk(self) -> None:
        for value in (None, "", "three_way", 5, [], object()):
            self.assertEqual(DEFAULT_DIFF_MODE, normalize_diff_mode(value))

    def test_auto_check_zero_disables(self) -> None:
        self.assertEqual(0, normalize_auto_check_minutes(0))
        self.assertEqual(0, normalize_auto_check_minutes(-30))

    def test_auto_check_is_clamped(self) -> None:
        self.assertEqual(MAX_AUTO_CHECK_MINUTES, normalize_auto_check_minutes(999_999))

    def test_auto_check_rejects_non_numbers(self) -> None:
        for value in (None, "15", True, [], {}):
            self.assertEqual(DEFAULT_AUTO_CHECK_MINUTES, normalize_auto_check_minutes(value))

    def test_presets_are_all_valid(self) -> None:
        for preset in AUTO_CHECK_PRESETS:
            self.assertEqual(preset, normalize_auto_check_minutes(preset))

    def test_update_hours_zero_checks_on_every_start(self) -> None:
        self.assertEqual(0, normalize_update_check_hours(0))
        self.assertEqual(0, normalize_update_check_hours(-3))

    def test_update_hours_are_clamped(self) -> None:
        self.assertEqual(MAX_UPDATE_CHECK_HOURS, normalize_update_check_hours(100_000))

    def test_update_hours_reject_non_numbers(self) -> None:
        for value in (None, "24", True, [], {}):
            self.assertEqual(DEFAULT_UPDATE_CHECK_HOURS, normalize_update_check_hours(value))

    def test_a_missing_or_absurd_check_time_reads_as_never(self) -> None:
        for value in (None, "yesterday", True, -1, 0, []):
            self.assertEqual(0.0, normalize_timestamp(value))

    def test_a_real_check_time_survives(self) -> None:
        self.assertEqual(1_700_000_000.0, normalize_timestamp(1_700_000_000))


class AppSettingsTests(unittest.TestCase):
    def test_defaults_are_sane(self) -> None:
        settings = AppSettings()
        self.assertEqual(DEFAULT_THEME, settings.theme)
        self.assertEqual(DEFAULT_SORT_MODE, settings.sort_mode)
        self.assertEqual(DEFAULT_AUTO_CHECK_MINUTES, settings.auto_check_minutes)
        self.assertTrue(settings.confirm_destructive)
        self.assertTrue(settings.check_updates)
        self.assertEqual(DEFAULT_UPDATE_CHECK_HOURS, settings.update_check_hours)
        self.assertEqual(0.0, settings.update_checked_at)

    def test_normalized_fixes_bad_values(self) -> None:
        settings = AppSettings(theme="neon", sort_mode="whatever", diff_mode="nope", auto_check_minutes=-5)
        fixed = settings.normalized()
        self.assertEqual(DEFAULT_THEME, fixed.theme)
        self.assertEqual(DEFAULT_SORT_MODE, fixed.sort_mode)
        self.assertEqual(DEFAULT_DIFF_MODE, fixed.diff_mode)
        self.assertEqual(0, fixed.auto_check_minutes)

    def test_round_trip_through_dict(self) -> None:
        settings = AppSettings(theme=THEME_LIGHT, sort_mode=SORT_NEWEST_COMMIT, auto_check_minutes=30)
        restored = AppSettings.from_dict(settings.to_dict())
        self.assertEqual(settings.to_dict(), restored.to_dict())

    def test_unknown_keys_are_dropped(self) -> None:
        restored = AppSettings.from_dict({"theme": THEME_LIGHT, "colour_scheme": "purple"})
        self.assertEqual(THEME_LIGHT, restored.theme)

    def test_from_dict_survives_garbage(self) -> None:
        for value in (None, [], "text", 42):
            self.assertEqual(DEFAULT_THEME, AppSettings.from_dict(value).theme)


class PersistenceTests(unittest.TestCase):
    def test_save_then_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "settings.json"
            settings = AppSettings(theme=THEME_LIGHT, language="de", auto_check_minutes=60)
            self.assertTrue(save_settings(settings, target))
            loaded = load_settings(target)
            self.assertEqual(THEME_LIGHT, loaded.theme)
            self.assertEqual("de", loaded.language)
            self.assertEqual(60, loaded.auto_check_minutes)

    def test_the_update_check_time_survives_a_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            save_settings(
                AppSettings(check_updates=False, update_checked_at=1_700_000_000.0), target
            )
            loaded = load_settings(target)
            self.assertFalse(loaded.check_updates)
            self.assertEqual(1_700_000_000.0, loaded.update_checked_at)

    def test_missing_file_yields_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_settings(Path(tmp) / "absent.json")
            self.assertEqual(DEFAULT_THEME, loaded.theme)

    def test_corrupt_file_yields_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("{ this is not json", encoding="utf-8")
            self.assertEqual(DEFAULT_THEME, load_settings(target).theme)

    def test_no_temp_file_is_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            save_settings(AppSettings(), target)
            leftovers = [item.name for item in Path(tmp).iterdir() if item.name.endswith(".tmp")]
            self.assertEqual([], leftovers)

    def test_written_file_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            save_settings(AppSettings(language="de"), target)
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual("de", data["language"])


if __name__ == "__main__":
    unittest.main()
