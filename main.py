import sys
import os
import json
import yaml
import logging
import time
from pathlib import Path
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                 QHBoxLayout, QSplitter, QTableWidget, QTableWidgetItem,
                                   QTextEdit, QPushButton, QLabel, QFileDialog, QProgressBar,
                                   QComboBox, QLineEdit, QTextBrowser,
                                   QDialog, QDialogButtonBox, QMessageBox,
                                   QStyleFactory, QCheckBox, QHeaderView, QDoubleSpinBox,
                                   QTabWidget, QSizePolicy, QFrame, QLayout)
from PySide6.QtCore import QObject, Signal, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QColor, QPainter

from src.theme import Theme, TOKENS, apply_theme, detect_system_theme
from src.pdf_parser.runner import PdfParserRunner
from src.pdf_parser.review_dialog import ReviewDialog
from src.pdf_parser.feedback import FeedbackCollector
from src.toast import ToastManager
from src.widget_base import CardWidget, paint_styled_background, setup_shadow
from src.excel_writer import ExcelWriter
from src.llm_client import LLMClient
from src.mcp_agent_runner import MCPAgentRunner
from gui.graph_assistant import AssistantToolPanel
from gui.graph_explorer import GraphExplorerWidget
from gui.agent_monitor import AgentMonitorPanel
from gui.metrics_panel import MetricsPanel
from gui.spinner_widget import SpinnerWidget
from src.graph_engine import GraphEngine
from src.skip_registry import SkipRegistry


class LogReceiver(QObject):
    log_received = Signal(str, str, str)

_log_receiver = LogReceiver()


class UiLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            level = {"INFO": "INFO", "WARNING": "WARN", "ERROR": "ERR", "CRITICAL": "ERR"}.get(record.levelname, "INFO")
            phase = getattr(record, 'phase', record.name)
            _log_receiver.log_received.emit(level, phase, msg)
        except Exception:
            pass


logging.basicConfig(level=logging.DEBUG, force=True, format='%(levelname)s:%(name)s:%(message)s')
logging.getLogger().addHandler(UiLogHandler())

