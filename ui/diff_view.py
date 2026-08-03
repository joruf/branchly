"""
The comparison panel.

The rendering is deliberately split out as plain functions that turn a parsed
diff into HTML. That keeps the interesting part — line pairing, word-level
highlighting, escaping — testable without starting a GUI, and it makes the widget
itself a thin shell around a text view.

HTML rather than a custom-painted widget because a diff is exactly what markup is
good at: two nested colours per line, a gutter, and a monospace body.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.app_settings import DIFF_SIDE_BY_SIDE, DIFF_UNIFIED, normalize_diff_mode
from config.theme import ThemeColors, get_theme_colors
from constants import DIFF_MAX_LINE_LENGTH
from gitops.diff import (
    IMAGE_SUFFIXES,
    LINE_ADDED,
    LINE_CONTEXT,
    LINE_NO_NEWLINE,
    LINE_REMOVED,
    TARGET_LABEL_KEYS,
    TARGET_WORKTREE_HEAD,
    VALID_TARGETS,
    DiffHunk,
    DiffLine,
    FileDiff,
    pair_lines,
)
from ui.widgets import EmptyState, InlineMessage, apply_monospace

TAB_WIDTH = 4


def _escape(text: str) -> str:
    """
    Escapes text for HTML and makes tabs visible.

    Args:
        text: Raw line content.

    Returns:
        str: HTML-safe text with tabs expanded.
    """

    return html.escape(text.replace("\t", " " * TAB_WIDTH), quote=False)


def _with_spans(text: str, spans: list[tuple[int, int]], color: str) -> str:
    """
    Wraps the changed parts of a line in highlight markup.

    Args:
        text: Raw line content.
        spans: Character ranges to highlight, as ``(start, end)`` pairs.
        color: Background colour for the highlighted ranges.

    Returns:
        str: HTML-safe text with the ranges wrapped.
    """

    if not spans:
        return _escape(text)

    ordered = sorted((max(0, start), min(len(text), end)) for start, end in spans if end > start)
    pieces: list[str] = []
    cursor = 0
    for start, end in ordered:
        if start < cursor:
            start = cursor
        if start >= end:
            continue
        pieces.append(_escape(text[cursor:start]))
        pieces.append(
            f'<span style="background-color:{color};border-radius:2px;">{_escape(text[start:end])}</span>'
        )
        cursor = end
    pieces.append(_escape(text[cursor:]))
    return "".join(pieces)


def _truncate(text: str) -> tuple[str, bool]:
    """
    Shortens an absurdly long line so the view stays usable.

    Args:
        text: Raw line content.

    Returns:
        tuple[str, bool]: Possibly shortened text, and whether it was cut.
    """

    if len(text) <= DIFF_MAX_LINE_LENGTH:
        return text, False
    return text[:DIFF_MAX_LINE_LENGTH], True


def _line_styles(kind: str, colors: ThemeColors) -> tuple[str, str, str]:
    """
    Returns the colours for one kind of diff line.

    Args:
        kind: Line kind constant.
        colors: Active theme tokens.

    Returns:
        tuple[str, str, str]: Row background, gutter background, text colour.
    """

    if kind == LINE_ADDED:
        return colors.diff_added_bg, colors.diff_added_gutter, colors.text
    if kind == LINE_REMOVED:
        return colors.diff_removed_bg, colors.diff_removed_gutter, colors.text
    if kind == LINE_NO_NEWLINE:
        return colors.surface_alt, colors.diff_gutter_bg, colors.text_muted
    return "transparent", colors.diff_gutter_bg, colors.diff_context_text


def _document_head(colors: ThemeColors) -> str:
    """
    Builds the shared style block for a rendered diff.

    Args:
        colors: Active theme tokens.

    Returns:
        str: HTML style element.
    """

    return (
        "<style>"
        f"body {{ background-color:{colors.surface}; color:{colors.text}; margin:0; }}"
        "table { border-collapse:collapse; width:100%; }"
        "td { padding:0 6px; vertical-align:top; white-space:pre; }"
        f"td.gutter {{ text-align:right; width:1%; color:{colors.diff_gutter_text};"
        f" background-color:{colors.diff_gutter_bg}; -qt-user-state:0; }}"
        f"tr.hunk td {{ background-color:{colors.diff_header_bg}; color:{colors.diff_header_text};"
        " padding:3px 6px; }"
        "</style>"
    )


def _hunk_header_row(hunk: DiffHunk, columns: int) -> str:
    """
    Builds the row that introduces a hunk.

    Args:
        hunk: Hunk being rendered.
        columns: Number of table columns to span.

    Returns:
        str: HTML table row.
    """

    heading = _escape(hunk.heading.strip())
    label = f"@@ −{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
    suffix = f"  {heading}" if heading else ""
    return f'<tr class="hunk"><td colspan="{columns}">{_escape(label)}{suffix}</td></tr>'


def _cell(line: DiffLine | None, colors: ThemeColors, word_level: bool) -> str:
    """
    Builds one side of a side-by-side row.

    Args:
        line: Line to render, or None for a blank side.
        colors: Active theme tokens.
        word_level: Whether to apply intra-line highlighting.

    Returns:
        str: Two HTML table cells: gutter and content.
    """

    if line is None:
        return f'<td class="gutter" style="background-color:{colors.diff_gutter_bg};"></td><td></td>'

    background, gutter, foreground = _line_styles(line.kind, colors)
    number = line.old_lineno if line.kind in {LINE_REMOVED} else line.new_lineno
    if line.kind == LINE_CONTEXT:
        number = line.old_lineno
    text, cut = _truncate(line.text)
    highlight = colors.diff_word_added_bg if line.kind == LINE_ADDED else colors.diff_word_removed_bg
    body = _with_spans(text, line.spans if word_level else [], highlight)
    if cut:
        body += f'<span style="color:{colors.text_muted};"> …</span>'
    return (
        f'<td class="gutter" style="background-color:{gutter};">{number if number else ""}</td>'
        f'<td style="background-color:{background};color:{foreground};">{body}</td>'
    )


def render_side_by_side(diff: FileDiff, colors: ThemeColors, word_level: bool = True) -> str:
    """
    Renders a diff as two columns.

    Args:
        diff: Parsed diff.
        colors: Active theme tokens.
        word_level: Whether to apply intra-line highlighting.

    Returns:
        str: Complete HTML document.
    """

    rows: list[str] = []
    for hunk in diff.hunks:
        rows.append(_hunk_header_row(hunk, 4))
        for left, right in pair_lines(hunk):
            rows.append(f"<tr>{_cell(left, colors, word_level)}{_cell(right, colors, word_level)}</tr>")
    return f"{_document_head(colors)}<table>{''.join(rows)}</table>"


def render_unified(diff: FileDiff, colors: ThemeColors, word_level: bool = True) -> str:
    """
    Renders a diff as one column with both line numbers.

    Args:
        diff: Parsed diff.
        colors: Active theme tokens.
        word_level: Whether to apply intra-line highlighting.

    Returns:
        str: Complete HTML document.
    """

    rows: list[str] = []
    for hunk in diff.hunks:
        rows.append(_hunk_header_row(hunk, 4))
        for line in hunk.lines:
            background, gutter, foreground = _line_styles(line.kind, colors)
            marker = {LINE_ADDED: "+", LINE_REMOVED: "−", LINE_NO_NEWLINE: "\\"}.get(line.kind, " ")
            text, cut = _truncate(line.text)
            highlight = (
                colors.diff_word_added_bg if line.kind == LINE_ADDED else colors.diff_word_removed_bg
            )
            body = _with_spans(text, line.spans if word_level else [], highlight)
            if cut:
                body += f'<span style="color:{colors.text_muted};"> …</span>'
            rows.append(
                "<tr>"
                f'<td class="gutter" style="background-color:{gutter};">{line.old_lineno or ""}</td>'
                f'<td class="gutter" style="background-color:{gutter};">{line.new_lineno or ""}</td>'
                f'<td style="background-color:{background};color:{foreground};width:1%;">{marker}</td>'
                f'<td style="background-color:{background};color:{foreground};">{body}</td>'
                "</tr>"
            )
    return f"{_document_head(colors)}<table>{''.join(rows)}</table>"


def render_many(diffs: list[FileDiff], mode: str, colors: ThemeColors, word_level: bool = True) -> str:
    """
    Renders several files as one scrollable document.

    Args:
        diffs: Parsed diffs, in the order they should appear.
        mode: ``side_by_side`` or ``unified``.
        colors: Active theme tokens.
        word_level: Whether to apply intra-line highlighting.

    Returns:
        str: Complete HTML document.
    """

    if not diffs:
        return _document_head(colors)

    columns = 4
    blocks: list[str] = []
    for diff in diffs:
        name = diff.path or diff.old_path or "?"
        counts = f"+{diff.added} −{diff.removed}"
        blocks.append(
            '<table><tr><td colspan="{columns}" style="background-color:{bg};color:{fg};'
            'padding:6px;font-weight:bold;">{name}'
            '<span style="color:{muted};font-weight:normal;">  {counts}</span></td></tr>'.format(
                columns=columns,
                bg=colors.surface_alt,
                fg=colors.text,
                muted=colors.text_muted,
                name=_escape(name),
                counts=_escape(counts),
            )
        )
        if diff.binary:
            blocks.append(
                f'<tr><td colspan="{columns}" style="color:{colors.text_muted};padding:6px;">'
                f"{_escape(i18n.t('diff.binary'))}</td></tr></table>"
            )
            continue
        body = render_diff(diff, mode, colors, word_level)
        # Reuse the per-file renderer but drop its own head and outer table tags.
        inner = body.split("<table>", 1)[-1].rsplit("</table>", 1)[0]
        blocks.append(f"{inner}</table>")
    return f"{_document_head(colors)}{''.join(blocks)}"


def render_diff(diff: FileDiff, mode: str, colors: ThemeColors, word_level: bool = True) -> str:
    """
    Renders a diff in the requested layout.

    Args:
        diff: Parsed diff.
        mode: ``side_by_side`` or ``unified``.
        colors: Active theme tokens.
        word_level: Whether to apply intra-line highlighting.

    Returns:
        str: Complete HTML document.
    """

    if normalize_diff_mode(mode) == DIFF_UNIFIED:
        return render_unified(diff, colors, word_level)
    return render_side_by_side(diff, colors, word_level)


class DiffView(QWidget):
    """
    Shows the difference between two states of one file.

    Attributes:
        options_changed: Emitted when the user changed a display option, so the
            main window can persist it.
        target_changed: Emitted with the newly chosen comparison target.
        reload_requested: Emitted when the current diff should be produced again.
        open_file_requested: Emitted with the current path.
        reveal_file_requested: Emitted with the current path.
    """

    options_changed = Signal()
    target_changed = Signal(str)
    reload_requested = Signal()
    open_file_requested = Signal(str)
    reveal_file_requested = Signal(str)

    def __init__(
        self,
        mode: str,
        ignore_whitespace: bool,
        word_level: bool,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            mode: Initial layout.
            ignore_whitespace: Whether whitespace-only changes start hidden.
            word_level: Whether intra-line highlighting starts on.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._mode = normalize_diff_mode(mode)
        self._diff: FileDiff | None = None
        self._many: list[FileDiff] = []
        self._path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_toolbar(ignore_whitespace, word_level))

        self._stack = QStackedWidget(self)

        self._empty = EmptyState(i18n.t("diff.no_selection"), "", self)
        self._stack.addWidget(self._empty)

        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(False)
        self._browser.setOpenLinks(False)
        self._browser.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        apply_monospace(self._browser, -1)
        self._stack.addWidget(self._browser)

        self._notice_holder = QWidget(self)
        notice_layout = QVBoxLayout(self._notice_holder)
        notice_layout.setContentsMargins(16, 16, 16, 16)
        self._notice = InlineMessage("", "", "info", self._notice_holder)
        notice_layout.addWidget(self._notice)
        notice_layout.addStretch(1)
        self._stack.addWidget(self._notice_holder)

        self._images = self._build_image_view()
        self._stack.addWidget(self._images)

        layout.addWidget(self._stack, 1)
        self._stack.setCurrentWidget(self._empty)

    def _build_image_view(self) -> QWidget:
        """
        Builds the two-panel preview used for image files.

        Returns:
            QWidget: Scrollable holder with a "before" and an "after" panel.
        """

        area = QScrollArea(self)
        area.setWidgetResizable(True)
        holder = QWidget(area)
        row = QHBoxLayout(holder)
        row.setContentsMargins(16, 16, 16, 16)
        row.setSpacing(16)

        self._image_before_caption = QLabel(i18n.t("diff.image_before"), holder)
        self._image_before_caption.setObjectName("Muted")
        self._image_before = QLabel(holder)
        self._image_before.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._image_after_caption = QLabel(i18n.t("diff.image_after"), holder)
        self._image_after_caption.setObjectName("Muted")
        self._image_after = QLabel(holder)
        self._image_after.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for caption, image in (
            (self._image_before_caption, self._image_before),
            (self._image_after_caption, self._image_after),
        ):
            column = QVBoxLayout()
            column.setSpacing(6)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            column.addWidget(caption)
            column.addWidget(image, 1)
            wrapper = QWidget(holder)
            wrapper.setLayout(column)
            row.addWidget(wrapper, 1)

        area.setWidget(holder)
        return area

    def show_images(self, before: bytes | None, after: bytes | None) -> None:
        """
        Shows an image file's two versions next to each other.

        Args:
            before: Raw content of the old version, or None when it did not exist.
            after: Raw content of the new version, or None when it was deleted.

        Returns:
            None
        """

        colors = get_theme_colors()
        for payload, label in ((before, self._image_before), (after, self._image_after)):
            if payload is None:
                label.setPixmap(QPixmap())
                label.setText("—")
                label.setStyleSheet(f"color: {colors.text_muted};")
                continue
            pixmap = QPixmap()
            if not pixmap.loadFromData(payload):
                label.setPixmap(QPixmap())
                label.setText(i18n.t("diff.binary"))
                label.setStyleSheet(f"color: {colors.text_muted};")
                continue
            if pixmap.width() > 900 or pixmap.height() > 900:
                pixmap = pixmap.scaled(
                    900,
                    900,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            label.setText("")
            label.setStyleSheet(f"border: 1px solid {colors.border};")
            label.setPixmap(pixmap)
        self._stack.setCurrentWidget(self._images)

    def _build_toolbar(self, ignore_whitespace: bool, word_level: bool) -> QWidget:
        """
        Builds the option row above the diff.

        Args:
            ignore_whitespace: Initial state of the whitespace toggle.
            word_level: Initial state of the word-highlight toggle.

        Returns:
            QWidget: The toolbar.
        """

        holder = QWidget(self)
        holder.setObjectName("PanelHeader")
        row = QHBoxLayout(holder)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        self._target = QComboBox(holder)
        for target in VALID_TARGETS:
            self._target.addItem(i18n.t(TARGET_LABEL_KEYS[target]), target)
        # "Your edits vs. the last saved version" is what someone looking at a
        # changed file almost always means, so it is the one selected up front.
        default_index = self._target.findData(TARGET_WORKTREE_HEAD)
        if default_index >= 0:
            self._target.setCurrentIndex(default_index)
        self._target.currentIndexChanged.connect(self._on_target_changed)
        row.addWidget(QLabel(i18n.t("diff.target_label"), holder))
        row.addWidget(self._target)

        self._mode_box = QComboBox(holder)
        self._mode_box.addItem(i18n.t("diff.mode_side_by_side"), DIFF_SIDE_BY_SIDE)
        self._mode_box.addItem(i18n.t("diff.mode_unified"), DIFF_UNIFIED)
        index = self._mode_box.findData(self._mode)
        if index >= 0:
            self._mode_box.setCurrentIndex(index)
        self._mode_box.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(self._mode_box)

        self._whitespace = QCheckBox(i18n.t("diff.ignore_whitespace"), holder)
        self._whitespace.setChecked(ignore_whitespace)
        self._whitespace.toggled.connect(self._on_option_toggled)
        row.addWidget(self._whitespace)

        self._words = QCheckBox(i18n.t("diff.word_level"), holder)
        self._words.setChecked(word_level)
        self._words.toggled.connect(self._on_option_toggled)
        row.addWidget(self._words)

        row.addStretch(1)

        self._counts = QLabel("", holder)
        self._counts.setObjectName("Muted")
        row.addWidget(self._counts)

        self._open_button = QPushButton(i18n.t("diff.open_file"), holder)
        self._open_button.clicked.connect(lambda: self.open_file_requested.emit(self._path))
        self._open_button.setEnabled(False)
        row.addWidget(self._open_button)

        self._reveal_button = QPushButton(i18n.t("diff.open_folder"), holder)
        self._reveal_button.clicked.connect(lambda: self.reveal_file_requested.emit(self._path))
        self._reveal_button.setEnabled(False)
        row.addWidget(self._reveal_button)
        return holder

    # ----------------------------------------------------------------- options

    @property
    def mode(self) -> str:
        """
        Returns the active layout.

        Returns:
            str: Diff mode identifier.
        """

        return self._mode

    @property
    def ignore_whitespace(self) -> bool:
        """
        Reports whether whitespace-only changes are hidden.

        Returns:
            bool: True when hidden.
        """

        return self._whitespace.isChecked()

    @property
    def word_level(self) -> bool:
        """
        Reports whether intra-line highlighting is on.

        Returns:
            bool: True when on.
        """

        return self._words.isChecked()

    @property
    def target(self) -> str:
        """
        Returns the chosen comparison target.

        Returns:
            str: Target identifier.
        """

        data = self._target.currentData()
        return data if isinstance(data, str) else VALID_TARGETS[0]

    def set_target(self, target: str) -> None:
        """
        Selects a comparison target without emitting a change.

        Args:
            target: Target identifier.

        Returns:
            None
        """

        index = self._target.findData(target)
        if index >= 0:
            self._target.blockSignals(True)
            self._target.setCurrentIndex(index)
            self._target.blockSignals(False)

    def set_target_choices(self, targets: list[str]) -> None:
        """
        Limits the target dropdown to the comparisons that make sense right now.

        Comparing two commits needs two commits selected in the graph, so offering
        it while looking at working-tree changes would be a dead end.

        Args:
            targets: Target identifiers to offer.

        Returns:
            None
        """

        current = self.target
        self._target.blockSignals(True)
        self._target.clear()
        for target in targets:
            self._target.addItem(i18n.t(TARGET_LABEL_KEYS[target]), target)
        index = self._target.findData(current)
        self._target.setCurrentIndex(index if index >= 0 else 0)
        self._target.blockSignals(False)

    def _on_mode_changed(self, _index: int) -> None:
        """
        Applies a layout picked from the dropdown.

        Args:
            _index: Unused index from the signal.

        Returns:
            None
        """

        data = self._mode_box.currentData()
        if isinstance(data, str):
            self._mode = normalize_diff_mode(data)
            self.options_changed.emit()
            self._rerender()

    def _on_option_toggled(self, _checked: bool) -> None:
        """
        Applies a toggled option.

        Whitespace handling changes what git produces, so that one needs a fresh
        diff; word highlighting is pure presentation and only needs a redraw.

        Args:
            _checked: Unused state from the signal.

        Returns:
            None
        """

        self.options_changed.emit()
        if self.sender() is self._whitespace:
            self.reload_requested.emit()
        else:
            self._rerender()

    def _on_target_changed(self, _index: int) -> None:
        """
        Announces a new comparison target.

        Args:
            _index: Unused index from the signal.

        Returns:
            None
        """

        self.target_changed.emit(self.target)

    # ------------------------------------------------------------------ content

    def clear(self, message_key: str = "diff.no_selection") -> None:
        """
        Empties the panel.

        Args:
            message_key: Translation key for the placeholder text.

        Returns:
            None
        """

        self._diff = None
        self._many = []
        self._path = ""
        self._counts.setText("")
        self._open_button.setEnabled(False)
        self._reveal_button.setEnabled(False)
        self._empty.set_message(i18n.t(message_key), "")
        self._stack.setCurrentWidget(self._empty)

    def show_diff(self, diff: FileDiff) -> None:
        """
        Displays a parsed diff.

        Args:
            diff: Diff to show.

        Returns:
            None
        """

        self._diff = diff
        self._many = []
        self._path = diff.path or diff.old_path
        self._open_button.setEnabled(bool(self._path))
        self._reveal_button.setEnabled(bool(self._path))
        self._counts.setText(
            f"{i18n.t('diff.lines_added', count=diff.added)}  {i18n.t('diff.lines_removed', count=diff.removed)}"
        )

        if diff.error_key:
            self._show_notice(i18n.t("error.title"), i18n.t(diff.error_key), "danger")
            return
        if diff.binary:
            # An image gets a real preview via show_images(); until the caller
            # supplies the two blobs, the explanation stands in for it.
            self._show_notice(i18n.t("diff.binary"), i18n.t("diff.binary_hint"), "info")
            return
        if diff.is_empty:
            self._counts.setText("")
            self._empty.set_message(i18n.t("diff.no_changes"), "")
            self._stack.setCurrentWidget(self._empty)
            return

        self._rerender()

    def show_many(self, diffs: list[FileDiff], caption: str = "") -> None:
        """
        Displays several files at once, as a commit or a range comparison does.

        Args:
            diffs: Parsed diffs to show.
            caption: Text for the counts area, e.g. the commit being shown.

        Returns:
            None
        """

        self._diff = None
        self._many = diffs
        self._path = ""
        self._open_button.setEnabled(False)
        self._reveal_button.setEnabled(False)

        if not diffs:
            self._counts.setText("")
            self._empty.set_message(i18n.t("diff.no_changes"), "")
            self._stack.setCurrentWidget(self._empty)
            return

        added = sum(item.added for item in diffs)
        removed = sum(item.removed for item in diffs)
        prefix = f"{caption}  ·  " if caption else ""
        self._counts.setText(
            f"{prefix}{i18n.t('diff.lines_added', count=added)}  "
            f"{i18n.t('diff.lines_removed', count=removed)}"
        )
        colors = get_theme_colors()
        self._browser.setHtml(render_many(diffs, self._mode, colors, self.word_level))
        self._browser.verticalScrollBar().setValue(0)
        self._stack.setCurrentWidget(self._browser)

    def _show_notice(self, title: str, detail: str, token: str) -> None:
        """
        Shows a message instead of a diff.

        Args:
            title: Headline.
            detail: Explanation.
            token: Theme token naming the accent colour.

        Returns:
            None
        """

        self._notice.set_message(title, detail, token)
        self._stack.setCurrentWidget(self._notice_holder)

    def _rerender(self) -> None:
        """
        Redraws the current diff with the current options.

        Returns:
            None
        """

        colors = get_theme_colors()
        if self._many:
            self._browser.setHtml(render_many(self._many, self._mode, colors, self.word_level))
            self._stack.setCurrentWidget(self._browser)
            return
        if self._diff is None or self._diff.binary or self._diff.error_key:
            return
        self._browser.setHtml(render_diff(self._diff, self._mode, colors, self.word_level))
        self._browser.verticalScrollBar().setValue(0)
        self._stack.setCurrentWidget(self._browser)
        if self._diff.truncated:
            self._counts.setText(
                f"{self._counts.text()}  ·  {i18n.t('diff.too_large', count=self._diff.line_count)}"
            )

    def refresh(self, _theme: str | None = None) -> None:
        """
        Redraws after a theme change.

        Args:
            _theme: Accepted for the theme-refresh protocol; the active theme is
                read directly.

        Returns:
            None
        """

        self._rerender()

    def current_path(self) -> str:
        """
        Returns the path currently shown.

        Returns:
            str: Repository-relative path, empty when nothing is shown.
        """

        return self._path

    def suffix_is_image(self) -> bool:
        """
        Reports whether the shown file looks like an image.

        Returns:
            bool: True for a known image suffix.
        """

        return bool(self._path) and Path(self._path).suffix.lower() in IMAGE_SUFFIXES
