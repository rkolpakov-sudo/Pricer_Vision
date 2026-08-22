"""Геометрический парсер ГОСТ-формы «Спецификация оборудования…».

Текстовые экстракторы сплющивают вертикально объединённые ячейки формы
(~98% позиций реальных спецификаций), поэтому разбор идёт на уровне
координат каждого текстового элемента:

  1) детект сетки колонок по заголовку страницы (маркеры «1»..«9»);
  2) кластеризация элементов в строки по Y;
  3) привязка элементов к колонкам по X-центру;
  4) сборка позиций: строка с маркером «NNN.» открывает позицию,
     строки без маркера продолжают предыдущую.

Колонки формы: Позиция | Наименование | Тип, марка | Код | Завод |
Единица | Количество | Масса | Примечание.
"""
import bisect
import logging
import re
import statistics

from src.pdf_parser.fast_backend import AVAILABLE, _pi
from src.pdf_parser.structurer import (
    SpecStructurer,
    _UNIT_CELL_RE,
    _STAMP_RE,
)

logger = logging.getLogger("pricer.pdf.gost")

# Якорные префиксы заголовков колонок (в нижнем регистре).
_COLUMN_ANCHORS = [
    ("pos", "позиция"),
    ("name", "наименование"),
    ("spec", "тип"),
    ("code", "код"),
    ("manufacturer", "завод"),
    ("unit", "единица"),
    ("qty", "коли"),
    ("weight", "масса"),
    ("note", "примечание"),
]

_POS_CELL_RE = re.compile(r"^\.?\s*(\d{1,4})\s*\.?,?\s*$")
_POS_GLUED_RE = re.compile(r"^\s*\.?\s*(\d{1,4})\.\s+(.+)$")
_JUNK_TOKENS = {"и", "№", "н"}


