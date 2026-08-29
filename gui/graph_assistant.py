import re
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTextEdit, QLineEdit, QLabel, QStackedWidget,
    QTableWidget, QTableWidgetItem,
    QListWidget, QSplitter, QFrame, QFormLayout,
    QDoubleSpinBox, QComboBox, QMessageBox,
    QCheckBox, QAbstractItemView, QProgressBar, QApplication,
)
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from gui.spinner_widget import SpinnerWidget
from src.memory_manager import MemoryManager
from src.study_runner import StudyRunner

DB_PATH = "data/pricer.db"

PRIORITY_LABELS = {0: "primary", 1: "secondary", 2: "all"}


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}м {s:02d}с" if m else f"{s}с"


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    return iso[:10]


# ── Shared helpers ──

def _confirm(parent, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text,
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes


def _msg(parent, text: str):
    QMessageBox.information(parent, "Готово", text)


def _combo_value(combo) -> str:
    """Value of an editable QComboBox.

    In an editable combo `currentData()` keeps returning the previously selected
    item's data while the user is typing new text, so `data or text` silently uses
    the stale value. Only trust the item data when the current text matches the
    current item's display text; otherwise use what the user typed.
    """
    text = combo.currentText().strip()
    idx = combo.currentIndex()
    if idx >= 0 and combo.itemText(idx) == text:
        data = combo.itemData(idx)
        if data is not None and str(data):
            return str(data)
    return text


def _step_action(step) -> str:
    """Безопасно извлекает имя действия из шага подхода.

    Шаг может быть dict ({'action': ...}) или legacy-строкой
    (например 'browser_navigate'). Возвращает строку в любом случае.
    """
    if isinstance(step, dict):
        return str(step.get("action") or step.get("name") or "?")
    return str(step or "?")


# ═══════════════════════════════════════════════
# SearchPage — поиск подходов
# ═══════════════════════════════════════════════

class SearchPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._last_approaches = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Поиск подходов")
        title.setObjectName("section")
        layout.addWidget(title)

        form = QHBoxLayout()
        form.addWidget(QLabel("Тип товара:"))
        self.product_combo = QComboBox()
        self.product_combo.setEditable(True)
        self.product_combo.setMinimumWidth(200)
        form.addWidget(self.product_combo, 1)
        form.addWidget(QLabel("Сайт:"))
        self.site_combo = QComboBox()
        self.site_combo.setEditable(True)
        self.site_combo.setMinimumWidth(150)
        form.addWidget(self.site_combo, 1)
        self.search_btn = QPushButton("Поиск")
        self.search_btn.setObjectName("primary")
        self.search_btn.clicked.connect(self._search)
        form.addWidget(self.search_btn)
        layout.addLayout(form)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.cursorPositionChanged.connect(self._highlight_current_line)
        layout.addWidget(self.result_text, 1)

        btn_row = QHBoxLayout()
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._delete_approach)
        btn_row.addWidget(self.delete_btn)
        self.deprecate_btn = QPushButton("Депрекейтнуть")
        self.deprecate_btn.setObjectName("warning")
        self.deprecate_btn.setEnabled(False)
        self.deprecate_btn.clicked.connect(self._deprecate_approach)
        btn_row.addWidget(self.deprecate_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def refresh_combo(self, products: dict):
        current = self.product_combo.currentData()
        self.product_combo.clear()
        self.product_combo.addItem("(все)", "")
        for pid, pdata in sorted(products.items()):
            name = pdata.get("name", pid)
            self.product_combo.addItem(name, pid)
        if current:
            idx = self.product_combo.findData(current)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def sync_combo(self, pid: str):
        if pid:
            idx = self.product_combo.findData(pid)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def refresh_sites(self):
        all_sites = self._panel.mm.get_all_sites()
        self.site_combo.clear()
        self.site_combo.addItem("(все)", "")
        for sid, sdata in sorted(all_sites.items()):
            name = sdata.get("name", sid)
            self.site_combo.addItem(name, sid)

    def _highlight_current_line(self):
        cursor = self.result_text.textCursor()
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
        selections = []
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(self.result_text.palette().highlight())
        sel.cursor = cursor
        selections.append(sel)
        self.result_text.setExtraSelections(selections)

    def _search(self):
        mm = self._panel.mm
        pt = self._panel.resolve_pt(self.product_combo)
        site = _combo_value(self.site_combo)
        if not pt and not site:
            self.result_text.setText("Укажите тип товара или сайт")
            return
        approaches = mm.get_all_approaches(pt) if not site else mm.get_site_approaches(pt, site)
        if not approaches:
            self.result_text.setText(f"Нет подходов для «{pt or site}»")
            self._last_approaches = []
            self.delete_btn.setEnabled(False)
            self.deprecate_btn.setEnabled(False)
            return
        self._last_approaches = approaches
        lines = [f"Подходов: {len(approaches)}  (кликните ID для выбора, затем удалите/депрекейтните)"]
        for a in approaches[:20]:
            aid = a.get("id", "?")
            sid = a.get("site_id", "?")
            suc = a.get("success_count", 0)
            fail = a.get("consecutive_failures", 0)
            last = _fmt_date(a.get("last_success_date"))
            dep = " [DEPRECATED]" if a.get("is_deprecated") else ""
            pat = " → ".join(_step_action(s) for s in a.get("pattern", [])[:5])
            lines.append(f"\n[ID {aid}]{dep} {sid} (успехов: {suc}, неудач: {fail}, последний: {last})")
            lines.append(f"  шаги: {pat}")
        self.result_text.setText("\n".join(lines))
        self.delete_btn.setEnabled(True)
        self.deprecate_btn.setEnabled(True)

    def _get_selected_approach_id(self) -> int | None:
        text = self.result_text.toPlainText()
        cursor = self.result_text.textCursor()
        block = cursor.block().text().strip()
        m = re.match(r'\[ID (\d+)\]', block)
        if m:
            return int(m.group(1))
        return None

    def _delete_approach(self):
        aid = self._get_selected_approach_id()
        if aid is None:
            _msg(self, "Кликните на строку с ID подхода, затем нажмите Удалить")
            return
        if not _confirm(self, "Удаление", f"Удалить подход ID {aid}?"):
            return
        self._panel.mm.delete_approach(aid)
        self._search()

    def _deprecate_approach(self):
        aid = self._get_selected_approach_id()
        if aid is None:
            _msg(self, "Кликните на строку с ID подхода")
            return
        if not _confirm(self, "Депрекейт", f"Пометить подход ID {aid} как устаревший?"):
            return
        self._panel.mm.deprecate_approach(aid)
        self._search()


# ═══════════════════════════════════════════════
# ContextPage — просмотр контекста графа
# ═══════════════════════════════════════════════

class ContextPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Контекст графа")
        title.setObjectName("section")
        layout.addWidget(title)

        top = QHBoxLayout()
        top.addWidget(QLabel("Тип товара:"))
        self.product_combo = QComboBox()
        self.product_combo.setMinimumWidth(200)
        self.product_combo.currentIndexChanged.connect(self._load)
        top.addWidget(self.product_combo, 1)
        self.load_btn = QPushButton("Загрузить")
        self.load_btn.setObjectName("ghost")
        self.load_btn.clicked.connect(self._load)
        top.addWidget(self.load_btn)
        layout.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Тип товара", "Сайты", "Подходов", "Цен"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)

        self._load()

    def refresh_combo(self, products: dict):
        current = self.product_combo.currentData()
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        self.product_combo.addItem("(все)", "")
        for pid, pdata in sorted(products.items()):
            name = pdata.get("name", pid)
            self.product_combo.addItem(name, pid)
        if current:
            idx = self.product_combo.findData(current)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
        self.product_combo.blockSignals(False)
        self._load()

    def _load(self):
        engine = self._panel.engine
        filter_pid = self.product_combo.currentData() or ""
        try:
            categories = engine.get_cached_categories()
        except Exception:
            categories = {}
        self.table.setRowCount(0)
        for cat, data in sorted(categories.items()):
            if filter_pid and cat != filter_pid:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            pdata = engine.get_all_products().get(cat, {})
            name = pdata.get("name", cat)
            self.table.setItem(row, 0, QTableWidgetItem(name))
            sites = [s.get("id", "") for s in data.get("sites", [])]
            self.table.setItem(row, 1, QTableWidgetItem(", ".join(sites[:5])))
            self.table.setItem(row, 2, QTableWidgetItem(str(len(data.get("approaches", [])))))
            self.table.setItem(row, 3, QTableWidgetItem(str(len(data.get("prices", [])))))


# ═══════════════════════════════════════════════
# HintPage — управление подсказками
# ═══════════════════════════════════════════════

class HintPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Подсказки")
        title.setObjectName("section")
        layout.addWidget(title)

        gb = QFrame()
        gf = QFormLayout(gb)
        self.product_combo = QComboBox()
        self.product_combo.setEditable(True)
        self.product_combo.setMinimumWidth(200)
        self.product_combo.currentIndexChanged.connect(self._on_combo_changed)
        gf.addRow("Тип товара:", self.product_combo)
        self.hint_text = QLineEdit()
        self.hint_text.setPlaceholderText("напр. ИБП, источник бесперебойного питания")
        gf.addRow("Текст подсказки:", self.hint_text)
        self.hint_priority = QDoubleSpinBox()
        self.hint_priority.setRange(0.0, 1.0)
        self.hint_priority.setValue(0.5)
        self.hint_priority.setSingleStep(0.1)
        gf.addRow("Приоритет:", self.hint_priority)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("Добавить")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add)
        btn_row.addWidget(add_btn)
        show_all_btn = QPushButton("Показать все")
        show_all_btn.clicked.connect(self._show_all)
        btn_row.addWidget(show_all_btn)
        gf.addRow(btn_row)
        layout.addWidget(gb)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.cursorPositionChanged.connect(self._highlight_current_line)
        layout.addWidget(self.result_text, 1)

        btn_row2 = QHBoxLayout()
        self.delete_hint_btn = QPushButton("Удалить выбранную подсказку")
        self.delete_hint_btn.setObjectName("danger")
        self.delete_hint_btn.setEnabled(False)
        self.delete_hint_btn.clicked.connect(self._delete_hint)
        btn_row2.addWidget(self.delete_hint_btn)
        btn_row2.addStretch()
        layout.addLayout(btn_row2)

        self._all_hints = []

    def refresh_combo(self, products: dict):
        current = self.product_combo.currentData()
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        for pid, pdata in sorted(products.items()):
            name = pdata.get("name", pid)
            self.product_combo.addItem(name, pid)
        if current:
            idx = self.product_combo.findData(current)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
        self.product_combo.blockSignals(False)

    def sync_combo(self, pid: str):
        if pid:
            idx = self.product_combo.findData(pid)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def _on_combo_changed(self):
        self._show()

    def _add(self):
        mm = self._panel.mm
        pt = self._panel.resolve_pt(self.product_combo)
        text = self.hint_text.text().strip()
        priority = self.hint_priority.value()
        if not pt or not text:
            self.result_text.setText("Выберите тип товара и заполните текст подсказки")
            return
        mm.add_hint(pt, text, None, priority)
        self.hint_text.clear()
        self.result_text.setText(f"Подсказка добавлена для «{pt}»")

    def _show(self):
        mm = self._panel.mm
        pt = self._panel.resolve_pt(self.product_combo)
        if not pt:
            self.result_text.setText("Выберите тип товара")
            self._all_hints = []
            self.delete_hint_btn.setEnabled(False)
            return
        self._all_hints = mm.get_hints(pt)
        if not self._all_hints:
            self.result_text.setText(f"Нет подсказок для «{pt}»")
            self.delete_hint_btn.setEnabled(False)
            return
        lines = [f"Подсказок для «{pt}»: {len(self._all_hints)}  (кликните на ID для удаления)"]
        for h in self._all_hints:
            lines.append(f"\n[ID {h['id']}] (приоритет: {h.get('priority', 0.5):.1f})")
            lines.append(f"  {h.get('hint_text', '')}")
        self.result_text.setText("\n".join(lines))
        self.delete_hint_btn.setEnabled(True)

    def _show_all(self):
        mm = self._panel.mm
        all_hints = mm.get_all_hints()
        if not all_hints:
            self.result_text.setText("Нет подсказок в системе")
            self._all_hints = []
            self.delete_hint_btn.setEnabled(False)
            return
        self._all_hints = all_hints
        products = self._panel.engine.get_all_products()
        lines = [f"Все подсказки ({len(all_hints)}):"]
        for h in all_hints:
            pt = h.get("product_type_id", "?")
            pdata = products.get(pt, {})
            pname = pdata.get("name", pt)
            lines.append(f"\n[ID {h['id']}] {pname} (приор: {h.get('priority', 0.5):.1f})")
            lines.append(f"  {h.get('hint_text', '')}")
        self.result_text.setText("\n".join(lines))
        self.delete_hint_btn.setEnabled(True)

    def _highlight_current_line(self):
        cursor = self.result_text.textCursor()
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.movePosition(QTextCursor.EndOfLine, QTextCursor.KeepAnchor)
        selections = []
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(self.result_text.palette().highlight())
        sel.cursor = cursor
        selections.append(sel)
        self.result_text.setExtraSelections(selections)

    def _delete_hint(self):
        import re
        text = self.result_text.toPlainText()
        cursor = self.result_text.textCursor()
        block = cursor.block().text().strip()
        m = re.match(r'\[ID (\d+)\]', block)
        if not m:
            _msg(self, "Кликните на строку [ID ...] подсказки, затем нажмите Удалить")
            return
        hid = int(m.group(1))
        if not _confirm(self, "Удаление", f"Удалить подсказку ID {hid}?"):
            return
        self._panel.mm.delete_hint(hid)
        self._show_all()


