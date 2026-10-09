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

import argparse
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

SCREENSHOT_ROOT = _ROOT / "docs" / "screenshots"

# Category names for the demo project list, per language.
DEMO_CATEGORIES = {"en": ("Work", "Personal"), "de": ("Arbeit", "Privat")}


def output_dir() -> Path:
    """
    Returns where the pictures for the active language belong.

    One folder per language, because a manual in English showing a window in
    German is worse than no picture at all.

    Returns:
        Path: The folder, created on demand by the caller.
    """

    return SCREENSHOT_ROOT / i18n.current_language()
WINDOW_SIZE = (1400, 880)


def _demo_env(base: Path) -> dict[str, str]:
    """
    Builds the git environment every demo repository runs in.

    Nothing outside the sandbox is read or written, and the author is invented.

    Args:
        base: Sandbox directory.

    Returns:
        dict[str, str]: Environment for the git calls.
    """

    home = base / "gitenv"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(home / ".gitconfig-system"),
            "GIT_AUTHOR_NAME": "Alex Berg",
            "GIT_AUTHOR_EMAIL": "alex@example.invalid",
            "GIT_COMMITTER_NAME": "Alex Berg",
            "GIT_COMMITTER_EMAIL": "alex@example.invalid",
            "LC_ALL": "C",
        }
    )
    return env


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
            "GIT_AUTHOR_NAME": "Alex Berg",
            "GIT_AUTHOR_EMAIL": "alex@example.invalid",
            "GIT_COMMITTER_NAME": "Alex Berg",
            "GIT_COMMITTER_EMAIL": "alex@example.invalid",
            "LC_ALL": "C",
        }
    )

    created: list[Path] = []

    # A project with local changes, a side branch and a merge, so the graph has
    # something to draw and the changes panel is not empty.
    main_repo = base / "invoicing"
    main_repo.mkdir()
    _git(["init", "-b", "main"], main_repo, env)
    _git(["config", "user.name", "Alex Berg"], main_repo, env)
    _git(["config", "user.email", "alex@example.invalid"], main_repo, env)

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

    for name, subject in (("sitemap", "Fix capture on Wayland"), ("cookiecheck", "Pin the scanner")):
        repo = base / name
        repo.mkdir()
        _git(["init", "-b", "main"], repo, env)
        _git(["config", "user.name", "Alex Berg"], repo, env)
        _git(["config", "user.email", "alex@example.invalid"], repo, env)
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
    set_current_theme(theme)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    from ui.main_window import MainWindow

    settings = AppSettings(theme=theme, language="de", auto_check_minutes=0, github_enabled=False)
    window = MainWindow(settings)
    window.resize(*WINDOW_SIZE)

    registry = window._registry
    # Invented, and in the language of the picture: a manual in English showing
    # German category names reads as a screenshot of somebody else's program.
    work, private = DEMO_CATEGORIES.get(i18n.current_language(), DEMO_CATEGORIES["en"])
    registry.add_category(work)
    registry.add_category(private)
    for index, path in enumerate(repositories):
        _outcome, entry = registry.add(path, work if index == 0 else private)
        if entry is not None and index == 0:
            entry.favorite = True
    window._sidebar.refresh()

    first = registry.entries[0]
    window._sidebar.select_key(first.key)
    window._activate(first)
    window._changes._list.setCurrentRow(0)
    window.show()
    app.processEvents()

    target_dir = output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"main-window-{theme}.png"
    window.grab().save(str(target))

    # The graph tab, which is the other view worth showing.
    window._tabs.setCurrentIndex(1)
    app.processEvents()
    graph_target = target_dir / f"graph-{theme}.png"
    window.grab().save(str(graph_target))

    window.close()
    return target


