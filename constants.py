"""
Application-wide constants.
"""

from __future__ import annotations

APP_NAME = "Branchly"
APP_SLUG = "branchly"
APP_VERSION = "0.3.0"
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

# Unchanged lines shown around each change when the panel is not showing the
# whole file. Twice git's own default of three, because three is enough to place
# a change and not enough to read it in context. Adjustable in the settings,
# within these bounds: none at all is a valid answer, and past a hundred the
# switch for the whole file is the better tool.
DIFF_CONTEXT_LINES = 6
DIFF_CONTEXT_MIN = 0
DIFF_CONTEXT_MAX = 100

# What the drop-down above the comparison offers. Concrete numbers rather than a
# switch plus a setting somewhere else: the amount of surrounding text is
# something people change while reading, not once when they install the program.
DIFF_CONTEXT_CHOICES = (3, 6, 10, 20, 50)

# What "show unchanged text" asks git for. There is no flag for "all of it", so
# this is simply a number no real source file reaches. The renderer's own
# ``DIFF_MAX_LINES`` still caps what ends up on screen.
DIFF_WHOLE_FILE_CONTEXT = 1_000_000

# Commits loaded per graph page.
GRAPH_PAGE_SIZE = 400

# Widest a single file may be before the side-by-side view stops trying to pair
# lines and falls back to the one-column view.
DIFF_MAX_LINE_LENGTH = 2000

GITHUB_API_ROOT = "https://api.github.com"
GITHUB_USER_AGENT = f"{APP_NAME}/{APP_VERSION}"

# Signing in with the browser, through GitHub's device flow. The client id of an
# OAuth app is public, not a secret: the device flow exists precisely so that a
# desktop program does not have to ship one. Left empty here because it names a
# particular registered app; set it, or the ``BRANCHLY_GITHUB_CLIENT_ID``
# environment variable, and the menu offers one-click sign-in. Without it,
# signing in still works by pasting a personal access token.
GITHUB_OAUTH_CLIENT_ID = ""
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_DEVICE_VERIFICATION_URL = "https://github.com/login/device"
# What Branchly asks for. The same set the settings dialog checks a pasted token
# against, so both routes end up with the same abilities.
GITHUB_OAUTH_SCOPES = ("repo", "workflow", "delete_repo", "read:org")
# Where a personal access token is created, with the boxes already ticked.
GITHUB_TOKEN_PAGE = (
    "https://github.com/settings/tokens/new"
    "?description=Branchly&scopes=repo,workflow,delete_repo,read:org"
)
GITHUB_OAUTH_TIMEOUT = 20

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
