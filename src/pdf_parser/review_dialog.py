import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                                QTableWidgetItem, QPushButton, QLabel,
                                QHeaderView, QFileDialog, QMessageBox)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from src.theme import TOKENS, Theme
from src.pdf_parser.review import SmartReview

logger = logging.getLogger("pricer.pdf.review")


COLUMNS = [
    ("#", 50),
    ("Наименование", 0),
    ("Характеристики", 0),
    ("Артикул", 200),
    ("Производитель", 180),
    ("Кол-во", 80),
    ("Ед.", 60),
    ("Уверенность", 90),
]
NAME_COL, SPECS_COL, CODE_COL, MFG_COL, QTY_COL, UNIT_COL, CONFIDENCE_COL = 1, 2, 3, 4, 5, 6, 7
COL_COUNT = len(COLUMNS)


class ReviewDialog(QDialog):
    def __init__(self, items: list[dict], parent=None, theme_name: str = Theme.DARK):
        super().__init__(parent)
        self.setWindowTitle("Проверка и подтверждение спецификации")
        self.resize(960, 600)
        self._items = items
        self._confirmed = False
        self._theme_name = theme_name
        self._tokens = TOKENS.get(theme_name, TOKENS[Theme.DARK])
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        auto = sum(1 for it in self._items if (it.get("confidence") or 0) >= SmartReview.CONFIDENCE_THRESHOLD)
        needs = len(self._items) - auto
        header = QLabel(
            f"Найдено {len(self._items)} позиций. "
            f"Авто-подтверждено: {auto}, требует проверки: {needs}. "
            "Отредактируйте при необходимости и подтвердите."
        )
        layout.addWidget(header)

        self.table = QTableWidget(len(self._items), COL_COUNT)
        self.table.setHorizontalHeaderLabels([c[0] for c in COLUMNS])
        for col, (_, width) in enumerate(COLUMNS):
            h = self.table.horizontalHeader()
            if width:
                h.resizeSection(col, width)
            else:
                h.setSectionResizeMode(col, QHeaderView.Stretch)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setAlternatingRowColors(True)

        corrected_bg = QColor("#3a3a2a") if self._theme_name == Theme.DARK else QColor("#fff9e0")
        low_conf_bg = QColor("#3a2a1a") if self._theme_name == Theme.DARK else QColor("#ffe9c2")

        for row, item in enumerate(self._items):
            pos_item = QTableWidgetItem(str(item.get("pos", row + 1)))
            pos_item.setFlags(pos_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, pos_item)

            self.table.setItem(row, NAME_COL, QTableWidgetItem(item.get("name", "")))
            self.table.setItem(row, SPECS_COL, QTableWidgetItem(item.get("specs", "")))
            self.table.setItem(row, CODE_COL, QTableWidgetItem(item.get("code", "")))
            self.table.setItem(row, MFG_COL, QTableWidgetItem(item.get("manufacturer", "")))
            self.table.setItem(row, QTY_COL, QTableWidgetItem(str(item.get("qty", ""))))
            self.table.setItem(row, UNIT_COL, QTableWidgetItem(item.get("unit", "шт")))

            confidence = float(item.get("confidence", 1.0) or 0)
            conf_item = QTableWidgetItem(f"{int(confidence * 100)}%")
            conf_item.setFlags(conf_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, CONFIDENCE_COL, conf_item)

            if item.get("_corrected"):
                for c in range(COL_COUNT):
                    cell = self.table.item(row, c)
                    if cell:
                        cell.setBackground(corrected_bg)
            elif confidence < SmartReview.CONFIDENCE_THRESHOLD:
                for c in range(COL_COUNT):
                    cell = self.table.item(row, c)
                    if cell:
                        cell.setBackground(low_conf_bg)

        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        export_btn = QPushButton("📥 Экспорт в Excel")
        export_btn.clicked.connect(self._on_export)
        btn_layout.addWidget(export_btn)

        btn_layout.addStretch()

        confirm_btn = QPushButton("✅ Подтвердить")
        confirm_btn.setObjectName("success")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(confirm_btn)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def _snapshot(self) -> list[dict]:
        """Read current table state into a list of dicts (does not modify _items)."""
        snapshot = []
        for row in range(self.table.rowCount()):
            item = dict(self._items[row]) if row < len(self._items) else {}
            item["name"] = (self.table.item(row, NAME_COL).text().strip()
                            or item.get("name", ""))
            item["specs"] = self.table.item(row, SPECS_COL).text().strip()
            item["code"] = self.table.item(row, CODE_COL).text().strip()
            item["manufacturer"] = self.table.item(row, MFG_COL).text().strip()
            try:
                item["qty"] = float(self.table.item(row, QTY_COL).text().replace(",", "."))
            except (ValueError, AttributeError):
                pass
            item["unit"] = self.table.item(row, UNIT_COL).text().strip() or "шт"
            snapshot.append(item)
        return snapshot

    def _on_export(self):
        snapshot = self._snapshot()
        if not snapshot:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить спецификацию как",
            str(Path.home() / f"pdf_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"),
            "Excel (*.xlsx)")
        if not path:
            return
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "PDF спецификация"
            ws.append(["#", "Наименование", "Характеристики", "Артикул", "Производитель", "Кол-во", "Ед."])
            for item in snapshot:
                ws.append([
                    item.get("pos", ""),
                    item.get("name", ""),
                    item.get("specs", ""),
                    item.get("code", ""),
                    item.get("manufacturer", ""),
                    item.get("qty", ""),
                    item.get("unit", "шт"),
                ])
            wb.save(path)
            QMessageBox.information(self, "Экспорт", f"Сохранено: {path}")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить: {e}")

    def _on_confirm(self):
        for row in range(self.table.rowCount()):
            item = self._items[row]
            item["name"] = (self.table.item(row, NAME_COL).text().strip()
                            or item.get("name", ""))
            item["specs"] = self.table.item(row, SPECS_COL).text().strip()
            item["code"] = self.table.item(row, CODE_COL).text().strip()
            item["manufacturer"] = self.table.item(row, MFG_COL).text().strip()
            try:
                item["qty"] = float(self.table.item(row, QTY_COL).text().replace(",", "."))
            except (ValueError, AttributeError):
                pass
            item["unit"] = self.table.item(row, UNIT_COL).text().strip() or "шт"

        self._confirmed = True
        self.accept()

    @property
    def items(self) -> list[dict]:
        return self._items

    @property
    def is_confirmed(self) -> bool:
        return self._confirmed
