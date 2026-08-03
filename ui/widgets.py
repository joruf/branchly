"""
Small reusable widgets.

Everything here reads its colors from the active theme's tokens. No widget in
Branchly hardcodes a hex value, which is what makes a new theme a matter of one
entry in ``config.theme`` rather than a hunt through the UI.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import ThemeColors, get_theme_colors

BADGE_ICONS: dict[str, str] = {
    "conflict": "!",
    "changed": "●",
    "incoming": "↓",
    "ahead": "↑",
}

BADGE_TOOLTIP_KEYS: dict[str, tuple[str, str]] = {
    "conflict": ("status.conflicts_one", "status.conflicts_many"),
    "changed": ("status.changes_one", "status.changes_many"),
    "incoming": ("status.behind_one", "status.behind_many"),
    "ahead": ("status.ahead_one", "status.ahead_many"),
}


def token_color(token: str, theme: str | None = None) -> str:
    """
    Resolves a theme token name to its color.

    Args:
        token: Attribute name on ``ThemeColors``.
        theme: Theme to read. Defaults to the active theme.

    Returns:
        str: Hex color, falling back to the primary text color for an unknown
            token so a typo shows up as plain text rather than as a crash.
    """

    colors = get_theme_colors(theme)
    return str(getattr(colors, token, colors.text))


class Badge(QLabel):
    """
    A small pill showing a count, coloured by what it means.
    """

    def __init__(self, kind: str, count: int, parent: QWidget | None = None) -> None:
        """
        Args:
            kind: Badge kind, one of the keys in ``BADGE_ICONS``.
            count: Number to show.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._kind = kind
        self._count = count
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.refresh()

    def refresh(self, theme: str | None = None) -> None:
        """
        Redraws the badge for the current theme.

        Args:
            theme: Theme to read. Defaults to the active theme.

        Returns:
            None
        """

        colors = get_theme_colors(theme)
        token = {
            "conflict": "status_conflict",
            "changed": "status_modified",
            "incoming": "info",
            "ahead": "warning",
        }.get(self._kind, "text_muted")
        foreground = token_color(token, theme)
        icon = BADGE_ICONS.get(self._kind, "")
        self.setText(f"{icon}{self._count}" if self._count else icon)
        singular, plural = BADGE_TOOLTIP_KEYS.get(self._kind, ("", ""))
        if singular:
            self.setToolTip(i18n.plural(self._count, singular, plural))
        self.setStyleSheet(
            f"color: {foreground};"
            f"background-color: {colors.surface_alt};"
            f"border: 1px solid {colors.border};"
            "border-radius: 7px;"
            "padding: 1px 6px;"
            "font-size: 11px;"
            "font-weight: 600;"
        )


