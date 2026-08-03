"""
Validation of remote URLs.

This module exists because of a real class of attack, not for tidiness. Two
examples of what it refuses:

* A carriage return smuggled into a URL. Git's credential protocol is
  line-based, so a ``\\r`` can end a line earlier than the reading program
  thinks and make credentials for one host be handed to another. That is exactly
  CVE-2025-23040 in GitHub Desktop.
* ``ext::sh -c ...`` URLs. Git's ``ext`` transport runs a command, so a URL is
  enough to execute code.

Anything that is not plainly an https, ssh or local-path address is refused.
"""

from __future__ import annotations

import re
from pathlib import Path

# Schemes Branchly is willing to hand to git. ``http`` is included because
# self-hosted servers on internal networks commonly still use it; git itself
# decides whether to send credentials over it.
ALLOWED_SCHEMES = ("https://", "http://", "ssh://", "git://", "file://")

# Transport helpers that execute commands or override the program git talks to.
# None of these can ever come from a URL Branchly typed in.
_FORBIDDEN_SUBSTRINGS = (
    "ext::",
    "--upload-pack",
    "--receive-pack",
    "--exec",
)

# scp-style address: user@host:path — the form GitHub and GitLab hand out.
_SCP_PATTERN = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9._-]+):(?P<path>.+)$")

# The first character must be alphanumeric: a host like ``-evil`` would be read
# as an option by ssh, not as a host name.
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?::\d{1,5})?$")

MAX_URL_LENGTH = 2048

REASON_OK = "ok"
REASON_EMPTY = "empty"
REASON_TOO_LONG = "too_long"
REASON_CONTROL_CHARS = "control_chars"
REASON_FORBIDDEN = "forbidden"
REASON_LEADING_DASH = "leading_dash"
REASON_UNKNOWN_FORM = "unknown_form"
REASON_BAD_HOST = "bad_host"

# Reasons that mean "this looks like an attack", as opposed to "this is a typo".
# The dialog shows a sharper warning for these.
SUSPICIOUS_REASONS = frozenset({REASON_CONTROL_CHARS, REASON_FORBIDDEN, REASON_LEADING_DASH})


def _has_control_char(value: str) -> bool:
    """
    Reports whether a string holds an ASCII control character.

    Newline, carriage return and tab count as control characters here — there is
    no legitimate remote URL containing any of them.

    Args:
        value: String to inspect.

    Returns:
        bool: True when a character below 0x20 or DEL is present.
    """

    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def classify(url: str) -> str:
    """
    Classifies a remote URL.

    Args:
        url: Candidate URL as typed by the user.

    Returns:
        str: ``REASON_OK`` when usable, otherwise the reason it was refused.
    """

    if not isinstance(url, str):
        return REASON_EMPTY
    raw = url.strip()
    if not raw:
        return REASON_EMPTY
    if len(raw) > MAX_URL_LENGTH:
        return REASON_TOO_LONG
    if "\x00" in raw or _has_control_char(raw):
        return REASON_CONTROL_CHARS
    lowered = raw.lower()
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle in lowered:
            return REASON_FORBIDDEN
    if raw.startswith("-"):
        return REASON_LEADING_DASH

    # Any "word::" prefix is a transport helper, and the only one we would ever
    # want is already covered above. Refuse the whole family.
    if re.match(r"^[A-Za-z0-9][A-Za-z0-9+.-]*::", raw):
        return REASON_FORBIDDEN

    for scheme in ALLOWED_SCHEMES:
        if lowered.startswith(scheme):
            remainder = raw[len(scheme):]
            if scheme == "file://":
                return REASON_OK if remainder else REASON_UNKNOWN_FORM
            if not remainder:
                return REASON_UNKNOWN_FORM
            authority = remainder.split("/", 1)[0]
            host = authority.rsplit("@", 1)[-1]
            if not host or not _HOST_PATTERN.match(host):
                return REASON_BAD_HOST
            return REASON_OK

    scp = _SCP_PATTERN.match(raw)
    if scp:
        if not _HOST_PATTERN.match(scp.group("host")):
            return REASON_BAD_HOST
        return REASON_OK if scp.group("path") else REASON_UNKNOWN_FORM

    # A plain local path: absolute, or an existing relative directory.
    if raw.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", raw):
        return REASON_OK
    if raw.startswith("~") or raw.startswith("./") or raw.startswith("../"):
        return REASON_OK
    try:
        if Path(raw).is_dir():
            return REASON_OK
    except OSError:
        return REASON_UNKNOWN_FORM

    return REASON_UNKNOWN_FORM


def is_valid(url: str) -> bool:
    """
    Reports whether a remote URL may be handed to git.

    Args:
        url: Candidate URL.

    Returns:
        bool: True when usable.
    """

    return classify(url) == REASON_OK


def is_suspicious(url: str) -> bool:
    """
    Reports whether a URL was refused for a reason that smells like an attack.

    Args:
        url: Candidate URL.

    Returns:
        bool: True when the URL contains control characters, a leading dash or a
            transport helper.
    """

    return classify(url) in SUSPICIOUS_REASONS


def normalized(url: str) -> str:
    """
    Returns the URL as it should be handed to git.

    Args:
        url: Candidate URL.

    Returns:
        str: Trimmed URL, or an empty string when it is not usable.
    """

    if not is_valid(url):
        return ""
    return url.strip()


def suggested_directory_name(url: str) -> str:
    """
    Derives the folder name git would use for a clone.

    Args:
        url: Remote URL.

    Returns:
        str: Directory name, or an empty string when none can be derived.
    """

    raw = normalized(url)
    if not raw:
        return ""
    tail = raw.rstrip("/")
    scp = _SCP_PATTERN.match(tail)
    if scp:
        tail = scp.group("path")
    else:
        for scheme in ALLOWED_SCHEMES:
            if tail.lower().startswith(scheme):
                tail = tail[len(scheme):]
                break
    tail = tail.rstrip("/").split("/")[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    # A name that would not be a sane directory is dropped rather than guessed at.
    if not tail or tail in {".", ".."} or "\\" in tail:
        return ""
    return tail


def github_slug(url: str) -> tuple[str, str] | None:
    """
    Extracts ``owner`` and ``repo`` from a GitHub remote URL.

    Args:
        url: Remote URL.

    Returns:
        tuple[str, str] | None: ``(owner, repo)`` for github.com remotes,
            otherwise None. Non-GitHub remotes are a normal case, not an error:
            the API panels simply stay hidden.
    """

    raw = normalized(url)
    if not raw:
        return None

    host = ""
    path = ""
    scp = _SCP_PATTERN.match(raw)
    if scp:
        host = scp.group("host")
        path = scp.group("path")
    else:
        for scheme in ALLOWED_SCHEMES:
            if raw.lower().startswith(scheme):
                remainder = raw[len(scheme):]
                authority, _, rest = remainder.partition("/")
                host = authority.rsplit("@", 1)[-1].split(":")[0]
                path = rest
                break
    if host.lower() not in {"github.com", "www.github.com"}:
        return None

    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo:
        return None
    return owner, repo
