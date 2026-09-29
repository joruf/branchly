"""
Tests that the manuals stay in step with the program and with each other.

Documentation rots quietly. Nothing crashes, no test goes red, the manual simply
starts describing a program that no longer exists, and nobody notices until
somebody follows it and it does not work. The only defence is to make the ways it
drifts into things a test can see:

* A picture that is referenced but was never generated, or generated and never
  used. The second one means a feature was photographed and never written about.
* A picture from the wrong language, which is how a translated manual ends up
  showing somebody else's program.
* Sections that exist in one language and not in the other, which is the normal
  way a second language falls behind the first.
* A language Branchly speaks with no manual at all.

The checks themselves live in ``scripts/build_manuals.py``, because they have to
be runnable by hand while writing. This only holds them to it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import i18n

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import build_manuals  # noqa: E402


class ManualTests(unittest.TestCase):
    """
    Every manual, against the pictures and against each other.
    """

    def test_every_language_has_a_manual(self) -> None:
        for code, _label in i18n.available_languages():
            with self.subTest(language=code):
                self.assertTrue(build_manuals.manual_path(code).is_file())

    def test_the_manuals_are_in_step(self) -> None:
        # One assertion for the whole set, because the script reports every
        # problem at once and a list of them is more use than the first one.
        problems = build_manuals.check(build_manuals.languages())
        self.assertEqual([], problems)

    def test_every_feature_has_a_picture(self) -> None:
        # Not a count of files: what matters is that the manual shows them.
        for code, _label in i18n.available_languages():
            text = build_manuals.manual_path(code).read_text(encoding="utf-8")
            with self.subTest(language=code):
                self.assertGreaterEqual(text.count("!["), 14)

    def test_no_manual_names_a_real_person_or_project(self) -> None:
        # The screenshots are anonymised; the text has to be too, or the
        # anonymising was pointless.
        forbidden = ("joruf", "Jo Ruf", "pmtool", "snappix", "consentry", "byteback")
        for code, _label in i18n.available_languages():
            text = build_manuals.manual_path(code).read_text(encoding="utf-8")
            for name in forbidden:
                with self.subTest(language=code, name=name):
                    self.assertNotIn(name, text)

    def test_the_build_script_can_be_asked_for_one_language(self) -> None:
        # The check-only path must not need a display or a git binary.
        self.assertEqual(0, build_manuals.main(["--check", "--language", "en"]))

    def test_an_unknown_language_is_refused(self) -> None:
        self.assertEqual(2, build_manuals.main(["--check", "--language", "klingon"]))


if __name__ == "__main__":
    unittest.main()
