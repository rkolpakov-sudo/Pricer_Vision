"""Real-time agent monitoring panel.

Получает события из MCPAgentRunner.monitor_signal и отображает
текущее действие, прогресс по строкам и историю действий.
"""

import time
from datetime import datetime

from PySide6.QtCore import QTimer
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
        self._row_start_time: float | None = None
        self._position_text: str = ""
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

        self.position_label = QLabel("Позиция: —")
        self.position_label.setWordWrap(True)
        self.position_label.setMaximumHeight(60)
        self.position_label.setStyleSheet("font-size: 12px; font-weight: 600; color: #a6e3a1;")
        layout.addWidget(self.position_label)

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

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_timer)

    def _tick_timer(self):
        if self._row_start_time is None:
            return
        elapsed = int(time.time() - self._row_start_time)
        if elapsed < 60:
            t_str = f"{elapsed}с"
        else:
            t_str = f"{elapsed // 60}м{elapsed % 60:02d}с"
        self.position_label.setText(f"Позиция: {self._position_text}  [{t_str}]")

    def reset(self):
        """Сброс панели перед новым прогоном."""
        self._stop_timer()
        self.row_label.setText("Строка: — / —")
        self.position_label.setText("Позиция: —")
        self.progress_bar.setValue(0)
        self.action_label.setText("Запуск...")
        self.history_list.clear()
        self._spinner.hide()

    def _stop_timer(self):
        self._timer.stop()
        self._row_start_time = None

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
            position = event.get("position") or preview
            self.progress_bar.setRange(0, max(total, 1))
            self.progress_bar.setValue(idx - 1)
            self.row_label.setText(f"Строка: {idx} / {total}")
            self.position_label.setText(f"Позиция {idx}/{total}: {position}")
            self.action_label.setText(f"Обработка: {preview}")
            self._add_history(f"▶ Строка {idx}/{total}: {preview}")
            self._row_start_time = time.time()
            self._position_text = f"{idx}/{total}: {position}"
            if not self._timer.isActive():
                self._timer.start()
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
            self._stop_timer()
        elif etype == "done":
            self._stop_timer()
            self._spinner.hide()
            self.position_label.setText("Позиция: —")
            found = event.get("found", 0)
            errors = event.get("errors", 0)
            total = event.get("total", 0)
            self.progress_bar.setValue(total)
            self.action_label.setText(f"Готово: {found}/{total} найдено, {errors} ошибок")
            self._add_history(f"✓ Прогон завершён: {found}/{total} найдено")
        elif etype == "stop":
            self._stop_timer()
            self._spinner.hide()
            self.position_label.setText("Позиция: —")
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
