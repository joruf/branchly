#!/usr/bin/env python3
"""
Moves a GitHub token from an encrypted file into the system keychain.

Branchly reads its token from the keychain and from nowhere else, which normally
means pasting it into Settings once. This script exists for the case where the
token already lives in an ``age`` or ``sops`` encrypted file: it decrypts, picks
the token out, stores it, and prints nothing but a confirmation.

The token is never written to a file, never echoed, and never passed on a command
line where another process could read it out of the process list.

Usage:

    ./scripts/import_token.py ~/Applications/credentials.ls.yaml.age
    ./scripts/import_token.py --key github_token ~/secrets.yaml.age
    ./scripts/import_token.py --stdin          # paste it, no file involved

The decryption tool is picked automatically: ``sops`` for a sops file, ``age``
otherwise. For ``age`` the identity is taken from ``--identity``, from
``SOPS_AGE_KEY_FILE``, or from the usual place under ``~/.config``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from github_api import token as token_store

# Keys worth looking at in a decrypted file, most specific first. A file holding
# several secrets should not have its database password mistaken for a token.
CANDIDATE_KEYS = (
    "github_token",
    "pat",
    "github_pat",
    "gh_token",
    "github",
    "token",
)

DEFAULT_IDENTITIES = (
    Path.home() / ".config" / "sops" / "age" / "keys.txt",
    Path.home() / ".config" / "age" / "keys.txt",
    Path.home() / ".age" / "keys.txt",
)


def decrypt(path: Path, identity: Path | None) -> str:
    """
    Decrypts a file and returns its plain text.

    Args:
        path: Encrypted file.
        identity: age identity file, or None to search the usual places.

    Returns:
        str: The decrypted content.

    Raises:
        SystemExit: When no decryption tool worked.
    """

    attempts: list[list[str]] = []
    if path.suffix in {".sops", ".yaml", ".yml", ".json"} or ".sops." in path.name:
        attempts.append(["sops", "-d", str(path)])
    attempts.append(["sops", "-d", str(path)])

    age_command = ["age", "-d"]
    chosen = identity or next((item for item in DEFAULT_IDENTITIES if item.is_file()), None)
    if os.environ.get("SOPS_AGE_KEY_FILE"):
        chosen = Path(os.environ["SOPS_AGE_KEY_FILE"])
    if chosen is not None:
        age_command += ["-i", str(chosen)]
    age_command.append(str(path))
    attempts.append(age_command)

    problems: list[str] = []
    for command in attempts:
        try:
            finished = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            problems.append(f"{command[0]}: {error}")
            continue
        if finished.returncode == 0 and finished.stdout.strip():
            return finished.stdout
        problems.append(f"{command[0]}: {finished.stderr.strip() or 'no output'}")

    sys.exit("Could not decrypt the file.\n  " + "\n  ".join(problems))


def walk(content: str) -> Iterator[tuple[str, str]]:
    """
    Walks a decrypted file, yielding each field with its full path.

    Deliberately a line scan rather than a YAML parse: the file may hold anything,
    and pulling one value out of it must not depend on the whole document being
    valid YAML.

    Args:
        content: Decrypted text.

    Yields:
        tuple[str, str]: Dotted path and value. A field that only opens a nested
            block has an empty value.
    """

    stack: list[tuple[int, str]] = []
    for line in content.splitlines():
        found = re.match(r"^(\s*)([A-Za-z0-9_.\-]+)\s*:\s*(.*?)\s*$", line)
        if not found:
            continue
        indent = len(found.group(1).expandtabs(4))
        name = found.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([item[1] for item in stack] + [name])
        stack.append((indent, name))
        yield path, found.group(3).strip().strip("\"'")


def extract(content: str, key: str = "") -> str:
    """
    Picks the token out of a decrypted file.

    A key may be a dotted path such as ``github.token``. That matters for a file
    holding several services: a bare ``password`` would otherwise take whichever
    block happens to come first, which is how a server password ends up being
    stored as a GitHub token.

    Args:
        content: Decrypted text.
        key: Field name or dotted path, empty to try the known ones.

    Returns:
        str: The token, empty when none was found.
    """

    fields = [(path, value) for path, value in walk(content) if value]

    if key:
        wanted = key.lower()
        for path, value in fields:
            if path.lower() == wanted:
                return value
        # A bare name is allowed to match the last segment of a path, so
        # "--key token" still finds "github.token".
        for path, value in fields:
            if path.lower().rsplit(".", 1)[-1] == wanted:
                return value
        return ""

    for name in CANDIDATE_KEYS:
        for path, value in fields:
            segments = path.lower().split(".")
            if segments[-1] == name or (len(segments) > 1 and segments[-2:] == ["github", name]):
                return value

    # Last resort: a value that is nothing but a token. Only tried when no named
    # field matched, so a labelled value always wins.
    loose = re.search(r"\b((?:github_pat_|ghp_|gho_|ghu_|ghs_|ghr_)[A-Za-z0-9_]{20,})\b", content)
    return loose.group(1) if loose else ""


def field_names(content: str) -> list[str]:
    """
    Lists the field names a decrypted file holds.

    Values are never returned, only the names, so this can be used to find the
    right ``--key`` without putting a secret on screen. Nesting is shown as a
    path, because a token two levels down is still reachable with ``--key``.

    Args:
        content: Decrypted text.

    Returns:
        list[str]: Field names in the order they appear.
    """

    names: list[str] = []
    for path, _value in walk(content):
        if path not in names:
            names.append(path)
    return names


def main() -> int:
    """
    Reads the token and stores it.

    Returns:
        int: Process exit code.
    """

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("file", nargs="?", help="encrypted file holding the token")
    parser.add_argument("--key", default="", help="field name inside the file")
    parser.add_argument("--identity", type=Path, default=None, help="age identity file")
    parser.add_argument("--stdin", action="store_true", help="type the token instead of decrypting a file")
    parser.add_argument(
        "--list-keys",
        action="store_true",
        help="print only the field names in the file, so --key can name the right one",
    )
    arguments = parser.parse_args()

    if not token_store.is_available():
        print("No system keychain is available, so the token cannot be stored.", file=sys.stderr)
        print("On Linux install gnome-keyring or kwallet, then run this again.", file=sys.stderr)
        return 2

    if arguments.stdin:
        token = getpass.getpass("GitHub token (not echoed): ").strip()
    else:
        if not arguments.file:
            parser.error("give a file, or use --stdin")
        path = Path(arguments.file).expanduser()
        if not path.is_file():
            return _fail(f"{path} does not exist.")
        content = decrypt(path, arguments.identity)
        if arguments.list_keys:
            for name in field_names(content):
                print(name)
            return 0
        token = extract(content, arguments.key)

    if not token:
        return _fail("No token was found. Name the field with --key, or use --stdin.")
    if not token_store.looks_like_a_token(token):
        print("Warning: that does not look like a GitHub token, storing it anyway.", file=sys.stderr)
    if not token_store.save(token):
        return _fail("The keychain refused to store the token.")

    print(f"Stored in the keychain as {token_store.SERVICE_NAME}/{token_store.ACCOUNT_NAME}.")
    print("Branchly will use it on the next start. Settings, GitHub tab shows which account it belongs to.")
    return 0


def _fail(message: str) -> int:
    """
    Prints a problem and returns a failing exit code.

    Args:
        message: What went wrong.

    Returns:
        int: Always 1.
    """

    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
