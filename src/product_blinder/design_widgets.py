"""Local native design system: light cards, quiet borders, restrained orange/blue/lime."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QScrollArea,
    QPushButton,
    QSizePolicy,
)

COLORS = {
    "ink": "#20252D",
    "muted": "#818893",
    "line": "#E8EBEF",
    "canvas": "#ECEFF5",
    "orange": "#F47A35",
    "purple": "#5B4CDB",
    "blue": "#477DF5",
    "lime": "#E4F48A",
    "red": "#D65373",
}
STYLE = """
QWidget {color:#1C1F27;font-family:'DejaVu Sans';font-size:12px;}
QMainWindow,QWidget#appRoot,QWidget#workspace {background:#ECEFF5;}
QFrame#sidebar {background:#F7F8FC;border-right:0;}
QFrame#navWell {background:transparent;border:0;}
QFrame[role="card"] {background:#FFFFFF;border:0;border-radius:22px;}
QFrame[role="kpi"][tint="peach"] {background:#FFF3E4;border:0;border-radius:22px;}
QFrame[role="kpi"][tint="lavender"] {background:#EEE8FF;border:0;border-radius:22px;}
QFrame[role="kpi"][tint="mint"] {background:#E5F8F3;border:0;border-radius:22px;}
QFrame[role="kpi"][tint="sky"] {background:#E8F0FF;border:0;border-radius:22px;}
QFrame#insightCard {background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #5B4CDB,stop:1 #7A6CF0);border:0;border-radius:22px;}
QFrame#insightCard QLabel,QFrame#insightCard QLabel#eyebrow,QFrame#insightCard QLabel#cardTitle,QFrame#insightCard QLabel#muted {color:#F4F2FF;}
QFrame#insightCard QLabel#insightValue {color:#FFFFFF;}
QFrame#insightCard QPushButton {background:rgba(255,255,255,0.16);color:#FFFFFF;border:0;}
QFrame#insightCard QPushButton:hover {background:rgba(255,255,255,0.24);}
QFrame#dullCard {background:#FFFFFF;border:0;border-radius:22px;}
QLabel {background:transparent;} QLabel#pageTitle {font-size:28px;font-weight:600;letter-spacing:-1px;}
QLabel#cardTitle {font-size:14px;font-weight:600;} QLabel#muted {color:#8A909C;font-size:11px;}
QLabel#eyebrow {color:#8A909C;font-size:10px;font-weight:600;letter-spacing:1px;}
QLabel#brandMark {background:#5B4CDB;color:white;border-radius:12px;font-size:21px;font-weight:600;}
QLabel#metricValue {font-size:32px;letter-spacing:-1px;font-weight:600;}
QLabel#insightValue {font-size:36px;letter-spacing:-1px;color:#FFFFFF;font-weight:600;}
QLabel#dullValue {font-size:40px;letter-spacing:-1px;color:#5B4CDB;font-weight:600;}
QLabel#pill {background:#F0F3F7;color:#637084;border-radius:12px;padding:4px 8px;font-size:10px;}
QPushButton {background:#FFFFFF;border:0;border-radius:14px;padding:9px 14px;color:#424A57;}
QPushButton:hover {background:#F3F4F8;}
QPushButton:pressed {background:#E9EBF1;}
QPushButton:disabled {color:#A8AEB7;background:#F4F5F8;}
QPushButton#primary {background:#5B4CDB;color:white;font-weight:600;}
QPushButton#primary:hover {background:#4C3EC9;}
QPushButton#nav {border:0;background:#F3F4F8;text-align:left;padding:13px 16px;color:#5E6573;border-radius:22px;}
QPushButton#nav:checked {background:#E4F48A;color:#1C1F27;font-weight:600;}
QPushButton#nav:hover {background:#E9EBF3;}
QPushButton#nav:checked:hover {background:#D8EC6F;}
QPushButton#disclosure {background:transparent;border:0;text-align:left;padding:10px 0;color:#637084;font-size:11px;}
QLineEdit,QPlainTextEdit,QComboBox,QSpinBox,QDoubleSpinBox {background:#FFFFFF;border:1px solid #E6E8EE;border-radius:14px;padding:9px 12px;color:#303742;selection-background-color:#E8E4FF;selection-color:#1C1F27;}
QLineEdit:focus,QPlainTextEdit:focus,QComboBox:focus {border-color:#B7B0F0;}
QComboBox {min-height:19px;padding-right:25px;} QComboBox::drop-down {border:0;width:23px;}
QComboBox QAbstractItemView {background:#FFFFFF;color:#1C1F27;selection-background-color:#E4F48A;border:1px solid #E6E8EE;border-radius:12px;}
QCheckBox {spacing:8px;color:#687180;} QCheckBox::indicator {width:15px;height:15px;border:1px solid #CAD1DA;border-radius:4px;background:white;}
QCheckBox::indicator:checked {background:#5B4CDB;border-color:#5B4CDB;}
QGroupBox {border:1px solid #E6E8EE;border-radius:16px;margin-top:15px;padding:18px 12px 12px;}
QGroupBox::title {subcontrol-origin:margin;left:12px;color:#677180;padding:0 5px;}
QTableWidget {background:#FFFFFF;border:0;gridline-color:#F0F1F4;selection-background-color:#F3F0FF;selection-color:#1C1F27;alternate-background-color:#FBFBFD;}
QTableWidget::item {padding:7px;border:0;} QTableWidget::item:selected {background:#F3F0FF;color:#1C1F27;}
QHeaderView::section {background:#F7F8FC;color:#8A909C;border:0;border-bottom:1px solid #ECEEF3;padding:10px 8px;font-weight:500;font-size:10px;}
QScrollArea {background:transparent;border:0;} QScrollBar:vertical {background:#ECEFF5;width:8px;margin:2px;} QScrollBar::handle:vertical {background:#D5DAE3;border-radius:4px;min-height:24px;}
QScrollBar:horizontal {background:#ECEFF5;height:8px;} QScrollBar::handle:horizontal {background:#D5DAE3;border-radius:4px;min-width:24px;}
QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;} QScrollBar::add-page,QScrollBar::sub-page {background:none;}
QProgressBar {background:#E6E8EE;border:0;border-radius:4px;min-height:6px;max-height:6px;color:transparent;}
QProgressBar::chunk {background:#5B4CDB;border-radius:4px;}
QSplitter::handle {background:#E6E8EE;width:8px;height:8px;border-radius:4px;}
QDialog {background:#F4F5F9;} QStatusBar {background:#ECEFF5;color:#8A909C;font-size:10px;border:0;}
QToolTip {background:#FFFFFF;color:#303742;border:1px solid #E6E8EE;padding:6px;border-radius:8px;}
"""


def light_palette():
    p = QPalette()
    for role, color in [
        (QPalette.Window, "#ECEFF5"),
        (QPalette.WindowText, "#20252D"),
        (QPalette.Base, "#FFFFFF"),
        (QPalette.AlternateBase, "#FAFBFC"),
        (QPalette.Text, "#20252D"),
        (QPalette.Button, "#FFFFFF"),
        (QPalette.ButtonText, "#424A57"),
        (QPalette.Highlight, "#E8E4FF"),
        (QPalette.HighlightedText, "#20252D"),
        (QPalette.ToolTipBase, "#FFFFFF"),
        (QPalette.ToolTipText, "#20252D"),
    ]:
        p.setColor(role, QColor(color))
    return p


def label(text="", kind=None):
    w = QLabel(text)
    w.setTextFormat(Qt.PlainText)
    if kind:
        w.setObjectName(kind)
    w.setWordWrap(True)
    return w


def card(title=None, subtitle=None, icon_name=None):
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(12)
    if title:
        if icon_name:
            from .icons import icon_label

            heading = QHBoxLayout()
            heading.setSpacing(9)
            heading.addWidget(icon_label(icon_name, 26, title))
            heading.addWidget(label(title, "cardTitle"), 1)
            layout.addLayout(heading)
        else:
            layout.addWidget(label(title, "cardTitle"))
    if subtitle:
        layout.addWidget(label(subtitle, "muted"))
    return frame, layout


class Disclosure(QWidget):
    """Native progressive disclosure; contents exist but are hidden by default."""

    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.button = QPushButton("›  " + title)
        self.button.setObjectName("disclosure")
        self.button.setCheckable(True)
        self.content = QWidget()
        self.content.setVisible(False)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 4, 0, 4)
        self.button.toggled.connect(lambda checked: self.toggle(checked, title))
        layout.addWidget(self.button)
        layout.addWidget(self.content)

    def toggle(self, checked, title):
        self.button.setText(("⌄  " if checked else "›  ") + title)
        self.content.setVisible(checked)


class MetricCard(QFrame):
    def __init__(self, title, icon_name=None):
        super().__init__()
        self.setProperty("role", "kpi")
        self.setMinimumHeight(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        self.heading = label(title, "muted")
        self.value = label("—", "metricValue")
        self.note = label("No observations yet", "muted")
        self.pill = label("Pending", "pill")
        self.pill.setWordWrap(False)
        row = QHBoxLayout()
        if icon_name:
            from .icons import icon_label

            row.addWidget(icon_label(icon_name, 23, title))
        row.addWidget(self.heading, 1)
        row.addWidget(self.pill)
        layout.addLayout(row)
        layout.addWidget(self.value)
        layout.addWidget(self.note)

    def set_tint(self, tint):
        self.setProperty("role", "kpi")
        self.setProperty("tint", tint)
        self.style().unpolish(self)
        self.style().polish(self)

    def text(self):
        return self.value.text() + " " + self.note.text() + " " + self.pill.text()

    def set_values(self, value, note, pill=None, tone="neutral"):
        self.value.setText(value)
        self.note.setText(note)
        self.pill.setText(pill or "Observed")
        colors = {
            "neutral": ("#F0F3F7", "#637084"),
            "positive": ("#EAF5D4", "#608229"),
            "negative": ("#FDEBF0", "#CC5775"),
            "blue": ("#EBF1FF", "#477DF5"),
            "orange": ("#FFF0E6", "#D76A2A"),
        }
        bg, fg = colors[tone]
        self.pill.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:9px;padding:4px 7px;font-size:10px;"
        )


class ConversationView(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.content = QWidget()
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(0, 4, 4, 4)
        self.layout.setSpacing(14)
        self.layout.addStretch()
        self.setWidget(self.content)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self.scroll_bottom)

    def scroll_bottom(self):
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def appendPlainText(self, text):
        user = "You:" in text
        bubble = QFrame()
        bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        bubble.setStyleSheet(
            "QFrame {background:" + ("#FFF1E7" if user else "#F3F6FA") + ";border:0;border-radius:13px;}"
        )
        body = QVBoxLayout(bubble)
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(5)
        stamp, _, message = text.partition(" HKT  ")
        body.addWidget(label(("You" if user else "Workbench assistant") + "  ·  " + stamp, "muted"))
        message = message.strip().removeprefix("You: ").removeprefix("Assistant: ")
        content = label(message)
        body.addWidget(content)
        wrapper = QWidget()
        wrapper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(24 if user else 0, 0, 0 if user else 24, 0)
        row.addWidget(bubble)
        self.layout.insertWidget(self.layout.count() - 1, wrapper)
        self.scroll_timer.start(0)