_log_dir = Path(__file__).parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = logging.FileHandler(_log_dir / "runtime.log", encoding="utf-8", mode="w")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s'))
logging.getLogger().addHandler(_file_handler)
logging.getLogger("pricer").setLevel(logging.DEBUG)
for noisy in ['websockets', 'asyncio', 'urllib3', 'httpx', 'httpcore', 'httpcore.http11', 'httpcore.connection']:
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(self, config, parent=None, theme_name=Theme.DARK):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.config = config
        self._theme_name = theme_name
        self._tokens = TOKENS.get(theme_name, TOKENS[Theme.DARK])
        setup_shadow(self, self._tokens)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Настройки LLM")
        layout.addWidget(title)

        self.fields = {}
        lm = config.get("llm", {})

        def add_field(label, widget):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(140)
            row.addWidget(lbl)
            row.addWidget(widget)
            layout.addLayout(row)

        self.fields["url"] = QLineEdit(str(lm.get("url", "http://localhost:1234/v1/chat/completions")))
        add_field("URL:", self.fields["url"])

        self.fields["model"] = QLineEdit(str(lm.get("model", "")))
        add_field("Модель:", self.fields["model"])

        temp = QDoubleSpinBox()
        temp.setRange(0.0, 2.0); temp.setSingleStep(0.05)
        temp.setValue(float(lm.get("temperature", 0.1)))
        self.fields["temperature"] = temp
        add_field("Температура:", temp)

        timeout = QDoubleSpinBox()
        timeout.setRange(10, 600); timeout.setSingleStep(10)
        timeout.setValue(int(lm.get("timeout", 120)))
        self.fields["timeout"] = timeout
        add_field("Таймаут (с):", timeout)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.save_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.setFixedSize(self.sizeHint())

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_styled_background(self, painter, self._tokens)

    def save_and_accept(self):
        settings_path = Path(__file__).parent / "config" / "settings.yaml"
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}
        cfg.setdefault("llm", {})
        cfg["llm"]["url"] = self.fields["url"].text().strip() or "http://localhost:1234/v1/chat/completions"
        cfg["llm"]["model"] = self.fields["model"].text().strip()
        cfg["llm"]["temperature"] = self.fields["temperature"].value()
        cfg["llm"]["timeout"] = int(self.fields["timeout"].value())
        with open(settings_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pricer Vision v31.0")
        self.resize(1600, 950)
        self.config = self._load_config()
        self.excel_writer = ExcelWriter(self.config)
        self._engine = GraphEngine("data/pricer.db")
        self._engine.build()
        from pathlib import Path
        yaml_path = "config/categories_and_sites.yaml"
        if Path(yaml_path).exists():
            self._engine.load_yaml_seed(yaml_path)

        self._processing_active = False
        self._spec_path = None
        self._total_rows = 0
        self._skip_registry = SkipRegistry()
        self._skip_reconciling = False
        from src.approach_relevance import load_rules
        load_rules()
        from src.config_loader import load_settings
        self._current_theme = load_settings().get("ui", {}).get("theme") or detect_system_theme()
        self._spinner_color = "#89b4fa"
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(120)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._spinner = SpinnerWidget(size=14, color=self._spinner_color, spacing=0.5)

        self._setup_ui()
        self._connect_signals()

        apply_theme(QApplication.instance(), self._current_theme)
        self.toast_manager = ToastManager(self)

    def closeEvent(self, event):
        if self._processing_active and hasattr(self, '_runner') and self._runner:
            self._runner.stop()
            self._runner.wait(3000)
        if hasattr(self, 'graph_widget'):
            self.graph_widget._physics.stop()
        super().closeEvent(event)

    def _load_config(self):
        config_path = Path(__file__).parent / "config" / "settings.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _setup_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        main_layout = QVBoxLayout(cw)
        main_layout.setContentsMargins(10, 2, 10, 10)
        main_layout.setSpacing(4)

        btn_frame = QFrame()
        btn_frame.setFrameShape(QFrame.NoFrame)
        btn_frame.setLayout(QHBoxLayout()); btn_frame.layout().setContentsMargins(6, 3, 6, 3); btn_frame.layout().setSpacing(10)
        btn_frame.setFixedHeight(38)
        top_bar = btn_frame.layout()

        self.load_btn = QPushButton("📊 Загрузить Excel")
        self.load_btn.clicked.connect(self.load_spec)
        top_bar.addWidget(self.load_btn)

        self.pdf_btn = QPushButton("📄 Загрузить PDF")
        self.pdf_btn.clicked.connect(self._load_pdf)
        top_bar.addWidget(self.pdf_btn)

        self.start_btn = QPushButton("Старт")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.start_processing)
        self.start_btn.setEnabled(False)
        top_bar.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.clicked.connect(self.stop_processing)
        self.stop_btn.setEnabled(False)
        top_bar.addWidget(self.stop_btn)

        self.study_btn = QPushButton("📖 Обучение")
        self.study_btn.clicked.connect(self._open_study_tool)
        top_bar.addWidget(self.study_btn)

        self.clear_skip_btn = QPushButton("Снять отметки")
        self.clear_skip_btn.setToolTip("Снять все отметки «пропустить» в предпросмотре")
        self.clear_skip_btn.setEnabled(False)
        self.clear_skip_btn.clicked.connect(self._clear_skip_marks)
        top_bar.addWidget(self.clear_skip_btn)

        from src.config_loader import load_settings
        self.headless_cb = QCheckBox("🕶️ Headless")
        self.headless_cb.setChecked(load_settings().get("browser", {}).get("headless", True))
        self.headless_cb.toggled.connect(self._on_headless_toggle)
        top_bar.addWidget(self.headless_cb)

        self.fresh_cb = QCheckBox("Не учитывать кэш цен")
        self.fresh_cb.setToolTip("Не использовать ранее сохранённые цены")
        from src.config_loader import get_run_config
        self.fresh_cb.setChecked(get_run_config("fresh", True))
        self.fresh_cb.toggled.connect(self._on_fresh_toggle)
        top_bar.addWidget(self.fresh_cb)

        top_bar.addStretch()

        self.settings_btn = QPushButton("Настройки")
        self.settings_btn.clicked.connect(self.open_settings)
        top_bar.addWidget(self.settings_btn)

        self.deps_btn = QPushButton("🧩 Зависимости")
        self.deps_btn.setToolTip("Обновить и изменить версии зависимостей (pip, @playwright/mcp)")
        self.deps_btn.clicked.connect(self.open_dependency_manager)
        top_bar.addWidget(self.deps_btn)

        self.rules_btn = QPushButton("🧠 Правила сопоставления")
        self.rules_btn.setToolTip("Настроить правила сопоставления наименований товаров (без правки кода)")
        self.rules_btn.clicked.connect(self.open_rules_editor)
        top_bar.addWidget(self.rules_btn)

        self.theme_btn = QPushButton("Тема")
        self.theme_btn.clicked.connect(self._toggle_theme)
        top_bar.addWidget(self.theme_btn)

        main_layout.addWidget(btn_frame, 0)

        fb_frame = QFrame()
        fb_frame.setFrameShape(QFrame.NoFrame)
        fb_layout = QHBoxLayout(fb_frame)
        fb_layout.setContentsMargins(6, 2, 6, 2)
        fb_layout.setSpacing(10)
        self._spinner.setFixedSize(16, 16)
        fb_layout.addWidget(self._spinner)
        self.status_label = QLabel("Готов")
        self.status_label.setFixedHeight(24)
        fb_layout.addWidget(self.status_label, 1)
        fb_frame.setFixedHeight(28)
        main_layout.addWidget(fb_frame, 0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(21)
        main_layout.addWidget(self.progress_bar, 0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)
        self._splitter = splitter

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        center_tabs = QTabWidget()
        center_tabs.setTabPosition(QTabWidget.South)
        self._center_tabs = center_tabs

        self.results_table = QTableWidget(0, 9)
        self.results_table.setHorizontalHeaderLabels([
            "#", "Спецификация", "Тип", "Цена", "Уверенность", "Время", "Сайт", "URL", "Обучение"
        ])
        self.results_table.setColumnWidth(0, 30)
        self.results_table.setColumnWidth(1, 200)
        self.results_table.setColumnWidth(2, 80)
        self.results_table.setColumnWidth(3, 90)
        self.results_table.setColumnWidth(4, 80)
        self.results_table.setColumnWidth(5, 65)
        self.results_table.setColumnWidth(6, 110)
        self.results_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.results_table.setColumnWidth(8, 110)
        self.results_table.verticalHeader().setDefaultSectionSize(30)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.itemDoubleClicked.connect(self._on_url_double_click)
        center_tabs.addTab(self.results_table, "Результаты")

        self.preview_table = QTableWidget(0, 7)
        self.preview_table.setHorizontalHeaderLabels(
            ["Пропустить", "#", "Наименование", "Производитель", "Тип/обозначение", "Артикул", "Кол-во"]
        )
        self.preview_table.setColumnWidth(0, 70)
        self.preview_table.setColumnWidth(1, 30)
        self.preview_table.setColumnWidth(2, 260)
        self.preview_table.setColumnWidth(3, 110)
        self.preview_table.setColumnWidth(4, 140)
        self.preview_table.setColumnWidth(5, 130)
        self.preview_table.setColumnWidth(6, 60)
        self.preview_table.setAlternatingRowColors(True)
        center_tabs.addTab(self.preview_table, "Предпросмотр")

        self.log_browser = QTextBrowser()
        center_tabs.addTab(self.log_browser, "Логи")
        center_tabs.setCurrentIndex(1)  # start on Предпросмотр

        center_layout.addWidget(center_tabs, 2)
        splitter.addWidget(center)

        right_tabs = QTabWidget()
        self._right_tabs = right_tabs
        right_tabs.setTabPosition(QTabWidget.South)
        right_tabs.setMinimumWidth(0)
        right_tabs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        self.graph_widget = GraphExplorerWidget()
        self.graph_widget.load_graph(self._engine)
        right_tabs.addTab(self.graph_widget, "Граф")

        self.assistant_panel = AssistantToolPanel()
        self.assistant_panel.engine = self._engine
        self.assistant_panel.llm_config = self.config.get("llm", {})
        right_tabs.addTab(self.assistant_panel, "Ассистент")

        monitor_widget = QWidget()
        monitor_layout = QVBoxLayout(monitor_widget)
        monitor_layout.setContentsMargins(0, 0, 0, 0)
        monitor_layout.setSpacing(4)
        self.monitor_panel = AgentMonitorPanel()
        monitor_layout.addWidget(self.monitor_panel, 2)
        self.metrics_panel = MetricsPanel()
        monitor_layout.addWidget(self.metrics_panel, 1)
        right_tabs.addTab(monitor_widget, "Мониторинг")

        splitter.addWidget(right_tabs)

        splitter.setSizes([700, 500])
        main_layout.addWidget(splitter, 1)

        self._log_data = []
        self._log_mode = "all"

    def _connect_signals(self):
        _log_receiver.log_received.connect(self.add_log)
        self.preview_table.itemChanged.connect(self._on_preview_item_changed)

    def _on_url_double_click(self, item):
        if item.column() == 6 and item.text().startswith("http"):
            QDesktopServices.openUrl(QUrl(item.text()))

    def add_log(self, level, phase, message):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level,
            "phase": phase,
            "message": message,
        }
        self._log_data.append(entry)
        if len(self._log_data) > 5000:
            self._log_data = self._log_data[-2500:]

        t = TOKENS.get(self._current_theme, TOKENS[Theme.DARK])
        level_colors = {"INFO": t["success"], "WARN": t["warning"], "ERR": t["danger"], "DEBUG": t["text-muted"]}
        lc = level_colors.get(level, t["text-primary"])
        ts = entry["timestamp"][11:19]
        safe_msg = str(message).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        border = f'border-left:3px solid {t["danger"]};' if level == "ERR" else \
                 f'border-left:3px solid {t["warning"]};' if level == "WARN" else ''
        self.log_browser.append(
            f'<div style="margin-bottom:2px;line-height:1.3;{border}padding-left:4px;">'
            f'<span style="color:{t["text-muted"]};font-size:11px;">[{ts}]</span>'
            f' <span style="color:{lc};font-weight:600;font-size:11px;">[{level}]</span>'
            f' <span style="color:{t["accent"]};font-size:11px;">[{phase}]</span>'
            f' <span style="color:{t["text-primary"]};font-size:11px;">{safe_msg}</span></div>'
        )
        self.log_browser.verticalScrollBar().setValue(
            self.log_browser.verticalScrollBar().maximum()
        )

    def _toggle_theme(self):
        self._current_theme = Theme.LIGHT if self._current_theme == Theme.DARK else Theme.DARK
        apply_theme(QApplication.instance(), self._current_theme)
        from src.config_loader import save_theme
        save_theme(self._current_theme)

    def open_settings(self):
        self.config = self._load_config()
        dlg = SettingsDialog(self.config, self, theme_name=self._current_theme)
        dlg.exec()
        self.config = self._load_config()

    def open_dependency_manager(self):
        from src.dependency_manager.dialog import DependencyManagerDialog
        dlg = DependencyManagerDialog(
            Path(__file__).parent,
            parent=self,
            busy=getattr(self, "_processing_active", False),
        )
        dlg.exec()

    def open_rules_editor(self):
        from gui.rules_editor import RulesEditorDialog
        dlg = RulesEditorDialog(parent=self)
        dlg.exec()

    def load_spec(self, path: str | None = None):
        if not path:
            input_dir = self.config.get("paths", {}).get("data_input", "data/input")
            path, _ = QFileDialog.getOpenFileName(self, "Open spec.xlsx", input_dir, "Excel (*.xlsx)")
            if not path:
                return

        try:
            headers, data_rows = self.excel_writer.load_spec(path)
            self._spec_path = path
            self._total_rows = data_rows
            self._skip_registry.reset()
            self.start_btn.setEnabled(True)
            self._show_preview()
            self._center_tabs.setCurrentIndex(1)  # switch to Предпросмотр
            self.add_log("INFO", "init", f"Loaded {self._total_rows} rows from {Path(path).name}")
            mapping = self.excel_writer.detect_columns(headers)
            self.status_label.setText(
                f"Загружено: {self._total_rows} строк · Колонки: {self._mapping_hint(mapping)}"
            )
            self.toast_manager.success(f"Loaded {self._total_rows} rows")
        except Exception as e:
            self.add_log("ERR", "init", f"Failed to load: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить файл:\n{e}")

    @staticmethod
    def _mapping_hint(mapping: dict) -> str:
        """Краткий список распознанных колонок для статусной строки."""
        roles = [
            ("name", "Наименование"), ("brand", "Производитель"), ("spec", "Тип/обозначение"),
            ("article", "Артикул"), ("uom", "Ед.изм."), ("qty", "Кол-во"),
        ]
        found = []
        for key, label in roles:
            value = mapping.get(key)
            present = bool(value) if key in ("name", "brand", "spec", "article") else value is not None
            if present:
                found.append(label)
        return ", ".join(found) if found else "нет распознанных колонок"

    def _show_preview(self):
        ws = self.excel_writer.ws
        headers = self.excel_writer.headers
        if ws is None:
            return

        mapping = self.excel_writer.detect_columns(headers)
        name_cols = mapping.get("name", [])
        brand_cols = mapping.get("brand", [])
        spec_cols = mapping.get("spec", [])
        article_cols = mapping.get("article", [])
        qty_col = mapping.get("qty")

        self.preview_table.setRowCount(0)
        self._skip_reconciling = True
        try:
            preview_rows = []
            for excel_row in range(2, ws.max_row + 1):
                name = self.excel_writer.build_item_name(excel_row, mapping)[0]
                if not name or name.strip() in ("", "None", "none"):
                    continue
                spec_item = self.excel_writer.spec_for_row(excel_row)
                preview_rows.append((excel_row, spec_item, name))

            self.preview_table.setRowCount(len(preview_rows))
            for i, (excel_row, spec_item, name) in enumerate(preview_rows):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                check.setCheckState(Qt.Unchecked)
                check.setData(Qt.UserRole, excel_row)
                check.setData(Qt.UserRole + 1, spec_item.text if spec_item else name)
                check.setData(Qt.UserRole + 2, spec_item.brand if spec_item else "")
                self.preview_table.setItem(i, 0, check)

                self.preview_table.setItem(i, 1, QTableWidgetItem(str(excel_row - 1)))

                self.preview_table.setItem(i, 2, QTableWidgetItem(name[:80]))

                brand = self._concat_display(ws, excel_row, brand_cols)
                self.preview_table.setItem(i, 3, QTableWidgetItem(brand))

                spec = self._concat_display(ws, excel_row, spec_cols)
                self.preview_table.setItem(i, 4, QTableWidgetItem(spec[:80]))

                article_parts = []
                for idx in article_cols:
                    val = str(ws.cell(excel_row, idx + 1).value or "").strip()
                    if val and val not in ("None", ""):
                        article_parts.append(val)
                self.preview_table.setItem(i, 5, QTableWidgetItem(", ".join(article_parts)))

                qty_val = ""
                if qty_col is not None:
                    v = ws.cell(excel_row, qty_col + 1).value
                    if v is not None:
                        qty_val = str(v)
                self.preview_table.setItem(i, 6, QTableWidgetItem(qty_val))
        finally:
            self._skip_reconciling = False
        self._reconcile_skip_checks()

    def _on_preview_item_changed(self, item):
        if self._skip_reconciling or item.column() != 0:
            return
        text = item.data(Qt.UserRole + 1)
        if not text:
            return
        brand = item.data(Qt.UserRole + 2) or ""
        if item.checkState() == Qt.Checked:
            self._skip_registry.mark(text, brand)
        else:
            self._skip_registry.unmark(text, brand)
        self._reconcile_skip_checks()

    def _reconcile_skip_checks(self):
        """Синхронизирует чекбоксы предпросмотра с реестром пропуска (транзитивно)."""
        self._skip_reconciling = True
        try:
            t = TOKENS.get(self._current_theme, TOKENS[Theme.DARK])
            skipped_count = 0
            for row in range(self.preview_table.rowCount()):
                item = self.preview_table.item(row, 0)
                if not item:
                    continue
                text = item.data(Qt.UserRole + 1)
                brand = item.data(Qt.UserRole + 2) or ""
                matched = self._skip_registry.matches(text, brand)
                target = Qt.Checked if matched else Qt.Unchecked
                if item.checkState() != target:
                    item.setCheckState(target)
                item.setToolTip(f"Пропускается: полный аналог «{matched}»" if matched else "")
                if matched:
                    skipped_count += 1
                    fg = QColor(t["text-muted"])
                else:
                    fg = QColor(t["text-primary"])
                for c in range(1, self.preview_table.columnCount()):
                    cell = self.preview_table.item(row, c)
                    if cell:
                        cell.setForeground(fg)
            if hasattr(self, "clear_skip_btn"):
                self.clear_skip_btn.setEnabled(skipped_count > 0)
        finally:
            self._skip_reconciling = False

    def _clear_skip_marks(self):
        self._skip_registry.reset()
        self._reconcile_skip_checks()
        self.add_log("INFO", "init", "Отметки «пропустить» сняты")

    @staticmethod
    def _concat_display(ws, excel_row: int, indices: list[int]) -> str:
        """Собирает значения колонок для предпросмотра (без кавычек бренда)."""
        parts = []
        for idx in indices:
            val = str(ws.cell(excel_row, idx + 1).value or "").strip()
            val = val.strip('"«»')
            if val and val not in ("None", ""):
                parts.append(val)
        return " ".join(parts)

    def _tick_spinner(self):
        self._spinner.tick()

    def start_processing(self):
        if not self._spec_path:
            return

        self.config = self._load_config()
        self._processing_active = True
        self.results_table.setRowCount(0)
        self.log_browser.clear()
        self.progress_bar.setValue(0)
        self._center_tabs.setCurrentIndex(0)  # switch to Результаты
        self._spinner.setFixedSize(20, 20)
        self._spinner.tick()
        self._spinner_timer.start()

        lm = self.config.get("llm", {})
        llm_client = LLMClient(
            url=lm.get("url", "http://localhost:1234/v1/chat/completions"),
            model=lm.get("model", ""),
            temperature=float(lm.get("temperature", 0.3)),
            timeout=int(lm.get("timeout", 120)),
        )
        from src.llm_client import FALLBACK_URLS
        llm_client.set_fallbacks(FALLBACK_URLS)

        self._runner = MCPAgentRunner(
            specs=self.excel_writer.get_specs(),
            llm_client=llm_client,
            fresh=self.fresh_cb.isChecked(),
            skip_registry=self._skip_registry,
        )
        self.monitor_panel.reset()
        self.metrics_panel.reset()
        self._runner.status_signal.connect(self._on_runner_status)
        self._runner.row_done_signal.connect(self._on_row_done)
        self._runner.done_signal.connect(self._on_all_done)
        self._runner.error_signal.connect(self._on_runner_error)
        self._runner.monitor_signal.connect(self._on_monitor_event)
        self._runner.metrics_signal.connect(self._on_metrics)
        self._runner.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.add_log("INFO", "init", f"Processing {self._total_rows} rows")

    def stop_processing(self):
        if hasattr(self, '_pdf_runner') and self._pdf_runner and self._pdf_runner.isRunning():
            self._pdf_runner.stop()
            self.add_log("INFO", "pdf", "Остановлено пользователем")
            self.status_label.setText("Остановлен")
            return
        if hasattr(self, '_runner') and self._runner:
            self._runner.stop()
        self._spinner_timer.stop()
        self._spinner.setFixedSize(0, 0)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._processing_active = False
        self.add_log("INFO", "control", "Остановлено пользователем")
        self.status_label.setText("Остановлен")

    def _on_runner_status(self, status):
        if isinstance(status, tuple):
            _, done, total, msg = status
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(done)
            self.status_label.setText(msg)
        elif status == "start":
            self.status_label.setText("Запуск...")
            self.add_log("INFO", "init", "Обработка начата")

    def _on_monitor_event(self, event):
        self.monitor_panel.handle_event(event)

    def _on_metrics(self, stats):
        self.metrics_panel.update_metrics(stats)

    def _on_row_done(self, idx, result):
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)

        price = result.get("price")
        conf = result.get("confidence", 0)
        price_text = f"₽{price:,.2f}" if price is not None else "—"
        conf_text = f"{conf:.0%}" if conf else "—"
        elapsed = result.get("elapsed")
        elapsed_text = f"{elapsed:.0f}с" if elapsed is not None else "—"
        site = result.get("site", "")
        url = result.get("url", "")
        spec = result.get("spec_text", "")[:60]
        pt = result.get("product_type", "")
        error = result.get("error", "")
        brand_mismatch = result.get("brand_mismatch", False)

        items = [
            str(idx + 1), spec, pt, price_text, conf_text, elapsed_text,
            site[:40] if site else "", url[:80] if url else ""
        ]
        t = TOKENS.get(self._current_theme, TOKENS[Theme.DARK])
        for c, text in enumerate(items):
            item = QTableWidgetItem(text)
            if error:
                item.setForeground(QColor(t["danger"]))
            elif brand_mismatch:
                item.setForeground(QColor(t["warning"]))
            elif price is not None:
                item.setForeground(QColor(t["success"]))
            else:
                item.setForeground(QColor(t["warning"]))
            self.results_table.setItem(row, c, item)

        # Study button — constrained to the row height so it can't overflow the cell
        study_btn = QPushButton("🤖 Обучить")
        study_btn.setObjectName("row-action")
        study_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        study_btn.setMinimumHeight(0)
        study_btn.setMaximumHeight(self.results_table.verticalHeader().defaultSectionSize())
        study_btn.setToolTip("Обучить агента на этом товаре")
        spec_full = result.get("spec_text", "")
        err_ctx = error or result.get("reason", "")
        if err_ctx:
            err_ctx = f"Ошибка: {err_ctx}\nСайт: {site or '?'}"
        study_btn.clicked.connect(
            lambda checked, s=spec_full, p=pt, f=err_ctx: self._open_study(s, p, f)
        )
        btn_wrap = QWidget()
        btn_wrap.setMinimumSize(0, 0)
        btn_layout = QHBoxLayout(btn_wrap)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(0)
        btn_layout.setSizeConstraint(QLayout.SetNoConstraint)
        btn_layout.setAlignment(Qt.AlignCenter)
        btn_layout.addWidget(study_btn)
        self.results_table.setCellWidget(row, 8, btn_wrap)

        self.results_table.scrollToBottom()

        if error:
            self.add_log("WARN", "agent", f"Row {idx+1}: {error}")
        elif brand_mismatch:
            self.add_log("WARN", "agent", f"Row {idx+1}: {price_text} ({conf_text}) on {site} — НЕ СОВПАДАЕТ БРЕНД")
        elif price:
            self.add_log("INFO", "agent", f"Row {idx+1}: {price_text} ({conf_text}) on {site}")

        ws = self.excel_writer.ws
        hm = self.excel_writer.header_map
        if ws and hm:
            excel_row = idx + 2
            ws.cell(excel_row, hm["price"], price)
            ws.cell(excel_row, hm["url"], url or "")
            ws.cell(excel_row, hm["category"], pt or "")
            if brand_mismatch and "note" in hm:
                ws.cell(excel_row, hm["note"], "не совпадает бренд")

    def _switch_to_assistant(self):
        """Switch right panel to assistant tab and ensure it's visible."""
        for i in range(self._right_tabs.count()):
            if self._right_tabs.tabText(i) == "Ассистент":
                self._right_tabs.setCurrentIndex(i)
                break
        sizes = self._splitter.sizes()
        if sizes[1] < 100:
            self._splitter.setSizes([700, 500])

    def _open_study(self, spec_text: str, product_type: str, failure_context: str = ""):
        """Open study tool in assistant panel pre-filled with row data."""
        self.assistant_panel.prefill_study(spec_text, product_type, failure_context)
        self._switch_to_assistant()

    def _open_study_tool(self):
        """Open study tool with empty fields (user fills manually)."""
        self.assistant_panel.prefill_study("", "")
        self._switch_to_assistant()

    def _load_pdf(self):
        if getattr(self, "_pdf_runner", None) and self._pdf_runner.isRunning():
            self.add_log("WARN", "pdf", "PDF уже обрабатывается")
            return

        path, _ = QFileDialog.getOpenFileName(self, "Открыть PDF спецификацию", "",
                                               "PDF (*.pdf)")
        if not path:
            return

        self.pdf_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._spinner_timer.start()
        self._spinner.setFixedSize(20, 20)

        lm = self.config.get("llm", {})
        llm_client = LLMClient(
            url=lm.get("url", "http://localhost:1234/v1/chat/completions"),
            model=lm.get("model", ""),
            temperature=0.1,
            timeout=int(lm.get("timeout", 120)),
        )
        from src.llm_client import FALLBACK_URLS
        llm_client.set_fallbacks(FALLBACK_URLS)

        self._pdf_runner = PdfParserRunner(
            pdf_path=path,
            llm_client=llm_client,
            config=self.config,
        )
        self._pdf_runner.progress_signal.connect(self._on_pdf_progress)
        self._pdf_runner.items_ready_signal.connect(self._on_pdf_items_ready)
        self._pdf_runner.done_signal.connect(self._on_pdf_done)
        self._pdf_runner.start()

        self.add_log("INFO", "pdf", f"Загрузка PDF: {Path(path).name}")

    def _on_pdf_progress(self, msg: str, step: int, total: int):
        self.status_label.setText(msg)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(step)

    def _on_pdf_items_ready(self, items: list[dict]):
        self.add_log("INFO", "pdf", f"Извлечено {len(items)} позиций из PDF")

        from src.pdf_parser.review import SmartReview
        auto = sum(1 for it in items if (it.get("confidence") or 0) >= SmartReview.CONFIDENCE_THRESHOLD)
        if auto:
            self.add_log("INFO", "pdf", f"SmartReview: {auto} авто-подтверждено, {len(items) - auto} требует проверки")

        dlg = ReviewDialog(items, self, theme_name=self._current_theme)
        if dlg.exec() and dlg.is_confirmed:
            confirmed = dlg.items
            self.add_log("INFO", "pdf", f"Подтверждено {len(confirmed)} позиций")

            feedback = FeedbackCollector()
            for orig, cor in zip(items, confirmed):
                orig_name = orig.get("name", "")
                cor_name = cor.get("name", "")
                if orig_name != cor_name:
                    feedback.save_correction(orig_name, cor_name, "manual")
                    self.add_log("INFO", "pdf", f"Исправление: «{orig_name}» → «{cor_name}»")

            xlsx_path = self._save_pdf_items_to_excel(confirmed)
            if xlsx_path:
                self.load_spec(path=xlsx_path)
                self.toast_manager.success(f"PDF: {len(confirmed)} позиций готово к старту")
        else:
            self.add_log("INFO", "pdf", "PDF спецификация отклонена пользователем")

    def _build_spec_text(self, item: dict) -> str:
        pos = item.get("pos", 0)
        name = item.get("name", "")
        specs = item.get("specs", "")
        qty = item.get("qty", 0)
        unit = item.get("unit", "шт")
        parts = [f"{pos}. {name}"]
        if specs:
            parts.append(f" {specs}")
        parts.append(f" — {qty} {unit}")
        return "".join(parts)

    def _load_pdf_item_into_spec(self, spec_text: str):
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        t = TOKENS.get(self._current_theme, TOKENS[Theme.DARK])
        items = [
            str(row + 1),
            spec_text[:80] if spec_text else "",
            "PDF",
            "",
            "",
            "",
            "",
            "",
        ]
        for c, text in enumerate(items):
            item = QTableWidgetItem(text)
            item.setForeground(QColor(t["accent"]))
            self.results_table.setItem(row, c, item)

    def _save_pdf_items_to_excel(self, items: list[dict]):
        out_dir = self.config.get("paths", {}).get("data_output", "data/output")
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = Path(out_dir) / f"pdf_spec_{ts}.xlsx"

        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PDF спецификация"
        ws.append(["#", "Наименование", "Характеристики", "Артикул", "Изготовитель", "Кол-во", "Ед."])
        for item in items:
            ws.append([
                item.get("pos", ""),
                item.get("name", ""),
                item.get("specs", ""),
                item.get("code", ""),
                item.get("manufacturer", ""),
                item.get("qty", ""),
                item.get("unit", "шт"),
            ])
        wb.save(str(out_path))
        self.add_log("INFO", "pdf", f"Сохранено: {out_path.name}")
        return str(out_path)

    def _on_pdf_done(self, success: bool, msg: str):
        self._spinner_timer.stop()
        self._spinner.setFixedSize(0, 0)
        self.pdf_btn.setEnabled(True)
        if not self._processing_active:
            self.stop_btn.setEnabled(False)
        self.status_label.setText(msg if success else f"Ошибка: {msg}")
        if success:
            self.add_log("INFO", "pdf", msg)
        else:
            self.add_log("ERR", "pdf", msg)

    def _on_all_done(self, success, spec_result):
        self._spinner_timer.stop()
        self._spinner.setFixedSize(0, 0)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._processing_active = False

        total = spec_result.get("total", 0)
        found = spec_result.get("found_count", 0)
        errs = spec_result.get("error_count", 0)

        self.add_log("INFO", "complete",
            f"Готово: {found}/{total} найдено, {errs} ошибок")
        self.status_label.setText(f"Готово: {found}/{total}")

        if found > 0:
            self.toast_manager.success(f"Found {found}/{total} prices")
        else:
            self.toast_manager.warning(f"No prices found ({errs} errors)")

        if self._spec_path:
            try:
                out_dir = self.config.get("paths", {}).get("data_output", "data/output")
                out_path = self.excel_writer.save_output_copy(out_dir)
                self.add_log("INFO", "output", f"Saved to {Path(out_path).name}")
            except Exception as e:
                self.add_log("ERR", "output", f"Save failed: {e}")

    def _on_runner_error(self, msg):
        self._spinner_timer.stop()
        self._spinner.setFixedSize(0, 0)
        self.add_log("ERR", "runner", msg)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._processing_active = False
        self.status_label.setText(f"Ошибка: {msg[:60]}")

    def _on_headless_toggle(self, checked):
        from src.config_loader import save_browser_headless
        save_browser_headless(checked)
        if self._processing_active and hasattr(self, '_runner') and self._runner:
            self._runner.trigger_bridge_restart(checked)
            self.add_log("INFO", "control", f"Bridge restarting with headless={checked}")

    def _on_fresh_toggle(self, checked):
        from src.config_loader import save_fresh
        save_fresh(checked)
        if self._processing_active and hasattr(self, '_runner') and self._runner:
            self._runner.set_fresh(checked)
            mode = "игнорировать кэш цен" if checked else "учитывать кэш цен"
            self.add_log("INFO", "control", f"Режим: {mode} (со следующей позиции)")


def main():
    from PySide6.QtGui import QSurfaceFormat
    fmt = QSurfaceFormat()
    fmt.setSamples(8)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
