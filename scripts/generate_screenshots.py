#!/usr/bin/env python3
"""
Builds a demo repository and captures the window for the documentation.

Runs happily on the offscreen Qt platform, so it works over SSH and in CI. The
demo repository is thrown away afterwards; nothing outside the temporary
directory is touched.

Usage:
    QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/generate_screenshots.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import i18n  # noqa: E402
from config.app_settings import AppSettings  # noqa: E402
from config.theme import (  # noqa: E402
    THEME_DARK,
    THEME_LIGHT,
    build_application_stylesheet,
    set_current_theme,
)

OUTPUT_DIR = _ROOT / "docs" / "screenshots"
WINDOW_SIZE = (1400, 880)


def _git(args: list[str], cwd: Path, env: dict[str, str]) -> None:
    """
    Runs a git command for the demo fixture.

    Args:
        args: Arguments after the executable.
        cwd: Directory to run in.
        env: Environment for the child process.

    Returns:
        None
    """

    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, shell=False)


def build_demo_repositories(base: Path) -> list[Path]:
    """
    Creates a few repositories with something interesting to show.

    Args:
        base: Directory to build them in.

    Returns:
        list[Path]: Working tree paths.
    """

    home = base / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(home / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Jo Ruf",
            "GIT_AUTHOR_EMAIL": "jo@example.invalid",
            "GIT_COMMITTER_NAME": "Jo Ruf",
            "GIT_COMMITTER_EMAIL": "jo@example.invalid",
            "LC_ALL": "C",
        }
    )

    created: list[Path] = []

    # A project with local changes, a side branch and a merge, so the graph has
    # something to draw and the changes panel is not empty.
    main_repo = base / "pmtool"
    main_repo.mkdir()
    _git(["init", "-b", "main"], main_repo, env)
    _git(["config", "user.name", "Jo Ruf"], main_repo, env)
    _git(["config", "user.email", "jo@example.invalid"], main_repo, env)

    (main_repo / "config.py").write_text("port = 8080\ndebug = True\ntimeout = 30\n", encoding="utf-8")
    (main_repo / "README.md").write_text("# PM Tool\n\nProject management.\n", encoding="utf-8")
    _git(["add", "-A"], main_repo, env)
    _git(["commit", "-m", "Initial project layout"], main_repo, env)

    _git(["switch", "--create", "feature/reminders"], main_repo, env)
    (main_repo / "reminders.py").write_text("def send():\n    pass\n", encoding="utf-8")
    _git(["add", "-A"], main_repo, env)
    _git(["commit", "-m", "Add purchasing reminders"], main_repo, env)

    _git(["switch", "main"], main_repo, env)
    (main_repo / "README.md").write_text("# PM Tool\n\nProject management for teams.\n", encoding="utf-8")
    _git(["add", "-A"], main_repo, env)
    _git(["commit", "-m", "Describe the project better"], main_repo, env)
    _git(["merge", "--no-edit", "feature/reminders"], main_repo, env)

    # Uncommitted work, so the file list and the diff have content.
    (main_repo / "config.py").write_text("port = 3000\ndebug = False\ntimeout = 30\n", encoding="utf-8")
    (main_repo / "notes.md").write_text("- ask about the SSO deadline\n", encoding="utf-8")
    created.append(main_repo)

    for name, subject in (("snappix", "Fix capture on Wayland"), ("consentry", "Pin the scanner")):
        repo = base / name
        repo.mkdir()
        _git(["init", "-b", "main"], repo, env)
        _git(["config", "user.name", "Jo Ruf"], repo, env)
        _git(["config", "user.email", "jo@example.invalid"], repo, env)
        (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
        _git(["add", "-A"], repo, env)
        _git(["commit", "-m", subject], repo, env)
        created.append(repo)

    return created


def capture(theme: str, repositories: list[Path], config_dir: Path) -> Path:
    """
    Builds the window with a demo registry and saves a picture of it.

    Args:
        theme: Theme to render.
        repositories: Repositories to register.
        config_dir: Directory used for settings and registry files.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    os.environ["XDG_CONFIG_HOME"] = str(config_dir / theme)
    i18n.set_language("de")
    set_current_theme(theme)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    from ui.main_window import MainWindow

    settings = AppSettings(theme=theme, language="de", auto_check_minutes=0, github_enabled=False)
    window = MainWindow(settings)
    window.resize(*WINDOW_SIZE)

    registry = window._registry
    registry.add_category("Arbeit")
    registry.add_category("Privat")
    for index, path in enumerate(repositories):
        _outcome, entry = registry.add(path, "Arbeit" if index == 0 else "Privat")
        if entry is not None and index == 0:
            entry.favorite = True
    window._sidebar.refresh()

    first = registry.entries[0]
    window._sidebar.select_key(first.key)
    window._activate(first)
    window._changes._list.setCurrentRow(0)
    window.show()
    app.processEvents()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"main-window-{theme}.png"
    window.grab().save(str(target))

    # The graph tab, which is the other view worth showing.
    window._tabs.setCurrentIndex(1)
    app.processEvents()
    graph_target = OUTPUT_DIR / f"graph-{theme}.png"
    window.grab().save(str(graph_target))

    window.close()
    return target


