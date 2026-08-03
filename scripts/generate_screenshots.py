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
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import i18n  # noqa: E402
from config.app_settings import AppSettings  # noqa: E402
from config.theme import THEME_DARK, THEME_LIGHT, build_application_stylesheet, set_current_theme  # noqa: E402

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

    registry = window._registry  # noqa: SLF001 - the demo needs a populated registry
    registry.add_category("Arbeit")
    registry.add_category("Privat")
    for index, path in enumerate(repositories):
        _outcome, entry = registry.add(path, "Arbeit" if index == 0 else "Privat")
        if entry is not None and index == 0:
            entry.favorite = True
    window._sidebar.refresh()  # noqa: SLF001

    first = registry.entries[0]
    window._sidebar.select_key(first.key)  # noqa: SLF001
    window._activate(first)  # noqa: SLF001
    window._changes._list.setCurrentRow(0)  # noqa: SLF001
    window.show()
    app.processEvents()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"main-window-{theme}.png"
    window.grab().save(str(target))

    # The graph tab, which is the other view worth showing.
    window._tabs.setCurrentIndex(1)  # noqa: SLF001
    app.processEvents()
    graph_target = OUTPUT_DIR / f"graph-{theme}.png"
    window.grab().save(str(graph_target))

    window.close()
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
    dialog._show_current_decision()  # noqa: SLF001
    dialog.show()
    app.processEvents()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"conflict-assistant-{theme}.png"
    dialog.grab().save(str(target))
    dialog.close()
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
            print(f"wrote {capture_conflict(theme, base)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
