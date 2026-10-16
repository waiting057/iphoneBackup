import sys
from pathlib import Path
from threading import Event

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QListView, QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QFrame, QInputDialog, QListWidgetItem, QSizePolicy, QStackedWidget, QStyledItemDelegate, QVBoxLayout, QWidget)

from .catalog import scan
from .exporter import export_zip, unfinished_exports
from .models import Catalog, format_size
from .usb import Library, Session
from .design import EmptyState, StatCard, STYLE, icon, label
from .voice_usb import VoiceAppSession
from . import voice_backup

ITEM_ROLE = Qt.ItemDataRole.UserRole + 1
HEADER_ROLE = Qt.ItemDataRole.UserRole + 2


class WorkerSignals(QObject):
    finished = Signal(object, object)  # result, error
    progress = Signal(object)


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.function(self.signals.progress.emit)
        except Exception as error:
            self.signals.finished.emit(None, error)
        else:
            self.signals.finished.emit(result, None)


class MediaModel(QAbstractListModel):
    selection_changed = Signal()

    def __init__(self):
        super().__init__()
        self.rows = []
        self.items = ()
        self.selected = set()

    def replace(self, items):
        self.beginResetModel()
        self.items = tuple(items)
        self.selected.clear()
        self.rows = []
        previous = None
        for item in self.items:
            if item.month != previous:
                self.rows.append(item.month)
                previous = item.month
            self.rows.append(item)
        self.endResetModel()
        self.selection_changed.emit()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        header = isinstance(row, str)
        if role == HEADER_ROLE:
            return header
        if role == ITEM_ROLE:
            return None if header else row
        if role == Qt.ItemDataRole.DisplayRole:
            return row if header else row.title
        if role == Qt.ItemDataRole.CheckStateRole and not header:
            return Qt.CheckState.Checked if row.id in self.selected else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.ToolTipRole and not header:
            date = row.captured.isoformat(sep=" ") if row.captured else "未取得可信的拍攝／錄製日期"
            details = [row.title, date]
            if row.duration_seconds is not None:
                details.append(f"錄音長度：{row.duration_seconds:.3f} 秒")
                details.append(f"含 {len(row.files)} 個原始／附屬素材")
            if row.date_source:
                details.append("日期來源：" + row.date_source)
            if row.preview_error:
                details.append(row.preview_error)
            return "\n".join(details)
        return None

    def flags(self, index):
        if index.data(HEADER_ROLE):
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        item = index.data(ITEM_ROLE)
        if role != Qt.ItemDataRole.CheckStateRole or item is None:
            return False
        if value == Qt.CheckState.Checked:
            self.selected.add(item.id)
        else:
            self.selected.discard(item.id)
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.selection_changed.emit()
        return True

    def select_all(self):
        self.selected = set() if len(self.selected) == len(self.items) else {item.id for item in self.items}
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1), [Qt.ItemDataRole.CheckStateRole])
        self.selection_changed.emit()

    def selected_items(self):
        return tuple(item for item in self.items if item.id in self.selected)