DEMO_OWNER = "joruf"
DEMO_REPO = "branchly"


def _demo_github_routes(server: object) -> None:
    """
    Fills the stand-in server with something worth photographing.

    Args:
        server: The ``FakeGitHub`` instance to register routes on.

    Returns:
        None
    """

    def person(login: str) -> dict:
        return {"login": login, "avatar_url": ""}

    base = f"/repos/{DEMO_OWNER}/{DEMO_REPO}"
    server.json("GET", base, {
        "name": DEMO_REPO,
        "full_name": f"{DEMO_OWNER}/{DEMO_REPO}",
        "owner": person(DEMO_OWNER),
        "default_branch": "main",
        "private": False,
        "permissions": {"push": True, "admin": True},
    })
    server.json("GET", f"{base}/branches", [
        {"name": "main", "commit": {"sha": "a" * 40}},
        {"name": "konflikt-assistent", "commit": {"sha": "b" * 40}},
    ])
    server.json("GET", f"{base}/pulls", [
        {
            "number": 42, "title": "Konflikte einzeln entscheiden statt Marker zeigen",
            "state": "open", "draft": False, "user": person("joruf"),
            "head": {"ref": "konflikt-assistent", "sha": "b" * 40}, "base": {"ref": "main"},
            "labels": [{"name": "feature"}], "assignees": [person("joruf")],
            "updated_at": "2026-09-18T09:20:00Z",
            "body": "Drei Spalten, vier Knöpfe, keine `<<<<<<<` mehr.\n\n"
                    "Nichts wird geschrieben, bevor jede Entscheidung gefallen ist.",
        },
        {
            "number": 41, "title": "Alle Projekte in einem Rutsch holen",
            "state": "open", "draft": True, "user": person("joruf"),
            "head": {"ref": "pull-all", "sha": "c" * 40}, "base": {"ref": "main"},
            "updated_at": "2026-09-17T16:05:00Z",
        },
    ])
    server.json("GET", f"{base}/pulls/42", {
        "number": 42, "title": "Konflikte einzeln entscheiden statt Marker zeigen",
        "state": "open", "draft": False, "user": person("joruf"),
        "head": {"ref": "konflikt-assistent", "sha": "b" * 40}, "base": {"ref": "main"},
        "labels": [{"name": "feature"}], "assignees": [person("joruf")],
        "mergeable": True, "mergeable_state": "clean", "updated_at": "2026-09-18T09:20:00Z",
        "body": "Drei Spalten, vier Knöpfe, keine `<<<<<<<` mehr.\n\n"
                "Nichts wird geschrieben, bevor jede Entscheidung gefallen ist.",
    })
    server.json("GET", f"{base}/issues/42/comments", [
        {"id": 1, "user": person("joruf"), "created_at": "2026-09-18T10:02:00Z",
         "body": "Getestet gegen ein Repo mit drei Konflikten in einer Datei."},
    ])
    server.json("GET", f"{base}/pulls/42/reviews", [])
    server.json("GET", f"{base}/pulls/42/commits", [
        {"sha": "b" * 40, "commit": {"message": "Konfliktregionen aus den Index-Stufen lesen"}},
    ])
    server.json("GET", f"{base}/pulls/42/files", [
        {"filename": "gitops/conflict.py", "status": "added", "additions": 412, "deletions": 0},
        {"filename": "ui/conflict_dialog.py", "status": "added", "additions": 638, "deletions": 0},
    ])
    server.json("GET", f"{base}/commits/konflikt-assistent/status", {"total_count": 1, "state": "success"})
    server.json("GET", f"{base}/commits/konflikt-assistent/check-runs", {"check_runs": []})
    for path in ("/issues", "/labels", "/assignees", "/milestones", "/releases", "/tags"):
        server.json("GET", base + path, [])
    server.json("GET", f"{base}/actions/runs", {"workflow_runs": []})