class StatusDot(QLabel):
    """
    A coloured dot, used for check states and file kinds.
    """

    def __init__(self, token: str, diameter: int = 9, parent: QWidget | None = None) -> None:
        """
        Args:
            token: Theme token name for the fill colour.
            diameter: Dot size in pixels.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._token = token
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.refresh()

    def set_token(self, token: str) -> None:
        """
        Changes the colour.

        Args:
            token: Theme token name.

        Returns:
            None
        """

        self._token = token
        self.refresh()

    def refresh(self, theme: str | None = None) -> None:
        """
        Redraws the dot for the current theme.

        Args:
            theme: Theme to read. Defaults to the active theme.

        Returns:
            None
        """

        radius = self._diameter // 2
        self.setStyleSheet(
            f"background-color: {token_color(self._token, theme)};"
            f"border-radius: {radius}px;"
        )


class InlineMessage(QFrame):
    """
    A message strip with a title, an explanation and optional buttons.

    This is the shape every "something needs saying" moment in Branchly uses —
    empty states, warnings, failures — so the wording carries the weight rather
    than a modal dialog interrupting the user.
    """

    action_clicked = Signal(str)

    def __init__(
        self,
        title: str = "",
        detail: str = "",
        token: str = "info",
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            title: Headline.
            detail: Explanation in plain language.
            token: Theme token naming the accent colour.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._token = token
        self.setObjectName("InlineMessage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        self._title = QLabel(title)
        title_font = QFont(self._title.font())
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._title.setWordWrap(True)

        self._detail = QLabel(detail)
        self._detail.setObjectName("Muted")
        self._detail.setWordWrap(True)
        self._detail.setVisible(bool(detail))

        layout.addWidget(self._title)
        layout.addWidget(self._detail)

        self._buttons = QHBoxLayout()
        self._buttons.setContentsMargins(0, 6, 0, 0)
        self._buttons.setSpacing(8)
        self._buttons.addStretch(1)
        self._button_row = QWidget()
        self._button_row.setLayout(self._buttons)
        self._button_row.setVisible(False)
        layout.addWidget(self._button_row)

        self.refresh()

    def set_message(self, title: str, detail: str = "", token: str | None = None) -> None:
        """
        Replaces the text.

        Args:
            title: Headline.
            detail: Explanation.
            token: Theme token naming the accent colour, or None to keep it.

        Returns:
            None
        """

        self._title.setText(title)
        self._detail.setText(detail)
        self._detail.setVisible(bool(detail))
        if token is not None:
            self._token = token
        self.refresh()

    def add_action(self, label: str, action_id: str, primary: bool = False) -> QPushButton:
        """
        Adds a button to the strip.

        Args:
            label: Button text.
            action_id: Identifier emitted by ``action_clicked``.
            primary: Whether it is styled as the main action.

        Returns:
            QPushButton: The button, for callers that need to disable it later.
        """

        button = QPushButton(label)
        if primary:
            button.setObjectName("Primary")
        button.clicked.connect(lambda: self.action_clicked.emit(action_id))
        self._buttons.addWidget(button)
        self._button_row.setVisible(True)
        return button

    def clear_actions(self) -> None:
        """
        Removes every button.

        Returns:
            None
        """

        while self._buttons.count() > 1:
            item = self._buttons.takeAt(self._buttons.count() - 1)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._button_row.setVisible(False)

    def refresh(self, theme: str | None = None) -> None:
        """
        Redraws the strip for the current theme.

        Args:
            theme: Theme to read. Defaults to the active theme.

        Returns:
            None
        """

        colors = get_theme_colors(theme)
        background = {
            "info": colors.info_bg,
            "warning": colors.warning_bg,
            "danger": colors.danger_bg,
            "success": colors.success_bg,
        }.get(self._token, colors.surface_alt)
        accent = token_color(self._token, theme)
        self.setStyleSheet(
            f"QFrame#InlineMessage {{"
            f" background-color: {background};"
            f" border: 1px solid {accent};"
            f" border-radius: 6px;"
            f"}}"
        )


class EmptyState(QWidget):
    """
    Centred "there is nothing here yet" panel with an optional action.
    """

    action_clicked = Signal(str)

    def __init__(self, title: str = "", detail: str = "", parent: QWidget | None = None) -> None:
        """
        Args:
            title: Headline.
            detail: Explanation of how to get something here.
            parent: Parent widget.
        """

        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 40, 32, 40)
        layout.setSpacing(8)
        layout.addStretch(1)

        self._title = QLabel(title)
        self._title.setObjectName("Heading")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setWordWrap(True)

        self._detail = QLabel(detail)
        self._detail.setObjectName("Muted")
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setWordWrap(True)

        layout.addWidget(self._title)
        layout.addWidget(self._detail)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        self._actions.addStretch(1)
        self._actions.addStretch(1)
        holder = QWidget()
        holder.setLayout(self._actions)
        layout.addWidget(holder)
        layout.addStretch(1)

    def set_message(self, title: str, detail: str = "") -> None:
        """
        Replaces the text.

        Args:
            title: Headline.
            detail: Explanation.

        Returns:
            None
        """

        self._title.setText(title)
        self._detail.setText(detail)

    def add_action(self, label: str, action_id: str, primary: bool = False) -> QPushButton:
        """
        Adds a button below the text.

        Args:
            label: Button text.
            action_id: Identifier emitted by ``action_clicked``.
            primary: Whether it is styled as the main action.

        Returns:
            QPushButton: The button.
        """

        button = QPushButton(label)
        if primary:
            button.setObjectName("Primary")
        button.clicked.connect(lambda: self.action_clicked.emit(action_id))
        self._actions.insertWidget(self._actions.count() - 1, button)
        return button


class SectionHeader(QFrame):
    """
    A panel header holding a title and a row of controls.
    """

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        """
        Args:
            title: Header text.
            parent: Parent widget.
        """

        super().__init__(parent)
        self.setObjectName("PanelHeader")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(8)

        self._title = QLabel(title)
        title_font = QFont(self._title.font())
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._layout.addWidget(self._title)
        self._layout.addStretch(1)

    def set_title(self, title: str) -> None:
        """
        Replaces the header text.

        Args:
            title: New text.

        Returns:
            None
        """

        self._title.setText(title)

    def add_widget(self, widget: QWidget) -> None:
        """
        Adds a control to the right-hand side.

        Args:
            widget: Widget to add.

        Returns:
            None
        """

        self._layout.addWidget(widget)


def format_relative_check_time(checked_at: float | None, now: float) -> str:
    """
    Describes how long ago a scan happened, in words.

    Args:
        checked_at: Unix timestamp of the last scan, or None.
        now: Current Unix timestamp.

    Returns:
        str: Translated phrase such as "checked 5 min ago".
    """

    if checked_at is None:
        return i18n.t("sidebar.never_checked")
    seconds = max(0.0, now - checked_at)
    if seconds < 90:
        return i18n.t("sidebar.checked_just_now")
    minutes = int(seconds // 60)
    if minutes < 60:
        return i18n.t("sidebar.checked_minutes", count=minutes)
    hours = int(minutes // 60)
    if hours < 24:
        return i18n.t("sidebar.checked_hours", count=hours)
    return i18n.t("sidebar.checked_days", count=int(hours // 24))


def apply_monospace(widget: QWidget, point_size_delta: int = 0) -> None:
    """
    Switches a widget to the platform's fixed-width font.

    Args:
        widget: Widget to restyle.
        point_size_delta: Adjustment to the inherited font size.

    Returns:
        None
    """

    font = QFont("monospace")
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setFixedPitch(True)
    base = widget.font().pointSize()
    if base > 0:
        font.setPointSize(max(7, base + point_size_delta))
    widget.setFont(font)


def refresh_theme_aware(root: QWidget, theme: str | None = None) -> None:
    """
    Tells every themed child widget to redraw itself.

    Widgets that carry their own inline stylesheet cannot be reached by the
    application-wide sheet, so they are refreshed explicitly when the theme
    changes.

    Args:
        root: Widget to walk.
        theme: Theme to apply. Defaults to the active theme.

    Returns:
        None
    """

    for child in root.findChildren(QWidget):
        refresh = getattr(child, "refresh", None)
        if callable(refresh):
            try:
                refresh(theme)
            except TypeError:
                refresh()


def theme_colors() -> ThemeColors:
    """
    Returns the active theme's tokens.

    A thin shim so widgets do not each import from ``config.theme``.

    Returns:
        ThemeColors: Active colour tokens.
    """

    return get_theme_colors()
