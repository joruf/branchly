"""
Letting git use the GitHub token Branchly already has.

Branchly keeps a personal access token in the system keychain for the GitHub
panel. Git knows nothing about it: it has its own credential helpers, and on a
machine where none of them has an entry for github.com, a push over HTTPS fails
with "could not read Username". Branchly then reported a rejected login, which
was true in the sense that no login was offered at all, and unhelpful in every
other sense.

So when the remote is a GitHub HTTPS URL and a token is stored, that token is
offered for the operation. Deliberately narrow:

* **Only HTTPS.** An ``ssh://`` or ``git@`` remote authenticates with a key, and
  a token would be meaningless there.
* **Only github.com.** The token belongs to GitHub. Sending it to any other host
  would be handing a credential to a stranger.
* **Only when git has nothing of its own.** A working credential helper is the
  user's own arrangement, and quietly overriding it would mean pushing as
  somebody they did not choose.

Nothing is written to disk and nothing is configured in the repository. The
token exists for the lifetime of one git process, in its environment.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from github_api import token as token_store
from gitops import askpass
from gitops import remote as remote_mod

# The user name sent alongside a personal access token. GitHub ignores it as
# long as the token is right, and this is the name its own documentation uses.
TOKEN_USERNAME = "x-access-token"

GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


def is_github_https(url: str) -> bool:
    """
    Reports whether a remote URL is a GitHub repository reached over HTTPS.

    Args:
        url: Remote URL as git has it.

    Returns:
        bool: True when a GitHub token would be the right credential.
    """

    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https":
        return False
    host = parsed.hostname or ""
    return host.lower() in GITHUB_HOSTS


def for_url(url: str) -> dict[str, str]:
    """
    Builds the environment that lets git log in for one remote.

    Args:
        url: Remote URL as git has it.

    Returns:
        dict[str, str]: Variables for ``run(env_extra=...)``, empty when the
            token does not apply or none is stored.
    """

    if not is_github_https(url):
        return {}
    stored = token_store.load()
    if not stored:
        return {}
    return askpass.environment(TOKEN_USERNAME, stored)


def for_repository(repo: Path | str, remote: str = "origin") -> dict[str, str]:
    """
    Builds the environment that lets git log in for one repository.

    Args:
        repo: Working tree path.
        remote: Remote name.

    Returns:
        dict[str, str]: Variables for ``run(env_extra=...)``, empty when nothing
            applies.
    """

    return for_url(remote_mod.remote_fetch_url(repo, remote))


def can_help(repo: Path | str, remote: str = "origin") -> bool:
    """
    Reports whether a stored token would have been offered for a repository.

    Used to word a failed login: "check your saved credentials" is the wrong
    advice for someone who has no token stored and a GitHub remote, and the
    right advice for everyone else.

    Args:
        repo: Working tree path.
        remote: Remote name.

    Returns:
        bool: True when the remote is GitHub over HTTPS and a token is stored.
    """

    return bool(for_repository(repo, remote))


def wants_token(repo: Path | str, remote: str = "origin") -> bool:
    """
    Reports whether a token would help this repository but none is stored.

    Args:
        repo: Working tree path.
        remote: Remote name.

    Returns:
        bool: True for a GitHub HTTPS remote with no token in the keychain.
    """

    if not is_github_https(remote_mod.remote_fetch_url(repo, remote)):
        return False
    return not token_store.load()
