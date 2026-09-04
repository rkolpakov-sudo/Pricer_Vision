"""Диалог переклассификации строки результата.

Пользователь указывает ПРАВИЛЬНЫЙ тип товара для спецификации (когда агент
назначил неверный тип из-за пересекающихся обозначений, напр. КТР-20/КТР-25).
После подтверждения правило сохраняется в БД (product_type_overrides) и строка
перезапускается с нуля.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel,
    QFormLayout, QDialogButtonBox,
)


class ReclassifyDialog(QDialog):
    """Выбор правильного типа товара для строки."""

    def __init__(self, spec_text: str, current_type: str,
                 products: dict, parent=None):
        """
        products — dict {product_type_id: {name, category, keywords}} из графа.
        """
        super().__init__(parent)
        self.setWindowTitle("Исправить тип товара")
        self.setMinimumWidth(560)
        self._spec_text = spec_text
        self._products = products

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        header = QLabel("Строка спецификации")
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)

        spec_label = QLabel(spec_text)
        spec_label.setWordWrap(True)
        spec_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(spec_label)

        form = QFormLayout()
        current_ru = self._type_label(current_type)
        self.current_label = QLabel(current_ru or current_type or "не определён")
        self.current_label.setStyleSheet("font-weight: 600;")
        form.addRow("Текущий тип:", self.current_label)

        self.type_combo = QComboBox()
        self.type_combo.setEditable(True)
        self.type_combo.setInsertPolicy(QComboBox.NoInsert)
        self._items = []
        for pid in sorted(products.keys()):
            name = self._type_label(pid)
            display = f"{name}  ({pid})"
            self._items.append((pid, display))
            self.type_combo.addItem(display, pid)
        self.type_combo.setCurrentIndex(-1)
        self.type_combo.setPlaceholderText("Выберите правильный тип…")
        # Попробуем предзаполнить похожим (не текущим) типом — пусто.
        form.addRow("Правильный тип:", self.type_combo)
        layout.addLayout(form)

        note = QLabel(
            "После сохранения правило «спецификация → тип» запомнится в БД,\n"
            "память строки очистится и запустится повторный поиск с нуля.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = btns.button(QDialogButtonBox.Ok)
        ok_btn.setText("Сохранить и переискать")
        ok_btn.setObjectName("primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.type_combo.setFocus()

    def _type_label(self, pid: str) -> str:
        p = self._products.get(pid)
        if not p:
            return pid
        name = p.get("name") or pid
        return name

    def selected_type(self) -> str:
        return self.type_combo.currentData() or ""
