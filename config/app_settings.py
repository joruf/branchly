"""
Application settings model and persistence.

Settings live in ``settings.json`` inside the user config directory. Every read
is defensive: a truncated or hand-edited file must degrade to defaults rather
than prevent the application from starting.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import paths
from config.theme import DEFAULT_THEME, normalize_theme_name
from models.sort import DEFAULT_SORT_MODE, normalize_sort_mode

DIFF_SIDE_BY_SIDE = "side_by_side"
DIFF_UNIFIED = "unified"
VALID_DIFF_MODES: tuple[str, ...] = (DIFF_SIDE_BY_SIDE, DIFF_UNIFIED)
DEFAULT_DIFF_MODE = DIFF_SIDE_BY_SIDE

DEFAULT_LANGUAGE = "en"

# Presets offered in the settings dialog. Any integer in range is accepted, so a
# hand-edited config keeps working; 0 disables the automatic check entirely.
AUTO_CHECK_PRESETS: tuple[int, ...] = (0, 5, 15, 30, 60, 120)
DEFAULT_AUTO_CHECK_MINUTES = 15
MAX_AUTO_CHECK_MINUTES = 1440

# How often the startup update check may contact GitHub. Not offered in the
# dialog — one checkbox is the decision users care about — but honoured when
# hand-edited, with 0 meaning "on every start".
DEFAULT_UPDATE_CHECK_HOURS = 24
MAX_UPDATE_CHECK_HOURS = 720


def normalize_diff_mode(mode: str | None) -> str:
    """
    Maps arbitrary input onto a known diff display mode.

    Args:
        mode: Candidate mode.

    Returns:
        str: A valid diff mode, falling back to side-by-side.
    """

    if isinstance(mode, str) and mode.strip().lower() in VALID_DIFF_MODES:
        return mode.strip().lower()
    return DEFAULT_DIFF_MODE


def normalize_auto_check_minutes(minutes: Any) -> int:
    """
    Clamps the automatic check interval into a sane range.

    Args:
        minutes: Candidate interval in minutes.

    Returns:
        int: 0 when disabled, otherwise 1..1440.
    """

    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        return DEFAULT_AUTO_CHECK_MINUTES
    value = int(minutes)
    if value <= 0:
        return 0
    return min(value, MAX_AUTO_CHECK_MINUTES)


def normalize_update_check_hours(hours: Any) -> int:
    """
    Clamps the update-check interval into a sane range.

    Args:
        hours: Candidate interval in hours.

    Returns:
        int: 0 when every start should check, otherwise 1..720.
    """

    if isinstance(hours, bool) or not isinstance(hours, (int, float)):
        return DEFAULT_UPDATE_CHECK_HOURS
    value = int(hours)
    if value <= 0:
        return 0
    return min(value, MAX_UPDATE_CHECK_HOURS)


def normalize_commit(value: Any) -> str:
    """
    Reduces a stored commit hash to something that can only be a commit hash.

    Args:
        value: Candidate hash, possibly hand-edited.

    Returns:
        str: Lowercase hex of at most 40 characters, or an empty string when the
            value is not a plausible hash. Never a path, an option or a URL.
    """

    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower()
    if not candidate or len(candidate) > 40:
        return ""
    if any(character not in "0123456789abcdef" for character in candidate):
        return ""
    return candidate


def normalize_summary(value: Any) -> str:
    """
    Trims a stored commit subject to one harmless line.

    Args:
        value: Candidate subject.

    Returns:
        str: First line, at most 200 characters, empty when unusable.
    """

    if not isinstance(value, str) or not value.strip():
        return ""
    return value.strip().splitlines()[0][:200]


def normalize_timestamp(value: Any) -> float:
    """
    Reduces a stored point in time to a usable number.

    Args:
        value: Candidate epoch timestamp.

    Returns:
        float: The timestamp, or 0.0 when it is missing or nonsense. Zero reads
            as "never", which is the safe answer: it triggers a check rather than
            suppressing one forever.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if value <= 0:
        return 0.0
    return float(value)


