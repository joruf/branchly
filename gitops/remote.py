"""
Talking to remotes.

The online check uses ``git ls-remote``, not ``git fetch``: it asks the server
which commits exist and changes nothing locally. A background check that quietly
wrote to the repository every few minutes would be the wrong kind of helpful.
``fetch`` and ``pull`` only ever run because the user asked.

Authentication is not handled here. ``GIT_TERMINAL_PROMPT=0`` from the runner
means a missing credential fails immediately instead of hanging, and git's own
credential helper supplies whatever it has.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from constants import GIT_TIMEOUT_NETWORK
from gitops.refname import is_valid_branch_name, is_valid_revision
from gitops.remote_url import is_valid as is_valid_remote_url
from gitops.runner import GitResult, run, run_lines


def _refused(command: str, reason: str = "unsafe argument") -> GitResult:
    """
    Builds a failed result for input that never reached git.

    Args:
        command: Command that was refused.
        reason: Text placed in stderr.

    Returns:
        GitResult: Failed result.
    """

    return GitResult(returncode=-1, stdout="", stderr=reason, args=(command,))


@dataclass(frozen=True, slots=True)
class RemoteState:
    """
    What the server says compared to what is here.

    Attributes:
        reachable: Whether the server answered at all.
        incoming: Commits on the tracked remote branch that are not here yet.
        remote_tip: Commit id the remote branch points at.
        local_tip: Commit id the local tracking ref points at.
        error_key: Translation key when the server could not be reached.
    """

    reachable: bool = False
    incoming: int = 0
    remote_tip: str = ""
    local_tip: str = ""
    error_key: str = ""

    @property
    def has_news(self) -> bool:
        """
        Reports whether the server holds something new.

        Returns:
            bool: True when the remote tip differs from the local tracking ref.
        """

        return bool(self.remote_tip and self.remote_tip != self.local_tip)


def remotes(repo: Path | str) -> list[str]:
    """
    Lists configured remote names.

    Args:
        repo: Working tree path.

    Returns:
        list[str]: Remote names, e.g. ``["origin"]``.
    """

    return run_lines(["remote"], cwd=repo)


def remote_fetch_url(repo: Path | str, remote: str = "origin") -> str:
    """
    Reads the fetch URL of a remote.

    Args:
        repo: Working tree path.
        remote: Remote name.

    Returns:
        str: URL, empty when the remote does not exist.
    """

    if not is_valid_branch_name(remote):
        return ""
    result = run(["remote", "get-url", remote], cwd=repo, read_only=True)
    if result.failed:
        return ""
    return result.stdout.strip()


def has_remote(repo: Path | str, remote: str = "origin") -> bool:
    """
    Reports whether a remote is configured.

    Args:
        repo: Working tree path.
        remote: Remote name.

    Returns:
        bool: True when the remote exists.
    """

    return remote in remotes(repo)


def upstream_of(repo: Path | str, branch: str = "") -> str:
    """
    Reads the tracking branch of a local branch.

    Args:
        repo: Working tree path.
        branch: Local branch name. Defaults to the current branch.

    Returns:
        str: Upstream name like ``origin/main``, empty when there is none.
    """

    target = f"{branch}@{{upstream}}" if branch else "@{upstream}"
    if branch and not is_valid_branch_name(branch):
        return ""
    result = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", target], cwd=repo, read_only=True)
    if result.failed:
        return ""
    return result.stdout.strip()


def ls_remote_heads(repo: Path | str, remote: str = "origin") -> dict[str, str]:
    """
    Asks the server which branches it has and where they point.

    Read-only on both sides: nothing is downloaded and nothing local changes.

    Args:
        repo: Working tree path.
        remote: Remote name.

    Returns:
        dict[str, str]: Short branch name mapped to commit id, empty when the
            server could not be reached.
    """

    if not is_valid_branch_name(remote):
        return {}
    result = run(
        ["ls-remote", "--heads", remote],
        cwd=repo,
        timeout=GIT_TIMEOUT_NETWORK,
        read_only=True,
    )
    if result.failed:
        return {}
    heads: dict[str, str] = {}
    for line in result.out_lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        oid, ref = parts
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = oid
    return heads


def local_ref_oid(repo: Path | str, ref: str) -> str:
    """
    Reads the commit id a local ref points at.

    Args:
        repo: Working tree path.
        ref: Ref name, e.g. ``origin/main``.

    Returns:
        str: Commit id, empty when the ref does not exist.
    """

    if not is_valid_revision(ref):
        return ""
    result = run(["rev-parse", "--verify", "--quiet", ref], cwd=repo, read_only=True)
    if result.failed:
        return ""
    return result.stdout.strip()


def check_remote_state(repo: Path | str, branch: str = "", remote: str = "origin") -> RemoteState:
    """
    Compares the server's branch tip against the local tracking ref.

    This is what the sidebar's "news from the server" badge is built on.

    Args:
        repo: Working tree path.
        branch: Branch to check. Defaults to the current branch.
        remote: Remote name.

    Returns:
        RemoteState: Comparison result. A repository without a remote comes back
            unreachable with no error — that is a normal state, not a failure.
    """

    if not has_remote(repo, remote):
        return RemoteState(reachable=False)

    target_branch = branch
    if not target_branch:
        result = run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo, read_only=True)
        target_branch = result.stdout.strip() if result.ok else ""
    if not target_branch or not is_valid_branch_name(target_branch):
        return RemoteState(reachable=False)

    heads = ls_remote_heads(repo, remote)
    if not heads:
        return RemoteState(reachable=False, error_key="sync.offline")

    remote_tip = heads.get(target_branch, "")
    local_tip = local_ref_oid(repo, f"{remote}/{target_branch}")
    incoming = 0
    if remote_tip and remote_tip != local_tip:
        # The exact count needs the objects, which we deliberately have not
        # fetched. Report "at least one" and let the UI say "news available".
        incoming = 1
    return RemoteState(
        reachable=True,
        incoming=incoming,
        remote_tip=remote_tip,
        local_tip=local_tip,
    )


def count_between(repo: Path | str, base: str, head: str) -> int:
    """
    Counts commits reachable from one revision but not the other.

    Args:
        repo: Working tree path.
        base: Revision to count from.
        head: Revision to count to.

    Returns:
        int: Commit count, zero when it cannot be determined.
    """

    if not is_valid_revision(base) or not is_valid_revision(head):
        return 0
    result = run(["rev-list", "--count", f"{base}..{head}"], cwd=repo, read_only=True)
    if result.failed:
        return 0
    try:
        return max(0, int(result.stdout.strip()))
    except ValueError:
        return 0


def fetch(repo: Path | str, remote: str = "origin", prune: bool = True) -> GitResult:
    """
    Downloads new commits without changing the working tree.

    Args:
        repo: Working tree path.
        remote: Remote name.
        prune: Whether to drop tracking refs for branches deleted on the server.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(remote):
        return _refused("fetch", "invalid remote name")
    args = ["fetch", "--tags"]
    if prune:
        args.append("--prune")
    args.append(remote)
    return run(args, cwd=repo, timeout=GIT_TIMEOUT_NETWORK)


