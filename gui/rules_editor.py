"""Диалог «Правила сопоставления» — настройка матчинга наименований без правки кода.

Qt-обёртка над src/approach_relevance (вся логика там, Qt-free). Пользователь
правит таблицы и проверяет результат на вкладке «Проверка»; сохранение пишет
config/matching_rules.yaml и применяется к текущей сессии.
"""

import copy
import logging

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from src import approach_relevance as ar

logger = logging.getLogger("pricer.rules.ui")


class RulesEditorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules = copy.deepcopy(ar.get_rules())
        self.setWindowTitle("Правила сопоставления")
        self.resize(780, 560)
        self.setMinimumSize(680, 480)
        self._build_ui()
        self._populate(self._rules)

    # ── UI construction ──────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        hint = QLabel(
            "Правила сопоставления наименований товаров. Правки из таблиц "
            "применяются к текущей сессии при нажатии «Проверить» или «Сохранить»; "
            "«Сохранить» дополнительно записывает их в конфиг. На вкладке "
            "«Проверка» можно убедиться, что два наименования совпадают как нужно."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.tabs = QTabWidget()
        self._build_tab_stopwords()
        self._build_tab_structural()
        self._build_tab_param()
        self._build_tab_abbrev()
        self._build_tab_context()
        self._build_tab_check()
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        btns = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setToolTip("Записать правила в config/matching_rules.yaml")
        self.save_btn.clicked.connect(self._on_save)
        btns.addWidget(self.save_btn)
        self.reload_btn = QPushButton("Перезагрузить")
        self.reload_btn.setToolTip("Прочитать правила из конфига (отбросить несохранённые правки)")
        self.reload_btn.clicked.connect(self._on_reload)
        btns.addWidget(self.reload_btn)
        self.reset_btn = QPushButton("Сбросить к встроенным")
        self.reset_btn.setToolTip("Вернуть встроенные правила по умолчанию")
        self.reset_btn.clicked.connect(self._on_reset)
        btns.addWidget(self.reset_btn)
        btns.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        root.addLayout(btns)

    def _add_row_controls(self, tab_layout, table):
        row = QHBoxLayout()
        add_btn = QPushButton("Добавить")
        add_btn.clicked.connect(lambda: self._append_row(table))
        del_btn = QPushButton("Удалить выделенные")
        del_btn.clicked.connect(lambda: self._remove_selected(table))
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        row.addStretch()
        tab_layout.addLayout(row)

    @staticmethod
    def _make_table(parent, headers):
        table = QTableWidget(0, len(headers), parent)
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        return table

    def _build_tab_stopwords(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Слова, не участвующие в сравнении (предлоги, служебные)."))
        self.stop_table = self._make_table(w, ["Слово"])
        self._add_row_controls(lay, self.stop_table)
        lay.addWidget(self.stop_table, 1)
        self.tabs.addTab(w, "Стоп-слова")

    def _build_tab_structural(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Слова, не доказывающие сходство («завод-изготовитель» и т.п.)."))
        self.struct_table = self._make_table(w, ["Слово"])
        self._add_row_controls(lay, self.struct_table)
        lay.addWidget(self.struct_table, 1)
        self.tabs.addTab(w, "Структурные")

    def _build_tab_param(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Параметры, которые могут отсутствовать в названии карточки (ру, kvs, бар...)."))
        self.param_table = self._make_table(w, ["Слово"])
        self._add_row_controls(lay, self.param_table)
        lay.addWidget(self.param_table, 1)
        self.tabs.addTab(w, "Параметры")

    def _build_tab_abbrev(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Сокращения: сокращение → полная форма (например «фл» → «фланцевый»)."))
        self.abbr_table = self._make_table(w, ["Сокращение", "Полная форма"])
        self._add_row_controls(lay, self.abbr_table)
        lay.addWidget(self.abbr_table, 1)
        self.tabs.addTab(w, "Сокращения")

    def _build_tab_context(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "Контекстные правила: если в наименовании встречается «Базовое наименование», "
            "то «Незначимая фраза» на совпадение не влияет."
        ))
        self.context_table = self._make_table(w, ["Базовое наименование", "Незначимая фраза"])
        self._add_row_controls(lay, self.context_table)
        lay.addWidget(self.context_table, 1)
        self.tabs.addTab(w, "Контекстные правила")

    def _build_tab_check(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(
            "Введите наименование из спецификации и наименование с карточки — результат "
            "по текущим правилам (правки из таблиц учитываются)."
        ))
        lay.addWidget(QLabel("Наименование из спецификации:"))
        self.check_spec = QLineEdit()
        lay.addWidget(self.check_spec)
        lay.addWidget(QLabel("Наименование с карточки:"))
        self.check_found = QLineEdit()
        lay.addWidget(self.check_found)
        check_btn = QPushButton("Проверить")
        check_btn.clicked.connect(self._on_check)
        lay.addWidget(check_btn)
        self.check_result = QLabel("")
        self.check_result.setWordWrap(True)
        lay.addWidget(self.check_result, 1)
        self.tabs.addTab(w, "Проверка")

    # ── table helpers ────────────────────────────────────────────
    @staticmethod
    def _append_row(table):
        row = table.rowCount()
        table.insertRow(row)
        for col in range(table.columnCount()):
            table.setItem(row, col, QTableWidgetItem(""))

    @staticmethod
    def _remove_selected(table):
        rows = sorted({i.row() for i in table.selectedItems()}, reverse=True)
        for r in rows:
            table.removeRow(r)

    def _collect(self) -> dict:
        def words(table):
            out = []
            for r in range(table.rowCount()):
                item = table.item(r, 0)
                val = (item.text() if item else "").strip()
                if val:
                    out.append(val)
            return out

        def pairs(table):
            out = []
            for r in range(table.rowCount()):
                a = (table.item(r, 0).text() if table.item(r, 0) else "").strip()
                b = (table.item(r, 1).text() if table.item(r, 1) else "").strip()
                if a:
                    out.append((a, b))
            return out

        rules = dict(ar._RULES_DEFAULTS)
        rules["stopwords"] = words(self.stop_table)
        rules["structural_words"] = words(self.struct_table)
        rules["param_words"] = words(self.param_table)
        rules["abbreviations"] = {a: b for a, b in pairs(self.abbr_table)}
        rules["context_insignificant"] = [
            {"base": a, "drop": b} for a, b in pairs(self.context_table)
        ]
        return rules

    @staticmethod
    def _fill_words(table, items):
        table.setRowCount(0)
        for val in items:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(str(val)))

    @staticmethod
    def _fill_pairs(table, items):
        table.setRowCount(0)
        for a, b in items:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(a))
            table.setItem(r, 1, QTableWidgetItem(b))

    def _populate(self, rules):
        self._fill_words(self.stop_table, rules.get("stopwords", []))
        self._fill_words(self.struct_table, rules.get("structural_words", []))
        self._fill_words(self.param_table, rules.get("param_words", []))
        abbr = rules.get("abbreviations", {})
        self._fill_pairs(self.abbr_table, [(k, v) for k, v in abbr.items()])
        ctx = rules.get("context_insignificant", [])
        self._fill_pairs(self.context_table, [(c.get("base", ""), c.get("drop", "")) for c in ctx])

    # ── actions ──────────────────────────────────────────────────
    def _apply_tables(self):
        ar.set_rules(self._collect())

    def _on_check(self):
        self._apply_tables()
        spec = self.check_spec.text()
        found = self.check_found.text()
        if not spec.strip() or not found.strip():
            self.check_result.setText("Заполните оба поля.")
            self.check_result.setStyleSheet("color: #ffb74d;")
            return
        if ar.product_name_matches(spec, found):
            self.check_result.setText("✓ Совпадает")
            self.check_result.setStyleSheet("color: #4caf50; font-weight: bold;")
        else:
            missing = ar.missing_required_tokens(spec, found)
            detail = f" Не хватает: {', '.join(missing)}." if missing else ""
            self.check_result.setText(f"✗ Не совпадает.{detail}")
            self.check_result.setStyleSheet("color: #f44336; font-weight: bold;")

    def _on_save(self):
        self._apply_tables()
        try:
            path = ar.save_rules()
        except Exception as e:
            logger.exception("Failed to save matching rules")
            self.status_label.setText(f"Не удалось сохранить: {e}")
            return
        self.status_label.setText(f"Сохранено: {path}")

    def _on_reload(self):
        self._rules = copy.deepcopy(ar.load_rules())
        self._populate(self._rules)
        self.status_label.setText("Правила перечитаны из конфига.")

    def _on_reset(self):
        ar.reset_rules()
        self._rules = copy.deepcopy(ar.get_rules())
        self._populate(self._rules)
        self.status_label.setText("Встроенные правила восстановлены.")
