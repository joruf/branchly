"""
Theme definitions and Qt Style Sheet generation for Branchly.

Adding a theme means adding one ``ThemeColors`` instance plus one entry in
``_THEMES`` — no other module needs to change, because every widget reads its
colors from these tokens instead of hardcoding hex values.
"""

from __future__ import annotations

from dataclasses import dataclass

THEME_DARK = "dark"
THEME_LIGHT = "light"
DEFAULT_THEME = THEME_DARK

_current_theme = DEFAULT_THEME


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """
    Defines every color token used by Branchly.

    Attributes:
        window_bg: Main window background.
        surface: Panel and control background.
        surface_alt: Secondary surface (headers, toolbars, gutters).
        surface_raised: Popups, tooltips and dialogs sitting above a panel.
        text: Primary text color.
        text_muted: Secondary label text color.
        text_inverted: Text drawn on top of ``accent``.
        border: Default border color.
        border_strong: Emphasized border color.
        accent: Primary action and selection color.
        accent_hover: Hover state for accent controls.
        button_bg: Default button background.
        button_hover: Default button hover background.
        button_pressed: Default button pressed background.
        disabled_bg: Background of a control that cannot be used right now.
        disabled_text: Text of a control that cannot be used right now. Dimmer
            than ``text_muted`` on purpose: muted text is still something to
            read, this is something to skip over.
        disabled_border: Border of a control that cannot be used right now.
        input_bg: Text fields, spin boxes and search inputs.
        dropdown_bg: Combo box popup background.
        link: Link-style text color.
        link_hover: Link-style hover text color.
        scrollbar_bg: Scrollbar track background.
        scrollbar_handle: Scrollbar handle color.
        scrollbar_handle_hover: Scrollbar handle hover color.
        selection_bg: Selected row background in lists and trees.
        selection_text: Selected row text color.
        row_hover: Hovered row background in lists and trees.
        success: Positive state (pushed, checks passed).
        success_bg: Soft background for positive badges.
        warning: Attention state (unpushed commits, stale data).
        warning_bg: Soft background for attention badges.
        danger: Destructive or failed state.
        danger_bg: Soft background for destructive badges.
        info: Neutral informational state (incoming commits).
        info_bg: Soft background for informational badges.
        diff_added_bg: Background of an added diff line.
        diff_added_gutter: Line-number gutter of an added diff line.
        diff_removed_bg: Background of a removed diff line.
        diff_removed_gutter: Line-number gutter of a removed diff line.
        diff_word_added_bg: Intra-line highlight for inserted words.
        diff_word_removed_bg: Intra-line highlight for deleted words.
        diff_header_bg: Hunk header (``@@ ... @@``) background.
        diff_header_text: Hunk header text color.
        diff_gutter_bg: Line-number gutter background for context lines.
        diff_gutter_text: Line-number text color.
        diff_context_text: Unchanged diff line text color.
        diff_context_quiet: Unchanged diff line text color while the panel is
            showing the surrounding text as well. Paler than
            ``diff_context_text``: with twenty lines of it around every change,
            the unchanged part has to stay readable without competing with the
            change itself.
        status_modified: Icon/label color for modified files.
        status_added: Icon/label color for added files.
        status_deleted: Icon/label color for deleted files.
        status_renamed: Icon/label color for renamed or copied files.
        status_untracked: Icon/label color for untracked files.
        status_conflict: Icon/label color for conflicted files.
        graph_lanes: Cycled lane colors for the commit graph. Chromatic on
            purpose — lanes must be told apart at a glance.
        graph_edge: Fallback edge color when a lane index is unavailable.
        graph_node_border: Outline around a commit dot.
        graph_head_ring: Ring drawn around the currently checked-out commit.
        conflict_ours_bg: Background of the "your change" column.
        conflict_theirs_bg: Background of the "from the server" column.
        conflict_result_bg: Background of the result column.
        conflict_chosen_border: Border of the side currently chosen.
        check_pass: CI check succeeded.
        check_fail: CI check failed.
        check_pending: CI check queued or running.
        check_neutral: CI check skipped or neutral.
    """

    window_bg: str
    surface: str
    surface_alt: str
    surface_raised: str
    text: str
    text_muted: str
    text_inverted: str
    border: str
    border_strong: str
    accent: str
    accent_hover: str
    button_bg: str
    button_hover: str
    button_pressed: str
    disabled_bg: str
    disabled_text: str
    disabled_border: str
    input_bg: str
    dropdown_bg: str
    link: str
    link_hover: str
    scrollbar_bg: str
    scrollbar_handle: str
    scrollbar_handle_hover: str
    selection_bg: str
    selection_text: str
    row_hover: str
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str
    info: str
    info_bg: str
    diff_added_bg: str
    diff_added_gutter: str
    diff_removed_bg: str
    diff_removed_gutter: str
    diff_word_added_bg: str
    diff_word_removed_bg: str
    diff_header_bg: str
    diff_header_text: str
    diff_gutter_bg: str
    diff_gutter_text: str
    diff_context_text: str
    diff_context_quiet: str
    status_modified: str
    status_added: str
    status_deleted: str
    status_renamed: str
    status_untracked: str
    status_conflict: str
    graph_lanes: tuple[str, ...]
    graph_edge: str
    graph_node_border: str
    graph_head_ring: str
    conflict_ours_bg: str
    conflict_theirs_bg: str
    conflict_result_bg: str
    conflict_chosen_border: str
    check_pass: str
    check_fail: str
    check_pending: str
    check_neutral: str


