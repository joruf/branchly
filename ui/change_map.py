"""
The bar that shows where the changes are in a long file.

Once the comparison shows a whole file instead of a window around each change,
the changes stop being what you see and become something you have to go looking
for. A thousand-line file with three edits in it is three edits and nine hundred
and ninety-seven lines of scrolling.

So the bar draws the whole file as one column, twenty pixels wide, with a mark
at every place something changed. A click on a mark goes there. It is the same
idea as the scrollbar, except that it is about the content rather than about the
viewport, which is why it sits beside the scrollbar and not instead of it.

The marks carry the comparison's own colours: green for added, red for removed,
amber where one block holds both. A bar in a single colour would answer "is
there anything here" and nothing else, and the panel beside it has already
taught the reader what the colours mean.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

import i18n
from config.theme import get_theme_colors

# What one mark stands for.
KIND_ADDED = "added"
KIND_REMOVED = "removed"
KIND_MIXED = "mixed"

BAR_WIDTH = 20
# A change of one line in a file of thousands would be a fraction of a pixel.
# Every mark is at least this tall, or the bar would be blank exactly where it
# matters most.
MIN_MARK_HEIGHT = 3


@dataclass(frozen=True, slots=True)
class ChangeSpan:
    """
    One run of changed lines.

    Attributes:
        first: First display row of the run, counted from zero.
        last: Last display row of the run.
        kind: One of the ``KIND_*`` constants.
        anchor: Name of the anchor in the rendered document, for jumping there.
    """

    first: int
    last: int
    kind: str
    anchor: str = ""


class ChangeMap(QWidget):
    """
    A column of the whole file with every change marked.

    Attributes:
        jump_requested: Emitted with the anchor name of the change that was
            clicked, empty when the click landed on nothing in particular.
    """

    jump_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """
        Args:
            parent: Parent widget.
        """

        super().__init__(parent)
        self._spans: list[ChangeSpan] = []
        self._rows = 0
        self._view_first = 0.0
        self._view_last = 0.0

        self.setFixedWidth(BAR_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(i18n.t("tip.change_map"))

    # ------------------------------------------------------------------ contents

    def set_spans(self, spans: list[ChangeSpan], rows: int) -> None:
        """
        Replaces what the bar shows.

        Args:
            spans: The changed runs, in file order.
            rows: Total number of display rows the file occupies.

        Returns:
            None
        """

        self._spans = list(spans)
        self._rows = max(0, rows)
        self.update()

    def set_viewport(self, first: float, last: float) -> None:
        """
        Marks which part of the file is on screen.

        Args:
            first: Fraction of the file above the visible area.
            last: Fraction of the file above the bottom of the visible area.

        Returns:
            None
        """

        self._view_first = max(0.0, min(1.0, first))
        self._view_last = max(self._view_first, min(1.0, last))
        self.update()

    @property
    def spans(self) -> list[ChangeSpan]:
        """
        Returns the runs currently drawn.

        Returns:
            list[ChangeSpan]: The runs, in file order.
        """

        return list(self._spans)

    def anchor_at(self, y: int) -> str:
        """
        Returns the change a click at this height belongs to.

        The nearest one rather than only a direct hit: a single changed line in
        a long file is a two pixel mark, and asking somebody to hit that is
        asking them to use the scrollbar instead.

        Args:
            y: Position inside the bar, in pixels from the top.

        Returns:
            str: Anchor name, empty when there is nothing to jump to.
        """

        if not self._spans or self._rows <= 0 or self.height() <= 0:
            return ""
        row = (y / self.height()) * self._rows
        nearest = min(self._spans, key=lambda span: _distance(span, row))
        return nearest.anchor

    # -------------------------------------------------------------------- events

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Jumps to the change that was clicked.

        Args:
            event: Mouse event.

        Returns:
            None
        """

        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        anchor = self.anchor_at(int(event.position().y()))
        if anchor:
            self.jump_requested.emit(anchor)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """
        Draws the file and its changes.

        Args:
            event: Paint event.

        Returns:
            None
        """

        del event
        colors = get_theme_colors()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(colors.diff_gutter_bg))

        if self._rows > 0:
            # The vivid status colours rather than the diff's own backgrounds.
            # A background is meant to sit behind text without shouting; a mark
            # two pixels tall in a column of grey has to be seen at a glance.
            marks = {
                KIND_ADDED: QColor(colors.status_added),
                KIND_REMOVED: QColor(colors.status_deleted),
                KIND_MIXED: QColor(colors.status_modified),
            }
            height = self.height()
            for span in self._spans:
                top = int(span.first / self._rows * height)
                bottom = int((span.last + 1) / self._rows * height)
                painter.fillRect(
                    QRect(2, top, BAR_WIDTH - 4, max(MIN_MARK_HEIGHT, bottom - top)),
                    marks.get(span.kind, marks[KIND_MIXED]),
                )

        # The part on screen, as a frame rather than a fill: it has to be
        # readable across a mark without hiding it.
        if self._view_last > self._view_first:
            top = int(self._view_first * self.height())
            bottom = int(self._view_last * self.height())
            painter.setPen(QColor(colors.border_strong))
            painter.drawRect(QRect(0, top, BAR_WIDTH - 1, max(2, bottom - top - 1)))
        painter.end()


def _distance(span: ChangeSpan, row: float) -> float:
    """
    Returns how far a row is from a run of changes.

    Args:
        span: The run.
        row: Row the user clicked on.

    Returns:
        float: Zero inside the run, the gap otherwise.
    """

    if span.first <= row <= span.last:
        return 0.0
    return min(abs(span.first - row), abs(span.last - row))
