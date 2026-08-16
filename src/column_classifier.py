"""Системная классификация колонок спецификации.

Заменяет наивный substring-матчинг (if/elif по первому совпадению), который
ломается на реальных спецификациях:
- «Завод-изготовитель» не детектился как производитель → данные терялись;
- «Код оборудования, изделия, материала» попадал в name из-за слова «материал»;
- «Масса единицы (кг)» перекрывала «Единица измерения» (обе содержат «ед»).

Подход:
1. Нормализация заголовка (lowercase, убрать кавычки, схлопнуть пробелы).
2. Взвешенная скоринг-модель: для каждой роли — паттерны с весами
   (3 — сильный сигнал, 2 — обычный, 1 — слабый/неоднозначный).
3. Валидация по фактическим значениям колонки: лексикон ед. изм., числа,
   номера позиций. Противоречащие значения понижают скор заголовка.
4. Назначение ролей: одиночные (position/uom/qty/weight/brand/note) — по
   максимальному скору; списковые (name/spec/article) — по лучшей роли.
   Ни одна колонка не теряется молча — неклассифицированные логируются.

Чистый модуль (без Qt) — покрыт юнит-тестами.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

# Роли колонок
POSITION = 0
NAME = 1
SPEC = 2
ARTICLE = 3
BRAND = 4
UOM = 5
QTY = 6
WEIGHT = 7
NOTE = 8

ROLE_NAMES = {
    POSITION: "position",
    NAME: "name",
    SPEC: "spec",
    ARTICLE: "article",
    BRAND: "brand",
    UOM: "uom",
    QTY: "qty",
    WEIGHT: "weight",
    NOTE: "note",
}

# Одиночные роли (одна колонка на роль) — порядок приоритета назначения
_SINGLE_INT_ROLES = (POSITION, UOM, QTY, WEIGHT, NOTE)
_SINGLE_LIST_ROLES = (BRAND,)  # brand исторически list (старый detect_columns)
# Списковые роли (можно несколько колонок)
_LIST_ROLES = (NAME, SPEC, ARTICLE)

# Паттерны заголовков: (подстрока, вес). Суммируются внутри роли.
PATTERNS = {
    POSITION: [("позици", 3), ("поз.", 2), ("п/п", 3), ("№", 3), ("порядков", 2), ("номер", 1)],
    NAME: [("наименовани", 3), ("назван", 2), ("товар", 2), ("описан", 2),
           ("продукц", 2), ("материал", 1)],
    SPEC: [("тип", 2), ("обозначени", 2), ("характеристи", 2), ("опросного", 2),
           ("модел", 2), ("марка", 1), ("документа", 1)],
    ARTICLE: [("артикул", 3), ("article", 3), ("код оборуд", 3), ("код", 2),
              ("sku", 3), ("каталожн", 2), ("каталог", 2), ("part", 2),
              ("обозначение документа", 2)],
    BRAND: [("изготовител", 3), ("завод", 3), ("производител", 3), ("произв", 3),
            ("бренд", 3), ("brand", 3), ("maker", 3), ("фирма", 3), ("vendor", 3),
            ("компани", 2), ("марка", 2)],
    UOM: [("единиц", 3), ("измерени", 3), ("ед изм", 3), ("ед.изм", 3),
          ("unit", 3), ("упаков", 2), ("ед", 2)],
    QTY: [("количеств", 3), ("кол-во", 3), ("кол.", 2), ("qty", 3), ("quantity", 3),
          ("число", 2), ("всего", 2)],
    WEIGHT: [("масса", 3), ("вес", 3), ("weight", 3)],
    NOTE: [("примечани", 3), ("note", 3), ("remark", 3), ("комментари", 2)],
}

# Лексикон единиц измерения (нормализованные значения без пробелов/точек)
UOM_LEXICON = {
    "шт", "штук", "м", "м2", "м3", "мкв", "мкуб", "квм", "кубм",
    "пм", "погм", "мп", "м.пог", "кг", "т", "г", "мг", "л", "мл",
    "компл", "комплект", "упак", "уп", "ед", "пар", "лист", "руб", "бр",
}

_NUM_RE = re.compile(r"^-?\d+([.,]\d+)?$")


def _normalize_header(header) -> str:
    if header is None:
        return ""
    text = str(header).strip().lower()
    text = text.replace("«", "").replace("»", "").replace('"', "").replace("'", "")
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_value(value: str) -> str:
    return re.sub(r"[\s\"'«»]+", "", value.strip().lower())


def _is_number(value: str) -> bool:
    return bool(_NUM_RE.match(value.strip()))


def _value_profile(values: list[str]) -> dict:
    """Доли значений колонки: ед.изм. / числа / целые / с точкой / десятичные."""
    n = max(len(values), 1)
    uom = sum(1 for v in values if _normalize_value(v) in UOM_LEXICON)
    num = sum(1 for v in values if _is_number(v))
    plain = sum(1 for v in values if re.fullmatch(r"\d+", v.strip()))
    dotted = sum(1 for v in values if re.fullmatch(r"\d+\.", v.strip()))
    decimal = sum(1 for v in values if _is_number(v) and not re.fullmatch(r"\d+", v.strip()))
    return {"uom": uom / n, "num": num / n, "plain": plain / n,
            "dotted": dotted / n, "decimal": decimal / n}


@dataclass
class ColumnMapping:
    name: list[int] = field(default_factory=list)
    article: list[int] = field(default_factory=list)
    brand: list[int] = field(default_factory=list)
    spec: list[int] = field(default_factory=list)
    uom: Optional[int] = None
    qty: Optional[int] = None
    weight: Optional[int] = None
    position: Optional[int] = None
    note: Optional[int] = None
    unmapped: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "article": self.article,
            "brand": self.brand,
            "spec": self.spec,
            "uom": self.uom,
            "qty": self.qty,
            "weight": self.weight,
            "position": self.position,
            "note": self.note,
        }

    def describe(self) -> str:
        parts = []
        for role, cols in self.as_dict().items():
            if cols is not None and cols != [] and cols != 0:
                parts.append(f"{role}={cols}")
        if self.unmapped:
            parts.append(f"unmapped={self.unmapped}")
        return "; ".join(parts) if parts else "empty"


def classify_columns(headers, value_rows=None, sample: int = 50) -> ColumnMapping:
    """Классифицирует колонки по заголовкам и (опционально) по значениям.

    value_rows — итератор списков (значения ячеек по строкам, без шапки).
    """
    n = len(headers)
    header_score = [[0.0] * 9 for _ in range(n)]
    for c, header in enumerate(headers):
        norm = _normalize_header(header)
        if not norm or norm == "none":
            continue
        for role, pats in PATTERNS.items():
            header_score[c][role] = float(
                sum(w for pat, w in pats if pat in norm)
            )

    # Профиль значений колонок (сэмпл)
    vprof = [{"uom": 0.0, "num": 0.0, "plain": 0.0, "dotted": 0.0, "decimal": 0.0}
             for _ in range(n)]
    rows = [list(r) for r in (value_rows or [])][:sample]
    for c in range(n):
        vals = []
        for r in rows:
            if c < len(r) and r[c] is not None and str(r[c]).strip() != "":
                vals.append(str(r[c]))
        if vals:
            vprof[c] = _value_profile(vals)

    def combined(c: int, role: int) -> float:
        score = header_score[c][role]
        v = vprof[c]
        if role == UOM:
            if v["uom"] > 0.5:
                score += 3.0  # значения из лексикона ед. изм.
            elif v["num"] > 0.7:
                score -= 3.0  # числовые значения — не ед. изм.
            elif header_score[c][WEIGHT] >= 3:
                score -= 5.0  # «масса/вес» в заголовке — не uom
        elif role == QTY:
            if header_score[c][QTY] > 0:
                score += 2.0
            elif v["num"] > 0.7 and v["plain"] > 0.8:
                score += 2.0  # целочисленные значения
        elif role == POSITION:
            if header_score[c][POSITION] >= 2.0:
                score += 2.0  # сильный сигнал заголовка (позици/№/п/п/поз)
            elif v["dotted"] > 0.8:
                score += 2.0  # «1.», «2.» — номера позиций
        elif role == WEIGHT:
            if header_score[c][WEIGHT] > 0:
                score += 2.0
            elif v["num"] > 0.7 and v["decimal"] > 0.5:
                score += 2.0  # десятичные значения — масса
        elif role == BRAND:
            if v["num"] < 0.3 and v["uom"] < 0.3:
                score += 0.5
        return score

    # Лучшая роль для каждой колонки (по комбинированному скору)
    best_role = [None] * n
    best_score = [0.0] * n
    for c in range(n):
        for role in range(9):
            s = combined(c, role)
            if s > best_score[c]:
                best_score[c], best_role[c] = s, role

    mapping = ColumnMapping()
    used = [False] * n

    # 1) Одиночные роли: колонка назначается, только если эта роль — её лучшая
    for role in _SINGLE_INT_ROLES + _SINGLE_LIST_ROLES:
        candidates = [c for c in range(n)
                      if not used[c] and best_role[c] == role and best_score[c] >= 2.0]
        if not candidates:
            continue
        c = max(candidates, key=lambda cc: combined(cc, role))
        used[c] = True
        if role in _SINGLE_LIST_ROLES:
            getattr(mapping, ROLE_NAMES[role]).append(c)
        else:
            setattr(mapping, ROLE_NAMES[role], c)

    # 2) Списковые роли: остальные колонки идут в свою лучшую роль
    for c in range(n):
        if used[c]:
            continue
        role = best_role[c]
        if role in _LIST_ROLES and best_score[c] >= 2.0:
            getattr(mapping, ROLE_NAMES[role]).append(c)
            used[c] = True

    # 3) Fallback для name: первая неиспользованная непустая колонка
    if not mapping.name:
        for c in range(n):
            if not used[c] and _normalize_header(headers[c]):
                mapping.name.append(c)
                used[c] = True
                break

    mapping.unmapped = [c for c in range(n) if not used[c]]
    return mapping
