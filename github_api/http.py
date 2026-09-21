"""
The HTTP transport underneath every GitHub call.

Splitting this out from the endpoints keeps one hard question in one place: what
happens when the server says something other than "here you go". The endpoint
modules describe *what* to ask for, this module owns *how* asking works.

Five things it takes care of:

* An ETag cache, so the frequent "is anything new" poll usually costs a 304 and
  does not eat into the rate limit. Any write clears the cache, because a list
  that was correct a second ago is not correct after something was added to it.
* Rate-limit awareness. When GitHub says to wait, the client stops asking and
  reports how long, instead of hammering away and getting the user blocked.
* Pagination. GitHub caps a page at 100 items and names the next one in the
  ``Link`` header; a caller asking for "all issues" should not have to know that.
* Turning a failed response into a translation key plus GitHub's own sentence.
  A 422 that says "name already exists on this account" is worth showing; a bare
  "something went wrong" is not.
* A timeout on everything, so a slow network never blocks a worker thread for
  longer than the work is worth.

The token is passed in, never read from storage here, which keeps this module
free of any opinion about where secrets live.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

import requests

from constants import GITHUB_API_ROOT, GITHUB_USER_AGENT

REQUEST_TIMEOUT = 15
UPLOAD_TIMEOUT = 120

# GitHub's own ceiling for a page. Asking for more is silently capped, asking for
# less just means more round trips.
PAGE_SIZE = 100

# How many pages a "fetch everything" call will walk before it stops. A project
# with more than 5000 open issues is not going to be browsed in a list widget,
# and an endless loop on a paginated endpoint is worse than a truncated list.
MAX_PAGES = 50

ERROR_NO_TOKEN = "github.no_token"
ERROR_RATE_LIMITED = "github.rate_limited"
ERROR_UNAUTHORIZED = "settings.github_token_invalid"
ERROR_OFFLINE = "sync.offline"
ERROR_GENERIC = "error.git_failed"
ERROR_NOT_FOUND = "github.not_found"
ERROR_FORBIDDEN = "github.forbidden"
ERROR_VALIDATION = "github.validation_failed"
ERROR_CONFLICT = "github.conflict"
ERROR_NOT_MERGEABLE = "github.not_mergeable"

# Write methods. Listed rather than inferred so a future addition has to be a
# deliberate edit: everything in here empties the response cache.
_WRITE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})

# Keys under which a list endpoint may hide its actual list. GitHub answers some
# endpoints with a bare array and others with an object holding a total count
# plus the array under a name of its own.
_ENVELOPE_KEYS = (
    "items",
    "workflow_runs",
    "workflows",
    "check_runs",
    "check_suites",
    "artifacts",
    "jobs",
    "secrets",
    "variables",
    "repositories",
)


@dataclass(slots=True)
class _CacheEntry:
    """
    One cached response.

    Attributes:
        etag: ETag the server sent.
        payload: Parsed body belonging to that ETag.
        next_url: Successor page the same response named, so a 304 does not lose
            the rest of a paginated list.
    """

    etag: str
    payload: Any
    next_url: str = ""


@dataclass(frozen=True, slots=True)
class ApiResult:
    """
    The outcome of one API call.

    Reads and writes both come back in this shape so a caller never has to
    remember whether a particular endpoint returns a tuple, a payload or a bool.

    Attributes:
        ok: Whether the call succeeded.
        payload: Parsed body. ``None`` for a 204, which is what GitHub answers to
            a successful delete.
        error_key: Translation key describing the failure, empty on success.
        detail: GitHub's own explanation, already flattened into one sentence.
            Shown next to the translated headline, because only the server knows
            that the branch was protected or the name was taken.
        retry_after_minutes: How long to wait when the failure was a rate limit.
        status: HTTP status code, kept for the rare caller that needs to tell two
            failures apart without parsing text.
        next_url: URL of the following page, as the ``Link`` header named it.
            Empty when this was the last page or the endpoint does not paginate.
    """

    ok: bool
    payload: Any = None
    error_key: str = ""
    detail: str = ""
    retry_after_minutes: int = 0
    status: int = 0
    next_url: str = ""

    @property
    def items(self) -> list[Any]:
        """
        Returns the payload as a list.

        Convenience for the many endpoints that answer with an array: a failed
        call and an unexpected object both come back as an empty list, so a
        caller filling a list widget needs no type check of its own.

        Returns:
            list[Any]: The payload when it is a list, otherwise an empty list.
        """

        return self.payload if isinstance(self.payload, list) else []

    @property
    def data(self) -> dict[str, Any]:
        """
        Returns the payload as an object.

        Returns:
            dict[str, Any]: The payload when it is a dict, otherwise an empty dict.
        """

        return self.payload if isinstance(self.payload, dict) else {}


@dataclass(frozen=True, slots=True)
class RateLimit:
    """
    What the API reports about the request budget.

    Attributes:
        limit: Requests allowed per hour.
        remaining: Requests left.
        reset_at: Unix timestamp at which the budget refills.
    """

    limit: int = 0
    remaining: int = 0
    reset_at: float = 0.0

    @property
    def minutes_until_reset(self) -> int:
        """
        Returns the wait until the budget refills.

        Returns:
            int: Whole minutes, rounded up, zero when the moment has passed.
        """

        remaining = self.reset_at - time.time()
        if remaining <= 0:
            return 0
        return max(1, int(remaining // 60) + 1)


def _flatten_message(payload: Any) -> str:
    """
    Turns a GitHub error body into one readable sentence.

    A 422 carries a headline plus a list of field errors, and the field errors
    are the half that says what to fix. Both are folded into one line rather than
    showing only the generic headline.

    Args:
        payload: Parsed error body.

    Returns:
        str: The explanation, empty when the body carried none.
    """

    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""

    parts: list[str] = []
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        parts.append(message.strip())

    errors = payload.get("errors")
    if isinstance(errors, list):
        for entry in errors:
            if isinstance(entry, str):
                parts.append(entry.strip())
                continue
            if not isinstance(entry, dict):
                continue
            # "message" is set for the interesting failures (custom validation);
            # the field/code pair is the fallback for the mechanical ones.
            text = entry.get("message")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
                continue
            field_name = entry.get("field")
            code = entry.get("code")
            if isinstance(field_name, str) and isinstance(code, str):
                parts.append(f"{field_name}: {code}")

    return ". ".join(dict.fromkeys(part for part in parts if part))


def _parse_link_header(value: str) -> dict[str, str]:
    """
    Reads the ``Link`` header into its named relations.

    Args:
        value: Raw header value.

    Returns:
        dict[str, str]: Relation name to URL, for example ``{"next": "..."}``.
    """

    links: dict[str, str] = {}
    for chunk in value.split(","):
        section = chunk.split(";")
        if len(section) < 2:
            continue
        url = section[0].strip()
        if not (url.startswith("<") and url.endswith(">")):
            continue
        for attribute in section[1:]:
            name, _, raw = attribute.strip().partition("=")
            if name.strip() == "rel":
                links[raw.strip().strip('"')] = url[1:-1]
    return links


class HttpTransport:
    """
    Issues the requests every GitHub endpoint is built on.

    Attributes:
        rate_limit: What the last response said about the request budget.
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
        self._scopes: tuple[str, ...] = ()
        self.rate_limit = RateLimit()

    # ------------------------------------------------------------------ state

    @property
    def has_token(self) -> bool:
        """
        Reports whether a token is configured.

        Returns:
            bool: True when calls can be made.
        """

        return bool(self._token)

    @property
    def api_root(self) -> str:
        """
        Returns the API base URL in use.

        Returns:
            str: Base URL without a trailing slash.
        """

        return self._api_root

    @property
    def scopes(self) -> tuple[str, ...]:
        """
        Returns the scopes the last response attributed to the token.

        Returns:
            tuple[str, ...]: Scope names, empty before the first call and for a
                fine-grained token, which reports no classic scopes at all.
        """

        return self._scopes

    def set_token(self, token: str) -> None:
        """
        Replaces the token and clears everything derived from the old one.

        Args:
            token: New token.

        Returns:
            None
        """

        with self._lock:
            self._token = token.strip()
            self._cache.clear()
            self._blocked_until = 0.0
            self._scopes = ()

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

    def clear_cache(self) -> None:
        """
        Drops every cached response.

        Returns:
            None
        """

        with self._lock:
            self._cache.clear()

    def close(self) -> None:
        """
        Releases the HTTP session.

        Returns:
            None
        """

        self._session.close()

    # --------------------------------------------------------------- internals

    def _headers(self, etag: str = "", extra: dict[str, str] | None = None) -> dict[str, str]:
        """
        Builds the request headers.

        Args:
            etag: ETag to send for a conditional request.
            extra: Additional headers, for example a different Accept.

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
        if extra:
            headers.update(extra)
        return headers

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

    def _note_headers(self, response: requests.Response) -> None:
        """
        Reads the bookkeeping headers off a response.

        Args:
            response: Response to inspect.

        Returns:
            None
        """

        raw_scopes = response.headers.get("X-OAuth-Scopes")
        if raw_scopes is not None:
            self._scopes = tuple(item.strip() for item in raw_scopes.split(",") if item.strip())

        def _number(name: str) -> float:
            try:
                return float(response.headers.get(name, "") or 0)
            except ValueError:
                return 0.0

        if "X-RateLimit-Limit" in response.headers:
            self.rate_limit = RateLimit(
                limit=int(_number("X-RateLimit-Limit")),
                remaining=int(_number("X-RateLimit-Remaining")),
                reset_at=_number("X-RateLimit-Reset"),
            )

    def _classify(self, response: requests.Response) -> tuple[str, str]:
        """
        Maps a failed response onto a translation key and an explanation.

        Args:
            response: Response with a status of 400 or above.

        Returns:
            tuple[str, str]: Error key and GitHub's own sentence.
        """

        try:
            body = response.json()
        except ValueError:
            body = None
        detail = _flatten_message(body)

        status = response.status_code
        if status in {401, 403}:
            # 403 is also how GitHub reports a used-up rate limit, so the two are
            # told apart by the remaining-requests header rather than by status.
            if response.headers.get("X-RateLimit-Remaining") == "0":
                self._note_rate_limit(response.headers.get("X-RateLimit-Reset"))
                return ERROR_RATE_LIMITED, detail
            if status == 401:
                return ERROR_UNAUTHORIZED, detail
            return ERROR_FORBIDDEN, detail
        if status == 404:
            return ERROR_NOT_FOUND, detail
        if status == 409:
            return ERROR_CONFLICT, detail
        if status in {405, 422} and "merge" in detail.lower():
            return ERROR_NOT_MERGEABLE, detail
        if status in {405, 422}:
            return ERROR_VALIDATION, detail
        return ERROR_GENERIC, detail

    def _failure(self, error_key: str, detail: str = "", status: int = 0) -> ApiResult:
        """
        Builds a failed result.

        Args:
            error_key: Translation key.
            detail: GitHub's explanation.
            status: HTTP status code.

        Returns:
            ApiResult: The failure.
        """

        return ApiResult(
            ok=False,
            error_key=error_key,
            detail=detail,
            retry_after_minutes=self.rate_limited_for_minutes,
            status=status,
        )

    # ---------------------------------------------------------------- requests

    def get(self, path: str, params: dict[str, Any] | None = None, accept: str = "") -> ApiResult:
        """
        Performs a cached GET.

        Args:
            path: Path below the API root, starting with a slash, or a full URL.
            params: Query parameters.
            accept: Alternative Accept header, for example to ask for raw content.

        Returns:
            ApiResult: Parsed body, or the reason it could not be fetched.
        """

        if not self._token:
            return self._failure(ERROR_NO_TOKEN)
        if self.rate_limited_for_minutes:
            return self._failure(ERROR_RATE_LIMITED)

        url = path if path.startswith("http") else f"{self._api_root}{path}"
        cache_key = f"{accept}|{url}?{sorted((params or {}).items())}"
        with self._lock:
            cached = self._cache.get(cache_key)

        try:
            response = self._session.get(
                url,
                params=params,
                headers=self._headers(cached.etag if cached else "", {"Accept": accept} if accept else None),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            return self._failure(ERROR_OFFLINE)

        self._note_headers(response)

        links = _parse_link_header(response.headers.get("Link", ""))
        if response.status_code == 304 and cached is not None:
            return ApiResult(ok=True, payload=cached.payload, status=304, next_url=cached.next_url)
        if response.status_code >= 400:
            error_key, detail = self._classify(response)
            return self._failure(error_key, detail, response.status_code)

        payload = self._decode(response, accept)
        etag = response.headers.get("ETag", "")
        if etag:
            with self._lock:
                self._cache[cache_key] = _CacheEntry(
                    etag=etag, payload=payload, next_url=links.get("next", "")
                )
        return ApiResult(
            ok=True, payload=payload, status=response.status_code, next_url=links.get("next", "")
        )

    def get_all(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> ApiResult:
        """
        Fetches every page of a list endpoint.

        Pages are followed through the ``Link`` header rather than by counting up
        a page number, which is what GitHub asks for and what keeps working when
        an endpoint changes its page size.

        Args:
            path: Path below the API root, starting with a slash.
            params: Query parameters. ``per_page`` is filled in when missing.
            limit: Stop once this many items were collected, zero for no limit.

        Returns:
            ApiResult: All items as one list, or the first failure. A failure on a
                later page still reports the failure rather than a half list,
                because a silently truncated list is the worse lie.
        """

        query = dict(params or {})
        query.setdefault("per_page", PAGE_SIZE)

        collected: list[Any] = []
        url: str = path
        next_params: dict[str, Any] | None = query

        for _page in range(MAX_PAGES):
            result = self.get(url, next_params)
            if not result.ok:
                return result
            batch = result.payload
            if isinstance(batch, dict):
                # Several endpoints wrap their list in an object carrying a total
                # count. The key is named after the resource, so each one has to
                # be listed: an unknown envelope reads as an empty page and the
                # list silently comes back empty.
                unwrapped = next(
                    (batch[key] for key in _ENVELOPE_KEYS if isinstance(batch.get(key), list)),
                    None,
                )
                if unwrapped is None:
                    return ApiResult(ok=True, payload=collected, status=result.status)
                batch = unwrapped
            if not isinstance(batch, list):
                return ApiResult(ok=True, payload=collected, status=result.status)
            collected.extend(batch)
            if limit and len(collected) >= limit:
                return ApiResult(ok=True, payload=collected[:limit], status=result.status)

            if not result.next_url:
                break
            url = result.next_url
            # The next URL already carries the query, so sending params again
            # would duplicate them.
            next_params = None

        return ApiResult(ok=True, payload=collected, status=200)

    def send(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> ApiResult:
        """
        Performs a request that changes something.

        Args:
            method: HTTP verb, one of POST, PATCH, PUT or DELETE.
            path: Path below the API root, starting with a slash, or a full URL.
            body: JSON body.
            params: Query parameters.

        Returns:
            ApiResult: Parsed body, or the reason the change was refused. A
                successful call empties the response cache, so the next read
                cannot answer from a list that predates the change.
        """

        if not self._token:
            return self._failure(ERROR_NO_TOKEN)
        if self.rate_limited_for_minutes:
            return self._failure(ERROR_RATE_LIMITED)

        verb = method.upper()
        url = path if path.startswith("http") else f"{self._api_root}{path}"
        try:
            response = self._session.request(
                verb,
                url,
                json=body,
                params=params,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            return self._failure(ERROR_OFFLINE)

        self._note_headers(response)
        if response.status_code >= 400:
            error_key, detail = self._classify(response)
            return self._failure(error_key, detail, response.status_code)

        if verb in _WRITE_METHODS:
            self.clear_cache()
        return ApiResult(ok=True, payload=self._decode(response), status=response.status_code)

    def upload(self, url: str, name: str, content_type: str, payload: bytes) -> ApiResult:
        """
        Uploads a release asset.

        Asset uploads go to a different host than the API and send raw bytes
        rather than JSON, which is why they do not go through ``send``.

        Args:
            url: Upload URL from the release, with its ``{?name,label}`` template
                already removed.
            name: File name the asset gets on the release.
            content_type: MIME type of the payload.
            payload: File contents.

        Returns:
            ApiResult: The created asset, or the reason it was refused.
        """

        if not self._token:
            return self._failure(ERROR_NO_TOKEN)
        try:
            response = self._session.post(
                url,
                params={"name": name},
                data=payload,
                headers=self._headers(extra={"Content-Type": content_type or "application/octet-stream"}),
                timeout=UPLOAD_TIMEOUT,
            )
        except requests.RequestException:
            return self._failure(ERROR_OFFLINE)

        self._note_headers(response)
        if response.status_code >= 400:
            error_key, detail = self._classify(response)
            return self._failure(error_key, detail, response.status_code)
        self.clear_cache()
        return ApiResult(ok=True, payload=self._decode(response), status=response.status_code)

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> ApiResult:
        """
        Sends one GraphQL query.

        Used only where REST has no equivalent. Marking a draft pull request as
        ready is the case that forced this in: there is no REST endpoint for it,
        and leaving the button out over that would have been the wrong trade.

        Args:
            query: The GraphQL document.
            variables: Variable values.

        Returns:
            ApiResult: The ``data`` object, or the reason the query failed.
                GraphQL answers 200 even for errors, so the body is inspected
                rather than the status.
        """

        if not self._token:
            return self._failure(ERROR_NO_TOKEN)
        try:
            response = self._session.post(
                f"{self._api_root}/graphql",
                json={"query": query, "variables": variables or {}},
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            return self._failure(ERROR_OFFLINE)

        self._note_headers(response)
        if response.status_code >= 400:
            error_key, detail = self._classify(response)
            return self._failure(error_key, detail, response.status_code)

        body = self._decode(response)
        if not isinstance(body, dict):
            return self._failure(ERROR_GENERIC, "", response.status_code)
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            messages = [
                entry.get("message", "")
                for entry in errors
                if isinstance(entry, dict) and isinstance(entry.get("message"), str)
            ]
            return self._failure(ERROR_VALIDATION, ". ".join(filter(None, messages)), response.status_code)
        self.clear_cache()
        return ApiResult(ok=True, payload=body.get("data"), status=response.status_code)

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _decode(response: requests.Response, accept: str = "") -> Any:
        """
        Reads a response body.

        Args:
            response: Response to read.
            accept: Accept header the request was made with. A raw or diff media
                type comes back as text, everything else as JSON.

        Returns:
            Any: Parsed body. ``None`` for an empty one, which is what a 204
                delete answers with.
        """

        if accept and "json" not in accept:
            return response.text
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None


def missing_scopes(granted: Iterable[str], required: Iterable[str]) -> list[str]:
    """
    Reports which of the required scopes a token does not carry.

    A fine-grained token reports no classic scopes at all, so an empty grant is
    treated as "cannot tell" rather than as "has nothing": warning about a
    missing scope that is actually present would train the user to ignore the
    warning.

    Args:
        granted: Scopes the API attributed to the token.
        required: Scopes the action needs.

    Returns:
        list[str]: Missing scope names, empty when nothing is missing or when the
            token reports no scopes.
    """

    have = {item.strip() for item in granted if item.strip()}
    if not have:
        return []
    # "repo" implies its narrower children, which the API lists separately only
    # for tokens that were granted them individually.
    if "repo" in have:
        have.update({"repo:status", "repo_deployment", "public_repo", "security_events"})
    if "admin:org" in have:
        have.update({"read:org", "write:org"})
    if "write:org" in have:
        have.add("read:org")
    return [item for item in required if item not in have]


def as_models(result: ApiResult, factory: Any) -> ApiResult:
    """
    Turns a successful list result into typed objects.

    Args:
        result: What the transport returned.
        factory: Callable building one object from one API element, returning
            ``None`` for an element it cannot use.

    Returns:
        ApiResult: The same result with its payload replaced by the parsed
            objects. A failed result is handed back untouched, so a caller checks
            ``ok`` once rather than at both levels.
    """

    if not result.ok:
        return result
    parsed = [factory(entry) for entry in result.items]
    return replace(result, payload=[entry for entry in parsed if entry is not None])


def as_model(result: ApiResult, factory: Any) -> ApiResult:
    """
    Turns a successful object result into a typed object.

    Args:
        result: What the transport returned.
        factory: Callable building the object from the API body.

    Returns:
        ApiResult: The same result with its payload replaced. An object the
            factory refuses becomes a generic failure rather than a ``None``
            payload that every caller would have to check for.
    """

    if not result.ok:
        return result
    parsed = factory(result.payload)
    if parsed is None:
        return ApiResult(ok=False, error_key=ERROR_GENERIC, status=result.status)
    return replace(result, payload=parsed)