# ═══════════════════════════════════════════════
# CorrectionPage — ручная коррекция цен
# ═══════════════════════════════════════════════

class CorrectionPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Коррекция цен")
        title.setObjectName("section")
        layout.addWidget(title)

        form = QFormLayout()
        self.product_combo = QComboBox()
        self.product_combo.setEditable(True)
        self.product_combo.setMinimumWidth(250)
        form.addRow("Тип товара:", self.product_combo)
        self.spec_input = QLineEdit()
        self.spec_input.setPlaceholderText("текст спецификации")
        form.addRow("Спецификация:", self.spec_input)
        self.site_combo = QComboBox()
        self.site_combo.setEditable(True)
        self.site_combo.setMinimumWidth(150)
        form.addRow("Сайт:", self.site_combo)
        self.price_input = QDoubleSpinBox()
        self.price_input.setRange(0, 99999999)
        self.price_input.setSuffix(" ₽")
        form.addRow("Цена:", self.price_input)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("URL страницы с ценой")
        form.addRow("URL:", self.url_input)
        self.confidence_input = QDoubleSpinBox()
        self.confidence_input.setRange(0.6, 1.0)
        self.confidence_input.setValue(0.95)
        self.confidence_input.setSingleStep(0.05)
        form.addRow("Уверенность:", self.confidence_input)
        self.save_btn = QPushButton("Сохранить коррекцию")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        form.addRow("", self.save_btn)
        layout.addLayout(form)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    def refresh_combo(self, products: dict):
        current = self.product_combo.currentData()
        self.product_combo.clear()
        for pid, pdata in sorted(products.items()):
            name = pdata.get("name", pid)
            self.product_combo.addItem(name, pid)
        if current:
            idx = self.product_combo.findData(current)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def sync_combo(self, pid: str):
        if pid:
            idx = self.product_combo.findData(pid)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def refresh_sites(self):
        all_sites = self._panel.mm.get_all_sites()
        self.site_combo.clear()
        for sid, sdata in sorted(all_sites.items()):
            name = sdata.get("name", sid)
            self.site_combo.addItem(name, sid)
        # Поле сайта при добавлении цены должно быть пустым, а не предвыбранным.
        self.site_combo.setCurrentIndex(-1)
        self.site_combo.clearEditText()

    def _save(self):
        mm = self._panel.mm
        pt = self._panel.resolve_pt(self.product_combo)
        spec = self.spec_input.text().strip()
        site = _combo_value(self.site_combo)
        price = self.price_input.value()
        url = self.url_input.text().strip()
        conf = self.confidence_input.value()
        if not pt or not spec or not site or price <= 0:
            self.status_label.setText("Заполните тип товара, спецификацию, сайт и цену")
            return
        pid = mm.save_price(
            spec_text=spec, product_type=pt, site=site,
            price=price, url=url, confidence=conf,
            reason="manual correction",
        )
        if pid:
            self.status_label.setText(f"Цена сохранена (ID: {pid})")
        else:
            self.status_label.setText("Не сохранено: confidence ниже 0.6 или цена уже существует")


# ═══════════════════════════════════════════════
# StatsPage — статистика
# ═══════════════════════════════════════════════

class StatsPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Статистика")
        title.setObjectName("section")
        layout.addWidget(title)

        self.refresh_btn = QPushButton("Обновить статистику")
        self.refresh_btn.clicked.connect(self._refresh)
        layout.addWidget(self.refresh_btn)

        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        layout.addWidget(self.stats_text, 1)

        self._refresh()

    def _refresh(self):
        engine = self._panel.engine
        if engine is None:
            self.stats_text.setText("Загрузка... Дождитесь инициализации графа.")
            return
        try:
            stats = engine.get_stats()
            hints = engine.get_all_hints()
            lines = [
                f"Категорий: {stats.get('product_types', 0)}",
                f"Сайтов: {stats.get('sites', 0)}",
                f"Подходов: {stats.get('approaches', 0)}",
                f"Подтверждённых цен: {stats.get('confirmed_prices', 0)}",
                f"Подсказок: {len(hints)}",
            ]
            approaches = engine.get_recent_approaches(5)
            if approaches:
                lines.append("\nПоследние подходы:")
                for a in approaches:
                    lines.append(f"  • {a.get('site_id', '?')} — успехов: {a.get('success_count', 0)}")
            self.stats_text.setText("\n".join(lines))
        except Exception as e:
            self.stats_text.setText(f"Ошибка: {e}")


# ═══════════════════════════════════════════════
# ProductTypePage — CRUD типов товаров
# ═══════════════════════════════════════════════

class ProductTypePage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Типы товаров")
        title.setObjectName("section")
        layout.addWidget(title)

        form = QHBoxLayout()
        self.id_combo = QComboBox()
        self.id_combo.setEditable(True)
        self.id_combo.setInsertPolicy(QComboBox.NoInsert)
        self.id_combo.setPlaceholderText("ID (англ, напр. cable_new)")
        self.id_combo.currentTextChanged.connect(self._on_id_selected)
        form.addWidget(self.id_combo, 1)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Название (рус, напр. Новый кабель)")
        form.addWidget(self.name_input, 1)
        self.cat_input = QLineEdit()
        self.cat_input.setPlaceholderText("Категория (опц.)")
        form.addWidget(self.cat_input, 1)
        save_btn = QPushButton("Сохранить")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        form.addWidget(save_btn)
        layout.addLayout(form)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Название", "ID (англ)", "Категория", "Keywords"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.rename_btn = QPushButton("Переименовать")
        self.rename_btn.clicked.connect(self._rename)
        btn_row.addWidget(self.rename_btn)
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch()
        self.reload_btn = QPushButton("Перезагрузить YAML seed")
        self.reload_btn.setObjectName("warning")
        self.reload_btn.clicked.connect(self._reload_yaml)
        btn_row.addWidget(self.reload_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.yaml_progress = QProgressBar()
        self.yaml_progress.setRange(0, 0)
        self.yaml_progress.setFixedHeight(6)
        self.yaml_progress.hide()
        layout.addWidget(self.yaml_progress)

    def refresh(self):
        self._load()

    def _load(self):
        if self._panel.engine is None:
            return
        products = self._panel.mm.get_all_products()
        self.table.setRowCount(0)
        self.id_combo.clear()
        self.id_combo.addItem("")
        for pid, pdata in sorted(products.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(pdata.get("name", "")))
            self.table.setItem(row, 1, QTableWidgetItem(pid))
            self.table.setItem(row, 2, QTableWidgetItem(pdata.get("category", "")))
            self.table.setItem(row, 3, QTableWidgetItem((pdata.get("keywords", "") or "")[:80]))
            self.id_combo.addItem(pid)

    def _on_id_selected(self, pid):
        if not pid or self._panel.engine is None:
            return
        products = self._panel.mm.get_all_products()
        pdata = products.get(pid)
        if pdata:
            self.name_input.setText(pdata.get("name", ""))
            self.cat_input.setText(pdata.get("category", ""))
        else:
            self.name_input.clear()
            self.cat_input.clear()

    def _save(self):
        pid = self.id_combo.currentText().strip()
        name = self.name_input.text().strip()
        cat = self.cat_input.text().strip()
        if not pid or not name:
            self.status_label.setText("Заполните ID и название")
            return
        self._panel.mm.save_product_type(pid, name, cat)
        self.id_combo.setCurrentText("")
        self.name_input.clear()
        self.cat_input.clear()
        self._load()
        self._panel.refresh_all_combos()
        self.status_label.setText(f"Тип «{pid}» сохранён")

    def _rename(self):
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("Выберите строку")
            return
        name = self.table.item(row, 0).text()
        pid = self.table.item(row, 1).text()
        if not name:
            self.status_label.setText("Название не может быть пустым")
            return
        self._panel.mm.update_product_type_name(pid, name)
        self._panel.refresh_all_combos()
        self.status_label.setText(f"Тип «{pid}» переименован")

    def _delete(self):
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("Выберите строку")
            return
        name = self.table.item(row, 0).text()
        pid = self.table.item(row, 1).text()
        if not _confirm(self, "Удаление", f"Удалить тип «{name}» ({pid}) и все связанные данные?"):
            return
        self._panel.mm.delete_product_type(pid)
        self._load()
        self._panel.refresh_all_combos()
        self.status_label.setText(f"Тип «{pid}» удалён")

    def _reload_yaml(self):
        from pathlib import Path
        yaml_path = "config/categories_and_sites.yaml"
        if not Path(yaml_path).exists():
            self.status_label.setText(f"Файл {yaml_path} не найден")
            return
        if not _confirm(self, "Перезагрузка", "Перезагрузить YAML seed? Ручные изменения в БД будут перезаписаны."):
            return
        self.yaml_progress.show()
        self.status_label.setText("Загрузка YAML...")
        QApplication.processEvents()
        try:
            self._panel.engine.load_yaml_seed(yaml_path)
            self._load()
            self._panel.refresh_all_combos()
            self.status_label.setText("YAML seed перезагружен")
        finally:
            self.yaml_progress.hide()


# ═══════════════════════════════════════════════
# SitePage — управление сайтами
# ═══════════════════════════════════════════════

class SitePage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Сайты и привязки к типам товаров")
        title.setObjectName("section")
        layout.addWidget(title)

        top = QHBoxLayout()
        top.addWidget(QLabel("Тип товара:"))
        self.product_combo = QComboBox()
        self.product_combo.setMinimumWidth(200)
        self.product_combo.currentIndexChanged.connect(self._load)
        top.addWidget(self.product_combo, 1)
        layout.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Сайт", "Приоритет", "Название", "Base URL"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)

        form = QHBoxLayout()
        form.addWidget(QLabel("Добавить сайт:"))
        self.site_combo = QComboBox()
        self.site_combo.setEditable(True)
        self.site_combo.setMinimumWidth(150)
        form.addWidget(self.site_combo, 1)
        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["primary", "secondary", "all"])
        form.addWidget(self.priority_combo)
        add_btn = QPushButton("+")
        add_btn.setObjectName("small-btn")
        add_btn.setFixedWidth(30)
        add_btn.clicked.connect(self._add_site)
        form.addWidget(add_btn)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.update_btn = QPushButton("Обновить приоритет")
        self.update_btn.clicked.connect(self._update_priority)
        btn_row.addWidget(self.update_btn)
        self.del_site_btn = QPushButton("Отвязать сайт")
        self.del_site_btn.setObjectName("danger")
        self.del_site_btn.clicked.connect(self._delete_site)
        btn_row.addWidget(self.del_site_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    def refresh_combo(self, products: dict):
        current = self.product_combo.currentData()
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        for pid, pdata in sorted(products.items()):
            name = pdata.get("name", pid)
            self.product_combo.addItem(name, pid)
        if current:
            idx = self.product_combo.findData(current)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
        self.product_combo.blockSignals(False)

    def refresh_sites(self):
        all_sites = self._panel.mm.get_all_sites()
        self.site_combo.clear()
        for sid, sdata in sorted(all_sites.items()):
            name = sdata.get("name", sid)
            self.site_combo.addItem(name, sid)
        # Не предвыбирать первый сайт: поле добавления должно быть пустым,
        # чтобы нельзя было случайно привязать сайт по умолчанию (напр. abbro.ru).
        self.site_combo.setCurrentIndex(-1)
        self.site_combo.clearEditText()

    def _load(self):
        if self._panel.engine is None:
            return
        pid = self.product_combo.currentData() or ""
        if not pid:
            self.table.setRowCount(0)
            return
        sites = self._panel.mm.get_product_sites(pid)
        self.table.setRowCount(0)
        for s in sites:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(s.get("id", "")))
            priority = s.get("priority", 2)
            self.table.setItem(row, 1, QTableWidgetItem(PRIORITY_LABELS.get(priority, "all")))
            self.table.setItem(row, 2, QTableWidgetItem(s.get("name", "")))
            self.table.setItem(row, 3, QTableWidgetItem(s.get("base_url", "")))

    def _add_site(self):
        pid = self.product_combo.currentData() or ""
        if not pid:
            self.status_label.setText("Сначала выберите тип товара")
            return
        sid = _combo_value(self.site_combo)
        if not sid:
            self.status_label.setText("Введите или выберите сайт")
            return
        priority = {"primary": 0, "secondary": 1, "all": 2}.get(self.priority_combo.currentText(), 2)
        self._panel.mm.set_product_site_priority(pid, sid, priority)
        self._load()
        self.status_label.setText(f"Сайт «{sid}» привязан к «{pid}»")

    def _update_priority(self):
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("Выберите строку")
            return
        pid = self.product_combo.currentData() or ""
        sid = self.table.item(row, 0).text()
        priority = {"primary": 0, "secondary": 1, "all": 2}.get(self.priority_combo.currentText(), 2)
        self._panel.mm.set_product_site_priority(pid, sid, priority)
        self._load()
        self.status_label.setText(f"Приоритет «{sid}» обновлён")

    def _delete_site(self):
        row = self.table.currentRow()
        if row < 0:
            self.status_label.setText("Выберите строку")
            return
        pid = self.product_combo.currentData() or ""
        sid = self.table.item(row, 0).text()
        if not _confirm(self, "Отвязка", f"Отвязать сайт «{sid}» от «{pid}»?"):
            return
        self._panel.mm.delete_product_site(pid, sid)
        self._load()
        self.status_label.setText(f"Сайт «{sid}» отвязан")


