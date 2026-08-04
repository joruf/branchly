"""
The project sidebar.

Categories are collapsible groups; inside each one the starred projects sit at
the top, and everything below them follows the chosen sort order. The badges on
each row are the whole point of the panel: at a glance, which projects have local
changes, which have news on the server, and which need a decision.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

import i18n
from config.theme import get_theme_colors
from models.category import Category
from models.repository import RepoEntry
from models.sort import SORT_MODE_LABEL_KEYS, VALID_SORT_MODES
from services.registry import Registry
from services.scanner import describe
from ui.widgets import Badge, format_relative_check_time

_ROLE_ENTRY_KEY = int(Qt.ItemDataRole.UserRole)
_ROLE_CATEGORY = int(Qt.ItemDataRole.UserRole) + 1


class RepoRow(QWidget):
    """
    One project row: a star, a name, and its badges.
    """

    favorite_toggled = Signal(str)

    def __init__(self, entry: RepoEntry, parent: QWidget | None = None) -> None:
        """
        Args:
            entry: Repository to show.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._key = entry.key

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 6, 1)
        layout.setSpacing(6)

        self._star = QToolButton(self)
        self._star.setAutoRaise(True)
        self._star.setCheckable(True)
        self._star.setCursor(Qt.CursorShape.PointingHandCursor)
        self._star.clicked.connect(lambda: self.favorite_toggled.emit(self._key))
        layout.addWidget(self._star)

        self._name = QLabel(self)
        self._name.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self._name, 1)

        self._badges = QHBoxLayout()
        self._badges.setContentsMargins(0, 0, 0, 0)
        self._badges.setSpacing(4)
        badge_holder = QWidget(self)
        badge_holder.setLayout(self._badges)
        layout.addWidget(badge_holder)

        self.update_entry(entry)

    @property
    def key(self) -> str:
        """
        Returns the registry key of the row's repository.

        Returns:
            str: Resolved path string.
        """

        return self._key

    def update_entry(self, entry: RepoEntry) -> None:
        """
        Redraws the row from a repository's current state.

        Args:
            entry: Repository to show.

        Returns:
            None
        """

        colors = get_theme_colors()
        self._key = entry.key

        self._star.setChecked(entry.favorite)
        self._star.setText("★" if entry.favorite else "☆")
        self._star.setToolTip(i18n.t("repo.favorite_off" if entry.favorite else "repo.favorite_on"))
        self._star.setStyleSheet(
            f"color: {colors.warning if entry.favorite else colors.text_muted};"
            "border: none; font-size: 14px; padding: 0 2px;"
        )

        font = QFont(self._name.font())
        font.setBold(entry.favorite)
        self._name.setFont(font)
        self._name.setText(entry.name)

        missing = not entry.exists
        if missing:
            self._name.setStyleSheet(f"color: {colors.danger};")
            self._name.setToolTip(i18n.t("repo.missing_hint", path=str(entry.path)))
        else:
            self._name.setStyleSheet(f"color: {colors.text};")
            checked = format_relative_check_time(entry.status.checked_at, time.time())
            self._name.setToolTip(f"{entry.path}\n{checked}")

        while self._badges.count():
            item = self._badges.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

        if missing:
            marker = QLabel("!")
            marker.setToolTip(i18n.t("repo.missing"))
            marker.setStyleSheet(f"color: {colors.danger}; font-weight: 700;")
            self._badges.addWidget(marker)
            return

        for kind, _token, count in describe(entry.status):
            self._badges.addWidget(Badge(kind, count, self))


