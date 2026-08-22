import json
import logging
import re

from src.llm_client import LLMClient

logger = logging.getLogger("pricer.pdf.structurer")

MAX_INPUT_CHARS = 24000

# Словарь единиц измерения русских BoQ (якорь для разбора хвоста строки).
_UNITS_ALT = r"шт|м\.п|мп|мм|м2|м3|м²|м³|м|кг|т|л|уп|компл|рул|секц|поз|км|см"
_UNIT_CELL_RE = re.compile(rf"^(?:{_UNITS_ALT})\.?$", re.IGNORECASE)
_UNIT_THEN_NUM_RE = re.compile(rf"^({_UNITS_ALT})\.?\s+(\d+(?:[.,]\d+)?)$", re.IGNORECASE)
_NUM_THEN_UNIT_RE = re.compile(rf"^(\d+(?:[.,]\d+)?)\s+({_UNITS_ALT})\.?$", re.IGNORECASE)
# Позиция: отдельная ячейка «12» / «12.» или приклеенная к имени «12 Кран…»
_POS_CELL_RE = re.compile(r"^\.?\d{1,4}\.?,?$")
_POS_GLUED_RE = re.compile(r"^(\d{1,4})\.?\s+(.+)$")
# Штампы рамки чертежа («.Инв № подл», «Взам», «Подпись») — мусор, не имя.
_STAMP_RE = re.compile(
    r"^(?:\.\s*)?(?:№|Инв|Взам|Подпись|дата|подл|Н\.?контр|ГИП|Гип|Разраб|Пров)"
    r"(?=[\s.:]|$)[\s.:]*",
    re.IGNORECASE,
)


def _extract_llm_content(response) -> str:
    """Extract text content from an LLMClient.chat() response dict.

    Handles both the OpenAI chat-completions envelope and a plain
    {"content": ...} dict. Returns "" for error responses.
    """
    if not isinstance(response, dict):
        return ""
    if response.get("error"):
        return ""
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        pass
    return str(response.get("content", ""))


