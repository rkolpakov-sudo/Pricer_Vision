"""
Theme system for Pricer — dark & light themes with CSS variables.

Design principles:
- Flat, modern, minimal (inspired by VS Code, Postman, Tableau)
- HSL-based color tokens for easy maintainability
- Auto-detect system dark mode on Qt 6.5+
- Runtime theme switching without restart
"""

from PySide6.QtCore import Qt


# ── Color Tokens ──────────────────────────────────────────────────────────

class Theme:
    DARK = "dark"
    LIGHT = "light"


TOKENS = {
    Theme.DARK: {
        "bg-primary": "#1e1e2e",
        "bg-secondary": "#181825",
        "bg-surface": "#252536",
        "bg-hover": "#313244",
        "bg-active": "#45475a",
        "border": "#313244",
        "border-light": "#45475a",
        "text-primary": "#cdd6f4",
        "text-secondary": "#a6adc8",
        "text-muted": "#6c7086",
        "accent": "#89b4fa",
        "accent-hover": "#74c7ec",
        "accent-muted": "#45475a",
        "success": "#a6e3a1",
        "warning": "#f9e2af",
        "danger": "#f38ba8",
        "info": "#89dceb",
        "risk-low": "#a6e3a1",
        "risk-med": "#f9e2af",
        "risk-high": "#f38ba8",
        "scrollbar-bg": "#181825",
        "scrollbar-fg": "#45475a",
        "scrollbar-hover": "#585b70",
        "input-bg": "#313244",
        "input-border": "#45475a",
        "input-focus": "#89b4fa",
        "table-alt-row": "#252536",
        "table-selected": "#45475a",
        "progress-bar": "#89b4fa",
        "progress-bg": "#313244",
        "toast-bg": "#313244",
        "toast-border": "#45475a",
        "shadow": "rgba(0,0,0,0.3)",
    },
    Theme.LIGHT: {
        "bg-primary": "#d5d8e0",
        "bg-secondary": "#c8ccd6",
        "bg-surface": "#dde0e8",
        "bg-hover": "#c0c4ce",
        "bg-active": "#adb1bd",
        "border": "#a5a9b5",
        "border-light": "#b8bcc8",
        "text-primary": "#3a3c4a",
        "text-secondary": "#46485a",
        "text-muted": "#5a5c70",
        "accent": "#1a54d0",
        "accent-hover": "#1145a0",
        "accent-muted": "#c0c4d0",
        "success": "#358a24",
        "warning": "#c07d18",
        "danger": "#b00d30",
        "info": "#1a85a0",
        "risk-low": "#358a24",
        "risk-med": "#c07d18",
        "risk-high": "#b00d30",
        "scrollbar-bg": "#c8ccd6",
        "scrollbar-fg": "#a0a4b0",
        "scrollbar-hover": "#9094a2",
        "input-bg": "#eceef2",
        "input-border": "#a5a9b5",
        "input-focus": "#1a54d0",
        "table-alt-row": "#d0d4dc",
        "table-selected": "#c4c8d2",
        "progress-bar": "#1a54d0",
        "progress-bg": "#a5a9b5",
        "toast-bg": "#d5d8e0",
        "toast-border": "#a5a9b5",
        "shadow": "rgba(58,60,74,0.18)",
    },
}


def detect_system_theme():
    try:
        from PySide6.QtCore import QStyleHints
        hints = QStyleHints()
        if hasattr(hints, 'colorScheme'):
            return Theme.DARK if hints.colorScheme() == Qt.ColorScheme.Dark else Theme.LIGHT
    except Exception:
        pass
    return Theme.LIGHT


