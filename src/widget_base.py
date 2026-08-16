"""
StyledWidget — базовый класс с тенью и градиентным фоном.
CardWidget — карточка с заголовком, иконкой, разделителем и content_layout.

Вспомогательные функции paint_styled_background / setup_shadow
для использования в QDialog и других QWidget-классах.
"""
from PySide6.QtWidgets import QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QLinearGradient, QColor, QPen

from src.theme import TOKENS, Theme


# ── Общие функции рисования ────────────────────────────────────────────────

def paint_styled_background(widget, painter, tokens):
    """Рисует градиент bg-surface → bg-primary + rounded rect 8px + border."""
    painter.setRenderHint(QPainter.Antialiasing)
    r = widget.rect().adjusted(0, 0, -1, -1)
    gradient = QLinearGradient(0, 0, 0, widget.height())
    gradient.setColorAt(0.0, QColor(tokens["bg-surface"]))
    gradient.setColorAt(1.0, QColor(tokens["bg-primary"]))
    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(r, 8, 8)
    painter.setPen(QPen(QColor(tokens["border"]), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(r, 8, 8)


def setup_shadow(widget, tokens):
    """Добавляет QGraphicsDropShadowEffect blur=14 offset=0,2."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(14)
    shadow.setOffset(0, 2)
    shadow.setColor(QColor(tokens["shadow"]))
    widget.setGraphicsEffect(shadow)


def sep_stylesheet(tokens) -> str:
    c = tokens['border']
    return f"QFrame#info-sep {{ color: {c}; background-color: {c}; border: none; max-height: 1px; }}"


# ── StyledWidget ───────────────────────────────────────────────────────────

class StyledWidget(QWidget):
    def __init__(self, parent=None, theme_name=Theme.DARK):
        super().__init__(parent)
        self._tokens = TOKENS.get(theme_name, TOKENS[Theme.DARK])
        setup_shadow(self, self._tokens)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_styled_background(self, painter, self._tokens)

    def refresh_theme(self, tokens=None, theme_name=None):
        if theme_name:
            self._tokens = TOKENS.get(theme_name, TOKENS[Theme.DARK])
        elif tokens:
            self._tokens = tokens
        setup_shadow(self, self._tokens)
        self.update()


# ── CardWidget ─────────────────────────────────────────────────────────────

class CardWidget(StyledWidget):
    """Карточка с заголовком, иконкой, разделителем и content_layout."""

    def __init__(self, title: str, icon: str = "📊", parent=None, theme_name=Theme.DARK):
        super().__init__(parent, theme_name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(3)

        header = QHBoxLayout()
        self._icon = QLabel(icon)
        self._icon.setFixedWidth(18)
        header.addWidget(self._icon)
        self._title = QLabel(title)
        self._title.setObjectName("card-title")
        header.addWidget(self._title)
        header.addStretch()
        layout.addLayout(header)

        self._sep = QFrame()
        self._sep.setObjectName("info-sep")
        self._sep.setFrameShape(QFrame.HLine)
        self._sep.setMaximumHeight(1)
        layout.addWidget(self._sep)

        self.content_layout = QVBoxLayout()
        self.content_layout.setContentsMargins(0, 4, 0, 0)
        self.content_layout.setSpacing(6)
        layout.addLayout(self.content_layout)

        self._apply_theme()

    def _apply_theme(self):
        self._title.setStyleSheet(
            f"color: {self._tokens['accent']}; font-size: 13px; font-weight: 700; background: transparent;"
        )
        self._sep.setStyleSheet(sep_stylesheet(self._tokens))
