"""Диалог выбора сессии при запуске приложения.

QDialog — показывает список последних сессий с возможностью загрузки,
создания новой или удаления выбранной.
"""

import logging
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QMessageBox, QAbstractItemView,
)
from PySide6.QtCore import Qt

logger = logging.getLogger("pricer.gui.session")


class SessionDialog(QDialog):
    """Диалог выбора сессии для восстановления."""

    def __init__(self, sessions: list[dict], parent=None):
        """
        sessions — список из session_manager.list_sessions():
            [{path, saved_at, spec_name, total_rows, processed_count, found_count}]
        """
        super().__init__(parent)
        self.setWindowTitle("Выбор сессии")
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self.selected_session: str | None = None

        layout = QVBoxLayout(self)

        # Заголовок
        title = QLabel("Выберите сессию для восстановления:")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        # Список сессий
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setAlternatingRowColors(True)
        self._sessions = sessions

        for s in sessions:
            saved = s.get("saved_at", "")
            try:
                dt = datetime.fromisoformat(saved)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = saved[:16]
            total = s.get("total_rows", 0)
            processed = s.get("processed_count", 0)
            found = s.get("found_count", 0)
            spec = s.get("spec_name", "?")
            text = f"{spec}  —  {processed}/{total} позиций, {found} найдено  |  {date_str}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, s.get("path", ""))
            self._list.addItem(item)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

        self._list.itemDoubleClicked.connect(self._on_load)
        layout.addWidget(self._list)

        # Кнопки
        btn_layout = QHBoxLayout()

        self._btn_new = QPushButton("Новая сессия")
        self._btn_new.setToolTip("Начать с нуля — загрузить спецификацию заново")
        self._btn_new.clicked.connect(self._on_new)
        btn_layout.addWidget(self._btn_new)

        btn_layout.addStretch()

        self._btn_delete = QPushButton("Удалить")
        self._btn_delete.setToolTip("Удалить выбранную сессию с диска")
        self._btn_delete.clicked.connect(self._on_delete)
        btn_layout.addWidget(self._btn_delete)

        self._btn_load = QPushButton("Загрузить")
        self._btn_load.setDefault(True)
        self._btn_load.clicked.connect(self._on_load)
        btn_layout.addWidget(self._btn_load)

        layout.addLayout(btn_layout)

    def _on_load(self):
        item = self._list.currentItem()
        if not item:
            QMessageBox.information(self, "Сессия", "Выберите сессию из списка.")
            return
        self.selected_session = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _on_new(self):
        self.selected_session = None
        self.accept()

    def _on_delete(self):
        item = self._list.currentItem()
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        spec = item.text().split("  —  ")[0] if "  —  " in item.text() else "?"
        reply = QMessageBox.question(
            self, "Удаление сессии",
            f"Удалить сессию «{spec}»?\n\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from src.session_manager import delete_session
            if delete_session(path):
                row = self._list.row(item)
                self._list.takeItem(row)
                if self._list.count() == 0:
                    self._on_new()
