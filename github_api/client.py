"""
The GitHub REST client.

Three things it takes care of beyond issuing requests:

* An ETag cache, so the frequent "is anything new" poll usually costs a 304 and
  does not eat into the rate limit.
* Rate-limit awareness. When GitHub says to wait, the client stops asking and
  reports how long, instead of hammering away and getting the user blocked.
* A short timeout on everything, so a slow network never blocks a scan thread for
  longer than a scan is worth.

The token is passed in, never read from storage here — that keeps this module
free of any opinion about where secrets live.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

from constants import GITHUB_API_ROOT, GITHUB_USER_AGENT
from github_api.models import (
    CHECK_NONE,
    Issue,
    PullRequest,
    RepositorySnapshot,
    Viewer,
    combine_check_states,
    normalize_check_state,
)

REQUEST_TIMEOUT = 15
MAX_ITEMS_PER_LIST = 50

ERROR_NO_TOKEN = "github.no_token"
ERROR_RATE_LIMITED = "github.rate_limited"
ERROR_UNAUTHORIZED = "settings.github_token_invalid"
ERROR_OFFLINE = "sync.offline"
ERROR_GENERIC = "error.git_failed"


@dataclass(slots=True)
class _CacheEntry:
    """
    One cached response.

    Attributes:
        etag: ETag the server sent.
        payload: Parsed body belonging to that ETag.
    """

    etag: str
    payload: Any


class GitHubClient:
    """
    Reads pull requests, issues and check states for GitHub repositories.
    """

    def __init__(self, token: str = "", api_root: str = GITHUB_API_ROOT) -> None:
        """
        Args:
            token: Personal access token. An empty token means every call reports
                ``github.no_token`` rather than trying and failing.
            api_root: API base URL, overridable for tests.
        """

        self._token = token.strip()
        self._api_root = api_root.rstrip("/")
        self._session = requests.Session()
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()
        self._blocked_until = 0.0

    @property
    def has_token(self) -> bool:
        """
        Reports whether a token is configured.

        Returns:
            bool: True when calls can be made.
        """

        return bool(self._token)

    def set_token(self, token: str) -> None:
        """
        Replaces the token and clears the cache.

        Args:
            token: New token.

        Returns:
            None
        """

        with self._lock:
            self._token = token.strip()
            self._cache.clear()
            self._blocked_until = 0.0

    @property
    def rate_limited_for_minutes(self) -> int:
        """
        Returns how long the client is holding off.

        Returns:
            int: Minutes remaining, zero when not limited.
        """

        remaining = self._blocked_until - time.time()
        if remaining <= 0:
            return 0
        return max(1, int(remaining // 60) + 1)

    def close(self) -> None:
        """
        Releases the HTTP session.

        Returns:
            None
        """

        self._session.close()

    # ----------------------------------------------------------------- requests

    def _headers(self, etag: str = "") -> dict[str, str]:
        """
        Builds the request headers.

        Args:
            etag: ETag to send for conditional requests.

        Returns:
            dict[str, str]: Headers.
        """

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": GITHUB_USER_AGENT,
            "Authorization": f"Bearer {self._token}",
        }
        if etag:
            headers["If-None-Match"] = etag
        return headers

    def _get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, str]:
        """
        Performs a cached GET.

        Args:
            path: Path below the API root, starting with a slash.
            params: Query parameters.

        Returns:
            tuple[Any, str]: Parsed body and an error key. Exactly one of the two
                is meaningful: on success the error key is empty.
        """

        if not self._token:
            return None, ERROR_NO_TOKEN
        if self.rate_limited_for_minutes:
            return None, ERROR_RATE_LIMITED

        cache_key = f"{path}?{sorted((params or {}).items())}"
        with self._lock:
            cached = self._cache.get(cache_key)

        try:
            response = self._session.get(
                f"{self._api_root}{path}",
                params=params,
                headers=self._headers(cached.etag if cached else ""),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            return None, ERROR_OFFLINE

        if response.status_code == 304 and cached is not None:
            return cached.payload, ""
        if response.status_code in {401, 403}:
            # 403 is also how GitHub reports a used-up rate limit, so the two are
            # told apart by the remaining-requests header rather than by status.
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                self._note_rate_limit(response.headers.get("X-RateLimit-Reset"))
                return None, ERROR_RATE_LIMITED
            return None, ERROR_UNAUTHORIZED
        if response.status_code == 404:
            return None, ERROR_GENERIC
        if response.status_code >= 400:
            return None, ERROR_GENERIC

        try:
            payload = response.json()
        except ValueError:
            return None, ERROR_GENERIC

        etag = response.headers.get("ETag", "")
        if etag:
            with self._lock:
                self._cache[cache_key] = _CacheEntry(etag=etag, payload=payload)
        return payload, ""

    def _note_rate_limit(self, reset_header: str | None) -> None:
        """
        Records how long to stay quiet after being rate limited.

        Args:
            reset_header: Value of ``X-RateLimit-Reset``, a Unix timestamp.

        Returns:
            None
        """

        try:
            reset_at = float(reset_header or 0)
        except ValueError:
            reset_at = 0.0
        # A missing or nonsensical header still deserves a pause.
        self._blocked_until = reset_at if reset_at > time.time() else time.time() + 300

    # -------------------------------------------------------------------- calls

    def viewer(self) -> tuple[Viewer | None, str]:
        """
        Asks who the token belongs to.

        Returns:
            tuple[Viewer | None, str]: The account and an error key.
        """

        if not self._token:
            return None, ERROR_NO_TOKEN
        try:
            response = self._session.get(
                f"{self._api_root}/user",
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            return None, ERROR_OFFLINE
        if response.status_code in {401, 403}:
            return None, ERROR_UNAUTHORIZED
        if response.status_code >= 400:
            return None, ERROR_GENERIC
        try:
            payload = response.json()
        except ValueError:
            return None, ERROR_GENERIC
        if not isinstance(payload, dict):
            return None, ERROR_GENERIC

        raw_scopes = response.headers.get("X-OAuth-Scopes", "")
        scopes = tuple(item.strip() for item in raw_scopes.split(",") if item.strip())
        login = payload.get("login")
        if not isinstance(login, str) or not login:
            return None, ERROR_GENERIC
        name = payload.get("name")
        return Viewer(login=login, name=name if isinstance(name, str) else "", scopes=scopes), ""

    def pull_requests(self, owner: str, repo: str) -> tuple[list[PullRequest], str]:
        """
        Lists open pull requests.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            tuple[list[PullRequest], str]: Pull requests and an error key.
        """

        payload, error = self._get(
            f"/repos/{owner}/{repo}/pulls",
            {"state": "open", "per_page": MAX_ITEMS_PER_LIST, "sort": "updated", "direction": "desc"},
        )
        if error:
            return [], error
        if not isinstance(payload, list):
            return [], ERROR_GENERIC
        found = [PullRequest.from_api(item) for item in payload]
        return [item for item in found if item is not None], ""

    def issues(self, owner: str, repo: str) -> tuple[list[Issue], str]:
        """
        Lists open issues, excluding pull requests.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            tuple[list[Issue], str]: Issues and an error key.
        """

        payload, error = self._get(
            f"/repos/{owner}/{repo}/issues",
            {"state": "open", "per_page": MAX_ITEMS_PER_LIST, "sort": "updated", "direction": "desc"},
        )
        if error:
            return [], error
        if not isinstance(payload, list):
            return [], ERROR_GENERIC
        found = [Issue.from_api(item) for item in payload]
        return [item for item in found if item is not None], ""

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

        combined, error = self._get(f"/repos/{owner}/{repo}/commits/{ref}/status")
        if error and error != ERROR_GENERIC:
            return CHECK_NONE, error
        if isinstance(combined, dict):
            total = combined.get("total_count")
            if isinstance(total, int) and total > 0:
                states.append(normalize_check_state(combined.get("state")))

        runs, error = self._get(f"/repos/{owner}/{repo}/commits/{ref}/check-runs")
        if error and error != ERROR_GENERIC:
            return CHECK_NONE, error
        if isinstance(runs, dict):
            entries = runs.get("check_runs")
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

    def snapshot(self, owner: str, repo: str, ref: str = "") -> RepositorySnapshot:
        """
        Fetches everything the panels need for one repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            ref: Commit or branch whose check state should be read.

        Returns:
            RepositorySnapshot: Collected data, or one carrying an error key.
        """

        pulls, error = self.pull_requests(owner, repo)
        if error:
            return RepositorySnapshot(
                owner=owner,
                repo=repo,
                error_key=error,
                retry_after_minutes=self.rate_limited_for_minutes,
            )

        found_issues, issue_error = self.issues(owner, repo)
        head_state, _state_error = self.check_state(owner, repo, ref)

        enriched: list[PullRequest] = []
        for pull in pulls:
            if pull.head_sha:
                state, _error = self.check_state(owner, repo, pull.head_sha)
                enriched.append(pull.with_check_state(state))
            else:
                enriched.append(pull)

        # A failure fetching issues does not propagate: the pull requests were
        # fetched successfully and throwing them away over a secondary panel
        # would be the wrong trade. The issues list simply comes back empty.
        del issue_error
        return RepositorySnapshot(
            owner=owner,
            repo=repo,
            pull_requests=tuple(enriched),
            issues=tuple(found_issues),
            head_check_state=head_state,
        )
