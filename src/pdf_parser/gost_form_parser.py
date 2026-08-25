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
# Клей без точки: «6 DN57х2,5» — номер позиции пробелом отделён от имени.
_POS_SPACE_RE = re.compile(r"^\s*(\d{1,4})\s+([А-ЯA-Z].*)$")
# Клей без пробела: «20Рулон», «22Зажимы» — цифра сразу перед заглавной буквой.
_POS_DIRECT_RE = re.compile(r"^\s*(\d{1,4})([А-ЯA-Z].*)$")
_JUNK_TOKENS = {"и", "№", "н"}


def _is_section_title(text: str) -> bool:
    """Заголовок раздела: «СИСТЕМА ОТОПЛЕНИЯ» (только ЗАГЛАВНЫЕ) или
    короткая фраза с ключевым словом раздела («Хозяйственно бытовая
    канализация»). Короткие названия товаров не трогаем."""
    s = text.strip()
    if not s or not re.search(r"[А-Яа-яA-Za-z]{2}", s):
        return True
    if len(s) <= 40 and not re.search(r"[а-яa-z]", s):
        return True
    words = s.split()
    if len(words) <= 4:
        for kw in ("канализац", "водопровод", "отоплени", "вентиляц",
                   "кондиционирован", "сеть", "система"):
            if kw in s.lower():
                return True
    return False


def _significant_tokens(text: str) -> set[str]:
    """Значимые слова (≥4 симв., буквы), без стоп-слов/размерных токенов."""
    import re as _re
    words = _re.findall(r"[А-Яа-яA-Za-z]{4,}", text.lower())
    stop = {"типа", "тип", "марка", "включ", "компл", "креп", "прибор",
            "стандарт", "проход", "систем", "режим", "работ", "подключ"}
    return {w for w in words if w not in stop}


def _is_bare_variant(name: str) -> bool:
    """Имя — «голый» вариант: только размер/тип/ду, без существительного
    товара («DN15», «500x400», «М30х1,5», «PN 2,5»)."""
    s = name.strip()
    if not s:
        return True
    # существительные товара (длинные слова) отсутствуют
    words = re.findall(r"[А-Яа-яA-Za-z]{4,}", s)
    if not words:
        return True   # только размеры/цифры
    return False


