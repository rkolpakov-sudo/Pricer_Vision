import random
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QRadialGradient, QColor, QBrush
from PySide6.QtWidgets import QWidget


class SpinnerWidget(QWidget):
    def __init__(self, parent=None, size=20, color="#89b4fa", spacing=0.6):
        super().__init__(parent)
        self._color = QColor(color)
        self._state = [False] * 9
        self._spacing = spacing
        self.setFixedSize(size, size)

    def tick(self):
        for i in range(9):
            self._state[i] = random.random() < 0.4
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        cell_w = w / 3.0
        cell_h = h / 3.0
        dot_r = min(cell_w, cell_h) * 0.28
        glow_r = dot_r * 3.5
        grid_w = w * self._spacing
        grid_h = h * self._spacing
        off_x = (w - grid_w) / 2.0
        off_y = (h - grid_h) / 2.0
        step_x = grid_w / 2.0
        step_y = grid_h / 2.0
        for idx in range(9):
            row = idx // 3
            col = idx % 3
            cx = off_x + col * step_x
            cy = off_y + row * step_y
            if self._state[idx]:
                grad = QRadialGradient(cx, cy, glow_r)
                grad.setColorAt(0.0, QColor(self._color.red(), self._color.green(), self._color.blue(), 200))
                grad.setColorAt(0.3, QColor(self._color.red(), self._color.green(), self._color.blue(), 100))
                grad.setColorAt(0.6, QColor(self._color.red(), self._color.green(), self._color.blue(), 30))
                grad.setColorAt(1.0, QColor(self._color.red(), self._color.green(), self._color.blue(), 0))
                p.setBrush(QBrush(grad))
                p.setPen(Qt.NoPen)
                p.drawEllipse(int(cx - glow_r), int(cy - glow_r), int(glow_r * 2), int(glow_r * 2))
                p.setBrush(self._color)
                p.drawEllipse(int(cx - dot_r), int(cy - dot_r), int(dot_r * 2), int(dot_r * 2))
            else:
                p.setBrush(QColor(self._color.red(), self._color.green(), self._color.blue(), 25))
                p.setPen(Qt.NoPen)
                p.drawEllipse(int(cx - dot_r), int(cy - dot_r), int(dot_r * 2), int(dot_r * 2))
        p.end()
