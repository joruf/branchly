#!/usr/bin/env python3
"""
The program git asks when it needs a username or a password.

Git calls this with the prompt as its only argument and reads one line from
standard output. It exists because Branchly runs git without a terminal: with
``GIT_TERMINAL_PROMPT=0`` a repository whose credential helper has nothing
stored fails with "could not read Username", which Branchly could only report
as a rejected login.

The values come from the environment rather than from the command line, because
anyone on the machine can read another process's arguments through ``ps`` and
nobody can read its environment but its owner.

Standalone on purpose: git runs this as its own process, so it must not import
anything from Branchly.
"""

import os
import sys


def main() -> int:
    """
    Prints the answer to git's question.

    Returns:
        int: Zero when an answer was available.
    """

    prompt = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    # Git asks for the user name first and the password second, with the same
    # program. "Username" is git's own wording and LC_ALL=C keeps it English,
    # but the fallback costs nothing.
    wants_user = "username" in prompt or "user name" in prompt or "login" in prompt
    variable = "BRANCHLY_GIT_USERNAME" if wants_user else "BRANCHLY_GIT_PASSWORD"
    answer = os.environ.get(variable, "")
    sys.stdout.write(f"{answer}\n")
    return 0 if answer else 1


if __name__ == "__main__":
    raise SystemExit(main())