DEMO_OWNER = "example-team"
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
            "state": "open", "draft": False, "user": person("example-team"),
            "head": {"ref": "konflikt-assistent", "sha": "b" * 40}, "base": {"ref": "main"},
            "labels": [{"name": "feature"}], "assignees": [person("example-team")],
            "updated_at": "2026-09-18T09:20:00Z",
            "body": "Drei Spalten, vier Knöpfe, keine `<<<<<<<` mehr.\n\n"
                    "Nichts wird geschrieben, bevor jede Entscheidung gefallen ist.",
        },
        {
            "number": 41, "title": "Alle Projekte in einem Rutsch holen",
            "state": "open", "draft": True, "user": person("example-team"),
            "head": {"ref": "pull-all", "sha": "c" * 40}, "base": {"ref": "main"},
            "updated_at": "2026-09-17T16:05:00Z",
        },
    ])
    server.json("GET", f"{base}/pulls/42", {
        "number": 42, "title": "Konflikte einzeln entscheiden statt Marker zeigen",
        "state": "open", "draft": False, "user": person("example-team"),
        "head": {"ref": "konflikt-assistent", "sha": "b" * 40}, "base": {"ref": "main"},
        "labels": [{"name": "feature"}], "assignees": [person("example-team")],
        "mergeable": True, "mergeable_state": "clean", "updated_at": "2026-09-18T09:20:00Z",
        "body": "Drei Spalten, vier Knöpfe, keine `<<<<<<<` mehr.\n\n"
                "Nichts wird geschrieben, bevor jede Entscheidung gefallen ist.",
    })
    server.json("GET", f"{base}/issues/42/comments", [
        {"id": 1, "user": person("example-team"), "created_at": "2026-09-18T10:02:00Z",
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

        target_dir = output_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"github-panel-{theme}.png"
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
            "GIT_AUTHOR_NAME": "Alex Berg",
            "GIT_AUTHOR_EMAIL": "alex@example.invalid",
            "GIT_COMMITTER_NAME": "Alex Berg",
            "GIT_COMMITTER_EMAIL": "alex@example.invalid",
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

    target_dir = output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"binary-comparison-{theme}.png"
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
            "GIT_AUTHOR_NAME": "Alex Berg",
            "GIT_AUTHOR_EMAIL": "alex@example.invalid",
            "GIT_COMMITTER_NAME": "Alex Berg",
            "GIT_COMMITTER_EMAIL": "alex@example.invalid",
            "LC_ALL": "C",
        }
    )

    known: set[str] = set()
    for index, (name, branch) in enumerate(
        (
            ("branchly", "main"),
            ("cookiecheck", "main"),
            ("invoicing", "feature/topdesk"),
            ("sitemap", "main"),
            ("backupd", "main"),
        )
    ):
        root = home / "Applications" / name
        root.mkdir(parents=True, exist_ok=True)
        _git(["init", "-b", branch], root, env)
        _git(["remote", "add", "origin", f"https://github.com/example-team/{name}.git"], root, env)
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

    target_dir = output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"discover-{theme}.png"
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

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    repo = base / f"conflict-{theme}"
    repo.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Alex Berg",
            "GIT_AUTHOR_EMAIL": "alex@example.invalid",
            "GIT_COMMITTER_NAME": "Alex Berg",
            "GIT_COMMITTER_EMAIL": "alex@example.invalid",
            "LC_ALL": "C",
        }
    )
    _git(["init", "-b", "main"], repo, env)
    _git(["config", "user.name", "Alex Berg"], repo, env)
    _git(["config", "user.email", "alex@example.invalid"], repo, env)

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

    target_dir = output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"conflict-assistant-{theme}.png"
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

        target_dir = output_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"signin-{theme}.png"
        dialog.grab().save(str(target))
        dialog.done(0)
    finally:
        os.environ.pop("BRANCHLY_GITHUB_CLIENT_ID", None)
    return target



def _save(widget: object, theme: str, name: str) -> Path:
    """
    Writes one widget to the language's folder.

    Args:
        widget: The widget to photograph.
        theme: Theme it was rendered in.
        name: File name without the theme or the extension.

    Returns:
        Path: The written file.
    """

    target_dir = output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{name}-{theme}.png"
    widget.grab().save(str(target))
    return target


