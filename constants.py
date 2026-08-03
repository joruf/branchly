"""
Application-wide constants.
"""

from __future__ import annotations

APP_NAME = "Branchly"
APP_SLUG = "branchly"
APP_VERSION = "0.1.0"

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