def build_stylesheet(theme_name: str) -> str:
    t = TOKENS.get(theme_name, TOKENS[Theme.LIGHT])

    return f"""
    /* ── Global ─────────────────────────────────────── */
    * {{
        font-family: "Segoe UI", "Roboto", "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
        font-size: 12px;
    }}
    QMainWindow, QWidget {{
        background-color: {t["bg-primary"]};
        color: {t["text-primary"]};
    }}

    QLabel {{
        color: {t["text-primary"]};
        background: transparent;
    }}
    QLabel[heading="true"] {{
        font-size: 16px;
        font-weight: 700;
        color: {t["text-primary"]};
        letter-spacing: -0.3px;
    }}
    QLabel[subheading="true"] {{
        font-size: 13px;
        font-weight: 600;
        color: {t["text-secondary"]};
    }}
    QLabel[muted="true"] {{
        font-size: 11px;
        color: {t["text-muted"]};
    }}

    /* ── Buttons ────────────────────────────────────── */
    QPushButton {{
        background-color: {t["bg-surface"]};
        color: {t["text-primary"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        padding: 6px 20px;
        font-size: 12px;
        font-weight: 500;
        min-height: 20px;
    }}
    QPushButton:hover {{
        background-color: {t["bg-hover"]};
        border-color: {t["border-light"]};
    }}
    QPushButton:pressed {{
        background-color: {t["bg-active"]};
        border-color: {t["accent"]};
    }}
    QPushButton:disabled {{
        color: {t["text-muted"]};
        background-color: {t["bg-secondary"]};
        border-color: {t["border"]};
    }}

    /* Primary — accent filled, main CTA */
    QPushButton#primary {{
        background-color: {t["accent"]};
        color: {"#1e1e2e" if theme_name == Theme.DARK else "#ffffff"};
        border: none;
        font-weight: 600;
        padding: 6px 22px;
    }}
    QPushButton#primary:hover {{
        background-color: {t["accent-hover"]};
    }}
    QPushButton#primary:pressed {{
        background-color: {t["accent"]};
    }}
    QPushButton#primary:disabled {{
        background-color: {t["accent-muted"]};
        color: {t["text-muted"]};
    }}

    /* Success — green filled, confirm / save */
    QPushButton#success {{
        background-color: {t["success"]};
        color: {"#1e1e2e" if theme_name == Theme.DARK else "#ffffff"};
        border: none;
        font-weight: 600;
        padding: 6px 22px;
    }}
    QPushButton#success:hover {{
        background-color: {t["accent-hover"]};
    }}
    QPushButton#success:disabled {{
        background-color: {t["accent-muted"]};
        color: {t["text-muted"]};
    }}

    /* Danger — ghost red border, fills on hover */
    QPushButton#danger {{
        color: {t["danger"]};
        border: 1px solid {t["danger"]};
        background-color: transparent;
    }}
    QPushButton#danger:hover {{
        background-color: {t["danger"]};
        color: {"#1e1e2e" if theme_name == Theme.DARK else "#ffffff"};
    }}
    QPushButton#danger:pressed {{
        background-color: {t["danger"]};
    }}
    QPushButton#danger:disabled {{
        color: {t["text-muted"]};
        border-color: {t["border"]};
    }}

    /* Warning — ghost amber border, fills on hover */
    QPushButton#warning {{
        color: {t["warning"]};
        border: 1px solid {t["warning"]};
        background-color: transparent;
    }}
    QPushButton#warning:hover {{
        background-color: {t["warning"]};
        color: {"#1e1e2e" if theme_name == Theme.DARK else "#ffffff"};
    }}
    QPushButton#warning:disabled {{
        color: {t["text-muted"]};
        border-color: {t["border"]};
    }}

    /* Ghost / flat — invisible until hover */
    QPushButton#ghost {{
        background-color: transparent;
        border: none;
        color: {t["text-secondary"]};
        padding: 4px 12px;
    }}
    QPushButton#ghost:hover {{
        background-color: {t["bg-hover"]};
        color: {t["text-primary"]};
    }}
    QPushButton#ghost:disabled {{
        color: {t["text-muted"]};
    }}

    /* Small icon button (e.g. "+" add) */
    QPushButton#small-btn {{
        padding: 1px 4px;
        min-height: 0px;
        font-size: 16px;
    }}
    QPushButton#small-btn:hover {{
        background-color: {t["bg-hover"]};
    }}

    /* Row action button (e.g. "🤖 Обучить" in results table) */
    QPushButton#row-action {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 5px;
        color: {t["accent"]};
        padding: 1px 8px;
        font-size: 12px;
        font-weight: 600;
        min-height: 18px;
    }}
    QPushButton#row-action:hover {{
        background-color: {t["bg-hover"]};
        border-color: {t["border-light"]};
        color: {t["accent"]};
    }}
    QPushButton#row-action:pressed {{
        background-color: {t["bg-active"]};
    }}

    /* ── Inputs ─────────────────────────────────────── */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background-color: {t["input-bg"]};
        color: {t["text-primary"]};
        border: 1px solid {t["input-border"]};
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 12px;
        min-height: 20px;
        selection-background-color: {t["accent"]};
        selection-color: #ffffff;
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {t["input-focus"]};
    }}
    QLineEdit:disabled {{
        background-color: {t["bg-secondary"]};
        color: {t["text-muted"]};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox::down-arrow {{
        width: 8px;
        height: 8px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {t["bg-surface"]};
        color: {t["text-primary"]};
        border: 1px solid {t["border"]};
        border-radius: 4px;
        selection-background-color: {t["bg-hover"]};
        padding: 2px;
    }}
    QComboBox QAbstractItemView::item {{
        padding: 4px 8px;
        border-radius: 3px;
    }}

    /* ── Tables ─────────────────────────────────────── */
    QTableWidget, QTableView {{
        background-color: {t["bg-primary"]};
        color: {t["text-primary"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        gridline-color: {t["border"]};
        alternate-background-color: {t["table-alt-row"]};
        font-size: 11px;
        outline: none;
    }}
    QTableWidget::item, QTableView::item {{
        padding: 5px 8px;
        border-bottom: 1px solid {t["border"]};
    }}
    QTableWidget::item:selected, QTableView::item:selected {{
        background-color: {t["table-selected"]};
        color: {t["text-primary"]};
    }}
    QTableWidget::item:hover, QTableView::item:hover {{
        background-color: {t["bg-hover"]};
    }}
    QHeaderView::section {{
        background-color: {t["bg-secondary"]};
        color: {t["text-secondary"]};
        padding: 7px 8px;
        border: none;
        border-bottom: 1px solid {t["border"]};
        border-right: 1px solid {t["border"]};
        font-weight: 600;
        font-size: 11px;
        letter-spacing: 0.5px;
    }}
    QHeaderView::section:hover {{
        background-color: {t["bg-hover"]};
    }}
    QHeaderView::section:last {{
        border-right: none;
    }}

    /* ── Progress Bar ───────────────────────────────── */
    QProgressBar {{
        background-color: {t["progress-bg"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        height: 14px;
        text-align: center;
        font-size: 10px;
        color: {t["text-muted"]};
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {t["progress-bar"]},
            stop:0.5 {t["progress-bar"]}dd,
            stop:1 {t["progress-bar"]});
        border-radius: 5px;
        margin: 1px;
    }}

    /* ── Scrollbars ─────────────────────────────────── */
    QScrollBar:vertical {{
        background: {t["scrollbar-bg"]};
        width: 8px;
        border: none;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {t["scrollbar-fg"]};
        min-height: 30px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t["scrollbar-hover"]};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: {t["scrollbar-bg"]};
        height: 8px;
        border: none;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {t["scrollbar-fg"]};
        min-width: 30px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {t["scrollbar-hover"]};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}

    /* ── GroupBox ───────────────────────────────────── */
    QGroupBox {{
        background-color: {t["bg-surface"]};
        border: 1px solid {t["border"]};
        border-radius: 8px;
        margin-top: 20px;
        padding: 16px 12px 12px 12px;
        font-size: 12px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 8px;
        color: {t["text-secondary"]};
        font-weight: 600;
        font-size: 11px;
        letter-spacing: 0.5px;
    }}

    /* ── Splitter ───────────────────────────────────── */
    QSplitter::handle {{
        background: transparent;
        width: 0px;
        height: 0px;
    }}

    /* ── Status Bar ─────────────────────────────────── */
    QStatusBar {{
        background-color: {t["bg-secondary"]};
        color: {t["text-secondary"]};
        border-top: 1px solid {t["border"]};
        font-size: 11px;
        padding: 2px 8px;
    }}
    QStatusBar::item {{
        border: none;
    }}

    /* ── Tooltips ───────────────────────────────────── */
    QToolTip {{
        background-color: {t["toast-bg"]};
        color: {t["text-primary"]};
        border: 1px solid {t["toast-border"]};
        border-radius: 4px;
        padding: 5px 10px;
        font-size: 11px;
    }}

    /* ── Text Edit / Browser ────────────────────────── */
    QTextEdit, QTextBrowser, QPlainTextEdit {{
        background-color: {t["bg-primary"]};
        color: {t["text-primary"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        padding: 4px;
        font-size: 11px;
        selection-background-color: {t["accent"]};
        selection-color: #ffffff;
    }}
    QTextEdit:focus, QTextBrowser:focus {{
        border-color: {t["input-focus"]};
    }}

    /* ── Checkbox ───────────────────────────────────── */
    QCheckBox {{
        spacing: 7px;
        color: {t["text-primary"]};
        font-size: 12px;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 3px;
        border: 1px solid {t["border"]};
        background: {t["input-bg"]};
    }}
    QCheckBox::indicator:checked {{
        background: {t["accent"]};
        border-color: {t["accent"]};
    }}
    QCheckBox::indicator:hover {{
        border-color: {t["accent"]};
    }}

    /* ── Menu ───────────────────────────────────────── */
    QMenu {{
        background-color: {t["bg-surface"]};
        color: {t["text-primary"]};
        border: 1px solid {t["border"]};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {t["bg-hover"]};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t["border"]};
        margin: 4px 8px;
    }}

    /* ── Dialog ─────────────────────────────────────── */
    QDialog {{
        background-color: {t["bg-primary"]};
    }}

    /* ── Tab Widget ─────────────────────────────────── */
    QTabWidget::pane {{
        border: 1px solid {t["border"]};
        border-radius: 0 0 6px 6px;
        background-color: {t["bg-primary"]};
    }}
    QTabBar::tab {{
        background-color: {t["bg-secondary"]};
        color: {t["text-secondary"]};
        padding: 6px 16px;
        border: 1px solid {t["border"]};
        border-bottom: none;
        border-top-left-radius: 5px;
        border-top-right-radius: 5px;
        font-size: 11px;
        font-weight: 500;
        min-width: 60px;
    }}
    QTabBar::tab:selected {{
        background-color: {t["bg-primary"]};
        color: {t["accent"]};
        font-weight: 600;
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {t["bg-hover"]};
    }}
    """


def apply_theme(app, theme_name: str):
    stylesheet = build_stylesheet(theme_name)
    app.setStyleSheet(stylesheet)
