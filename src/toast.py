"""
Toast notification system for Pricer.

Non-blocking, auto-dismiss notifications styled after modern tools.
Position: bottom-right corner above status bar.
Multiple toasts stack vertically with offset.
"""

from PySide6.QtWidgets import QWidget, QLabel, QHBoxLayout, QGraphicsOpacityEffect
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint

from src.theme import TOKENS, Theme


TOAST_STYLES = {
    "info":    {"bg": TOKENS[Theme.DARK]["accent"],      "icon": "ℹ", "text": "#1e1e2e"},
    "success": {"bg": TOKENS[Theme.DARK]["success"],     "icon": "✓", "text": "#1e1e2e"},
    "warning": {"bg": TOKENS[Theme.DARK]["warning"],     "icon": "⚠", "text": "#1e1e2e"},
    "error":   {"bg": TOKENS[Theme.DARK]["danger"],      "icon": "✕", "text": "#1e1e2e"},
}


class Toast(QWidget):
    BASE_Y = 54
    STACK_GAP = 46
    FADE_IN_MS = 250
    FADE_OUT_MS = 350

    def __init__(self, parent, message: str, toast_type: str = "info", duration_ms: int = 4000):
        super().__init__(parent)
        self._duration = duration_ms
        style = TOAST_STYLES.get(toast_type, TOAST_STYLES["info"])
        tokens = TOKENS[Theme.DARK]

        self.setFixedHeight(38)
        self.setMinimumWidth(200)
        self.setMaximumWidth(420)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        icon = QLabel(style["icon"])
        icon.setStyleSheet(f"font-size: 14px; color: {style['text']}; background: transparent;")
        icon.setFixedWidth(16)
        layout.addWidget(icon)

        msg = QLabel(message)
        msg.setStyleSheet(f"color: {style['text']}; font-size: 12px; background: transparent;")
        msg.setWordWrap(True)
        layout.addWidget(msg, 1)

        self.setStyleSheet(f"""
            Toast {{
                background-color: {style['bg']};
                border: 1px solid {tokens['border-light']};
                border-radius: 8px;
            }}
        """)

        self.opacity = QGraphicsOpacityEffect(self)
        self.opacity.setOpacity(0.0)
        self.setGraphicsEffect(self.opacity)

    def reposition(self, index: int):
        parent = self.parent()
        if not parent:
            return
        self.adjustSize()
        pw, ph = parent.width(), parent.height()
        w = self.width()
        x = pw - w - 16
        y = ph - self.BASE_Y - index * self.STACK_GAP
        self.move(QPoint(max(8, x), max(8, y)))

    def animate_in(self, index: int):
        self.reposition(index)
        self.show()
        self.raise_()
        anim = QPropertyAnimation(self.opacity, b"opacity")
        anim.setDuration(self.FADE_IN_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()

        QTimer.singleShot(self._duration, self.fade_out)

    def fade_out(self):
        anim = QPropertyAnimation(self.opacity, b"opacity")
        anim.setDuration(self.FADE_OUT_MS)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InCubic)
        anim.finished.connect(self._on_faded_out)
        anim.start()

    def _on_faded_out(self):
        self.deleteLater()


class ToastManager:
    def __init__(self, parent_widget):
        self._parent = parent_widget
        self._toasts: list[Toast] = []

    def show(self, message: str, toast_type: str = "info", duration_ms: int = 4000):
        toast = Toast(self._parent, message, toast_type, duration_ms)
        self._toasts.append(toast)
        self._reposition_all()
        toast.animate_in(len(self._toasts) - 1)
        QTimer.singleShot(duration_ms + 500, lambda: self._remove(toast))

    def info(self, message: str):
        self.show(message, "info", 3500)

    def success(self, message: str):
        self.show(message, "success", 3500)

    def warning(self, message: str):
        self.show(message, "warning", 4500)

    def error(self, message: str):
        self.show(message, "error", 5500)

    def _remove(self, toast: Toast):
        if toast in self._toasts:
            self._toasts.remove(toast)
            self._reposition_all()

    def _reposition_all(self):
        for i, t in enumerate(self._toasts):
            if t.isVisible():
                t.reposition(i)