def _demo_window(theme: str, base: Path, repositories: list[Path]):  # noqa: ANN202
    """
    Builds a window on the demo projects, ready to be photographed.

    Args:
        theme: Theme to render.
        base: Sandbox directory.
        repositories: Demo projects, built once and reused.

    Returns:
        tuple: The application, the window and the first entry.
    """

    from PySide6.QtWidgets import QApplication

    from config.app_settings import AppSettings
    from ui.main_window import MainWindow

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    config_dir = base / "config"
    config_dir.mkdir(exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(config_dir)

    window = MainWindow(
        AppSettings(
            theme=theme,
            language=i18n.current_language(),
            auto_check_minutes=0,
            github_enabled=False,
        )
    )
    entry = None
    for path in repositories:
        _outcome, added = window._registry.add(path)
        entry = entry or added
    window._sidebar.refresh()
    if entry is not None:
        window._activate(entry)
    window._switch_scan_timer.stop()
    window.resize(1400, 880)
    window.show()
    for _round in range(6):
        app.processEvents()
    return app, window, entry


def capture_settings(theme: str) -> Path:
    """
    Photographs the settings window.

    Args:
        theme: Theme to render.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from config.app_settings import AppSettings
    from ui.settings_dialog import SettingsDialog

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    dialog = SettingsDialog(AppSettings(theme=theme, language=i18n.current_language()))
    dialog.resize(640, 560)
    dialog.show()
    for _round in range(6):
        app.processEvents()
    target = _save(dialog, theme, "settings")
    dialog.done(0)
    return target


def capture_github_discover(theme: str, base: Path) -> Path:
    """
    Photographs the search for new repositories on GitHub.

    The list is handed in rather than fetched: no token, no network, and only the
    demo account's names on the picture.

    Args:
        theme: Theme to render.
        base: Directory to build the demo folder in.

    Returns:
        Path: The written file.
    """

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from github_api.client import GitHubClient
    from services import github_discovery
    from services.github_discovery import RemoteRepository
    from ui.github_discover_dialog import GitHubDiscoverDialog

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    folder = base / f"github-discover-{theme}" / "Applications"
    folder.mkdir(parents=True, exist_ok=True)

    def demo(name: str, private: bool, pushed: str, description: str) -> RemoteRepository:
        return RemoteRepository(
            name=name,
            full_name=f"{DEMO_OWNER}/{name}",
            clone_url=f"https://github.com/{DEMO_OWNER}/{name}.git",
            description=description,
            private=private,
            pushed_at=f"{pushed}T09:00:00Z",
        )

    repositories = [
        demo("invoicing", True, "2026-09-30", "Invoices and reminders"),
        demo("sitemap", False, "2026-09-12", "Sitemap generator"),
        demo("reminders", True, "2026-08-21", "Purchasing reminders"),
        demo("planner", True, "2026-07-02", "Shift planning"),
        demo("cookiecheck", False, "2026-06-18", "Consent scanner"),
    ]
    known = {
        github_discovery.slug_key(f"https://github.com/{DEMO_OWNER}/invoicing.git"): "invoicing",
        github_discovery.slug_key(f"https://github.com/{DEMO_OWNER}/sitemap.git"): "sitemap",
    }
    dialog = GitHubDiscoverDialog(GitHubClient(""), known, [], folder, load=False)
    dialog.set_repositories(repositories)
    # Two of the three new rows ticked, so the box above the list shows its
    # third state. Offered rows start unticked on purpose.
    dialog._list.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._list.item(1).setCheckState(Qt.CheckState.Checked)
    # The plan is made, so the field can show a plausible home instead of the
    # temporary folder this picture was built in.
    dialog._folder.setText("/home/alex/Applications")
    dialog.resize(760, 520)
    dialog.show()
    for _round in range(6):
        app.processEvents()
    target = _save(dialog, theme, "github-discover")
    dialog.done(0)
    return target


def capture_pull_all(theme: str, base: Path) -> Path:
    """
    Photographs the bulk update window.

    Args:
        theme: Theme to render.
        base: Directory to build the demo projects in.

    Returns:
        Path: The written file.
    """

    return _capture_bulk(theme, base, push=False)


def capture_push_all(theme: str, base: Path) -> Path:
    """
    Photographs the window that sends every project.

    Args:
        theme: Theme to render.
        base: Directory to build the demo projects in.

    Returns:
        Path: The written file.
    """

    return _capture_bulk(theme, base, push=True)


def _capture_bulk(theme: str, base: Path, push: bool) -> Path:
    """
    Photographs a finished run over the demo projects.

    The run starts on its own once the window is shown, so a picture of the
    window before it is a state nobody sees. What is worth showing is the report,
    so the outcomes are fed in rather than produced against projects that do not
    exist.

    Args:
        theme: Theme to render.
        base: Directory to build the demo projects in.
        push: Whether to show sending instead of updating.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from services import pusher
    from services.puller import (
        RESULT_CURRENT,
        RESULT_PULLED,
        RESULT_PUSHED,
        RESULT_SKIPPED,
        SKIP_DIRTY,
        SKIP_OWN_COMMITS,
        PullJob,
        PullResult,
    )
    from ui.pull_all_dialog import MODE_PULL, MODE_PUSH, PullAllDialog

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    names = ("invoicing", "sitemap", "cookiecheck", "backupd", "reminders", "planner")
    jobs = [PullJob(path=base / "demo" / name, key=name, name=name) for name in names]
    if push:
        outcomes = [
            PullResult(key="invoicing", name="invoicing", state=RESULT_PUSHED, commits=2),
            PullResult(key="sitemap", name="sitemap", state=RESULT_CURRENT),
            PullResult(key="cookiecheck", name="cookiecheck", state=RESULT_PUSHED, commits=1),
            PullResult(
                key="backupd", name="backupd", state=RESULT_SKIPPED,
                reason_key=pusher.SKIP_BEHIND, waiting=3,
            ),
            PullResult(
                key="reminders", name="reminders", state=RESULT_SKIPPED,
                reason_key=pusher.SKIP_UNPUBLISHED,
            ),
            PullResult(key="planner", name="planner", state=RESULT_CURRENT),
        ]
    else:
        outcomes = [
            PullResult(key="invoicing", name="invoicing", state=RESULT_PULLED, commits=3),
            PullResult(key="sitemap", name="sitemap", state=RESULT_CURRENT),
            PullResult(
                key="cookiecheck", name="cookiecheck", state=RESULT_SKIPPED,
                reason_key=SKIP_DIRTY, waiting=2,
            ),
            PullResult(key="backupd", name="backupd", state=RESULT_PULLED, commits=1),
            PullResult(
                key="reminders", name="reminders", state=RESULT_SKIPPED,
                reason_key=SKIP_OWN_COMMITS,
            ),
            PullResult(key="planner", name="planner", state=RESULT_CURRENT),
        ]

    dialog = PullAllDialog(jobs, mode=MODE_PUSH if push else MODE_PULL, autostart=False)
    for outcome in outcomes:
        dialog._on_result(outcome)
    dialog._on_finished()
    dialog.show()
    for _round in range(6):
        app.processEvents()
    target = _save(dialog, theme, "push-all" if push else "pull-all")
    dialog.done(0)
    return target


def capture_revert(theme: str, base: Path) -> Path:
    """
    Photographs the revert confirmation.

    Args:
        theme: Theme to render.
        base: Directory to build a demo project in.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from gitops.status import read_state
    from services import revert as revert_mod
    from ui.revert_dialog import RevertDialog

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    repo = base / f"revert-{theme}"
    repo.mkdir(parents=True)
    env = _demo_env(base)
    for name in ("config.py", "listing.py", "README.md"):
        (repo / name).write_text(f"{name}\n", encoding="utf-8")
    _git(["init", "-b", "main"], repo, env)
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", "Initial layout"], repo, env)

    (repo / "config.py").write_text("changed\n", encoding="utf-8")
    (repo / "listing.py").write_text("changed too\n", encoding="utf-8")
    (repo / "README.md").unlink()
    (repo / "notes.txt").write_text("never saved\n", encoding="utf-8")
    (repo / "output.log").write_text("never saved\n", encoding="utf-8")

    dialog = RevertDialog(revert_mod.plan(read_state(repo).files), "invoicing")
    dialog.show()
    for _round in range(6):
        app.processEvents()
    target = _save(dialog, theme, "revert")
    dialog.done(0)
    return target


def capture_link_remote(theme: str, base: Path) -> Path:
    """
    Photographs the connect window in the case that needs a decision.

    Args:
        theme: Theme to render.
        base: Directory to build the demo repositories in.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from services import remote_link
    from ui.link_remote_dialog import LinkRemoteDialog

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    env = _demo_env(base)
    local = base / f"link-{theme}"
    local.mkdir(parents=True)
    _git(["init", "-b", "main"], local, env)
    (local / "a.txt").write_text("here\n", encoding="utf-8")
    _git(["add", "-A"], local, env)
    _git(["commit", "-m", "Local work"], local, env)

    server = base / f"server-{theme}.git"
    _git(["init", "--bare", "-b", "main", str(server)], base, env)
    seed = base / f"seed-{theme}"
    seed.mkdir()
    _git(["init", "-b", "main"], seed, env)
    (seed / "b.txt").write_text("there\n", encoding="utf-8")
    _git(["add", "-A"], seed, env)
    _git(["commit", "-m", "Server work"], seed, env)
    _git(["remote", "add", "origin", str(server)], seed, env)
    _git(["push", "-q", "-u", "origin", "main"], seed, env)

    dialog = LinkRemoteDialog(
        local, "invoicing", suggestion=f"https://github.com/{DEMO_OWNER}/invoicing.git"
    )
    dialog.resize(560, 300)
    dialog.show()
    for _round in range(4):
        app.processEvents()
    dialog._on_checked(remote_link.check(local, str(server)))
    dialog.adjustSize()
    for _round in range(4):
        app.processEvents()
    target = _save(dialog, theme, "link-remote")
    dialog.done(0)
    return target


def capture_tags(theme: str, base: Path) -> Path:
    """
    Photographs the tag list.

    Args:
        theme: Theme to render.
        base: Directory to build a demo project in.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from ui.tag_dialog import TagDialog

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    repo = base / f"tags-{theme}"
    repo.mkdir(parents=True)
    env = _demo_env(base)
    _git(["init", "-b", "main"], repo, env)
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", "First release"], repo, env)
    for name in ("v1.0.0", "v1.1.0", "v1.2.0"):
        _git(["tag", name], repo, env)

    dialog = TagDialog(repo, "main")
    dialog.show()
    for _round in range(6):
        app.processEvents()
    target = _save(dialog, theme, "tags")
    dialog.reject()
    return target


def capture_gitignore(theme: str, base: Path) -> Path:
    """
    Photographs the .gitignore editor with an ordinary file in it.

    Args:
        theme: Theme to render.
        base: Directory to build a demo project in.

    Returns:
        Path: The written file.
    """

    from PySide6.QtWidgets import QApplication

    from ui.gitignore_dialog import GitignoreDialog

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    repo = base / f"gitignore-{theme}"
    repo.mkdir(parents=True)
    comments = {
        "de": ("# Virtuelle Umgebung", "# Laufzeitdaten"),
    }.get(i18n.current_language(), ("# Virtual environment", "# Runtime data"))
    (repo / ".gitignore").write_text(
        f"{comments[0]}\n.venv/\n__pycache__/\n\n{comments[1]}\nsettings.json\n*.log\n",
        encoding="utf-8",
    )
    dialog = GitignoreDialog(repo)
    dialog.show()
    for _round in range(6):
        app.processEvents()
    target = _save(dialog, theme, "gitignore")
    dialog.done(0)
    return target


def capture_commit_files(theme: str, base: Path) -> list[Path]:
    """
    Photographs the files of one commit and the question before replacing.

    Args:
        theme: Theme to render.
        base: Directory to build a demo project in.

    Returns:
        list[Path]: The written files.
    """

    from PySide6.QtWidgets import QApplication

    from gitops import snapshot
    from gitops.history import read_history
    from ui.commit_files_dialog import CommitFilesDialog, RestoreConfirmDialog, ViewOptions

    set_current_theme(theme)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_application_stylesheet(theme))

    repo = base / f"commit-files-{theme}"
    (repo / "invoices").mkdir(parents=True)
    env = _demo_env(base)
    (repo / "config.py").write_text("port = 8080\ndebug = True\n", encoding="utf-8")
    (repo / "README.md").write_text("# Invoicing\n", encoding="utf-8")
    (repo / "numbers.txt").write_text("2026-0001\n2026-0002\n", encoding="utf-8")
    _git(["init", "-b", "main"], repo, env)
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", "Initial layout"], repo, env)

    (repo / "config.py").write_text(
        'port = 8080\ndebug = False\ninvoice_prefix = "RE"\n', encoding="utf-8"
    )
    (repo / "README.md").write_text(
        "# Invoicing\n\nNumbers come from the settings.\n", encoding="utf-8"
    )
    (repo / "invoices" / "numbering.py").write_text(
        "def next_number(last):\n    return last + 1\n", encoding="utf-8"
    )
    (repo / "numbers.txt").unlink()
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", "Take the invoice numbers from the settings"], repo, env)
    # Work not committed yet, so the question has something to warn about.
    (repo / "config.py").write_text(
        'port = 3000\ndebug = False\ninvoice_prefix = "RE"\n', encoding="utf-8"
    )

    commit = read_history(repo).commits[0]
    dialog = CommitFilesDialog(repo, commit, "invoicing", ViewOptions(mode="split"))
    # One file left out, so the box above the list shows its third state.
    dialog.set_ticked({"config.py", "invoices/numbering.py", "numbers.txt"})
    dialog.show()
    for _round in range(6):
        app.processEvents()
    written = [_save(dialog, theme, "commit-files")]

    files = [item for item in dialog.ticked() if item.restorable]
    risky = snapshot.at_risk(repo, [item.path for item in files])
    confirm = RestoreConfirmDialog(files, risky, commit.short, dialog)
    confirm.show()
    for _round in range(6):
        app.processEvents()
    written.append(_save(confirm, theme, "restore-confirm"))
    confirm.done(0)
    dialog.done(0)
    return written


def capture_blocks(theme: str, base: Path, repositories: list[Path]) -> Path:
    """
    Photographs the comparison with one block taken out of the commit.

    Args:
        theme: Theme to render.
        base: Sandbox directory.
        repositories: Demo projects, built once and reused.

    Returns:
        Path: The written file.
    """

    from gitops import diff as diff_mod

    app, window, entry = _demo_window(theme, base, repositories)
    window._changes._list.setCurrentRow(0)
    for _round in range(4):
        app.processEvents()

    parsed = diff_mod.file_diff(entry.path, "config.py", diff_mod.TARGET_WORKTREE_HEAD)
    if len(parsed.hunks) > 1:
        window._on_hunk_toggled(parsed.hunks[-1].key)
    for _round in range(6):
        app.processEvents()

    target_dir = output_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"blocks-{theme}.png"
    window.grab().copy(300, 120, 1100, 460).save(str(target))
    window._closing = True
    window.close()
    app.processEvents()
    return target


def capture_menus(theme: str, base: Path, repositories: list[Path]) -> list[Path]:
    """
    Photographs the two menus people look for things in.

    Args:
        theme: Theme to render.
        base: Sandbox directory.
        repositories: Demo projects, built once and reused.

    Returns:
        list[Path]: The written files.
    """

    from PySide6.QtWidgets import QMenu

    app, window, _entry = _demo_window(theme, base, repositories)
    written: list[Path] = []

    for title_key, name in (("menu.branch", "menu-branch"), ("menu.repository", "menu-project")):
        for menu in window.menuBar().findChildren(QMenu):
            if menu.title() != i18n.t(title_key):
                continue
            # A menu is photographed without being opened, so whatever it
            # would refresh on opening has to be done here.
            window._refresh_branch_menu()
            window._refresh_project_menu()
            menu.adjustSize()
            for _round in range(4):
                app.processEvents()
            written.append(_save(menu, theme, name))
            break

    window._closing = True
    window.close()
    app.processEvents()
    return written

def capture_all(language: str) -> list[Path]:
    """
    Produces every picture in one language.

    Args:
        language: Language code the window should run in.

    Returns:
        list[Path]: The files that were written.
    """

    i18n.set_language(language)
    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="branchly-shots-") as tmp:
        base = Path(tmp)
        repositories = build_demo_repositories(base)
        config_dir = base / "config"
        config_dir.mkdir()
        for theme in (THEME_DARK, THEME_LIGHT):
            written.append(capture(theme, repositories, config_dir))
            written.append(capture_github(theme, config_dir))
            written.append(capture_binary(theme, base))
            written.append(capture_discover(theme, base))
            written.append(capture_github_discover(theme, base))
            written.append(capture_conflict(theme, base))
            written.append(capture_signin(theme))
            written.append(capture_settings(theme))
            written.append(capture_pull_all(theme, base))
            written.append(capture_push_all(theme, base))
            written.append(capture_revert(theme, base))
            written.append(capture_link_remote(theme, base))
            written.append(capture_tags(theme, base))
            written.append(capture_gitignore(theme, base))
            written.extend(capture_commit_files(theme, base))
            written.append(capture_blocks(theme, base, repositories))
            written.extend(capture_menus(theme, base, repositories))
    return written


def main(argv: list[str] | None = None) -> int:
    """
    Generates every screenshot, in every language or in the ones named.

    Args:
        argv: Command line arguments. ``--language de`` narrows it down.

    Returns:
        int: Process exit code.
    """

    parser = argparse.ArgumentParser(description="Regenerate the manual's screenshots")
    parser.add_argument(
        "--language",
        action="append",
        metavar="CODE",
        help="only this language, may be given more than once",
    )
    args = parser.parse_args(argv)

    if shutil.which("git") is None:
        print("git is required to build the demo repositories", file=sys.stderr)
        return 2

    known = [code for code, _label in i18n.available_languages()]
    wanted = args.language or known
    unknown = [code for code in wanted if code not in known]
    if unknown:
        print(f"no such language: {', '.join(unknown)}", file=sys.stderr)
        return 2

    for language in wanted:
        for target in capture_all(language):
            print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