def pull(repo: Path | str, remote: str = "origin", branch: str = "", rebase: bool = False) -> GitResult:
    """
    Downloads and integrates the server's commits.

    Args:
        repo: Working tree path.
        remote: Remote name.
        branch: Branch to pull. Defaults to the tracking configuration.
        rebase: Whether to rebase local commits on top instead of merging.

    Returns:
        GitResult: Outcome. Conflicts make this fail; the caller then opens the
            conflict assistant.
    """

    if not is_valid_branch_name(remote):
        return _refused("pull", "invalid remote name")
    if branch and not is_valid_branch_name(branch):
        return _refused("pull", "invalid branch name")
    args = ["pull", "--rebase" if rebase else "--no-rebase", "--no-edit"]
    args.append(remote)
    if branch:
        args.append(branch)
    return run(args, cwd=repo, timeout=GIT_TIMEOUT_NETWORK)


def push(
    repo: Path | str,
    remote: str = "origin",
    branch: str = "",
    set_upstream: bool = False,
    force_with_lease: bool = False,
) -> GitResult:
    """
    Sends local commits to the server.

    Only ``--force-with-lease`` is offered, never a plain ``--force``: the lease
    makes the push fail if someone else pushed in the meantime, instead of
    deleting their work silently.

    Args:
        repo: Working tree path.
        remote: Remote name.
        branch: Branch to push. Defaults to the current branch.
        set_upstream: Whether to record the tracking relationship.
        force_with_lease: Whether to overwrite the remote branch, but only if it
            still points where we last saw it.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(remote):
        return _refused("push", "invalid remote name")
    if branch and not is_valid_branch_name(branch):
        return _refused("push", "invalid branch name")
    args = ["push"]
    if force_with_lease:
        args.append("--force-with-lease")
    if set_upstream:
        args.append("--set-upstream")
    args.append(remote)
    if branch:
        args.append(branch)
    return run(args, cwd=repo, timeout=GIT_TIMEOUT_NETWORK)


def set_remote_url(repo: Path | str, url: str, remote: str = "origin") -> GitResult:
    """
    Points a remote at a different URL.

    Args:
        repo: Working tree path.
        url: New URL.
        remote: Remote name.

    Returns:
        GitResult: Outcome.
    """

    if not is_valid_branch_name(remote):
        return _refused("remote", "invalid remote name")
    if not is_valid_remote_url(url):
        return _refused("remote", "invalid remote url")
    return run(["remote", "set-url", remote, url.strip()], cwd=repo)
