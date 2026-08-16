"""
SiteOrderDialog — drag-and-drop редактор порядка сайтов.
"""
import logging
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QComboBox,
                               QLabel, QPushButton, QListWidget, QListWidgetItem,
                               QAbstractItemView, QFrame, QMessageBox, QInputDialog,
                               QTabWidget, QWidget)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor
from src.theme import Theme, TOKENS
from src.widget_base import paint_styled_background, setup_shadow
from src.category_router import (load_categories_cached, save_site_order)

logger = logging.getLogger(__name__)

from src._labels import _CAT_RU_LABELS, _SUBCAT_RU_LABELS


class SiteOrderDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор маппинга")
        self.setMinimumWidth(680)
        self.setMinimumHeight(600)
        self._tokens = TOKENS[Theme.DARK]
        setup_shadow(self, self._tokens)
        self._config = None
        self._cat_keys = []
        self._init_ui()
        self._load_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("Редактор маппинга")
        title.setStyleSheet(
            f"color: {self._tokens['accent']}; font-size: 15px; font-weight: 700; background: transparent;")
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setMaximumHeight(1)
        sep.setStyleSheet(
            f"color: {self._tokens['border']}; background-color: {self._tokens['border']}; border: none; max-height: 1px;")
        layout.addWidget(sep)

        # Tabs: только категории
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {self._tokens['border']}; border-radius: 6px; }}"
            f"QTabBar::tab {{ color: {self._tokens['text-primary']}; "
            f"background: {self._tokens['bg-surface']}; padding: 6px 16px; margin: 2px; border-radius: 4px; }}"
            f"QTabBar::tab:selected {{ background: {self._tokens['accent']}; color: {self._tokens['bg-primary']}; }}")

        # Tab 1: Categories
        self._cat_tab = QWidget()
        self._init_cat_tab()
        self.tabs.addTab(self._cat_tab, "Категории")

        layout.addWidget(self.tabs, stretch=1)

    def _init_cat_tab(self):
        lt = QVBoxLayout(self._cat_tab)
        lt.setSpacing(8)
        lt.setContentsMargins(4, 8, 4, 4)

        cat_row = QHBoxLayout()
        cat_label = QLabel("Категория:")
        cat_label.setStyleSheet(f"color: {self._tokens['text-primary']}; font-size: 12px; background: transparent;")
        cat_label.setFixedWidth(100)
        cat_row.addWidget(cat_label)
        self.cat_combo = QComboBox()
        self.cat_combo.setMinimumWidth(280)
        self.cat_combo.currentIndexChanged.connect(self._on_category_changed)
        cat_row.addWidget(self.cat_combo)
        lt.addLayout(cat_row)

        self.sub_row = QHBoxLayout()
        sub_label = QLabel("Подкатегория:")
        sub_label.setStyleSheet(f"color: {self._tokens['text-primary']}; font-size: 12px; background: transparent;")
        sub_label.setFixedWidth(100)
        self.sub_row.addWidget(sub_label)
        self.sub_combo = QComboBox()
        self.sub_combo.setMinimumWidth(280)
        self.sub_combo.currentIndexChanged.connect(self._on_subcategory_changed)
        self.sub_row.addWidget(self.sub_combo)
        self.sub_no_sublabel = QLabel("— общие сайты категории")
        self.sub_no_sublabel.setStyleSheet(
            f"color: {self._tokens.get('text-muted', '#6c7086')}; font-size: 12px; background: transparent;")
        self.sub_no_sublabel.setVisible(False)
        self.sub_row.addWidget(self.sub_no_sublabel)
        self.sub_row.addStretch()
        lt.addLayout(self.sub_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setMaximumHeight(1)
        sep.setStyleSheet(
            f"color: {self._tokens['border']}; background-color: {self._tokens['border']}; border: none; max-height: 1px;")
        lt.addWidget(sep)

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.MoveAction)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: {self._tokens['bg-primary']}; border: 1px solid {self._tokens['border']}; "
            f"border-radius: 6px; padding: 4px; font-size: 13px; }}"
            f"QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}"
            f"QListWidget::item:selected {{ background: {self._tokens['bg-hover']}; }}")
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        lt.addWidget(self.list_widget, stretch=1)

        hint = QLabel("☑ активные  ·  перетащите для изменения порядка  ·  ➕ добавьте / 🗑 удалите")
        hint.setStyleSheet(f"color: {self._tokens.get('text-muted', '#6c7086')}; font-size: 11px; background: transparent;")
        lt.addWidget(hint)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("➕ Добавить")
        self.add_btn.clicked.connect(self._on_add_site)
        btn_row.addWidget(self.add_btn)
        self.remove_btn = QPushButton("🗑 Удалить")
        self.remove_btn.clicked.connect(self._on_remove_site)
        btn_row.addWidget(self.remove_btn)
        self.select_all_btn = QPushButton("✓ Все")
        self.select_all_btn.clicked.connect(self._on_select_all)
        btn_row.addWidget(self.select_all_btn)
        self.deselect_all_btn = QPushButton("✗ Ничего")
        self.deselect_all_btn.clicked.connect(self._on_deselect_all)
        btn_row.addWidget(self.deselect_all_btn)
        self.reset_btn = QPushButton("Сбросить")
        self.reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.save_btn)
        lt.addLayout(btn_row)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_styled_background(self, painter, self._tokens)

    # ==========================================================================
    # CATEGORY TAB
    # ==========================================================================

    def _load_config(self):
        self._config = load_categories_cached()
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        self._cat_keys = self._config.get("priority_order", [])
        for cat_key in self._cat_keys:
            label = _CAT_RU_LABELS.get(cat_key, cat_key)
            self.cat_combo.addItem(label, cat_key)
        self.cat_combo.blockSignals(False)
        if self._cat_keys:
            self._on_category_changed(0)
            self.cat_combo.setCurrentIndex(0)

    def _on_category_changed(self, idx):
        if idx < 0 or idx >= len(self._cat_keys):
            return
        cat_key = self._cat_keys[idx]
        cat_data = self._config.get("category_map", {}).get(cat_key, {})
        subs = cat_data.get("subcategories", {})
        self.sub_combo.blockSignals(True)
        self.sub_combo.clear()
        if subs:
            self.sub_combo.setVisible(True)
            self.sub_no_sublabel.setVisible(False)
            sub_labels = _SUBCAT_RU_LABELS.get(cat_key, {})
            for sk in subs:
                label = sub_labels.get(sk, sk)
                self.sub_combo.addItem(label, sk)
            self.sub_combo.blockSignals(False)
            self._on_subcategory_changed(0)
            self.sub_combo.setCurrentIndex(0)
        else:
            self.sub_combo.setVisible(False)
            self.sub_no_sublabel.setVisible(True)
            self.sub_combo.blockSignals(False)
            self._load_sites(cat_key, None)

    def _on_subcategory_changed(self, idx):
        if idx < 0:
            return
        cat_key = self._cat_keys[self.cat_combo.currentIndex()]
        sub_key = self.sub_combo.currentData()
        self._load_sites(cat_key, sub_key)

    def _load_sites(self, cat_key, sub_key):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        current_raw = self._get_sites_list(cat_key, sub_key)
        in_category = set()
        for entry in current_raw:
            domain = entry.get("site", "")
            in_category.add(domain)
            enabled = entry.get("enabled", True)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, domain)
            item.setData(Qt.UserRole + 2, True)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            self.list_widget.addItem(item)
        all_domains = self._collect_all_sites()
        for domain in sorted(all_domains):
            if domain not in in_category:
                item = QListWidgetItem()
                item.setData(Qt.UserRole, domain)
                item.setData(Qt.UserRole + 2, False)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemIsEnabled)
                item.setCheckState(Qt.Unchecked)
                self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        self._update_item_visuals()

    def _collect_all_sites(self):
        seen = {}
        cat_map = self._config.get("category_map", {})
        for cat_data in cat_map.values():
            for entry in cat_data.get("sites", []):
                seen[entry["site"]] = True
            for sub_data in cat_data.get("subcategories", {}).values():
                for entry in sub_data.get("sites", []):
                    seen[entry["site"]] = True
        return sorted(seen.keys())

    def _get_sites_list(self, cat_key, sub_key):
        cat_data = self._config.get("category_map", {}).get(cat_key, {})
        if sub_key:
            return cat_data.get("subcategories", {}).get(sub_key, {}).get("sites", [])
        return cat_data.get("sites", [])

    def _on_rows_moved(self):
        self._update_item_visuals()

    def _on_item_changed(self, item):
        if item.checkState() == Qt.Checked and not item.data(Qt.UserRole + 2):
            item.setData(Qt.UserRole + 2, True)
        self._update_item_visuals()

    def _update_item_visuals(self):
        dimmed = "#585b70"
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            enabled = item.checkState() == Qt.Checked
            num = f"{i + 1}"
            item.setText(f"{num:>2}  {item.data(Qt.UserRole)}")
            item.setForeground(QColor(self._tokens['accent']) if enabled else QColor(dimmed))
        self.list_widget.repaint()

    def _on_select_all(self):
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Checked)
        self.list_widget.blockSignals(False)
        self._update_item_visuals()

    def _on_deselect_all(self):
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Unchecked)
        self.list_widget.blockSignals(False)
        self._update_item_visuals()

    def _on_add_site(self):
        domain, ok = QInputDialog.getText(self, "Добавить сайт", "Домен (например: new-site.ru):")
        if not ok or not domain.strip():
            return
        domain = domain.strip().lower()
        for i in range(self.list_widget.count()):
            existing = self.list_widget.item(i).data(Qt.UserRole)
            if existing == domain:
                self.list_widget.item(i).setCheckState(Qt.Checked)
                self._update_item_visuals()
                return
        item = QListWidgetItem()
        item.setData(Qt.UserRole, domain)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled | Qt.ItemIsEnabled)
        item.setCheckState(Qt.Checked)
        last_active = -1
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).checkState() == Qt.Checked:
                last_active = i
        self.list_widget.insertItem(last_active + 1, item)
        self._update_item_visuals()

    def _on_remove_site(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "Ошибка", "Выберите сайт.")
            return
        domain = item.data(Qt.UserRole)
        answer = QMessageBox.question(self, "Удаление", f"Убрать «{domain}» из категории?",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        item.setCheckState(Qt.Unchecked)
        self._update_item_visuals()

    def _on_reset(self):
        import importlib
        import src.category_router
        importlib.reload(src.category_router)
        self._load_config()

    def _on_save(self):
        if self.cat_combo.currentIndex() < 0:
            QMessageBox.warning(self, "Ошибка", "Не выбрана категория")
            return
        cat_key = self._cat_keys[self.cat_combo.currentIndex()]
        sub_key = self.sub_combo.currentData() if self.sub_combo.isVisible() else None
        ordered = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() != Qt.Checked:
                continue
            domain = item.data(Qt.UserRole)
            ordered.append((domain, True, "all"))
        if save_site_order(cat_key, sub_key or "", ordered):
            cat_ru = _CAT_RU_LABELS.get(cat_key, cat_key)
            sub_ru = _SUBCAT_RU_LABELS.get(cat_key, {}).get(sub_key, sub_key or "—")
            QMessageBox.information(self, "Сохранено", f"Порядок для «{cat_ru} / {sub_ru}» сохранён.")
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить файл.")
