"""
Material Symbols icons for PySide6 UI.

Single source for professional UI icons:
  - fonts  : register()  — loads MaterialSymbols_ui.ttf via QFontDatabase
  - glyphs : GLYPHS      — semantic key -> (PUA code, icon font codepoint)
  - render : make_pixmap / icon() — precise glyph -> QPixmap/QIcon
  - html   : span()      — inline <span> for QTextBrowser / rich text
  - text   : attach()    — put icon onto QPushButton/QToolButton/QCheckBox/QAction

The subset font is bundled at assets/fonts/MaterialSymbols_ui.ttf (Apache 2.0,
Material Symbols Outlined, static opsz=24, only the glyphs used in the app).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPixmap

_FONT_RELPATH = Path("assets") / "fonts" / "MaterialSymbols_ui.ttf"
_FONT_PATH = Path(__file__).resolve().parent.parent / _FONT_RELPATH

# ── Glyph table: semantic key -> 4-hex codepoint (Material Symbols Outlined) ──
GLYPHS: dict[str, str] = {
    "warning": "f083", "lightbulb": "e90f", "paid": "f041", "save": "e161",
    "refresh": "e5d5", "search": "ef7a", "check_circle": "f0be", "error": "f8b6",
    "cancel": "e888", "close": "e5cd", "info": "e88e", "block": "f08c",
    "upload": "f09b", "download": "f090", "photo_camera": "e412", "stop": "e047",
    "schedule": "efd6", "hourglass_empty": "e88b", "rocket_launch": "eb9b",
    "center_focus_strong": "e3b4", "home": "e9b2", "cleaning_services": "f0ff",
    "trending_down": "e8e3", "content_cut": "e14e", "map": "e55b",
    "straighten": "e41c", "bar_chart": "e26b", "keyboard": "e312",
    "repeat": "e040", "touch_app": "e913", "visibility_off": "e8f5",
    "folder_open": "e2c8", "recycling": "e760", "menu_book": "ea19",
    "database": "f20e", "settings_backup_restore": "e8ba", "play_circle": "e1c4",
    "pause_circle": "e1a2", "download_for_offline": "f000", "history": "e8b3",
    "api": "f1b7", "rate_review": "e560", "smartphone": "e7ba",
    "monitor": "ef5b", "speed": "e9e4", "query_stats": "e4fc",
    "build": "f8cd", "smart_toy": "f06c", "psychology": "ea4a",
    "settings": "e8b8", "inventory_2": "e1a1", "language": "ea07",
    "description": "e873", "edit_note": "e745", "bolt": "ea0b",
    "timer": "e425", "help": "e8fd", "forum": "e8af", "send": "e163",
    "extension": "e87b", "key": "e73c", "power": "e63c", "lock": "e899",
    "lock_open": "e898", "content_paste": "e14f", "notes": "e26c",
    "link": "e250", "cable": "efe6", "bookmark": "e8e7", "play_arrow": "e037",
    "pause": "e034", "stop_circle": "ef71", "construction": "ea3c",
    "memory": "e322", "science": "ea4b", "cell_tower": "ebba",
    "webhook": "eb92", "outbox": "ef5f", "inbox": "e156", "verified": "ef76",
    "fact_check": "f0c5", "rule": "f1c2", "assistant": "e39f",
    "wifi_tethering": "e1e5", "bug_report": "e868", "auto_awesome": "e65f",
    "local_fire_department": "ef55", "account_tree": "e35b", "open_in_new": "e89e",
    "check": "e5ca", "arrow_forward": "e5c8", "arrow_back": "e5c4",
    "expand_more": "e5cf", "expand_less": "e5ce", "more_horiz": "e5d3",
    "sort": "e164", "filter_list": "e152", "edit": "e3c9", "delete": "e872",
    "add": "e145", "remove": "e15b", "close_fullscreen": "f1cf",
    "fullscreen": "e5d0", "lock_clock": "ef80", "error_outline": "e001",
    "shopping_bag": "f1d2", "local_shipping": "e559", "sell": "f057",
    "receipt_long": "ef6a", "payments": "ea14", "credit_score": "f019",
    "point_of_sale": "f13c", "storefront": "ea12", "stacked_line_chart": "f1cd",
    "monitoring": "f190", "troubleshoot": "f13d", "approval": "e982",
    "task_alt": "e74a", "published_with_changes": "f0d2",
}

# ── Emoji -> glyph key mapping (for legacy log messages / HTML rendering) ──
EMOJI_MAP: dict[str, str] = {
    "⚠": "warning", "💡": "lightbulb", "💰": "paid", "💾": "save",
    "🔄": "refresh", "🔁": "repeat", "🔍": "search", "🔎": "search",
    "✅": "check_circle", "❌": "cancel", "🔧": "build", "🛠": "construction",
    "🤖": "smart_toy", "🤔": "psychology", "🧠": "psychology", "⚙": "settings",
    "📦": "inventory_2", "🌐": "language", "📄": "description", "📝": "edit_note",
    "⚡": "bolt", "⏱": "timer", "⏳": "hourglass_empty", "❓": "help",
    "💬": "forum", "📤": "upload", "📥": "download", "🧩": "extension",
    "🔑": "key", "🔌": "cable", "🔒": "lock", "🔓": "lock_open",
    "🚫": "block", "ℹ": "info", "📋": "content_paste", "📊": "bar_chart",
    "📉": "trending_down", "📈": "stacked_line_chart", "🔗": "link",
    "🚀": "rocket_launch", "🎯": "center_focus_strong", "⌨": "keyboard",
    "👆": "touch_app", "✂": "content_cut", "📸": "photo_camera",
    "🏠": "home", "📑": "bookmark", "🗺": "map", "♻": "recycling",
    "🧹": "cleaning_services", "📐": "straighten", "🔴": "error",
    "🟢": "check_circle", "⏹": "stop", "▶": "play_arrow", "📖": "menu_book",
    "🔟": "monitoring", "✖": "close", "✕": "close", "✗": "close",
    "📂": "folder_open", "🔢": "filter_list", "✓": "check",
}

# ── State ────────────────────────────────────────────────────────────────
_registered = False
_family: str | None = None
_pix_cache: dict[tuple, QPixmap] = {}

# Суперсэмплинг и паддинг — без обрезки глифа по краям.
_SS = 4
_PAD = 0.10


def font_path() -> Path:
    return _FONT_PATH


def register() -> str | None:
    """Регистрирует Material Symbols TTF в Qt. Безопасно вызывать многократно."""
    global _registered, _family
    if _registered:
        return _family
    if not _FONT_PATH.exists():
        return None
    try:
        fid = QFontDatabase.addApplicationFont(str(_FONT_PATH))
        if fid >= 0:
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                _family = fams[0]
        _registered = True
    except Exception:
        _registered = True  # не падать повторно
    return _family


def family() -> str | None:
    if not _registered:
        register()
    return _family


def text_color() -> str:
    """Цвет иконок в текущей теме (text-primary токен)."""
    try:
        from src.theme import Theme as _Th, TOKENS as _TK, detect_system_theme
        from src.config_loader import load_settings as _ls
        theme = (_ls().get("ui", {}).get("theme")) or detect_system_theme()
        return _TK.get(theme, _TK[_Th.DARK])["text-primary"]
    except Exception:
        return "#cdd6f4"


def _char(key: str) -> str:
    return chr(int(GLYPHS[key], 16))


def _require_font() -> bool:
    return family() is not None


def make_pixmap(key: str, color: str = "#e0e0e0", px: int = 20) -> QPixmap:
    """Рендерит глиф по границам QPainterPath — ничего не срезается."""
    if key not in GLYPHS:
        return QPixmap(px, px)
    fam = family()
    if not fam:
        return QPixmap(px, px)
    cache_key = (fam, key, color, px, _PAD)
    if cache_key in _pix_cache:
        return _pix_cache[cache_key]

    f = QFont(fam)
    f.setPixelSize(px * _SS)
    path = QPainterPath()
    path.addText(0, 0, f, _char(key))
    r = path.boundingRect()
    if r.isEmpty():
        pm = QPixmap(px, px)
        _pix_cache[cache_key] = pm
        return pm
    canvas = px * _SS
    target = canvas * (1 - 2 * _PAD)
    scale = target / max(r.width(), r.height())
    pm = QPixmap(canvas, canvas)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.translate(canvas / 2.0, canvas / 2.0)
    p.scale(scale, scale)
    p.translate(-r.center())
    p.fillPath(path, QColor(color))
    p.end()
    out = pm.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _pix_cache[cache_key] = out
    return out


def icon(key: str, color: str = "#e0e0e0", px: int = 20) -> QIcon:
    return QIcon(make_pixmap(key, color, px))


def attach(widget, key: str, color: str | None = None, px: int = 18):
    """Ставит QIcon на QPushButton/QToolButton/QCheckBox/QAction."""
    if widget is None or key not in GLYPHS:
        return
    widget.setIcon(icon(key, color or text_color(), px))
    widget.setIconSize(QSize(px, px))
    return widget


def glyph_text(key: str) -> str:
    """Сам PUA-символ — только для контекстов с явным font-family (rich text)."""
    return _char(key)


def span(key: str, px: int = 14, color: str | None = None) -> str:
    """HTML-спан глифа Material Symbols для QTextBrowser/QTextEdit rich text."""
    fam = family()
    if not fam or key not in GLYPHS:
        return ""
    color_attr = f'color:{color};' if color else ''
    return (f'<span style="font-family:\'{fam}\';font-size:{px}px;'
            f'vertical-align:middle;{color_attr}">&#x{GLYPHS[key]};</span>')


def replace_emojis(html_text: str, px: int = 13, color: str | None = None) -> str:
    """Заменяет эмодзи на HTML-спаны Material Symbols внутри уже экранированного текста."""
    fam = family()
    if not fam:
        return html_text
    color_attr = f'color:{color};' if color else ''
    out = []
    i = 0
    n = len(html_text)
    while i < n:
        ch = html_text[i]
        nxt = html_text[i + 1] if i + 1 < n else ""
        pair = ch + nxt if nxt in "\ufe0f\u200d" else ch
        key = EMOJI_MAP.get(pair) or EMOJI_MAP.get(ch)
        if key:
            out.append(f'<span style="font-family:\'{fam}\';font-size:{px}px;'
                       f'vertical-align:middle;{color_attr}">&#x{GLYPHS[key]};</span>')
            i += len(pair)
        else:
            out.append(ch)
            i += 1
    return "".join(out)
