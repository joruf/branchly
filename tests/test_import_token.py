"""
Tests for the token import script.

The script reads a file that may hold secrets for a dozen services, so the only
thing worth testing hard is that it picks the right one. A file with an
``ssh.server.password`` above a ``github.token`` is the normal case, and taking
the first ``password`` it sees would store a server password as a GitHub token.

Nothing here decrypts anything: the decryption is one subprocess call, the
choosing is the part that can be subtly wrong.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from import_token import extract, field_names

TOKEN = "ghp_" + "a" * 36
OTHER = "github_pat_" + "b" * 22

NESTED = (
    "ssh:\n"
    "  mein-server:\n"
    "    user: root\n"
    "    password: serversecret\n"
    "github:\n"
    "  url: https://github.com/joruf\n"
    "  name: Joachim\n"
    f"  token: {TOKEN}\n"
    "unity:\n"
    "  password: unitysecret\n"
)


class FieldNameTests(unittest.TestCase):
    """
    Listing the fields, which is how a user finds the right ``--key``.
    """

    def test_paths_are_reported_with_their_parents(self) -> None:
        names = field_names(NESTED)
        self.assertIn("ssh.mein-server.password", names)
        self.assertIn("github.token", names)
        self.assertIn("unity.password", names)

    def test_a_flat_file_reports_plain_names(self) -> None:
        self.assertEqual(["a", "b"], field_names("a: 1\nb: 2\n"))

    def test_nothing_usable_reports_nothing(self) -> None:
        self.assertEqual([], field_names("just a sentence\n"))


class ExtractionTests(unittest.TestCase):
    """
    Picking one value out of a file holding many.
    """

    def test_a_dotted_path_wins_over_position(self) -> None:
        self.assertEqual(TOKEN, extract(NESTED, "github.token"))

    def test_a_bare_name_still_finds_the_nested_field(self) -> None:
        self.assertEqual(TOKEN, extract(NESTED, "token"))

    def test_a_bare_name_takes_the_first_match_as_written(self) -> None:
        # This is why the dotted form exists: "password" alone is ambiguous in a
        # file like this, and the first one is the server's.
        self.assertEqual("serversecret", extract(NESTED, "password"))

    def test_without_a_key_the_github_field_is_found(self) -> None:
        self.assertEqual(TOKEN, extract(NESTED))

    def test_a_github_password_is_preferred_over_an_unrelated_one(self) -> None:
        document = (
            "database:\n"
            "  password: dbsecret\n"
            "github:\n"
            f"  password: {OTHER}\n"
        )
        self.assertEqual(OTHER, extract(document))

    def test_a_loose_token_is_the_last_resort(self) -> None:
        self.assertEqual(TOKEN, extract(f"notes: the token is {TOKEN} somewhere\n"))

    def test_a_labelled_value_beats_a_loose_one(self) -> None:
        document = f"github_token: {TOKEN}\nnotes: an old one was {OTHER}\n"
        self.assertEqual(TOKEN, extract(document))

    def test_an_unknown_key_finds_nothing(self) -> None:
        self.assertEqual("", extract(NESTED, "nothing.here"))

    def test_a_file_without_a_token_finds_nothing(self) -> None:
        document = "github:\n  url: https://github.com/joruf\n  email: a@b.invalid\n"
        self.assertEqual("", extract(document))

    def test_quotes_are_stripped(self) -> None:
        self.assertEqual(TOKEN, extract(f'github:\n  token: "{TOKEN}"\n'))


if __name__ == "__main__":
    unittest.main()