# ═══════════════════════════════════════════════
# ApproachPage — все подходы таблицей
# ═══════════════════════════════════════════════

class ApproachPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Все подходы")
        title.setObjectName("section")
        layout.addWidget(title)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Тип товара:"))
        self.product_combo = QComboBox()
        self.product_combo.setMinimumWidth(180)
        filter_row.addWidget(self.product_combo)
        filter_row.addWidget(QLabel("Сайт:"))
        self.site_filter_combo = QComboBox()
        self.site_filter_combo.setMinimumWidth(150)
        filter_row.addWidget(self.site_filter_combo, 1)
        self.show_deprecated_cb = QCheckBox("Показывать устаревшие")
        filter_row.addWidget(self.show_deprecated_cb)
        self.filter_btn = QPushButton("Применить")
        self.filter_btn.clicked.connect(self._load)
        filter_row.addWidget(self.filter_btn)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            "ID", "Сайт", "Тип товара", "Успехов", "Неудач", "Последний успех", "Метод", "Шаги"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.detail_btn = QPushButton("Показать шаги")
        self.detail_btn.clicked.connect(self._show_detail)
        btn_row.addWidget(self.detail_btn)
        self.deprecate_btn = QPushButton("Депрекейтнуть")
        self.deprecate_btn.setObjectName("warning")
        self.deprecate_btn.clicked.connect(self._deprecate)
        btn_row.addWidget(self.deprecate_btn)
        self.delete_btn = QPushButton("Удалить")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(150)
        layout.addWidget(self.detail_text)

        self._all_approaches = []

    def refresh_combo(self, products: dict):
        current = self.product_combo.currentData()
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        self.product_combo.addItem("(все)", "")
        for pid, pdata in sorted(products.items()):
            name = pdata.get("name", pid)
            self.product_combo.addItem(name, pid)
        if current:
            idx = self.product_combo.findData(current)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
        self.product_combo.blockSignals(False)

    def sync_combo(self, pid: str):
        if pid:
            idx = self.product_combo.findData(pid)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def refresh(self):
        self._load()

    def refresh_sites(self):
        all_sites = self._panel.mm.get_all_sites()
        self.site_filter_combo.clear()
        self.site_filter_combo.addItem("(все)", "")
        for sid, sdata in sorted(all_sites.items()):
            name = sdata.get("name", sid)
            self.site_filter_combo.addItem(name, sid)

    def _load(self):
        if self._panel.engine is None:
            return
        mm = self._panel.mm
        approaches = mm.get_all_approaches_flat()
        site_filter = self.site_filter_combo.currentData() or ""
        product_filter = self.product_combo.currentData() or ""
        show_deprecated = self.show_deprecated_cb.isChecked()

        self._all_approaches = []
        self.table.setRowCount(0)
        products = self._panel.engine.get_all_products()
        for a in approaches:
            if not show_deprecated and a.get("is_deprecated"):
                continue
            if site_filter and site_filter != a.get("site_id", ""):
                continue
            if product_filter and product_filter != a.get("product_type_id", ""):
                continue
            self._all_approaches.append(a)
            row = self.table.rowCount()
            self.table.insertRow(row)
            pid = a.get("product_type_id", "")
            pdata = products.get(pid, {})
            pname = pdata.get("name", pid)
            self.table.setItem(row, 0, QTableWidgetItem(str(a.get("id", ""))))
            self.table.setItem(row, 1, QTableWidgetItem(a.get("site_id", "")))
            self.table.setItem(row, 2, QTableWidgetItem(f"{pname}"))
            self.table.setItem(row, 3, QTableWidgetItem(str(a.get("success_count", 0))))
            self.table.setItem(row, 4, QTableWidgetItem(str(a.get("consecutive_failures", 0))))
            self.table.setItem(row, 5, QTableWidgetItem(_fmt_date(a.get("last_success_date"))))
            self.table.setItem(row, 6, QTableWidgetItem((a.get("method", "") or "")[:20]))
            concrete = a.get("concrete", [])
            pat = " → ".join(_step_action(s) for s in concrete[:4])
            self.table.setItem(row, 7, QTableWidgetItem(pat[:60]))
        self.detail_text.clear()

    def _get_selected(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._all_approaches):
            return None
        return self._all_approaches[row]

    def _show_detail(self):
        a = self._get_selected()
        if not a:
            self.detail_text.setText("Выберите строку")
            return
        products = self._panel.engine.get_all_products()
        pid = a.get("product_type_id", "")
        pdata = products.get(pid, {})
        pname = pdata.get("name", pid)
        lines = [f"Подход ID {a.get('id', '?')} — {a.get('site_id', '?')} / {pname}"]
        concrete = a.get("concrete", [])
        for i, s in enumerate(concrete):
            action = _step_action(s)
            if isinstance(s, dict):
                target = s.get("target") or s.get("element") or ""
                text = s.get("text", "")
                key = s.get("key", "")
            else:
                target = ""
                text = ""
                key = ""
            part = action
            if target:
                part += f"[{target}]"
            if text and len(text) < 60:
                part += f"='{text}'"
            if key:
                part += f"({key})"
            lines.append(f"  {i+1}. {part}")
        if a.get("notes"):
            lines.append(f"\nЗаметки: {a['notes']}")
        self.detail_text.setText("\n".join(lines))

    def _deprecate(self):
        a = self._get_selected()
        if not a:
            return
        aid = a["id"]
        if not _confirm(self, "Депрекейт", f"Пометить подход ID {aid} как устаревший?"):
            return
        self._panel.mm.deprecate_approach(aid)
        self._load()

    def _delete(self):
        a = self._get_selected()
        if not a:
            return
        aid = a["id"]
        sid = a.get("site_id", "?")
        if not _confirm(self, "Удаление", f"Удалить подход ID {aid} ({sid})?"):
            return
        self._panel.mm.delete_approach(aid)
        self._load()


# ═══════════════════════════════════════════════
# PricePage — все цены таблицей
# ═══════════════════════════════════════════════