@dataclass(slots=True)
class AppSettings:
    """
    Everything the user can configure, persisted as one JSON object.

    Attributes:
        theme: Active theme name.
        language: Active language code.
        sort_mode: Sidebar sort order.
        auto_check_minutes: Automatic check interval, 0 to disable.
        check_online_automatically: Whether the automatic check also contacts
            remotes. When False it only inspects the working trees, which needs
            no network at all.
        check_updates: Whether Branchly asks GitHub on startup whether a newer
            version of itself exists. Looking only — nothing is ever installed
            without the user saying so.
        update_check_hours: Hours between two startup update checks.
        update_checked_at: Epoch timestamp of the last update check, 0 for never.
        update_remote_commit: Commit the last check found waiting, empty when
            there is nothing pending. Remembered so that dismissing the notice
            with "not now" does not blind Branchly until the next check is due:
            the notice comes back on the next start without asking GitHub again.
        update_remote_summary: Subject line belonging to that commit.
        diff_mode: Default diff layout.
        diff_ignore_whitespace: Whether diffs ignore whitespace-only changes.
        diff_word_level: Whether changed lines get intra-line word highlighting.
        github_enabled: Whether GitHub API features are active.
        show_avatars: Whether author avatars are downloaded and shown.
        confirm_destructive: Whether destructive actions require confirmation.
            Defaults to True and is deliberately exposed, not hardcoded, but
            the confirmation text always spells out what gets lost.
        discovery_offered: Whether Branchly has already offered to search the
            disk for repositories. It offers once, on the first start with an
            empty project list, and never brings it up on its own again.
        last_repo: Path of the repository selected when the app last closed.
        window_geometry: Hex-encoded Qt geometry blob.
        window_state: Hex-encoded Qt window state blob.
    """

    theme: str = DEFAULT_THEME
    language: str = DEFAULT_LANGUAGE
    sort_mode: str = DEFAULT_SORT_MODE
    auto_check_minutes: int = DEFAULT_AUTO_CHECK_MINUTES
    check_online_automatically: bool = True
    check_updates: bool = True
    update_check_hours: int = DEFAULT_UPDATE_CHECK_HOURS
    update_checked_at: float = 0.0
    update_remote_commit: str = ""
    update_remote_summary: str = ""
    diff_mode: str = DEFAULT_DIFF_MODE
    diff_ignore_whitespace: bool = False
    diff_word_level: bool = True
    github_enabled: bool = True
    show_avatars: bool = True
    confirm_destructive: bool = True
    discovery_offered: bool = False
    last_repo: str = ""
    window_geometry: str = ""
    window_state: str = ""

    def normalized(self) -> AppSettings:
        """
        Returns a copy with every field forced into a valid value.

        Returns:
            AppSettings: Sanitized settings.
        """

        return AppSettings(
            theme=normalize_theme_name(self.theme),
            language=self.language if isinstance(self.language, str) and self.language else DEFAULT_LANGUAGE,
            sort_mode=normalize_sort_mode(self.sort_mode),
            auto_check_minutes=normalize_auto_check_minutes(self.auto_check_minutes),
            check_online_automatically=bool(self.check_online_automatically),
            check_updates=bool(self.check_updates),
            update_check_hours=normalize_update_check_hours(self.update_check_hours),
            update_checked_at=normalize_timestamp(self.update_checked_at),
            update_remote_commit=normalize_commit(self.update_remote_commit),
            update_remote_summary=normalize_summary(self.update_remote_summary),
            diff_mode=normalize_diff_mode(self.diff_mode),
            diff_ignore_whitespace=bool(self.diff_ignore_whitespace),
            diff_word_level=bool(self.diff_word_level),
            github_enabled=bool(self.github_enabled),
            show_avatars=bool(self.show_avatars),
            confirm_destructive=bool(self.confirm_destructive),
            discovery_offered=bool(self.discovery_offered),
            last_repo=self.last_repo if isinstance(self.last_repo, str) else "",
            window_geometry=self.window_geometry if isinstance(self.window_geometry, str) else "",
            window_state=self.window_state if isinstance(self.window_state, str) else "",
        )

    def to_dict(self) -> dict[str, Any]:
        """
        Serializes the settings.

        Returns:
            dict[str, Any]: JSON-compatible mapping.
        """

        return {field.name: getattr(self, field.name) for field in fields(self)}

    @classmethod
    def from_dict(cls, data: Any) -> AppSettings:
        """
        Builds settings from stored data, ignoring unknown and invalid keys.

        Args:
            data: Mapping read from ``settings.json``.

        Returns:
            AppSettings: Sanitized settings.
        """

        if not isinstance(data, dict):
            return cls()
        known = {field.name for field in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in known}
        try:
            return cls(**kwargs).normalized()
        except (TypeError, ValueError):
            return cls()


def load_settings(path: Path | None = None) -> AppSettings:
    """
    Loads settings from disk.

    Args:
        path: File to read. Defaults to the standard settings path.

    Returns:
        AppSettings: Stored settings, or defaults when unreadable.
    """

    target = path or paths.settings_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return AppSettings()
    try:
        return AppSettings.from_dict(json.loads(raw))
    except ValueError:
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> bool:
    """
    Writes settings to disk atomically.

    A crash mid-write must not leave a half-written file that loses every
    setting on next start, so the content goes to a temporary file next to the
    target and is then renamed over it.

    Args:
        settings: Settings to persist.
        path: File to write. Defaults to the standard settings path.

    Returns:
        bool: True on success.
    """

    target = path or paths.settings_path()
    if not paths.ensure_dir(target.parent):
        return False
    payload = json.dumps(settings.normalized().to_dict(), indent=2, ensure_ascii=False) + "\n"
    temp = target.with_name(target.name + ".tmp")
    try:
        temp.write_text(payload, encoding="utf-8")
        os.replace(temp, target)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
