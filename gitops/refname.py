"""
Validation of branch, tag and revision names.

Git accepts a surprising range of ref names, and a name that merely *looks*
harmless can still be an option in disguise: a branch called ``--upload-pack=x``
becomes an option the moment it lands on a command line. Everything the user
types is checked here before it is passed to git, and the rules follow
``git check-ref-format`` plus one extra: a leading dash is always refused.
"""

from __future__ import annotations

import re

# Characters git itself forbids in ref names, plus the ones a shell or an option
# parser could misread. Space is included: git forbids it outright.
_FORBIDDEN_CHARS = set(" ~^:?*[\\\x7f")

# A full 40- or 64-hex-digit object id, or an abbreviation of at least four.
_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{4,64}$")

MAX_REF_LENGTH = 255


def _has_control_char(value: str) -> bool:
    """
    Reports whether a string holds an ASCII control character.

    Args:
        value: String to inspect.

    Returns:
        bool: True when a character below 0x20 is present.
    """

    return any(ord(char) < 0x20 for char in value)


def is_safe_argument(value: str) -> bool:
    """
    Reports whether a value can be handed to git as a plain argument.

    This is the backstop that keeps user input from turning into an option. It
    deliberately says nothing about whether the value is a *valid* ref — only
    that passing it along cannot change what git was asked to do.

    Args:
        value: Candidate argument.

    Returns:
        bool: True when the value is a non-empty string that carries no NUL, no
            control characters and no leading dash.
    """

    if not isinstance(value, str) or not value:
        return False
    if "\x00" in value or _has_control_char(value):
        return False
    return not value.startswith("-")


def is_valid_branch_name(name: str) -> bool:
    """
    Reports whether a name is usable as a branch or tag name.

    Mirrors ``git check-ref-format --branch`` closely enough that a name accepted
    here is accepted by git too, so the user never gets a raw git error for
    something the dialog could have caught.

    Args:
        name: Candidate branch or tag name.

    Returns:
        bool: True when the name is valid.
    """

    if not isinstance(name, str) or not name:
        return False
    if len(name) > MAX_REF_LENGTH:
        return False
    if not is_safe_argument(name):
        return False
    if any(char in _FORBIDDEN_CHARS for char in name):
        return False
    if ".." in name or "@{" in name:
        return False
    if name == "@":
        return False
    if name.startswith("/") or name.endswith("/") or "//" in name:
        return False
    if name.endswith(".") or name.endswith(".lock"):
        return False
    for component in name.split("/"):
        if not component:
            return False
        if component.startswith(".") or component.endswith(".lock"):
            return False
    return True


def is_valid_revision(value: str) -> bool:
    """
    Reports whether a value is usable where git expects a revision.

    Accepts object ids and ref names, plus the handful of suffix forms the graph
    view needs (``HEAD``, ``name~2``, ``name^``, ``name^{}``).

    Args:
        value: Candidate revision.

    Returns:
        bool: True when the value is safe and well-formed.
    """

    if not is_safe_argument(value):
        return False
    if len(value) > MAX_REF_LENGTH:
        return False
    if _SHA_PATTERN.match(value):
        return True

    base = value
    # Strip the navigation suffixes git understands, from the right.
    match = re.match(r"^(?P<base>.+?)(?P<suffix>(?:[\^~]\d*|\^\{\})*)$", value)
    if match:
        base = match.group("base")
    if base in {"HEAD", "FETCH_HEAD", "ORIG_HEAD", "MERGE_HEAD"}:
        return True
    return is_valid_branch_name(base)


def invalid_reason_key(name: str) -> str | None:
    """
    Returns a translation key describing why a branch name was refused.

    Args:
        name: Candidate branch name.

    Returns:
        str | None: Key for the hint text, or None when the name is valid.
    """

    if is_valid_branch_name(name):
        return None
    return "branch.invalid_name_hint"


def suggest_branch_name(text: str) -> str:
    """
    Turns free text into a plausible branch name.

    Used by the "new branch" dialog so a user who types "Fix login bug" gets
    ``fix-login-bug`` offered instead of a validation error.

    Args:
        text: Free-form text.

    Returns:
        str: A valid branch name, or an empty string when nothing usable is left.
    """

    if not isinstance(text, str):
        return ""
    lowered = text.strip().lower()
    slug = re.sub(r"[^a-z0-9._/-]+", "-", lowered)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = re.sub(r"/{2,}", "/", slug)
    slug = slug.strip("-/.")
    slug = slug[:MAX_REF_LENGTH]
    while slug and not is_valid_branch_name(slug):
        slug = slug[:-1].strip("-/.")
    return slug
