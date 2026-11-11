"""Original MIT sticker assets rendered by native Qt SVG support, not emoji."""

from importlib.resources import files
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel

NAMES = (
    "app",
    "chatbot",
    "dashboard",
    "run",
    "tests",
    "speed",
    "quality",
    "consistency",
    "settings",
    "import",
    "export",
    "evidence",
    "alert",
)


def asset_path(name):
    if name not in NAMES:
        raise ValueError("Unknown packaged icon")
    return str(files("product_blinder").joinpath(f"assets/icons/{name}.svg"))


def icon(name):
    return QIcon(asset_path(name))


def apply_icon(button, name, size=22):
    button.setIcon(icon(name))
    button.setIconSize(QSize(size, size))
    if not button.toolTip():
        button.setToolTip(button.text())
    return button


def icon_label(name, size=28, description=None):
    label = QLabel()
    label.setFixedSize(size, size)
    label.setPixmap(icon(name).pixmap(size, size))
    label.setAccessibleName(description or name.title())
    label.setToolTip(description or name.title())
    return label
