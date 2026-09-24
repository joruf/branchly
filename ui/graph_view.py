"""
The commit graph.

The lanes are painted by a delegate into the first column of an ordinary tree
widget, rather than into a bespoke scrolling canvas. That choice buys selection,
keyboard navigation, scrolling and context menus for free, and leaves only the
drawing to do.

Each row paints three things: the vertical segments of every lane running through
it, its own dot, and a short diagonal to each parent's lane. A parent further down
is then reached by that lane's vertical segments, which is why a merge looks like
a branch peeling off rather than a long slanted line across the view.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import get_theme_colors, lane_color
from gitops.branch import RESET_HARD, RESET_MIXED, RESET_SOFT
from gitops.history import GraphRow, History
from ui.widgets import SectionHeader

LANE_WIDTH = 14
DOT_RADIUS = 4
MAX_PAINTED_LANES = 12

_ROLE_OID = int(Qt.ItemDataRole.UserRole)
_ROLE_LANE = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_PASSING = int(Qt.ItemDataRole.UserRole) + 2
_ROLE_EDGES = int(Qt.ItemDataRole.UserRole) + 3
_ROLE_INCOMING = int(Qt.ItemDataRole.UserRole) + 4
_ROLE_IS_HEAD = int(Qt.ItemDataRole.UserRole) + 5


class GraphDelegate(QStyledItemDelegate):
    """
    Paints the lane diagram in the graph column.
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """
        Draws one row of the diagram.

        Args:
            painter: Painter supplied by the view.
            option: Style options, including the row rectangle.
            index: Model index of the row.

        Returns:
            None
        """

        if index.column() != 0:
            super().paint(painter, option, index)
            return

        lane = index.data(_ROLE_LANE)
        if not isinstance(lane, int):
            super().paint(painter, option, index)
            return

        passing = index.data(_ROLE_PASSING) or ()
        edges = index.data(_ROLE_EDGES) or ()
        incoming = bool(index.data(_ROLE_INCOMING))
        is_head = bool(index.data(_ROLE_IS_HEAD))
        colors = get_theme_colors()

        rect: QRect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        def x_of(position: int) -> float:
            return rect.left() + LANE_WIDTH * min(position, MAX_PAINTED_LANES) + LANE_WIDTH / 2

        top = float(rect.top())
        bottom = float(rect.bottom() + 1)
        middle = (top + bottom) / 2

        for other in passing:
            if not isinstance(other, int):
                continue
            painter.setPen(QPen(QColor(lane_color(other)), 2))
            painter.drawLine(QPointF(x_of(other), top), QPointF(x_of(other), bottom))

        own_color = QColor(lane_color(lane))
        painter.setPen(QPen(own_color, 2))
        if incoming:
            painter.drawLine(QPointF(x_of(lane), top), QPointF(x_of(lane), middle))

        for edge in edges:
            if not (isinstance(edge, (tuple, list)) and len(edge) == 2):
                continue
            parent_lane = edge[0]
            if not isinstance(parent_lane, int):
                continue
            painter.setPen(QPen(QColor(lane_color(parent_lane)), 2))
            if parent_lane == lane:
                painter.drawLine(QPointF(x_of(lane), middle), QPointF(x_of(lane), bottom))
                continue
            path = QPainterPath(QPointF(x_of(lane), middle))
            path.cubicTo(
                QPointF(x_of(lane), (middle + bottom) / 2),
                QPointF(x_of(parent_lane), (middle + bottom) / 2),
                QPointF(x_of(parent_lane), bottom),
            )
            painter.strokePath(path, painter.pen())

        painter.setPen(QPen(QColor(colors.graph_node_border), 1.5))
        painter.setBrush(QBrush(own_color))
        painter.drawEllipse(QPointF(x_of(lane), middle), DOT_RADIUS, DOT_RADIUS)

        if is_head:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(colors.graph_head_ring), 1.5))
            painter.drawEllipse(QPointF(x_of(lane), middle), DOT_RADIUS + 3, DOT_RADIUS + 3)

        painter.restore()


