"""
Signing in to GitHub from a desktop program.

The device flow is the one OAuth variant made for this situation. A desktop
program cannot keep a client secret (anyone can read it out of the files) and
cannot reliably own a redirect URL (there is no web server, and a local one is
its own kind of trouble). So instead GitHub hands out a short code, the user
types it into a page in their own browser, and the program polls until GitHub
says the user agreed.

What that buys over asking for a pasted token: the user never handles a
credential, the scopes are fixed by Branchly rather than by whichever boxes
somebody happened to tick, and the token can be revoked on GitHub as one named
application.

It needs a registered OAuth app, whose client id is public by design. Without
one configured this module reports that plainly and the dialog falls back to the
token route, which needs nothing.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

from constants import (
    GITHUB_ACCESS_TOKEN_URL,
    GITHUB_DEVICE_CODE_URL,
    GITHUB_DEVICE_VERIFICATION_URL,
    GITHUB_OAUTH_CLIENT_ID,
    GITHUB_OAUTH_SCOPES,
    GITHUB_OAUTH_TIMEOUT,
    GITHUB_USER_AGENT,
)

CLIENT_ID_VARIABLE = "BRANCHLY_GITHUB_CLIENT_ID"

# What ``poll`` reports back. Only ``OUTCOME_TOKEN`` carries a token.
OUTCOME_TOKEN = "token"
OUTCOME_PENDING = "pending"
OUTCOME_SLOW_DOWN = "slow_down"
OUTCOME_DENIED = "denied"
OUTCOME_EXPIRED = "expired"
OUTCOME_FAILED = "failed"

# GitHub asks for at least five seconds between polls and says so in the
# response. Used when it does not.
DEFAULT_INTERVAL_SECONDS = 5
# A ceiling on how long the dialog waits, whatever GitHub says the code is good
# for. Fifteen minutes is already longer than anybody stares at a dialog.
MAX_WAIT_SECONDS = 900


@dataclass(frozen=True, slots=True)
class DeviceLogin:
    """
    What GitHub hands back when a sign-in is started.

    Attributes:
        user_code: The code the user types into the browser.
        verification_url: The page to type it into.
        device_code: The identifier Branchly polls with. Not shown to anyone.
        interval: Seconds to wait between polls.
        expires_in: Seconds the code stays valid.
        error_key: Translation key when the sign-in could not be started.
    """

    user_code: str = ""
    verification_url: str = GITHUB_DEVICE_VERIFICATION_URL
    device_code: str = ""
    interval: int = DEFAULT_INTERVAL_SECONDS
    expires_in: int = 0
    error_key: str = ""

    @property
    def ok(self) -> bool:
        """
        Reports whether a sign-in was started.

        Returns:
            bool: True when there is a code to show the user.
        """

        return bool(self.user_code and self.device_code and not self.error_key)


@dataclass(frozen=True, slots=True)
class PollResult:
    """
    The answer to one poll.

    Attributes:
        outcome: One of the ``OUTCOME_*`` constants.
        token: The access token, only with ``OUTCOME_TOKEN``.
        interval: Seconds to wait before polling again.
        error_key: Translation key for a failure worth showing.
    """

    outcome: str
    token: str = ""
    interval: int = DEFAULT_INTERVAL_SECONDS
    error_key: str = ""


def client_id() -> str:
    """
    Returns the OAuth app to sign in with.

    The environment wins over the constant, so a build can be pointed at another
    app without editing code.

    Returns:
        str: The client id, empty when none is configured.
    """

    from_environment = os.environ.get(CLIENT_ID_VARIABLE, "").strip()
    return from_environment or GITHUB_OAUTH_CLIENT_ID.strip()


def is_configured() -> bool:
    """
    Reports whether browser sign-in is available at all.

    Returns:
        bool: True when an OAuth app is configured.
    """

    return bool(client_id())


def _post(url: str, body: dict[str, str]) -> dict[str, object] | None:
    """
    Sends one form post and reads the JSON answer.

    Args:
        url: Endpoint to post to.
        body: Form fields.

    Returns:
        dict[str, object] | None: The parsed answer, or None when the request or
            the parsing failed.
    """

    try:
        response = requests.post(
            url,
            data=body,
            headers={"Accept": "application/json", "User-Agent": GITHUB_USER_AGENT},
            timeout=GITHUB_OAUTH_TIMEOUT,
        )
    except requests.RequestException:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def start(scopes: tuple[str, ...] = GITHUB_OAUTH_SCOPES) -> DeviceLogin:
    """
    Asks GitHub for a code the user can type into their browser.

    Args:
        scopes: Permissions to ask for.

    Returns:
        DeviceLogin: The code and where to type it, or a reason it failed.
    """

    identifier = client_id()
    if not identifier:
        return DeviceLogin(error_key="signin.not_configured")

    payload = _post(
        GITHUB_DEVICE_CODE_URL, {"client_id": identifier, "scope": " ".join(scopes)}
    )
    if payload is None:
        return DeviceLogin(error_key="sync.offline")
    if payload.get("error"):
        return DeviceLogin(error_key="signin.failed")

    user_code = str(payload.get("user_code") or "")
    device_code = str(payload.get("device_code") or "")
    if not user_code or not device_code:
        return DeviceLogin(error_key="signin.failed")

    return DeviceLogin(
        user_code=user_code,
        verification_url=str(payload.get("verification_uri") or GITHUB_DEVICE_VERIFICATION_URL),
        device_code=device_code,
        interval=_positive(payload.get("interval"), DEFAULT_INTERVAL_SECONDS),
        expires_in=_positive(payload.get("expires_in"), MAX_WAIT_SECONDS),
    )


def poll(device_code: str, interval: int = DEFAULT_INTERVAL_SECONDS) -> PollResult:
    """
    Asks once whether the user has finished in the browser.

    Args:
        device_code: Identifier from ``start``.
        interval: Seconds waited before this poll, used as the default for the
            next one.

    Returns:
        PollResult: What GitHub said.
    """

    identifier = client_id()
    if not identifier or not device_code:
        return PollResult(OUTCOME_FAILED, error_key="signin.not_configured")

    payload = _post(
        GITHUB_ACCESS_TOKEN_URL,
        {
            "client_id": identifier,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
    )
    if payload is None:
        # A dropped request in the middle of a wait is not a failed sign-in.
        # Keep waiting; the overall deadline ends it if the network stays down.
        return PollResult(OUTCOME_PENDING, interval=interval)

    token = str(payload.get("access_token") or "")
    if token:
        return PollResult(OUTCOME_TOKEN, token=token, interval=interval)

    error = str(payload.get("error") or "")
    if error == "authorization_pending":
        return PollResult(OUTCOME_PENDING, interval=interval)
    if error == "slow_down":
        # GitHub adds five seconds to the interval it wants. Respect its own
        # number when it sends one.
        return PollResult(
            OUTCOME_SLOW_DOWN, interval=_positive(payload.get("interval"), interval + 5)
        )
    if error == "access_denied":
        return PollResult(OUTCOME_DENIED, error_key="signin.denied")
    if error == "expired_token":
        return PollResult(OUTCOME_EXPIRED, error_key="signin.expired")
    return PollResult(OUTCOME_FAILED, error_key="signin.failed")


def wait_for_token(
    login: DeviceLogin,
    should_stop=None,  # noqa: ANN001 - a plain callable, typed in the docstring
    sleep=time.sleep,  # noqa: ANN001 - injected so tests need not wait
) -> PollResult:
    """
    Polls until the user agrees, refuses, or the code runs out.

    Args:
        login: What ``start`` returned.
        should_stop: Optional callable returning True when the caller wants to
            give up, checked between polls so a cancelled dialog stops quickly.
        sleep: Sleep function, injected so tests do not wait in real time.

    Returns:
        PollResult: The final answer.
    """

    if not login.ok:
        return PollResult(OUTCOME_FAILED, error_key=login.error_key or "signin.failed")

    interval = max(1, login.interval)
    deadline = time.monotonic() + min(max(1, login.expires_in), MAX_WAIT_SECONDS)
    while time.monotonic() < deadline:
        if should_stop is not None and should_stop():
            return PollResult(OUTCOME_DENIED, error_key="")
        sleep(interval)
        if should_stop is not None and should_stop():
            return PollResult(OUTCOME_DENIED, error_key="")
        answer = poll(login.device_code, interval)
        if answer.outcome not in {OUTCOME_PENDING, OUTCOME_SLOW_DOWN}:
            return answer
        interval = max(1, answer.interval)
    return PollResult(OUTCOME_EXPIRED, error_key="signin.expired")


def _positive(value: object, fallback: int) -> int:
    """
    Reads a positive whole number out of an untrusted payload.

    Args:
        value: What the server sent.
        fallback: What to use when it is not a usable number.

    Returns:
        int: A positive integer.
    """

    if isinstance(value, bool):
        return fallback
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return fallback
        if parsed > 0:
            return parsed
    return fallback
