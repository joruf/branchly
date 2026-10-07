"""
Finding the repositories on GitHub that Branchly does not have yet.

The counterpart of ``services.discovery``, which looks on the disk. This one asks
GitHub for the account's own repositories and sorts each into one of four:

* **New**: nowhere to be seen locally. Ticked, it is cloned into the chosen
  folder and added.
* **On disk**: the folder it would be cloned into already holds a clone of it,
  Branchly just never knew. Ticked, it is added as it is, without a second clone.
* **Known**: Branchly already lists a project with this repository as its server.
  Shown so the list does not look incomplete, never offered.
* **Blocked**: the folder it would be cloned into exists and is something else.
  Shown with the reason, never offered: a clone there would mix two projects.

Archived repositories and forks are left out. An archived one takes no new work,
and a fork is somebody else's project first.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from gitops.remote_url import github_slug
from services.discovery import read_origin_url, resolve_git_dir

STATE_NEW = "new"
STATE_ON_DISK = "on_disk"
STATE_KNOWN = "known"
STATE_BLOCKED = "blocked"

#: States whose rows can be ticked.
OFFERED_STATES = frozenset({STATE_NEW, STATE_ON_DISK})


@dataclass(frozen=True, slots=True)
class RemoteRepository:
    """
    The parts of a GitHub repository the search needs.

    Attributes:
        name: Repository name, also the folder it is cloned into.
        full_name: ``owner/name``.
        clone_url: HTTPS clone address.
        description: Short description.
        private: Whether it is private.
        fork: Whether it is a fork.
        archived: Whether it is archived.
        pushed_at: ISO timestamp of the last push.
    """

    name: str
    full_name: str
    clone_url: str
    description: str = ""
    private: bool = False
    fork: bool = False
    archived: bool = False
    pushed_at: str = ""

    @classmethod
    def from_model(cls, repository: object) -> RemoteRepository:
        """
        Takes the fields from a ``github_api.models.Repository``.

        Args:
            repository: The API model.

        Returns:
            RemoteRepository: The search's own view of it.
        """

        return cls(
            name=str(getattr(repository, "name", "")),
            full_name=str(getattr(repository, "full_name", "")),
            clone_url=str(getattr(repository, "clone_url", "")),
            description=str(getattr(repository, "description", "") or ""),
            private=bool(getattr(repository, "private", False)),
            fork=bool(getattr(repository, "fork", False)),
            archived=bool(getattr(repository, "archived", False)),
            pushed_at=str(getattr(repository, "pushed_at", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class Offer:
    """
    One repository and what ticking it would do.

    Attributes:
        repository: The repository on GitHub.
        state: One of the ``STATE_*`` constants.
        target: Folder it would be cloned into, or where it already lies.
        known_name: Display name of the project that already has it, for
            ``STATE_KNOWN``.
    """

    repository: RemoteRepository
    state: str
    target: Path
    known_name: str = ""

    @property
    def offered(self) -> bool:
        """
        Reports whether the row can be ticked.

        Returns:
            bool: True for a new repository and for one already on disk.
        """

        return self.state in OFFERED_STATES


def slug_key(url: str) -> str:
    """
    Reduces a remote URL to the repository it names, for comparing.

    Args:
        url: Remote URL in any form git accepts.

    Returns:
        str: ``owner/name`` in lower case, empty for anything not on GitHub.
    """

    slug = github_slug(url)
    return f"{slug[0]}/{slug[1]}".lower() if slug else ""


def is_wanted(repository: RemoteRepository) -> bool:
    """
    Reports whether a repository belongs in the list at all.

    Args:
        repository: The repository.

    Returns:
        bool: False for forks, archived repositories and unusable entries.
    """

    return bool(repository.name and repository.clone_url) and not (repository.fork or repository.archived)


def folder_state(target: Path, key: str) -> str:
    """
    Says what the folder a repository would be cloned into holds.

    Args:
        target: The folder.
        key: ``slug_key`` of the repository.

    Returns:
        str: ``STATE_NEW`` when there is nothing, ``STATE_ON_DISK`` when it is a
            clone of this repository, ``STATE_BLOCKED`` otherwise.
    """

    if not target.exists():
        return STATE_NEW
    git_dir = resolve_git_dir(target) if target.is_dir() else None
    if git_dir is not None and slug_key(read_origin_url(git_dir)) == key:
        return STATE_ON_DISK
    return STATE_BLOCKED


def plan(
    repositories: Iterable[RemoteRepository],
    known: dict[str, str],
    folder: Path,
) -> list[Offer]:
    """
    Sorts the account's repositories into what can be done with each.

    Args:
        repositories: The repositories GitHub listed.
        known: ``slug_key`` of every project Branchly has, mapped to its display
            name.
        folder: Where new clones go.

    Returns:
        list[Offer]: New ones first, then those on disk, then the rest, each
            group by name.
    """

    order = {STATE_NEW: 0, STATE_ON_DISK: 1, STATE_KNOWN: 2, STATE_BLOCKED: 3}
    offers: list[Offer] = []
    for repository in repositories:
        if not is_wanted(repository):
            continue
        key = slug_key(repository.clone_url)
        target = folder / repository.name
        if key and key in known:
            offers.append(Offer(repository, STATE_KNOWN, target, known[key]))
            continue
        offers.append(Offer(repository, folder_state(target, key), target))
    offers.sort(key=lambda offer: (order[offer.state], offer.repository.name.lower()))
    return offers


def follow_moves(
    known: dict[str, str],
    listed: Iterable[RemoteRepository],
    lookup: Callable[[str, str], str],
) -> dict[str, str]:
    """
    Adds the current name of every known repository GitHub lists under another.

    A project whose repository was renamed or moved still carries the old address
    until it is next opened. Without this, the repository would show up as new
    under its new name and be cloned a second time.

    Args:
        known: ``slug_key`` of every project Branchly has, mapped to its name.
        listed: The repositories GitHub listed.
        lookup: Asks GitHub for ``(owner, name)`` and returns the ``full_name`` it
            answers with today, empty when it has none.

    Returns:
        dict[str, str]: ``known`` with the current names added.
    """

    listed_keys = {slug_key(repository.clone_url) for repository in listed}
    result = dict(known)
    for key, name in known.items():
        if key in listed_keys or "/" not in key:
            continue
        owner, repo = key.split("/", 1)
        current = (lookup(owner, repo) or "").lower()
        if current and current != key:
            result.setdefault(current, name)
    return result


def default_folder(project_paths: Iterable[Path]) -> Path:
    """
    Suggests where new clones go: the folder most projects already live in.

    Args:
        project_paths: Working tree paths of the projects Branchly has.

    Returns:
        Path: The most common parent folder, or the home folder without any.
    """

    counts: dict[Path, int] = {}
    for path in project_paths:
        parent = Path(path).parent
        counts[parent] = counts.get(parent, 0) + 1
    if not counts:
        return Path.home()
    return max(counts.items(), key=lambda item: (item[1], str(item[0])))[0]
