"""Compact frameless opening splash tightly around the Product Blinder wordmark."""

from importlib.resources import files
import os

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QSplashScreen

DEFAULT_SPLASH_MS = 2000
REDUCED_MOTION_SPLASH_MS = 400
# Tight typography box; never stretch a tall cream poster across the desktop.
MAX_SPLASH_WIDTH = 380
CONTENT_PAD = 8
CROP_PAD = 12
CREAM = (254, 250, 239)


def opening_logo_path():
    return files("product_blinder").joinpath("assets/branding/opening-logo.png")


def _is_background(color, reference=CREAM, tolerance=18):
    return (
        abs(color.red() - reference[0]) <= tolerance
        and abs(color.green() - reference[1]) <= tolerance
        and abs(color.blue() - reference[2]) <= tolerance
    )


def crop_wordmark(pixmap, pad=CROP_PAD):
    """Crop empty cream margins so the splash hugs Product / BLINDER / by NCCv3v."""
    if pixmap.isNull():
        return pixmap
    image = pixmap.toImage()
    left, top, right, bottom = image.width(), image.height(), 0, 0
    found = False
    for y in range(image.height()):
        for x in range(image.width()):
            if _is_background(QColor(image.pixel(x, y))):
                continue
            found = True
            left = min(left, x)
            top = min(top, y)
            right = max(right, x)
            bottom = max(bottom, y)
    if not found:
        return pixmap
    box = QRect(
        max(0, left - pad),
        max(0, top - pad),
        min(image.width(), right + 1 + pad) - max(0, left - pad),
        min(image.height(), bottom + 1 + pad) - max(0, top - pad),
    )
    if box.width() <= 0 or box.height() <= 0:
        return pixmap
    return pixmap.copy(box)


def opening_logo_pixmap(max_height=None, max_width=None):
    pixmap = QPixmap(str(opening_logo_path()))
    if pixmap.isNull():
        return pixmap
    pixmap = crop_wordmark(pixmap)
    if max_width is None:
        max_width = MAX_SPLASH_WIDTH
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            # Only shrink on small screens; never scale the badge up toward 40% of a large desktop.
            max_width = min(MAX_SPLASH_WIDTH, max(220, int(screen.availableGeometry().width() * 0.28)))
    if max_height is not None and pixmap.height() > max_height:
        pixmap = pixmap.scaledToHeight(max_height, Qt.SmoothTransformation)
    if max_width and pixmap.width() > max_width:
        pixmap = pixmap.scaledToWidth(max_width, Qt.SmoothTransformation)
    return pixmap


def splash_duration_ms(reduced_motion=False):
    raw = os.getenv("PB_SPLASH_MS")
    if raw is not None and raw != "":
        return max(0, int(raw))
    return REDUCED_MOTION_SPLASH_MS if reduced_motion else DEFAULT_SPLASH_MS


class OpeningSplash(QSplashScreen):
    """Frameless compact wordmark; a click dismisses it early."""

    dismissed = Signal()

    def mousePressEvent(self, event):
        self.dismissed.emit()
        event.accept()


def create_opening_splash():
    pixmap = opening_logo_pixmap()
    if pixmap.isNull():
        return None
    splash = OpeningSplash(pixmap, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
    splash.setWindowFlag(Qt.FramelessWindowHint, True)
    splash.setWindowTitle("Product Blinder")
    splash.setAccessibleName("Product Blinder opening logo by NCCv3v")
    splash.setFixedSize(pixmap.size())
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        splash.move(geo.center().x() - pixmap.width() // 2, geo.center().y() - pixmap.height() // 2)
    return splash


def reveal_after_splash(app, window, splash, reduced_motion=False):
    """Show the compact splash, then reveal the main window. Click or the timer dismisses it."""
    if splash is None:
        window.show()
        return None

    revealed = {"done": False}

    def reveal():
        if revealed["done"]:
            return
        revealed["done"] = True
        window.show()
        window.raise_()
        window.activateWindow()
        splash.finish(window)

    splash.dismissed.connect(reveal)
    splash.show()
    app.processEvents()
    delay = splash_duration_ms(reduced_motion)
    if delay <= 0:
        reveal()
        return None
    timer = QTimer(splash)
    timer.setSingleShot(True)
    timer.timeout.connect(reveal)
    timer.start(delay)
    return timer
