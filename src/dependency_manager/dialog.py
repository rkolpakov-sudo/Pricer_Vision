"""Modal dialog for managing project dependencies (pip + @playwright/mcp)."""
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .manager import DependencyManager
from .models import ApplyChange, BrowserInfo, Dependency, Status
from .worker import ApplyWorker, BrowserWorker, CheckWorker

logger = logging.getLogger("pricer.deps.ui")

COL_CHECK, COL_NAME, COL_MANAGER, COL_CURRENT, COL_LATEST, COL_VERSION, COL_STATUS = range(7)

_STATUS_TEXT = {
    Status.CHECKING: "проверка…",
    Status.UPTODATE: "актуальна",
    Status.UPDATE: "есть обновление",
    Status.MISSING: "не установлена",
    Status.DOWNGRADE: "новее актуальной",
    Status.ERROR: "ошибка",
}

_STATUS_COLOR = {
    Status.CHECKING: "#9aa0a6",
    Status.UPTODATE: "#4caf50",
    Status.UPDATE: "#ff9800",
    Status.MISSING: "#9aa0a6",
    Status.DOWNGRADE: "#42a5f5",
    Status.ERROR: "#f44336",
}


class DependencyManagerDialog(QDialog):
    def __init__(self, project_root, parent=None, busy: bool = False):
        super().__init__(parent)
        self._manager = DependencyManager(project_root)
        self._busy = busy
        self._deps: list[Dependency] = []
        self._backup_path = None
        self._check_worker = None
        self._apply_worker = None
        self._browser_worker = None
        self._browser_info = None

        self.setWindowTitle("Зависимости проекта")
        self.resize(920, 620)
        self.setMinimumSize(820, 520)

        self._build_ui()
        self._load_initial()
        self._start_check()

    # ── UI construction ──────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(QLabel("Окружение:"))
        self.env_combo = QComboBox()
        self.env_combo.setMinimumWidth(240)
        self.env_combo.currentIndexChanged.connect(self._on_env_changed)
        top.addWidget(self.env_combo)
        self.check_btn = QPushButton("Проверить обновления")
        self.check_btn.clicked.connect(self._start_check)
        top.addWidget(self.check_btn)
        self.install_browser_cb = QCheckBox("Установить браузер при обновлении @playwright/mcp")
        self.install_browser_cb.setToolTip("Установит Chromium для выбранной версии @playwright/mcp")
        top.addWidget(self.install_browser_cb)
        top.addStretch()
        self.status_label = QLabel("")
        top.addWidget(self.status_label)
        root.addLayout(top)

        browser_group = QGroupBox("Браузеры (текущая конфигурация)")
        browser_layout = QVBoxLayout(browser_group)
        browser_layout.setContentsMargins(8, 14, 8, 8)
        browser_layout.setSpacing(4)
        self._browser_rows = QVBoxLayout()
        self._browser_rows.setSpacing(2)
        browser_layout.addLayout(self._browser_rows)
        browser_btn_row = QHBoxLayout()
        browser_btn_row.addStretch()
        self.browser_btn = QPushButton("Обновить браузеры")
        self.browser_btn.setObjectName("primary")
        self.browser_btn.setToolTip("Установит/обновит браузеры, недостающие для активной конфигурации")
        self.browser_btn.clicked.connect(self._on_browser_update)
        browser_btn_row.addWidget(self.browser_btn)
        browser_layout.addLayout(browser_btn_row)
        root.addWidget(browser_group)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["✓", "Пакет", "Менеджер", "Текущая", "Актуальная", "Версия", "Статус"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_CHECK, 28)
        self.table.setColumnWidth(COL_MANAGER, 64)
        self.table.setColumnWidth(COL_CURRENT, 110)
        self.table.setColumnWidth(COL_LATEST, 110)
        self.table.setColumnWidth(COL_VERSION, 130)
        self.table.setColumnWidth(COL_STATUS, 130)
        root.addWidget(self.table, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(140)
        self.log_view.setVisible(False)
        font = QFont("Consolas")
        font.setPointSize(9)
        self.log_view.setFont(font)
        root.addWidget(self.log_view)

        hint = QLabel("Изменения вступят в силу после перезапуска приложения.")
        hint.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        root.addWidget(hint)

        bottom = QHBoxLayout()
        self.apply_btn = QPushButton("Применить выбранные")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self._on_apply)
        bottom.addWidget(self.apply_btn)
        self.rollback_btn = QPushButton("Откатить")
        self.rollback_btn.setObjectName("danger")
        self.rollback_btn.setEnabled(False)
        self.rollback_btn.clicked.connect(self._on_rollback)
        bottom.addWidget(self.rollback_btn)
        bottom.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    def _load_initial(self):
        envs = self._manager.environments()
        self._envs = envs
        if not envs:
            self.status_label.setText("Окружения не найдены")
            self.apply_btn.setEnabled(False)
            self.check_btn.setEnabled(False)
            return
        for env in envs:
            self.env_combo.addItem(f"{env.name} (Python)", env)

    # ── checking ─────────────────────────────────────────────────
    @property
    def _env(self):
        return self.env_combo.currentData() if self.env_combo.count() else None

    def _on_env_changed(self):
        self._start_check()

    def _set_busy_ui(self, busy: bool):
        self.check_btn.setEnabled(not busy)
        self.env_combo.setEnabled(not busy)
        self.apply_btn.setEnabled(not busy)
        self.browser_btn.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        if busy:
            self.status_label.setText("Работаю…")
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setVisible(False)

    def _start_check(self):
        if self._env is None:
            return
        if self._check_worker is not None and self._check_worker.isRunning():
            return
        self._set_busy_ui(True)
        self._deps = self._manager.load_manifest()
        self.table.setRowCount(0)
        self.status_label.setText("Проверка…")
        self._browsers_checking()
        self._check_worker = CheckWorker(self._manager, self._deps, self._env, self)
        self._check_worker.progress.connect(self._on_check_progress)
        self._check_worker.finished_ok.connect(self._on_check_done)
        self._check_worker.failed.connect(self._on_check_failed)
        self._check_worker.browser_checked.connect(self._on_browser_checked)
        self._check_worker.start()

    def _on_check_progress(self, done, total, message):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        self.status_label.setText(message)

    def _on_check_done(self, deps):
        self._deps = deps
        self._render()
        self._set_busy_ui(False)
        counts = {s: sum(1 for d in deps if d.status == s) for s in Status}
        self.status_label.setText(
            f"Обновлений: {counts[Status.UPDATE]}, актуальных: {counts[Status.UPTODATE]}, "
            f"не установлено: {counts[Status.MISSING]}"
        )

    def _on_check_failed(self, message):
        self._set_busy_ui(False)
        self.status_label.setText("Ошибка проверки")
        QMessageBox.warning(self, "Ошибка", message)

    # ── browsers (per configured backend) ────────────────────────
    def _clear_browser_rows(self):
        while self._browser_rows.count():
            item = self._browser_rows.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    @staticmethod
    def _browser_text(info: BrowserInfo) -> str:
        label = info.label or info.name
        if info.error or not info.expected_rev:
            text = f"{label}: не определён"
            if info.error:
                text += f" — {info.error}"
            return text
        if not info.installed:
            return f"{label}: не установлен (ожидается {info.expected_rev})"
        if info.up_to_date:
            return f"{label}: {info.expected_rev} — актуален"
        return f"{label}: устарел (установлен {info.installed_rev}, ожидается {info.expected_rev})"

    @staticmethod
    def _browser_color(info: BrowserInfo) -> str:
        if info.error or not info.expected_rev:
            return "#9aa0a6"
        if info.up_to_date:
            return "#4caf50"
        return "#ff9800"

    def _on_browser_checked(self, infos):
        self._browser_info = infos if isinstance(infos, list) else [infos]
        self._clear_browser_rows()
        for info in self._browser_info:
            label = QLabel(self._browser_text(info))
            label.setStyleSheet(f"color: {self._browser_color(info)}; font-size: 12px;")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            tip = self._browser_text(info)
            if info.details:
                tip += "\n" + "\n".join(f"{k}: {v}" for k, v in info.details.items())
            label.setToolTip(tip)
            self._browser_rows.addWidget(label)

    def _browsers_checking(self):
        self._browser_info = None
        self._clear_browser_rows()
        label = QLabel("Браузеры: проверка…")
        label.setStyleSheet("color: #9aa0a6; font-size: 12px;")
        self._browser_rows.addWidget(label)

    def _start_browser_check(self):
        try:
            infos = self._manager.browser_status(self._env)
        except Exception as e:  # noqa: BLE001
            infos = [BrowserInfo(error=str(e))]
        self._on_browser_checked(infos)

    def _on_browser_update(self):
        if self._browser_worker is not None and self._browser_worker.isRunning():
            return
        if not self._browser_info:
            QMessageBox.information(self, "Нет данных",
                                    "Сначала выполните проверку зависимостей.")
            return
        targets = [i.name for i in self._browser_info
                   if i.name in ("playwright", "camoufox") and not i.up_to_date]
        if not targets:
            QMessageBox.information(self, "Браузеры актуальны",
                                    "Все браузеры текущей конфигурации установлены и актуальны.")
            return
        if self._busy:
            ret = QMessageBox.warning(
                self, "Внимание",
                "Идёт обработка. Установка браузера во время работы приложения "
                "может повлиять на текущий процесс. Всё равно продолжить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return
        self._set_busy_ui(True)
        self.log_view.clear()
        self.log_view.setVisible(True)
        self._log_line(f"Обновление браузеров: {', '.join(targets)}")
        self._browser_worker = BrowserWorker(self._manager, [(n, self._env) for n in targets], self)
        self._browser_worker.progress.connect(self._on_apply_progress)
        self._browser_worker.log.connect(self._log_line)
        self._browser_worker.finished_ok.connect(self._on_browser_done)
        self._browser_worker.failed.connect(self._on_browser_failed)
        self._browser_worker.start()

    def _on_browser_done(self, messages):
        self._set_busy_ui(False)
        text = "\n".join(f"• {m}" for _, m in messages)
        self._log_line(text)
        self._start_browser_check()
        QMessageBox.information(self, "Готово", text)

    def _on_browser_failed(self, message):
        self._set_busy_ui(False)
        self.status_label.setText("Ошибка установки браузера")
        self._log_line(f"ОШИБКА: {message}")
        QMessageBox.critical(self, "Ошибка", message)

    # ── rendering ────────────────────────────────────────────────
    def _render(self):
        self.table.setRowCount(len(self._deps))
        for row, dep in enumerate(self._deps):
            self._render_row(row, dep)

    def _render_row(self, row: int, dep: Dependency):
        # checkbox
        chk = QTableWidgetItem()
        if dep.status in (Status.UPDATE, Status.DOWNGRADE, Status.MISSING):
            chk.setCheckState(Qt.Checked)
        else:
            chk.setCheckState(Qt.Unchecked)
        self.table.setItem(row, COL_CHECK, chk)

        name_item = QTableWidgetItem(dep.name)
        if dep.source_file:
            name_item.setToolTip(f"{dep.source_file}: {dep.display_manifest}")
        name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, COL_NAME, name_item)

        mgr_item = QTableWidgetItem(dep.manager.value)
        mgr_item.setFlags(mgr_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, COL_MANAGER, mgr_item)

        cur_item = QTableWidgetItem(dep.installed or "—")
        cur_item.setFlags(cur_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, COL_CURRENT, cur_item)

        lat_item = QTableWidgetItem(dep.latest or "—")
        lat_item.setFlags(lat_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(row, COL_LATEST, lat_item)

        # version — editable cell value (double-click / F2), no cell widget
        default = dep.latest or dep.installed or (dep.available[0] if dep.available else "")
        ver_item = QTableWidgetItem(default)
        if dep.available:
            ver_item.setToolTip("Доступные версии:\n" + "\n".join(dep.available[:15]))
        self.table.setItem(row, COL_VERSION, ver_item)

        status_item = QTableWidgetItem(_STATUS_TEXT.get(dep.status, "?"))
        status_item.setForeground(QColor(_STATUS_COLOR.get(dep.status, "#888888")))
        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
        if dep.error:
            status_item.setToolTip(dep.error)
        self.table.setItem(row, COL_STATUS, status_item)

        # row coloring
        color = _STATUS_COLOR.get(dep.status)
        if dep.status == Status.UPTODATE:
            color = "#2a2f35"
        elif dep.status == Status.UPDATE:
            color = "#3d3326"
        for c in (COL_NAME, COL_MANAGER, COL_CURRENT, COL_LATEST, COL_VERSION):
            item = self.table.item(row, c)
            if item is not None:
                item.setBackground(QColor(color))

    def _on_item_changed(self, item):
        if item.column() == COL_VERSION:
            self._on_version_edited(item.row())

    def _on_version_edited(self, row: int):
        item = self.table.item(row, COL_VERSION)
        if item is not None and row < len(self._deps):
            self._deps[row].selected = item.text().strip() or None

    # ── apply ────────────────────────────────────────────────────
    def _collect_changes(self) -> list[ApplyChange]:
        changes = []
        for row, dep in enumerate(self._deps):
            chk = self.table.item(row, COL_CHECK)
            if chk is None or chk.checkState() != Qt.Checked:
                continue
            item = self.table.item(row, COL_VERSION)
            target = item.text().strip() if item else ""
            if not target:
                continue
            if dep.installed and target == dep.installed:
                continue
            changes.append(ApplyChange(dependency=dep, target_version=target))
        return changes

    def _on_apply(self):
        changes = self._collect_changes()
        if not changes:
            QMessageBox.information(self, "Ничего не выбрано",
                                    "Отметьте строки и выберите версии для обновления.")
            return
        if self._busy:
            ret = QMessageBox.warning(
                self, "Внимание",
                "Идёт обработка. Обновление зависимостей во время работы приложения "
                "может нарушить текущий процесс. Всё равно продолжить?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return

        summary = "\n".join(f"  • {c.dependency.name}: {c.dependency.installed or '—'} → {c.target_version}"
                            for c in changes)
        ret = QMessageBox.question(
            self, "Подтверждение",
            f"Будет обновлено:\n{summary}\n\nТребуется доступ в интернет. Продолжить?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ret != QMessageBox.Yes:
            return

        self._set_busy_ui(True)
        self.apply_btn.setEnabled(False)
        self.log_view.clear()
        self.log_view.setVisible(True)
        self._log_line(f"Применяю изменения в {self._env.name}...")
        self._apply_worker = ApplyWorker(
            self._manager, changes, self._env,
            install_browser=self.install_browser_cb.isChecked(), parent=self,
        )
        self._apply_worker.progress.connect(self._on_apply_progress)
        self._apply_worker.log.connect(self._log_line)
        self._apply_worker.finished_ok.connect(self._on_apply_done)
        self._apply_worker.failed.connect(self._on_apply_failed)
        self._apply_worker.start()

    def _on_apply_progress(self, done, total, message):
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(done)
        self.status_label.setText(message)
        self._log_line(message)

    def _on_apply_done(self, result):
        self._backup_path = result.get("backup")
        self.rollback_btn.setEnabled(self._backup_path is not None)
        self._set_busy_ui(False)
        self.status_label.setText(result.get("summary", "Готово"))
        self._log_line(result.get("summary", "Готово"))
        QMessageBox.information(self, "Готово", result.get("summary", "Обновление завершено."))
        self._start_check()

    def _on_apply_failed(self, message):
        self._set_busy_ui(False)
        self.apply_btn.setEnabled(True)
        self.status_label.setText("Ошибка применения")
        self._log_line(f"ОШИБКА: {message}")
        QMessageBox.critical(self, "Ошибка", message)

    def _on_rollback(self):
        if self._backup_path is None:
            return
        ret = QMessageBox.question(
            self, "Откат", "Вернуть requirements.txt из последнего бэкапа?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        try:
            self._manager.rollback(self._backup_path)
            self._backup_path = None
            self.rollback_btn.setEnabled(False)
            self.status_label.setText("Откат выполнен")
            self._log_line("requirements.txt восстановлен из бэкапа.")
            self._start_check()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", str(e))

    def _log_line(self, message: str):
        self.log_view.appendPlainText(message)

    def closeEvent(self, event):
        if self._apply_worker is not None and self._apply_worker.isRunning():
            ret = QMessageBox.question(
                self, "Выполняется обновление",
                "Идёт установка зависимостей. Прервать и закрыть окно?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self._apply_worker.wait(5000)
        if self._browser_worker is not None and self._browser_worker.isRunning():
            ret = QMessageBox.question(
                self, "Выполняется установка браузера",
                "Идёт установка браузера. Прервать и закрыть окно?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            self._browser_worker.wait(5000)
        if self._check_worker is not None and self._check_worker.isRunning():
            self._check_worker.wait(5000)
        self._manager.close()
        super().closeEvent(event)
