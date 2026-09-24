"""
Handing git a username and a password without a terminal.

Branchly runs git with ``GIT_TERMINAL_PROMPT=0``, which is right: a GUI has no
terminal, and a git process waiting forever for input nobody can type is worse
than one that fails. The cost is that a repository whose credential helper has
nothing stored fails immediately with "could not read Username", and from the
outside that is indistinguishable from a rejected password.

``GIT_ASKPASS`` closes that gap. Git runs the named program with the prompt as
its argument and takes one line of its output as the answer. This module builds
the environment for that program; deciding whether Branchly has anything to
answer with is not a git question and lives in ``services.git_credentials``.

The secret travels in the environment, never in an argument list: process
arguments are world-readable through ``ps``, the environment of a process is
readable only by its owner.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import paths

HELPER_NAME = "branchly-askpass.py"

USERNAME_VARIABLE = "BRANCHLY_GIT_USERNAME"
PASSWORD_VARIABLE = "BRANCHLY_GIT_PASSWORD"


def _shipped_helper() -> Path:
    """
    Returns where the helper lives in the source tree.

    Returns:
        Path: Path to the shipped script, which may not exist.
    """

    return paths.project_root() / "resources" / HELPER_NAME


def _cached_helper() -> Path:
    """
    Returns where a runnable copy of the helper is kept.

    Returns:
        Path: Path inside the user's cache directory.
    """

    return paths.user_cache_dir() / HELPER_NAME


def helper_path() -> Path | None:
    """
    Returns a helper script git can execute.

    The shipped copy is used when it is executable. An installation unpacked
    from a zip, or one sitting on a read-only mount, loses the execute bit, and
    a ``GIT_ASKPASS`` that cannot run is worse than none at all: git would fall
    straight back to the failure this exists to prevent. In that case a copy is
    made in the user's cache directory, where the bit can be set.

    Returns:
        Path | None: An executable script, or None when none could be prepared.
    """

    shipped = _shipped_helper()
    if shipped.is_file():
        if os.access(shipped, os.X_OK):
            return shipped
        try:
            shipped.chmod(shipped.stat().st_mode | stat.S_IXUSR)
        except OSError:
            pass
        else:
            if os.access(shipped, os.X_OK):
                return shipped

    cached = _cached_helper()
    try:
        if not shipped.is_file():
            return cached if os.access(cached, os.X_OK) else None
        if not cached.is_file() or cached.read_bytes() != shipped.read_bytes():
            paths.ensure_dir(cached.parent)
            shutil.copyfile(shipped, cached)
        cached.chmod(0o700)
    except OSError:
        return None
    return cached if os.access(cached, os.X_OK) else None


def environment(username: str, secret: str) -> dict[str, str]:
    """
    Builds the environment that lets git ask Branchly for a login.

    Args:
        username: User name to answer with.
        secret: Password or token to answer with.

    Returns:
        dict[str, str]: Variables for ``run(env_extra=...)``, empty when no
            helper could be prepared or nothing was passed to answer with.
    """

    if not secret:
        return {}
    helper = helper_path()
    if helper is None:
        return {}
    return {
        "GIT_ASKPASS": str(helper),
        # SSH has its own prompt and its own variable. Setting it as well means
        # a passphrase prompt cannot hang the process either; the helper simply
        # answers with nothing and git gives up straight away.
        "SSH_ASKPASS": str(helper),
        "SSH_ASKPASS_REQUIRE": "never",
        USERNAME_VARIABLE: username,
        PASSWORD_VARIABLE: secret,
    }