class GostFormParser:
    """Разбор позиций из PDF с ГОСТ-формой по координатам текста."""

    def __init__(self):
        self._struct = SpecStructurer(llm_client=None)

    def available(self) -> bool:
        return AVAILABLE

    # ── Публичный API ────────────────────────────────────────────
    def parse(self, pdf_path: str):
        """-> (items, markers_seen) или None, если форма не обнаружена.

        items — тот же контракт, что у SpecStructurer.
        markers_seen — сколько уникальных номеров позиций найдено в колонке 1
        (для контроля полноты покрытия).
        """
        if not AVAILABLE:
            return None
        try:
            raw = _pi.extract_text_with_positions(str(pdf_path))
        except Exception as e:  # noqa: BLE001
            logger.warning("gost parser: extraction failed: %s", e)
            return None

        pages: dict[int, list] = {}
        for it in raw:
            if it.text.strip():
                pages.setdefault(it.page, []).append(it)
        if not pages:
            return None

        grid = None
        items: list[dict] = []
        seen: set[int] = set()

        for page in sorted(pages):
            bands = self._cluster_bands(pages[page])
            found = self._detect_grid(bands)
            if found:
                # Заголовок СОСУЩЕСТВУЕТ с данными на той же странице —
                # сетку обновляем, но строки страницы не пропускаем.
                grid = found
            if not grid:
                continue
            page_items = self._assemble_rows(bands, grid, items, seen)
            items.extend(page_items)

        items = [it for it in items if re.search(r"[А-Яа-яA-Za-z]{2}", it["name"])]
        if not items:
            return None
        logger.info("gost parser: %d items, %d position markers", len(items), len(seen))
        return items, len(seen)

    # ── Кластеризация по Y ───────────────────────────────────────
    def _cluster_bands(self, items: list) -> list[list]:
        fs = statistics.median([i.font_size for i in items]) or 10.0
        tol = max(8.0, 0.9 * float(fs))
        ordered = sorted(items, key=lambda i: (-i.y, i.x))
        bands: list[list] = []
        cur: list = []
        last_y = None
        for it in ordered:
            if last_y is not None and abs(last_y - it.y) <= tol:
                cur.append(it)
            else:
                if cur:
                    bands.append(cur)
                cur = [it]
            last_y = it.y
        if cur:
            bands.append(cur)
        return [sorted(b, key=lambda i: i.x) for b in bands]

    # ── Детект сетки колонок ─────────────────────────────────────
    def _detect_grid(self, bands: list[list]) -> list[float] | None:
        """Маркеры «1»..«9» в одной строке -> границы (середины соседних X)."""
        for band in bands:
            marks = []
            for it in band:
                t = it.text.strip()
                if re.fullmatch(r"[1-9]", t):
                    marks.append((it.x, int(t)))
            if len(marks) >= 6:
                marks.sort()
                vals = [v for _, v in marks]
                if all(vals[k] < vals[k + 1] for k in range(len(vals) - 1)):
                    xs = [x for x, _ in marks]
                    return [(xs[k] + xs[k + 1]) / 2 for k in range(len(xs) - 1)]
        return self._grid_from_titles(bands)

    def _grid_from_titles(self, bands: list[list]) -> list[float] | None:
        anchors: dict[str, float] = {}
        for band in bands:
            for it in band:
                t = it.text.strip().lower()
                for key, prefix in _COLUMN_ANCHORS:
                    if key not in anchors and t.startswith(prefix):
                        anchors[key] = it.x
                        break
        if len(anchors) < 6:
            return None
        ordered = sorted(anchors.items(), key=lambda kv: kv[1])
        xs = [x for _, x in ordered]
        return [(xs[k] + xs[k + 1]) / 2 for k in range(len(xs) - 1)]

    # ── Сборка строк ─────────────────────────────────────────────
    def _clean_cell(self, band_item_group: list) -> str:
        parts = [_STAMP_RE.sub("", it.text.strip()).strip()
                 for it in sorted(band_item_group, key=lambda i: i.x)]
        txt = " ".join(p for p in parts if p)
        while True:
            head = txt.split()[0] if txt.split() else ""
            if head and head.lower() in _JUNK_TOKENS:
                txt = txt[len(head):].strip()
            else:
                break
        return txt

    def _band_cells(self, band: list, bounds: list[float]) -> list[str]:
        cells: list[list] = [[] for _ in range(len(bounds) + 1)]
        for it in band:
            cx = it.x + (it.width or 0) / 2
            idx = bisect.bisect_right(bounds, cx)
            cells[idx].append(it)
        return [self._clean_cell(g) for g in cells]

    def _assemble_rows(self, bands: list[list], bounds: list[float],
                       items: list[dict], seen: set[int]) -> list[dict]:
        built: list[dict] = []
        prev = items[-1] if items else None

        for band in bands:
            cells = self._band_cells(band, bounds)
            if not any(cells) or self._is_header_band(cells):
                continue

            pos = None
            m = _POS_CELL_RE.match(cells[0])
            if m:
                pos = int(m.group(1))
            else:
                gm0 = _POS_GLUED_RE.match(cells[0])
                if gm0:
                    # Клей «109. Цилиндр…» целиком в первой колонке
                    pos = int(gm0.group(1))
                    rest = gm0.group(2).strip()
                    cells[1] = f"{rest} {cells[1]}".strip() if len(cells) > 1 else rest
                elif len(cells) > 1:
                    gm = _POS_GLUED_RE.match(cells[1])
                    if gm:
                        pos = int(gm.group(1))
                        cells[1] = gm.group(2)

            if pos is None:
                # Продолжение предыдущей позиции (перенос имени и т.п.)
                extra = cells[1] if len(cells) > 1 else ""
                if prev is not None and extra and re.search(r"[А-Яа-яA-Za-z]{2}", extra):
                    prev["name"] = f"{prev['name']} {extra}".strip()
                continue

            seen.add(pos)
            name = cells[1].strip() if len(cells) > 1 else ""
            spec = cells[2] if len(cells) > 2 else ""
            code = cells[3] if len(cells) > 3 else ""
            manuf = cells[4].strip('" ').strip() if len(cells) > 4 else ""
            unit_c = cells[5] if len(cells) > 5 else ""
            qty_c = cells[6] if len(cells) > 6 else ""
            weight_c = cells[7] if len(cells) > 7 else ""

            unit_s, qty_s = self._split_unit_qty(unit_c, qty_c)
            item = {
                "pos": pos,
                "name": name,
                "specs": spec,
                "code": code,
                "manufacturer": manuf,
                "qty": self._struct._to_float(qty_s),
                "unit": unit_s.lower().rstrip("."),
                "weight": self._struct._to_float(weight_c),
                "requires_review": False,
            }
            built.append(item)
            prev = item

        return built

    def _is_header_band(self, cells: list[str]) -> bool:
        """Строка заголовка формы («Позиция | Наименование…» / «1 | 2 | 3…»)."""
        joined = " ".join(c.lower() for c in cells)
        if sum(1 for _, p in _COLUMN_ANCHORS if p in joined) >= 3:
            return True
        nonempty = [c for c in cells if c]
        return bool(nonempty) and all(re.fullmatch(r"[1-9]", c) for c in nonempty)

    @staticmethod
    def _split_unit_qty(unit_c: str, qty_c: str) -> tuple[str, str]:
        """Единица и количество из объединённых/раздельных ячеек."""
        unit_s, qty_s = "", ""
        uq = SpecStructurer._match_unit_qty(unit_c)
        if uq:
            unit_s, qty_s = uq
        elif unit_c and _UNIT_CELL_RE.match(unit_c):
            unit_s = unit_c
        if qty_c:
            mq = SpecStructurer._match_unit_qty(qty_c)
            if mq:
                unit_s = unit_s or mq[0]
                qty_s = qty_s or mq[1]
            elif not qty_s:
                qty_s = qty_c
        return unit_s, qty_s