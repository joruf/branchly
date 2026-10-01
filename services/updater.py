"""
Asking GitHub whether a newer Branchly exists, fetching it, restarting.

For a checkout, "newer" means the head commit of the default branch: the API
reports it, and it is compared with the commit this installation sits on.

Two ways to apply an update, picked automatically:

* **git** — ``git pull --ff-only`` when Branchly runs from a checkout, which is
  how it is installed. It refuses to run over a dirty tree or over local commits,
  which is exactly the wanted behaviour: an update must never silently throw away
  somebody's work.
* **archive** — otherwise the branch ZIP is unpacked over the installation. Only
  files the archive contains are replaced; the virtualenv, the settings and the
  repository registry live elsewhere and are never touched.

The single-file executable (``build-exe.py``) has neither sources to pull nor a
commit to compare, so it asks for the latest GitHub *release* instead: the
workflow ``release-exe.yml`` publishes one per build number, tagged
``v<version>-build<build>``, with one executable per platform. A higher build
number is an update; the matching file is saved next to the running one under
its own, versioned name and takes its place after the restart.

Nothing here runs by itself: :func:`check` only looks, :func:`apply` only acts
when the caller says so, and :func:`restart` only when the window is already gone.

The update is deliberately *not* routed through the ``github_api`` client. That
client is about a user's token, their pull requests and an ETag cache; this asks
one anonymous question about one public repository and should not be able to spend
the user's rate limit or see their token.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

import paths
import version as _version
from constants import (
    APP_BUILD,
    APP_URL,
    APP_VERSION,
    GIT_TIMEOUT_LOCAL,
    GIT_TIMEOUT_NETWORK,
    GITHUB_API_ROOT,
    GITHUB_USER_AGENT,
    REEXEC_MARKER,
    UPDATE_BRANCH,
    UPDATE_DOWNLOAD_TIMEOUT,
    UPDATE_MAX_ARCHIVE_BYTES,
    UPDATE_MAX_CHANGES,
    UPDATE_MAX_EXECUTABLE_BYTES,
    UPDATE_TIMEOUT,
)
from gitops import runner

ERROR_NOT_GITHUB = "update.not_github"
ERROR_OFFLINE = "sync.offline"
ERROR_CHECK_FAILED = "update.check_failed"
ERROR_DIRTY = "update.blocked_dirty"
ERROR_PULL_FAILED = "update.pull_failed"
ERROR_DOWNLOAD_FAILED = "update.download_failed"
ERROR_ARCHIVE_BROKEN = "update.archive_broken"
ERROR_WRITE_FAILED = "update.write_failed"

METHOD_GIT = "git"
METHOD_ARCHIVE = "archive"
METHOD_EXECUTABLE = "executable"

#: Release tags of the executables, as ``release-exe.yml`` writes them.
RELEASE_TAG = re.compile(r"^v(?P<version>[0-9][0-9.]*)-build(?P<build>[0-9]+)$")
# Key in the update state naming the executable an update replaced, until it is
# removed by the program that replaced it.
_REPLACED_KEY = "replaced_executable"
# Anything smaller than this is an error page, not the program.
_MIN_EXECUTABLE_BYTES = 1024 * 1024
# The executable :func:`apply` just downloaded; the restart starts it.
_updated_executable: Path | None = None

# Never overwritten by an archive update: git's own data, the virtualenv, and the
# runtime state a portable installation may keep next to the code.
_KEEP: frozenset[str] = frozenset({".git", ".venv", "settings.json", "repos.json"})

# Commit hashes are shown, not compared, in this form. Ten characters is what
# GitHub itself shows and stays unambiguous for a project this size.
_SHORT_HASH = 10

_HEADERS = {
    "User-Agent": GITHUB_USER_AGENT,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """
    The outcome of one look at the repository.

    Attributes:
        available: Whether the branch is ahead of this installation.
        local: Commit this installation sits on, shortened; empty when unknown.
        remote: Commit the branch points at, shortened; empty when unreachable.
        summary: Subject line of the remote commit.
        count: How many commits the branch is ahead, 0 when unknown.
        changes: Commit subjects between the two, newest first and capped at
            :data:`constants.UPDATE_MAX_CHANGES`. Empty when the comparison could
            not be made.
        error_key: Translation key describing why the check failed, empty on
            success.
        detail: Technical detail behind ``error_key``, for the details pane.
    """

    available: bool = False
    local: str = ""
    remote: str = ""
    summary: str = ""
    count: int = 0
    changes: tuple[str, ...] = ()
    error_key: str = ""
    detail: str = ""

    @property
    def known(self) -> bool:
        """
        Reports whether the check produced a usable answer.

        Returns:
            bool: True when there was no error and a remote commit came back.
        """

        return not self.error_key and bool(self.remote)


@dataclass(frozen=True, slots=True)
class _Comparison:
    """
    What the compare endpoint said about two commits.

    Attributes:
        known: Whether the comparison could be made at all. False for a 404, which
            is the normal answer when the local commit was never pushed.
        count: How many commits the branch is ahead of the installation.
        subjects: Commit subjects, newest first and capped.
    """

    known: bool = False
    count: int = 0
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateOutcome:
    """
    The outcome of one attempt to install an update.

    Attributes:
        ok: Whether the new version is now on disk.
        method: ``git`` or ``archive``, whichever was used.
        error_key: Translation key describing the failure, empty on success.
        detail: Technical detail, which for the git path is git's own output.
        files: Number of files written by the archive path.
    """

    ok: bool = False
    method: str = ""
    error_key: str = ""
    detail: str = ""
    files: int = 0


def repository_slug(url: str = APP_URL) -> str:
    """
    Extracts ``owner/name`` from a GitHub repository URL.

    Args:
        url: Repository URL.

    Returns:
        str: The slug, or an empty string when the URL is not a GitHub one.
    """

    marker = "github.com/"
    if not isinstance(url, str) or marker not in url:
        return ""
    slug = url.split(marker, 1)[1]
    if slug.endswith(".git"):
        slug = slug[: -len(".git")]
    return slug.strip("/")


def installation_root() -> Path:
    """
    Returns the directory an update would replace.

    Returns:
        Path: The Branchly source tree.
    """

    return paths.project_root()


def is_git_checkout(root: Path | None = None) -> bool:
    """
    Reports whether the installation can be updated through git.

    Args:
        root: Installation directory. Defaults to the source tree.

    Returns:
        bool: True when there is a ``.git`` and a git executable to use it with.
    """

    target = root or installation_root()
    return (target / ".git").exists() and runner.git_executable() is not None


def local_commit(root: Path | None = None) -> str:
    """
    Reads the commit the installation sits on.

    Args:
        root: Installation directory. Defaults to the source tree.

    Returns:
        str: Full commit hash, or an empty string when it cannot be determined.
    """

    target = root or installation_root()
    if not is_git_checkout(target):
        return ""
    result = runner.run(["rev-parse", "HEAD"], cwd=target, timeout=GIT_TIMEOUT_LOCAL, read_only=True)
    return result.stdout.strip() if result.ok else ""


def installed_revision() -> str:
    """
    Names what this installation is, in the form a check reports it.

    Returns:
        str: The short commit of a checkout, or ``version (build)`` of the
            single-file executable; empty when it cannot be determined.
    """

    if paths.IS_FROZEN:
        return f"{APP_VERSION} ({APP_BUILD})"
    return local_commit()[:_SHORT_HASH]


def check(root: Path | None = None) -> UpdateInfo:
    """
    Asks GitHub whether the branch is ahead of this installation.

    Args:
        root: Installation directory. Defaults to the source tree.

    Returns:
        UpdateInfo: What was found. Never raises — a failed check is an answer,
            not an exception, because it happens on every offline start.
    """

    if paths.IS_FROZEN:
        return _check_release()
    slug = repository_slug()
    if not slug:
        return UpdateInfo(error_key=ERROR_NOT_GITHUB, detail=APP_URL)

    url = f"{GITHUB_API_ROOT}/repos/{slug}/commits/{UPDATE_BRANCH}"
    try:
        response = requests.get(url, headers=_HEADERS, timeout=UPDATE_TIMEOUT)
    except requests.RequestException as error:
        return UpdateInfo(error_key=ERROR_OFFLINE, detail=str(error))

    if response.status_code != 200:
        return UpdateInfo(error_key=ERROR_CHECK_FAILED, detail=f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        return UpdateInfo(error_key=ERROR_CHECK_FAILED, detail=str(error))
    if not isinstance(payload, dict):
        return UpdateInfo(error_key=ERROR_CHECK_FAILED, detail="unexpected answer")

    remote = payload.get("sha")
    if not isinstance(remote, str) or not remote:
        return UpdateInfo(error_key=ERROR_CHECK_FAILED, detail="the answer held no commit")

    local = local_commit(root)
    # Without a local commit there is nothing to compare, so do not claim an
    # update: offering one would mean replacing an installation blind.
    differs = bool(local) and local != remote
    comparison = _changes_between(slug, local, remote) if differs else _Comparison()
    return UpdateInfo(
        available=differs and _is_behind(local, remote, comparison, root),
        local=local[:_SHORT_HASH],
        remote=remote[:_SHORT_HASH],
        summary=_summary_of(payload),
        count=comparison.count,
        changes=comparison.subjects,
    )


def _is_behind(local: str, remote: str, comparison: _Comparison, root: Path | None) -> bool:
    """
    Decides whether the branch really has something this installation lacks.

    "Different commit" is not the same as "newer commit". Anyone working on
    Branchly itself sits on an unpushed commit of their own, and announcing an
    update to them would be a phantom: the pull would have nothing to fast-forward.

    Args:
        local: Commit the installation sits on.
        remote: Commit the branch points at.
        comparison: What the compare endpoint said, if anything.
        root: Installation directory.

    Returns:
        bool: True when the branch is genuinely ahead.
    """

    if comparison.known:
        # The authoritative answer, straight from the repository.
        return comparison.count > 0
    # No comparison to be had — GitHub does not know a commit that was never
    # pushed. Git can still answer it when the remote commit was fetched once.
    if _already_contains(remote, root):
        return False
    return bool(local)


def _already_contains(commit: str, root: Path | None) -> bool:
    """
    Reports whether HEAD already contains a commit.

    Args:
        commit: Commit to look for.
        root: Installation directory.

    Returns:
        bool: True only when the answer is certain. A commit whose object is not
            present locally cannot be judged, and that is reported as False rather
            than guessed at.
    """

    target = root or installation_root()
    if not commit or not is_git_checkout(target):
        return False
    present = runner.run(
        ["cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=target,
        timeout=GIT_TIMEOUT_LOCAL,
        read_only=True,
    )
    if present.failed:
        return False
    contained = runner.run(
        ["merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=target,
        timeout=GIT_TIMEOUT_LOCAL,
        read_only=True,
    )
    return contained.ok


def _changes_between(slug: str, local: str, remote: str) -> _Comparison:
    """
    Collects the commit subjects between the installation and the branch.

    One commit subject answers "what is new" badly when twelve commits landed: the
    user sees the last one and never learns about the rest.

    Args:
        slug: ``owner/name`` of the repository.
        local: Commit the installation sits on.
        remote: Commit the branch points at.

    Returns:
        _Comparison: The subjects and the count, or an unknown comparison when the
            endpoint could not answer.
    """

    url = f"{GITHUB_API_ROOT}/repos/{slug}/compare/{local}...{remote}"
    try:
        response = requests.get(url, headers=_HEADERS, timeout=UPDATE_TIMEOUT)
    except requests.RequestException:
        return _Comparison()
    if response.status_code != 200:
        return _Comparison()
    try:
        payload = response.json()
    except ValueError:
        return _Comparison()
    if not isinstance(payload, dict):
        return _Comparison()

    commits = payload.get("commits")
    entries = commits if isinstance(commits, list) else []
    subjects: list[str] = []
    for entry in reversed(entries):
        if not isinstance(entry, dict):
            continue
        subject = _summary_of(entry)
        if subject:
            subjects.append(subject)
        if len(subjects) >= UPDATE_MAX_CHANGES:
            break

    ahead = payload.get("ahead_by")
    if isinstance(ahead, int) and not isinstance(ahead, bool) and ahead >= 0:
        return _Comparison(known=True, count=ahead, subjects=tuple(subjects))
    # No usable ahead_by: fall back to what came in the list rather than treating
    # the whole answer as missing.
    return _Comparison(known=bool(entries), count=len(subjects), subjects=tuple(subjects))


def _summary_of(payload: dict) -> str:
    """
    Pulls the subject line out of a commit payload.

    Args:
        payload: Parsed API answer.

    Returns:
        str: First line of the commit message, empty when there is none.
    """

    commit = payload.get("commit")
    if not isinstance(commit, dict):
        return ""
    message = commit.get("message")
    if not isinstance(message, str) or not message.strip():
        return ""
    return message.strip().splitlines()[0]


def _fetch_release() -> tuple[dict, str, str]:
    """
    Asks GitHub for the newest executable release.

    Returns:
        tuple[dict, str, str]: The release, plus a translation key and detail
            when it could not be fetched; the release is empty then.
    """

    slug = repository_slug()
    if not slug:
        return {}, ERROR_NOT_GITHUB, APP_URL
    url = f"{GITHUB_API_ROOT}/repos/{slug}/releases/latest"
    try:
        response = requests.get(url, headers=_HEADERS, timeout=UPDATE_TIMEOUT)
    except requests.RequestException as error:
        return {}, ERROR_OFFLINE, str(error)
    if response.status_code != 200:
        return {}, ERROR_CHECK_FAILED, f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError as error:
        return {}, ERROR_CHECK_FAILED, str(error)
    if not isinstance(payload, dict):
        return {}, ERROR_CHECK_FAILED, "unexpected answer"
    return payload, "", ""


def release_build(tag: str) -> int | None:
    """
    Reads the build number from a release tag.

    Args:
        tag: For example ``v0.10.2-build31``.

    Returns:
        int | None: For example ``31``; None for a tag that is not an executable build.
    """

    match = RELEASE_TAG.match(tag.strip())
    return int(match.group("build")) if match else None


def release_asset_name(release: dict) -> str:
    """
    Returns the file name this platform's executable has in a release.

    Args:
        release: The release as the GitHub API returns it.

    Returns:
        str: For example ``branchly-linux-x86_64-0.10.2-build31``; empty for a
            release that is not an executable build.
    """

    match = RELEASE_TAG.match(str(release.get("tag_name") or "").strip())
    if not match:
        return ""
    return paths.executable_name(match.group("version"), int(match.group("build")))


def release_asset_url(release: dict) -> str:
    """
    Returns the download URL of this platform's executable in a release.

    Args:
        release: The release as the GitHub API returns it.

    Returns:
        str: The URL, or an empty string when the release has none for this platform.
    """

    wanted = release_asset_name(release)
    if not wanted:
        return ""
    for asset in release.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == wanted:
            return str(asset.get("browser_download_url") or "")
    return ""


def _check_release() -> UpdateInfo:
    """
    Compares the newest release with the build of this executable.

    Returns:
        UpdateInfo: What was found; never raises.
    """

    release, error_key, detail = _fetch_release()
    if error_key:
        return UpdateInfo(error_key=error_key, detail=detail)
    tag = str(release.get("tag_name") or "").strip()
    match = RELEASE_TAG.match(tag)
    if match is None:
        return UpdateInfo(
            error_key=ERROR_CHECK_FAILED, detail=f"the latest release {tag!r} is not an executable build"
        )
    if not release_asset_url(release):
        return UpdateInfo(
            error_key=ERROR_CHECK_FAILED, detail=f"the release {tag} has no {release_asset_name(release)}"
        )
    build = int(match.group("build"))
    available = build > APP_BUILD
    # The commits in between, for "what is new": the release names the commit it
    # was built from, and the bundled VERSION file the one this file was.
    local = _version.current().commit
    remote = str(release.get("target_commitish") or "")
    known = available and local and remote
    comparison = _changes_between(repository_slug(), local, remote) if known else _Comparison()
    return UpdateInfo(
        available=available,
        local=installed_revision(),
        remote=f"{match.group('version')} ({build})",
        summary=comparison.subjects[0] if comparison.subjects else str(release.get("name") or ""),
        count=comparison.count,
        changes=comparison.subjects,
    )


def due(hours: int, last_checked: float, now: float | None = None) -> bool:
    """
    Decides whether the startup check should contact GitHub again.

    Args:
        hours: Minimum age of the last check before asking again. Zero or less
            checks on every start.
        last_checked: Epoch timestamp of the last check, 0 for never.
        now: Current time, for tests.

    Returns:
        bool: True when a check should run now.
    """

    if hours <= 0:
        return True
    if not last_checked or last_checked <= 0:
        return True
    moment = time.time() if now is None else now
    # A clock that jumped backwards, or a hand-edited timestamp from the future,
    # must not suppress every future check.
    if last_checked > moment:
        return True
    return (moment - last_checked) >= hours * 3600


def apply(root: Path | None = None) -> UpdateOutcome:
    """
    Fetches the new version into the installation.

    Args:
        root: Installation directory. Defaults to the source tree.

    Returns:
        UpdateOutcome: What happened, with a translation key when it failed.
    """

    if paths.IS_FROZEN:
        return _apply_release()
    target = root or installation_root()
    if is_git_checkout(target):
        return _apply_git(target)
    return _apply_archive(target)


def _apply_git(root: Path) -> UpdateOutcome:
    """
    Updates a checkout, refusing to touch local work.

    Args:
        root: The working tree.

    Returns:
        UpdateOutcome: What happened.
    """

    status = runner.run(["status", "--porcelain"], cwd=root, timeout=GIT_TIMEOUT_LOCAL, read_only=True)
    if status.failed:
        return UpdateOutcome(
            method=METHOD_GIT, error_key=ERROR_PULL_FAILED, detail=status.stderr.strip()
        )
    if status.stdout.strip():
        # Uncommitted work in Branchly's own folder: whoever is editing it gets to
        # decide what happens to those changes, not the updater.
        return UpdateOutcome(method=METHOD_GIT, error_key=ERROR_DIRTY, detail=status.stdout.strip())

    pulled = runner.run(["pull", "--ff-only"], cwd=root, timeout=GIT_TIMEOUT_NETWORK)
    if pulled.failed:
        detail = pulled.stderr.strip() or pulled.stdout.strip()
        return UpdateOutcome(method=METHOD_GIT, error_key=ERROR_PULL_FAILED, detail=detail)
    return UpdateOutcome(ok=True, method=METHOD_GIT, detail=pulled.stdout.strip())


def _apply_archive(root: Path) -> UpdateOutcome:
    """
    Downloads the branch archive and unpacks it over the installation.

    Args:
        root: The installation directory.

    Returns:
        UpdateOutcome: What happened.
    """

    slug = repository_slug()
    if not slug:
        return UpdateOutcome(method=METHOD_ARCHIVE, error_key=ERROR_NOT_GITHUB, detail=APP_URL)
    url = f"https://github.com/{slug}/archive/refs/heads/{UPDATE_BRANCH}.zip"

    with tempfile.TemporaryDirectory(prefix="branchly-update-") as work:
        workspace = Path(work)
        archive = workspace / "update.zip"
        failure = _download(url, archive)
        if failure is not None:
            return failure

        unpacked = workspace / "unpacked"
        try:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(unpacked)
        except (zipfile.BadZipFile, OSError, ValueError) as error:
            return UpdateOutcome(
                method=METHOD_ARCHIVE, error_key=ERROR_ARCHIVE_BROKEN, detail=str(error)
            )

        # GitHub wraps the tree in a single "<name>-<branch>" folder.
        folders = [item for item in unpacked.iterdir() if item.is_dir()]
        if len(folders) != 1:
            return UpdateOutcome(
                method=METHOD_ARCHIVE,
                error_key=ERROR_ARCHIVE_BROKEN,
                detail="unexpected archive layout",
            )
        try:
            written = _copy_tree(folders[0], root)
        except OSError as error:
            return UpdateOutcome(
                method=METHOD_ARCHIVE, error_key=ERROR_WRITE_FAILED, detail=str(error)
            )

    # The archive carries no history and no VERSION file, so the one left over
    # from before now names a version this installation no longer is. An
    # invented number is worse than none: without it the About box says
    # "unknown", which is true.
    try:
        (root / "VERSION").unlink(missing_ok=True)
    except OSError:
        pass
    return UpdateOutcome(ok=True, method=METHOD_ARCHIVE, files=written)


def _download(
    url: str, destination: Path, method: str = METHOD_ARCHIVE, limit: int | None = None
) -> UpdateOutcome | None:
    """
    Streams the archive to disk, with a ceiling on its size.

    Args:
        url: Archive URL.
        destination: File to write.
        method: Reported in a failed outcome.
        limit: Largest size accepted, in bytes; the archive ceiling when None.

    Returns:
        UpdateOutcome | None: A failed outcome, or None when the file is there.
    """

    ceiling = UPDATE_MAX_ARCHIVE_BYTES if limit is None else limit
    try:
        with requests.get(
            url, headers={"User-Agent": GITHUB_USER_AGENT}, timeout=UPDATE_DOWNLOAD_TIMEOUT, stream=True
        ) as response:
            if response.status_code != 200:
                return UpdateOutcome(
                    method=method,
                    error_key=ERROR_DOWNLOAD_FAILED,
                    detail=f"HTTP {response.status_code}",
                )
            size = 0
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > ceiling:
                        return UpdateOutcome(
                            method=method,
                            error_key=ERROR_DOWNLOAD_FAILED,
                            detail="the download exceeded the size limit",
                        )
                    handle.write(chunk)
    except requests.RequestException as error:
        return UpdateOutcome(method=method, error_key=ERROR_OFFLINE, detail=str(error))
    except OSError as error:
        return UpdateOutcome(method=method, error_key=ERROR_WRITE_FAILED, detail=str(error))
    return None


def _copy_tree(source: Path, target: Path) -> int:
    """
    Copies the archive contents over the installation.

    Args:
        source: The unpacked archive root.
        target: The installation directory.

    Returns:
        int: How many files were written.

    Raises:
        OSError: When a file cannot be written, e.g. a read-only installation.
    """

    root = target.resolve()
    written = 0
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if relative.parts and relative.parts[0] in _KEEP:
            continue
        destination = target / relative
        # Belt and braces on top of what zipfile already sanitizes: nothing an
        # archive names may land outside the installation.
        try:
            resolved = destination.resolve()
        except OSError:
            continue
        if resolved != root and root not in resolved.parents:
            continue
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        written += 1
    return written


def _apply_release() -> UpdateOutcome:
    """
    Downloads the newest executable next to the running one.

    The file name carries the version, so the new one gets its own name instead
    of overwriting this one, which Windows would refuse for a running ``.exe``
    anyway. The old file is removed, and the menu entry pointed at the new one,
    by :func:`finish_executable_update` in the new program.

    Returns:
        UpdateOutcome: What happened.
    """

    global _updated_executable
    release, error_key, detail = _fetch_release()
    if error_key:
        return UpdateOutcome(method=METHOD_EXECUTABLE, error_key=error_key, detail=detail)
    url = release_asset_url(release)
    if not url:
        missing = release_asset_name(release) or "executable"
        return UpdateOutcome(
            method=METHOD_EXECUTABLE,
            error_key=ERROR_DOWNLOAD_FAILED,
            detail=f"the latest release has no {missing}",
        )

    current = paths.executable()
    target = current.with_name(release_asset_name(release))
    if target == current:
        return UpdateOutcome(
            method=METHOD_EXECUTABLE,
            error_key=ERROR_DOWNLOAD_FAILED,
            detail=f"{current.name} is already this version",
        )
    partial = target.with_name(target.name + ".part")
    failure = _download(url, partial, METHOD_EXECUTABLE, UPDATE_MAX_EXECUTABLE_BYTES)
    if failure is None:
        try:
            if partial.stat().st_size < _MIN_EXECUTABLE_BYTES:
                failure = UpdateOutcome(
                    method=METHOD_EXECUTABLE,
                    error_key=ERROR_DOWNLOAD_FAILED,
                    detail="the downloaded file is too small to be the program",
                )
            else:
                if not paths.is_windows():
                    partial.chmod(0o755)
                os.replace(partial, target)
        except OSError as error:
            failure = UpdateOutcome(method=METHOD_EXECUTABLE, error_key=ERROR_WRITE_FAILED, detail=str(error))
    if failure is not None:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        return failure

    state = _load_update_state()
    state[_REPLACED_KEY] = str(current)
    _save_update_state(state)
    _updated_executable = target
    return UpdateOutcome(ok=True, method=METHOD_EXECUTABLE, detail=target.name, files=1)


def finish_executable_update() -> None:
    """
    Completes an update in the program it installed.

    Points the application menu entry at this file when it named the previous
    one, then removes the previous file. While that one is still running (the
    Windows restart overlaps for a moment), its removal waits for the next start.

    Returns:
        None
    """

    state = _load_update_state()
    previous = state.get(_REPLACED_KEY)
    if not isinstance(previous, str) or not previous:
        return
    old = Path(previous)
    if old == paths.executable():
        # The old file was started again; it must never delete itself.
        return

    from install_dependencies import desktop_entry_path, install_desktop_entry

    try:
        entry = desktop_entry_path().read_text(encoding="utf-8")
    except OSError:
        entry = ""
    if str(old) in entry:
        install_desktop_entry()

    try:
        old.unlink(missing_ok=True)
    except OSError:
        return
    state.pop(_REPLACED_KEY, None)
    _save_update_state(state)


def _load_update_state() -> dict:
    """
    Reads what the executable's updater remembers between two starts.

    Returns:
        dict: The stored values, empty when there are none.
    """

    try:
        stored = json.loads(paths.update_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _save_update_state(state: dict) -> None:
    """
    Stores what the executable's updater remembers, never failing the caller.

    Args:
        state: The values to keep.

    Returns:
        None
    """

    target = paths.update_state_path()
    if not paths.ensure_dir(target.parent):
        return
    try:
        target.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def restart_command() -> list[str]:
    """
    Builds the command that starts Branchly again.

    Returns:
        list[str]: Interpreter and entry point, or the executable on its own.
    """

    if paths.IS_FROZEN:
        return [str(_updated_executable or paths.executable())]
    interpreter = paths.venv_python_path(gui=True)
    if not interpreter.exists():
        interpreter = Path(sys.executable)
    return [str(interpreter), str(installation_root() / "run.py")]


def restart() -> None:
    """
    Replaces the running program with a fresh one.

    Call this only once the window is gone and the settings are written: on POSIX
    the process is replaced outright and never comes back here.

    Returns:
        None
    """

    command = restart_command()
    # Without the executable's library paths: execve bypasses the cleaning every
    # subprocess gets (see paths.use_system_environment_for_children).
    environment = paths.child_environment()
    # The child must not inherit "already re-executed" from this process, or it
    # would stay on whatever interpreter this one happens to be running.
    environment.pop(REEXEC_MARKER, None)
    if paths.IS_FROZEN:
        # Without it the new executable would reuse this one's unpacked files,
        # which are deleted the moment this process ends, and run the old code.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if paths.is_windows():
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            command, env=environment, close_fds=True, shell=False, creationflags=creation_flags
        )
        return
    os.execve(command[0], command, environment)