_DARK_COLORS = ThemeColors(
    window_bg="#1f2430",
    surface="#242833",
    surface_alt="#222938",
    surface_raised="#2a3040",
    text="#e7ecf2",
    text_muted="#9fb2c9",
    text_inverted="#ffffff",
    border="#3e4657",
    border_strong="#434d63",
    accent="#2f7dd1",
    accent_hover="#4591e4",
    button_bg="#2f3543",
    button_hover="#3a4357",
    button_pressed="#272d3a",
    disabled_bg="#272c38",
    disabled_text="#6d7f97",
    disabled_border="#333b4b",
    input_bg="#2f3543",
    dropdown_bg="#2a3040",
    link="#78b8ff",
    link_hover="#a9d1ff",
    scrollbar_bg="#2a3040",
    scrollbar_handle="#434d63",
    scrollbar_handle_hover="#55617a",
    selection_bg="#31517a",
    selection_text="#ffffff",
    row_hover="#2c3343",
    success="#4ec98a",
    success_bg="#1e3a2c",
    warning="#e0b341",
    warning_bg="#3b3320",
    danger="#e8705f",
    danger_bg="#3d2521",
    info="#5aa9e6",
    info_bg="#1e3040",
    diff_added_bg="#1c3326",
    diff_added_gutter="#24462f",
    diff_removed_bg="#3a2320",
    diff_removed_gutter="#4c2b26",
    diff_word_added_bg="#2f6b45",
    diff_word_removed_bg="#7a3a30",
    diff_header_bg="#2a3040",
    diff_header_text="#9fb2c9",
    diff_gutter_bg="#222938",
    diff_gutter_text="#6d7f97",
    diff_context_text="#c3cedd",
    diff_context_quiet="#7b8798",
    status_modified="#e0b341",
    status_added="#4ec98a",
    status_deleted="#e8705f",
    status_renamed="#5aa9e6",
    status_untracked="#9fb2c9",
    status_conflict="#e8705f",
    graph_lanes=(
        "#5aa9e6",
        "#4ec98a",
        "#e0b341",
        "#c98ae0",
        "#e8705f",
        "#54c8c8",
        "#b0c95a",
        "#e69a5a",
    ),
    graph_edge="#55617a",
    graph_node_border="#1f2430",
    graph_head_ring="#e7ecf2",
    conflict_ours_bg="#1e3040",
    conflict_theirs_bg="#3b3320",
    conflict_result_bg="#1e3a2c",
    conflict_chosen_border="#4ec98a",
    check_pass="#4ec98a",
    check_fail="#e8705f",
    check_pending="#e0b341",
    check_neutral="#9fb2c9",
)

