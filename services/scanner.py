"""
Working out what is new in a repository.

Two levels, because they cost very different amounts. The local scan only reads
files and is fast enough to run for every project at once. The online check talks
to a server, so it is slower, can fail, and is skipped entirely when the user
turned it off.

Neither level ever writes to a repository. The online check uses ``ls-remote``,
which asks the server a question and changes nothing — so an automatic check
running in the background cannot surprise the user by moving their refs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from gitops import remote as remote_mod
from gitops.remote_url import github_slug
from gitops.status import last_commit_timestamp, read_state
from models.repository import RepoEntry, RepoStatus
from services import git_credentials


@dataclass(frozen=True, slots=True)
class ScanRequest:
    """
    One repository to scan.

    Attributes:
        path: Working tree path.
        key: Registry key, used to match the result back to its entry.
        check_online: Whether the server should be asked as well.
    """

    path: Path
    key: str
    check_online: bool = True


@dataclass(frozen=True, slots=True)
class ScanResult:
    """
    The outcome of scanning one repository.

    Attributes:
        key: Registry key of the entry this belongs to.
        status: Fresh status.
        remote_url: Fetch URL of ``origin``, empty when there is none.
        github: ``(owner, repo)`` when the remote is on GitHub, otherwise None.
        missing: Whether the folder is gone or no longer a repository.
    """

    key: str
    status: RepoStatus
    remote_url: str = ""
    github: tuple[str, str] | None = None
    missing: bool = False


def scan(request: ScanRequest) -> ScanResult:
    """
    Reads the state of one repository.

    Args:
        request: What to scan.

    Returns:
        ScanResult: Fresh status. A repository whose folder disappeared comes back
            with ``missing`` set rather than as an error, because the sidebar shows
            that as its own kind of entry.
    """

    path = Path(request.path)
    if not (path / ".git").exists():
        return ScanResult(
            key=request.key,
            status=RepoStatus(error_key="repo.missing", checked_at=time.time()),
            missing=True,
        )

    state = read_state(path)
    if not state.ok:
        return ScanResult(
            key=request.key,
            status=RepoStatus(error_key=state.error_key, checked_at=time.time()),
        )

    status = RepoStatus(
        branch=state.display_branch,
        detached=state.detached,
        changed_files=len(state.unstaged_files),
        staged_files=len(state.staged_files),
        conflicted_files=len(state.conflicted_files),
        ahead=state.ahead,
        behind=state.behind,
        last_commit_at=last_commit_timestamp(path),
        checked_at=time.time(),
    )

    remote_url_value = remote_mod.remote_fetch_url(path)
    slug = github_slug(remote_url_value) if remote_url_value else None

    if request.check_online and remote_url_value:
        online = remote_mod.check_remote_state(
            path, state.branch, credentials=git_credentials.for_url(remote_url_value)
        )
        if online.reachable:
            status.incoming = max(status.behind, online.incoming)
            status.online_checked_at = time.time()
        elif online.error_key:
            # A server that cannot be reached must not wipe out the local numbers
            # that were just read successfully.
            status.online_checked_at = None

    return ScanResult(
        key=request.key,
        status=status,
        remote_url=remote_url_value,
        github=slug,
    )


def build_requests(entries: list[RepoEntry], check_online: bool) -> list[ScanRequest]:
    """
    Turns registry entries into scan jobs.

    Args:
        entries: Entries to scan.
        check_online: Whether servers should be asked.

    Returns:
        list[ScanRequest]: One job per entry.
    """

    return [
        ScanRequest(path=entry.path, key=entry.key, check_online=check_online)
        for entry in entries
    ]


def apply_result(entry: RepoEntry, result: ScanResult) -> None:
    """
    Copies a scan result onto its registry entry.

    Args:
        entry: Entry to update.
        result: Fresh scan result.

    Returns:
        None
    """

    # The number of open pull requests comes from the GitHub layer, not from git,
    # so carry the previous value over instead of resetting it to zero.
    result.status.open_pull_requests = entry.status.open_pull_requests
    if result.status.check_state is None:
        result.status.check_state = entry.status.check_state
    entry.status = result.status
    if result.remote_url:
        entry.remote_url = result.remote_url


def describe(status: RepoStatus) -> list[tuple[str, str, int]]:
    """
    Builds the badge list for one repository.

    Args:
        status: Status to describe.

    Returns:
        list[tuple[str, str, int]]: ``(kind, theme token, count)`` per badge, in
            the order they should be shown. ``kind`` names what the badge means so
            the view can pick an icon and a tooltip for it.
    """

    badges: list[tuple[str, str, int]] = []
    if status.conflicted_files:
        badges.append(("conflict", "status_conflict", status.conflicted_files))
    changed = status.changed_files + status.staged_files
    if changed:
        badges.append(("changed", "status_modified", changed))
    if status.incoming or status.behind:
        badges.append(("incoming", "info", max(status.incoming, status.behind)))
    if status.ahead:
        badges.append(("ahead", "warning", status.ahead))
    return badges
