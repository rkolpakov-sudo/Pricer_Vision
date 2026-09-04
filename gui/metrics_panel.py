"""Панель метрик текущего/последнего прогона.

Получает stats-dict из MCPAgentRunner.metrics_signal.
Форматирование вынесено в чистую функцию format_metric_value (тестируется без Qt).
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QLabel, QFrame,
    QSizePolicy,
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

# Высота одной плитки дашборда (px) — фиксированная, чтобы сетка не растягивалась.
TILE_HEIGHT = 56
# Колонок в сетке плиток.
GRID_COLUMNS = 3


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


def _make_tile(label: str, value_label: QLabel) -> QFrame:
    """Создаёт плитку-карточку: подпись сверху, значение крупно."""
    tile = QFrame()
    tile.setObjectName("metric-tile")
    tile.setFixedHeight(TILE_HEIGHT)
    tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    v = QVBoxLayout(tile)
    v.setContentsMargins(6, 5, 6, 4)
    v.setSpacing(0)
    cap = QLabel(label)
    cap.setObjectName("metric-tile-label")
    cap.setAlignment(Qt.AlignCenter)
    v.addWidget(cap)
    value_label.setObjectName("metric-tile-value")
    value_label.setAlignment(Qt.AlignCenter)
    value_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    v.addWidget(value_label, 1)
    return tile


class MetricsPanel(QWidget):
    """Панель метрик прогона — сетка фиксированных плиток-карточек."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        self._boxes = {}
        self._rows = (len(METRIC_DEFS) + GRID_COLUMNS - 1) // GRID_COLUMNS
        for i, (key, label) in enumerate(METRIC_DEFS):
            row, col = divmod(i, GRID_COLUMNS)
            value_label = QLabel("—")
            self._boxes[key] = value_label
            layout.addWidget(_make_tile(label, value_label), row, col)

        for c in range(GRID_COLUMNS):
            layout.setColumnStretch(c, 1)
        # Запрещаем вертикальное растяжение — высота по числу рядов плиток.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def _height_hint(self) -> int:
        return self._rows * TILE_HEIGHT + (self._rows - 1) * 8 + 4

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self._height_hint())

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self._height_hint())

    def reset(self):
        for label in self._boxes.values():
            label.setText("—")

    def update_metrics(self, stats: dict):
        for key, label in self._boxes.items():
            if key in stats:
                label.setText(format_metric_value(key, stats[key]))
