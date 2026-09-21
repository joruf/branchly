"""
Bringing every project up to the server's version in one go.

Pulling one project is a decision the user makes while looking at it. Pulling
twenty at once is not, so this layer is deliberately more careful than the button
in the header:

* **Fast-forward only.** Git is told to refuse anything else, so a pull here can
  never build a merge commit, never leave a conflict behind, and never move HEAD
  anywhere but straight forward. A branch that has diverged is reported, not
  wrestled with.
* **Anything unclear is skipped, not guessed at.** Uncommitted work, a detached
  HEAD, an operation in progress, no server, no tracking branch, own commits — each
  is a reason to leave that project alone and say so by name.
* **Skipped still means informed.** A skipped project is fetched anyway. Fetching
  never touches the working tree, and it makes the badge tell the truth about what
  is waiting there.

Nothing here knows about Qt: the coordinator that runs these jobs on a thread pool
lives in ``services.scheduler``, so the decisions below stay testable without a
window.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gitops import remote as remote_mod
from gitops.runner import ERROR_GENERIC
from gitops.status import read_state

#: Fast-forwarded onto new commits.
RESULT_PULLED = "pulled"
#: Contacted the server and there was nothing new.
RESULT_CURRENT = "current"
#: Left alone on purpose; ``reason_key`` says why.
RESULT_SKIPPED = "skipped"
#: Tried and failed; ``reason_key`` and ``detail`` say why.
RESULT_FAILED = "failed"

SKIP_MISSING = "repo.missing"
SKIP_NO_REMOTE = "pull_all.skip_no_remote"
SKIP_DETACHED = "pull_all.skip_detached"
SKIP_CONFLICTS = "pull_all.skip_conflicts"
SKIP_IN_PROGRESS = "pull_all.skip_in_progress"
SKIP_DIRTY = "pull_all.skip_dirty"
SKIP_NO_UPSTREAM = "pull_all.skip_no_upstream"
SKIP_OWN_COMMITS = "pull_all.skip_own_commits"
SKIP_EMPTY = "pull_all.skip_empty"


@dataclass(frozen=True, slots=True)
class PullJob:
    """
    One project to bring up to date.

    Attributes:
        path: Working tree path.
        key: Registry key, used to match the result back to its entry.
        name: Display name, so the report can name the project without the UI
            having to look it up again.
    """

    path: Path
    key: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class PullResult:
    """
    What happened to one project.

    Attributes:
        key: Registry key of the project.
        name: Display name.
        state: One of the ``RESULT_*`` constants.
        reason_key: Translation key explaining a skip or a failure.
        detail: Git's own words, for the details pane.
        commits: How many commits arrived.
        waiting: How many commits are waiting on the server for a project that was
            skipped, 0 when unknown.
        branch: Branch the project is on, for the report.
    """

    key: str
    name: str = ""
    state: str = RESULT_CURRENT
    reason_key: str = ""
    detail: str = ""
    commits: int = 0
    waiting: int = 0
    branch: str = ""

    @property
    def changed(self) -> bool:
        """
        Reports whether this project actually moved.

        Returns:
            bool: True when commits were fast-forwarded in.
        """

        return self.state == RESULT_PULLED

    @property
    def needs_attention(self) -> bool:
        """
        Reports whether the user has to do something about this project.

        Returns:
            bool: True for a skip or a failure — the two outcomes that leave the
                project behind the server.
        """

        return self.state in (RESULT_SKIPPED, RESULT_FAILED)


def build_jobs(entries: list) -> list[PullJob]:
    """
    Turns registry entries into pull jobs.

    Args:
        entries: Entries to bring up to date.

    Returns:
        list[PullJob]: One job per entry, in the order given.
    """

    return [PullJob(path=entry.path, key=entry.key, name=entry.name) for entry in entries]


def blocking_reason(state) -> str:  # noqa: ANN001 - RepositoryState
    """
    Names the one thing that stops this project from being fast-forwarded.

    Ordered by what the user most needs to hear: an unresolved conflict or a
    half-finished operation outranks a stray edit, and both outrank the mere
    absence of a tracking branch.

    Args:
        state: Freshly read repository state.

    Returns:
        str: Translation key for the reason, empty when nothing is in the way.
    """

    if state.initial:
        return SKIP_EMPTY
    if state.has_conflicts:
        return SKIP_CONFLICTS
    if state.operation_in_progress:
        return SKIP_IN_PROGRESS
    if state.detached:
        return SKIP_DETACHED
    if not state.is_clean:
        return SKIP_DIRTY
    if not state.upstream:
        return SKIP_NO_UPSTREAM
    if state.ahead:
        # Own commits mean the branch has diverged, so a fast-forward is
        # impossible by definition. Better to say that than to let git say it.
        return SKIP_OWN_COMMITS
    return ""


def pull_one(job: PullJob) -> PullResult:
    """
    Brings one project up to the server's version, or explains why it did not.

    Args:
        job: Project to update.

    Returns:
        PullResult: What happened. Never raises: one unusable project must not
            take the whole run down.
    """

    path = Path(job.path)
    if not (path / ".git").exists():
        return PullResult(
            key=job.key, name=job.name, state=RESULT_SKIPPED, reason_key=SKIP_MISSING
        )

    state = read_state(path)
    if not state.ok:
        return PullResult(
            key=job.key,
            name=job.name,
            state=RESULT_FAILED,
            reason_key=state.error_key or ERROR_GENERIC,
        )

    branch = state.display_branch
    if not remote_mod.has_remote(path):
        return PullResult(
            key=job.key,
            name=job.name,
            state=RESULT_SKIPPED,
            reason_key=SKIP_NO_REMOTE,
            branch=branch,
        )

    reason = blocking_reason(state)
    if reason:
        return PullResult(
            key=job.key,
            name=job.name,
            state=RESULT_SKIPPED,
            reason_key=reason,
            waiting=_fetch_and_count(path, state.upstream),
            branch=branch,
        )

    before = state.oid
    result = remote_mod.pull(path, ff_only=True)
    if result.failed:
        return PullResult(
            key=job.key,
            name=job.name,
            state=RESULT_FAILED,
            reason_key=result.error_key() or ERROR_GENERIC,
            detail=(result.stderr or result.stdout).strip(),
            branch=branch,
        )

    after = remote_mod.local_ref_oid(path, "HEAD")
    arrived = remote_mod.count_between(path, before, after) if before and after else 0
    return PullResult(
        key=job.key,
        name=job.name,
        state=RESULT_PULLED if arrived else RESULT_CURRENT,
        commits=arrived,
        branch=branch,
    )


def _fetch_and_count(path: Path, upstream: str) -> int:
    """
    Fetches a project that will not be pulled, and counts what is waiting.

    Fetching is safe where pulling is not: it writes only to the tracking refs and
    leaves the working tree untouched, so a skipped project still ends up with an
    honest badge instead of a stale one.

    Args:
        path: Working tree path.
        upstream: Tracking branch, empty when there is none.

    Returns:
        int: Commits waiting on the server, 0 when that cannot be worked out.
    """

    if remote_mod.fetch(path).failed:
        return 0
    if not upstream:
        return 0
    return remote_mod.count_between(path, "HEAD", upstream)


@dataclass(frozen=True, slots=True)
class PullSummary:
    """
    The whole run in numbers.

    Attributes:
        pulled: Projects that moved forward.
        commits: Commits that arrived in total.
        current: Projects that were already up to date.
        skipped: Projects deliberately left alone.
        failed: Projects whose update did not work.
    """

    pulled: int = 0
    commits: int = 0
    current: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        """
        Returns how many projects the run covered.

        Returns:
            int: Sum of every outcome.
        """

        return self.pulled + self.current + self.skipped + self.failed


def summarize(results: list[PullResult]) -> PullSummary:
    """
    Counts the outcomes of a run.

    Args:
        results: Every result of the run.

    Returns:
        PullSummary: The totals.
    """

    return PullSummary(
        pulled=sum(1 for item in results if item.state == RESULT_PULLED),
        commits=sum(item.commits for item in results),
        current=sum(1 for item in results if item.state == RESULT_CURRENT),
        skipped=sum(1 for item in results if item.state == RESULT_SKIPPED),
        failed=sum(1 for item in results if item.state == RESULT_FAILED),
    )
