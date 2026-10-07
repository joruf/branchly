"""
The dialog that finds the account's repositories on GitHub that Branchly does
not have yet, and clones the ticked ones.

It looks like the search on disk on purpose (``ui.discover_dialog``): the same
two-line rows, the same tick that stands for all of them, the same category to
file the new projects under. What differs is the target folder at the top, since
these projects do not exist locally yet, and that the work after the click is a
clone per project, so each row reports how it went.

Both slow parts run off the GUI thread: reading the list from GitHub through an
``ApiRunner``, and the clones on a thread of their own. The clone worker's
signals go to methods of this dialog, never to lambdas: a lambda runs on the
thread that emits, and a dialog touched from a worker thread freezes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import i18n
from github_api.client import GitHubClient
from gitops import clone as clone_mod
from models.category import Category
from services import git_credentials
from services import github_discovery as discovery
from services.github_discovery import Offer, RemoteRepository
from ui.changes_panel import SelectAllBox
from ui.github_worker import ApiRunner
from ui.widgets import InlineMessage, token_color

_ROLE_INDEX = int(Qt.ItemDataRole.UserRole)


class _BatchCloneWorker(QObject):
    """
    Clones several repositories one after the other, off the GUI thread.

    Attributes:
        item_started: Emitted with the index of the offer being worked on.
        item_done: Emitted with ``(index, ok, message)``.
        finished: Emitted once every offer was handled or the run was cancelled.
    """

    item_started = Signal(int)
    item_done = Signal(int, bool, str)
    finished = Signal()

    def __init__(self, jobs: list[tuple[int, Offer]], credentials: dict[str, dict[str, str]]) -> None:
        """
        Args:
            jobs: ``(row index, offer)`` for every ticked row.
            credentials: Login per clone address, read on the GUI thread.
        """

        super().__init__()
        self._jobs = jobs
        self._credentials = credentials
        self._cancelled = False

    def cancel(self) -> None:
        """
        Asks the run to stop: the clone in progress ends, nothing new starts.

        Returns:
            None
        """

        self._cancelled = True

    def run(self) -> None:
        """
        Works through the jobs.

        Returns:
            None
        """

        for index, offer in self._jobs:
            if self._cancelled:
                break
            self.item_started.emit(index)
            if offer.state == discovery.STATE_ON_DISK:
                # Already there: nothing to clone, only to add.
                self.item_done.emit(index, True, "")
                continue
            request = clone_mod.prepare(
                offer.repository.clone_url, offer.target.parent, offer.target.name
            )
            if request is None:
                self.item_done.emit(index, False, i18n.t("clone.invalid_url"))
                continue
            result = clone_mod.clone(
                request,
                should_cancel=lambda: self._cancelled,
                credentials=self._credentials.get(offer.repository.clone_url),
            )
            self.item_done.emit(index, not result.failed, result.message)
        self.finished.emit()


class GitHubDiscoverDialog(QDialog):
    """
    Lists the account's repositories on GitHub and clones the ticked ones.

    Attributes:
        added: Working trees that can now go into the project list, filled as
            the clones finish.
        category: Category the new projects go into.
    """

    def __init__(
        self,
        client: GitHubClient,
        known: dict[str, str],
        categories: list[Category],
        folder: Path,
        parent: QWidget | None = None,
        load: bool = True,
    ) -> None:
        """
        Args:
            client: The API client, signed in.
            known: ``slug_key`` of every project Branchly has, mapped to its name.
            categories: Categories offered for filing the new projects.
            folder: Where new clones go, to start with.
            parent: Parent widget.
            load: Whether to ask GitHub right away. Off only for tests and
                screenshots that hand in the list themselves.
        """

        super().__init__(parent)
        self.setWindowTitle(i18n.t("github_discover.title"))
        self.setMinimumSize(720, 560)

        self.added: list[Path] = []
        self.category = ""
        self._client = client
        self._known = known
        self._repositories: list[RemoteRepository] = []
        self._offers: list[Offer] = []
        self._runner = ApiRunner(self)
        self._thread: QThread | None = None
        self._worker: _BatchCloneWorker | None = None
        self._results: dict[int, tuple[bool, str]] = {}
        self._moved_known: dict[str, str] | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(16, 16, 16, 16)
        column.setSpacing(10)

        intro = QLabel(i18n.t("github_discover.intro"), self)
        intro.setWordWrap(True)
        intro.setObjectName("Muted")
        column.addWidget(intro)

        column.addWidget(self._build_folder_row(folder))

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)
        column.addWidget(self._progress)

        self._status = QLabel("", self)
        self._status.setObjectName("Muted")
        column.addWidget(self._status)

        self._list = QListWidget(self)
        self._list.setToolTip(i18n.t("tip.github_discover_list"))
        self._list.itemChanged.connect(self._on_item_changed)
        column.addWidget(self._list, 1)

        column.addWidget(self._build_selection_row(categories))

        self._notice = InlineMessage("", "", "info", self)
        self._notice.setVisible(False)
        column.addWidget(self._notice)

        self._buttons = QDialogButtonBox(self)
        self._apply_button = self._buttons.addButton(
            i18n.t("github_discover.apply"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._apply_button.setObjectName("Primary")
        self._apply_button.setToolTip(i18n.t("tip.github_discover_apply"))
        self._close_button = self._buttons.addButton(
            i18n.t("action.cancel"), QDialogButtonBox.ButtonRole.RejectRole
        )
        self._buttons.accepted.connect(self.start_cloning)
        self._buttons.rejected.connect(self.reject)
        column.addWidget(self._buttons)

        self._update_buttons()
        if load:
            self.reload()

    # ------------------------------------------------------------------- build

    def _build_folder_row(self, folder: Path) -> QWidget:
        """
        Builds the target folder field with its browse and reload buttons.

        Args:
            folder: Folder to start with.

        Returns:
            QWidget: The row.
        """

        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        row.addWidget(QLabel(i18n.t("github_discover.folder_label"), holder))
        self._folder = QLineEdit(str(folder), holder)
        self._folder.setToolTip(i18n.t("tip.github_discover_folder"))
        self._folder.editingFinished.connect(self._replan)
        row.addWidget(self._folder, 1)

        browse = QPushButton(i18n.t("action.browse"), holder)
        browse.setToolTip(i18n.t("tip.github_discover_browse"))
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)

        self._reload_button = QPushButton(i18n.t("discover.rescan"), holder)
        self._reload_button.setToolTip(i18n.t("tip.github_discover_reload"))
        self._reload_button.clicked.connect(self.reload)
        row.addWidget(self._reload_button)
        return holder

    def _build_selection_row(self, categories: list[Category]) -> QWidget:
        """
        Builds the tick for all rows and the category picker.

        Args:
            categories: Categories to offer.

        Returns:
            QWidget: The row.
        """

        holder = QWidget(self)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        self._select_all = SelectAllBox("", holder)
        self._select_all.setTristate(True)
        self._select_all.setToolTip(i18n.t("tip.discover_select_all"))
        self._select_all.clicked.connect(self._on_select_all_clicked)
        row.addWidget(self._select_all)

        row.addStretch(1)
        row.addWidget(QLabel(i18n.t("discover.category_label"), holder))
        self._category = QComboBox(holder)
        self._category.setToolTip(i18n.t("tip.discover_category"))
        self._category.addItem(i18n.t("sidebar.uncategorized"), "")
        for category in categories:
            if not category.is_uncategorized:
                self._category.addItem(category.name, category.name)
        row.addWidget(self._category)
        return holder

    # ----------------------------------------------------------------- listing

    @property
    def folder(self) -> Path:
        """
        Returns the folder new clones go into.

        Returns:
            Path: The folder as typed, with ``~`` expanded.
        """

        return Path(self._folder.text().strip() or str(Path.home())).expanduser()

    def reload(self) -> None:
        """
        Asks GitHub for the account's repositories again.

        Returns:
            None
        """

        if self._thread is not None:
            return
        self._notice.setVisible(False)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._reload_button.setEnabled(False)
        self._status.setText(i18n.t("github_discover.loading"))
        self._runner.submit(self._load, self._on_loaded)

    def _load(self) -> object:
        """
        Fetches the list, on the runner's thread, and follows renamed projects.

        Returns:
            object: The ``ApiResult`` of the listing.
        """

        result = self._client.repositories(affiliation="owner")
        if getattr(result, "ok", False):
            listed = [RemoteRepository.from_model(item) for item in result.payload or []]

            def current_name(owner: str, repo: str) -> str:
                answer = self._client.repository(owner, repo)
                return str(getattr(answer.payload, "full_name", "")) if answer.ok else ""

            self._moved_known = discovery.follow_moves(self._known, listed, current_name)
        return result

    def _on_loaded(self, outcome: object) -> None:
        """
        Takes the list GitHub sent.

        Args:
            outcome: An ``ApiResult`` or the exception the call raised.

        Returns:
            None
        """

        self._progress.setVisible(False)
        self._reload_button.setEnabled(True)
        ok = getattr(outcome, "ok", False)
        if not ok:
            key = getattr(outcome, "error_key", "") or "error.git_failed"
            self._notice.set_message(i18n.t("github_discover.load_failed"), i18n.t(key), "danger")
            self._notice.setVisible(True)
            self._status.setText("")
            return
        payload = getattr(outcome, "payload", [])
        if self._moved_known is not None:
            self._known = self._moved_known
        self.set_repositories([RemoteRepository.from_model(item) for item in payload or []])

    def set_repositories(self, repositories: list[RemoteRepository]) -> None:
        """
        Shows a list of repositories.

        Args:
            repositories: The account's repositories.

        Returns:
            None
        """

        self._repositories = repositories
        self._replan()

    def _replan(self) -> None:
        """
        Sorts the repositories against the current folder and redraws.

        Returns:
            None
        """

        if self._thread is not None:
            return
        self._offers = discovery.plan(self._repositories, self._known, self.folder)
        self._results = {}
        self._fill()

    def _fill(self) -> None:
        """
        Rebuilds the list from the current offers.

        Returns:
            None
        """

        self._list.blockSignals(True)
        self._list.clear()
        for index, offer in enumerate(self._offers):
            item = QListWidgetItem(self._row_text(index, offer), self._list)
            item.setData(_ROLE_INDEX, index)
            item.setToolTip(offer.repository.description or offer.repository.full_name)
            if offer.offered:
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(Qt.CheckState.Checked)
            else:
                # Nothing to do with these, so they are shown but not offered.
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setForeground(QColor(token_color("text_muted")))
        self._list.blockSignals(False)

        fresh = sum(1 for offer in self._offers if offer.offered)
        if not self._offers:
            self._status.setText(i18n.t("github_discover.none_found"))
        else:
            self._status.setText(
                i18n.t("github_discover.summary", new=fresh, known=len(self._offers) - fresh)
            )
        self._update_buttons()

    def _row_text(self, index: int, offer: Offer) -> str:
        """
        Describes one repository in two lines, like the search on disk does.

        Args:
            index: Row index, to look up how a clone went.
            offer: The repository and its state.

        Returns:
            str: Name on the first line, the facts on the second.
        """

        repository = offer.repository
        parts = [i18n.t("github_discover.private" if repository.private else "github_discover.public")]
        if repository.pushed_at:
            parts.append(i18n.t("github_discover.pushed", date=repository.pushed_at[:10]))
        if offer.state == discovery.STATE_KNOWN:
            parts.append(i18n.t("github_discover.state_known", name=offer.known_name))
        elif offer.state == discovery.STATE_ON_DISK:
            parts.append(i18n.t("github_discover.state_on_disk", path=str(offer.target)))
        elif offer.state == discovery.STATE_BLOCKED:
            parts.append(i18n.t("github_discover.state_blocked", path=str(offer.target)))
        if index in self._results:
            ok, message = self._results[index]
            parts.append(
                i18n.t("github_discover.done")
                if ok
                else i18n.t("github_discover.failed", reason=message.splitlines()[-1] if message else "")
            )
        elif repository.description:
            parts.append(repository.description)
        return f"{repository.name}\n{'  ·  '.join(parts)}"

    # --------------------------------------------------------------- selection

    def checked_offers(self) -> list[tuple[int, Offer]]:
        """
        Returns the ticked rows.

        Returns:
            list[tuple[int, Offer]]: Row index and offer, in list order.
        """

        picked: list[tuple[int, Offer]] = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            index = item.data(_ROLE_INDEX)
            if isinstance(index, int) and self._offers[index].offered:
                picked.append((index, self._offers[index]))
        return picked

    def _offerable_items(self) -> list[QListWidgetItem]:
        """
        Collects the rows that can be ticked.

        Returns:
            list[QListWidgetItem]: Those rows.
        """

        return [
            self._list.item(row)
            for row in range(self._list.count())
            if self._list.item(row).flags() & Qt.ItemFlag.ItemIsUserCheckable
        ]

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        """
        Follows a click on one row's tick.

        Args:
            _item: The row.

        Returns:
            None
        """

        self._update_buttons()

    def _on_select_all_clicked(self) -> None:
        """
        Ticks or unticks every offered row, as the box now says.

        Returns:
            None
        """

        state = (
            Qt.CheckState.Checked
            if self._select_all.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Unchecked
        )
        self._list.blockSignals(True)
        for item in self._offerable_items():
            item.setCheckState(state)
        self._list.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self) -> None:
        """
        Matches the tick for all rows and the buttons to what is ticked.

        Returns:
            None
        """

        offerable = len(self._offerable_items())
        count = len(self.checked_offers())
        busy = self._thread is not None
        if count == 0:
            state = Qt.CheckState.Unchecked
        elif count == offerable:
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self._select_all.setCheckState(state)
        self._select_all.setEnabled(offerable > 0 and not busy)
        self._select_all.setText(
            i18n.t("changes.selected_count", selected=count, total=offerable) if offerable else ""
        )
        self._apply_button.setEnabled(count > 0 and not busy)
        self._apply_button.setText(
            i18n.t("github_discover.apply_count", count=count) if count else i18n.t("github_discover.apply")
        )

    # ----------------------------------------------------------------- folders

    def _on_browse(self) -> None:
        """
        Asks for the folder new clones go into.

        Returns:
            None
        """

        chosen = QFileDialog.getExistingDirectory(
            self, i18n.t("github_discover.folder_label"), str(self.folder)
        )
        if chosen:
            self._folder.setText(chosen)
            self._replan()

    # ----------------------------------------------------------------- cloning

    def start_cloning(self) -> None:
        """
        Clones the ticked repositories one after the other.

        Returns:
            None
        """

        jobs = self.checked_offers()
        if not jobs or self._thread is not None:
            return
        self.category = str(self._category.currentData() or "")
        # The keychain is read here, on the GUI thread, once per address.
        credentials = {
            offer.repository.clone_url: git_credentials.for_url(offer.repository.clone_url)
            for _index, offer in jobs
        }

        self._progress.setRange(0, len(jobs))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._list.setEnabled(True)
        self._folder.setEnabled(False)
        self._reload_button.setEnabled(False)
        self._category.setEnabled(False)
        self._status.setText(i18n.t("github_discover.cloning", count=len(jobs)))

        worker = _BatchCloneWorker(jobs, credentials)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.item_started.connect(self._on_item_started)
        worker.item_done.connect(self._on_item_done)
        worker.finished.connect(self._on_cloning_finished)
        self._worker = worker
        self._thread = thread
        self._close_button.setText(i18n.t("action.abort"))
        self._update_buttons()
        thread.start()

    def _item_for(self, index: int) -> QListWidgetItem | None:
        """
        Finds the row of an offer.

        Args:
            index: Offer index.

        Returns:
            QListWidgetItem | None: The row.
        """

        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(_ROLE_INDEX) == index:
                return item
        return None

    def _on_item_started(self, index: int) -> None:
        """
        Marks the row being cloned.

        Args:
            index: Offer index.

        Returns:
            None
        """

        item = self._item_for(index)
        if item is not None:
            self._list.scrollToItem(item)
            item.setText(f"{self._offers[index].repository.name}\n{i18n.t('github_discover.working')}")

    def _on_item_done(self, index: int, ok: bool, message: str) -> None:
        """
        Records how one clone went.

        Args:
            index: Offer index.
            ok: Whether it worked.
            message: Git's own words when it did not.

        Returns:
            None
        """

        self._results[index] = (ok, message)
        if ok:
            self.added.append(self._offers[index].target)
        item = self._item_for(index)
        if item is not None:
            self._list.blockSignals(True)
            item.setText(self._row_text(index, self._offers[index]))
            item.setForeground(QColor(token_color("success" if ok else "danger")))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._list.blockSignals(False)
        self._progress.setValue(len(self._results))

    def _on_cloning_finished(self) -> None:
        """
        Winds the clone thread down and reports the totals.

        Returns:
            None
        """

        self._stop_thread()
        self._progress.setVisible(False)
        failed = sum(1 for ok, _message in self._results.values() if not ok)
        done = len(self._results) - failed
        self._status.setText(i18n.t("github_discover.finished", done=done, failed=failed))
        self._notice.set_message(
            i18n.t("github_discover.finished", done=done, failed=failed),
            i18n.t("github_discover.finished_hint") if failed else "",
            "warning" if failed else "success",
        )
        self._notice.setVisible(True)
        self._close_button.setText(i18n.t("action.close"))
        self._apply_button.setVisible(False)
        self._update_buttons()

    def _stop_thread(self) -> None:
        """
        Winds the clone thread down.

        Returns:
            None
        """

        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()

    def reject(self) -> None:
        """
        Stops a running clone before closing.

        The clone in progress is ended and the half-written folder left to git's
        own clean-up; projects that were already cloned stay in ``added``.

        Returns:
            None
        """

        if self._worker is not None:
            self._worker.cancel()
            thread = self._thread
            if thread is not None:
                thread.quit()
                thread.wait(30_000)
            self._thread = None
            self._worker = None
        super().reject()