def _apply_mothers(items: list[dict], mothers) -> None:
    """Наследование материнских имён в порядке чтения документа.

    Позиция наследует имя матери, только если она — её вариант:
      - имя «голое» (размер/тип без существительного), или
      - делит значимый токен с именем матери («LEMAX», «МС-140»).
    Иначе это самостоятельный товар — группа матери заканчивается.
    """
    if not mothers or not items:
        return

    events: list[tuple[int, float, int, object]] = []
    for row in items:
        events.append((row["_page"], -row["_y"], 1, row))
    for pg, y, txt in mothers:
        events.append((pg, -y, 0, txt))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    cur: str | None = None
    cur_tokens: set[str] = set()
    last_kind = None
    for _pg, _ny, kind, payload in events:
        if kind == 0:
            txt = str(payload).strip()
            if cur is not None and last_kind == 0:
                cur = f"{cur} {txt}".strip()          # продолжение той же ячейки
                cur_tokens |= _significant_tokens(txt)
            else:
                cur = txt
                cur_tokens = _significant_tokens(txt)
            last_kind = 0
            continue
        last_kind = 1
        row = payload
        name = (row.get("name") or "").strip()
        if not name or not cur:
            continue
        if not re.search(r"[А-Яа-яA-Za-z]{2}", name):
            row["name"] = cur
            continue
        # Вариант матери: голый размер/тип ИЛИ общий значимый токен
        is_variant = _is_bare_variant(name) or bool(
            _significant_tokens(name) & cur_tokens)
        if is_variant:
            if not name.startswith(cur[:25]):
                row["name"] = f"{cur} {name}".strip()
        else:
            cur = None          # самостоятельный товар — группа матери окончена
            cur_tokens = set()


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
        self._pending_parent = ""
        self._synth_next = None
        self._seen_first_marker = False
        self._accumulating_mother = False
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
        mothers: list[tuple[float, str]] = []   # (y, объединённое имя группы)

        for page in sorted(pages):
            probe = self._cluster_bands(pages[page])
            found = self._detect_grid(probe)
            if found:
                # Заголовок СОСУЩЕСТВУЕТ с данными на той же странице —
                # сетку обновляем, но строки страницы не пропускаем.
                grid = found
            if not grid:
                continue
            # Приоритет: разбивка строк по якорям колонки «Позиция» —
            # устойчива к цепному слиянию соседних строк при кластеризации.
            bands = self._split_by_anchors(pages[page], grid) \
                or self._cluster_bands(pages[page])
            page_items, page_mothers = self._assemble_rows(
                bands, grid, items, seen, page=page)
            items.extend(page_items)
            mothers.extend(page_mothers)

        # ── Глобальное разрешение материнских имён ────────────────
        # Мать может состоять из нескольких визуальных строк (объединённая
        # ячейка) и находиться в середине/начале группы; группы переходят
        # между страницами. Разбиваем страницу на группы «мать + её строки»
        # и наследуем имя матери всем строкам группы.
        items.sort(key=lambda i: i.get("_y", 0), reverse=True)
        mothers.sort(key=lambda m: m[0], reverse=True)
        _apply_mothers(items, mothers)

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

    def _detect_grid(self, bands: list[list]) -> tuple[list[float], list[str], bool] | None:
        """Маркеры «1»..«9» в одной строке -> (границы X, семантика, зеркальность).

        Страница может быть повёрнута: маркеры идут по X и по возрастанию
        значения (обычная форма), и по убыванию (зеркальная). Семантика
        каждого столбца определяется ЗНАЧЕНИЕМ маркера, а не позицией;
        на зеркальных страницах чтение внутри ячейки идёт по убыванию X.
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
                    return {"bounds": bounds, "keys": keys, "mirrored": desc,
                            "mx_min": min(xs), "mx_max": max(xs)}
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
        # Нет цифр-маркеров: границы по серединам между X заголовков
        # (X текста заголовка ≈ центр его колонки).
        bounds = [(xs[k] + xs[k + 1]) / 2 for k in range(len(xs) - 1)]
        keys = [k for k, _ in ordered]
        mirrored = keys.index("pos") > len(keys) / 2
        return {"bounds": bounds, "keys": keys, "mirrored": mirrored,
                "mx_min": min(xs), "mx_max": max(xs)}

    # ── Разбивка строк по якорям колонки «Позиция» ───────────────
    def _split_by_anchors(self, items: list, grid) -> list[list] | None:
        """Каждый маркер позиции задаёт Y-якорь строки; элементы страницы
        прикрепляются к ближайшему якорю. Исключает цепное слияние соседних
        строк при обычной кластеризации и отбрасывает штампы вне таблицы."""
        if grid is None:
            return None
        bounds, keys = grid["bounds"], grid["keys"]
        mx_min, mx_max = grid["mx_min"], grid["mx_max"]
        mirrored = grid["mirrored"]
        pos_i = keys.index("pos")
        p_lo = bounds[pos_i - 1] if pos_i > 0 else float("-inf")
        p_hi = bounds[pos_i] if pos_i < len(bounds) else float("inf")

        def outside(it):
            # Штампы рамки: на зеркальных страницах правее таблицы,
            # на обычных — левее.
            if mirrored:
                return it.x > mx_max + 40
            return (it.x + (it.width or 0)) < mx_min - 40

        markers = [it for it in items
                   if re.fullmatch(r"\d{1,4}", it.text.strip())
                   and p_lo <= it.x <= p_hi]
        if len(markers) < 2:
            return None
        raw_ys = sorted((m.y for m in markers), reverse=True)
        anchors: list[float] = []
        for y in raw_ys:
            if not anchors or abs(anchors[-1] - y) > 8:
                anchors.append(y)
        gaps = [anchors[k] - anchors[k + 1] for k in range(len(anchors) - 1)
                if anchors[k] - anchors[k + 1] > 0]
        cap = (min(gaps) * 0.45) if gaps else 12.0

        bands: list[list] = [[] for _ in anchors]
        unassigned: list = []
        name_i = keys.index("name") if "name" in keys else -1
        for it in items:
            if outside(it):
                continue
            k = min(range(len(anchors)), key=lambda i: abs(anchors[i] - it.y))
            if abs(anchors[k] - it.y) <= cap:
                bands[k].append(it)
            elif name_i >= 0:
                # Имена-строки между якорями (материнские строки групп).
                # Принимаем ТОЛЬКО текст из колонки «Наименование», чтобы
                # не тащить данные весов/штампов между строк.
                w = it.width or 0.0
                if w > 30 and it.x + w > bounds[-1] + 60:
                    lo, hi = it.x - w, it.x
                else:
                    lo, hi = it.x, it.x + w
                best, best_ov = -1, -1.0
                prev_b = float("-inf")
                for kk, bb in enumerate(list(bounds) + [float("inf")]):
                    ov = min(hi, bb) - max(lo, prev_b)
                    if ov > best_ov:
                        best, best_ov = kk, ov
                    prev_b = bb
                if best == name_i and re.search(r"[А-Яа-яA-Za-z]{2}", it.text):
                    unassigned.append(it)
        out: list[list] = [sorted(b, key=lambda i: i.x) for b in bands if b]
        if unassigned:
            # Группируем нераспределённые имена по Y в отдельные полосы
            # и вставляем в общий поток по вертикальному порядку.
            un = sorted(unassigned, key=lambda i: (-i.y, i.x))
            mini: list[list] = []
            cur: list = []
            last_y = None
            for it in un:
                if last_y is not None and abs(last_y - it.y) <= 8:
                    cur.append(it)
                else:
                    if cur:
                        mini.append(cur)
                    cur = [it]
                last_y = it.y
            if cur:
                mini.append(cur)
            # объединяем: идём по всем полосам сверху вниз
            merged: list[list] = []
            pooled = [sorted(s, key=lambda i: i.x) for s in mini] + out
            pooled.sort(key=lambda b: min(i.y for i in b), reverse=True)
            merged = pooled
            out = merged
        return out

    # ── Сборка строк ─────────────────────────────────────────────
    def _clean_cell(self, band_item_group: list, reverse: bool = False) -> str:
        parts = [_STAMP_RE.sub("", it.text.strip()).strip()
                 for it in sorted(band_item_group, key=lambda i: i.x,
                                  reverse=reverse)]
        txt = " ".join(p for p in parts if p)
        while True:
            head = txt.split()[0] if txt.split() else ""
            if head and head.lower() in _JUNK_TOKENS:
                txt = txt[len(head):].strip()
            else:
                break
        return txt

    def _band_cells(self, band: list, grid) -> dict[str, str]:
        bounds, keys = grid["bounds"], grid["keys"]
        mirrored = grid["mirrored"]
        cells: list[list] = [[] for _ in range(len(bounds) + 1)]
        for it in band:
            w = it.width or 0.0
            if w <= 30:
                # Узкие элементы (номера позиций, кол-ва): колонка по НАЧАЛУ X —
                # перекрытие выкидывает короткий маркер, задевающий границу,
                # в соседнюю колонку.
                idx = bisect.bisect_right(bounds, it.x)
                cells[idx].append(it)
                continue
            # Широкие: повёрнутая ширина может отсчитываться в обратную сторону,
            # назначение по максимальному перекрытию отрезка.
            if it.x + w > bounds[-1] + 60:
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
        return {keys[i]: self._clean_cell(g, reverse=mirrored)
                for i, g in enumerate(cells)}

    def _assemble_rows(self, bands: list[list], grid,
                       items: list[dict], seen: set[int], page: int = 0):
        """-> (строки, матери). Материнские имена НЕ применяются здесь —
        они разрешаются глобально в parse() (группы переходят между
        страницами, мать может стоять в середине группы по Y)."""
        built: list[dict] = []
        mothers: list[tuple[int, float, str]] = []   # (page, y, имя)
        synth_next = getattr(self, "_synth_next", None)
        if synth_next is None:
            synth_next = max(seen or {0}) + 1000
            self._synth_next = synth_next

        for band in bands:
            cells = self._band_cells(band, grid)
            if not any(cells.values()) or self._is_header_band(cells):
                continue

            c_pos = cells.get("pos", "")
            c_name = cells.get("name", "")
            c_qty = cells.get("qty", "")
            c_unit = cells.get("unit", "")
            row_y = min(i.y for i in band)

            # Позиция из всех форм клея
            pos = None
            m = _POS_CELL_RE.match(c_pos)
            if m:
                pos = int(m.group(1))
            else:
                # Клей в pos-ячейке: «30 Хомут ∅100», «109. Цилиндр…»
                gm0 = _POS_GLUED_RE.match(c_pos)
                sp0 = None if gm0 else _POS_SPACE_RE.match(c_pos)
                dr0 = None if (gm0 or sp0) else _POS_DIRECT_RE.match(c_pos)
                if gm0:
                    pos = int(gm0.group(1))
                    rest = gm0.group(2).strip()
                    c_name = f"{rest} {c_name}".strip() if c_name else rest
                elif sp0:
                    pos = int(sp0.group(1))
                    rest = sp0.group(2).strip()
                    c_name = f"{rest} {c_name}".strip() if c_name else rest
                elif dr0:
                    pos = int(dr0.group(1))
                    rest = dr0.group(2).strip()
                    c_name = f"{rest} {c_name}".strip() if c_name else rest
                elif c_name:
                    gm = _POS_GLUED_RE.match(c_name)
                    if gm:
                        pos = int(gm.group(1))
                        c_name = gm.group(2)
                    else:
                        sp = _POS_SPACE_RE.match(c_name)
                        if sp:
                            pos = int(sp.group(1))
                            c_name = sp.group(2)
                        else:
                            dr = _POS_DIRECT_RE.match(c_name)
                            if dr:
                                pos = int(dr.group(1))
                                c_name = dr.group(2)

            # ── ГЛАВНЫЙ ПРИЗНАК: наличие количества. ──────────────
            # У каждой реальной номенклатурной позиции ЕСТЬ количество.
            # Полоса с количеством = позиция (даже без номера — синтетический).
            # Полоса без количества = мать/шапка/заголовок.
            has_qty = bool(re.search(r"\d", c_qty))

            if pos is None and has_qty:
                # Самостоятельный товар без номера в документе
                pos = self._synth_next
                self._synth_next += 1

            if pos is None:
                extra = c_name.strip()
                if not extra or not re.search(r"[А-Яа-яA-Za-z]{2}", extra):
                    continue
                if _is_section_title(extra):
                    continue
                # Материнская строка (имя без количества) — собираем глобально.
                if not re.search(r"наименование", extra, re.I):
                    mothers.append((page, row_y, extra))
                continue

            seen.add(pos)
            name = c_name.strip()

            # ── Иерархия комплектов ──────────────────────────────
            is_label = bool(
                re.search(r":\s*$", name)
                or re.search(r"в составе|в комплекте", name, re.I)
                or re.match(r"^Комплект\b", name, re.I)
            )
            is_child = bool(re.match(r"^\s*(?:-\s*|[а-дa-e]\)\s*)", name))
            if is_label:
                base = re.split(r":\s*", name, maxsplit=1)[0]
                self._pending_parent = base.rstrip(": ").strip()
                if is_child and self._pending_parent:
                    name = f"{self._pending_parent} {name}".strip()
            elif is_child and self._pending_parent:
                name = f"{self._pending_parent} {name}".strip()
            else:
                self._pending_parent = ""
            name = re.sub(r"\s+", " ", name).strip()

            spec = cells.get("spec", "")
            code = cells.get("code", "")
            manuf = cells.get("manufacturer", "").strip('" ').strip()
            weight_c = cells.get("weight", "")

            unit_s, qty_s = self._split_unit_qty(c_unit, c_qty)

            review = not re.search(r"[А-Яа-яA-Za-z]{2}", name)

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
                "_y": row_y,
                "_page": page,
            }
            built.append(item)

        return built, mothers

    def _is_header_band(self, cells: dict[str, str]) -> bool:
        """Строка заголовка формы («Позиция | Наименование…» / «1 | 2 | 3…»).
        Строка без номера и количества, содержащая слово-якорь шапки
        («тип, марка», «наименование», «единица», «кол-во», «масса»,
        «примечание», «завод-изготовитель»), считается фрагментом шапки.
        """
        values = [c for c in cells.values()]
        joined = " ".join(values).lower()
        anchors = sum(1 for _, p in _COLUMN_ANCHORS if p in joined)
        if anchors >= 3:
            return True
        nonempty = [c for c in values if c]
        if nonempty and all(re.fullmatch(r"[1-9]", c) for c in nonempty):
            return True
        # Фрагмент шапки: без позиции и количества + якорное слово
        if not cells.get("pos", "").strip() and not re.search(r"\d", cells.get("qty", "")):
            for word in ("наименование", "техническая характеристика",
                         "тип, марка", "обозначение", "единица", "кол-во",
                         "количество", "масса единицы", "примечание",
                         "завод-изготовитель", "изготовитель/поставщик"):
                if word in joined:
                    return True
        return False

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