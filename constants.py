"""
Application-wide constants.
"""

from __future__ import annotations

APP_NAME = "Branchly"
APP_SLUG = "branchly"
APP_VERSION = "0.1.0"
APP_URL = "https://github.com/joruf/branchly"

# Set in the child process of a re-exec or a restart-after-update, so neither can
# ever turn into a loop. Lives here because both ``run`` and ``services.updater``
# need the same name.
REEXEC_MARKER = "BRANCHLY_REEXEC"

# Timeouts in seconds, split by expected cost. Reading local state must never
# hang the UI for long; anything touching the network gets real room.
GIT_TIMEOUT_LOCAL = 20
GIT_TIMEOUT_NETWORK = 120
GIT_TIMEOUT_CLONE = 900

# Beyond this, a diff is truncated for display: rendering a hundred thousand
# lines freezes the view and tells the user nothing they can act on.
DIFF_MAX_LINES = 4000

# Commits loaded per graph page.
GRAPH_PAGE_SIZE = 400

# Widest a single file may be before the side-by-side view stops trying to pair
# lines and falls back to the one-column view.
DIFF_MAX_LINE_LENGTH = 2000

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

# Branchly publishes neither releases nor tags, so "newer" means the head commit
# of this branch rather than a version number.
UPDATE_BRANCH = "main"
UPDATE_TIMEOUT = 20
UPDATE_DOWNLOAD_TIMEOUT = 180

# Commit subjects listed as "what is new". Beyond this the list stops being read
# and starts being wallpaper; the total count is shown either way.
UPDATE_MAX_CHANGES = 10

# A source archive of this project is a few megabytes. The ceiling is not about
# disk space but about never streaming an unbounded body into a temporary file
# because a redirect landed somewhere unexpected.
UPDATE_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