class PricePage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Подтверждённые цены")
        title.setObjectName("section")
        layout.addWidget(title)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Фильтр:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("поиск по спецификации или сайту...")
        filter_row.addWidget(self.search_input, 1)
        self.filter_btn = QPushButton("Применить")
        self.filter_btn.clicked.connect(self._load)
        filter_row.addWidget(self.filter_btn)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Спецификация", "Сайт", "Цена", "Уверенность", "Дата", "URL"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table, 1)

        edit_form = QHBoxLayout()
        edit_form.addWidget(QLabel("Новая цена:"))
        self.edit_price = QDoubleSpinBox()
        self.edit_price.setRange(0, 99999999)
        self.edit_price.setSuffix(" ₽")
        edit_form.addWidget(self.edit_price)
        self.save_edit_btn = QPushButton("Сохранить")
        self.save_edit_btn.setObjectName("primary")
        self.save_edit_btn.clicked.connect(self._edit_price)
        edit_form.addWidget(self.save_edit_btn)
        self.del_price_btn = QPushButton("Удалить")
        self.del_price_btn.setObjectName("danger")
        self.del_price_btn.clicked.connect(self._delete_price)
        edit_form.addWidget(self.del_price_btn)
        edit_form.addStretch()
        layout.addLayout(edit_form)

        self._all_prices = []

    def refresh(self):
        self._load()

    def _load(self):
        if self._panel.engine is None:
            return
        mm = self._panel.mm
        prices = mm.get_all_confirmed_prices()
        search = self.search_input.text().strip().lower()
        self._all_prices = []
        self.table.setRowCount(0)
        for p in prices:
            if search and search not in p.get("spec_text", "").lower() and search not in p.get("site_id", "").lower():
                continue
            self._all_prices.append(p)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(str(p.get("id", ""))))
            self.table.setItem(row, 1, QTableWidgetItem((p.get("spec_text") or "")[:60]))
            self.table.setItem(row, 2, QTableWidgetItem(p.get("site_id", "")))
            price = p.get("price")
            self.table.setItem(row, 3, QTableWidgetItem(f"₽{price:,.2f}" if price else "—"))
            conf = p.get("confidence", 0)
            self.table.setItem(row, 4, QTableWidgetItem(f"{conf:.0%}" if conf else "—"))
            self.table.setItem(row, 5, QTableWidgetItem(_fmt_date(p.get("created_at"))))
            self.table.setItem(row, 6, QTableWidgetItem((p.get("url") or "")[:60]))

    def _get_selected_price(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._all_prices):
            return None
        return self._all_prices[row]

    def _edit_price(self):
        p = self._get_selected_price()
        if not p:
            return
        new_price = self.edit_price.value()
        if new_price <= 0:
            return
        self._panel.mm.update_confirmed_price(
            price_id=p["id"],
            spec_text=p.get("spec_text", ""),
            price=new_price,
            site=p.get("site_id", ""),
            confidence=p.get("confidence", 0.95),
            reason=p.get("reason", ""),
        )
        self._load()
        self.edit_price.setValue(0)

    def _delete_price(self):
        p = self._get_selected_price()
        if not p:
            return
        pid = p["id"]
        spec_short = (p.get("spec_text") or "")[:40]
        if not _confirm(self, "Удаление", f"Удалить цену ID {pid} («{spec_short}...»)?") :
            return
        self._panel.mm.delete_confirmed_price(pid)
        self._load()


# ═══════════════════════════════════════════════
# AssistantToolPanel — главная панель
# ═══════════════════════════════════════════════

# ═══════════════════════════════════════════════
# StudyPage — принудительное обучение сайта
# ═══════════════════════════════════════════════

class StudyPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        self._runner = None
        self._approach_checkboxes: list[tuple[QCheckBox, dict]] = []
        self._spinner_color = "#89b4fa"
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(120)
        self._spinner_timer.timeout.connect(self._tick_spinner)
        self._spinner = SpinnerWidget(size=24, color=self._spinner_color, spacing=0.5)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = QLabel("Принудительное обучение")
        title.setObjectName("section")
        layout.addWidget(title)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://site.ru/catalog/...")
        form.addRow("URL товара:", self.url_input)
        self.spec_input = QLineEdit()
        self.spec_input.setPlaceholderText("Труба ПВХ гибкая гофр. д.20мм, ДКС")
        form.addRow("Спецификация:", self.spec_input)
        self.product_combo = QComboBox()
        self.product_combo.setEditable(True)
        self.product_combo.setPlaceholderText("Выберите или введите новый...")
        self.product_combo.setMinimumWidth(280)
        pt_layout = QHBoxLayout()
        pt_layout.setContentsMargins(0, 0, 0, 0)
        pt_layout.setSpacing(2)
        pt_layout.addWidget(self.product_combo)
        self.combo_btn = QPushButton("▼")
        self.combo_btn.setFixedWidth(24)
        self.combo_btn.setToolTip("Показать все типы товаров")
        self.combo_btn.clicked.connect(lambda: self.product_combo.showPopup())
        pt_layout.addWidget(self.combo_btn)
        form.addRow("Тип товара:", pt_layout)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("🚀 Запустить обучение")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ Остановить")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.stop_btn)
        self.headless_cb = QCheckBox("🕶️ Headless")
        from src.config_loader import load_settings
        self.headless_cb.setChecked(load_settings().get("browser", {}).get("headless", True))
        self.headless_cb.toggled.connect(self._on_headless_toggle)
        btn_row.addWidget(self.headless_cb)
        self.fresh_cb = QCheckBox("🔄 Fresh")
        self.fresh_cb.setToolTip("Не использовать ранее сохранённые цены")
        btn_row.addWidget(self.fresh_cb)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Conversation log
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setPlaceholderText("Здесь будет отображаться ход анализа...")
        layout.addWidget(self.log_text, 2)

        # Q&A area (hidden until agent asks)
        qa_frame = QFrame()
        qa_frame.setObjectName("card")
        qa_layout = QHBoxLayout(qa_frame)
        qa_layout.setContentsMargins(8, 4, 8, 4)
        self.question_label = QLabel("")
        self.question_label.setWordWrap(True)
        qa_layout.addWidget(self.question_label, 1)
        self.answer_input = QLineEdit()
        self.answer_input.setPlaceholderText("Ваш ответ...")
        self.answer_input.setEnabled(False)
        self.answer_input.returnPressed.connect(self._send_answer)
        qa_layout.addWidget(self.answer_input, 1)
        self.send_btn = QPushButton("Отправить")
        self.send_btn.setObjectName("primary")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send_answer)
        qa_layout.addWidget(self.send_btn)
        qa_frame.setVisible(False)
        self._qa_frame = qa_frame
        layout.addWidget(qa_frame)

        # Approaches approval area (hidden until agent finishes)
        appr_frame = QFrame()
        appr_frame.setObjectName("card")
        appr_layout = QVBoxLayout(appr_frame)
        appr_layout.setContentsMargins(8, 4, 8, 4)
        appr_layout.addWidget(QLabel("Предложенные подходы:"))
        self.approaches_container = QVBoxLayout()
        appr_layout.addLayout(self.approaches_container)
        save_appr_row = QHBoxLayout()
        self.save_selected_btn = QPushButton("💾 Сохранить выбранные")
        self.save_selected_btn.setObjectName("success")
        self.save_selected_btn.clicked.connect(self._save_selected_approaches)
        save_appr_row.addWidget(self.save_selected_btn)
        save_appr_row.addStretch()
        appr_layout.addLayout(save_appr_row)
        appr_frame.setVisible(False)
        self._appr_frame = appr_frame
        layout.addWidget(appr_frame)

        status_row = QHBoxLayout()
        self._spinner.setFixedSize(20, 20)
        status_row.addWidget(self._spinner)
        self.status_label = QLabel("")
        status_row.addWidget(self.status_label, 1)
        layout.addLayout(status_row)

    def refresh_combo(self, products: dict):
        current = self.product_combo.currentData()
        self.product_combo.clear()
        for pid, pdata in sorted(products.items()):
            self.product_combo.addItem(pdata.get("name", pid), pid)
        if current:
            idx = self.product_combo.findData(current)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def sync_combo(self, pid: str):
        if pid:
            idx = self.product_combo.findData(pid)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)

    def prefill(self, spec_text: str, product_type: str, failure_context: str = ""):
        """Pre-fill from results table row."""
        self.spec_input.setText(spec_text)
        self._failure_context = failure_context
        if product_type:
            idx = self.product_combo.findData(product_type)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
        self.url_input.setFocus()

    def _tick_spinner(self):
        self._spinner.tick()

    def _on_headless_toggle(self, checked):
        from src.config_loader import save_browser_headless
        save_browser_headless(checked)

    def _start(self):
        url = self.url_input.text().strip()
        spec = self.spec_input.text().strip()
        pt = self._panel.resolve_pt(self.product_combo)
        if not url or not spec or not pt:
            self.status_label.setText("Заполните URL, спецификацию и тип товара")
            return

        llm_config = self._panel.llm_config
        if not llm_config:
            self.status_label.setText("LLM не настроен (укажите в настройках)")
            return

        self._clear_approaches()
        self._qa_frame.setVisible(False)
        self._appr_frame.setVisible(False)

        ctx = getattr(self, "_failure_context", "")
        self._runner = StudyRunner(url, spec, pt, llm_config, failure_context=ctx)
        self._runner.log_signal.connect(self._on_log)
        self._runner.question_signal.connect(self._on_question)
        self._runner.approaches_signal.connect(self._on_approaches)
        self._runner.done_signal.connect(self._on_done)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log_text.clear()
        self.status_label.setText("Обучение запущено...")
        self._spinner.setFixedSize(20, 20)
        self._spinner.tick()
        self._spinner_timer.start()
        self._runner.start()

    def _stop(self):
        if self._runner:
            self._runner.stop()
        self._spinner_timer.stop()
        self._spinner.setFixedSize(0, 0)
        self.status_label.setText("Остановлено")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._qa_frame.setVisible(False)

    def _on_log(self, msg: str):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_question(self, question: str):
        self.question_label.setText(f"❓ {question}")
        self.answer_input.clear()
        self.answer_input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self._qa_frame.setVisible(True)
        self.answer_input.setFocus()
        self.log_text.append(f"\n❓ Агент спрашивает: {question}")

    def _send_answer(self):
        answer = self.answer_input.text().strip()
        if not answer or not self._runner:
            return
        self._runner.answer_user(answer)
        self.log_text.append(f"💬 Ваш ответ: {answer}")
        self.answer_input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._qa_frame.setVisible(False)

    def _on_approaches(self, data: dict):
        self._clear_approaches()
        total = sum(len(v) for v in data.values())
        if not total:
            return
        self._appr_frame.setVisible(True)

        sections = [
            ("approaches", "📋 Подходы", "site", lambda a: f"[{a.get('site', '?')}] {' → '.join(_step_action(s) for s in a.get('concrete_steps', [])[:4])}"),
            ("hints", "💡 Хинты", "hint_text", lambda a: f"[{a.get('product_type', '?')}] {a.get('hint_text', '')[:80]}"),
            ("concepts", "🔗 Концепты", "relation", lambda a: f"{a.get('child', '?')} {a.get('relation', '?')} {a.get('parent', '?')}"),
            ("sites", "🌐 Новые сайты", "domain", lambda a: f"{a.get('domain', '?')} ({a.get('name', '?')})"),
        ]

        for key, label, _, fmt in sections:
            items = data.get(key, [])
            if not items:
                continue
            section_label = QLabel(f"{label} ({len(items)}):")
            section_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
            self.approaches_container.addWidget(section_label)
            self.log_text.append(f"\n💡 {label} ({len(items)}):")
            for i, a in enumerate(items):
                summary = fmt(a)
                self.log_text.append(f"  {i+1}. {summary}")
                cb = QCheckBox(summary)
                cb.setChecked(True)
                self.approaches_container.addWidget(cb)
                a["_type"] = key
                self._approach_checkboxes.append((cb, a))
        self.status_label.setText(f"Предложено: {total} — выберите и сохраните")

    def _on_done(self, success: bool, message: str):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._qa_frame.setVisible(False)
        self._spinner_timer.stop()
        self._spinner.setFixedSize(0, 0)
        self.status_label.setText(message)
        self._runner = None

    def _clear_approaches(self):
        while self.approaches_container.count():
            item = self.approaches_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._approach_checkboxes.clear()
        self._appr_frame.setVisible(False)

    def _save_selected_approaches(self):
        selected = sum(1 for cb, _ in self._approach_checkboxes if cb.isChecked())
        if selected == 0:
            return
        if not _confirm(self, "Сохранение", f"Сохранить {selected} элементов в граф?"):
            return
        mm = self._panel.mm
        saved = {"approaches": 0, "hints": 0, "concepts": 0, "sites": 0}
        for cb, a in self._approach_checkboxes:
            if not cb.isChecked():
                continue
            typ = a.get("_type", "approaches")
            try:
                if typ == "approaches":
                    mm.save_approach(
                        product_type=a.get("product_type", ""),
                        site=a.get("site", ""),
                        concrete_steps=a.get("concrete_steps", []),
                        selectors_cache=a.get("selectors_cache"),
                        param_slots=a.get("param_slots"),
                        method=a.get("method", "study"),
                        search_query=a.get("search_query", ""),
                        notes=a.get("notes", ""),
                    )
                elif typ == "hints":
                    mm.add_hint(
                        a.get("product_type", "unknown"),
                        a.get("hint_text", ""),
                        None,
                        a.get("priority", 0.7),
                    )
                elif typ == "concepts":
                    mm.save_concept_edge(
                        child=a.get("child", ""),
                        parent=a.get("parent", ""),
                        relation=a.get("relation", "SOLD_AT"),
                        weight=a.get("weight", 1.0),
                    )
                elif typ == "sites":
                    mm.add_site(a.get("domain", ""), a.get("name", ""), a.get("product_type", ""))
                saved[typ] += 1
            except Exception as e:
                self.log_text.append(f"❌ Ошибка сохранения {typ}: {e}")
        total = sum(saved.values())
        self.log_text.append(f"💾 Сохранено: {total} (подходов {saved['approaches']}, хинтов {saved['hints']}, концептов {saved['concepts']}, сайтов {saved['sites']})")
        self.status_label.setText(f"Сохранено: {total}")
        if total:
            QApplication.processEvents()
            self._panel.engine.rebuild()
            QApplication.processEvents()
            self._panel.refresh_all_combos()
        self._clear_approaches()


