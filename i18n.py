"""
Translation layer.

Language files are plain JSON in ``locales/``. Adding a language means dropping
another file in there — no code change. English is the fallback for any missing
key, so a partial translation is still usable, and an unknown key renders as the
key itself rather than as an empty label.

The active language is persisted in ``settings.json`` (see
``config.app_settings``); this module only holds the catalog.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

DEFAULT_LANGUAGE = "en"
LOCALES_DIR = Path(__file__).resolve().parent / "locales"

# Shown in the settings dropdown when a file has no "_label" key of its own.
_LANGUAGE_FALLBACK_LABELS = {"en": "English", "de": "Deutsch"}


class Translator:
    """
    Holds the active catalog and resolves translation keys.

    Access is guarded by a lock because the scanner threads may format status
    strings while the settings dialog switches language on the UI thread.
    """

    def __init__(self) -> None:
        """
        Loads the fallback catalog.
        """

        self._lock = threading.RLock()
        self._language = DEFAULT_LANGUAGE
        self._fallback: dict[str, str] = self._read(DEFAULT_LANGUAGE)
        self._catalog: dict[str, str] = dict(self._fallback)

    @staticmethod
    def _read(code: str) -> dict[str, str]:
        """
        Reads one language file.

        Args:
            code: Language code, matching the file stem in ``locales/``.

        Returns:
            dict[str, str]: String entries, empty when the file is unusable.
        """

        path = LOCALES_DIR / f"{code}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items() if isinstance(value, str)}

    def available(self) -> list[tuple[str, str]]:
        """
        Lists every language that parses.

        Returns:
            list[tuple[str, str]]: ``(code, display label)`` pairs.
        """

        found: list[tuple[str, str]] = []
        for path in sorted(LOCALES_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            label = data.get("_label")
            if not isinstance(label, str) or not label:
                label = _LANGUAGE_FALLBACK_LABELS.get(path.stem, path.stem.upper())
            found.append((path.stem, label))
        return found or [(DEFAULT_LANGUAGE, "English")]

    @property
    def language(self) -> str:
        """
        Returns the active language code.

        Returns:
            str: Language code.
        """

        with self._lock:
            return self._language

    def set_language(self, code: str | None) -> str:
        """
        Switches the active language.

        A code without a usable file is ignored rather than blanking the UI.

        Args:
            code: Language code to activate.

        Returns:
            str: The language code that is now active.
        """

        if not isinstance(code, str) or not code:
            return self.language
        catalog = self._read(code)
        with self._lock:
            if not catalog and code != DEFAULT_LANGUAGE:
                return self._language
            self._language = code
            self._catalog = catalog or dict(self._fallback)
            return self._language

    def get(self, key: str, **params: object) -> str:
        """
        Resolves a key, formatting any placeholders.

        Args:
            key: Translation key.
            **params: Values for ``{name}`` placeholders in the template.

        Returns:
            str: Translated text, the English text, or the key itself.
        """

        with self._lock:
            template = self._catalog.get(key) or self._fallback.get(key)
        if template is None:
            return key
        if not params:
            return template
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            # A malformed placeholder must not take the label down.
            return template


TRANSLATOR = Translator()


def t(key: str, **params: object) -> str:
    """
    Resolves a translation key against the active catalog.

    Args:
        key: Translation key.
        **params: Values for placeholders in the template.

    Returns:
        str: Translated text.
    """

    return TRANSLATOR.get(key, **params)


def set_language(code: str | None) -> str:
    """
    Switches the active language.

    Args:
        code: Language code to activate.

    Returns:
        str: The language code that is now active.
    """

    return TRANSLATOR.set_language(code)


def current_language() -> str:
    """
    Returns the active language code.

    Returns:
        str: Language code.
    """

    return TRANSLATOR.language


def available_languages() -> list[tuple[str, str]]:
    """
    Lists selectable languages.

    Returns:
        list[tuple[str, str]]: ``(code, display label)`` pairs.
    """

    return TRANSLATOR.available()


def plural(count: int, singular_key: str, plural_key: str, **params: object) -> str:
    """
    Picks a singular or plural template and injects the count.

    German and English agree on "one vs. many", which is all Branchly needs;
    a language with more plural forms would get its own rule here.

    Args:
        count: Number deciding the form.
        singular_key: Key used when ``count`` is exactly 1.
        plural_key: Key used otherwise.
        **params: Extra placeholder values.

    Returns:
        str: Translated text with ``{count}`` filled in.
    """

    key = singular_key if count == 1 else plural_key
    return TRANSLATOR.get(key, count=count, **params)
