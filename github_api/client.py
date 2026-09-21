"""
The GitHub client.

One class assembled from the endpoint groups, so a caller holds a single object
rather than five. The transport underneath it (``github_api.http``) owns caching,
pagination, rate limits and error translation; the endpoint modules own what to
ask for; this module owns the few calls that belong to no group, plus the
snapshot the repository panel is built on.

The error constants are re-exported here because callers have always imported
them from this module, and moving them would have been churn for its own sake.
"""

from __future__ import annotations

from github_api.actions import ActionEndpoints
from github_api.http import (  # noqa: F401 - re-exported for callers
    ERROR_CONFLICT,
    ERROR_FORBIDDEN,
    ERROR_GENERIC,
    ERROR_NO_TOKEN,
    ERROR_NOT_FOUND,
    ERROR_NOT_MERGEABLE,
    ERROR_OFFLINE,
    ERROR_RATE_LIMITED,
    ERROR_UNAUTHORIZED,
    ERROR_VALIDATION,
    REQUEST_TIMEOUT,
    ApiResult,
    HttpTransport,
    RateLimit,
    missing_scopes,
)
from github_api.issues import IssueEndpoints
from github_api.models import (
    CHECK_NONE,
    Issue,
    PullRequest,
    Viewer,
    combine_check_states,
    normalize_check_state,
)
from github_api.pulls import PullRequestEndpoints
from github_api.releases import ReleaseEndpoints
from github_api.repos import RepositoryEndpoints

MAX_ITEMS_PER_LIST = 50

# Scopes the write features need, so the settings dialog can name a missing one
# instead of letting the user discover it as a 403 halfway through an action.
SCOPE_REPO = "repo"
SCOPE_WORKFLOW = "workflow"
SCOPE_DELETE_REPO = "delete_repo"


class GitHubClient(
    RepositoryEndpoints,
    IssueEndpoints,
    PullRequestEndpoints,
    ReleaseEndpoints,
    ActionEndpoints,
    HttpTransport,
):
    """
    Reads and changes repositories, issues, pull requests, releases and runs.
    """

    def viewer(self) -> tuple[Viewer | None, str]:
        """
        Asks who the token belongs to.

        Deliberately uncached: this is the call the settings dialog makes to
        verify a token that was just pasted, and an answer from before the change
        would be worse than no answer.

        Returns:
            tuple[Viewer | None, str]: The account and an error key.
        """

        if not self.has_token:
            return None, ERROR_NO_TOKEN
        result = self.send("GET", "/user")
        if not result.ok:
            return None, result.error_key
        payload = result.data
        login = payload.get("login")
        if not isinstance(login, str) or not login:
            return None, ERROR_GENERIC
        name = payload.get("name")
        return Viewer(login=login, name=name if isinstance(name, str) else "", scopes=self.scopes), ""

    def rate_limit_state(self) -> ApiResult:
        """
        Reads the current request budget from the server.

        Returns:
            ApiResult: Payload is the raw rate limit object.
        """

        return self.get("/rate_limit")

    def missing_scopes_for(self, *required: str) -> list[str]:
        """
        Reports which of the given scopes the token is missing.

        Args:
            *required: Scope names the action needs.

        Returns:
            list[str]: Missing names, empty when nothing is missing or when the
                token reports no scopes at all, which a fine-grained token does.
        """

        return missing_scopes(self.scopes, required)

    # ------------------------------------------------- the original read calls

    def pull_requests(
        self, owner: str, repo: str, state: str = "open"
    ) -> tuple[list[PullRequest], str]:
        """
        Lists pull requests.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: ``open``, ``closed`` or ``all``.

        Returns:
            tuple[list[PullRequest], str]: Pull requests and an error key.
        """

        result = self.list_pull_requests(owner, repo, state=state, limit=MAX_ITEMS_PER_LIST)
        if not result.ok:
            return [], result.error_key
        return list(result.payload or []), ""

    def issues(self, owner: str, repo: str, state: str = "open") -> tuple[list[Issue], str]:
        """
        Lists issues, excluding pull requests.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: ``open``, ``closed`` or ``all``.

        Returns:
            tuple[list[Issue], str]: Issues and an error key.
        """

        result = self.list_issues(owner, repo, state=state, limit=MAX_ITEMS_PER_LIST)
        if not result.ok:
            return [], result.error_key
        return list(result.payload or []), ""

    def check_state(self, owner: str, repo: str, ref: str) -> tuple[str, str]:
        """
        Reads the aggregated check state of a commit.

        Both the older commit-status API and the newer check-runs API are
        consulted, because a repository may use either or both.

        Args:
            owner: Repository owner.
            repo: Repository name.
            ref: Commit id or branch name.

        Returns:
            tuple[str, str]: Combined state and an error key.
        """

        if not ref:
            return CHECK_NONE, ""
        states: list[str] = []
        tolerated = {ERROR_GENERIC, ERROR_NOT_FOUND}

        combined = self.get(f"/repos/{owner}/{repo}/commits/{ref}/status")
        if not combined.ok and combined.error_key not in tolerated:
            return CHECK_NONE, combined.error_key
        if combined.ok:
            total = combined.data.get("total_count")
            if isinstance(total, int) and total > 0:
                states.append(normalize_check_state(combined.data.get("state")))

        runs = self.get(f"/repos/{owner}/{repo}/commits/{ref}/check-runs")
        if not runs.ok and runs.error_key not in tolerated:
            return CHECK_NONE, runs.error_key
        if runs.ok:
            entries = runs.data.get("check_runs")
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    status = entry.get("status")
                    if status in {"queued", "in_progress", "waiting", "requested"}:
                        states.append(normalize_check_state(status))
                    else:
                        states.append(normalize_check_state(entry.get("conclusion")))

        return combine_check_states(states), ""

    def repository_badge(self, owner: str, repo: str, ref: str = "") -> tuple[int, str, str]:
        """
        Fetches just the two numbers the sidebar shows for a repository.

        Deliberately not ``snapshot``: that one asks for the check state of every
        open pull request, which is one request each. The sidebar needs the count
        and the state of the current branch's tip, and nothing else, so it costs
        two requests regardless of how many pull requests are open.

        Args:
            owner: Repository owner.
            repo: Repository name.
            ref: Commit or branch whose check state is wanted.

        Returns:
            tuple[int, str, str]: Open pull requests, check state and an error key.
        """

        pulls = self.list_pull_requests(owner, repo, state="open", limit=MAX_ITEMS_PER_LIST)
        if not pulls.ok:
            return 0, CHECK_NONE, pulls.error_key
        state, error = self.check_state(owner, repo, ref)
        return len(pulls.payload or []), state, error