_LIGHT_COLORS = ThemeColors(
    window_bg="#f5f6f8",
    surface="#ffffff",
    surface_alt="#eef1f5",
    surface_raised="#ffffff",
    text="#1e293b",
    text_muted="#64748b",
    text_inverted="#ffffff",
    border="#cbd5e1",
    border_strong="#94a3b8",
    accent="#2563eb",
    accent_hover="#1d4ed8",
    button_bg="#e2e8f0",
    button_hover="#cbd5e1",
    button_pressed="#b6c2d2",
    disabled_bg="#eef1f5",
    disabled_text="#94a3b8",
    disabled_border="#dde3ea",
    input_bg="#ffffff",
    dropdown_bg="#ffffff",
    link="#2563eb",
    link_hover="#1d4ed8",
    scrollbar_bg="#eef1f5",
    scrollbar_handle="#cbd5e1",
    scrollbar_handle_hover="#94a3b8",
    selection_bg="#cfe0fb",
    selection_text="#12213b",
    row_hover="#eef1f5",
    success="#16794a",
    success_bg="#dcf5e7",
    warning="#8a5a06",
    warning_bg="#fbf0d5",
    danger="#b02a1c",
    danger_bg="#fbdfda",
    info="#1a5fa8",
    info_bg="#dbeafe",
    diff_added_bg="#e3f7ea",
    diff_added_gutter="#c7ebd5",
    diff_removed_bg="#fbe4e0",
    diff_removed_gutter="#f2c9c2",
    diff_word_added_bg="#a5e3bf",
    diff_word_removed_bg="#f4b3a8",
    diff_header_bg="#eef1f5",
    diff_header_text="#64748b",
    diff_gutter_bg="#f2f4f7",
    diff_gutter_text="#8b97a8",
    diff_context_text="#33415a",
    diff_context_quiet="#95a1b3",
    status_modified="#8a5a06",
    status_added="#16794a",
    status_deleted="#b02a1c",
    status_renamed="#1a5fa8",
    status_untracked="#64748b",
    status_conflict="#b02a1c",
    graph_lanes=(
        "#1a5fa8",
        "#16794a",
        "#8a5a06",
        "#7b3fa0",
        "#b02a1c",
        "#0f7a7a",
        "#5c7a10",
        "#a85a12",
    ),
    graph_edge="#94a3b8",
    graph_node_border="#ffffff",
    graph_head_ring="#1e293b",
    conflict_ours_bg="#dbeafe",
    conflict_theirs_bg="#fbf0d5",
    conflict_result_bg="#dcf5e7",
    conflict_chosen_border="#16794a",
    check_pass="#16794a",
    check_fail="#b02a1c",
    check_pending="#8a5a06",
    check_neutral="#64748b",
)

_THEMES: dict[str, ThemeColors] = {
    THEME_DARK: _DARK_COLORS,
    THEME_LIGHT: _LIGHT_COLORS,
}

_LIGHT_FAMILY_THEMES = frozenset({THEME_LIGHT})

VALID_THEMES = frozenset(_THEMES)


def available_themes() -> tuple[str, ...]:
    """
    Returns every selectable theme name in display order.

    Returns:
        tuple[str, ...]: Theme identifiers.
    """

    return tuple(_THEMES)


def normalize_theme_name(theme_name: str | None) -> str:
    """
    Maps arbitrary input onto a known theme name.

    Args:
        theme_name: Candidate theme name, possibly from a stale config file.

    Returns:
        str: A valid theme name, falling back to the default.
    """

    if isinstance(theme_name, str):
        candidate = theme_name.strip().lower()
        if candidate in _THEMES:
            return candidate
    return DEFAULT_THEME


def set_current_theme(theme_name: str | None) -> str:
    """
    Sets the process-wide active theme.

    Args:
        theme_name: Theme to activate.

    Returns:
        str: The theme name that is now active.
    """

    global _current_theme
    _current_theme = normalize_theme_name(theme_name)
    return _current_theme