def capture_github(theme: str, config_dir: Path) -> Path:
    """
    Photographs the GitHub panel against a stand-in server.

    The data has to come from somewhere, and pointing the real client at a fake
    server is the honest way: the panel goes through the same code it does in
    the application, rather than being filled by hand for the picture. The
    server is the one the tests use, which is why a script imports from
    ``tests``.

    Args:
        theme: Theme to render.
        config_dir: Directory used for settings and registry files.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from github_api.client import GitHubClient
    from tests.support_github import FakeGitHub
    from ui.github_lists import GitHubContext
    from ui.github_panel import GitHubPanel

    os.environ["XDG_CONFIG_HOME"] = str(config_dir / f"{theme}-github")
    i18n.set_language("de")
    set_current_theme(theme)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    server = FakeGitHub()
    try:
        _demo_github_routes(server)
        client = GitHubClient("ghp_" + "x" * 36, api_root=server.root)
        panel = GitHubPanel(client, show_avatars=False)
        panel.resize(1100, 760)
        panel.set_context(
            GitHubContext(
                owner=DEMO_OWNER,
                repo=DEMO_REPO,
                viewer_login=DEMO_OWNER,
                local_branch="konflikt-assistent",
                default_branch="main",
            )
        )
        panel.show()
        # The panel loads on a worker thread, so the event loop has to run until
        # the list and the detail are actually there.
        deadline = time.time() + 15
        while time.time() < deadline:
            app.processEvents()
            if not panel._runner.busy and panel._pulls._list.count() > 1:
                break
            time.sleep(0.02)
        panel._pulls._list.setCurrentRow(0)
        deadline = time.time() + 15
        while time.time() < deadline:
            app.processEvents()
            if not panel._runner.busy:
                break
            time.sleep(0.02)
        app.processEvents()

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUTPUT_DIR / f"github-panel-{theme}.png"
        panel.grab().save(str(target))
        panel.stop()
        panel.close()
        client.close()
    finally:
        server.stop()
    return target


def capture_binary(theme: str, base: Path) -> Path:
    """
    Photographs the comparison of a file that has no readable diff.

    Args:
        theme: Theme to render.
        base: Directory to build the demo repository in.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from gitops import blobs
    from ui.diff_view import BinaryComparisonView

    i18n.set_language("de")
    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    root = base / f"binary-{theme}"
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(root),
            "GIT_CONFIG_GLOBAL": str(root / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(root / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Jo Ruf",
            "GIT_AUTHOR_EMAIL": "jo@example.invalid",
            "GIT_COMMITTER_NAME": "Jo Ruf",
            "GIT_COMMITTER_EMAIL": "jo@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-14T10:15:00",
            "GIT_COMMITTER_DATE": "2026-09-14T10:15:00",
            "LC_ALL": "C",
        }
    )
    _git(["init", "-b", "main"], root, env)
    (root / "handbuch.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00\x01" * 9000)
    _git(["add", "-A"], root, env)
    _git(["commit", "-m", "Handbuch hinzufügen"], root, env)
    (root / "handbuch.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00\x01" * 14500)

    view = BinaryComparisonView()
    view.resize(900, 460)
    view.show_comparison(blobs.compare(root, "handbuch.pdf"))
    view.show()
    app.processEvents()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"binary-comparison-{theme}.png"
    view.grab().save(str(target))
    view.close()
    return target


def capture_discover(theme: str, base: Path) -> Path:
    """
    Photographs the repository search against a small folder tree.

    Args:
        theme: Theme to render.
        base: Directory to build the demo tree in.

    Returns:
        Path: The written file.
    """

    import time

    from PySide6.QtWidgets import QApplication

    from ui.discover_dialog import DiscoverDialog

    i18n.set_language("de")
    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    home = base / f"discover-{theme}"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(home / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Jo Ruf",
            "GIT_AUTHOR_EMAIL": "jo@example.invalid",
            "GIT_COMMITTER_NAME": "Jo Ruf",
            "GIT_COMMITTER_EMAIL": "jo@example.invalid",
            "LC_ALL": "C",
        }
    )

    known: set[str] = set()
    for index, (name, branch) in enumerate(
        (
            ("branchly", "main"),
            ("consentry", "main"),
            ("pmtool", "feature/topdesk"),
            ("snappix", "main"),
            ("byteback", "main"),
        )
    ):
        root = home / "Applications" / name
        root.mkdir(parents=True, exist_ok=True)
        _git(["init", "-b", branch], root, env)
        _git(["remote", "add", "origin", f"https://github.com/joruf/{name}.git"], root, env)
        if index < 2:
            known.add(str(root.resolve()))
    # Something the walk must not report, so the picture shows the rule working.
    vendored = home / "Applications" / "branchly" / "node_modules" / "left-pad"
    vendored.mkdir(parents=True, exist_ok=True)
    (vendored / ".git").mkdir(exist_ok=True)

    dialog = DiscoverDialog(known, [], [home])
    dialog.resize(760, 560)
    dialog.show()
    deadline = time.time() + 20
    while time.time() < deadline:
        app.processEvents()
        if dialog._thread is None and dialog._list.count():
            break
        time.sleep(0.02)
    app.processEvents()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"discover-{theme}.png"
    dialog.grab().save(str(target))
    dialog.close()
    return target