class Sidebar(QWidget):
    """
    The panel listing every tracked project.

    Attributes:
        repo_selected: Emitted with the registry key of the newly selected project.
        check_requested: Emitted with a registry key when one project should be
            checked, or with an empty string for all of them.
        pull_all_requested: Emitted when every project should be brought up to the
            server's version.
        add_requested: Emitted when the user wants to add an existing project.
        clone_requested: Emitted when the user wants to clone one.
        registry_changed: Emitted whenever the registry was modified and needs
            saving.
        open_folder_requested: Emitted with a registry key.
        open_remote_requested: Emitted with a registry key.
        rename_requested: Emitted with a registry key.
        remove_requested: Emitted with a registry key.
    """

    repo_selected = Signal(str)
    check_requested = Signal(str)
    pull_all_requested = Signal()
    add_requested = Signal()
    clone_requested = Signal()
    registry_changed = Signal()
    open_folder_requested = Signal(str)
    open_remote_requested = Signal(str)
    rename_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, registry: Registry, sort_mode: str, parent: QWidget | None = None) -> None:
        """
        Args:
            registry: Registry to display.
            sort_mode: Initial sort order.
            parent: Parent widget.
        """

        super().__init__(parent)
        self._registry = registry
        self._sort_mode = sort_mode
        self._selected_key = ""
        self._rows: dict[str, RepoRow] = {}
        self._suppress_selection = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._build_actions())
        layout.addWidget(self._build_filters())

        self._tree = QTreeWidget(self)
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(10)
        self._tree.setUniformRowHeights(False)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        self._tree.itemExpanded.connect(self._on_expanded)
        self._tree.itemCollapsed.connect(self._on_collapsed)
        layout.addWidget(self._tree, 1)

        layout.addWidget(self._build_footer())
        self.refresh()

    # ------------------------------------------------------------------ chrome

    def _build_actions(self) -> QWidget:
        """
        Builds the add/clone button row.

        Returns:
            QWidget: The row.
        """

        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        # Short labels: the panel is narrow, and a truncated button reads as a
        # bug. The full wording lives in the tooltip and in the menus.
        add = QPushButton(i18n.t("sidebar.add_repo_short"), holder)
        add.setToolTip(i18n.t("sidebar.add_repo"))
        add.clicked.connect(self.add_requested.emit)
        clone = QPushButton(i18n.t("sidebar.clone_repo_short"), holder)
        clone.setObjectName("Primary")
        clone.setToolTip(i18n.t("sidebar.clone_repo"))
        clone.clicked.connect(self.clone_requested.emit)
        row.addWidget(add, 1)
        row.addWidget(clone, 1)
        return holder

    def _build_filters(self) -> QWidget:
        """
        Builds the search field and the sort dropdown.

        Returns:
            QWidget: The row.
        """

        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)

        self._search = QLineEdit(holder)
        self._search.setPlaceholderText(i18n.t("sidebar.search"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _text: self.refresh())
        column.addWidget(self._search)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(i18n.t("sidebar.sort_label"), holder)
        label.setObjectName("Muted")
        row.addWidget(label)

        self._sort = QComboBox(holder)
        for mode in VALID_SORT_MODES:
            self._sort.addItem(i18n.t(SORT_MODE_LABEL_KEYS[mode]), mode)
        index = self._sort.findData(self._sort_mode)
        if index >= 0:
            self._sort.setCurrentIndex(index)
        self._sort.currentIndexChanged.connect(self._on_sort_changed)
        row.addWidget(self._sort, 1)
        column.addLayout(row)
        return holder

    def _build_footer(self) -> QWidget:
        """
        Builds the summary line, the progress bar and the check-all button.

        Returns:
            QWidget: The footer.
        """

        holder = QWidget(self)
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)

        self._summary = QLabel("", holder)
        self._summary.setObjectName("Muted")
        self._summary.setWordWrap(True)
        column.addWidget(self._summary)

        self._progress = QProgressBar(holder)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        column.addWidget(self._progress)

        self._check_all = QPushButton(i18n.t("sidebar.check_all"), holder)
        self._check_all.clicked.connect(lambda: self.check_requested.emit(""))
        column.addWidget(self._check_all)

        self._pull_all = QPushButton(i18n.t("sidebar.pull_all"), holder)
        self._pull_all.setToolTip(i18n.t("sidebar.pull_all_hint"))
        self._pull_all.clicked.connect(self.pull_all_requested.emit)
        column.addWidget(self._pull_all)
        return holder

    # ------------------------------------------------------------------- state

    @property
    def sort_mode(self) -> str:
        """
        Returns the active sort order.

        Returns:
            str: Sort mode identifier.
        """

        return self._sort_mode

    @property
    def selected_key(self) -> str:
        """
        Returns the registry key of the selected project.

        Returns:
            str: Key, empty when nothing is selected.
        """

        return self._selected_key

    def set_sort_mode(self, mode: str) -> None:
        """
        Changes the sort order.

        Args:
            mode: Sort mode identifier.

        Returns:
            None
        """

        self._sort_mode = mode
        index = self._sort.findData(mode)
        if index >= 0:
            self._sort.setCurrentIndex(index)
        self.refresh()

    def _on_sort_changed(self, _index: int) -> None:
        """
        Applies a sort order picked from the dropdown.

        Args:
            _index: Unused index from the signal.

        Returns:
            None
        """

        data = self._sort.currentData()
        if isinstance(data, str):
            self._sort_mode = data
            self.refresh()

    def set_scan_progress(self, done: int, total: int) -> None:
        """
        Shows how far a running check has got.

        Args:
            done: Completed scans.
            total: Total scans in the batch.

        Returns:
            None
        """

        running = total > 0 and done < total
        self._progress.setVisible(running)
        self._check_all.setEnabled(not running)
        # A pull would fight the scan for the same index locks.
        self._pull_all.setEnabled(not running)
        if running:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
            self._summary.setText(i18n.t("sidebar.checking", done=done, total=total))
        else:
            self._progress.setVisible(False)
            self.refresh_summary()

    def refresh_summary(self) -> None:
        """
        Rewrites the one-line overview under the list.

        Returns:
            None
        """

        counts = self._registry.summary()
        if not counts["total"]:
            self._summary.setText("")
            return
        parts: list[str] = []
        if counts["conflicts"]:
            parts.append(
                i18n.plural(
                    counts["conflicts"],
                    "sidebar.summary_conflicts_one",
                    "sidebar.summary_conflicts_many",
                )
            )
        if counts["changed"]:
            parts.append(
                i18n.plural(counts["changed"], "sidebar.summary_changes_one", "sidebar.summary_changes_many")
            )
        if counts["incoming"]:
            parts.append(
                i18n.plural(
                    counts["incoming"], "sidebar.summary_incoming_one", "sidebar.summary_incoming_many"
                )
            )
        self._summary.setText(" · ".join(parts) if parts else i18n.t("sidebar.summary_all_clean"))

    # ------------------------------------------------------------------ filling

    def refresh(self) -> None:
        """
        Rebuilds the whole tree from the registry.

        Returns:
            None
        """

        query = self._search.text()
        groups = self._registry.grouped(self._sort_mode, query)

        self._suppress_selection = True
        self._tree.clear()
        self._rows = {}

        total_shown = 0
        for category, entries in groups:
            total_shown += len(entries)
            parent = QTreeWidgetItem(self._tree)
            parent.setData(0, _ROLE_CATEGORY, category.name)
            parent.setFirstColumnSpanned(True)
            self._style_category_item(parent, category, len(entries))
            for entry in entries:
                child = QTreeWidgetItem(parent)
                child.setData(0, _ROLE_ENTRY_KEY, entry.key)
                row = RepoRow(entry, self._tree)
                row.favorite_toggled.connect(self._on_favorite_toggled)
                self._tree.setItemWidget(child, 0, row)
                self._rows[entry.key] = row
            # A search should reveal what it found, not leave it folded away.
            collapsed = self._registry.is_collapsed(category.name) and not query.strip()
            parent.setExpanded(not collapsed)

        self._suppress_selection = False
        self._restore_selection()
        self.refresh_summary()
        self._update_placeholder(total_shown, query)

    def _style_category_item(self, item: QTreeWidgetItem, category: Category, count: int) -> None:
        """
        Labels and colours a category header row.

        Args:
            item: Tree item to style.
            category: Category being shown.
            count: Number of projects in it.

        Returns:
            None
        """

        colors = get_theme_colors()
        name = category.name or i18n.t("sidebar.uncategorized")
        item.setText(0, f"{name}  ({count})")
        font = QFont(self._tree.font())
        font.setBold(True)
        font.setPointSize(max(7, font.pointSize() - 1))
        item.setFont(0, font)
        item.setToolTip(0, name)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setForeground(0, QBrush(QColor(colors.text_muted)))

    def _update_placeholder(self, shown: int, query: str) -> None:
        """
        Puts a message in the tree when it would otherwise be blank.

        Args:
            shown: Number of visible projects.
            query: Active search text.

        Returns:
            None
        """

        if shown or self._registry.entries:
            if not shown and query.strip():
                item = QTreeWidgetItem(self._tree)
                item.setText(0, i18n.t("sidebar.no_match", query=query.strip()))
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        item = QTreeWidgetItem(self._tree)
        item.setText(0, i18n.t("sidebar.empty_title"))
        item.setToolTip(0, i18n.t("sidebar.empty_hint"))
        item.setFlags(Qt.ItemFlag.NoItemFlags)

    def update_entry(self, entry: RepoEntry) -> None:
        """
        Redraws one row in place, without rebuilding the tree.

        Used after a scan so a running check does not make the list jump around
        under the user's cursor.

        Args:
            entry: Repository whose state changed.

        Returns:
            None
        """

        row = self._rows.get(entry.key)
        if row is None:
            self.refresh()
            return
        row.update_entry(entry)

    def select_key(self, key: str) -> None:
        """
        Selects a project by registry key.

        Args:
            key: Key to select.

        Returns:
            None
        """

        self._selected_key = key
        self._restore_selection()

    def _restore_selection(self) -> None:
        """
        Re-selects the remembered project after a rebuild.

        Returns:
            None
        """

        if not self._selected_key:
            return
        for index in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(index)
            if parent is None:
                continue
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                if child is not None and child.data(0, _ROLE_ENTRY_KEY) == self._selected_key:
                    self._suppress_selection = True
                    self._tree.setCurrentItem(child)
                    self._suppress_selection = False
                    return

    # ------------------------------------------------------------------ events

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        """
        Emits the selection change.

        Args:
            current: Newly current item.
            _previous: Previously current item.

        Returns:
            None
        """

        if self._suppress_selection or current is None:
            return
        key = current.data(0, _ROLE_ENTRY_KEY)
        if isinstance(key, str) and key and key != self._selected_key:
            self._selected_key = key
            self.repo_selected.emit(key)

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        """
        Remembers that a category was unfolded.

        Args:
            item: The category item.

        Returns:
            None
        """

        name = item.data(0, _ROLE_CATEGORY)
        if isinstance(name, str):
            self._registry.set_collapsed(name, False)
            self.registry_changed.emit()

    def _on_collapsed(self, item: QTreeWidgetItem) -> None:
        """
        Remembers that a category was folded.

        Args:
            item: The category item.

        Returns:
            None
        """

        name = item.data(0, _ROLE_CATEGORY)
        if isinstance(name, str):
            self._registry.set_collapsed(name, True)
            self.registry_changed.emit()

    def _on_favorite_toggled(self, key: str) -> None:
        """
        Pins or unpins a project and re-sorts the list.

        Args:
            key: Registry key.

        Returns:
            None
        """

        entry = self._find(key)
        if entry is None:
            return
        self._registry.toggle_favorite(entry)
        self.registry_changed.emit()
        self.refresh()

    def _find(self, key: str) -> RepoEntry | None:
        """
        Looks up an entry by key.

        Args:
            key: Registry key.

        Returns:
            RepoEntry | None: The entry, or None.
        """

        for entry in self._registry.entries:
            if entry.key == key:
                return entry
        return None

    def _show_context_menu(self, position) -> None:  # noqa: ANN001 - Qt passes a QPoint
        """
        Opens the right-click menu for whatever was clicked.

        Args:
            position: Click position in viewport coordinates.

        Returns:
            None
        """

        item = self._tree.itemAt(position)
        if item is None:
            self._show_background_menu(position)
            return
        key = item.data(0, _ROLE_ENTRY_KEY)
        if isinstance(key, str) and key:
            self._show_repo_menu(item, key, position)
            return
        name = item.data(0, _ROLE_CATEGORY)
        if isinstance(name, str):
            self._show_category_menu(name, position)

    def _show_background_menu(self, position) -> None:  # noqa: ANN001
        """
        Opens the menu for empty space in the list.

        Args:
            position: Click position.

        Returns:
            None
        """

        menu = QMenu(self)
        menu.addAction(i18n.t("sidebar.add_repo"), self.add_requested.emit)
        menu.addAction(i18n.t("sidebar.clone_repo"), self.clone_requested.emit)
        menu.addSeparator()
        menu.addAction(i18n.t("category.new"), self._prompt_new_category)
        menu.addSeparator()
        menu.addAction(i18n.t("category.expand_all"), lambda: self._set_all_collapsed(False))
        menu.addAction(i18n.t("category.collapse_all"), lambda: self._set_all_collapsed(True))
        menu.exec(self._tree.viewport().mapToGlobal(position))

    def _show_repo_menu(self, item: QTreeWidgetItem, key: str, position) -> None:  # noqa: ANN001
        """
        Opens the menu for one project.

        Args:
            item: The clicked item.
            key: Registry key.
            position: Click position.

        Returns:
            None
        """

        entry = self._find(key)
        if entry is None:
            return
        self._tree.setCurrentItem(item)

        menu = QMenu(self)
        favorite = menu.addAction(
            i18n.t("repo.favorite_off" if entry.favorite else "repo.favorite_on")
        )
        favorite.triggered.connect(lambda: self._on_favorite_toggled(key))
        menu.addAction(i18n.t("repo.check_now"), lambda: self.check_requested.emit(key))
        menu.addSeparator()
        menu.addAction(i18n.t("repo.open_folder"), lambda: self.open_folder_requested.emit(key))
        remote_action = menu.addAction(
            i18n.t("repo.open_remote"), lambda: self.open_remote_requested.emit(key)
        )
        remote_action.setEnabled(bool(entry.remote_url))
        menu.addSeparator()

        move = menu.addMenu(i18n.t("category.move_to"))
        for category in self._registry.categories:
            action = move.addAction(category.name)
            action.setCheckable(True)
            action.setChecked(entry.category == category.name)
            action.triggered.connect(
                lambda _checked=False, name=category.name: self._move_entry(key, name)
            )
        uncategorized = move.addAction(i18n.t("sidebar.uncategorized"))
        uncategorized.setCheckable(True)
        uncategorized.setChecked(not entry.category)
        uncategorized.triggered.connect(lambda: self._move_entry(key, ""))
        move.addSeparator()
        move.addAction(i18n.t("category.new"), lambda: self._prompt_new_category(assign_key=key))

        menu.addAction(i18n.t("action.rename"), lambda: self.rename_requested.emit(key))
        menu.addSeparator()
        remove = QAction(i18n.t("action.remove"), menu)
        remove.triggered.connect(lambda: self.remove_requested.emit(key))
        menu.addAction(remove)
        menu.exec(self._tree.viewport().mapToGlobal(position))

    def _show_category_menu(self, name: str, position) -> None:  # noqa: ANN001
        """
        Opens the menu for a category header.

        Args:
            name: Category name, empty for the catch-all group.
            position: Click position.

        Returns:
            None
        """

        menu = QMenu(self)
        menu.addAction(i18n.t("category.new"), self._prompt_new_category)
        if name:
            menu.addSeparator()
            menu.addAction(i18n.t("action.rename"), lambda: self._prompt_rename_category(name))
            menu.addAction(i18n.t("action.remove"), lambda: self._prompt_remove_category(name))
        menu.addSeparator()
        menu.addAction(i18n.t("category.expand_all"), lambda: self._set_all_collapsed(False))
        menu.addAction(i18n.t("category.collapse_all"), lambda: self._set_all_collapsed(True))
        menu.exec(self._tree.viewport().mapToGlobal(position))

    # ----------------------------------------------------------- category edits

    def _set_all_collapsed(self, collapsed: bool) -> None:
        """
        Folds or unfolds every category.

        Args:
            collapsed: Whether to fold.

        Returns:
            None
        """

        self._registry.set_all_collapsed(collapsed)
        self.registry_changed.emit()
        self.refresh()

    def _move_entry(self, key: str, category: str) -> None:
        """
        Moves a project into a category.

        Args:
            key: Registry key.
            category: Target category name.

        Returns:
            None
        """

        entry = self._find(key)
        if entry is None:
            return
        self._registry.assign_category(entry, category)
        self.registry_changed.emit()
        self.refresh()

    def _prompt_new_category(self, assign_key: str = "") -> None:
        """
        Asks for a name and creates a category.

        Args:
            assign_key: Registry key to move into the new category, if any.

        Returns:
            None
        """

        name, accepted = QInputDialog.getText(
            self, i18n.t("category.new_title"), i18n.t("category.new_prompt")
        )
        if not accepted or not name.strip():
            return
        created = self._registry.add_category(name)
        if created is None:
            QMessageBox.information(
                self, i18n.t("category.new_title"), i18n.t("category.duplicate", name=name.strip())
            )
            return
        if assign_key:
            self._move_entry(assign_key, created.name)
        self.registry_changed.emit()
        self.refresh()

    def _prompt_rename_category(self, name: str) -> None:
        """
        Asks for a new name and renames a category.

        Args:
            name: Current category name.

        Returns:
            None
        """

        new_name, accepted = QInputDialog.getText(
            self, i18n.t("category.rename_title"), i18n.t("category.rename_prompt"), text=name
        )
        if not accepted or not new_name.strip():
            return
        if not self._registry.rename_category(name, new_name):
            QMessageBox.information(
                self, i18n.t("category.rename_title"), i18n.t("category.duplicate", name=new_name.strip())
            )
            return
        self.registry_changed.emit()
        self.refresh()

    def _prompt_remove_category(self, name: str) -> None:
        """
        Confirms and deletes a category, keeping its projects.

        Args:
            name: Category name.

        Returns:
            None
        """

        answer = QMessageBox.question(
            self,
            i18n.t("category.delete_title"),
            i18n.t("category.delete_prompt", name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._registry.remove_category(name)
        self.registry_changed.emit()
        self.refresh()