def current_theme_name() -> str:
    """
    Returns the active theme name.

    Returns:
        str: Active theme identifier.
    """

    return _current_theme


def get_theme_colors(theme_name: str | None = None) -> ThemeColors:
    """
    Returns the color tokens of a theme.

    Args:
        theme_name: Theme to read. Defaults to the active theme.

    Returns:
        ThemeColors: Color tokens.
    """

    return _THEMES[normalize_theme_name(theme_name or _current_theme)]


def is_light_theme(theme_name: str | None = None) -> bool:
    """
    Returns whether a theme belongs to the light family.

    Useful for the few places that need to pick an icon variant rather than a
    color, where a token would not help.

    Args:
        theme_name: Theme to check. Defaults to the active theme.

    Returns:
        bool: True for light themes.
    """

    return normalize_theme_name(theme_name or _current_theme) in _LIGHT_FAMILY_THEMES


def lane_color(lane_index: int, theme_name: str | None = None) -> str:
    """
    Returns the graph color for a lane, cycling when lanes outnumber colors.

    Args:
        lane_index: Zero-based lane index.
        theme_name: Theme to read. Defaults to the active theme.

    Returns:
        str: Hex color for the lane.
    """

    colors = get_theme_colors(theme_name).graph_lanes
    if not colors:
        return get_theme_colors(theme_name).graph_edge
    return colors[max(0, lane_index) % len(colors)]