def _html_to_text(text: str) -> str:
    """Convert HTML table to pipe-delimited text for parsing.

    `<br>` внутри ячеек -> пробел (иначе рвёт pipe-строку), вне ячеек -> '\\n'.
    """
    text = re.sub(
        r'(<(?:td|th)[^>]*>)(.*?)(</(?:td|th)>)',
        lambda m: m.group(1) + re.sub(r'<br\s*/?>', ' ', m.group(2), flags=re.IGNORECASE) + m.group(3),
        text, flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r'</(?:td|th)>\s*<(?:td|th)[^>]*>', ' | ', text, flags=re.IGNORECASE)
    text = re.sub(r'<(?:td|th)[^>]*>(.*?)</(?:td|th)>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


class SpecStructurer:
    def __init__(self, llm_client: LLMClient, use_llm: bool = False,
                 max_chars: int = 3000, max_tokens: int = 1024,
                 temperature: float = 0.0):
        self._llm = llm_client
        self.use_llm = use_llm
        self._max_chars = max_chars
        self._max_tokens = max_tokens
        self._temperature = temperature

    async def structure(self, raw_text: str) -> list[dict]:
        if not raw_text.strip():
            return []

        cleaned = _html_to_text(raw_text)
        if not cleaned:
            logger.warning("MinerU output was only HTML markup, nothing after stripping")
            return []

        if self.use_llm and self._llm is not None:
            items = await self._llm_structure(cleaned[:MAX_INPUT_CHARS])
            if items:
                return items
            logger.warning("LLM structuring returned no items, falling back to regex parser")

        # Regex-путь работает с ПОЛНЫМ текстом — тихая обрезка теряла позиции
        # многостраничных спецификаций.
        return self._fallback_parse(cleaned)

    async def _llm_structure(self, text: str) -> list[dict]:
        """Structure table text via the LLM (option). Returns [] on any failure."""
        truncated = text[: self._max_chars] or text
        prompt = (
            "Преобразуй текст таблицы в JSON список позиций. "
            "Только результат, без пояснений и рассуждений.\n\n"
            'Формат каждой позиции: {"pos": 1, "name": "Кабель ВВГнг", "specs": "3х2.5", '
            '"code": "A001", "manufacturer": "ООО Кабель", "qty": 100.0, "unit": "м", "weight": 0.0}\n\n'
            "Текст таблицы:\n"
            f"{truncated}\n\n"
            "JSON:"
        )
        response = await self._llm.chat(
            messages=[{"role": "user", "content": prompt}],
            force_json=True,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        return self._safe_parse_items(response)

    def _safe_parse_items(self, response: dict) -> list[dict]:
        """Parse the list of items from an LLM response. Falls back to []."""
        content = _extract_llm_content(response)
        if not content:
            return []
        try:
            start, end = content.find("["), content.rfind("]") + 1
            if start >= 0 and end > start:
                items = json.loads(content[start:end])
                if isinstance(items, list):
                    return [self._normalize_item(it) for it in items]
        except (json.JSONDecodeError, TypeError, AttributeError):
            logger.warning("Failed to parse LLM JSON, using fallback")
        return []

    @staticmethod
    def _normalize_item(it) -> dict:
        """Coerce an item to the SpecStructurer contract."""
        if not isinstance(it, dict):
            return {}
        return {
            "pos": int(it.get("pos", 0) or 0),
            "name": str(it.get("name", "")).strip(),
            "specs": str(it.get("specs", "")),
            "code": str(it.get("code", "")),
            "manufacturer": str(it.get("manufacturer", "")),
            "qty": float(it.get("qty", 0) or 0),
            "unit": str(it.get("unit", "")),
            "weight": float(it.get("weight", 0) or 0),
        }

    def _fallback_parse(self, text: str) -> list[dict]:
        items = []
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("---"):
                continue

            if "|" in line:
                for item in self._parse_pipe_row(line):
                    item["pos"] = len(items) + 1
                    items.append(item)
                continue

            # Якорный разбор хвоста: единица из словаря обязана стоять на стыке
            # «имя… unit qty» или «имя… qty unit». Числа внутри имени
            # (ГОСТ 10704-91, Ду50х3.5) не могут стать количеством.
            m = re.match(
                rf"^(?:(\d{{1,4}})[.)]\s*)?(.+?)\s+({_UNITS_ALT})\.?\s+(\d+(?:[.,]\d+)?)$",
                line, re.IGNORECASE | re.UNICODE,
            )
            if m:
                pos, name, unit, qty = m.group(1), m.group(2), m.group(3), m.group(4)
            else:
                m = re.match(
                    rf"^(?:(\d{{1,4}})[.)]\s*)?(.+?)\s+(\d+(?:[.,]\d+)?)\s+({_UNITS_ALT})\.?$",
                    line, re.IGNORECASE | re.UNICODE,
                )
                if not m:
                    continue
                pos, name, qty, unit = m.group(1), m.group(2), m.group(3), m.group(4)

            name = (name or "").strip()
            # Тире/дефис перед количеством — сепаратор «имя — 350 м», не часть имени.
            name = re.sub(r"\s*[-–—]\s*$", "", name)
            name = _STAMP_RE.sub("", name).strip()
            # Санитарный гейт: имя без связных букв — мусор рамки/разметки
            if not name or not re.search(r"[А-Яа-яA-Za-z]{2}", name):
                continue
            # Склейка позиций («151. 152. Отвод-90 …»): лишние маркеры в начале —
            # признак сплющенного стека, восстановить границы имён нельзя.
            review = False
            while True:
                mm = re.match(r"^(\d{1,4})\.\s*(.+)$", name)
                if not mm:
                    break
                review = True
                name = mm.group(2).strip()
            # Хвост-осколок «… шт. 2» внутри имени — тоже неоднозначность
            if re.search(rf"\s(?:{_UNITS_ALT})\.?\s*\d+(?:[.,]\d+)?$", name,
                         flags=re.IGNORECASE):
                review = True
            items.append({
                "pos": int(pos) if pos else len(items) + 1,
                "name": name,
                "specs": "",
                "code": "",
                "manufacturer": "",
                "qty": self._to_float(qty),
                "unit": unit.lower().rstrip("."),
                "weight": 0,
                "requires_review": review,
            })
        return items

    @staticmethod
    def _match_unit_qty(cell: str):
        """«шт. 48» / «48 шт» -> (unit, qty) или None."""
        m = _UNIT_THEN_NUM_RE.match(cell)
        if m:
            return m.group(1), m.group(2)
        m = _NUM_THEN_UNIT_RE.match(cell)
        if m:
            return m.group(2), m.group(1)
        return None

    def _expand_merged_row(self, parts: list[str]) -> list[dict] | None:
        """Разбор строки с несколькими позициями в общих ячейках.

        Экстракторы PDF иногда склеивают соседние строки: имя содержит маркеры
        «1 … 2 …», единицы идут списком («шт шт»), количества списком («48 12»).
        Восстанавливаем N позиций только при полном совпадении счётчиков.
        """
        name_idx = None
        markers: list[str] = []
        for i, cell in enumerate(parts):
            # Маркер позиции: отдельное число, возможно с точкой («50.», «152.»).
            # Lookahead не съедает разделитель — соседние маркеры не перекрываются.
            found = re.findall(r"(?:^|\s)(\d{1,4})(?:\.|(?=\s)|(?=$))", cell)
            if len(found) >= 2:
                vals = [int(x) for x in found]
                if all(vals[k] < vals[k + 1] for k in range(len(vals) - 1)):
                    name_idx, markers = i, found
                    break
        if name_idx is None:
            return None
        n = len(markers)

        units: list[str] = []
        qtys: list[str] = []
        used = {name_idx}

        # Пары «ед. кол-во» в одной ячейке: «шт. 48 шт. 12»
        for j, cell in enumerate(parts):
            if j in used:
                continue
            pairs = re.findall(
                rf"({_UNITS_ALT})\.?\s+(\d+(?:[.,]\d+)?)", cell, flags=re.IGNORECASE
            )
            if len(pairs) == n:
                units = [p[0].lower().rstrip(".") for p in pairs]
                qtys = [p[1] for p in pairs]
                used.add(j)

        # Раздельные ячейки: «шт шт» + «48 12»
        for j, cell in enumerate(parts):
            if j in used:
                continue
            toks = cell.split()
            if len(toks) == n and all(_UNIT_CELL_RE.match(t) for t in toks):
                units = [t.lower().rstrip(".") for t in toks]
                used.add(j)
                break
        for j, cell in enumerate(parts):
            if j in used:
                continue
            toks = cell.split()
            if len(toks) == n and all(re.fullmatch(r"\d+(?:[.,]\d+)?", t) for t in toks):
                qtys = toks
                used.add(j)
                break

        if not qtys or len(qtys) != n:
            return None

        # Имена: сегменты между маркерами позиции
        cell = parts[name_idx]
        spans = [
            (m.start(1), m.end(1))
            for m in re.finditer(r"(?:^|\s)(\d{1,4})(?:\.|(?=\s)|(?=$))", cell)
        ]
        segments = []
        prev_end = 0
        for start, end in spans:
            segments.append(cell[prev_end:start].strip().strip("."))
            prev_end = end
        segments.append(cell[prev_end:].strip().strip("."))
        segments = [seg for seg in segments if seg]
        if len(segments) != n:
            return None
        # Санитарный гейт: имя обязано содержать буквы (≥2 подряд),
        # иначе склейка ложная («71.», «м.п», «и»)
        if any(not re.search(r"[А-Яа-яA-Za-z]{2}", seg) for seg in segments):
            return None

        manufacturer = next(
            (parts[j] for j in range(len(parts)) if j not in used), ""
        )

        out = []
        for k in range(n):
            out.append({
                "pos": int(markers[k]),
                "name": segments[k],
                "specs": "",
                "code": "",
                "manufacturer": manufacturer,
                "qty": self._to_float(qtys[k]),
                "unit": units[k] if k < len(units) else "",
                "weight": 0,
                "requires_review": False,
            })
        return out

    def _parse_pipe_row(self, line: str) -> list[dict]:
        """Строка таблицы -> 0..N позиций (см. _expand_merged_row)."""
        lowered = line.lower()
        header_kw = ("позиция", "наименование", "единица измерения", "количество",
                     "завод-изготовитель", "тип, марка", "код оборудования",
                     "масса единицы", "примечание", "оборудования и материалы")
        if any(kw in lowered for kw in header_kw):
            return []

        stripped = line.strip()
        if re.match(r'^[\d\s|]+$', stripped):
            return []
        if re.match(r'^[_\-.\s|]+$', stripped):
            return []
        if re.match(r'^[а-яА-Я]\s*\|', stripped):
            return []

        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            return []

        expanded = self._expand_merged_row(list(parts))
        if expanded:
            return expanded

        one = self._parse_pipe_line(line)
        return [one] if one else []

    def _parse_pipe_line(self, line: str) -> dict | None:
        """Разбор pipe-строки таблицы.

        Формат: [позиция] | имя [| код] [| производитель] | ед | кол [| масса].
        Позиция снимается СЛЕВА до числового скана (P1-fix: раньше номер позиции
        попадал в qty/weight и строка отбрасывалась). Единица — якорь из словаря;
        числа внутри имени (ГОСТ, Ду) количеством стать не могут (P2-fix).
        """
        lowered = line.lower()
        header_kw = ("позиция", "наименование", "единица измерения", "количество",
                     "завод-изготовитель", "тип, марка", "код оборудования",
                     "масса единицы", "примечание", "оборудования и материалы")
        if any(kw in lowered for kw in header_kw):
            return None

        stripped = line.strip()
        if re.match(r'^[\d\s|]+$', stripped):
            return None
        if re.match(r'^[_\-.\s|]+$', stripped):
            return None
        if re.match(r'^[а-яА-Я]\s*\|', stripped):
            return None

        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            return None

        def _is_num(s: str) -> bool:
            return bool(re.match(r'^\d+[\.\d]*$', s.replace(",", ".")))

        # ── Штампы рамки ДО извлечения позиции ───────────────────
        # («.№», «Взам дата и» → «и»; иначе хвост марки остаётся в имени)
        while parts:
            head = parts[0]
            if _STAMP_RE.match(head):
                parts[0] = _STAMP_RE.sub("", head, count=1).strip()
                if not parts[0]:
                    parts = parts[1:]
            elif head.lower() in ("и", "№", "н"):
                parts = parts[1:]
            else:
                break

        if not parts:
            return None

        # ── Позиция слева ────────────────────────────────────────
        pos = 0
        if len(parts) > 1 and _POS_CELL_RE.match(parts[0]):
            digits = re.sub(r"\D", "", parts[0])
            pos = int(digits) if digits else 0
            parts = parts[1:]
        else:
            m_pos = _POS_GLUED_RE.match(parts[0])
            if m_pos and len(parts) > 1 and not _is_num(parts[0]):
                pos = int(m_pos.group(1))
                parts = [m_pos.group(2)] + parts[1:]

        if len(parts) < 2:
            return None

        # ── Хвост: единица + количество (+ масса) ────────────────
        qty_s = unit_s = weight_s = ""
        split_at = len(parts)

        merged = self._match_unit_qty(parts[-1])
        if merged and len(parts) >= 2:
            unit_s, qty_s = merged
            split_at = len(parts) - 1
            if split_at - 1 >= 1 and _is_num(parts[split_at - 1]):
                weight_s = parts[split_at - 1]
                split_at -= 1
        else:
            unit_idx = next(
                (i for i in range(len(parts) - 1, 0, -1) if _UNIT_CELL_RE.match(parts[i])),
                None,
            )
            if unit_idx is not None:
                nums_after = [i for i in range(unit_idx + 1, len(parts)) if _is_num(parts[i])]
                if not nums_after:
                    return None
                unit_s = parts[unit_idx]
                qty_s = parts[nums_after[0]]
                if len(nums_after) > 1:
                    weight_s = parts[nums_after[1]]
                split_at = unit_idx
            else:
                # Якоря-единицы нет: два крайних числа справа = масса/кол-во.
                nums = [i for i in range(1, len(parts)) if _is_num(parts[i])]
                if len(nums) >= 2:
                    weight_s, qty_s = parts[nums[-1]], parts[nums[-2]]
                    split_at = nums[-2]
                elif len(nums) == 1:
                    qty_s = parts[nums[0]]
                    split_at = nums[0]
                else:
                    return None

        left = parts[:split_at]
        if not left:
            return None

        code = ""
        manufacturer = ""

        # Последнее поле слева — производитель?
        if len(left) > 1:
            last = left[-1]
            _norm = last.lower().replace('a', 'а').replace('e', 'е').replace('c', 'с').replace('o', 'о').replace('p', 'р').replace('x', 'х')
            if (re.search(r'(?:ооо|зао|ип|оао|ао|гк|ltd|inc|gmbh|corp)\b', _norm)
                    or re.search(r'аналог', _norm)
                    or re.search(r'["\']', last)):
                manufacturer = last
                left = left[:-1]

        # Новое последнее поле — артикул/код?
        if len(left) > 1:
            last = left[-1]
            if (re.match(r'^(?=.*\d)[А-ЯA-Z0-9][А-Яа-яA-Za-z0-9.,()/\-№\s]{0,50}$', last)
                    or re.match(r'^[A-Za-z][A-Za-z0-9/.\-]*(?:\s+[A-Za-z][A-Za-z0-9/.\-]*)*$', last)):
                code = last
                left = left[:-1]

        name = " ".join(left).strip()
        if not name:
            return None

        return {
            "pos": pos,
            "name": name,
            "specs": "",
            "code": code or "",
            "manufacturer": manufacturer or "",
            "qty": self._to_float(qty_s),
            "unit": unit_s.replace("ШT", "шт").replace("ШТ", "шт").lower().rstrip("."),
            "weight": self._to_float(weight_s),
            "requires_review": False,
        }

    def _to_float(self, val) -> float:
        try:
            return float(str(val).replace(",", ".").replace(" ", ""))
        except (ValueError, TypeError):
            return 0.0