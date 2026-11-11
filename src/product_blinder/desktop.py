"""Real native Qt Widgets application. No webview, browser, local server, or HTML UI."""

import argparse
import asyncio
from datetime import datetime
from importlib.resources import files
import json
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QThread, Signal, Qt, QTimer, QCoreApplication, QEvent
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QTabWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QInputDialog,
    QFileDialog,
    QSplitter,
    QScrollArea,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QSizePolicy,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from .activity import ActivityStatus, score_percent, score_transition
from .analysis import analysis_markdown, context_line
from .records import dull_change_view, record_snapshot
from .icons import icon, apply_icon, icon_label
from .splash import create_opening_splash, reveal_after_splash
from .adapters import Budget
from .batches import estimate, run_batch
from .batch_reports import batch_csv
from .evaluators import DESCRIPTIONS
from .judge import propose_rubric
from .models import Case, Suite, TargetConfig, RunConfig, load_suite
from .security import Secrets
from .store import Store

from .design_widgets import (
    STYLE,
    COLORS,
    light_palette,
    card,
    label,
    Disclosure,
    MetricCard,
    ConversationView,
)


def text_edit(placeholder="", height=85):
    w = QPlainTextEdit()
    w.setPlaceholderText(placeholder)
    w.setMaximumHeight(height)
    return w


def number(minimum, maximum, value, decimals=None):
    w = QDoubleSpinBox() if decimals is not None else QSpinBox()
    w.setRange(minimum, maximum)
    if decimals is not None:
        w.setDecimals(decimals)
        w.setSingleStep(0.001 if decimals >= 3 else 0.1)
    w.setValue(value)
    return w


def fmt(value, digits=3, percent=False):
    if value is None:
        return "N/A"
    return f"{value:.{digits}%}" if percent else f"{value:.{digits}f}"


