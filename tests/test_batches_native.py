import asyncio
import json
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import httpx
import pytest
import respx
from product_blinder.batches import run_batch, estimate, compare_batches
from product_blinder.batch_reports import batch_csv
from product_blinder.models import Case, Suite, RunConfig, TargetConfig
from product_blinder.security import Secrets
from product_blinder.store import Store


def suite(n=12):
    return Suite(
        name="Unit synthetic suite",
        cases=[
            Case(id=f"case-{i}", prompt=f"Question {i}", expected="A", must_include=["A"]) for i in range(n)
        ],
    )


def run(coro):
    return asyncio.run(coro)


def test_five_full_repetitions_default_and_frozen_provenance(tmp_path):
    store = Store(tmp_path / "db")
    s = suite()
    b = run(run_batch(s, TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    assert b["status"] == "complete"
    assert len(b["runs"]) == 5
    assert b["calls"]["target"] == 60
    assert len({r["id"] for r in b["runs"]}) == 5
    assert {r["suite_hash"] for r in b["runs"]} == {s.digest}
    assert [r["repetition"] for r in b["runs"]] == [1, 2, 3, 4, 5]
    assert [r["recorded_seed"] for r in b["runs"]] == [42, 43, 44, 45, 46]
    assert all(r["batch_id"] == b["id"] and not r["provider_seed_applied"] for r in b["runs"])
    assert b["aggregate"]["score_mean"] == 1
    assert b["aggregate"]["score_sd"] == 0
    assert b["aggregate"]["output_consistency_mean"] == 1
    assert b["aggregate"]["score_sample_count"] == 5
    assert b["aggregate"]["performance"]["attempted_cases"] == 60
    assert all(
        r["performance"]["duration_s"] > 0
        and r["performance"]["latency_ms_p95"] >= r["performance"]["latency_ms_p50"]
        for r in b["runs"]
    )
    assert all(r["performance"]["target_success_rate"] == 1 for r in b["runs"])
    assert len(store.history()) == 5 and len(store.batches()) == 1


def test_five_repeat_baseline_comparison_no_pseudo_replication(tmp_path):
    store = Store(tmp_path / "db")
    s = suite()
    cfg = RunConfig(max_calls=1000)
    first = run(run_batch(s, TargetConfig(), cfg, Secrets(), store))
    store.set_batch_baseline(first["id"])
    second = run(run_batch(s, TargetConfig(fixture="degraded"), cfg, Secrets(), store))
    assert second["comparison"]["verdict"] == "regression"
    assert second["comparison"]["paired_cases"] == 12
    assert second["comparison"]["independent_units"] == 12  # NOT 60
    assert second["comparison"]["repeat_noise"]["candidate_score_sd"] == 0
    assert len(store.history()) == 10


def test_batch_config_or_repetition_change_rejected(tmp_path):
    store = Store(tmp_path / "db")
    s = suite()
    a = run(run_batch(s, TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    b = run(run_batch(s, TargetConfig(), RunConfig(max_calls=1000, concurrency=2), Secrets(), store))
    assert compare_batches(a, b)["verdict"] == "insufficient-data"
    b["batch_scoring_hash"] = a["batch_scoring_hash"]
    b["repetitions"] = 4
    assert compare_batches(a, b)["verdict"] == "insufficient-data"


def test_cancel_before_start_preserves_five_missing_slots(tmp_path):
    event = threading.Event()
    event.set()
    store = Store(tmp_path / "db")
    b = run(
        run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store, cancel_event=event)
    )
    assert b["status"] == "cancelled" and b["runs"] == []
    assert len(b["repetition_records"]) == 5
    assert all(x["status"] == "not-run-cancelled" and x["run_id"] is None for x in b["repetition_records"])
    assert b["aggregate"]["score_mean"] is None
    assert b["comparison"]["verdict"] == "insufficient-data"
    assert b["calls"]["target"] == 0
    with pytest.raises(ValueError):
        store.set_batch_baseline(b["id"])


def test_cancel_in_flight_stops_target_tasks_without_fake_results(tmp_path):
    async def scenario():
        event = threading.Event()
        with respx.mock as mock:

            async def slow(request):
                await asyncio.sleep(2)
                return httpx.Response(200, json={"output": "A"})

            mock.post("http://localhost:9000/test").mock(side_effect=slow)
            task = asyncio.create_task(
                run_batch(
                    suite(),
                    TargetConfig(kind="http", url="http://localhost:9000/test"),
                    RunConfig(max_calls=1000, target_call_cap_usd=0.001),
                    Secrets(),
                    Store(tmp_path / "db"),
                    consent=True,
                    cancel_event=event,
                )
            )
            await asyncio.sleep(0.05)
            event.set()
            b = await asyncio.wait_for(task, 1)
        assert b["status"] == "cancelled"
        assert b["runs"][0]["status"] == "cancelled"
        assert b["runs"][0]["traces"] == []
        assert b["aggregate"]["score_mean"] is None
        assert b["runs"][0]["missing_case_ids"]

    run(scenario())


def test_budget_shared_across_full_batch_partial_stops_no_zeros(tmp_path):
    async def scenario():
        with respx.mock as mock:
            route = mock.post("http://localhost:9000/test").mock(
                return_value=httpx.Response(200, json={"output": "A"})
            )
            b = await run_batch(
                suite(2),
                TargetConfig(kind="http", url="http://localhost:9000/test"),
                RunConfig(max_calls=10, target_call_cap_usd=0.01, budget_usd=0.03, retry=0),
                Secrets(),
                Store(tmp_path / "db"),
                consent=True,
            )
        assert route.call_count == 3 and b["calls"]["target"] == 3
        assert b["reserved_budget_usd"] == 0.03
        assert b["status"] == "incomplete" and b["aggregate"]["complete_repetitions"] == 1
        assert b["repetition_records"][1]["status"] == "incomplete"
        assert b["repetition_records"][2]["run_id"] is None
        assert b["comparison"]["verdict"] == "insufficient-data"
        assert b["runs"][1]["traces"][1]["score"] is None

    run(scenario())


def test_batch_attempt_ceiling_preflight_and_paid_consent(tmp_path):
    for cfg, consent in [
        (RunConfig(max_calls=10), False),
        (RunConfig(max_calls=1000, target_call_cap_usd=0.01), False),
    ]:
        with pytest.raises(ValueError):
            run(
                run_batch(
                    suite(),
                    TargetConfig(kind="http", url="http://localhost:9000/test"),
                    cfg,
                    Secrets(),
                    Store(tmp_path / "db"),
                    consent=consent,
                )
            )


def test_estimate_includes_five_complete_judge_suites():
    s = Suite(
        name="x",
        cases=[
            Case(id="a", prompt="Q", expected="A", evidence=["A"]),
            Case(id="b", prompt="Q", rubric="Human criterion"),
        ],
    )
    cfg = RunConfig(use_judge=True, target_call_cap_usd=0.01, judge_call_cap_usd=0.02)
    quote = estimate(s, TargetConfig(kind="http"), cfg)
    assert quote["minimum_target_attempts"] == 10
    assert quote["minimum_judge_attempts"] == 15
    assert quote["planned_initial_reservation_usd"] == pytest.approx(0.4)


def test_no_secret_persistence_batch_or_progress(tmp_path):
    store = Store(tmp_path / "db")
    progress = []
    b = run(
        run_batch(
            suite(2),
            TargetConfig(),
            RunConfig(max_calls=20),
            Secrets("private-key-value"),
            store,
            on_progress=progress.append,
        )
    )
    assert "private-key-value" not in json.dumps(b)
    assert b["id"] == store.get_batch(b["id"])["id"]
    assert "private-key-value".encode() not in (tmp_path / "db").read_bytes()
    assert len(progress) >= 6


def test_missing_repetition_csv_empty_not_zero(tmp_path):
    event = threading.Event()
    event.set()
    b = run(
        run_batch(
            suite(),
            TargetConfig(),
            RunConfig(max_calls=1000),
            Secrets(),
            Store(tmp_path / "db"),
            cancel_event=event,
        )
    )
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(batch_csv(b))))
    assert len(rows) == 5
    assert all(r["score"] == "" and r["duration_s"] == "" and r["ttft_ms"] == "" for r in rows)


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from product_blinder.design_widgets import STYLE, light_palette

    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(STYLE)
    yield app
    from PySide6.QtCore import QCoreApplication, QEvent
    import shiboken6

    # Delete hidden top-level widget trees before QApplication/interpreter teardown.
    for widget in list(app.topLevelWidgets()):
        if shiboken6.isValid(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_native_two_tab_settings_masks_and_real_canvas(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow, SettingsDialog
    from PySide6.QtWidgets import QLineEdit
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

    window = MainWindow(tmp_path / "gui.sqlite")
    window.show()
    qt_app.processEvents()
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == ["Chatbot", "Dashboard Report"]
    assert window.repetitions.value() == 5
    assert isinstance(window.canvas, FigureCanvasQTAgg)
    assert "160 target attempts" in window.estimate_label.text()
    dialog = SettingsDialog(window.settings, window)
    assert dialog.fields["target_key"].echoMode() == QLineEdit.Password
    assert dialog.fields["judge_key"].echoMode() == QLineEdit.Password
    window.intake.setText("http://localhost:9000/test")
    window.capture_intake()
    assert window.adapter.currentIndex() == 1 and window.url.text() == "http://localhost:9000/test"
    assert window.store.history() == []  # URL intake did not execute anything.
    dialog.close()
    window.close()
    qt_app.processEvents()


def test_native_dashboard_renders_five_real_run_rows_and_chart_widgets(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    store = Store(tmp_path / "gui.sqlite")
    b = run(run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    w = MainWindow(tmp_path / "gui.sqlite")
    w.show()
    qt_app.processEvents()
    assert w.repeat_table.rowCount() == 5
    assert sum(len(fig.axes) for fig in w.figures) == 4
    assert len(w.canvases) == 4
    assert w.case_table.rowCount() == 12
    assert all(w.repeat_table.item(i, 4).text() != "N/A" for i in range(5))
    assert "100.0%" in w.metrics[0].text()
    assert w.rendered_batch["id"] == b["id"]
    w.close()
    qt_app.processEvents()


def test_native_worker_nonblocking_event_loop_and_safe_cancel(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest

    w = MainWindow(tmp_path / "gui.sqlite")
    w.show()
    # Native controls launch worker; no paid API or browser involved.
    w.suite_editor.setPlainText(suite(100).model_dump_json(by_alias=True))
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    w.start_evaluation()
    QTest.qWait(20)
    w.cancel_batch()
    deadline = time.monotonic() + 5
    while w.worker.isRunning() and time.monotonic() < deadline:
        QTest.qWait(20)
    QTest.qWait(30)
    timer.stop()
    assert not w.worker.isRunning()
    assert len(ticks) > 1
    assert w.store.batches()[0]["status"] in ("cancelled", "complete")
    assert w.start_button.isEnabled()
    assert not w.activity.spinner.timer.isActive()
    assert w.activity.state in ("cancelled", "complete")
    w.close()
    qt_app.processEvents()


def test_native_cli_real_process_clean_smoke_exit(tmp_path):
    import subprocess
    import sys

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    output = tmp_path / "smoke.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "product_blinder.desktop",
            "--db",
            str(tmp_path / "smoke.sqlite"),
            "--smoke",
            "--smoke-out",
            str(output),
        ],
        env=env,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode()
    data = json.loads(output.read_text())
    assert data["tabs"] == ["Chatbot", "Dashboard Report"] and not data["browser_required"]
    assert data["native_chart_canvas"] == "FigureCanvasQTAgg"


def test_selectable_native_scenarios_are_real_data_not_mock_ui(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    w = MainWindow(tmp_path / "scenarios.sqlite")
    for index, count in [(0, 32), (1, 12), (2, 12), (3, 24)]:
        w.scenario.setCurrentIndex(index)
        s = Suite.model_validate_json(w.suite_editor.toPlainText())
        assert len(s.cases) == count
        assert w.adapter.currentIndex() == 0
        if index == 2:
            assert all(c.schema_ for c in s.cases)
        if index == 1:
            assert all(c.evidence for c in s.cases)
    w.close()
    qt_app.processEvents()


def test_v03_progressive_disclosure_and_explicit_paid_controls(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    w = MainWindow(tmp_path / "disclosure.sqlite")
    w.show()
    qt_app.processEvents()
    assert w.adapter_disclosure.content.isHidden()
    assert w.suite_disclosure.content.isHidden()
    assert w.rubric_disclosure.content.isHidden()
    assert w.url.isHidden() and w.consent.isHidden() and w.allow_exec.isHidden()
    w.adapter_disclosure.button.click()
    qt_app.processEvents()
    assert not w.adapter_disclosure.content.isHidden()
    w.adapter.setCurrentIndex(1)
    assert not w.url.isHidden() and not w.consent.isHidden()
    assert not w.consent.isChecked()
    w.adapter.setCurrentIndex(2)
    assert not w.allow_exec.isHidden() and not w.allow_exec.isChecked()
    w.adapter.setCurrentIndex(0)
    w.rubric_disclosure.button.click()
    assert not w.consent.isHidden()  # a separate real rubric request can be consented to
    assert w.store.history() == []
    w.close()
    qt_app.processEvents()


def test_v03_sidebar_keeps_exact_two_native_destinations(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    w = MainWindow(tmp_path / "navigation.sqlite")
    w.show()
    qt_app.processEvents()
    assert w.tabs.count() == 2 and w.tabs.tabBar().isHidden()
    assert [b.text() for b in w.nav_buttons] == ["Chatbot", "Dashboard Report"]
    w.nav_buttons[1].click()
    qt_app.processEvents()
    assert w.tabs.currentIndex() == 1 and w.nav_buttons[1].isChecked()
    assert w.page_title.text() == "Evaluation overview"
    assert w.metrics[0].property("role") == "kpi"
    assert len(w.canvases) == 4
    w.close()
    qt_app.processEvents()


def test_v03_real_case_inspector_filters_and_all_metric_columns(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    store = Store(tmp_path / "inspector.sqlite")
    b = run(run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    w = MainWindow(tmp_path / "inspector.sqlite")
    w.show()
    qt_app.processEvents()
    assert w.expected_view.toPlainText() == "A" and w.output_view.toPlainText() == "A"
    assert w.metric_details.rowCount() == 8
    assert len(json.loads(w.trace.toPlainText())["observations"]) == 5
    assert w.repeat_table.isColumnHidden(14)
    w.all_metrics.setChecked(True)
    assert not w.repeat_table.isColumnHidden(14)
    w.case_query.setText("case-0")
    assert w.case_table.rowCount() == 1
    w.failed_only.setChecked(True)
    assert w.case_table.rowCount() == 0
    w.failed_only.setChecked(False)
    w.case_query.clear()
    assert w.case_table.rowCount() == 12
    assert "prompt" in w.prompt_view.toPlainText()
    w.inspector_rep.setCurrentIndex(4)
    assert w.output_view.toPlainText() == "A"
    w.review_button.click()
    qt_app.processEvents()
    assert w.tabs.currentIndex() == 1
    assert b["status"] == "complete"
    w.close()
    qt_app.processEvents()


def test_v03_cancelled_dashboard_never_fabricates_score(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    event = threading.Event()
    event.set()
    store = Store(tmp_path / "cancelled.sqlite")
    run(run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store, cancel_event=event))
    w = MainWindow(tmp_path / "cancelled.sqlite")
    w.show()
    qt_app.processEvents()
    assert "N/A" in w.metrics[0].text()
    assert not w.baseline_button.isEnabled()
    assert all(w.repeat_table.item(i, 3).text() == "N/A" for i in range(5))
    assert w.case_table.rowCount() == 0
    w.close()
    qt_app.processEvents()


@pytest.mark.parametrize(
    "baseline,current,text",
    [
        (1, 0.40625, "100% → 40.6%"),
        (0, 1, "0% → 100%"),
        (0.4, 0.6, "40% → 60%"),
        (None, None, "N/A → N/A"),
        (1, 0, "100% → 0%"),
    ],
)
def test_v04_percentages_are_scores_not_mislabelled_deltas(baseline, current, text):
    from product_blinder.activity import score_transition

    assert score_transition(baseline, current) == text


@pytest.mark.parametrize("state", ["complete", "error", "cancelled"])
def test_v04_native_animation_progress_and_terminal_cleanup(qt_app, state):
    from product_blinder.activity import ActivityStatus
    from PySide6.QtWidgets import QProgressBar
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QAbstractAnimation

    progress = QProgressBar()
    activity = ActivityStatus(progress)
    activity.show()
    activity.start(5)
    assert activity.spinner.timer.isActive() and activity.running
    before = activity.spinner.angle
    QTest.qWait(120)
    assert activity.spinner.angle != before
    assert progress.value() == 0  # the spinner is activity, not invented progress
    activity.update_progress(2, 5)
    QTest.qWait(120)
    assert 0 <= progress.value() <= 2000 and "40%" in activity.label.text()
    activity.finish(state, state.title())
    assert not activity.spinner.timer.isActive() and not activity.running
    assert progress.value() == 2000 and activity.state == state
    assert activity.smooth.state() == QAbstractAnimation.Stopped
    QTest.qWait(1100)
    assert activity.fade.state() == QAbstractAnimation.Stopped
    activity.close()
    activity.deleteLater()
    progress.deleteLater()
    qt_app.processEvents()


def test_v04_reduced_motion_preserves_truthful_status_without_movement(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow, SettingsDialog
    from PySide6.QtCore import QAbstractAnimation

    w = MainWindow(tmp_path / "reduce.sqlite")
    dialog = SettingsDialog(w.settings, w)
    assert "reduced_motion" in dialog.fields
    dialog.fields["reduced_motion"].setChecked(True)
    w.settings = dialog.values()
    w.activity.set_reduced_motion(True)
    w.config_secrets()  # UI preference must not pollute frozen evaluation configuration
    w.activity.start(5)
    w.activity.update_progress(1, 5)
    assert not w.activity.spinner.timer.isActive()
    assert w.progress.value() == 1000 and "20%" in w.activity.label.text()
    w.activity.finish("complete", "Complete")
    assert w.activity.fade.state() == QAbstractAnimation.Stopped
    w.activity.set_reduced_motion(False)
    w.activity.start(5)
    assert w.activity.spinner.timer.isActive()
    w.on_worker_error("Test setup error")
    assert w.activity.state == "error" and not w.activity.spinner.timer.isActive()
    w.worker_finished()
    assert w.start_button.isEnabled()
    dialog.close()
    w.close()
    qt_app.processEvents()


def test_v04_inspector_clears_and_updates_synchronously_on_cell_or_batch_change(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    s = suite()
    s.cases[1].expected = "A different expected result"
    store = Store(tmp_path / "selection.sqlite")
    stable = run(run_batch(s, TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    faulty = run(run_batch(s, TargetConfig(fixture="degraded"), RunConfig(max_calls=1000), Secrets(), store))
    w = MainWindow(tmp_path / "selection.sqlite")
    w.render_batch(stable)
    w.case_table.setCurrentCell(1, 1)
    assert w.expected_view.toPlainText() == "A different expected result"
    assert w.output_view.toPlainText() == "A different expected result"
    w.case_table.clearSelection()
    assert w.expected_view.toPlainText() == "" and w.output_view.toPlainText() == ""
    w.render_batch(faulty)
    assert w.expected_view.toPlainText() == "A"
    assert w.output_view.toPlainText() == faulty["runs"][0]["traces"][0]["output"]
    w.case_query.setText("no-matching-case")
    assert w.expected_view.toPlainText() == "" and w.output_view.toPlainText() == ""
    assert w.prompt_view.toPlainText() == ""
    w.close()
    qt_app.processEvents()


def test_v04_actual_five_repeat_worker_stops_spinner_on_success(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow
    from PySide6.QtTest import QTest

    w = MainWindow(tmp_path / "actual-five.sqlite")
    w.show()
    w.start_evaluation()
    assert w.activity.running
    deadline = time.monotonic() + 10
    while w.worker.isRunning() and time.monotonic() < deadline:
        QTest.qWait(20)
    QTest.qWait(100)
    assert not w.worker.isRunning() and w.activity.state == "complete"
    assert w.activity.isVisible()  # terminal fade remains visible on Dashboard Report
    assert not w.activity.spinner.timer.isActive()
    assert w.progress.value() == 5000 and "100%" in w.activity.label.text()
    assert len(w.store.batches()) == 1 and len(w.rendered_batch["runs"]) == 5
    w.close()
    qt_app.processEvents()


def test_v04_native_comparison_shows_labelled_baseline_current_and_uncertainty(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    store = Store(tmp_path / "percent.sqlite")
    baseline = run(run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    store.set_batch_baseline(baseline["id"])
    candidate = run(
        run_batch(suite(), TargetConfig(fixture="degraded"), RunConfig(max_calls=1000), Secrets(), store)
    )
    w = MainWindow(tmp_path / "percent.sqlite")
    w.render_batch(candidate)
    assert "BASELINE → CURRENT" in w.insight_badge.text()
    assert w.insight_value.text().startswith("100% → ")
    assert "Baseline: 100%" in w.insight_body.text() and "Current:" in w.insight_body.text()
    assert " pp" not in w.insight_value.text() + w.metrics[0].text()
    assert "percentage points" in w.verdict.text()
    assert w.case_table.horizontalHeaderItem(2).text() == "Baseline → Current"
    w.close()
    qt_app.processEvents()


@pytest.mark.parametrize(
    "name",
    [
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
    ],
)
def test_v05_original_svg_loads_and_renders_at_small_native_sizes(qt_app, name):
    from product_blinder.icons import asset_path, icon
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import Qt

    renderer = QSvgRenderer(asset_path(name))
    assert renderer.isValid()
    assert not icon(name).isNull()
    for size in [24, 32, 64]:
        image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        opaque = sum(image.pixelColor(x, y).alpha() > 0 for x in range(size) for y in range(size))
        assert opaque > size * size * 0.25
        assert image.pixelColor(0, 0).alpha() == 0
        assert not icon(name).pixmap(size, size).isNull()


def test_v05_transparent_app_rasters_and_packaged_resources(qt_app):
    from importlib.resources import files
    from PySide6.QtGui import QImage

    for size in [1024, 256, 64, 32]:
        image = QImage(str(files("product_blinder").joinpath(f"assets/icons/app-{size}.png")))
        assert not image.isNull() and image.width() == size and image.height() == size
        assert image.hasAlphaChannel() and image.pixelColor(0, 0).alpha() == 0


def test_v05_native_app_identity_and_semantic_action_icons(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow
    from PySide6.QtWidgets import QPushButton

    w = MainWindow(tmp_path / "icons.sqlite")
    w.show()
    qt_app.processEvents()
    assert "0.7" in w.windowTitle() and not w.windowIcon().isNull()
    assert w.brand_icon.accessibleName().startswith("Product Blinder")
    from PySide6.QtWidgets import QLabel

    labels = [label.text() for label in w.findChildren(QLabel)]
    assert "Local Store Only" in labels and "Local by design" not in labels
    assert "No cloud sync. No automatic release. Your keys stay in this session." in labels
    assert not w.brand_icon.pixmap().isNull()
    assert [b.text() for b in w.nav_buttons] == ["Chatbot", "Dashboard Report"]
    assert all(not b.icon().isNull() for b in w.nav_buttons)
    assert not w.start_button.icon().isNull() and not w.review_button.icon().isNull()
    buttons = {b.text(): b for b in w.findChildren(QPushButton)}
    for text in ["Import suite", "Export CSV", "Export JSON", "Settings / API keys", "Cancel batch"]:
        assert not buttons[text].icon().isNull() and buttons[text].toolTip()
    w.activity.set_reduced_motion(True)
    w.activity.start(5)
    assert not w.activity.spinner.timer.isActive()
    w.activity.finish("cancelled", "Cancelled")
    w.close()
    qt_app.processEvents()


def test_v06_find_out_problem_and_test_case_wording(qt_app, tmp_path):
    from product_blinder.desktop import MainWindow

    store = Store(tmp_path / "ui.sqlite")
    baseline = run(run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    store.set_batch_baseline(baseline["id"])
    candidate = run(
        run_batch(suite(), TargetConfig(fixture="degraded"), RunConfig(max_calls=1000), Secrets(), store)
    )
    window = MainWindow(tmp_path / "ui.sqlite")
    window.show()
    window.tabs.setCurrentIndex(1)
    qt_app.processEvents()
    assert window.tabs.tabText(1) == "Dashboard Report"
    assert [b.text() for b in window.nav_buttons] == ["Chatbot", "Dashboard Report"]
    assert window.problem_heading.text() == "Find out problem"
    assert window.problem_table.rowCount() >= 1
    assert not window.problem_empty.isVisible()
    assert window.case_table.horizontalHeaderItem(0).text() == "Test Case"
    assert window.case_context.text().startswith("Test Case ")
    assert "100%" in window.analysis_facts.item(0, 1).text()
    assert window.failing_table.rowCount() >= 1
    assert window.suggestions.text().startswith("Suggested next checks")
    window.render_batch(baseline)
    assert window.problem_table.rowCount() == 0
    assert window.problem_empty.isVisible()
    assert candidate["analysis"]["completions"]["display"] == "5/5"
    window.close()
    qt_app.processEvents()


def test_v07_save_to_record_and_how_dull_changed_label(qt_app, tmp_path):
    from PySide6.QtCore import Qt
    from product_blinder.desktop import MainWindow
    from product_blinder.records import record_snapshot

    store = Store(tmp_path / "ui.sqlite")
    baseline = run(run_batch(suite(), TargetConfig(), RunConfig(max_calls=1000), Secrets(), store))
    store.set_batch_baseline(baseline["id"])
    candidate = run(
        run_batch(suite(), TargetConfig(fixture="degraded"), RunConfig(max_calls=1000), Secrets(), store)
    )
    store.save_record(record_snapshot(baseline, "Older stable"))
    window = MainWindow(tmp_path / "ui.sqlite")
    window.show()
    window.tabs.setCurrentIndex(1)
    qt_app.processEvents()
    assert window.save_record_button.text() == "Save to record"
    assert window.dull_heading.text() == "How Dull changed 指數"
    assert window.save_record_button.isEnabled()
    # Pick the saved stable record against the current (latest) degraded report.
    index = window.record_picker.findText("Older stable", Qt.MatchContains)
    assert index > 0
    window.record_picker.setCurrentIndex(index)
    qt_app.processEvents()
    from product_blinder.records import dull_change_index, format_index
    expected = format_index(
        dull_change_index(baseline["aggregate"]["score_mean"], candidate["aggregate"]["score_mean"])
    )
    assert expected not in (None, "N/A", "0")
    assert window.dull_value.text() == expected
    assert "100%" in window.dull_scores.text()
    assert candidate["id"]
    window.close()
    qt_app.processEvents()