class CardDelegate(QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.thumbnails = {}
        self._layout_key = None
        self._card_height = 90

    def editorEvent(self, event, model, option, index):
        # Selection is handled once by the view's clicked signal, including the
        # top-left checkbox. Disable the default delegate's second toggle.
        return False

    def sizeHint(self, option, index):
        if index.data(HEADER_ROLE):
            return QSize(max(160, self.view.viewport().width() - 12), 28)
        model = self.view.model()
        key = (self.view.viewport().width(), self.view.viewport().height(), id(model.items))
        if self._layout_key != key:
            self._layout_key = key
            columns = max(1, (key[0] - 8) // 152)
            headings, rows, column = 0, 0, 0
            # Reserve actual month headings among the first five media rows.
            # Small months must not reduce the promised visible row count.
            for item in model.rows:
                if isinstance(item, str):
                    if rows >= 5:
                        break
                    headings += 1
                    column = 0
                else:
                    if column == 0:
                        if rows >= 5:
                            break
                        rows += 1
                    column = (column + 1) % columns
            available = key[1] - max(headings, 1) * 32 - 28
            self._card_height = min(148, max(54, available // 5))
        return QSize(148, self._card_height)

    def paint(self, painter: QPainter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 3, -4, -3)
        if index.data(HEADER_ROLE):
            painter.setPen(QColor("#45656a"))
            font = QApplication.font()
            font.setPointSize(10)
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            month = index.data()
            if month != "日期未知":
                month = month.replace("-", " 年 ") + " 月"
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, month)
            painter.setPen(QPen(QColor("#e5edeb")))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.restore()
            return
        item = index.data(ITEM_ROLE)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        painter.setBrush(QColor("#eef8f4" if checked else "#ffffff"))
        painter.setPen(QPen(QColor("#168477" if checked else "#e3eae9"), 2 if checked else 1))
        painter.drawRoundedRect(rect, 12, 12)
        image_rect = rect.adjusted(6, 5, -6, -37)
        painter.fillRect(image_rect, QColor("#f0f5f4"))
        if item.thumbnail:
            if item.id not in self.thumbnails:
                pixmap = QPixmap()
                pixmap.loadFromData(item.thumbnail)
                self.thumbnails[item.id] = pixmap
            pixmap = self.thumbnails[item.id].scaled(image_rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(image_rect.center().x() - pixmap.width() // 2, image_rect.center().y() - pixmap.height() // 2, pixmap)
        else:
            painter.setPen(QColor("#7b9692"))
            if item.category == "voice":
                caption = "語音錄音" + (f"\n{item.duration_seconds:.1f} 秒" if item.duration_seconds is not None else "")
            else:
                caption = Path(item.title).suffix.upper().lstrip(".")
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, caption)
        box = image_rect.adjusted(4, 4, 0, 0)
        box.setSize(QSize(18, 18))
        painter.setBrush(QColor("#0f766e" if checked else "#ffffff"))
        painter.setPen(QColor("#0f766e" if checked else "#c4d7d2"))
        painter.drawRoundedRect(box, 4, 4)
        if checked:
            painter.setPen(QPen(QColor("white"), 2))
            painter.drawLine(box.left() + 4, box.top() + 9, box.left() + 7, box.top() + 12)
            painter.drawLine(box.left() + 7, box.top() + 12, box.left() + 14, box.top() + 5)
        painter.setPen(QColor("#24354b"))
        font = QApplication.font()
        font.setPointSize(9)
        painter.setFont(font)
        text_rect = rect.adjusted(6, rect.height() - 34, -6, -18)
        title = painter.fontMetrics().elidedText(item.title, Qt.TextElideMode.ElideMiddle, text_rect.width())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextSingleLine, title)
        painter.setPen(QColor("#63758b"))
        size_rect = rect.adjusted(6, rect.height() - 18, -6, -2)
        painter.drawText(size_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextSingleLine, format_size(item.size))
        painter.restore()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iPhoneBackUp · 開發版")
        self.resize(1200, 850)
        self.setMinimumSize(1000, 760)
        self.pool = QThreadPool(self)
        self.cancel = Event()
        self.busy = False
        self.polling = False
        self.workers = set()
        self.settings = QSettings("iPhoneBackUp", "iPhoneBackUp")
        self.device_ids = ()
        self.model = MediaModel()
        self.has_catalog = False
        self.voice_unavailable = False
        self.backup_reader = None
        self.backup_folder = None
        self.reading_existing_backup = False
        self.model.selection_changed.connect(self.update_selection)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("navigation")
        self.sidebar.setIconSize(QSize(20, 20))
        for text, symbol in (("照片＆影片", "photos"), ("語音備忘錄", "voice")):
            self.sidebar.addItem(QListWidgetItem(icon(symbol, "#a9cfca", 20), text))
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(self.change_page)
        self.devices = QComboBox()
        self.devices.setMinimumWidth(200)
        self.devices.setPlaceholderText("尚未連接裝置")
        self.devices.currentIndexChanged.connect(self.device_changed)
        self.refresh = QPushButton("讀取手機內容")
        self.refresh.setIcon(icon("refresh", "#557770", 16))
        self.refresh.clicked.connect(self.load_catalog)
        self.backup_button = QPushButton("從暫存備份讀取錄音")
        self.backup_button.clicked.connect(self.start_voice_backup)
        self.backup_button.hide()
        self.existing_backup_button = QPushButton("讀取現有備份")
        self.existing_backup_button.clicked.connect(self.load_existing_backup)
        self.existing_backup_button.setToolTip("唯讀使用已完成的備份；不建立新備份，也不自動清除此來源")
        self.existing_backup_button.hide()
        self.cleanup_button = QPushButton("清除暫存")
        self.cleanup_button.clicked.connect(self.clear_backup_clicked)
        self.cleanup_button.hide()
        self.title = QLabel("照片＆影片")
        self.title.setObjectName("pageTitle")
        self.subtitle = label("將珍貴的畫面，安心收藏到電腦。", "muted")
        self.status = QLabel("正在檢查 USB 連線…")
        self.status.setWordWrap(True)
        self.status.setObjectName("smallMuted")
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.connection_badge = label("檢查中", "connectionBadge")
        self.warning = QLabel("開發版：完整手機內容與原始素材保存尚未通過實機驗證。")
        self.warning.setWordWrap(True)
        self.warning.setObjectName("warning")
        self.warning.hide()
        self.warning_button = QPushButton("讀取提示")
        self.warning_button.setToolTip("查看完整讀取限制、日期與預覽狀態")
        self.warning_button.clicked.connect(lambda: QMessageBox.information(self, "讀取提示", self.warning.text()))
        self.stats = label("讀取後依月份整理，點選卡片即可選取。", "smallMuted")
        self.count_card = StatCard("已讀取的檔案項目", "photos", "讀取清單後顯示")
        self.size_card = StatCard("已讀取的總容量", "folder", "含已辨識的輔助檔案")
        self.selected_card = StatCard("這次選取", "check", "尚未選取任何項目")
        self.selection = QLabel("已選取 0 個檔案項目")
        self.selection.setObjectName("smallMuted")
        self.all_button = QPushButton("全選")
        self.all_button.clicked.connect(self.model.select_all)
        self.export_button = QPushButton("匯出 ZIP")
        self.export_button.setObjectName("primary")
        self.export_button.setIcon(icon("download", "#ffffff", 18))
        self.export_button.clicked.connect(self.start_export)
        self.cancel_button = QPushButton("取消工作")
        self.cancel_button.clicked.connect(lambda: self.cancel.set())
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.view = QListView()
        self.view.setObjectName("mediaGrid")
        self.view.setUniformItemSizes(False)
        self.view.setModel(self.model)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setLayoutMode(QListView.LayoutMode.Batched)
        self.view.setBatchSize(100)
        self.view.setSpacing(2)
        self.view.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.delegate = CardDelegate(self.view)
        self.view.setItemDelegate(self.delegate)
        self.view.clicked.connect(self.toggle_item)
        self.empty_state = EmptyState()
        self.library_stack = QStackedWidget()
        self.library_stack.addWidget(self.empty_state)
        self.library_stack.addWidget(self.view)
        self.model.modelReset.connect(self.update_library_state)
        sidebar_panel = QFrame()
        sidebar_panel.setObjectName("sidebar")
        sidebar_panel.setFixedWidth(220)
        nav = QVBoxLayout(sidebar_panel)
        nav.setContentsMargins(16, 20, 16, 16)
        nav.setSpacing(6)
        brand_icon = QLabel()
        brand_icon.setPixmap(icon("phone", "#8ad3bb", 30).pixmap(QSize(30, 30)))
        brand_icon.hide()
        nav.addWidget(label("iPhoneBackUp", "brand"))
        nav.addWidget(label("讓每一份回憶，都有備份。", "brandNote"))
        nav.addSpacing(18)
        nav.addWidget(label("媒體資料庫", "navCaption"))
        self.sidebar.setFixedHeight(112)
        nav.addWidget(self.sidebar)
        nav.addStretch(1)
        nav.addSpacing(18)
        for card in (self.count_card, self.size_card, self.selected_card):
            nav.addWidget(card)
        nav.addSpacing(12)
        safe = label("唯讀存取\n不會刪除或修改手機內容", "sidebarFoot")
        safe.setWordWrap(True)
        nav.addWidget(safe)
        recovery = QPushButton("清除殘留備份")
        recovery.clicked.connect(self.check_previous_backups)
        nav.addWidget(recovery)
        nav.addWidget(label("桌面開發版  /  0.3", "brandNote"))
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(8)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(7)
        titles.addWidget(self.title)
        self.subtitle.hide()
        top.addLayout(titles)
        top.addStretch()
        top.addWidget(self.devices)
        top.addWidget(self.backup_button)
        top.addWidget(self.cleanup_button)
        top.addWidget(self.refresh)
        layout.addLayout(top)
        device_row = QHBoxLayout()
        device_row.addWidget(self.connection_badge)
        device_row.addWidget(self.status, 1)
        device_row.addWidget(self.existing_backup_button)
        device_row.addWidget(self.warning_button)
        layout.addLayout(device_row)
        library = QFrame()
        library.setObjectName("library")
        library_layout = QVBoxLayout(library)
        library_layout.setContentsMargins(10, 8, 10, 6)
        library_layout.setSpacing(4)
        toolbar = QHBoxLayout()
        self.library_title = label("所有照片與影片", "sectionTitle")
        toolbar.addWidget(self.library_title)
        toolbar.addStretch()
        toolbar.addWidget(label("完整內容尚待驗證", "smallMuted"))
        toolbar.addWidget(self.all_button)
        library_layout.addLayout(toolbar)
        self.stats.hide()  # Counts/capacity remain visible in the sidebar.
        library_layout.addWidget(self.library_stack, 1)
        layout.addWidget(library, 1)
        export_bar = QFrame()
        export_bar.setObjectName("exportBar")
        export_layout = QVBoxLayout(export_bar)
        export_layout.setContentsMargins(12, 8, 12, 8)
        export_layout.setSpacing(4)
        footer = QHBoxLayout()
        export_text = QVBoxLayout()
        export_text.setSpacing(4)
        export_text.addWidget(self.selection)
        footer.addLayout(export_text, 1)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.export_button)
        export_layout.addLayout(footer)
        export_layout.addWidget(self.progress)
        layout.addWidget(export_bar)
        shell = QWidget()
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        shell_layout.addWidget(sidebar_panel)
        shell_layout.addWidget(content, 1)
        self.setCentralWidget(shell)
        self.setStyleSheet(STYLE)
        self.set_busy(False)
        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.poll_devices)
        self.timer.start()
        self.poll_devices()
        QTimer.singleShot(0, self.check_previous_exports)
        QTimer.singleShot(0, self.check_previous_backups)

    @property
    def category(self):
        return "photos" if self.sidebar.currentRow() == 0 else "voice"

    def launch(self, function, finished, progress=None):
        worker = Worker(function)
        self.workers.add(worker)
        def complete(result, error):
            try:
                finished(result, error)
            finally:
                self.workers.discard(worker)
        worker.signals.finished.connect(complete)
        if progress:
            worker.signals.progress.connect(progress)
        self.pool.start(worker)

    def poll_devices(self):
        if self.polling:
            return
        self.polling = True
        self.launch(lambda _: Library().devices(), self.devices_ready)

    def check_previous_exports(self):
        folders = self.settings.value("exportFolders", [], type=list)
        def find_leftovers(_):
            leftovers = []
            for folder in folders:
                try:
                    leftovers.extend(unfinished_exports(Path(folder)))
                except OSError:
                    # An external disk may be unavailable at launch; its folder
                    # remains registered for the next launch/output selection.
                    pass
            return leftovers
        def ready(result, error):
            if result:
                QMessageBox.warning(self, "發現未完成匯出", "上次工作留下未完成暫存檔，不能視為成功成品。\n" + "\n".join(str(file) for file in result[:10]))
        self.launch(find_leftovers, ready)

    def release_backup(self):
        folder = self.backup_folder
        self.backup_reader = None
        self.backup_folder = None
        self.cleanup_button.hide()
        if folder:
            self.launch(lambda _: voice_backup.cleanup(folder), self.cleanup_ready)

    def cleanup_ready(self, result, error):
        if error:
            QMessageBox.warning(self, "暫存清除未完成", str(error) + "\n可按「清除殘留備份」重試。")

    def clear_backup_clicked(self):
        if self.busy:
            return
        self.invalidate()

    def check_previous_backups(self):
        if self.busy:
            return
        parents = self.settings.value("backupParents", [], type=list)
        active = self.backup_folder or (self.backup_reader.root if self.backup_reader else None)
        def find(_):
            return tuple(path for path in voice_backup.leftovers(parents) if not active or not Path(active).is_relative_to(path))
        def ready(result, error):
            if self.busy:
                return
            active = self.backup_folder or (self.backup_reader.root if self.backup_reader else None)
            result = tuple(path for path in (result or ()) if not active or not Path(active).is_relative_to(path))
            if not result:
                return
            choice = QMessageBox.question(self, "清除殘留備份", "發現 App 建立的暫存備份，是否清除？\n" + "\n".join(map(str, result)), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if choice == QMessageBox.StandardButton.Yes:
                def remove(_):
                    errors = []
                    for folder in result:
                        try:
                            voice_backup.cleanup(folder)
                        except Exception as failure:
                            errors.append(str(failure))
                    if errors:
                        raise RuntimeError("\n".join(errors))
                self.launch(remove, self.cleanup_ready)
        self.launch(find, ready)

    def load_existing_backup(self):
        if self.busy:
            return
        folder = QFileDialog.getExistingDirectory(self, "選擇已完成的備份（唯讀，不自動刪除此來源）")
        if not folder:
            return
        selected = Path(folder).resolve()
        if self.backup_folder and (selected.is_relative_to(self.backup_folder.resolve()) or self.backup_folder.resolve().is_relative_to(selected)):
            QMessageBox.information(self, "目前備份仍在使用中", "此資料夾是本次暫存備份，清單已載入。請直接選取匯出。")
            return
        self.invalidate()
        self.reading_existing_backup = True
        self.cancel = Event()
        cancellation = self.cancel
        self.set_busy(True)
        self.progress.setRange(0, 0)
        def job(progress):
            reader = voice_backup.open_existing(folder)
            return reader, reader.catalog(cancellation, progress)
        def ready(result, error):
            self.reading_existing_backup = False
            if error or cancellation.is_set():
                self.catalog_ready(self.devices.currentData(), None, error)
                if error:
                    QMessageBox.warning(self, "無法讀取備份", str(error))
                return
            self.backup_reader, catalog = result
            self.catalog_ready(self.devices.currentData(), catalog, None)
            self.status.setText("已唯讀載入現有備份；此來源不會由本視窗自動清除。")
            self.warning.setText(self.warning.text() + "\n此來源只讀取、不自動刪除；若由另一個 App 視窗建立，請在匯出完成前保留該視窗與備份。")
        self.launch(job, ready, self.status.setText)

    def start_voice_backup(self):
        udid = self.devices.currentData()
        if self.busy or not udid or self.category != "voice":
            return
        parent = QFileDialog.getExistingDirectory(self, "選擇暫存備份位置（可選外接硬碟）")
        if not parent:
            return
        limit, accepted = QInputDialog.getInt(self, "備份空間設定", "暫存容量上限（GB，非僅錄音容量）\n保留至少 2 GB 可用空間；每秒檢查，可能短暫超過上限。", 50, 1, 100000)
        if not accepted:
            return
        answer = QMessageBox.question(self, "開始暫存備份", "將建立包含其他手機資料的電腦備份，再讀取其中錄音。\n可能需要較長時間與大量空間，請留意手機上的密碼提示。\n\nZIP 驗證成功、失敗／取消、切換清單或關閉 App 後會清除此暫存。\n加密備份目前不支援讀取；不會變更加密設定。\n\n確定開始？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        parents = self.settings.value("backupParents", [], type=list)
        if parent not in parents:
            self.settings.setValue("backupParents", parents + [parent])
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            QMessageBox.warning(self, "無法開始", "無法記錄暫存位置，尚未開始備份。")
            return
        self.invalidate()
        self.cancel = Event()
        cancellation = self.cancel
        self.set_busy(True)
        self.progress.setRange(0, 0)
        def job(progress):
            folder = voice_backup.create_job(parent)
            try:
                reader = voice_backup.create_backup(folder, udid, limit * voice_backup.GIB, 2 * voice_backup.GIB, cancellation, progress)
                catalog = reader.catalog(cancellation, progress)
                return folder, reader, catalog
            except Exception as error:
                try:
                    voice_backup.cleanup(folder)
                except Exception as failure:
                    raise RuntimeError(str(error) + "\n" + str(failure)) from error
                raise
        def ready(result, error):
            if result:
                self.backup_folder, self.backup_reader, catalog = result
                if cancellation.is_set() or udid != self.devices.currentData():
                    self.release_backup()
                    error = RuntimeError("備份讀取已取消。")
                else:
                    self.catalog_ready(udid, catalog, None)
                    self.status.setText("已從暫存備份讀取；選取完成後匯出，驗證成功即清除備份。")
                    self.cleanup_button.show()
                    return
            self.catalog_ready(udid, None, error)
            if error:
                QMessageBox.warning(self, "備份讀取未完成", str(error))
        self.launch(job, ready, self.status.setText)

    def devices_ready(self, result, error):
        self.polling = False
        if error:
            if self.device_ids and self.backup_reader is None and not self.reading_existing_backup:
                self.invalidate()
            self.device_ids = ()
            self.devices.blockSignals(True)
            self.devices.clear()
            self.devices.blockSignals(False)
            self.status.setText(str(error))
            self.connection_badge.setText("需要設定")
            if self.backup_reader and not self.busy:
                self.connection_badge.setText("本機備份")
                self.status.setText("已載入本機備份，可選取匯出；不需要連接手機。")
            self.refresh.setEnabled(False)
            return
        current = self.devices.currentData()
        if result != self.device_ids:
            self.device_ids = result
            self.devices.blockSignals(True)
            self.devices.clear()
            for position, udid in enumerate(result):
                self.devices.addItem(f"USB 裝置 {position + 1} · {udid[-8:]}", udid)
            if current in result:
                self.devices.setCurrentIndex(result.index(current))
            elif result:
                self.devices.setCurrentIndex(0)
            self.devices.blockSignals(False)
            if current and current not in result and self.backup_reader is None and not self.reading_existing_backup:
                self.invalidate()
                self.status.setText("USB 裝置已中斷，請重新連接並讀取內容。")
            elif not self.busy:
                self.status.setText("偵測到 USB 裝置。請解鎖手機、信任電腦，再讀取內容。" if result else "請使用 USB 連接 iPhone，並解鎖及信任電腦。")
        elif not result and not self.busy:
            self.status.setText("請使用 USB 連接 iPhone，並解鎖及信任電腦。")
        self.refresh.setEnabled(bool(result) and not self.busy)
        self.backup_button.setEnabled(bool(result) and not self.busy)
        if not self.busy:
            self.connection_badge.setText("本機備份" if self.backup_reader else "語音存取受限" if result and self.voice_unavailable else "已偵測裝置" if result else "等待連接")
            if self.backup_reader and not result:
                self.status.setText("已載入本機備份，可選取匯出；不需要連接手機。")

    def invalidate(self):
        self.cancel.set()
        if not self.busy:
            self.release_backup()
        self.has_catalog = False
        self.voice_unavailable = False
        self.model.replace(())
        self.delegate.thumbnails.clear()
        self.stats.setText("讀取後依月份整理，點選卡片即可選取。")
        self.count_card.value.setText("—")
        self.size_card.value.setText("—")
        self.count_card.note.setText("讀取清單後顯示")

    def update_library_state(self):
        self.library_stack.setCurrentIndex(1 if self.model.items else 0)
        if self.busy:
            self.empty_state.title.setText("正在整理手機裡的回憶…")
            self.empty_state.description.setText("讀取清單期間，你可以隨時取消工作。")
        elif self.has_catalog:
            self.empty_state.title.setText("目前沒有可顯示的檔案")
            self.empty_state.description.setText("請查看上方讀取提示；這不代表手機內沒有內容。")
        else:
            self.empty_state.title.setText("讓回憶，在電腦上好好保存")
            self.empty_state.description.setText("連接 iPhone 後，讀取內容並選擇想匯出的檔案。")

    def device_changed(self):
        self.invalidate()

    def change_page(self):
        self.invalidate()
        self.backup_button.setVisible(self.category == "voice")
        self.existing_backup_button.setVisible(self.category == "voice")
        self.backup_button.setEnabled(bool(self.device_ids) and not self.busy)
        self.title.setText("照片＆影片" if self.category == "photos" else "語音備忘錄")
        self.subtitle.setText("將珍貴的畫面，安心收藏到電腦。" if self.category == "photos" else "把熟悉的聲音與重要片刻，保存在身邊。")
        self.library_title.setText("所有照片與影片" if self.category == "photos" else "所有語音備忘錄")
        self.count_card.note.setText("讀取清單後顯示")
        self.warning.setText("完整手機內容與原始素材保存尚未通過實機驗證。")

    def toggle_item(self, index):
        if self.busy or index.data(HEADER_ROLE):
            return
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        self.model.setData(index, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)

    def update_selection(self):
        items = self.model.selected_items()
        unit = "筆錄音" if self.category == "voice" else "個檔案項目"
        self.selection.setText(f"已選 {len(items)} {unit} · {format_size(sum(item.size for item in items))}")
        self.selected_card.value.setText(f"{len(items):,}")
        self.selected_card.note.setText(format_size(sum(item.size for item in items)) + " · 將匯出為 ZIP" if items else "尚未選取任何項目")
        self.export_button.setEnabled(bool(items) and not self.busy)
        self.all_button.setText("取消全選" if items and len(items) == len(self.model.items) else "全選")
        self.all_button.setEnabled(bool(self.model.items) and not self.busy)

    def set_busy(self, value):
        self.busy = value
        self.refresh.setEnabled(bool(self.device_ids) and not value)
        self.backup_button.setEnabled(bool(self.device_ids) and not value)
        self.cleanup_button.setEnabled(not value)
        self.existing_backup_button.setEnabled(not value)
        self.devices.setEnabled(not value)
        self.sidebar.setEnabled(not value)
        self.cancel_button.setEnabled(value)
        self.cancel_button.setVisible(value)
        self.progress.setVisible(value)
        self.view.setEnabled(not value)
        self.connection_badge.setText("處理中" if value else "本機備份" if self.backup_reader else "已偵測裝置" if self.device_ids else "等待連接")
        self.update_library_state()
        self.update_selection()

    def load_catalog(self):
        udid = self.devices.currentData()
        if not udid or self.busy:
            return
        self.invalidate()
        self.cancel = Event()
        self.set_busy(True)
        self.progress.setRange(0, 0)
        self.status.setText("正在取得授權並讀取手機清單…")
        category, cancellation = self.category, self.cancel
        self.launch(lambda progress: scan(udid, category, cancellation, progress),
            lambda result, error: self.catalog_ready(udid, result, error),
            lambda count: self.status.setText(f"讀取中：已處理 {count} 個檔案，統計尚未完成…"))

    def catalog_ready(self, udid, catalog: Catalog | None, error):
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.set_busy(False)
        if self.cancel.is_set() or udid != self.devices.currentData():
            self.status.setText("清單讀取已取消或 USB 已中斷，請重新讀取。")
            return
        if error:
            self.status.setText(str(error))
            self.warning.setText("讀取失敗，不能判定手機沒有內容。")
            return
        if catalog.availability == "unavailable":
            self.voice_unavailable = True
            self.model.replace(())
            self.has_catalog = False
            self.warning.setText("\n".join(catalog.warnings))
            self.status.setText("目前的 USB 方式無法取得語音備忘錄，請查看「讀取提示」。")
            self.connection_badge.setText("語音存取受限")
            self.count_card.value.setText("—")
            self.size_card.value.setText("—")
            self.count_card.note.setText("尚無法取得錄音總數")
            self.empty_state.title.setText("目前無法讀取語音備忘錄")
            self.empty_state.description.setText("手機可能有錄音，但目前的 USB 存取方式無法列出。請按右上方「讀取提示」查看原因。")
            return
        self.has_catalog = True
        self.model.replace(catalog.items)
        self.count_card.value.setText(f"{len(catalog.items):,}")
        self.count_card.note.setText("目前可讀取的錄音筆數" if self.category == "voice" else "目前可讀取的檔案項目")
        self.size_card.value.setText(format_size(sum(item.size for item in catalog.items)))
        self.warning.setText("\n".join(catalog.warnings))
        self.stats.setText(f"目前讀取 {len(catalog.items)} 個檔案項目 · {format_size(sum(item.size for item in catalog.items))}（含已辨識輔助檔）")
        self.status.setText("USB 檔案清單已讀取；完整手機內容尚未驗證。")

    def start_export(self):
        items = self.model.selected_items()
        udid = self.devices.currentData()
        if self.busy or not items or (not udid and self.backup_reader is None):
            return
        path, _ = QFileDialog.getSaveFileName(self, "儲存選取內容", "iPhone-export.zip", "ZIP 檔案 (*.zip)")
        if not path:
            return
        destination = Path(path)
        if self.backup_reader and destination.resolve().is_relative_to((self.backup_folder or self.backup_reader.root).resolve()):
            QMessageBox.warning(self, "請選擇其他匯出位置", "ZIP 請存放在來源備份資料夾之外，以免修改來源或隨暫存一起清除。")
            return
        folders = self.settings.value("exportFolders", [], type=list)
        folder = str(destination.parent)
        if folder not in folders:
            self.settings.setValue("exportFolders", folders + [folder])
            self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError:
                QMessageBox.warning(self, "無法記錄匯出位置", "無法保存異常退出復原資訊，本次匯出尚未開始。請確認電腦設定儲存權限。")
                return
        leftovers = unfinished_exports(destination.parent)
        if leftovers:
            QMessageBox.warning(self, "發現未完成匯出", "此資料夾有未完成暫存檔，不能視為成功成品。\n" + "\n".join(str(file) for file in leftovers[:10]))
        self.cancel = Event()
        cancellation = self.cancel
        self.set_busy(True)
        self.progress.setValue(0)
        self.status.setText("開始匯出所選可讀取檔案…")
        backup_reader = self.backup_reader
        def job(progress):
            sources = {file.source for item in items for file in item.files}
            if len(sources) != 1:
                raise ValueError("選取內容來自不同 USB 介面，請重新讀取清單。")
            source = next(iter(sources))
            if source == "backup":
                if backup_reader is None:
                    raise ValueError("暫存備份已清除，請重新讀取。")
                return export_zip(items, destination, backup_reader.chunks, cancellation, progress)
            connection = Session(udid) if source == "media" else VoiceAppSession(udid, source)
            with connection as session:
                return export_zip(items, destination, session.chunks, cancellation, progress)
        self.launch(job, self.export_ready, self.export_progress)

    def export_progress(self, value):
        amount = value.completed_bytes / max(value.total_bytes, 1)
        percent = int(amount * 50) + (50 if value.phase == "驗證" else 0)
        self.progress.setValue(min(percent, 99))
        self.status.setText(f"{value.phase}中 · {value.completed_files}/{value.total_files} 個檔案 · {format_size(value.completed_bytes)}/{format_size(value.total_bytes)}")

    def export_ready(self, result, error):
        self.set_busy(False)
        if self.backup_folder:
            self.invalidate()
        if error:
            self.progress.setValue(0)
            self.status.setText("整批匯出失敗。")
            QMessageBox.warning(self, "整批匯出失敗", str(error))
        else:
            self.progress.setValue(100)
            self.status.setText("所選可讀取檔案已完成匯出及內容驗證。")
            QMessageBox.information(self, "選取檔案匯出完成", f"{result}\n\n完整手機內容、Live Photo 配對及編輯素材保存仍未通過實機驗證；此訊息只確認本次所選可讀取檔案。")

    def closeEvent(self, event):
        if self.busy:
            self.cancel.set()
            self.status.setText("正在取消工作並清除暫存，完成後可關閉視窗。")
            event.ignore()
        elif self.workers:
            # Avoid destroying Qt objects while a USB polling task still runs.
            self.status.setText("正在完成裝置檢查，請稍後關閉。")
            event.ignore()
        else:
            if self.backup_folder:
                self.invalidate()
                self.status.setText("正在清除暫存備份，完成後可關閉。")
                event.ignore()
                return
            self.timer.stop()
            event.accept()


def configure_application(app):
    # The cards and window use a light design. Set every palette role instead
    # of inheriting white text from macOS/Windows dark mode onto light surfaces.
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#f6f8fb",
        QPalette.ColorRole.WindowText: "#24354b",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#eef2f6",
        QPalette.ColorRole.Text: "#24354b",
        QPalette.ColorRole.Button: "#e9eef5",
        QPalette.ColorRole.ButtonText: "#24354b",
        QPalette.ColorRole.Highlight: "#0f766e",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#ffffff",
        QPalette.ColorRole.ToolTipText: "#24354b",
        QPalette.ColorRole.PlaceholderText: "#63758b",
        QPalette.ColorRole.Light: "#ffffff",
        QPalette.ColorRole.Midlight: "#e9eef5",
        QPalette.ColorRole.Mid: "#b8c4d3",
        QPalette.ColorRole.Dark: "#63758b",
        QPalette.ColorRole.Shadow: "#24354b",
        QPalette.ColorRole.Link: "#174b89",
        QPalette.ColorRole.LinkVisited: "#684b89",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#718096"))
    app.setPalette(palette)


def main():
    app = QApplication(sys.argv)
    configure_application(app)
    app.setApplicationName("iPhoneBackUp")
    window = Window()
    window.show()
    return app.exec()