# ═══════════════════════════════════════════════
# HelpPage — подробная документация
# ═══════════════════════════════════════════════

HELP_TEXT = """
<h1>🔧 Ассистент графа цен — полное руководство</h1>

<p>Ассистент — это интерфейс для ручного управления базой знаний Pricer Vision.
Он позволяет просматривать, создавать, редактировать и удалять данные графа:
типы товаров, сайты, подходы к сбору цен, подсказки для LLM и сами цены.</p>

<hr>

<h2>🏠 Главная панель (верхняя)</h2>
<p><b>Тип товара</b> — глобальный фильтр. При его смене все страницы, поддерживающие
фильтрацию, синхронизируются автоматически (кроме страниц Поиск и Цены, где фильтр
задаётся отдельно).</p>

<hr>

<h2>📑 Страницы инструмента</h2>

<h3>1️⃣ Контекст графа</h3>
<p><b>Назначение:</b> общая картина — какие типы товаров есть в графе, какие сайты
и подходы к ним привязаны, сколько подтверждённых цен.</p>
<p><b>Как работает:</b> загружает данные из <code>get_cached_categories()</code> — сводку
по каждому типу товара. Глобальный фильтр сверху сужает до выбранного типа.</p>
<p><b>Когда использовать:</b> первым делом при открытии ассистента, чтобы оценить
наполненность базы.</p>

<h3>2️⃣ Поиск подходов</h3>
<p><b>Назначение:</b> найти конкретные подходы по типу товара и/или сайту, просмотреть
их шаги, удалить или пометить устаревшими.</p>
<p><b>Как работает:</b></p>
<ul>
  <li>Выберите тип товара и/или сайт в выпадающих списках</li>
  <li>Нажмите "Поиск" — отобразятся подходы (макс. 20)</li>
  <li>Кликните на строку с <code>[ID N]</code> — она подсветится</li>
  <li>Нажмите "Удалить выбранный подход" или "Депрекейтнуть"</li>
</ul>
<p><b>Важно:</b> при двойном поиске результаты не накапливаются — каждый поиск
заменяет предыдущие.</p>
<p><b>Пример:</b> тип "Кабель" + сайт "dks" → показывает все подходы для
сбора цен на кабель с сайта ДКС.</p>

<h3>3️⃣ Сайты</h3>
<p><b>Назначение:</b> управление привязкой сайтов к типам товаров и их приоритетами.</p>
<p><b>Приоритеты:</strong></p>
<ul>
  <li><b>primary</b> (0) — основные сайты, проверяются в первую очередь</li>
  <li><b>secondary</b> (1) — второстепенные</li>
  <li><b>all</b> (2) — агрегаторы/поисковики (Яндекс, Google)</li>
</ul>
<p><b>Как работает:</b></p>
<ul>
  <li>Выберите тип товара → таблица покажет привязанные сайты</li>
  <li>Чтобы добавить сайт: выберите из списка или введите новый, укажите приоритет, нажмите "+"</li>
  <li>Чтобы обновить приоритет: выберите строку, измените приоритет, нажмите "Обновить приоритет"</li>
  <li>Чтобы отвязать сайт: выберите строку, нажмите "Отвязать сайт"</li>
</ul>
<p><b>NB:</b> отвязка сайта удаляет только связь "тип товара ↔ сайт",
сам сайт из справочника не удаляется.</p>
<p><b>NB:</b> Новые сайты можно добавить только отсюда или через обучение
(страница "Обучение" → секция "Новые сайты").</p>

<h3>4️⃣ Подходы</h3>
<p><b>Назначение:</b> просмотр, фильтрация и управление всеми подходами в табличном виде.</p>
<p><b>Фильтры:</b></p>
<ul>
  <li><b>Тип товара</b> — выберите конкретный тип или "(все)"</li>
  <li><b>Сайт</b> — выберите конкретный сайт или "(все)"</li>
  <li><b>Показывать устаревшие</b> — по умолчанию подходы с флагом deprecated скрыты</li>
</ul>
<p><b>Действия:</b></p>
<ul>
  <li>Выберите строку → нажмите "Показать шаги" для просмотра деталей подхода</li>
  <li>"Депрекейтнуть" — пометить подход как устаревший (он перестанет использоваться)</li>
  <li>"Удалить" — полностью удалить подход из БД</li>
</ul>
<p><b>Пример детального просмотра:</b></p>
<pre>
Подход ID 42 — dks / Кабель силовой
  1. browser_navigate[https://dks.ru/catalog/...]
  2. wait_for_load
  3. query_selector_all[.price]
  4. extract_text
</pre>

<h3>5️⃣ Цены</h3>
<p><b>Назначение:</b> просмотр и редактирование подтверждённых цен.</p>
<p><b>Фильтр:</b> поиск по спецификации или сайту (текстовый, не выпадающий список).</p>
<p><b>Действия:</b></p>
<ul>
  <li>Выберите строку → введите новую цену → нажмите "Сохранить" для изменения цены</li>
  <li>Выберите строку → нажмите "Удалить" для удаления записи о цене</li>
</ul>
<p><b>NB:</b> Редактирование цен здесь сохраняет причину (reason) из исходной записи.
Для ручного добавления новой цены используйте страницу "Коррекция цен".</p>

<h3>6️⃣ Типы товаров</h3>
<p><b>Назначение:</b> CRUD для типов товаров — создание, переименование, удаление,
перезагрузка из YAML seed-файла.</p>
<p><b>Поля:</b></p>
<ul>
  <li><b>ID (англ)</b> — уникальный идентификатор, напр. <code>cable_vvg</code></li>
  <li><b>Название (рус)</b> — отображаемое имя, напр. "Кабель ВВГ"</li>
  <li><b>Категория</b> — группировка (опционально)</li>
</ul>
<p><b>Как работает:</b></p>
<ul>
  <li>Создание: введите ID и название в верхней форме, нажмите "Сохранить"</li>
  <li>Редактирование: выберите ID из выпадающего списка или кликните строку в таблице,
  измените название (через двойной клик на ячейке) и нажмите "Переименовать"</li>
  <li>Удаление: выберите строку, нажмите "Удалить" — <b>будут удалены все связанные
  подходы, цены, подсказки и привязки сайтов</b></li>
  <li>Перезагрузка YAML: нажмите "Перезагрузить YAML seed" — данные из файла
  <code>config/categories_and_sites.yaml</code> перезапишут существующие записи
  в БД. Ручные изменения БЕЗ YAML будут потеряны.</li>
</ul>
<p><b>Важно:</b> YAML-файл — единственный источник "истины" для первичных типов
товаров. Всегда добавляйте новые типы сначала в YAML, если планируете
переиспользовать их в других инсталляциях.</p>

<h3>7️⃣ Подсказки</h3>
<p><b>Назначение:</b> подсказки (hints) — текстовая информация для LLM-агента
о том, как искать цены на конкретный тип товара.</p>
<p><b>Как работает:</b></p>
<ul>
  <li>Выберите тип товара → введите текст подсказки → укажите приоритет → "Добавить"</li>
  <li>Подсказки автоматически загружаются агентом при анализе сайта</li>
  <li>Приоритет (0.0–1.0) влияет на то, насколько настойчиво агент будет следовать подсказке</li>
  <li>"Показать все" — просмотр всех подсказок в системе</li>
  <li>Кликните на строку с <code>[ID N]</code> → "Удалить выбранную подсказку"</li>
</ul>
<p><b>Пример содержимого подсказки:</b></p>
<pre>
[ID 5] ИБП (приоритет: 0.9)
  ИБП, источник бесперебойного питания, UPS, бесперебойник
━━━
[ID 12] ИБП (приоритет: 0.5)
  На Яндекс.Маркете цена обычно в блоке с классом "_price"
</pre>
<p><b>Совет:</b> Разбивайте подсказки на логические части. Для каждого типа товара
можно добавить несколько подсказок — агент объединит их все.</p>

<h3>8️⃣ Коррекция цен</h3>
<p><b>Назначение:</b> ручное добавление или корректировка цены с указанием всех
параметров. Используется, когда автоматический сбор не справился или цена
заведомо верна.</p>
<p><b>Поля:</b></p>
<ul>
  <li>Тип товара — выберите из списка (можно ввести русское название)</li>
  <li>Спецификация — текст характеристики товара</li>
  <li>Сайт — выберите из списка</li>
  <li>Цена — числовое значение в рублях</li>
  <li>URL — страница, где указана цена (опционально, но рекомендуется)</li>
  <li>Уверенность — от 0.6 до 1.0 (рекомендуется 0.95 для ручного ввода)</li>
</ul>
<p><b>NB:</b> Если цена с такой же спецификацией на таком же сайте уже существует,
она будет обновлена (новая цена, уверенность = min(1.0, новое значение)).</p>

<h3>9️⃣ Обучение (Study)</h3>
<p><b>Назначение:</b> запуск LLM-агента для автоматического изучения сайта и
построения подходов к сбору цен.</p>
<p><b>Поля:</b></p>
<ul>
  <li>URL товара — ссылка на страницу с ценой</li>
  <li>Спецификация — текст спецификации товара</li>
  <li>Тип товара — выберите из списка (можно ввести русское название)</li>
</ul>
<p><b>Опции:</b></p>
<ul>
  <li><b>🕶️ Headless</b> — браузер без графического интерфейса (рекомендуется для
  массового обучения)</li>
  <li><b>🔄 Fresh</b> — игнорировать ранее сохранённые цены для этого сайта/товара</li>
</ul>
<p><b>Процесс:</b></p>
<ol>
  <li>Заполните URL, спецификацию и тип товара</li>
  <li>Нажмите "🚀 Запустить обучение"</li>
  <li>Агент открывает сайт, анализирует страницу, ищет цену</li>
  <li>Если агенту нужна помощь — появится блок Q&A: введите ответ и нажмите Enter</li>
  <li>По завершении отобразится список предложенных подходов, хинтов, концептов и новых сайтов</li>
  <li>Отметьте нужные элементы и нажмите "💾 Сохранить выбранные"</li>
</ol>
<p><b>NB:</b> не отмечайте явно ошибочные подходы — они не будут сохранены.</p>
<p><b>NB:</b> После сохранения граф перестраивается автоматически, и все комбобоксы
обновляются. Если обучение прервано кнопкой "⏹ Остановить", результаты не сохраняются.</p>

<h3>🔟 Статистика</h3>
<p><b>Назначение:</b> быстрый обзор количества типов товаров, сайтов, подходов,
цен и подсказок в системе, а также последние подходы.</p>
<p><b>Как работает:</b> нажмите "Обновить статистику" для перезагрузки данных.
Обновляется автоматически при перестроении графа.</p>
<p><b>Пример вывода:</b></p>
<pre>
Категорий: 12
Сайтов: 8
Подходов: 143
Подтверждённых цен: 527
Подсказок: 34

Последние подходы:
  • dks — успехов: 12
  • iec — успехов: 5
  • yandex — успехов: 3
</pre>

<hr>

<h2>🔗 Связь между страницами</h2>
<ul>
  <li><b>Типы товаров → Сайты:</b> сначала создайте тип товара, затем привяжите к нему сайты</li>
  <li><b>Сайты → Подходы:</b> подходы создаются автоматически при обучении или вручную
  через интерфейс "Обучение"</li>
  <li><b>Подходы → Цены:</b> подтверждённые цены — результат успешного выполнения подхода</li>
  <li><b>Типы товаров → Подсказки:</b> подсказки привязаны к типу товара и
  используются агентом при анализе</li>
  <li><b>StudyPage → Все остальное:</b> обучение может создавать подходы, хинты,
  концепты, привязывать новые сайты</li>
</ul>

<hr>

<h2>⚙️ Рекомендации и best practices</h2>

<h3>Порядок добавления нового товара</h3>
<ol>
  <li>Добавьте тип товара в <code>config/categories_and_sites.yaml</code> (рекомендуется)
  или через страницу "Типы товаров" → "Сохранить"</li>
  <li>Привяжите сайты на странице "Сайты"</li>
  <li>Добавьте подсказки на странице "Подсказки" (особенно для специфичных товаров)</li>
  <li>Запустите обучение на реальном URL этого товара</li>
  <li>Проверьте и сохраните предложенные подходы</li>
</ol>

<h3>Работа с типами товаров</h3>
<ul>
  <li>ID должны быть на английском, в snake_case (напр. <code>cable_vvg</code>)</li>
  <li>Название — на русском, для отображения (напр. "Кабель ВВГ")</li>
  <li>Не удаляйте тип товара, если к нему привязаны цены/подходы — удаление каскадное</li>
  <li>Перед перезагрузкой YAML убедитесь, что файл актуален</li>
</ul>

<h3>Работа с подходами</h3>
<ul>
  <li>Депрекейтните подход, если сайт изменил структуру — не удаляйте сразу</li>
  <li>После исправления структуры сайта можно создать новый подход; старый deprecated подход
  будет игнорироваться</li>
  <li>Одинаковые подходы автоматически дедуплицируются при сохранении</li>
</ul>

<h3>Работа с ценами</h3>
<ul>
  <li>Ручная коррекция — для случаев, когда цена известна точно (письмо поставщика,
  прайс-лист)</li>
  <li>Уверенность 0.95 — стандарт для ручного ввода</li>
  <li>Одинаковые спецификации на одном сайте обновляют существующую цену</li>
</ul>

<h3>Обучение (Study)</h3>
<ul>
  <li>Для первого обучения выберите headless = выкл, чтобы видеть, что делает агент</li>
  <li>Если агент "завис" — ответьте на его вопрос в Q&A или остановите и запустите заново</li>
  <li>Fresh режим полезен для повторного обучения — агент не будет опираться на старые цены</li>
  <li>После сохранения обязательно проверьте статистику — подходы и цены должны обновиться</li>
</ul>

<hr>

<h2>❓ Частые вопросы</h2>

<p><b>Q: Почему поиск подходов ничего не находит?</b></p>
<p>A: Проверьте, что выбранный тип товара и сайт существуют в системе и привязаны
друг к другу. Используйте страницу "Контекст графа" для проверки.</p>

<p><b>Q: Я добавил сайт, но он не появляется в выпадающем списке?</b></p>
<p>A: Обновите список, переключившись на другую страницу или нажав кнопку "Поиск" /
"Применить". Если сайт только что создан через "Сайты" → "+", он должен сразу появиться.</p>

<p><b>Q: Обучение завершилось, но подходы не сохранились?</b></p>
<p>A: Нужно явно отметить подходы чекбоксами и нажать "💾 Сохранить выбранные".
Если ничего не отметить — ничего не сохранится.</p>

<p><b>Q: Как отменить случайно сохранённый подход?</b></p>
<p>A: Найдите его на странице "Подходы" (по ID или сайту) и удалите или депрекейтните.</p>

<p><b>Q: Почему в статистике 0 подходов, хотя я их добавлял?</b></p>
<p>A: Возможно, граф не перестроился. Нажмите "Обновить статистику". Если не помогает —
перезагрузите приложение. Если подходы есть в БД, но не видны — проверьте
на странице "Подходы" снят ли фильтр.</p>

<p><b>Q: Что такое "концепты" и зачем они?</b></p>
<p>A: Концепты — это семантические связи между сущностями графа (напр. товар SOLD_AT сайт).
Они используются для построения контекста при анализе цен и для визуализации графа.
Можно просмотреть на вкладке "Граф" основного окна.</p>

<hr>

<h2>🗺️ Data Flow</h2>
<pre>
YAML seed → ProductTypePage → product_types (БД)
                                   ↓
StudyPage (LLM-агент) → approaches → ApproachPage / SearchPage
                       → confirmed_prices → PricePage
                       → hints → HintPage
                       → concepts → визуализация графа
                       → new sites → SitePage
                                   ↓
                          CorrectionPage (ручной ввод цен)
</pre>
"""