class SettingsDialog(QDialog):
    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Product Blinder — Session-only settings")
        self.resize(620, 680)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Keys remain in process memory/environment only. They are not saved in SQLite or exports.\nProvider-side spending limits are still required."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.fields = {}
        for key, field_label in [
            ("target_key", "Target API key"),
            ("judge_key", "Judge API key"),
            ("judge_base_url", "Judge base URL"),
            ("judge_model", "Judge model identifier"),
        ]:
            w = QLineEdit(str(values.get(key, "")))
            if key.endswith("_key"):
                w.setEchoMode(QLineEdit.Password)
            self.fields[key] = w
            form.addRow(field_label, w)
        self.fields["use_judge"] = QCheckBox("Use real OpenAI-compatible LLM judge")
        self.fields["use_judge"].setChecked(values.get("use_judge", False))
        form.addRow(self.fields["use_judge"])
        self.fields["use_agent_planner"] = QCheckBox(
            "Let the paid LLM plan named testing agents (one structured plan per batch)"
        )
        self.fields["use_agent_planner"].setChecked(values.get("use_agent_planner", False))
        form.addRow(self.fields["use_agent_planner"])
        self.fields["reduced_motion"] = QCheckBox("Reduce motion (static status; no decorative animations)")
        self.fields["reduced_motion"].setChecked(values.get("reduced_motion", False))
        form.addRow(self.fields["reduced_motion"])
        for key, field_label, mi, ma, default, dec in [
            ("concurrency", "Concurrent cases", 1, 16, 4, None),
            ("timeout_s", "Timeout per attempt (seconds)", 1, 300, 20, None),
            ("max_calls", "Target attempts: WHOLE batch", 1, 10000, 1000, None),
            ("max_judge_calls", "Judge attempts: WHOLE batch", 0, 2000, 1000, None),
            ("budget_usd", "Declared USD budget: WHOLE batch", 0, 10000, 5, 2),
            ("target_call_cap_usd", "Declared USD upper bound / target attempt", 0, 100, 0.01, 3),
            ("judge_call_cap_usd", "Declared USD upper bound / judge attempt", 0, 100, 0.01, 3),
            ("retry", "Retries per request", 0, 2, 1, None),
            ("regression_threshold", "Practical score-change margin", 0.001, 1, 0.05, 3),
            ("min_pairs", "Minimum independent case families", 2, 1000, 10, None),
            ("agent_count", "Named testing agents (local or planned)", 5, 8, 8, None),
            ("max_generated_cases", "Max agent-generated Test Cases", 0, 8, 4, None),
        ]:
            w = number(mi, ma, values.get(key, default), dec)
            self.fields[key] = w
            form.addRow(field_label, w)
        warning = QLabel(
            "Declared bounds are NOT verified prices. Every retry reserves budget too. Native program wrappers are trusted code, not sandboxes. Rubric proposal is a separate budgeted operation."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        out = {}
        for key, w in self.fields.items():
            out[key] = (
                w.isChecked()
                if isinstance(w, QCheckBox)
                else w.text()
                if isinstance(w, QLineEdit)
                else w.value()
            )
        return out


class EvaluationWorker(QThread):
    progress = Signal(dict)
    result = Signal(dict)
    error = Signal(str)

    def __init__(self, suite, target, config, secrets, db, repetitions, consent, allow_exec):
        super().__init__()
        self.args = (suite, target, config, secrets, db, repetitions, consent, allow_exec)
        self.cancel_event = threading.Event()

    def run(self):
        suite, target, config, secrets, db, reps, consent, allow_exec = self.args
        try:
            result = asyncio.run(
                run_batch(
                    suite,
                    target,
                    config,
                    secrets,
                    Store(db),
                    repetitions=reps,
                    consent=consent,
                    allow_exec=allow_exec,
                    cancel_event=self.cancel_event,
                    on_progress=self.progress.emit,
                )
            )
            self.result.emit(result)
        except Exception as exc:
            self.error.emit(
                type(exc).__name__
                + ": check validated setup, consent, call ceilings, keys and budget. No credential-bearing traceback is displayed."
            )


class RubricWorker(QThread):
    result = Signal(str)
    error = Signal(str)

    def __init__(self, task, config, secrets):
        super().__init__()
        self.task, self.config, self.secrets = task, config, secrets

    def run(self):
        import httpx

        async def execute():
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                return await propose_rubric(client, self.config, self.secrets, Budget(self.config), self.task)

        try:
            self.result.emit(asyncio.run(execute()))
        except Exception as exc:
            self.error.emit("Rubric request failed: " + type(exc).__name__)


class MainWindow(QMainWindow):
    def __init__(self, db=None):
        super().__init__()
        default = Path.home() / ".local/share/product-blinder/product-blinder.sqlite"
        self.db = str(db or os.getenv("PB_DB", default))
        self.store = Store(self.db)
        self.worker = None
        self.rubric_worker = None
        self.ai_rubric = ""
        self.rendered_batch = None
        self.settings = {
            "target_key": os.getenv("PB_TARGET_API_KEY", ""),
            "judge_key": os.getenv("PB_JUDGE_API_KEY", ""),
            "judge_base_url": "https://api.openai.com/v1",
            "judge_model": "",
            "use_judge": False,
            "use_agent_planner": False,
            "agent_count": 8,
            "max_generated_cases": 4,
            "reduced_motion": os.getenv("PB_REDUCED_MOTION", "0") == "1",
            "concurrency": 4,
            "timeout_s": 20,
            "max_calls": 1000,
            "max_judge_calls": 1000,
            "retry": 1,
            "budget_usd": 5.0,
            "target_call_cap_usd": 0.01,
            "judge_call_cap_usd": 0.01,
            "regression_threshold": 0.05,
            "min_pairs": 10,
        }
        self.build_shell()

    def build_shell(self):
        self.setWindowTitle("Product Blinder 0.7 — Native AI Evaluation Workbench")
        self.setWindowIcon(icon("app"))
        geometry = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1540, geometry.width() - 32), min(1080, geometry.height() - 32))
        self.setMinimumSize(1040, 680)
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(236)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 26, 18, 22)
        side.setSpacing(12)
        brand = QHBoxLayout()
        mark = icon_label("app", 44, "Product Blinder — AI product evaluation")
        self.brand_icon = mark
        brand.addWidget(mark)
        word = QVBoxLayout()
        title = label("Product Blinder")
        title.setFont(QFont("DejaVu Sans", 11, QFont.Bold))
        word.addWidget(title)
        word.addWidget(label("AI quality workbench", "muted"))
        brand.addLayout(word)
        side.addLayout(brand)
        side.addSpacing(28)
        side.addWidget(label("WORKSPACE", "eyebrow"))
        self.nav_buttons = []
        for index, text in enumerate(("Chatbot", "Dashboard Report")):
            button = QPushButton(text)
            apply_icon(button, "chatbot" if index == 0 else "dashboard", 24)
            button.setObjectName("nav")
            button.setMinimumHeight(46)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, i=index: self.tabs.setCurrentIndex(i))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addSpacing(18)
        self.sidebar_state = label("No active session", "muted")
        side.addWidget(self.sidebar_state)
        side.addStretch()
        privacy = QFrame()
        privacy.setStyleSheet("QFrame {background:#F8FAFD;border:0;border-radius:12px;}")
        pl = QVBoxLayout(privacy)
        pl.setContentsMargins(12, 14, 12, 14)
        pl.addWidget(label("Local Store Only", "cardTitle"))
        pl.addWidget(label("No cloud sync. No automatic release. Your keys stay in this session.", "muted"))
        side.addWidget(privacy)
        settings = QPushButton("Settings / API keys")
        apply_icon(settings, "settings", 22)
        settings.clicked.connect(self.open_settings)
        side.addWidget(settings)
        side.addWidget(label("Native desktop  ·  v0.7", "muted"))
        shell.addWidget(sidebar)
        body = QWidget()
        body.setObjectName("workspace")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(26, 23, 26, 12)
        body_layout.setSpacing(19)
        heading = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(5)
        self.page_title = label("Let's test something.", "pageTitle")
        self.page_subtitle = label("Create a repeatable evaluation, then keep the evidence.", "muted")
        texts.addWidget(self.page_title)
        texts.addWidget(self.page_subtitle)
        heading.addLayout(texts, 1)
        self.local_badge = label("LOCAL WORKSPACE", "pill")
        self.local_badge.setWordWrap(False)
        heading.addWidget(self.local_badge)
        body_layout.addLayout(heading)
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        self.tabs.setStyleSheet("QTabWidget::pane {border:0;background:transparent;}")
        self.chat_tab = QWidget()
        self.dashboard_tab = QWidget()
        self.tabs.addTab(self.chat_tab, "Chatbot")
        self.tabs.addTab(self.dashboard_tab, "Dashboard Report")
        body_layout.addWidget(self.tabs, 1)
        shell.addWidget(body, 1)
        self.build_chat()
        self.build_dashboard()
        self.tabs.currentChanged.connect(self.page_changed)
        self.refresh_history()
        self.update_estimate()
        self.page_changed(0)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().showMessage(
            "  Native Qt Widgets  ·  No browser / webview  ·  Synthetic demos never call a provider"
        )

    def page_changed(self, index):
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        titles = [
            ("Let's test something.", "Create a repeatable evaluation, then keep the evidence."),
            ("Evaluation overview", "Track quality, speed and consistency across five complete repetitions."),
        ]
        self.page_title.setText(titles[index][0])
        self.page_subtitle.setText(titles[index][1])

    def message(self, text):
        self.chat_log.appendPlainText(datetime.now().strftime("%H:%M:%S") + " HKT  " + text + "\n")

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.Accepted:
            self.settings = dialog.values()
            self.activity.set_reduced_motion(self.settings["reduced_motion"])
            self.update_estimate()
            self.message("Settings updated in memory. A running batch keeps its original frozen settings.")

    def config_secrets(self):
        values = {k: v for k, v in self.settings.items() if not k.endswith("_key") and k != "reduced_motion"}
        return RunConfig(**values), Secrets(self.settings["target_key"], self.settings["judge_key"])

    def build_chat(self):
        layout = QVBoxLayout(self.chat_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(12)
        layout.addWidget(split, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(16)
        scroll.setWidget(left)
        split.addWidget(scroll)
        target_box, t = card(
            "01  Choose your target", "Start safely with a local demo, or connect a product you own.", "app"
        )
        form = QFormLayout()
        form.setSpacing(13)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        t.addLayout(form)
        self.adapter = QComboBox()
        self.adapter.addItems(["Synthetic fixture", "HTTP JSON API", "Trusted local program"])
        self.adapter.currentIndexChanged.connect(self.update_estimate)
        form.addRow("Target type", self.adapter)
        self.scenario = QComboBox()
        self.scenario.addItems(
            [
                "Complete support / safety benchmark (32 cases)",
                "Evidence-based support / RAG (12 cases)",
                "Receipt JSON extraction (12 cases)",
                "Robustness / fairness / injection (24 cases)",
            ]
        )
        self.scenario.currentIndexChanged.connect(self.select_scenario)
        self.fixture = QComboBox()
        self.fixture.addItems(["stable", "degraded"])
        self.scenario_row = self.row(self.scenario, self.fixture)
        form.addRow("Demo scenario", self.scenario_row)
        self.url = QLineEdit("http://127.0.0.1:9000/evaluate")
        self.url.setPlaceholderText("https://your-product.example/evaluate")
        self.endpoint_label = label("POST endpoint")
        form.addRow(self.endpoint_label, self.url)
        self.adapter_disclosure = Disclosure("Advanced adapter settings")
        advanced = QFormLayout()
        advanced.setSpacing(12)
        self.adapter_disclosure.content_layout.addLayout(advanced)
        self.input_path = QLineEdit("input")
        self.output_path = QLineEdit("output")
        advanced.addRow("Input / output paths", self.row(self.input_path, self.output_path))
        self.body = text_edit("Static JSON request body — no credentials", 85)
        self.body.setPlainText("{}")
        advanced.addRow("Static request JSON", self.body)
        self.command = QLineEdit('["python3", "examples/echo_workflow.py"]')
        advanced.addRow("Trusted command argv", self.command)
        self.model = QLineEdit("unspecified")
        self.revision = QLineEdit("unspecified")
        self.prompt_version = QLineEdit("unspecified")
        advanced.addRow("Model identifier", self.model)
        advanced.addRow("App / prompt revision", self.row(self.revision, self.prompt_version))
        self.auth_header = QLineEdit("Authorization")
        self.auth_prefix = QLineEdit("Bearer ")
        advanced.addRow("Auth header / prefix", self.row(self.auth_header, self.auth_prefix))
        advanced.addRow(label("Keys belong in Settings, never a URL, suite or request body.", "muted"))
        t.addWidget(self.adapter_disclosure)
        left_layout.addWidget(target_box)
        task_box, f = card(
            "02  Define a good result",
            "Optional for demos. Write a task and expected result to create your own case.",
        )
        f.addWidget(label("Task prompt", "muted"))
        self.task = text_edit("What should your AI product do?", 100)
        f.addWidget(self.task)
        f.addWidget(label("Expected result  ·  optional", "muted"))
        self.expected = text_edit(
            "Describe or paste the ideal output. Blank means accuracy needs a reviewed rubric.", 95
        )
        f.addWidget(self.expected)
        rubric_section = Disclosure("Custom rubric & AI-assisted criteria")
        self.rubric_disclosure = rubric_section
        rubric_section.button.toggled.connect(self.update_estimate)
        self.rubric = text_edit("Human rubric, or review an AI-proposed rubric before use", 95)
        rubric_section.content_layout.addWidget(self.rubric)
        self.propose = QPushButton("Propose AI rubric (paid opt-in)")
        self.propose.clicked.connect(self.propose_rubric)
        rubric_section.content_layout.addWidget(self.propose)
        rubric_section.content_layout.addWidget(
            label(
                "A real judge call, only after explicit consent. Provisional criteria are not objective truth.",
                "muted",
            )
        )
        f.addWidget(rubric_section)
        custom = QPushButton("Use task as one-case suite")
        custom.clicked.connect(self.use_task)
        f.addWidget(custom)
        left_layout.addWidget(task_box)
        suite_box, sl = card(
            "03  Review the benchmark",
            "JSON / safe YAML is data only. A workflow file is never executed automatically.",
        )
        self.suite_summary = label("32 authored cases", "muted")
        sl.addWidget(self.suite_summary)
        load = apply_icon(QPushButton("Import suite"), "import")
        load.clicked.connect(self.import_suite)
        reset = apply_icon(QPushButton("Restore demo"), "consistency")
        reset.clicked.connect(self.load_demo)
        sl.addWidget(self.row(load, reset))
        self.suite_disclosure = Disclosure("Inspect / edit frozen suite")
        self.suite_editor = QPlainTextEdit()
        self.suite_editor.setMinimumHeight(230)
        self.suite_editor.textChanged.connect(self.update_estimate)
        self.suite_disclosure.content_layout.addWidget(self.suite_editor)
        sl.addWidget(self.suite_disclosure)
        left_layout.addWidget(suite_box)
        left_layout.addStretch()
        conversation, chat = card(
            "Your workbench assistant", "Local intake assistant · no model calls just for chatting"
        )
        self.chat_log = ConversationView()
        self.chat_log.setMinimumHeight(180)
        chat.addWidget(self.chat_log, 1)
        self.intake = QLineEdit()
        self.intake.setPlaceholderText("Describe your product, or paste an API URL…")
        self.intake.returnPressed.connect(self.capture_intake)
        send = QPushButton("Send")
        send.clicked.connect(self.capture_intake)
        chat.addWidget(self.row(self.intake, send))
        boundary = label(
            "No URL is opened until you confirm a run. Apps need an API or trusted wrapper, not universal UI automation.",
            "muted",
        )
        chat.addWidget(boundary)
        split.addWidget(conversation)
        split.setSizes([720, 520])
        run_card, run_layout = card()
        controls = QHBoxLayout()
        controls.setSpacing(12)
        controls.addWidget(label("Complete repetitions", "cardTitle"))
        self.repetitions = number(1, 20, 5)
        self.repetitions.setFixedWidth(75)
        self.repetitions.valueChanged.connect(self.update_estimate)
        controls.addWidget(self.repetitions)
        controls.addStretch()
        self.cancel_button = apply_icon(QPushButton("Cancel batch"), "alert")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_batch)
        controls.addWidget(self.cancel_button)
        self.start_button = apply_icon(QPushButton("Run 5 repetitions"), "run", 24)
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(190)
        self.start_button.clicked.connect(self.start_evaluation)
        controls.addWidget(self.start_button)
        run_layout.addLayout(controls)
        self.estimate_label = label("", "muted")
        run_layout.addWidget(self.estimate_label)
        self.consent = QCheckBox("I approve external/program calls and possible provider charges")
        self.allow_exec = QCheckBox("I explicitly trust program execution — not sandboxed")
        run_layout.addWidget(self.consent)
        run_layout.addWidget(self.allow_exec)
        self.progress = QProgressBar()
        self.progress.setRange(0, 5)
        self.progress.setTextVisible(False)
        run_layout.addWidget(self.progress)
        self.activity = ActivityStatus(self.progress)
        self.activity.set_reduced_motion(self.settings["reduced_motion"])
        # Global native footer remains visible when results switch to Dashboard Report.
        self.statusBar().addPermanentWidget(self.activity, 1)
        layout.addWidget(run_card)
        self.load_demo()
        self.message(
            "Assistant: Ready when you are. Choose a synthetic scenario or connect your target, review the expected outcome, and run the same suite five times. No paid calls happen in fixture mode with judging off."
        )

    @staticmethod
    def row(*widgets):
        w = QWidget()
        row_layout = QHBoxLayout(w)
        row_layout.setContentsMargins(0, 0, 0, 0)
        for child in widgets:
            row_layout.addWidget(child)
        return w

    def load_demo(self):
        self.suite_editor.setPlainText(files("product_blinder").joinpath("data/demo-suite.yaml").read_text())

    def select_scenario(self, index):
        if not hasattr(self, "suite_editor"):
            return
        name = ["demo-suite.yaml", "support-suite.yaml", "extraction-suite.yaml", "safety-suite.yaml"][index]
        self.suite_editor.setPlainText(files("product_blinder").joinpath("data/" + name).read_text())
        self.adapter.setCurrentIndex(0)
        self.message(
            "Selected synthetic scenario: "
            + self.scenario.currentText()
            + ". Choose stable/degraded; no API key or paid-call consent is needed for fixtures with judging disabled."
        )

    def capture_intake(self):
        text = self.intake.text().strip()
        if not text:
            return
        self.message("You: " + text)
        if text.startswith(("http://", "https://")):
            self.url.setText(text)
            self.adapter.setCurrentIndex(1)
        else:
            self.task.setPlainText(text)
        self.message(
            "Assistant: Captured as data only. Verify the target mapping and expected outcome. App/workflow files are not automatically executed."
        )
        self.intake.clear()

    def import_suite(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import suite data", "", "JSON/YAML (*.json *.yaml *.yml)"
        )
        if path:
            try:
                text = Path(path).read_text()
                load_suite(text)
                self.suite_editor.setPlainText(text)
                self.message("Suite data imported. No code executed.")
            except Exception as exc:
                self.message("Import rejected: " + type(exc).__name__)

    def use_task(self):
        if not self.task.toPlainText().strip():
            self.message("Enter a task prompt first.")
            return
        rubric = self.rubric.toPlainText().strip()
        suite = Suite(
            name="Interactive task",
            cases=[
                Case(
                    id="interactive-1",
                    prompt=self.task.toPlainText(),
                    expected=self.expected.toPlainText() or None,
                    rubric=rubric or None,
                    rubric_source="ai-proposed" if rubric == self.ai_rubric and rubric else "human",
                )
            ],
        )
        self.suite_editor.setPlainText(suite.model_dump_json(by_alias=True, indent=2))
        self.message(
            "One-case suite prepared. Five repetitions do not create five independent task families; a regression verdict needs a larger suite."
        )

    def setup(self):
        suite = load_suite(self.suite_editor.toPlainText())
        config, secrets = self.config_secrets()
        kind = ["fixture", "http", "program"][self.adapter.currentIndex()]
        target = TargetConfig(
            kind=kind,
            fixture=self.fixture.currentText(),
            url=self.url.text().strip() if kind == "http" else "",
            input_path=self.input_path.text(),
            output_path=self.output_path.text(),
            body=json.loads(self.body.toPlainText()),
            command=json.loads(self.command.text()) if kind == "program" else [],
            model=self.model.text(),
            revision=self.revision.text(),
            prompt_version=self.prompt_version.text(),
            auth_header=self.auth_header.text(),
            auth_prefix=self.auth_prefix.text(),
        )
        return suite, target, config, secrets

    def update_estimate(self):
        if not hasattr(self, "estimate_label"):
            return
        try:
            suite, target, config, _ = self.setup()
            quote = estimate(suite, target, config, self.repetitions.value())
            paid = target.kind != "fixture" or config.use_judge or config.use_agent_planner
            suffix = (
                f"Declared initial reservation ${quote['planned_initial_reservation_usd']:.3f} / ${config.budget_usd:.2f} budget; not verified pricing."
                if paid
                else "Synthetic local evaluation · no provider calls or API key required."
            )
            planner = (
                f"Paid tester planner on · {config.agent_count} agents, up to {config.max_generated_cases} new Test Cases."
                if config.use_agent_planner
                else f"{config.agent_count} local heuristic testers · no paid planner."
            )
            self.estimate_label.setText(
                f"{len(suite.cases)} Test Cases × {quote['repetitions']} repetitions = {quote['minimum_target_attempts']} target attempts + {quote['minimum_judge_attempts']} judge/planner attempts. "
                + suffix
                + " "
                + planner
            )
            self.suite_summary.setText(
                f"{suite.name} · {len(suite.cases)} Test Cases · SHA-256 {suite.digest[:16]}…"
            )
            self.start_button.setText(f"Run {self.repetitions.value()} repetitions")
            self.scenario_row.setVisible(target.kind == "fixture")
            self.url.setVisible(target.kind == "http")
            self.endpoint_label.setVisible(target.kind == "http")
            self.consent.setVisible(paid or self.rubric_disclosure.button.isChecked())
            self.allow_exec.setVisible(target.kind == "program")
        except Exception:
            self.estimate_label.setText(
                "Fix invalid suite, mapping or settings before starting. No requests sent."
            )

    def start_evaluation(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            suite, target, config, secrets = self.setup()
        except Exception as exc:
            self.message("Setup rejected: " + type(exc).__name__)
            return
        self.activity.start(self.repetitions.value())
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.worker = EvaluationWorker(
            suite,
            target,
            config,
            secrets,
            self.db,
            self.repetitions.value(),
            self.consent.isChecked(),
            self.allow_exec.isChecked(),
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.result.connect(self.on_result)
        self.worker.error.connect(self.on_worker_error)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        self.message(
            f"Started worker thread with {len(suite.cases)} Test Cases. Suite, testers, references/rubric, target and settings freeze for the whole batch."
        )

    def on_progress(self, batch):
        done = sum(e["status"] == "complete" for e in batch["repetition_records"])
        self.activity.update_progress(done, batch["repetitions"])
        cases = len(batch.get("test_cases") or batch.get("suite", {}).get("cases") or [])
        testers = (batch.get("agents") or {}).get("label") or "local testers"
        self.statusBar().showMessage(
            f"Batch {batch['id']} · {done}/{batch['repetitions']} complete · {cases} Test Cases · {testers} · {batch['status']}"
        )
        self.refresh_history(batch["id"])

    def on_worker_error(self, text):
        self.activity.finish("error", "Evaluation error · incomplete observations are not scores")
        self.message(text)

    def on_result(self, batch):
        done = batch["aggregate"]["complete_repetitions"]
        self.activity.update_progress(done, batch["repetitions"])
        self.activity.finish(
            batch["status"],
            f"{batch['status'].title()} · {done}/{batch['repetitions']} repetitions complete ({done / batch['repetitions']:.0%})",
        )
        self.message(
            f"Batch {batch['id']}: {batch['status']}, {batch['aggregate']['complete_repetitions']}/{batch['repetitions']} complete. Verdict: {batch['comparison']['verdict']}. See Dashboard Report."
        )
        self.refresh_history(batch["id"])
        self.tabs.setCurrentIndex(1)
        self.dashboard_scroll.verticalScrollBar().setValue(0)

    def worker_finished(self):
        if self.activity.running:
            self.activity.finish("incomplete", "Stopped · review incomplete results")
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def cancel_batch(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel_event.set()
            self.activity.label.setText("Cancelling · waiting for active work to stop")
            self.cancel_button.setEnabled(False)
            self.message(
                "Cancellation requested. Completed observations will be kept; unfinished repetitions stay missing. Requests already sent may still be billed."
            )

    def propose_rubric(self):
        if not self.consent.isChecked():
            self.message("Explicit paid-call consent required for real AI rubric proposal.")
            return
        config, secrets = self.config_secrets()
        if not secrets.judge or not config.judge_model:
            self.message("Set a judge API key and model in Settings first.")
            return
        if not self.task.toPlainText().strip():
            self.message("Enter a task before proposing criteria.")
            return
        if self.rubric_worker and self.rubric_worker.isRunning():
            return
        self.propose.setEnabled(False)
        self.rubric_worker = RubricWorker(self.task.toPlainText(), config, secrets)

        def accept(text):
            self.ai_rubric = text
            self.rubric.setPlainText(text)
            self.message(
                "AI-PROPOSED RUBRIC — provisional, not objective truth. Review it, then click Use task as one-case suite to freeze. It is not regenerated between repetitions."
            )

        self.rubric_worker.result.connect(accept)
        self.rubric_worker.error.connect(self.message)
        self.rubric_worker.finished.connect(lambda: self.propose.setEnabled(True))
        self.rubric_worker.start()

    def build_dashboard(self):
        outer = QVBoxLayout(self.dashboard_tab)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        self.dashboard_scroll = scroll
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 9, 0)
        layout.setSpacing(18)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        toolbar = QHBoxLayout()
        self.history = QComboBox()
        self.history.setMinimumWidth(320)
        self.history.currentIndexChanged.connect(self.select_batch)
        toolbar.addWidget(self.history, 1)
        refresh = apply_icon(QPushButton("Refresh"), "consistency")
        refresh.clicked.connect(lambda: self.refresh_history())
        toolbar.addWidget(refresh)
        self.baseline_button = apply_icon(QPushButton("Set as baseline"), "quality")
        self.baseline_button.clicked.connect(self.set_baseline)
        toolbar.addWidget(self.baseline_button)
        for text, slot in [("Export CSV", self.export_csv), ("Export JSON", self.export_json)]:
            button = apply_icon(QPushButton(text), "export")
            button.clicked.connect(slot)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        records = QHBoxLayout()
        records.setSpacing(10)
        self.record_picker = QComboBox()
        self.record_picker.setMinimumWidth(280)
        self.record_picker.setToolTip("Compare the current analysis report with a saved local record")
        self.record_picker.currentIndexChanged.connect(self.render_dull_index)
        records.addWidget(self.record_picker, 1)
        self.save_record_button = apply_icon(QPushButton("Save to record"), "export")
        self.save_record_button.setEnabled(False)
        self.save_record_button.setToolTip(
            "Save the current analysis report to this computer's local store. Keys are never stored."
        )
        self.save_record_button.clicked.connect(self.save_to_record)
        records.addWidget(self.save_record_button)
        layout.addLayout(records)
        self.provenance = label("No sessions yet. Start a safe synthetic evaluation in Chatbot.", "muted")
        layout.addWidget(self.provenance)
        kpis = QHBoxLayout()
        kpis.setSpacing(16)
        self.metrics = []
        for title in (
            "Mean quality score",
            "Repetitions completed",
            "Mean response time",
            "Output consistency",
        ):
            metric = MetricCard(title, ["quality", "tests", "speed", "consistency"][len(self.metrics)])
            metric.set_tint(["peach", "lavender", "mint", "sky"][len(self.metrics)])
            kpis.addWidget(metric, 1)
            self.metrics.append(metric)
        layout.addLayout(kpis)
        self.figures = []
        self.canvases = []

        def plot_card(title, subtitle, height):
            frame, box = card(title, subtitle)
            fig = Figure(figsize=(5, 2.4), facecolor="#FFFFFF")
            fig.subplots()
            canvas = FigureCanvasQTAgg(fig)
            canvas.setMinimumHeight(height)
            canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            box.addWidget(canvas, 1)
            self.figures.append(fig)
            self.canvases.append(canvas)
            return frame

        trend = plot_card(
            "Quality over five repetitions",
            "A repeatable suite. Individual observations, not invented averages.",
            235,
        )
        insight = QFrame()
        insight.setObjectName("insightCard")
        ins = QVBoxLayout(insight)
        ins.setContentsMargins(23, 22, 23, 22)
        ins.setSpacing(13)
        self.insight_badge = label("BASELINE COMPARISON", "eyebrow")
        self.insight_value = label("—", "insightValue")
        self.insight_title = label("Start with a baseline", "cardTitle")
        self.insight_body = label(
            "Choose a completed session as your reference, then evaluate a matched candidate.", "muted"
        )
        self.insight_facts = label("No paired comparison yet.", "muted")
        for w in (self.insight_badge, self.insight_value, self.insight_title, self.insight_body):
            ins.addWidget(w)
        ins.addStretch()
        ins.addWidget(self.insight_facts)
        self.review_button = apply_icon(QPushButton("Review Test Case evidence"), "evidence")
        self.review_button.clicked.connect(self.review_evidence)
        ins.addWidget(self.review_button)
        first = QHBoxLayout()
        first.setSpacing(16)
        first.addWidget(trend, 2)
        first.addWidget(insight, 1)
        layout.addLayout(first)
        dull, dull_layout = card(
            None, "How much duller the current mean quality is versus a saved record. Not a causal claim."
        )
        dull.setObjectName("dullCard")
        dull_head = QHBoxLayout()
        dull_head.addWidget(icon_label("quality", 26, "How Dull changed 指數"))
        self.dull_heading = label("How Dull changed 指數", "cardTitle")
        dull_head.addWidget(self.dull_heading, 1)
        dull_layout.insertLayout(0, dull_head)
        self.dull_value = label("N/A", "dullValue")
        dull_layout.addWidget(self.dull_value)
        self.dull_scores = label(
            "Select a saved record to compare with the current analysis report.", "muted"
        )
        dull_layout.addWidget(self.dull_scores)
        self.dull_reason = label(
            "Index = 100 × max(0, S_old − S_now) / max(S_old, 1e-9), with scores in [0, 1]. Missing or unmatched suites stay N/A.",
            "muted",
        )
        dull_layout.addWidget(self.dull_reason)
        self.dull_notes = label("", "muted")
        dull_layout.addWidget(self.dull_notes)
        layout.addWidget(dull)
        report_box, report_layout = card(
            "Analysis report",
            "Compact measured facts. Missing values stay N/A. Testers are labelled honestly.",
            "quality",
        )
        self.analysis_mode = label("Local heuristic testers — no paid model ran", "muted")
        report_layout.addWidget(self.analysis_mode)
        self.analysis_facts = QTableWidget()
        self.analysis_facts.setMinimumHeight(150)
        self.analysis_facts.setMaximumHeight(190)
        report_layout.addWidget(self.analysis_facts)
        splits = QHBoxLayout()
        splits.setSpacing(12)
        fail_box, fail_l = card("Top failing Test Cases")
        self.failing_table = QTableWidget()
        self.failing_table.setMinimumHeight(150)
        fail_l.addWidget(self.failing_table)
        gap_box, gap_l = card("Evaluator gaps")
        self.gap_table = QTableWidget()
        self.gap_table.setMinimumHeight(150)
        gap_l.addWidget(self.gap_table)
        splits.addWidget(fail_box, 1)
        splits.addWidget(gap_box, 1)
        report_layout.addLayout(splits)
        self.recurrence_label = label("Recurrence: N/A until a batch finishes.", "muted")
        report_layout.addWidget(self.recurrence_label)
        self.suggestions = label("Suggested next checks will appear after a run.", "muted")
        report_layout.addWidget(self.suggestions)
        export_row = QHBoxLayout()
        self.export_analysis_json = apply_icon(QPushButton("Export analysis JSON"), "export")
        self.export_analysis_json.clicked.connect(self.export_analysis_json_file)
        self.export_analysis_md = apply_icon(QPushButton("Export analysis Markdown"), "export")
        self.export_analysis_md.clicked.connect(self.export_analysis_markdown)
        export_row.addWidget(self.export_analysis_json)
        export_row.addWidget(self.export_analysis_md)
        export_row.addStretch()
        report_layout.addLayout(export_row)
        layout.addWidget(report_box)
        problem_box, problem_layout = card(
            None,
            "Short list of evidenced issues only. Empty when every completed Test Case passed.",
            "alert",
        )
        heading = QHBoxLayout()
        heading.addWidget(icon_label("alert", 26, "Find out problem"))
        self.problem_heading = label("Find out problem", "cardTitle")
        heading.addWidget(self.problem_heading, 1)
        problem_layout.insertLayout(0, heading)
        self.problem_empty = label("No problems found in completed observations.", "muted")
        problem_layout.addWidget(self.problem_empty)
        self.problem_table = QTableWidget()
        self.problem_table.setMinimumHeight(180)
        self.problem_table.setMaximumHeight(260)
        problem_layout.addWidget(self.problem_table)
        layout.addWidget(problem_box)
        second = QHBoxLayout()
        second.setSpacing(16)
        for frame in [
            plot_card("Evaluator breakdown", "Applicable checks only · missing stays N/A", 235),
            plot_card("Response speed", "Measured target latency · milliseconds", 235),
            plot_card("Evaluation time", "Complete suite execution + judging · seconds", 235),
        ]:
            second.addWidget(frame, 1)
        layout.addLayout(second)
        self.figure = self.figures[0]
        self.canvas = self.canvases[0]
        repeat_box, rl = card(
            "Every repetition, accounted for",
            "Actual scores and timings. Cancelled or unfinished observations are never filled with zeros.",
        )
        self.all_metrics = QCheckBox("Show all metric / provenance columns")
        self.all_metrics.toggled.connect(self.toggle_metric_columns)
        rl.addWidget(self.all_metrics)
        self.repeat_table = QTableWidget()
        self.repeat_table.setMinimumHeight(238)
        self.repeat_table.setMaximumHeight(275)
        rl.addWidget(self.repeat_table)
        layout.addWidget(repeat_box)
        self.verdict = label("Complete matched batches are required for a regression gate.", "muted")
        layout.addWidget(self.verdict)
        evidence, ev = card(
            "Test Case evidence",
            "Select a Test Case and repetition to inspect context, expected outcome, actual response and eight evaluator reasons.",
            "tests",
        )
        self.evidence_card = evidence
        filters = QHBoxLayout()
        self.case_query = QLineEdit()
        self.case_query.setPlaceholderText("Find a Test Case…")
        self.case_query.textChanged.connect(self.refresh_cases)
        filters.addWidget(self.case_query, 1)
        self.failed_only = QCheckBox("Only failed checks")
        self.failed_only.toggled.connect(self.refresh_cases)
        filters.addWidget(self.failed_only)
        ev.addLayout(filters)
        detail = QSplitter(Qt.Horizontal)
        detail.setHandleWidth(12)
        self.case_table = QTableWidget()
        self.case_table.setMinimumHeight(480)
        self.case_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.case_table.setSelectionMode(QTableWidget.SingleSelection)
        self.case_table.itemSelectionChanged.connect(self.select_case)
        detail.addWidget(self.case_table)
        inspector = QWidget()
        il = QVBoxLayout(inspector)
        il.setContentsMargins(8, 0, 0, 0)
        il.setSpacing(10)
        self.inspector_title = label("Choose a Test Case", "cardTitle")
        il.addWidget(self.inspector_title)
        self.case_context = label("Test Case context will appear here.", "muted")
        il.addWidget(self.case_context)
        self.inspector_rep = QComboBox()
        self.inspector_rep.currentIndexChanged.connect(self.select_case)
        il.addWidget(self.inspector_rep)
        prompt_section = Disclosure("Prompt, evidence & rubric")
        self.prompt_view = QPlainTextEdit()
        self.prompt_view.setReadOnly(True)
        self.prompt_view.setMaximumHeight(175)
        prompt_section.content_layout.addWidget(self.prompt_view)
        il.addWidget(prompt_section)
        il.addWidget(label("EXPECTED OUTCOME", "eyebrow"))
        self.expected_view = text_edit("No reference supplied", 75)
        self.expected_view.setReadOnly(True)
        il.addWidget(self.expected_view)
        il.addWidget(label("ACTUAL RESPONSE", "eyebrow"))
        self.output_view = text_edit("Select an observation", 110)
        self.output_view.setReadOnly(True)
        il.addWidget(self.output_view)
        self.metric_details = QTableWidget()
        self.metric_details.setMinimumHeight(235)
        il.addWidget(self.metric_details)
        raw = Disclosure("Raw trace & comparison JSON")
        self.trace = QPlainTextEdit()
        self.trace.setReadOnly(True)
        self.trace.setMinimumHeight(200)
        raw.content_layout.addWidget(self.trace)
        il.addWidget(raw)
        detail.addWidget(inspector)
        detail.setSizes([620, 500])
        ev.addWidget(detail)
        layout.addWidget(evidence)
        self.metadata_section = Disclosure("Frozen configuration, provenance and budget details")
        self.metadata_view = QPlainTextEdit()
        self.metadata_view.setReadOnly(True)
        self.metadata_view.setMaximumHeight(260)
        self.metadata_section.content_layout.addWidget(self.metadata_view)
        layout.addWidget(self.metadata_section)
        layout.addWidget(
            label(
                "Synthetic examples are not live-model benchmarks. Repeat consistency is not correctness; measured changes do not identify a provider's infrastructure decisions.",
                "muted",
            )
        )
        self.draw_charts(None)

    def review_evidence(self):
        self.tabs.setCurrentIndex(1)
        self.dashboard_scroll.verticalScrollBar().setValue(self.evidence_card.y())
        self.case_table.setFocus()

    def toggle_metric_columns(self, *_):
        visible = {0, 1, 3, 4, 5, 6, 7, 8, 9}
        for column in range(self.repeat_table.columnCount()):
            self.repeat_table.setColumnHidden(
                column, not self.all_metrics.isChecked() and column not in visible
            )

    def refresh_history(self, selected=None):
        batches = self.store.batches()
        current = selected or self.history.currentData()
        self.history.blockSignals(True)
        self.history.clear()
        for b in reversed(batches):
            stamp = b["created"][5:16].replace("T", "  ")
            source = (
                ("Demo · " + b["target"]["fixture"]) if b["source"]["synthetic"] else b["source"]["model"]
            )
            self.history.addItem(f"{stamp}  ·  {source}  ·  {b['status']}", b["id"])
        idx = self.history.findData(current) if current else 0
        self.history.setCurrentIndex(max(0, idx))
        self.history.blockSignals(False)
        self.sidebar_state.setText(f"{len(batches)} saved sessions\nLocal SQLite history")
        self.refresh_records()
        self.select_batch()

    def select_batch(self, *_):
        bid = self.history.currentData()
        if bid:
            self.render_batch(self.store.get_batch(bid))

    @staticmethod
    def fill(table, headers, rows):
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        table.clearSelection()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                table.setItem(i, j, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(39)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.blockSignals(False)
        table.setUpdatesEnabled(True)

    def render_batch(self, b):
        self.rendered_batch = b
        a = b["aggregate"]
        comp = b["comparison"]
        src = b["source"]
        p = a["performance"]
        n = a["complete_repetitions"]
        self.provenance.setText(
            f"{'SYNTHETIC DEMO — not a live LLM benchmark' if src['synthetic'] else 'TARGET OBSERVATIONS'}  ·  {len(b['suite']['cases'])} cases / repetition  ·  {b['created'][:19].replace('T', ' ')} HKT  ·  Session {b['id']}"
        )
        delta = comp.get("delta")
        baseline_record = self.store.get_batch(comp["baseline_id"]) if comp.get("baseline_id") else None
        baseline_score = baseline_record["aggregate"]["score_mean"] if baseline_record else None
        score_pill = (
            "Baseline " + score_percent(baseline_score) if baseline_score is not None else "No baseline"
        )
        self.metrics[0].set_values(
            fmt(a["score_mean"], 1, True),
            f"Range {fmt(a['score_min'], 1, True)} – {fmt(a['score_max'], 1, True)}",
            score_pill,
            "negative" if delta is not None and delta < 0 else "blue",
        )
        self.metrics[1].set_values(
            f"{n} / {b['repetitions']}",
            f"Sample SD {fmt(a['score_sd'], 4)} · {a['score_sample_count']} observations",
            b["status"].title(),
            "positive" if b["status"] == "complete" else "orange",
        )
        mean = p.get("latency_ms_mean")
        self.metrics[2].set_values(
            (fmt(mean, 2) + " ms") if mean is not None else "N/A",
            f"P95 {fmt(p.get('latency_ms_p95'), 2)} ms · {fmt(p.get('throughput_cases_s'), 1)} cases/s",
            "Measured",
            "blue",
        )
        self.metrics[3].set_values(
            fmt(a["output_consistency_mean"], 1, True),
            "Same-case agreement ≠ task correctness",
            "Across repeats",
            "positive",
        )
        verdict = comp["verdict"]
        self.insight_badge.setText(
            "BASELINE → CURRENT SCORE" if baseline_record is not None else "BASELINE COMPARISON"
        )
        self.insight_value.setText(
            score_transition(baseline_score, a["score_mean"])
            if baseline_record is not None
            else ("In progress" if b["status"] == "running" else "No baseline")
        )
        titles = {
            "regression": "A measurable quality drop",
            "stable": "Within the practical margin",
            "improved": "A measurable improvement",
            "insufficient-data": "More evidence is needed",
        }
        self.insight_title.setText(titles.get(verdict, "Review the evidence"))
        ci = comp.get("ci95")
        self.insight_body.setText(
            f"Baseline: {score_percent(baseline_score)} · Current: {score_percent(a['score_mean'])}. Uncertainty and paired score-change interval are in details below. This measures changes, not their cause."
            if ci
            else "Select a completed reference batch, then evaluate the same frozen suite again. Five repeats alone do not create independent tasks."
        )
        self.insight_facts.setText(
            f"{comp.get('paired_cases', 0)} paired cases  ·  {comp.get('independent_units', 0)} families\nCost / TTFT: N/A  ·  Target tokens: {a.get('tokens') if a.get('tokens') is not None else 'N/A'}"
        )
        self.baseline_button.setEnabled(b["status"] == "complete")
        runs = {r["repetition"]: r for r in b["runs"]}
        rows = []
        for entry in b["repetition_records"]:
            r = runs.get(entry["index"])
            rp = r["performance"] if r else {}
            rs = r["summary"] if r else {}
            rows.append(
                [
                    entry["index"],
                    entry["status"],
                    entry["run_id"] or "N/A",
                    fmt(rs.get("score"), 1, True),
                    fmt(rp.get("duration_s"), 4),
                    fmt(rp.get("latency_ms_mean"), 2),
                    fmt(rp.get("latency_ms_p50"), 2),
                    fmt(rp.get("latency_ms_p95"), 2),
                    fmt(rp.get("throughput_cases_s"), 1),
                    fmt(rp.get("target_success_rate"), 1, True),
                    fmt(rp.get("target_error_rate"), 1, True),
                    fmt(rp.get("timeout_rate"), 1, True),
                    entry["recorded_seed"],
                    entry["started"] or "N/A",
                    *[fmt(rs.get("per_evaluator", {}).get(role), 2, True) for role in DESCRIPTIONS],
                ]
            )
        self.fill(
            self.repeat_table,
            [
                "Repeat",
                "Status",
                "Run ID",
                "Score",
                "Wall s",
                "Mean ms",
                "P50 ms",
                "P95 ms",
                "Cases/s",
                "Success",
                "Error",
                "Timeout",
                "Recorded seed*",
                "Started (HKT)",
                *[r.title() for r in DESCRIPTIONS],
            ],
            rows,
        )
        self.toggle_metric_columns()
        baseline = None
        if comp.get("baseline_id"):
            try:
                baseline = self.store.get_batch(comp["baseline_id"])
            except ValueError:
                pass
        if baseline and (
            baseline["batch_scoring_hash"] != b["batch_scoring_hash"]
            or baseline["suite_hash"] != b["suite_hash"]
            or baseline["repetitions"] != b["repetitions"]
        ):
            baseline = None
        self.draw_charts(b, baseline)
        self.verdict.setText(
            verdict.upper().replace("-", " ")
            + "  ·  "
            + comp.get("reason", "")
            + (
                f" · 95% paired score-change interval: {ci[0] * 100:+.1f} to {ci[1] * 100:+.1f} percentage points."
                if ci
                else ""
            )
            + "  ·  Recorded seeds are not automatically sent to providers."
        )
        self.metadata_view.setPlainText(
            json.dumps(
                {
                    k: b[k]
                    for k in (
                        "id",
                        "status",
                        "created",
                        "finished",
                        "suite_hash",
                        "batch_scoring_hash",
                        "source",
                        "target",
                        "config",
                        "calls",
                        "reserved_budget_usd",
                        "estimate",
                    )
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        prior = self.inspector_rep.currentData()
        self.inspector_rep.blockSignals(True)
        self.inspector_rep.clear()
        for i in range(1, b["repetitions"] + 1):
            self.inspector_rep.addItem(f"Inspect repetition {i}", i)
        self.inspector_rep.setCurrentIndex(max(0, self.inspector_rep.findData(prior)))
        self.inspector_rep.blockSignals(False)
        self.render_analysis(b)
        self.render_problems(b)
        self.save_record_button.setEnabled(b.get("status") == "complete")
        self.render_dull_index()
        self.refresh_cases()

    def refresh_cases(self, *_):
        b = self.rendered_batch
        if not b:
            return
        query = self.case_query.text().strip().lower()
        definitions = {c["id"]: c for c in b["suite"]["cases"]}
        paired = {c["case_id"]: c for c in b["comparison"].get("per_case", [])}
        runs = {r["repetition"]: r for r in b["runs"]}
        rows = []
        for t in b["aggregate"]["traces"]:
            if (
                query
                and query
                not in (t["case_id"] + " " + definitions.get(t["case_id"], {}).get("prompt", "")).lower()
            ):
                continue
            if self.failed_only.isChecked() and (t["score"] is None or t["score"] >= 1):
                continue
            scores = []
            for i in range(1, b["repetitions"] + 1):
                item = next(
                    (x for x in runs.get(i, {}).get("traces", []) if x["case_id"] == t["case_id"]), None
                )
                scores.append(fmt(item["score"] if item else None, 1, True))
            rows.append(
                [
                    t["case_id"],
                    fmt(t["score"], 1, True),
                    (
                        score_transition(paired[t["case_id"]]["baseline"], paired[t["case_id"]]["candidate"])
                        if t["case_id"] in paired
                        else "N/A"
                    ),
                    *scores,
                    fmt(t["score_sd"], 4),
                    fmt(t["output_consistency"], 1, True),
                    fmt(t["latency_ms"], 2),
                ]
            )
        self.fill(
            self.case_table,
            ["Test Case", "Mean score", "Baseline → Current"]
            + [f"R{i}" for i in range(1, b["repetitions"] + 1)]
            + ["Score SD", "Consistency", "Mean ms"],
            rows,
        )
        if rows:
            self.case_table.selectRow(0)
            self.select_case()
        else:
            self.clear_case_details()
            self.inspector_title.setText("No matching observed Test Cases")
            self.expected_view.clear()
            self.output_view.clear()
            self.fill(self.metric_details, ["Role", "Score", "Reason"], [])
            self.trace.setPlainText(
                "No complete repetition or no filter matches. Missing observations remain missing in JSON export."
            )

    def draw_charts(self, b, baseline=None):
        import numpy as np
        from matplotlib.ticker import PercentFormatter

        axes = []
        for fig in self.figures:
            fig.clear()
            fig.set_facecolor("#FFFFFF")
            fig.subplots_adjust(left=0.17, right=0.97, bottom=0.19, top=0.96)
            ax = fig.subplots()
            ax.set_facecolor("#FFFFFF")
            ax.tick_params(colors="#8B929C", labelsize=8, length=0, pad=7)
            ax.spines[:].set_visible(False)
            ax.grid(axis="y", color="#F0F2F5", linewidth=0.8)
            ax.set_axisbelow(True)
            axes.append(ax)
        if not b:
            for ax in axes:
                ax.text(
                    0.5,
                    0.5,
                    "Your observations will appear here",
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    color="#9AA2AD",
                    fontsize=9,
                )
                ax.set_xticks([])
                ax.set_yticks([])
        else:
            for data, color, name in [
                (baseline, COLORS["blue"], "Baseline"),
                (b, COLORS["orange"], "Selected batch"),
            ]:
                if not data:
                    continue
                runs = {r["repetition"]: r for r in data["runs"]}
                x = list(range(1, data["repetitions"] + 1))
                y = [
                    runs[i]["summary"]["score"] if i in runs and runs[i]["status"] == "complete" else np.nan
                    for i in x
                ]
                axes[0].plot(x, y, "o-", color=color, label=name, linewidth=2.2, markersize=5)
                axes[0].fill_between(x, y, alpha=0.055, color=color)
            axes[0].set(ylim=(-0.04, 1.08), xticks=list(range(1, b["repetitions"] + 1)), yticks=[0, 0.5, 1])
            axes[0].yaxis.set_major_formatter(PercentFormatter(1))
            axes[0].set_xticklabels([f"R{i}" for i in range(1, b["repetitions"] + 1)])
            axes[0].legend(frameon=False, fontsize=8, loc="lower left", labelcolor="#687180")
            self.figures[0].subplots_adjust(left=0.08, right=0.99, bottom=0.17, top=0.96)
            self.figures[1].subplots_adjust(left=0.31, right=0.98, bottom=0.15, top=0.97)
            roles = list(DESCRIPTIONS)
            values = b["aggregate"]["summary"]["per_evaluator"]
            counts = b["aggregate"]["summary"]["sample_sizes"]
            axes[1].grid(False)
            axes[1].barh(
                roles,
                [np.nan if values[r] is None else values[r] for r in roles],
                color="#AFC7FF",
                height=0.52,
            )
            axes[1].set_xlim(0, 1.23)
            axes[1].invert_yaxis()
            for i, r in enumerate(roles):
                axes[1].text(
                    1.02,
                    i,
                    ("N/A" if values[r] is None else f"{values[r]:.0%}") + f" · {counts[r]}",
                    va="center",
                    fontsize=7.5,
                    color="#7B8594",
                )
            axes[1].set_xticks([0, 0.5, 1])
            axes[1].xaxis.set_major_formatter(PercentFormatter(1))
            axes[1].tick_params(axis="y", labelsize=8)
            runs = b["runs"]
            x = [r["repetition"] for r in runs]
            for key, name, color in [
                ("latency_ms_mean", "Mean", COLORS["orange"]),
                ("latency_ms_p95", "P95", COLORS["blue"]),
            ]:
                y = [r["performance"][key] if r["performance"][key] is not None else np.nan for r in runs]
                axes[2].plot(x, y, "o-", label=name, color=color, linewidth=1.7, markersize=3.8)
            axes[2].set_xticks(list(range(1, b["repetitions"] + 1)))
            axes[2].legend(frameon=False, fontsize=7.5, labelcolor="#687180")
            axes[2].set_ylabel("ms", fontsize=8, color="#8B929C")
            axes[3].bar(x, [r["performance"]["duration_s"] for r in runs], color="#BFD0F7", width=0.52)
            axes[3].set_xticks(list(range(1, b["repetitions"] + 1)))
            axes[3].set_ylabel("seconds", fontsize=8, color="#8B929C")
        for canvas in self.canvases:
            canvas.draw_idle()

    def clear_case_details(self):
        self.inspector_title.setText("Select a Test Case")
        if hasattr(self, "case_context"):
            self.case_context.setText("Test Case context will appear here.")
        for field in (self.expected_view, self.output_view, self.prompt_view, self.trace):
            field.clear()
        self.fill(self.metric_details, ["Role", "Score", "Reason"], [])

    def select_case(self, *_):
        self.clear_case_details()
        rows = self.case_table.selectionModel().selectedIndexes() if self.case_table.selectionModel() else []
        if not rows or not self.rendered_batch:
            return
        item = self.case_table.item(rows[0].row(), 0)
        if not item:
            return
        cid = item.text()
        b = self.rendered_batch
        index = self.inspector_rep.currentData() or 1
        observations = [
            {
                "repetition": r["repetition"],
                "run_id": r["id"],
                "trace": next((t for t in r["traces"] if t["case_id"] == cid), None),
            }
            for r in b["runs"]
        ]
        selected = next((x["trace"] for x in observations if x["repetition"] == index), None)
        case = next((c for c in b["suite"]["cases"] if c["id"] == cid), {})
        self.inspector_title.setText("Test Case " + cid)
        record = next((item for item in b.get("test_cases") or [] if item["id"] == cid), None)
        if record:
            self.case_context.setText(context_line(record))
        else:
            self.case_context.setText("Test Case " + cid + " · context unavailable")
        self.prompt_view.setPlainText(
            json.dumps(
                {
                    k: case.get(k)
                    for k in ("prompt", "evidence", "rubric", "rubric_source", "category", "pair")
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        self.expected_view.setPlainText(
            case.get("expected")
            if case.get("expected") is not None
            else "N/A — no reference outcome. A rubric, if supplied, is in the frozen trace."
        )
        self.output_view.setPlainText(
            selected["output"] if selected else "N/A — this observation was not completed."
        )
        metricrows = []
        if selected:
            for role, m in selected["metrics"].items():
                metricrows.append([role.title(), fmt(m["score"], 1, True), m["reason"]])
        self.fill(self.metric_details, ["Role", "Score", "Reason"], metricrows)
        self.metric_details.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        delta = next((t for t in b["comparison"].get("per_case", []) if t["case_id"] == cid), None)
        self.trace.setPlainText(
            json.dumps(
                {"case_id": cid, "baseline_comparison": delta, "observations": observations},
                indent=2,
                ensure_ascii=False,
            )
        )

    def render_analysis(self, batch):
        report = batch.get("analysis") or {}
        self.analysis_mode.setText(report.get("mode_label") or "Local heuristic testers — no paid model ran")
        quality = report.get("quality") or {}
        completions = report.get("completions") or {}
        consistency = report.get("consistency") or {}
        latency = report.get("latency") or {}
        missing = report.get("missing") or {}

        def pct(value):
            return "N/A" if value is None else f"{value:.0%}"

        def num(value, suffix=""):
            return "N/A" if value is None else f"{value:.2f}{suffix}"

        rows = [
            ["Quality", quality.get("display") or "N/A"],
            ["Completions", completions.get("display") or "N/A"],
            ["Consistency", consistency.get("display") or "N/A"],
            ["Mean latency", num(latency.get("mean_ms"), " ms")],
            ["P95 latency", num(latency.get("p95_ms"), " ms")],
            ["Error rate", pct(latency.get("error_rate"))],
            ["Timeout rate", pct(latency.get("timeout_rate"))],
            [
                "Tokens / cost / TTFT",
                f"{missing.get('tokens', 'N/A')} / {missing.get('cost_usd', 'N/A')} / {missing.get('ttft_ms', 'N/A')}",
            ],
        ]
        if report.get("planner_error"):
            rows.append(["Planner", report["planner_error"]])
        self.fill(self.analysis_facts, ["Fact", "Value"], rows)
        self.analysis_facts.horizontalHeader().setStretchLastSection(True)
        failing = [
            [item["id"], item["severity"], item["repetitions_failed"], ", ".join(item["failed_checks"])]
            for item in report.get("top_failing_test_cases") or []
        ]
        self.fill(self.failing_table, ["Test Case", "Severity", "Failed reps", "Failed checks"], failing)
        gaps = [
            [
                item["role"],
                "N/A" if item["score"] is None else f"{item['score']:.0%}",
                str(item.get("sample_size") or 0),
            ]
            for item in report.get("evaluator_gaps") or []
        ]
        self.fill(self.gap_table, ["Role", "Score", "n"], gaps)
        recurrence = report.get("recurrence") or []
        self.recurrence_label.setText(
            "Recurrence: "
            + ", ".join(f"{item['test_case']} ({item['failed_of_observed']})" for item in recurrence)
            if recurrence
            else "Recurrence: no Test Case failed every observed repetition."
        )
        tips = report.get("suggested_next") or []
        self.suggestions.setText(
            "Suggested next checks:\n" + "\n".join(f"• {tip}" for tip in tips)
            if tips
            else "Suggested next checks: N/A"
        )

    def render_problems(self, batch):
        problems = batch.get("problems") or []
        rows = [
            [
                item["problem"],
                item["severity"],
                ", ".join(item["test_cases"]),
                ", ".join(item["failed_checks"]),
                item["failed_of_five"],
                item["evidence"],
            ]
            for item in problems
        ]
        self.fill(
            self.problem_table,
            ["Problem", "Severity", "Test Case(s)", "Failed checks", "Failed reps", "Evidence"],
            rows,
        )
        self.problem_table.setVisible(bool(rows))
        self.problem_empty.setVisible(not rows)
        self.problem_heading.setText("Find out problem")

    def export_analysis_json_file(self):
        if not self.rendered_batch or not self.rendered_batch.get("analysis"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analysis JSON", self.rendered_batch["id"] + "-analysis.json", "JSON (*.json)"
        )
        if path:
            Path(path).write_text(json.dumps(self.rendered_batch["analysis"], indent=2, ensure_ascii=False))

    def export_analysis_markdown(self):
        if not self.rendered_batch or not self.rendered_batch.get("analysis"):
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export analysis Markdown", self.rendered_batch["id"] + "-analysis.md", "Markdown (*.md)"
        )
        if path:
            Path(path).write_text(analysis_markdown(self.rendered_batch["analysis"], self.rendered_batch))

    def refresh_records(self, selected=None):
        current = selected or self.record_picker.currentData()
        self.record_picker.blockSignals(True)
        self.record_picker.clear()
        self.record_picker.addItem("Compare with a saved record…", None)
        for record in self.store.list_records():
            created = (record.get("created") or "")[:16].replace("T", " ")
            score = record.get("score_mean")
            score_text = "N/A" if score is None else f"{score:.1%}"
            self.record_picker.addItem(f"{record['name']}  ·  {created} HKT  ·  {score_text}", record["id"])
        if current:
            index = self.record_picker.findData(current)
            if index >= 0:
                self.record_picker.setCurrentIndex(index)
        self.record_picker.blockSignals(False)
        self.render_dull_index()

    def save_to_record(self):
        if not self.rendered_batch or self.rendered_batch.get("status") != "complete":
            self.statusBar().showMessage("Only a completed analysis report can be saved to a record.")
            return
        default = (
            f"{(self.rendered_batch.get('suite') or {}).get('name') or 'Session'} {self.rendered_batch['id']}"
        )
        name, ok = QInputDialog.getText(
            self, "Save to record", "Record name (stored only on this computer):", text=default
        )
        if not ok:
            return
        snapshot = record_snapshot(self.rendered_batch, name)
        self.store.save_record(snapshot)
        self.message(f"Saved local record “{snapshot['name']}”. Keys and secrets are not stored.")
        self.refresh_records(snapshot["id"])

    def render_dull_index(self, *_):
        if not hasattr(self, "dull_heading"):
            return
        self.dull_heading.setText("How Dull changed 指數")
        record_id = self.record_picker.currentData() if hasattr(self, "record_picker") else None
        record = None
        if record_id:
            try:
                record = self.store.get_record(record_id)
            except ValueError:
                record = None
        view = dull_change_view(record, self.rendered_batch)
        self.dull_value.setText(view["display"])
        if record is None:
            self.dull_scores.setText("Select a saved record to compare with the current analysis report.")
        else:
            created = (view.get("record_created") or "")[:19].replace("T", " ")
            self.dull_scores.setText(f"{view['record_name']} ({created} HKT)  ·  {view['scores_display']}")
        self.dull_reason.setText(view["reason"])
        self.dull_notes.setText("\n".join(f"• {item}" for item in view["bullets"]))

    def set_baseline(self):
        if not self.rendered_batch:
            return
        try:
            self.store.set_batch_baseline(self.rendered_batch["id"])
            self.message(
                "Complete batch selected as baseline. Future matched batches will compare against it."
            )
            self.statusBar().showMessage("Baseline selected for future sessions.")
        except ValueError:
            self.statusBar().showMessage("Only complete batches can be baselines.")

    def export_json(self):
        if not self.rendered_batch:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export batch JSON", self.rendered_batch["id"] + ".json", "JSON (*.json)"
        )
        if path:
            Path(path).write_text(json.dumps(self.rendered_batch, indent=2, ensure_ascii=False))

    def export_csv(self):
        if not self.rendered_batch:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export repetition metrics CSV", self.rendered_batch["id"] + ".csv", "CSV (*.csv)"
        )
        if path:
            Path(path).write_text(batch_csv(self.rendered_batch))

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.cancel_batch()
            self.statusBar().showMessage("Cancelling active evaluation. Close again after it stops.")
            event.ignore()
            return
        if self.rubric_worker and self.rubric_worker.isRunning():
            self.statusBar().showMessage("Wait for the bounded rubric request to finish before closing.")
            event.ignore()
            return
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Product Blinder native desktop program (no browser)")
    parser.add_argument("--db", default=os.getenv("PB_DB"))
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Instantiate native widgets/charts, process events, save smoke data, exit",
    )
    parser.add_argument("--smoke-out", default="native-smoke.json")
    parser.add_argument("--capture-dir", help="Diagnostic native QWidget render captures, not mockups")
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("Product Blinder")
    app.setApplicationVersion("0.7.0")
    app.setDesktopFileName("product-blinder-v07")
    app.setWindowIcon(icon("app"))
    app.setStyle("Fusion")
    app.setPalette(light_palette())
    app.setStyleSheet(STYLE)
    reduced_motion = os.getenv("PB_REDUCED_MOTION", "0") == "1"
    splash = None if args.smoke else create_opening_splash()
    if splash is not None:
        splash.show()
        app.processEvents()
    window = MainWindow(args.db)
    if args.capture_dir:
        window.resize(1540, 1040)
    if args.smoke:
        window.show()

        def smoke():
            if args.capture_dir:
                directory = Path(args.capture_dir)
                directory.mkdir(parents=True, exist_ok=True)
                for index, name in [(0, "chatbot"), (1, "dashboard")]:
                    window.tabs.setCurrentIndex(index)
                    app.processEvents()
                    for canvas in window.canvases:
                        canvas.draw()
                    window.grab().save(str(directory / ("native-" + name + ".png")))
            payload = {
                "toolkit": "PySide6 Qt Widgets",
                "browser_required": False,
                "tabs": [window.tabs.tabText(i) for i in range(window.tabs.count())],
                "native_chart_canvas": type(window.canvas).__name__,
                "plot_axes": sum(len(fig.axes) for fig in window.figures),
                "native_chart_widgets": len(window.canvases),
                "default_repetitions": window.repetitions.value(),
                "batch_history_count": window.history.count(),
                "qt_platform": app.platformName(),
            }
            Path(args.smoke_out).write_text(json.dumps(payload, indent=2))
            app.quit()

        QTimer.singleShot(600, smoke)
    else:
        reveal_after_splash(app, window, splash, reduced_motion=reduced_motion)
    result = app.exec()
    # Dispose native widgets/canvases while QApplication still exists (Qt shutdown ordering).
    window.close()
    for figure in window.figures:
        figure.clear()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