def build_application_stylesheet(theme_name: str | None = None) -> str:
    """
    Builds the global Qt Style Sheet for the whole application.

    Args:
        theme_name: Theme to render. Defaults to the active theme.

    Returns:
        str: Qt Style Sheet source.
    """

    c = get_theme_colors(theme_name)
    return f"""
QWidget {{
    background-color: {c.window_bg};
    color: {c.text};
}}
QMainWindow, QDialog {{
    background-color: {c.window_bg};
}}
QFrame#Panel, QWidget#Panel {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 6px;
}}
QFrame#PanelHeader, QWidget#PanelHeader {{
    background-color: {c.surface_alt};
    border: none;
    border-bottom: 1px solid {c.border};
}}
QLabel {{
    background: transparent;
}}
QLabel#Muted {{
    color: {c.text_muted};
}}
QLabel#Heading {{
    font-size: 14px;
    font-weight: 600;
}}
QToolTip {{
    background-color: {c.surface_raised};
    color: {c.text};
    border: 1px solid {c.border_strong};
    padding: 4px 6px;
}}
QPushButton {{
    background-color: {c.button_bg};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: 5px;
    padding: 6px 12px;
}}
QPushButton:hover {{
    background-color: {c.button_hover};
    border-color: {c.border_strong};
}}
QPushButton:pressed {{
    background-color: {c.button_pressed};
}}
QPushButton:disabled {{
    color: {c.disabled_text};
    background-color: {c.disabled_bg};
    border-color: {c.disabled_border};
}}
QPushButton#Primary {{
    background-color: {c.accent};
    color: {c.text_inverted};
    border: 1px solid {c.accent};
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background-color: {c.accent_hover};
    border-color: {c.accent_hover};
}}
QPushButton#Primary:disabled {{
    background-color: {c.disabled_bg};
    color: {c.disabled_text};
    border: 1px solid {c.disabled_border};
}}
QPushButton#Danger {{
    background-color: {c.danger};
    color: {c.text_inverted};
    border: 1px solid {c.danger};
    font-weight: 600;
}}
QPushButton#Danger:disabled {{
    background-color: {c.disabled_bg};
    color: {c.disabled_text};
    border: 1px solid {c.disabled_border};
}}
QPushButton#Link {{
    background: transparent;
    border: none;
    color: {c.link};
    padding: 2px 4px;
    text-align: left;
}}
QPushButton#Link:hover {{
    color: {c.link_hover};
    text-decoration: underline;
}}
QPushButton#Link:disabled {{
    color: {c.disabled_text};
    text-decoration: none;
}}
QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px;
}}
QToolButton:hover {{
    background-color: {c.button_hover};
    border-color: {c.border};
}}
QToolButton:checked {{
    background-color: {c.accent};
    color: {c.text_inverted};
    border-color: {c.accent};
}}
QToolButton:disabled {{
    color: {c.disabled_text};
    background-color: transparent;
    border-color: transparent;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background-color: {c.input_bg};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: 5px;
    padding: 5px 7px;
    selection-background-color: {c.selection_bg};
    selection-color: {c.selection_text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {c.accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QSpinBox:disabled, QComboBox:disabled {{
    background-color: {c.disabled_bg};
    color: {c.disabled_text};
    border-color: {c.disabled_border};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {c.dropdown_bg};
    color: {c.text};
    border: 1px solid {c.border_strong};
    selection-background-color: {c.selection_bg};
    selection-color: {c.selection_text};
    outline: none;
}}
QTreeView, QListView, QTableView {{
    background-color: {c.surface};
    alternate-background-color: {c.surface_alt};
    color: {c.text};
    border: 1px solid {c.border};
    border-radius: 6px;
    outline: none;
}}
QTreeView::item, QListView::item {{
    padding: 4px 2px;
    border-radius: 4px;
}}
QTreeView::item:hover, QListView::item:hover {{
    background-color: {c.row_hover};
}}
QTreeView::item:selected, QListView::item:selected {{
    background-color: {c.selection_bg};
    color: {c.selection_text};
}}
QHeaderView::section {{
    background-color: {c.surface_alt};
    color: {c.text_muted};
    border: none;
    border-bottom: 1px solid {c.border};
    padding: 6px 8px;
}}
QTabWidget::pane {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 6px;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {c.text_muted};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 7px 14px;
}}
QTabBar::tab:hover {{
    color: {c.text};
}}
QTabBar::tab:selected {{
    color: {c.text};
    border-bottom-color: {c.accent};
    font-weight: 600;
}}
QTabBar::tab:disabled {{
    color: {c.disabled_text};
}}
QSplitter::handle {{
    background-color: {c.border};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}
QScrollBar:vertical {{
    background-color: {c.scrollbar_bg};
    width: 11px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {c.scrollbar_handle};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {c.scrollbar_handle_hover};
}}
QScrollBar:horizontal {{
    background-color: {c.scrollbar_bg};
    height: 11px;
    margin: 0;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {c.scrollbar_handle};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {c.scrollbar_handle_hover};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QCheckBox, QRadioButton {{
    background: transparent;
    spacing: 7px;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid {c.border_strong};
    background-color: {c.input_bg};
}}
QCheckBox::indicator {{
    border-radius: 3px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {c.accent};
    border-color: {c.accent};
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {c.disabled_text};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background-color: {c.disabled_bg};
    border-color: {c.disabled_border};
}}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background-color: {c.disabled_border};
    border-color: {c.disabled_border};
}}
QLabel:disabled {{
    color: {c.disabled_text};
}}
QMenu {{
    background-color: {c.surface_raised};
    color: {c.text};
    border: 1px solid {c.border_strong};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 20px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {c.selection_bg};
    color: {c.selection_text};
}}
QMenu::item:disabled {{
    color: {c.disabled_text};
}}
QMenu::separator {{
    height: 1px;
    background-color: {c.border};
    margin: 4px 6px;
}}
QMenuBar {{
    background-color: {c.surface_alt};
    color: {c.text};
    border-bottom: 1px solid {c.border};
}}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background-color: {c.button_hover};
    border-radius: 4px;
}}
QMenuBar::item:disabled {{
    color: {c.disabled_text};
}}
QProgressBar {{
    background-color: {c.surface_alt};
    border: 1px solid {c.border};
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: {c.text_muted};
}}
QProgressBar::chunk {{
    background-color: {c.accent};
    border-radius: 4px;
}}
QStatusBar {{
    background-color: {c.surface_alt};
    color: {c.text_muted};
    border-top: 1px solid {c.border};
}}
QGraphicsView#GraphView {{
    background-color: {c.surface};
    border: 1px solid {c.border};
    border-radius: 6px;
}}
"""