class HelpPage(QWidget):
    def __init__(self, panel):
        super().__init__()
        self._panel = panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("Справка по ассистенту графа")
        title.setObjectName("section")
        layout.addWidget(title)

        self.browser = QTextEdit()
        self.browser.setReadOnly(True)
        self.browser.setHtml(HELP_TEXT)
        layout.addWidget(self.browser, 1)


class AssistantToolPanel(QWidget):
    TOOLS = [
        ("Справка", HelpPage),
        ("Контекст графа", ContextPage),
        ("Поиск подходов", SearchPage),
        ("Сайты", SitePage),
        ("Подходы", ApproachPage),
        ("Цены", PricePage),
        ("Типы товаров", ProductTypePage),
        ("Подсказки", HintPage),
        ("Коррекция цен", CorrectionPage),
        ("Обучение", StudyPage),
        ("Статистика", StatsPage),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = None
        self._mm = None
        self._llm_config = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Global product type combo
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(12, 8, 12, 4)
        top_bar.addWidget(QLabel("Тип товара:"))
        self.global_combo = QComboBox()
        self.global_combo.setMinimumWidth(250)
        self.global_combo.currentIndexChanged.connect(self._on_global_combo)
        top_bar.addWidget(self.global_combo, 1)
        main_layout.addLayout(top_bar)

        # Nav + content
        self._list = QListWidget()
        self._list.setFixedWidth(160)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.currentRowChanged.connect(self._switch)

        self._stack = QStackedWidget()
        self._pages = []
        for name, cls in self.TOOLS:
            page = cls(self)
            self._pages.append(page)
            self._stack.addWidget(page)
            self._list.addItem(name)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._list)
        splitter.addWidget(self._stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setHandleWidth(1)
        main_layout.addWidget(splitter, 1)

        self._list.setCurrentRow(0)

    @property
    def engine(self):
        return self._engine

    @engine.setter
    def engine(self, val):
        self._engine = val
        import traceback
        try:
            self._refresh_combos()
            self._refresh_pages()
        except Exception as e:
            print(f"[AssistantPanel] ERROR in engine setter: {e}")
            traceback.print_exc()

    @property
    def mm(self):
        if self._mm is None and self._engine:
            self._mm = MemoryManager(self._engine)
        return self._mm

    @mm.setter
    def mm(self, val):
        self._mm = val

    @property
    def llm_config(self):
        return self._llm_config

    @llm_config.setter
    def llm_config(self, val):
        self._llm_config = val

    def _on_global_combo(self):
        pid = self.global_combo.currentData() or ""
        for page in self._pages:
            if hasattr(page, "sync_combo"):
                page.sync_combo(pid)

    def resolve_pt(self, combo: QComboBox) -> str:
        """Get product type ID from editable combo, resolve typed Russian name."""
        val = _combo_value(combo)
        if not val:
            return ""
        for i in range(combo.count()):
            if val.lower() in combo.itemText(i).lower():
                return str(combo.itemData(i) or "")
        return val

    def _switch(self, row):
        if 0 <= row < len(self._pages):
            self._stack.setCurrentIndex(row)

    def _refresh_combos(self):
        if not self._engine:
            return
        products = self._engine.get_all_products()
        self.global_combo.blockSignals(True)
        self.global_combo.clear()
        self.global_combo.addItem("(нет фильтра)", "")
        for pid, pdata in sorted(products.items()):
            name = pdata.get("name", pid)
            self.global_combo.addItem(name, pid)
        self.global_combo.blockSignals(False)

        for page in self._pages:
            if hasattr(page, "refresh_combo"):
                page.refresh_combo(products)
            if hasattr(page, "refresh_sites"):
                page.refresh_sites()

    def _refresh_pages(self):
        for page in self._pages:
            if hasattr(page, "refresh"):
                page.refresh()
            if hasattr(page, "_load"):
                page._load()

    def refresh_all_combos(self):
        self._refresh_combos()

    def prefill_study(self, spec_text: str, product_type: str, failure_context: str = ""):
        """Switch to study tab and pre-fill fields from a results row."""
        study_idx = None
        for i, (name, cls) in enumerate(self.TOOLS):
            if cls == StudyPage:
                study_idx = i
                break
        if study_idx is not None:
            self._list.setCurrentRow(study_idx)
            page = self._pages[study_idx]
            page.prefill(spec_text, product_type, failure_context)


GraphAssistantPanel = AssistantToolPanel