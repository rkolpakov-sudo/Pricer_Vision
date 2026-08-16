"""Real-time agent monitoring panel.

Получает события из MCPAgentRunner.monitor_signal и отображает
текущее действие, прогресс по строкам и историю действий.
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QListWidget, QListWidgetItem, QPushButton,
)

from gui.spinner_widget import SpinnerWidget


class AgentMonitorPanel(QWidget):
    """Панель real-time мониторинга агента."""

    MAX_HISTORY = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Мониторинг агента")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)
        self._spinner = SpinnerWidget(size=14, spacing=0.5)
        self._spinner.setFixedSize(14, 14)
        self._spinner.hide()
        header.addWidget(self._spinner)
        layout.addLayout(header)

        self.row_label = QLabel("Строка: — / —")
        self.row_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        layout.addWidget(self.row_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.action_label = QLabel("Ожидание запуска...")
        self.action_label.setWordWrap(True)
        self.action_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #89b4fa;")
        layout.addWidget(self.action_label)

        history_row = QHBoxLayout()
        history_row.addWidget(QLabel("История действий"))
        history_row.addStretch(1)
        clear_btn = QPushButton("Очистить")
        clear_btn.setObjectName("ghost")
        clear_btn.clicked.connect(self.clear_history)
        history_row.addWidget(clear_btn)
        layout.addLayout(history_row)

        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(180)
        layout.addWidget(self.history_list, 1)

    def reset(self):
        """Сброс панели перед новым прогоном."""
        self.row_label.setText("Строка: — / —")
        self.progress_bar.setValue(0)
        self.action_label.setText("Запуск...")
        self.history_list.clear()
        self._spinner.hide()

    def clear_history(self):
        self.history_list.clear()

    def handle_event(self, event: dict):
        """Обрабатывает событие монитора из MCPAgentRunner."""
        etype = event.get("type")
        if etype == "start":
            total = event.get("total", 0)
            self.reset()
            self.progress_bar.setRange(0, max(total, 1))
            self.progress_bar.setValue(0)
            self.row_label.setText(f"Строка: 0 / {total}")
            self.action_label.setText(f"Начат прогон — {total} позиций")
            self._spinner.show()
        elif etype == "row":
            idx = event.get("idx", 0)
            total = event.get("total", 0)
            preview = event.get("preview", "")
            self.progress_bar.setRange(0, max(total, 1))
            self.progress_bar.setValue(idx - 1)
            self.row_label.setText(f"Строка: {idx} / {total}")
            self.action_label.setText(f"Обработка: {preview}")
            self._add_history(f"▶ Строка {idx}/{total}: {preview}")
        elif etype == "action":
            text = event.get("text", "")
            idx = event.get("idx")
            total = event.get("total", 0)
            if text:
                self.action_label.setText(text)
                self._add_history(text)
            if idx is not None:
                self.row_label.setText(f"Строка: {idx} / {total}")
        elif etype == "row_done":
            idx = event.get("idx", 0)
            total = event.get("total", 0)
            self.progress_bar.setValue(idx)
        elif etype == "done":
            self._spinner.hide()
            found = event.get("found", 0)
            errors = event.get("errors", 0)
            total = event.get("total", 0)
            self.progress_bar.setValue(total)
            self.action_label.setText(f"Готово: {found}/{total} найдено, {errors} ошибок")
            self._add_history(f"✓ Прогон завершён: {found}/{total} найдено")
        elif etype == "stop":
            self._spinner.hide()
            self.action_label.setText("Остановлен пользователем")
            self._add_history("⏹ Остановлен пользователем")

    def _add_history(self, text: str):
        ts = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{ts}] {text}")
        item.setToolTip(text)
        self.history_list.addItem(item)
        while self.history_list.count() > self.MAX_HISTORY:
            self.history_list.takeItem(0)
        self.history_list.scrollToBottom()