class GraphView(QWidget):
    """
    Shows history as a graph and offers the actions that act on a commit.

    Attributes:
        commit_selected: Emitted with the object id of the highlighted commit.
        checkout_requested: Emitted with an object id.
        branch_from_requested: Emitted with an object id.
        merge_requested: Emitted with an object id.
        cherry_pick_requested: Emitted with an object id.
        revert_requested: Emitted with an object id.
        reset_requested: Emitted with ``(object id, mode)``.
        tag_requested: Emitted with an object id.
        compare_requested: Emitted with ``(older, newer)``.
        reload_requested: Emitted when the graph should be read again.
    """

    commit_selected = Signal(str)
    checkout_requested = Signal(str)
    branch_from_requested = Signal(str)
    merge_requested = Signal(str)
    cherry_pick_requested = Signal(str)
    revert_requested = Signal(str)
    reset_requested = Signal(str, str)
    tag_requested = Signal(str)
    compare_requested = Signal(str, str)
    reload_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """
        Args:
            parent: Parent widget.
        """

        super().__init__(parent)
        self._history: History | None = None
        self._current_branch = ""
        self._marked_for_compare = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = SectionHeader(i18n.t("graph.title"), self)
        self._legend = QLabel(i18n.t("graph.legend"), header)
        self._legend.setObjectName("Muted")
        # A sentence this long refuses to be laid out narrower than itself unless
        # it is allowed to break, and that alone set a floor under the window's
        # width.
        self._legend.setWordWrap(True)
        self._legend.setMinimumWidth(1)
        header.add_widget(self._legend)
        reload_button = QPushButton(i18n.t("action.refresh"), header)
        reload_button.clicked.connect(self.reload_requested.emit)
        header.add_widget(reload_button)
        layout.addWidget(header)

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["", i18n.t("changes.history"), "", ""])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAlternatingRowColors(False)
        self._tree.setItemDelegate(GraphDelegate(self._tree))
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self._tree, 1)

        self._marked_label = QLabel("", self)
        self._marked_label.setObjectName("Muted")
        self._marked_label.setVisible(False)
        marked_row = QWidget(self)
        marked_layout = QHBoxLayout(marked_row)
        marked_layout.setContentsMargins(10, 4, 10, 4)
        marked_layout.addWidget(self._marked_label)
        marked_layout.addStretch(1)
        layout.addWidget(marked_row)

    # ------------------------------------------------------------------ filling

    def set_history(self, history: History, current_branch: str = "") -> None:
        """
        Replaces the shown history.

        Args:
            history: Laid-out rows.
            current_branch: Branch name used in the action labels.

        Returns:
            None
        """

        self._history = history
        self._current_branch = current_branch
        self._tree.clear()

        if not history.ok:
            item = QTreeWidgetItem(self._tree)
            item.setText(1, i18n.t(history.error_key))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        if not history.rows:
            item = QTreeWidgetItem(self._tree)
            item.setText(1, i18n.t("history.empty"))
            item.setToolTip(1, i18n.t("history.empty_hint"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            return

        incoming = self._rows_with_children(history)
        colors = get_theme_colors()

        for position, row in enumerate(history.rows):
            item = QTreeWidgetItem(self._tree)
            item.setData(0, _ROLE_OID, row.commit.oid)
            item.setData(0, _ROLE_LANE, row.lane)
            item.setData(0, _ROLE_PASSING, tuple(row.passing))
            item.setData(0, _ROLE_EDGES, tuple(row.edges))
            item.setData(0, _ROLE_INCOMING, position in incoming)
            item.setData(0, _ROLE_IS_HEAD, row.commit.is_head)

            item.setText(1, self._subject_text(row))
            item.setText(2, row.commit.author)
            item.setText(3, row.commit.date[:10])
            item.setToolTip(1, f"{row.commit.short}  {row.commit.subject}")
            if row.commit.is_head:
                font = QFont(self._tree.font())
                font.setBold(True)
                item.setFont(1, font)
                item.setToolTip(1, f"{i18n.t('graph.head_here')}\n{row.commit.short}")
            item.setForeground(2, QBrush(QColor(colors.text_muted)))
            item.setForeground(3, QBrush(QColor(colors.text_muted)))

        width = LANE_WIDTH * min(max(history.max_width, 1), MAX_PAINTED_LANES) + LANE_WIDTH
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setColumnWidth(0, width)
        if self._tree.topLevelItemCount():
            self._tree.setCurrentItem(self._tree.topLevelItem(0))

    @staticmethod
    def _rows_with_children(history: History) -> set[int]:
        """
        Finds the rows some earlier row points at.

        Those rows get a line coming in from above; a row nobody points at is the
        tip of its lane and must not have one, or the graph grows stray stubs.

        Args:
            history: Laid-out rows.

        Returns:
            set[int]: Row indices that are a parent of an earlier row.
        """

        referenced: set[int] = set()
        for row in history.rows:
            for _lane, parent_row in row.edges:
                if parent_row >= 0:
                    referenced.add(parent_row)
        return referenced

    def _subject_text(self, row: GraphRow) -> str:
        """
        Builds the label for a commit, including its branch and tag names.

        Args:
            row: Graph row.

        Returns:
            str: Text for the subject column.
        """

        refs = "  ".join(f"[{name}]" for name in row.commit.refs)
        return f"{refs}  {row.commit.subject}".strip() if refs else row.commit.subject

    # ------------------------------------------------------------------ queries

    def selected_oid(self) -> str:
        """
        Returns the highlighted commit.

        Returns:
            str: Object id, empty when nothing is selected.
        """

        item = self._tree.currentItem()
        if item is None:
            return ""
        value = item.data(0, _ROLE_OID)
        return value if isinstance(value, str) else ""

    @property
    def marked_for_compare(self) -> str:
        """
        Returns the commit marked as the other side of a comparison.

        Returns:
            str: Object id, empty when none is marked.
        """

        return self._marked_for_compare

    def clear_comparison_mark(self) -> None:
        """
        Forgets the marked commit.

        Returns:
            None
        """

        self._marked_for_compare = ""
        self._marked_label.setVisible(False)

    # ------------------------------------------------------------------- events

    def _on_current_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        """
        Announces the newly highlighted commit.

        Args:
            current: Newly current item.
            _previous: Previously current item.

        Returns:
            None
        """

        if current is None:
            return
        oid = current.data(0, _ROLE_OID)
        if isinstance(oid, str) and oid:
            self.commit_selected.emit(oid)

    def _show_context_menu(self, position) -> None:  # noqa: ANN001 - Qt passes a QPoint
        """
        Opens the action menu for the clicked commit.

        Args:
            position: Click position in viewport coordinates.

        Returns:
            None
        """

        item = self._tree.itemAt(position)
        if item is None:
            return
        oid = item.data(0, _ROLE_OID)
        if not isinstance(oid, str) or not oid:
            return
        self._tree.setCurrentItem(item)
        branch = self._current_branch or "?"

        menu = QMenu(self)
        menu.addAction(i18n.t("graph.checkout"), lambda: self.checkout_requested.emit(oid))
        menu.addAction(i18n.t("graph.branch_from"), lambda: self.branch_from_requested.emit(oid))
        menu.addSeparator()
        menu.addAction(
            i18n.t("graph.merge_into", branch=branch), lambda: self.merge_requested.emit(oid)
        )
        menu.addAction(
            i18n.t("graph.cherry_pick", branch=branch), lambda: self.cherry_pick_requested.emit(oid)
        )
        menu.addAction(i18n.t("graph.revert"), lambda: self.revert_requested.emit(oid))
        menu.addSeparator()

        reset_menu = menu.addMenu(i18n.t("graph.reset_menu"))
        reset_menu.addAction(
            i18n.t("graph.reset_soft"), lambda: self.reset_requested.emit(oid, RESET_SOFT)
        )
        reset_menu.addAction(
            i18n.t("graph.reset_mixed"), lambda: self.reset_requested.emit(oid, RESET_MIXED)
        )
        reset_menu.addAction(
            i18n.t("graph.reset_hard"), lambda: self.reset_requested.emit(oid, RESET_HARD)
        )

        menu.addAction(i18n.t("graph.tag"), lambda: self.tag_requested.emit(oid))
        menu.addSeparator()
        menu.addAction(i18n.t("graph.copy_sha"), lambda: self._copy_oid(oid))

        if self._marked_for_compare and self._marked_for_compare != oid:
            menu.addAction(
                i18n.t("graph.compare_with"),
                lambda: self._emit_comparison(oid),
            )
        menu.addAction(i18n.t("graph.select_for_compare"), lambda: self._mark_for_compare(oid))
        menu.exec(self._tree.viewport().mapToGlobal(position))

    def _mark_for_compare(self, oid: str) -> None:
        """
        Remembers a commit as one side of a comparison.

        Args:
            oid: Object id.

        Returns:
            None
        """

        self._marked_for_compare = oid
        self._marked_label.setText(f"{i18n.t('graph.select_for_compare')}: {oid[:7]}")
        self._marked_label.setVisible(True)

    def _emit_comparison(self, oid: str) -> None:
        """
        Asks for a diff between the marked commit and this one.

        The older commit goes first so the diff reads as "what changed since".

        Args:
            oid: Object id of the second commit.

        Returns:
            None
        """

        history = self._history
        if history is None:
            return
        first_row = history.row_of(self._marked_for_compare)
        second_row = history.row_of(oid)
        if first_row < 0 or second_row < 0:
            return
        # Rows are newest first, so the larger index is the older commit.
        if first_row > second_row:
            older, newer = self._marked_for_compare, oid
        else:
            older, newer = oid, self._marked_for_compare
        self.compare_requested.emit(older, newer)

    def _copy_oid(self, oid: str) -> None:
        """
        Puts an object id on the clipboard.

        Args:
            oid: Object id.

        Returns:
            None
        """

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(oid)