def capture_conflict(theme: str, base: Path) -> Path:
    """
    Builds a repository with a real conflict and photographs the assistant.

    Args:
        theme: Theme to render.
        base: Directory to build the repository in.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from gitops import branch as branch_mod
    from gitops import conflict as conflict_mod
    from ui.conflict_dialog import ConflictDialog

    i18n.set_language("de")
    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    repo = base / f"conflict-{theme}"
    repo.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Jo Ruf",
            "GIT_AUTHOR_EMAIL": "jo@example.invalid",
            "GIT_COMMITTER_NAME": "Jo Ruf",
            "GIT_COMMITTER_EMAIL": "jo@example.invalid",
            "LC_ALL": "C",
        }
    )
    _git(["init", "-b", "main"], repo, env)
    _git(["config", "user.name", "Jo Ruf"], repo, env)
    _git(["config", "user.email", "jo@example.invalid"], repo, env)

    (repo / "config.py").write_text("port = 1234\ndebug = True\ntimeout = 30\n", encoding="utf-8")
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", "Base configuration"], repo, env)

    branch_mod.create_branch(repo, "kollege")
    (repo / "config.py").write_text("port = 3000\ndebug = True\ntimeout = 30\n", encoding="utf-8")
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", "Colleague changes the port"], repo, env)

    branch_mod.checkout_branch(repo, "main")
    (repo / "config.py").write_text("port = 8080\ndebug = False\ntimeout = 30\n", encoding="utf-8")
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", "We change the port too"], repo, env)
    branch_mod.merge(repo, "kollege")

    dialog = ConflictDialog(repo, conflict_mod.load_all(repo), None)
    dialog.resize(980, 560)
    dialog._show_current_decision()
    dialog.show()
    app.processEvents()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"conflict-assistant-{theme}.png"
    dialog.grab().save(str(target))
    dialog.close()
    return target


def capture_signin(theme: str) -> Path:
    """
    Photographs the sign-in dialog.

    A client id is put in the environment so the browser route is shown as it
    looks when it is available. The value names no real application and nothing
    is sent anywhere: the dialog only reads it to decide whether to offer the
    button.

    Args:
        theme: Theme to render.

    Returns:
        Path: The written file.
    """

    import os

    from PySide6.QtWidgets import QApplication

    from ui.signin_dialog import SignInDialog

    i18n.set_language("de")
    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    os.environ["BRANCHLY_GITHUB_CLIENT_ID"] = "Iv1.example"
    try:
        dialog = SignInDialog()
        dialog.resize(560, 430)
        dialog.show()
        for _round in range(6):
            app.processEvents()

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUTPUT_DIR / f"signin-{theme}.png"
        dialog.grab().save(str(target))
        dialog.done(0)
    finally:
        os.environ.pop("BRANCHLY_GITHUB_CLIENT_ID", None)
    return target


def main() -> int:
    """
    Generates every screenshot.

    Returns:
        int: Process exit code.
    """

    if shutil.which("git") is None:
        print("git is required to build the demo repositories", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="branchly-shots-") as tmp:
        base = Path(tmp)
        repositories = build_demo_repositories(base)
        config_dir = base / "config"
        config_dir.mkdir()
        for theme in (THEME_DARK, THEME_LIGHT):
            print(f"wrote {capture(theme, repositories, config_dir)}")
            print(f"wrote {capture_github(theme, config_dir)}")
            print(f"wrote {capture_binary(theme, base)}")
            print(f"wrote {capture_discover(theme, base)}")
            print(f"wrote {capture_conflict(theme, base)}")
            print(f"wrote {capture_signin(theme)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
