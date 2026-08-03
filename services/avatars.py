"""
Author avatars.

Purely cosmetic, so the rules are: never block the UI, never retry hard, and
never let a failure surface as an error. A missing picture is a missing picture.

Files are cached on disk under a hash of the URL, so restarting the application
does not re-download every face.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import requests

import paths
from constants import GITHUB_USER_AGENT

REQUEST_TIMEOUT = 10
MAX_BYTES = 512_000

_lock = threading.RLock()
_failed: set[str] = set()


def cache_path(url: str) -> Path:
    """
    Returns where an avatar is cached.

    Args:
        url: Avatar URL.

    Returns:
        Path: File path inside the avatar cache directory.
    """

    digest = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:32]
    return paths.avatar_cache_dir() / f"{digest}.img"


def cached_bytes(url: str) -> bytes | None:
    """
    Reads an avatar from the cache.

    Args:
        url: Avatar URL.

    Returns:
        bytes | None: Cached content, or None when it is not cached.
    """

    if not url:
        return None
    target = cache_path(url)
    try:
        if target.is_file():
            return target.read_bytes()
    except OSError:
        return None
    return None


def fetch(url: str) -> bytes | None:
    """
    Downloads an avatar, using the cache when possible.

    Args:
        url: Avatar URL. Only ``https`` is accepted.

    Returns:
        bytes | None: Image content, or None when unavailable.
    """

    if not url or not url.lower().startswith("https://"):
        return None
    cached = cached_bytes(url)
    if cached is not None:
        return cached
    with _lock:
        if url in _failed:
            return None

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": GITHUB_USER_AGENT},
            stream=True,
        )
        if response.status_code != 200:
            raise requests.RequestException(f"status {response.status_code}")
        payload = response.raw.read(MAX_BYTES + 1, decode_content=True) or b""
    except (requests.RequestException, OSError, ValueError):
        with _lock:
            _failed.add(url)
        return None

    if not payload or len(payload) > MAX_BYTES:
        with _lock:
            _failed.add(url)
        return None

    target = cache_path(url)
    if paths.ensure_dir(target.parent):
        try:
            target.write_bytes(payload)
        except OSError:
            # An unwritable cache is not worth mentioning; the image still shows.
            pass
    return payload


def clear_cache() -> int:
    """
    Deletes every cached avatar.

    Returns:
        int: Number of files removed.
    """

    directory = paths.avatar_cache_dir()
    removed = 0
    try:
        entries = list(directory.glob("*.img"))
    except OSError:
        return 0
    for item in entries:
        try:
            item.unlink()
            removed += 1
        except OSError:
            continue
    with _lock:
        _failed.clear()
    return removed
