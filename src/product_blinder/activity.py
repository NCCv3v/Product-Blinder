"""Native, nonblocking status animation; progress never invents completed work."""

from PySide6.QtCore import Qt, QTimer, QVariantAnimation, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QLabel


class Spinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.angle = 0
        self.active = False
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self.advance)

    def advance(self):
        self.angle = (self.angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#E8EBEF"), 2.5))
        p.drawEllipse(4, 4, 14, 14)
        p.setPen(QPen(QColor("#F47A35" if self.active else "#758399"), 2.5, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(4, 4, 14, 14, int(self.angle * 16), int(235 * 16))


class ActivityStatus(QFrame):
    def __init__(self, progress, parent=None):
        super().__init__(parent)
        self.progress = progress
        self.running = False
        self.reduced = False
        self.state = "idle"
        self.target = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 3, 8, 3)
        self.spinner = Spinner(self)
        self.label = QLabel("Ready · no evaluation running")
        row.addWidget(self.spinner)
        row.addWidget(self.label, 1)
        self.smooth = QPropertyAnimation(progress, b"value", self)
        self.smooth.setDuration(260)
        self.smooth.setEasingCurve(QEasingCurve.OutCubic)
        self.fade = QVariantAnimation(self)
        self.fade.setDuration(1000)
        self.fade.valueChanged.connect(self.tint)

    def tint(self, color):
        self.setStyleSheet(f"ActivityStatus {{background:{color.name()};border-radius:8px;}}")

    def set_reduced_motion(self, value):
        self.reduced = bool(value)
        self.smooth.stop()
        self.fade.stop()
        self.progress.setValue(self.target)
        self.spinner.timer.stop()
        self.tint(QColor("#F6F7F9"))
        if self.running and not self.reduced:
            self.spinner.timer.start()

    def start(self, total):
        self.fade.stop()
        self.smooth.stop()
        self.running = True
        self.state = "running"
        self.target = 0
        self.progress.setRange(0, total * 1000)
        self.progress.setValue(0)
        self.spinner.active = True
        self.spinner.update()
        self.tint(QColor("#FFF4EA"))
        self.label.setText(f"Running · 0/{total} complete (0%) · waiting for measured results")
        if not self.reduced:
            self.spinner.timer.start()

    def update_progress(self, done, total):
        self.target = done * 1000
        self.smooth.stop()
        if self.reduced:
            self.progress.setValue(self.target)
        else:
            self.smooth.setStartValue(self.progress.value())
            self.smooth.setEndValue(self.target)
            self.smooth.start()
        self.label.setText(f"Running · {done}/{total} repetitions complete ({done / total:.0%})")

    def finish(self, state, text):
        self.state = state
        self.running = False
        self.spinner.active = False
        self.spinner.timer.stop()
        self.spinner.update()
        self.smooth.stop()
        self.progress.setValue(self.target)
        self.label.setText(text)
        self.fade.stop()
        color = "#EAF5DD" if state == "complete" else "#FCECE8" if state == "error" else "#FFF2DE"
        self.tint(QColor("#F6F7F9") if self.reduced else QColor(color))
        if not self.reduced:
            self.fade.setStartValue(QColor(color))
            self.fade.setEndValue(QColor("#F6F7F9"))
            self.fade.start()


def score_percent(value):
    return "N/A" if value is None else f"{value * 100:.1f}".rstrip("0").rstrip(".") + "%"


def score_transition(baseline, current):
    return f"{score_percent(baseline)} → {score_percent(current)}"
