#!/usr/bin/env python3
"""
Rebuilds the manuals: every picture, in every language, and then the checks.

Run this whenever the window changes. Screenshots rot quietly: nothing fails, no
test goes red, the manual simply starts showing a program that no longer exists.
The only defence is making them cheap to redo, which is what this is for.

    scripts/build_manuals.py                 everything
    scripts/build_manuals.py --language de   one language
    scripts/build_manuals.py --check         no pictures, only the checks

The checks are the second half of the job and the part that catches drift:

* Every picture a manual points at has to exist, in that manual's language.
* Every picture that gets generated has to be used by its manual. An unused one
  means a feature was photographed and never written about.
* The manuals have to have the same sections. A feature documented in one
  language and missing from the other is the normal way a translation rots.
* Every language Branchly speaks needs a manual at all.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import i18n  # noqa: E402

DOCS = _ROOT / "docs"
SCREENSHOTS = DOCS / "screenshots"
GENERATOR = _ROOT / "scripts" / "generate_screenshots.py"

# Both themes are produced; the manuals show one of them. The other is there so
# the choice can change without regenerating everything.
MANUAL_THEME = "dark"

_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_HEADING = re.compile(r"^(#{2,3})\s+(.*)$", re.M)


def manual_path(language: str) -> Path:
    """
    Returns where a language's manual lives.

    Args:
        language: Language code.

    Returns:
        Path: The file, which may not exist yet.
    """

    return DOCS / f"MANUAL.{language}.md"


def languages() -> list[str]:
    """
    Returns every language Branchly speaks.

    Returns:
        list[str]: Language codes.
    """

    return [code for code, _label in i18n.available_languages()]


def generate(wanted: list[str]) -> int:
    """
    Produces the screenshots.

    Args:
        wanted: Language codes to produce.

    Returns:
        int: Number of files written.
    """

    command = [sys.executable, str(GENERATOR)]
    for language in wanted:
        command.extend(["--language", language])
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    sys.stdout.write(done.stdout)
    if done.returncode != 0:
        sys.stderr.write(done.stderr)
        raise SystemExit(done.returncode)
    return done.stdout.count("wrote ")


def check(wanted: list[str]) -> list[str]:
    """
    Reports everything that would leave a manual out of step.

    Args:
        wanted: Language codes to check.

    Returns:
        list[str]: One line per problem, empty when all is well.
    """

    problems: list[str] = []
    structures: dict[str, list[str]] = {}

    for language in wanted:
        manual = manual_path(language)
        if not manual.is_file():
            problems.append(f"{manual.name} is missing")
            continue
        text = manual.read_text(encoding="utf-8")

        used: set[str] = set()
        for target in _IMAGE.findall(text):
            path = (manual.parent / target).resolve()
            if not path.is_file():
                problems.append(f"{manual.name}: points at {target}, which does not exist")
                continue
            if f"screenshots/{language}/" not in target:
                problems.append(
                    f"{manual.name}: {target} is not this language's picture"
                )
            used.add(path.name)

        folder = SCREENSHOTS / language
        if not folder.is_dir():
            problems.append(f"no pictures for {language}, run without --check")
            continue
        for picture in sorted(folder.glob(f"*-{MANUAL_THEME}.png")):
            if picture.name not in used:
                problems.append(f"{manual.name}: never shows {picture.name}")

        structures[language] = [title.strip() for _level, title in _HEADING.findall(text)]

    counts = {language: len(titles) for language, titles in structures.items()}
    if len(set(counts.values())) > 1:
        listing = ", ".join(f"{code}: {count}" for code, count in sorted(counts.items()))
        problems.append(f"the manuals have different sections ({listing})")

    return problems


def main(argv: list[str] | None = None) -> int:
    """
    Rebuilds and checks the manuals.

    Args:
        argv: Command line arguments.

    Returns:
        int: Zero when everything is in order.
    """

    parser = argparse.ArgumentParser(description="Rebuild the manuals and their pictures")
    parser.add_argument(
        "--language",
        action="append",
        metavar="CODE",
        help="only this language, may be given more than once",
    )
    parser.add_argument(
        "--check", action="store_true", help="only run the checks, generate nothing"
    )
    args = parser.parse_args(argv)

    known = languages()
    wanted = args.language or known
    unknown = [code for code in wanted if code not in known]
    if unknown:
        print(f"no such language: {', '.join(unknown)}", file=sys.stderr)
        return 2

    if not args.check:
        written = generate(wanted)
        print(f"\n{written} pictures written")

    problems = check(wanted)
    if problems:
        print(f"\n{len(problems)} problem(s):", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1

    print(f"\nthe manuals are in step ({', '.join(wanted)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
