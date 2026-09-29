"""
Noticing that a repository was renamed or moved on GitHub.

GitHub keeps an old name working for a while: the API answers a request for
``owner/old`` with a redirect to the repository's new home, and git itself is
redirected as well. Nothing breaks at first, which is exactly the problem. The
redirect disappears as soon as somebody creates a new repository under the old
name, and from then on the project quietly talks to the wrong place.

So Branchly does what GitHub Desktop does. Whenever it reads a project's
repository from the API anyway, it compares the name GitHub reports with the one
in the remote URL. When they differ, the remote is moved to the new name, the
same way it was reached before (HTTPS stays HTTPS, SSH stays SSH), and the user
is told once.

Only the remote changes. The folder on disk keeps its name, since other tools,
terminals and editors may point at it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitops.remote import remote_fetch_url, set_remote_url
from gitops.remote_url import github_slug, is_valid, with_github_slug
from gitops.runner import run


@dataclass(frozen=True, slots=True)
class Rename:
    """
    A difference between the remote URL and where GitHub says the repository is.

    Attributes:
        old_slug: ``owner/name`` from the remote URL.
        new_slug: ``owner/name`` GitHub reported.
        old_url: The remote URL as it is.
        new_url: The same URL pointing at the new name.
    """

    old_slug: str
    new_slug: str
    old_url: str
    new_url: str

    @property
    def old_name(self) -> str:
        """
        Returns the repository name before the rename.

        Returns:
            str: The part after the slash.
        """

        return self.old_slug.split("/", 1)[-1]

    @property
    def new_name(self) -> str:
        """
        Returns the repository name after the rename.

        Returns:
            str: The part after the slash.
        """

        return self.new_slug.split("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class RenameOutcome:
    """
    What moving the remote did.

    Attributes:
        ok: Whether the fetch URL now points at the new name.
        push_moved: Whether a separate push URL was moved as well.
        detail: Git's message when it failed.
    """

    ok: bool
    push_moved: bool = False
    detail: str = ""


def detect(remote_url: str, reported_full_name: str) -> Rename | None:
    """
    Compares a remote URL with the name GitHub reported for the repository.

    Args:
        remote_url: The project's fetch URL.
        reported_full_name: ``full_name`` from the API answer.

    Returns:
        Rename | None: The difference, or None when the names agree or either
            side cannot be read.
    """

    slug = github_slug(remote_url)
    owner, _, name = (reported_full_name or "").partition("/")
    if slug is None or not owner or not name or "/" in name:
        return None
    old_slug = f"{slug[0]}/{slug[1]}"
    new_slug = f"{owner}/{name}"
    # GitHub names are not case sensitive, but a changed case is still a
    # rename somebody made on purpose, so only an exact match counts as equal.
    if old_slug == new_slug:
        return None
    new_url = with_github_slug(remote_url, owner, name)
    if not new_url:
        return None
    return Rename(old_slug=old_slug, new_slug=new_slug, old_url=remote_url, new_url=new_url)


def _push_url(repo: Path | str, remote: str) -> str:
    """
    Reads a push URL configured apart from the fetch URL.

    Args:
        repo: Working tree path.
        remote: Remote name.

    Returns:
        str: The push URL, empty when pushing uses the fetch URL.
    """

    result = run(["config", "--get", f"remote.{remote}.pushurl"], cwd=repo, read_only=True)
    return result.stdout.strip() if not result.failed else ""


def apply(repo: Path | str, rename: Rename, remote: str = "origin") -> RenameOutcome:
    """
    Moves a remote to the repository's new name.

    Refuses when the remote no longer holds the URL the rename was worked out
    from: somebody changed it in the meantime, and their choice wins.

    Args:
        repo: Working tree path.
        rename: The detected rename.
        remote: Remote name.

    Returns:
        RenameOutcome: Whether it worked.
    """

    if remote_fetch_url(repo, remote) != rename.old_url:
        return RenameOutcome(ok=False, detail="remote changed meanwhile")
    result = set_remote_url(repo, rename.new_url, remote)
    if result.failed:
        return RenameOutcome(ok=False, detail=result.message)

    # A separate push URL pointing at the old name would keep pushes going
    # there, so it moves too. One pointing somewhere else was set on purpose.
    push = _push_url(repo, remote)
    slug = github_slug(push) if push else None
    if slug is None or f"{slug[0]}/{slug[1]}" != rename.old_slug:
        return RenameOutcome(ok=True)
    owner, name = rename.new_slug.split("/", 1)
    new_push = with_github_slug(push, owner, name)
    if not new_push or not is_valid(new_push):
        return RenameOutcome(ok=True)
    moved = run(["remote", "set-url", "--push", remote, new_push], cwd=repo)
    return RenameOutcome(ok=True, push_moved=not moved.failed)
