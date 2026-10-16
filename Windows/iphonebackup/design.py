"""Desktop visual components; no device or export behavior."""
from PySide6.QtCore import QByteArray, Qt, QSize
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


PATHS = {
    "photos": '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="9" cy="9" r="2"/><path d="m3 17 5-5 4 4 3-3 6 6"/>',
    "voice": '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',
    "phone": '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 5h4m-3 14h2"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
    "refresh": '<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
    "shield": '<path d="M12 2 3 6v5c0 5 4 9 9 11 5-2 9-6 9-11V6z"/><path d="m8 12 3 3 5-6"/>',
    "folder": '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
}


def icon(name, color="#597278", size=24):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


class StatCard(QFrame):
    def __init__(self, title, symbol, note):
        super().__init__()
        self.setObjectName("statCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(label(title, "muted"))
        top.addStretch()
        image = QLabel()
        image.setPixmap(icon(symbol, "#64827f", 18).pixmap(QSize(18, 18)))
        top.addWidget(image)
        layout.addLayout(top)
        self.value = label("—", "statValue")
        layout.addWidget(self.value)
        self.note = label(note, "smallMuted")
        layout.addWidget(self.note)


class EmptyState(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addStretch()
        illustration = label("", "emptyIllustration")
        self.illustration = illustration
        illustration.setFixedSize(64, 64)
        illustration.setPixmap(icon("phone", "#0f766e", 36).pixmap(QSize(36, 36)))
        illustration.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(illustration, 0, Qt.AlignmentFlag.AlignHCenter)
        self.title = label("讓回憶，在電腦上好好保存", "emptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.description = label("連接 iPhone 後，讀取內容並選擇想匯出的檔案。", "muted")
        self.description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.steps = QWidget()
        steps = QHBoxLayout(self.steps)
        steps.setContentsMargins(0, 6, 0, 0)
        steps.setSpacing(20)
        steps.addStretch()
        for number, text in (("01", "USB 連接手機"), ("02", "解鎖並信任電腦"), ("03", "讀取並選取內容")):
            step = label(f"{number}   {text}", "step")
            steps.addWidget(step)
        steps.addStretch()
        layout.addWidget(self.steps)
        layout.addStretch()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Preserve readable text when the window is short instead of letting
        # the fixed illustration overlap the title or instructions.
        self.illustration.setVisible(event.size().height() >= 190)
        self.steps.setVisible(event.size().height() >= 230)


STYLE = """
QMainWindow { background: #f5f7f8; }
QWidget { font-size: 13px; color: #223b40; }
QFrame#sidebar { background: #112e34; border: none; }
QFrame#sidebar QLabel { color: #9bb4b8; }
QFrame#sidebar QLabel#brand { color: #ffffff; font-size: 21px; font-weight: 700; }
QFrame#sidebar QLabel#brandNote { color: #7c9da3; font-size: 11px; }
QFrame#sidebar QLabel#navCaption { color: #79979c; font-size: 11px; padding-left: 12px; }
QFrame#sidebar QFrame#statCard { background: #1b3b41; border: 1px solid #2b4b51; border-radius: 8px; }
QFrame#sidebar QFrame#statCard QLabel#statValue { color: #edf9f5; font-size: 22px; }
QFrame#sidebar QFrame#statCard QLabel#muted { color: #a8c2c4; font-size: 11px; }
QFrame#sidebar QFrame#statCard QLabel#smallMuted { color: #8faeb2; font-size: 10px; }
QListWidget#navigation { background: transparent; border: none; outline: none; color: #a8c0c3; }
QListWidget#navigation::item { height: 48px; padding: 0 14px; margin: 4px 0; border-radius: 9px; }
QListWidget#navigation::item:selected { background: #24484d; color: #edfffb; }
QListWidget#navigation::item:hover { background: #1c3e44; }
QLabel#sidebarFoot { color: #91b1b5; font-size: 11px; line-height: 1.5; }
QLabel#breadcrumb { color: #829498; font-size: 11px; }
QLabel#pageTitle { font-size: 29px; font-weight: 700; color: #183b40; }
QLabel#muted { color: #6d8388; }
QLabel#smallMuted { color: #7e9297; font-size: 11px; }
QLabel#badge { color: #6d8388; background: #e9eef0; border-radius: 9px; padding: 5px 10px; font-size: 11px; }
QFrame#deviceCard, QFrame#statCard, QFrame#library, QFrame#exportBar { background: #ffffff; border: 1px solid #e1e8ea; border-radius: 12px; }
QLabel#statValue { font-size: 26px; font-weight: 600; color: #203c41; }
QLabel#deviceIcon, QLabel#emptyIllustration { background: #e9f5f1; border-radius: 16px; }
QLabel#connectionBadge { background: #eef3f4; color: #6d8388; padding: 5px 10px; border-radius: 8px; font-size: 11px; }
QLabel#sectionTitle { font-weight: 600; font-size: 14px; }
QLabel#warning { background: #fbf6eb; color: #8a7038; padding: 9px 12px; border-radius: 8px; font-size: 11px; }
QLabel#emptyTitle { color: #28484d; font-size: 20px; font-weight: 600; }
QLabel#step { color: #7d9296; font-size: 11px; }
QPushButton { background: #ffffff; border: 1px solid #dce5e7; border-radius: 7px; padding: 8px 13px; color: #36545a; font-weight: 500; }
QPushButton:hover { background: #f0f6f5; border-color: #9dbeb8; }
QPushButton:pressed { background: #e1eeeb; }
QPushButton:disabled { color: #93a6a9; border-color: #e4eaec; background: #f8fafb; }
QPushButton#primary { background: #0f766e; border-color: #0f766e; color: white; padding: 10px 20px; font-weight: 600; }
QPushButton#primary:hover { background: #0c655e; }
QPushButton#primary:disabled { background: #c8dcd7; border-color: #c8dcd7; color: #f8fffd; }
QComboBox { background: #f8fafb; border: 1px solid #e1e8ea; border-radius: 7px; padding: 9px 12px; color: #36545a; }
QComboBox:disabled { color: #8fa2a6; }
QComboBox::drop-down { border: none; width: 24px; }
QListView#mediaGrid { background: white; border: none; outline: none; }
QProgressBar { background: #edf2f2; border: none; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #24958a; border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #d5dfe0; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""
