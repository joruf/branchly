"""
Release endpoints: the list, one release, its notes and its attached files.

A release is a tag plus a text plus files, and the three have different rules. A
release can be created for a tag that does not exist yet, in which case GitHub
creates the tag from ``target_commitish`` at publish time; a draft creates no tag
at all until it is published. Deleting a release deliberately does not delete its
tag, because the tag is the part someone else may already have fetched.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from github_api.http import ApiResult, as_model, as_models
from github_api.models import Release, ReleaseAsset

# Assets are read into memory before they are sent, so a ceiling is needed. Half a
# gigabyte is far above any plausible desktop-app artefact and far below anything
# that would put a machine into swap.
MAX_ASSET_BYTES = 512 * 1024 * 1024


class ReleaseEndpoints:
    """
    Reads and changes releases and their assets.
    """

    def releases(self, owner: str, repo: str, limit: int = 0) -> ApiResult:
        """
        Lists releases, newest first, drafts included.

        Args:
            owner: Repository owner.
            repo: Repository name.
            limit: Stop after this many, zero for all of them.

        Returns:
            ApiResult: Payload is a ``list[Release]``.
        """

        return as_models(self.get_all(f"/repos/{owner}/{repo}/releases", limit=limit), Release.from_api)

    def release(self, owner: str, repo: str, release_id: int) -> ApiResult:
        """
        Reads one release.

        Args:
            owner: Repository owner.
            repo: Repository name.
            release_id: Release identifier.

        Returns:
            ApiResult: Payload is a ``Release``.
        """

        return as_model(self.get(f"/repos/{owner}/{repo}/releases/{release_id}"), Release.from_api)

    def create_release(
        self,
        owner: str,
        repo: str,
        tag_name: str,
        name: str = "",
        body: str = "",
        target_commitish: str = "",
        draft: bool = False,
        prerelease: bool = False,
        generate_notes: bool = False,
    ) -> ApiResult:
        """
        Creates a release.

        Args:
            owner: Repository owner.
            repo: Repository name.
            tag_name: Tag to publish. An existing tag is reused, a new one is
                created from ``target_commitish``.
            name: Release title, empty to reuse the tag name.
            body: Release notes.
            target_commitish: Branch or commit the tag is created from, ignored
                when the tag already exists.
            draft: Whether to keep it unpublished. A draft creates no tag.
            prerelease: Whether to mark it as a pre-release.
            generate_notes: Whether GitHub should append its generated changelog
                to the notes.

        Returns:
            ApiResult: Payload is the created ``Release``.
        """

        payload: dict[str, Any] = {
            "tag_name": tag_name,
            "draft": bool(draft),
            "prerelease": bool(prerelease),
            "generate_release_notes": bool(generate_notes),
        }
        if name:
            payload["name"] = name
        if body:
            payload["body"] = body
        if target_commitish:
            payload["target_commitish"] = target_commitish
        return as_model(self.send("POST", f"/repos/{owner}/{repo}/releases", payload), Release.from_api)

    def update_release(self, owner: str, repo: str, release_id: int, **changes: Any) -> ApiResult:
        """
        Changes a release.

        Args:
            owner: Repository owner.
            repo: Repository name.
            release_id: Release identifier.
            **changes: Any of ``tag_name``, ``name``, ``body``,
                ``target_commitish``, ``draft``, ``prerelease``.

        Returns:
            ApiResult: Payload is the updated ``Release``.
        """

        allowed = {"tag_name", "name", "body", "target_commitish", "draft", "prerelease"}
        body = {key: value for key, value in changes.items() if key in allowed}
        if not body:
            return self.release(owner, repo, release_id)
        return as_model(
            self.send("PATCH", f"/repos/{owner}/{repo}/releases/{release_id}", body), Release.from_api
        )

    def delete_release(self, owner: str, repo: str, release_id: int) -> ApiResult:
        """
        Deletes a release.

        The tag survives. Removing it as well is a separate, deliberate call,
        because a tag someone already fetched does not come back by being
        recreated.

        Args:
            owner: Repository owner.
            repo: Repository name.
            release_id: Release identifier.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/releases/{release_id}")

    def generate_notes(
        self, owner: str, repo: str, tag_name: str, previous_tag: str = "", target: str = ""
    ) -> ApiResult:
        """
        Asks GitHub to write the changelog for a tag.

        Args:
            owner: Repository owner.
            repo: Repository name.
            tag_name: Tag the notes are for.
            previous_tag: Tag to compare against, empty to let GitHub pick.
            target: Branch or commit the tag would be created from.

        Returns:
            ApiResult: Payload carries ``name`` and ``body``.
        """

        payload: dict[str, Any] = {"tag_name": tag_name}
        if previous_tag:
            payload["previous_tag_name"] = previous_tag
        if target:
            payload["target_commitish"] = target
        return self.send("POST", f"/repos/{owner}/{repo}/releases/generate-notes", payload)

    # ------------------------------------------------------------------ assets

    def upload_asset(self, release: Release, path: Path, name: str = "") -> ApiResult:
        """
        Attaches a file to a release.

        Args:
            release: The release to attach to, for its upload URL.
            path: File to upload.
            name: Name the asset gets, empty to use the file's own name.

        Returns:
            ApiResult: Payload is the created ``ReleaseAsset``.
        """

        if not release.upload_url:
            return ApiResult(ok=False, error_key="github.validation_failed")
        source = Path(path)
        try:
            size = source.stat().st_size
        except OSError:
            return ApiResult(ok=False, error_key="error.file_unreadable")
        if size > MAX_ASSET_BYTES:
            return ApiResult(ok=False, error_key="github.asset_too_large")
        try:
            payload = source.read_bytes()
        except OSError:
            return ApiResult(ok=False, error_key="error.file_unreadable")

        guessed, _encoding = mimetypes.guess_type(source.name)
        return as_model(
            self.upload(
                release.upload_url,
                name or source.name,
                guessed or "application/octet-stream",
                payload,
            ),
            ReleaseAsset.from_api,
        )

    def delete_asset(self, owner: str, repo: str, asset_id: int) -> ApiResult:
        """
        Removes a file from a release.

        Args:
            owner: Repository owner.
            repo: Repository name.
            asset_id: Asset identifier.

        Returns:
            ApiResult: Success carries no payload.
        """

        return self.send("DELETE", f"/repos/{owner}/{repo}/releases/assets/{asset_id}")
