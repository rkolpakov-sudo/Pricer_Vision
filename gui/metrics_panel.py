"""Панель метрик текущего/последнего прогона.

Получает stats-dict из MCPAgentRunner.metrics_signal.
Форматирование вынесено в чистую функцию format_metric_value (тестируется без Qt).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QGroupBox, QVBoxLayout, QLabel,
)

METRIC_DEFS = [
    ("total_products", "Всего товаров"),
    ("processed", "Обработано"),
    ("found", "Найдено"),
    ("success_rate", "Успешность"),
    ("llm_calls", "Запросов к LLM"),
    ("avg_llm_time", "Ср. время LLM"),
    ("prompt_tokens", "Токены LLM (исх.)"),
    ("completion_tokens", "Токены LLM (вх.)"),
    ("cache_hits", "Попаданий в кэш"),
    ("stuck_events", "Застреваний"),
    ("blocks", "Блокировок"),
]

DEFAULT_STATS = {key: None for key, _ in METRIC_DEFS}


def format_metric_value(key: str, value) -> str:
    """Форматирует значение метрики для отображения (чистая функция)."""
    if value is None:
        return "—"
    if key == "success_rate":
        return f"{value:.0%}"
    if key == "avg_llm_time":
        return f"{value:.1f}s"
    if key in ("prompt_tokens", "completion_tokens"):
        return f"{int(value):,}".replace(",", " ")
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    return str(value)


class MetricsPanel(QWidget):
    """Панель метрик прогона."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        self._boxes = {}
        for i, (key, label) in enumerate(METRIC_DEFS):
            row, col = divmod(i, 3)
            group = QGroupBox(label)
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(8, 14, 8, 8)
            value_label = QLabel("—")
            value_label.setStyleSheet("font-size: 22px; font-weight: 700;")
            value_label.setAlignment(Qt.AlignCenter)
            group_layout.addWidget(value_label)
            self._boxes[key] = value_label
            layout.addWidget(group, row, col)

        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(2, 1)

    def reset(self):
        for label in self._boxes.values():
            label.setText("—")

    def update_metrics(self, stats: dict):
        for key, label in self._boxes.items():
            if key in stats:
                label.setText(format_metric_value(key, stats[key]))
