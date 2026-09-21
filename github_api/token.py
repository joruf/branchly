"""
Storing the GitHub access token.

The token goes into the operating system's keychain — libsecret on Linux, the
Credential Manager on Windows — and nowhere else. In particular it never goes
into ``settings.json``: a config file gets copied into backups, synced between
machines and pasted into bug reports, and a token in one is a token leaked.

If no keychain is available, Branchly says so and leaves the GitHub features off
rather than quietly falling back to a plain file.

Git's own credentials are a separate matter entirely and are not touched here —
git's credential helper handles those.
"""

from __future__ import annotations

from dataclasses import dataclass

from constants import APP_SLUG

SERVICE_NAME = APP_SLUG
ACCOUNT_NAME = "github.com"

# Prefixes GitHub uses for its token formats. Used only to catch an obvious
# paste mistake early; the real check is asking the API who we are.
_KNOWN_PREFIXES = ("github_pat_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_")


@dataclass(frozen=True, slots=True)
class TokenState:
    """
    What Branchly knows about the stored token.

    Attributes:
        available: Whether a keychain could be reached at all.
        stored: Whether a token is currently saved.
        error: Short technical reason the keychain failed, for the log.
    """

    available: bool
    stored: bool = False
    error: str = ""


def _backend():  # noqa: ANN202 - keyring's backend type is not worth importing for a hint
    """
    Returns the keyring module, or None when it is unusable.

    Returns:
        module | None: The ``keyring`` module when a real backend is present.
    """

    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 - any backend problem means "no keychain"
        return None
    if isinstance(backend, FailKeyring):
        return None
    return keyring


def is_available() -> bool:
    """
    Reports whether a system keychain can be used.

    Returns:
        bool: True when a token could be stored.
    """

    return _backend() is not None


def state() -> TokenState:
    """
    Describes the current token situation.

    Returns:
        TokenState: Whether a keychain exists and whether a token is in it.
    """

    keyring = _backend()
    if keyring is None:
        return TokenState(available=False, error="no keyring backend")
    try:
        stored = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception as error:  # noqa: BLE001
        return TokenState(available=True, stored=False, error=str(error))
    return TokenState(available=True, stored=bool(stored))


def load() -> str:
    """
    Reads the stored token.

    Returns:
        str: The token, empty when none is stored or the keychain failed.
    """

    keyring = _backend()
    if keyring is None:
        return ""
    try:
        return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) or ""
    except Exception:  # noqa: BLE001
        return ""


def save(token: str) -> bool:
    """
    Stores a token in the keychain.

    Args:
        token: Token to store. Whitespace is trimmed, because a token pasted from
            a browser usually brings some along.

    Returns:
        bool: True on success.
    """

    keyring = _backend()
    if keyring is None:
        return False
    cleaned = token.strip()
    if not cleaned:
        return False
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, cleaned)
    except Exception:  # noqa: BLE001
        return False
    return True


def delete() -> bool:
    """
    Removes the stored token.

    Returns:
        bool: True when nothing is stored afterwards.
    """

    keyring = _backend()
    if keyring is None:
        return True
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:  # noqa: BLE001 - "was not there" is also success
        return not state().stored
    return True


def looks_like_a_token(candidate: str) -> bool:
    """
    Reports whether a string is plausibly a GitHub token.

    Only a sanity check for the settings dialog, so an accidentally pasted URL or
    password is caught before a pointless API round trip.

    Args:
        candidate: String the user pasted.

    Returns:
        bool: True when it has a known prefix and a plausible length.
    """

    cleaned = candidate.strip()
    if len(cleaned) < 20 or " " in cleaned:
        return False
    return cleaned.startswith(_KNOWN_PREFIXES)
