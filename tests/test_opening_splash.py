import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from product_blinder.splash import (
    DEFAULT_SPLASH_MS,
    REDUCED_MOTION_SPLASH_MS,
    create_opening_splash,
    opening_logo_path,
    opening_logo_pixmap,
    reveal_after_splash,
    splash_duration_ms,
)


import pytest


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    from product_blinder.design_widgets import STYLE, light_palette

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(STYLE)
    return app


def test_opening_logo_asset_loads(qt_app):
    path = opening_logo_path()
    assert path.name == "opening-logo.png"
    assert path.is_file()
    pixmap = opening_logo_pixmap(max_height=400)
    assert not pixmap.isNull()
    assert pixmap.height() <= 400
    assert pixmap.width() > 0


def test_create_opening_splash_shows_wordmark(qt_app):
    splash = create_opening_splash()
    assert splash is not None
    assert not splash.pixmap().isNull()
    assert "Product Blinder" in splash.windowTitle()
    splash.close()
    qt_app.processEvents()


def test_splash_duration_respects_env_and_reduced_motion(monkeypatch):
    monkeypatch.delenv("PB_SPLASH_MS", raising=False)
    assert splash_duration_ms(False) == DEFAULT_SPLASH_MS
    assert splash_duration_ms(True) == REDUCED_MOTION_SPLASH_MS
    monkeypatch.setenv("PB_SPLASH_MS", "0")
    assert splash_duration_ms(False) == 0
    assert splash_duration_ms(True) == 0


def test_zero_duration_splash_reveals_main_window(qt_app, tmp_path, monkeypatch):
    from product_blinder.desktop import MainWindow

    monkeypatch.setenv("PB_SPLASH_MS", "0")
    window = MainWindow(tmp_path / "splash.sqlite")
    splash = create_opening_splash()
    reveal_after_splash(qt_app, window, splash, reduced_motion=False)
    qt_app.processEvents()
    assert window.isVisible()
    window.close()
    qt_app.processEvents()


def test_splash_window_is_tight_wordmark_not_tall_poster(qt_app):
    from PySide6.QtCore import Qt
    from product_blinder.splash import MAX_SPLASH_WIDTH

    splash = create_opening_splash()
    pixmap = splash.pixmap()
    assert not pixmap.isNull()
    assert pixmap.width() <= MAX_SPLASH_WIDTH == 380
    assert pixmap.height() <= 300
    assert pixmap.height() / pixmap.width() < 0.85
    assert splash.width() == pixmap.width()
    assert splash.height() == pixmap.height()
    assert bool(splash.windowFlags() & Qt.FramelessWindowHint)
    splash.close()
    qt_app.processEvents()


def test_default_splash_duration_is_about_two_seconds(monkeypatch):
    monkeypatch.delenv("PB_SPLASH_MS", raising=False)
    assert DEFAULT_SPLASH_MS == 2000
    assert splash_duration_ms(False) == 2000
    assert splash_duration_ms(True) == REDUCED_MOTION_SPLASH_MS
