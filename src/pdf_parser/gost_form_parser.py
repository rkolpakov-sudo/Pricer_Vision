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
# Клей «105. Труба…» и «1008.Вентилятор» (пробел после точки необязателен,
# следующий символ — буква/кавычка/скобка).
_POS_GLUED_RE = re.compile(
    r"^\s*\.?\s*(\d{1,4})\.(?:\s+|(?=[А-Яа-яA-Za-z(\"]))(.*)$"
)
_JUNK_TOKENS = {"и", "№", "н"}


class GostFormParser:
    """Разбор позиций из PDF с ГОСТ-формой по координатам текста."""

    def __init__(self):
        self._struct = SpecStructurer(llm_client=None)
        self._last_group_name = ""

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
        self._last_group_name = ""
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

        # Позиции без распознанного имени не выбрасываем — помечаем на проверку
        for it in items:
            if not re.search(r"[А-Яа-яA-Za-z]{2}", it["name"]):
                it["requires_review"] = True
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
    # Колонки формы по номеру маркера (1..9).
    _COL_KEYS = ["pos", "name", "spec", "code", "manufacturer",
                 "unit", "qty", "weight", "note"]

    def _detect_grid(self, bands: list[list]) -> tuple[list[float], list[str]] | None:
        """Маркеры «1»..«9» в одной строке -> (границы X, семантика ячеек).

        Страница может быть повёрнута: маркеры идут по X и по возрастанию
        значения (обычная форма), и по убыванию (зеркальная). Семантика
        каждого столбца определяется ЗНАЧЕНИЕМ маркера, а не позицией.
        """
        for band in bands:
            marks = []
            for it in band:
                t = it.text.strip()
                if re.fullmatch(r"[1-9]", t):
                    marks.append((it.x, int(t)))
            if len(marks) >= 6:
                marks.sort()
                vals = [v for _, v in marks]
                asc = all(vals[k] < vals[k + 1] for k in range(len(vals) - 1))
                desc = all(vals[k] > vals[k + 1] for k in range(len(vals) - 1))
                if asc or desc:
                    xs = [x for x, _ in marks]
                    bounds = [(xs[k] + xs[k + 1]) / 2 for k in range(len(xs) - 1)]
                    keys = [self._COL_KEYS[v - 1] for _, v in marks]
                    return bounds, keys
        return self._grid_from_titles(bands)

    def _grid_from_titles(self, bands: list[list]):
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
        bounds = [(xs[k] + xs[k + 1]) / 2 for k in range(len(xs) - 1)]
        keys = [k for k, _ in ordered]
        return bounds, keys

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

    def _band_cells(self, band: list, grid) -> dict[str, str]:
        bounds, keys = grid
        last_b = bounds[-1] if bounds else float("inf")
        cells: list[list] = [[] for _ in range(len(bounds) + 1)]
        for it in band:
            w = it.width or 0.0
            # На повёрнутых страницах ширина текста отсчитывается в обратную
            # сторону: элемент «вылезает» за последнюю границу сетки. Тогда его
            # фактический отрезок — [x-w, x].
            if w > 30 and it.x + w > last_b + 60:
                lo, hi = it.x - w, it.x
            else:
                lo, hi = it.x, it.x + w
            best, best_ov = 0, -1.0
            prev_b = float("-inf")
            for k, b in enumerate(list(bounds) + [float("inf")]):
                ov = min(hi, b) - max(lo, prev_b)
                if ov > best_ov:
                    best, best_ov = k, ov
                prev_b = b
            cells[best].append(it)
        return {keys[i]: self._clean_cell(g) for i, g in enumerate(cells)}

    def _assemble_rows(self, bands: list[list], grid,
                       items: list[dict], seen: set[int]) -> list[dict]:
        built: list[dict] = []
        prev = items[-1] if items else None

        for band in bands:
            cells = self._band_cells(band, grid)
            if not any(cells.values()) or self._is_header_band(cells):
                continue

            c_pos = cells.get("pos", "")
            c_name = cells.get("name", "")
            pos = None
            m = _POS_CELL_RE.match(c_pos)
            if m:
                pos = int(m.group(1))
            else:
                gm0 = _POS_GLUED_RE.match(c_pos)
                if gm0:
                    # Клей «109. Цилиндр…» целиком в колонке «Позиция»
                    pos = int(gm0.group(1))
                    rest = gm0.group(2).strip()
                    c_name = f"{rest} {c_name}".strip() if c_name else rest
                elif c_name:
                    gm = _POS_GLUED_RE.match(c_name)
                    if gm:
                        pos = int(gm.group(1))
                        c_name = gm.group(2)

            if pos is None:
                # Продолжение предыдущей позиции (перенос имени и т.п.)
                extra = c_name
                if prev is not None and extra and re.search(r"[А-Яа-яA-Za-z]{2}", extra):
                    prev["name"] = f"{prev['name']} {extra}".strip()
                continue

            seen.add(pos)
            name = c_name.strip()
            spec = cells.get("spec", "")
            code = cells.get("code", "")
            manuf = cells.get("manufacturer", "").strip('" ').strip()
            unit_c = cells.get("unit", "")
            qty_c = cells.get("qty", "")
            weight_c = cells.get("weight", "")

            unit_s, qty_s = self._split_unit_qty(unit_c, qty_c)

            # Семантика ГОСТ-формы: у вариантов внутри группы имя в объединённой
            # ячейке пустое — наследуем последнее увиденное имя группы.
            review = False
            if not re.search(r"[А-Яа-яA-Za-z]{2}", name):
                if self._last_group_name:
                    name = self._last_group_name
                else:
                    review = True
            else:
                self._last_group_name = name

            item = {
                "pos": pos,
                "name": name,
                "specs": spec,
                "code": code,
                "manufacturer": manuf,
                "qty": self._struct._to_float(qty_s),
                "unit": unit_s.lower().rstrip("."),
                "weight": self._struct._to_float(weight_c),
                "requires_review": review,
            }
            built.append(item)
            prev = item

        return built

    def _is_header_band(self, cells: dict[str, str]) -> bool:
        """Строка заголовка формы («Позиция | Наименование…» / «1 | 2 | 3…»)."""
        values = [c for c in cells.values()]
        joined = " ".join(values).lower()
        if sum(1 for _, p in _COLUMN_ANCHORS if p in joined) >= 3:
            return True
        nonempty = [c for c in values if c]
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