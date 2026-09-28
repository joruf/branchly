"""
Connecting a project that only exists locally to a server.

Two questions have to be answered before a folder gets a server address, and
neither of them is "is the URL well formed".

**What is already there locally.** A project that already has an ``origin`` is a
project where a new address replaces an old one. That is not destructive, but it
is a decision, and the old address has to be on screen when it is made.

**What is already there on the server.** Setting an address writes one line into
``.git/config`` and nothing else, so the connecting itself can overwrite nothing.
What matters is what the *next* step would do. A server that already holds
commits, against a local project that also holds commits, means the next send is
refused, and the only way past that refusal deletes one side. Saying so before
the connection is made is the difference between an informed decision and a
surprise.

This module works the situation out. Presenting it is the dialog's job.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from gitops import remote as remote_mod
from gitops.remote_url import github_slug, is_valid, normalized
from gitops.runner import run

# What was found on the server, and therefore what the user has to be told.
STATE_EMPTY = "empty"
STATE_HAS_CONTENT = "has_content"
STATE_UNREACHABLE = "unreachable"
STATE_INVALID = "invalid"

GITHUB_TEMPLATE = "https://github.com/{owner}/{name}.git"


@dataclass(frozen=True, slots=True)
class LinkCheck:
    """
    What connecting to a particular address would mean.

    Attributes:
        state: One of the ``STATE_*`` constants.
        branches: Branch names the server has, newest knowledge first.
        local_commits: Whether the local project has a history of its own.
        replaces: The address that would be replaced, empty when there is none.
        collides: Whether both sides hold commits, so the next send is refused
            until somebody decides which history survives.
    """

    state: str
    branches: tuple[str, ...] = ()
    local_commits: bool = False
    replaces: str = ""
    collides: bool = field(default=False)


def has_commits(repo: Path | str) -> bool:
    """
    Reports whether a repository has any history yet.

    Args:
        repo: Working tree path.

    Returns:
        bool: True when at least one commit exists.
    """

    result = run(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo, read_only=True)
    return result.ok and bool(result.stdout.strip())


def suggest_url(name: str, known_urls: Iterable[str], fallback_owner: str = "") -> str:
    """
    Proposes the address a project would most likely have.

    Taken from the company the project keeps: whichever owner the other projects
    on this computer belong to is almost certainly this one's owner too. That
    beats asking the server, which needs a token and a network, and it is right
    often enough to save the typing.

    Args:
        name: The project's folder name.
        known_urls: Remote URLs of the other projects.
        fallback_owner: Account to use when no other project gives one away.

    Returns:
        str: A proposed URL, empty when no owner could be worked out.
    """

    cleaned = name.strip().strip("/")
    if not cleaned:
        return ""

    counts: dict[str, int] = {}
    for url in known_urls:
        slug = github_slug(url)
        if slug is None:
            continue
        counts[slug[0]] = counts.get(slug[0], 0) + 1

    owner = ""
    if counts:
        owner = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    owner = owner or fallback_owner.strip()
    if not owner:
        return ""
    return GITHUB_TEMPLATE.format(owner=owner, name=cleaned)


def check(
    repo: Path | str, url: str, credentials: dict[str, str] | None = None
) -> LinkCheck:
    """
    Works out what connecting this project to this address would mean.

    Args:
        repo: Working tree path.
        url: Address the user wants to use.
        credentials: Login for the server, as built by ``services.git_credentials``.

    Returns:
        LinkCheck: The situation, ready to be put in front of the user.
    """

    replaces = remote_mod.remote_fetch_url(repo)
    local = has_commits(repo)
    if not is_valid(url) or not normalized(url):
        return LinkCheck(STATE_INVALID, replaces=replaces, local_commits=local)

    heads = remote_mod.ls_remote_url(url, credentials)
    if heads is None:
        return LinkCheck(STATE_UNREACHABLE, replaces=replaces, local_commits=local)
    if not heads:
        return LinkCheck(STATE_EMPTY, replaces=replaces, local_commits=local)
    return LinkCheck(
        STATE_HAS_CONTENT,
        branches=tuple(heads),
        local_commits=local,
        replaces=replaces,
        collides=local,
    )
