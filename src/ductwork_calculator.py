"""Расчёт стоимости воздуховодов и фасонных частей (без обращения в сеть).

Логика портирована из Proj_duct/ductwork_calculator.py + ductwork_formulas.md
с уточнениями, подтверждёнными фактическими result-файлами:
- отвод прямоугольный: R = 1.0×max(A,B) (formula-документ; скрипт Proj_duct
  имел устаревшее R = 150мм + A/2);
- цена за изделие = S_м.п. × припуск × цена_м² × K_толщины × L_ном, где
  L_ном — принятая номенклатурная длина прямого воздуховода:
  круглый 3000 мм, прямоугольный 1250 мм.

Qt-free, без pandas/сети/LLM — полностью тестируемо.
"""

import logging
import math
import re
from typing import Optional

logger = logging.getLogger("pricer.ductwork")

# ──────────────────────────────────────────────
# 1. OCR-коррекции (из ocr_corrections.md + ductwork_formulas.md)
# ──────────────────────────────────────────────

OCR_FIXES = {
    '6=': 'δ=', '5=': 'δ=', 'б=': 'δ=',
    '1001С': '100°C', "120'С": "120°C", '1101С': '110°C',
    'поЗключением': 'подключением', '6 комплекте': 'в комплекте',
    'б комплекте': 'в комплекте', 'ковгоко': 'ковкого',
    'ВстаВками': 'вставками', 'ВстаВки': 'вставками',
    'возЗухоОтводчиком': 'воздухоотводчиком',
    'Адаптерами': 'адаптерами', 'крутоиэогнутый': 'крутоизогнутый',
    'гофрироанная': 'гофрированная', '15ухта': '1бухта',
    'стальныя': 'стальная', 'электросбарная': 'электросварная',
    'балансировочный': 'балансировочный', 'балансированный': 'балансировочный',
    'Цилинйры': 'Цилиндры', 'каширобанные': 'кашированные',
    'и110': 'Ду110', 'иНО': 'Ду110', 'мЗ': 'м3',
    'полнопрохойной': 'полнопроходной', 'мутфу': 'муфту',
    'Зюбель': 'Дюбель', 'пробоЗника': 'проводника',
    '5ронки': 'воронки', 'обуЗование': 'оборудование',
    'СреЗства': 'Средства', 'телевиЗения': 'телевидения',
    'обносторонняя': 'односторонняя', 'униберс': 'универс',
    'пробукция': 'продукция', 'воЗосточных': 'водосточных',
    'боронок': 'воронок', 'кулачобый': 'кулачковый',
    'концебая': 'концевая', 'проховная': 'проходная',
    'мебными': 'медными', 'бысокого': 'высокого',
    'ИДФ': 'МДФ', 'Размео': 'Размер',
    'циннковый': 'цинковый', 'площайки': 'площадки',
    'Злектропривой': 'Электропривод', 'Заз': 'для',
    'оборуЗование': 'оборудование',
}

# OCR-ошибка PDF: ⌀ (U+2300) извлекается как латинская 'p'/'р'. Паттерн
# конвертации p+число → Ø+число. НЕ внутри слова/числа (negative lookbehind),
# ПОСЛЕ любого разделителя. Дефис допускается только перед следующей
# размерностью ([pр]х/цифры-маркеры) — чтобы не портить латинские модели
# вида «Насос P125-40» (после дефиса идёт цифра, а не p/р).
_P_OCR_DIAM_RE = re.compile(
    r'(?<![A-Za-zА-Яа-я0-9])[pр](\d{2,4})(?=\s|$|[хx,/;)]|(?=-[pр]))',
)


def apply_ocr_fixes(name: str) -> str:
    for wrong, right in OCR_FIXES.items():
        name = name.replace(wrong, right)
    name = re.sub(r'О(?=\d)', '0', name)
    return name


def fix_circle_notation(name: str) -> str:
    """ø ⌀ ∅ ф → Ø; З<число> → Ø<число>; 0<число> → Ø<число> в контексте круглого воздуховода."""
    name = name.replace('ø', 'Ø').replace('⌀', 'Ø').replace('∅', 'Ø')
    name = re.sub(r'(?<![a-zA-Zа-яА-Я])ф(\d)', r'Ø\1', name)
    if any(kw in name.lower() for kw in ['воздуховод', 'кругл', 'прямоугольн', 'отвод', 'переход', 'тройник', 'утка', 'врезк', 'заглушк']):
        name = re.sub(r'(?<!\d)0(\d{2,4})', r'Ø\1', name)
        name = re.sub(r'З(\d{2,4})', r'Ø\1', name)
        name = _P_OCR_DIAM_RE.sub(r'Ø\1', name)
    return name


def normalize_diameter_symbols(text: str) -> str:
    """Нормализация символов диаметра на входе в пайплайн.

    Исправляет OCR-ошибку: шрифт PDF кодирует ⌀ (U+2300) как глиф 'p',
    из-за чего '⌀225' превращается в 'p225'. Нормализация:
    - Явные символы диаметра (⌀ ∅ ø) → Ø
    - p/р + число (2-4 цифры) в позиции размера → Ø. Ограничение — НЕ внутри
      слова/числа (negative lookbehind), но ПОСЛЕ любого разделителя: '-p125',
      '/p125', '(p100)', '=p200', ':p150', '  p125', 'p125-p100' — все нормализуются.
    """
    text = text.replace('⌀', 'Ø').replace('∅', 'Ø').replace('ø', 'Ø')
    text = _P_OCR_DIAM_RE.sub(r'Ø\1', text)
    return text


# ──────────────────────────────────────────────
# 2. Типы элементов (20 типов)
# ──────────────────────────────────────────────

ELEMENT_KEYWORDS = [
    ('elbow_round', ['отвод круглого']),
    ('elbow_rect', ['отвод прямоугольного']),
    ('hood_wall', ['зонт.*пристенн']),
    ('hood_island', ['зонт.*островн', 'зонт', 'hood']),
    ('transition_mix', ['переход.*круглого.*на.*прямоугольн', 'переход.*прямоугольн.*на.*кругл']),
    ('transition_round', ['переход круглого сечения', 'переход кругл']),
    ('transition_rect', ['переход прямоугольного сечения', 'переход прямоугольн']),
    ('tap_round', ['врезк.*кругл']),
    ('tap_rect', ['врезк.*прямоугольн']),
    ('cap_round', ['заглушка кругла']),
    ('cap_rect', ['заглушка прямоугольна']),
    ('deflector', ['дефлектор']),
    ('nipple', ['ниппель']),
    ('offset_round', ['утка.*кругл']),
    ('offset_rect', ['утка.*прямоугольн']),
    ('tee_round', ['тройник.*кругл']),
    ('tee_rect', ['тройник.*прямоугольн']),
    ('flex_insert', ['гибк.*вставк', 'вставк.*гибк', 'гв ']),
    ('duct_straight', ['воздуховод']),
]


def detect_element_type(name: str) -> str:
    """Определяет тип элемента вентиляции (20 типов) или 'other'."""
    name_lower = name.lower()
    for etype, keywords in ELEMENT_KEYWORDS:
        for kw in keywords:
            if re.search(kw, name_lower):
                return etype
    if 'отвод' in name_lower:
        if re.search(r'Ø', name):
            return 'elbow_round'
        return 'elbow_rect'
    if 'переход' in name_lower:
        has_round = bool(re.search(r'Ø', name))
        has_rect = bool(re.search(r'\d+\s*[xх]\s*\d+', name))
        if has_round and has_rect:
            return 'transition_mix'
        if has_round:
            return 'transition_round'
        if has_rect:
            return 'transition_rect'
        if re.search(r'\d+\s*/\s*\d+', name):
            return 'transition_round'
        return 'transition_rect'
    if 'утка' in name_lower:
        if re.search(r'Ø', name):
            return 'offset_round'
        return 'offset_rect'
    if 'тройник' in name_lower:
        if re.search(r'Ø', name):
            return 'tee_round'
        return 'tee_rect'
    if 'заглушк' in name_lower:
        if re.search(r'Ø', name):
            return 'cap_round'
        return 'cap_rect'
    if 'врезк' in name_lower:
        if re.search(r'Ø', name):
            return 'tap_round'
        return 'tap_rect'
    return 'other'


# ──────────────────────────────────────────────
# 3. Гейт «это воздуховод/фасонная часть»
# ──────────────────────────────────────────────

# Типы, однозначно принадлежащие вентиляции (без сантехнического омонима).
_DUCTWORK_UNIQUE_TYPES = {"hood_island", "hood_wall", "deflector", "flex_insert"}
_VENTILATION_PRODUCT_TYPE = "ventilation_climate_ventilation"
# Дополнительные типы вентиляции из settings.yaml → special_types.ductwork
# (при сплите типа в UI расчёт не отключается молча).
try:
    from src.config_loader import get_special_types
    _VENTILATION_TYPES = {_VENTILATION_PRODUCT_TYPE} | set(get_special_types().get("ductwork", []))
except Exception:
    _VENTILATION_TYPES = {_VENTILATION_PRODUCT_TYPE}
# Маркеры воздуховодного контекста в наименовании.
# Безопасные паттерны (проверены на 42 кейсах канализации/сантехники — 0 FP):
#  空气овод|вентиляц|приточн|вытяжн — прямые маркеры
#  круглого|прямоугольн|кругл — форма сечения (аббревиатуры)
#  °\s*\d.*R\d — угол + радиус (уникально для фасонных частей вентиляции)
_DUCT_CONTEXT_RE = re.compile(
    r'воздуховод|вентиляц|приточн|вытяжн'
    r'|круглого|прямоугольн|кругл'
    r'|°\s*\d.*R\d',
    re.IGNORECASE,
)

# Типы элементов, для которых достаточно контекста спецификации (без regex).
_SPEC_CONTEXT_TYPES = {
    'elbow_round', 'elbow_rect',
    'transition_round', 'transition_rect', 'transition_mix',
    'tee_round', 'tee_rect',
    'tap_round', 'tap_rect',
    'cap_round', 'cap_rect',
    'offset_round', 'offset_rect',
    'hood_wall', 'hood_island',
    'deflector', 'flex_insert',
    'nipple',
    'duct_straight',
}

# Сантехнические маркеры, которые запрещают отнесение к воздуховодам даже в
# spec_context="ventilation" (омонимы: отвод/тройник/заглушка канализационные,
# ППР/ПВХ/чугун/пластик, Ду-номиналы, «переходник» — сантехнический термин).
_PLUMBING_OVERRIDE_RE = re.compile(
    r'канализаци|полипропилен|полипроп|полиэтилен|\bППР\b|\bПВХ\b|\bПНД\b'
    r'|чугун|водопровод|отоплен|пластик|переходник'
    r'|(?<![a-zа-я0-9])ду\s*\d',
    re.IGNORECASE,
)


def is_ductwork_row(spec_text: str, product_type: Optional[str] = None,
                    spec_context: Optional[str] = None) -> bool:
    """True, если строка — воздуховод или фасонная часть (а не сантехника).

    Уровень 1: узкий детектор 20 типов (иначе 'other' — не вентиляция).
    Уровень 2: исключение сантехнических омонимов (ниппель, заглушка, отвод/переход/
    тройник без «круглого/прямоугольного» и без воздуховодного контекста).
    Уровень 3: spec_context="ventilation" — если спецификация вентиляционная,
    обнаруженные элементы считаются воздуховодами (кроме 'other' и сантехники
    с явными маркерами _PLUMBING_OVERRIDE_RE).
    """
    if not spec_text or not str(spec_text).strip():
        return False
    name = fix_circle_notation(apply_ocr_fixes(str(spec_text)))
    elem = detect_element_type(name)
    if elem == "other":
        return False
    low = name.lower()
    if _DUCT_CONTEXT_RE.search(low):
        return True
    if product_type in _VENTILATION_TYPES:
        return True
    if elem in _DUCTWORK_UNIQUE_TYPES:
        return True
    if spec_context == "ventilation" and elem in _SPEC_CONTEXT_TYPES:
        if _PLUMBING_OVERRIDE_RE.search(low):
            return False
        return True
    # Уровень 4: переход «круглое→прямоугольное сечение» существует только у
    # листовых воздуховодов (в канализации редукторы круглые→круглые/врезки).
    if elem == "transition_mix":
        has_round = bool(re.search(r'Ø\s*\d+', name))
        has_rect = bool(re.search(r'\d+\s*[xх]\s*\d+', name))
        if has_round and has_rect:
            return True
    return False


def count_ductwork_items(specs, product_types=None) -> int:
    """Число позиций-воздуховодов в списке спецификации (для детекции при загрузке).

    specs — итерируемое с атрибутом .text (SpecItem) или строк.
    """
    n = 0
    for i, s in enumerate(specs):
        text = s.text if hasattr(s, "text") else s
        pt = None
        if product_types is not None:
            try:
                pt = product_types[i]
            except (IndexError, TypeError):
                pt = None
        if is_ductwork_row(text, pt):
            n += 1
    return n


def infer_spec_context(specs, min_duct_rows: int = 3, min_share: float = 0.15) -> Optional[str]:
    """Определяет контекст спецификации по доле однозначных воздуховодных строк.

    Мажоритарное голосование (не зависит от имени файла): если в списке
    достаточно строк, РАСПОЗНАННЫХ как воздуховоды БЕЗ контекста (явные
    маркеры «воздуховод/круглого/зонт/...»), и их доля значима, спецификация
    вентиляционная → возвращает "ventilation" (для применения spec_context
    к неоднозначным строкам: «Переход 300x200-p125», «Заглушка 400x600»).

    Возвращает None, если спецификация не вентиляционная или данных мало.
    """
    texts = [(s.text if hasattr(s, "text") else s) for s in specs]
    total = len([t for t in texts if str(t).strip()])
    if total < min_duct_rows:
        return None
    n_duct = 0
    for t in texts:
        try:
            if is_ductwork_row(str(t)):
                n_duct += 1
        except Exception:
            continue
    if n_duct >= min_duct_rows and (n_duct / total) >= min_share:
        return "ventilation"
    return None



# ──────────────────────────────────────────────
# 4. Сталь, толщина, цены
# ──────────────────────────────────────────────

STEEL_DEFAULTS = {
    'оцинкованная': {'keywords': ['оцинкованн', 'оц', 'zn', 'оцинк'], 'default_price': 850},
    'нержавеющая': {'keywords': ['нержавеющ', 'нерж', 'aisi 304', 'aisi304', 'нержав', 'inox'], 'default_price': 2350},
    'чёрная': {'keywords': ['черн', 'обычн', 'х/к', '08пс', 'чёрн'], 'default_price': 625},
}

STRAIGHT_PRICES = {
    'rect': {0.5: 1457, 0.7: 1881, 0.8: 2098, 0.9: 2395, 1.0: 2615},
    'round': {0.5: 1220, 0.7: 1678, 0.8: 1900, 0.9: 2117, 1.0: 2341},
}

SHAPED_PRICES = {
    'rect': {0.5: 2148, 0.7: 2445, 0.8: 2665, 0.9: 3011, 1.0: 2878},
    'round': {0.5: 1860, 0.7: 2215, 0.8: 2435, 0.9: 2655, 1.0: 2878},
}

THICKNESS_COEFFS = {
    0.5: 0.80, 0.6: 0.90, 0.7: 0.95,
    0.8: 1.00, 0.9: 1.05, 1.0: 1.10, 1.2: 1.20,
}

VALID_THICKNESSES = sorted({k for d in (STRAIGHT_PRICES['rect'], STRAIGHT_PRICES['round'],
                                        SHAPED_PRICES['rect'], SHAPED_PRICES['round']) for k in d})

# Номенклатурная длина прямого воздуховода (изделие), м.
NOMENCLATURE_LENGTHS = {'round': 3.0, 'rect': 1.25}
AREA_ALLOWANCE = 1.05  # 5% припуск на площадь

# Фасонные части круглого сечения (цена по SHAPED_PRICES['round']).
_ROUND_SHAPED_TYPES = {'nipple', 'deflector', 'elbow_round', 'transition_round',
                       'tap_round', 'cap_round', 'tee_round', 'offset_round'}


def detect_steel_type(name: str) -> tuple:
    name_lower = name.lower()
    for steel, info in STEEL_DEFAULTS.items():
        for kw in info['keywords']:
            if kw in name_lower:
                return steel, info['default_price']
    return 'оцинкованная', 850


def get_closest_thickness(mm: float) -> float:
    return min(VALID_THICKNESSES, key=lambda x: abs(x - mm))


def extract_thickness_mm(name: str) -> float:
    m = re.search(r'(?:b|δ|δ|толщин[аоы](?:\s+\S+)?)\s*[:=]?\s*([\d.,]+)\s*[мm][мm]', name, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(',', '.'))
    return 0.8


def parse_thickness_k(name: str, coeffs=None) -> float:
    coeffs = coeffs or THICKNESS_COEFFS
    mm_val = extract_thickness_mm(name)
    return coeffs.get(mm_val, 1.0)


def parse_angle(name: str) -> float:
    m = re.search(r'(\d+)\s*°', name)
    return float(m.group(1)) if m else 90.0


def parse_rect_dims(name: str):
    m = re.search(r'(\d+)\s*[xх]\s*(\d+)', name)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def parse_round_dims(name: str):
    m = re.search(r'[Ø⌀ø]\s*(\d+)', name)
    return float(m.group(1)) if m else None


def parse_length(name: str, default_l: float) -> float:
    """L=... в мм → метры; иначе default_l (уже в метрах)."""
    m = re.search(r'[Ll]\s*=\s*(\d+)', name)
    if m:
        return float(m.group(1)) / 1000.0
    return default_l


# ──────────────────────────────────────────────
# 5. Площадь (20 формул)
# ──────────────────────────────────────────────

def calc_area(element_type: str, name: str) -> float:
    name_lower = name.lower()

    if element_type == 'duct_straight':
        d = parse_round_dims(name)
        if d:
            l_val = parse_length(name, 1.0)
            return math.pi * (d / 1000) * l_val
        a, b = parse_rect_dims(name)
        if a and b:
            l_val = parse_length(name, 1.0)
            return 2 * (a + b) / 1000 * l_val
        return 0.0

    if element_type == 'elbow_round':
        d = parse_round_dims(name)
        if not d:
            return 0.0
        angle = parse_angle(name)
        r = 1.0 * d / 1000
        l_arc = math.pi * r * angle / 180
        return math.pi * (d / 1000) * l_arc

    if element_type == 'elbow_rect':
        a, b = parse_rect_dims(name)
        if not a or not b:
            return 0.0
        angle = parse_angle(name)
        p = 2 * (a + b) / 1000
        r = 1.0 * max(a, b) / 1000  # R = 1.0×max(A,B) — подтверждено result-файлом
        l_arc = math.pi * r * angle / 180
        return p * l_arc

    if element_type == 'transition_round':
        dims = re.findall(r'Ø\s*(\d+)', name)
        if len(dims) < 2:
            slash_match = re.search(r'(\d+)\s*/\s*(\d+)', name)
            if slash_match:
                dims = [slash_match.group(1), slash_match.group(2)]
        if len(dims) < 2:
            return 0.0
        d1, d2 = float(dims[0]), float(dims[1])
        l_val = parse_length(name, 0.25)
        d_avg = (d1 + d2) / 2 / 1000
        l_obl = math.sqrt(l_val**2 + ((d1 - d2) / 2 / 1000)**2)
        return math.pi * d_avg * l_obl

    if element_type == 'transition_rect':
        dims = re.findall(r'(\d+)\s*[xх]\s*(\d+)', name)
        if len(dims) < 2:
            return 0.0
        a1, b1 = float(dims[0][0]), float(dims[0][1])
        a2, b2 = float(dims[1][0]), float(dims[1][1])
        l_val = parse_length(name, 0.2)
        h1 = math.sqrt(l_val**2 + ((a1 - a2) / 2 / 1000)**2)
        h2 = math.sqrt(l_val**2 + ((b1 - b2) / 2 / 1000)**2)
        return ((a1 + a2) * h1 + (b1 + b2) * h2) / 1000

    if element_type == 'transition_mix':
        round_dims = re.findall(r'Ø\s*(\d+)', name)
        rect_dims = re.findall(r'(\d+)\s*[xх]\s*(\d+)', name)
        d = float(round_dims[0]) if round_dims else 0
        a, b = (float(rect_dims[0][0]), float(rect_dims[0][1])) if rect_dims else (0, 0)
        if not d or not a or not b:
            return 0.0
        l_val = parse_length(name, 0.25)
        p_round = math.pi * d / 1000
        p_rect = 2 * (a + b) / 1000
        return ((p_round + p_rect) / 2) * l_val

    if element_type == 'tap_round':
        d = parse_round_dims(name)
        if not d:
            return 0.0
        l_val = parse_length(name, 0.1)
        return math.pi * (d / 1000) * l_val

    if element_type == 'tap_rect':
        a, b = parse_rect_dims(name)
        if not a or not b:
            return 0.0
        l_val = parse_length(name, 0.1)
        return 2 * (a + b) / 1000 * l_val

    if element_type == 'cap_round':
        d = parse_round_dims(name)
        if not d:
            return 0.0
        return math.pi * (d / 1000)**2 / 4

    if element_type == 'cap_rect':
        a, b = parse_rect_dims(name)
        if not a or not b:
            return 0.0
        return (a * b) / 1_000_000

    if element_type in ('hood_island', 'hood_wall'):
        dims = re.findall(r'(\d+)\s*x\s*(\d+)', name)
        if not dims:
            return 0.0
        l1, w1 = float(dims[0][0]), float(dims[0][1])
        h_val = parse_length(name, 0.2)
        l_top = l1 * 0.6
        w_top = w1 * 0.6
        h_slope_l = math.sqrt(h_val**2 + ((l1 - l_top) / 2 / 1000)**2)
        h_slope_w = math.sqrt(h_val**2 + ((w1 - w_top) / 2 / 1000)**2)
        s_side = ((l1 + l_top) * h_slope_l + (w1 + w_top) * h_slope_w) / 1000
        s_top = (l_top * w_top) / 1_000_000
        if element_type == 'hood_wall':
            s_side *= 0.5
            s_top *= 0.5
        return s_side + s_top

    if element_type == 'deflector':
        d = parse_round_dims(name) or 200
        d_m = d / 1000
        h_glass = d_m
        d_diff = 1.5 * d_m
        h_diff = 1.0 * d_m
        d_cone = 1.7 * d_m
        h_cone = 0.3 * d_m
        s_glass = math.pi * d_m * h_glass
        l_slope_diff = math.sqrt(h_diff**2 + ((d_diff - d_m) / 2)**2)
        s_diff = math.pi * (d_m + d_diff) / 2 * l_slope_diff
        s_cone = math.pi * d_cone * math.sqrt(h_cone**2 + (d_cone / 2)**2) / 2
        return s_glass + s_diff + s_cone

    if element_type == 'nipple':
        dims = re.findall(r'Ø\s*(\d+)', name)
        d1 = float(dims[0]) if dims else 100
        d2 = float(dims[1]) if len(dims) > 1 else d1
        l_val = parse_length(name, 0.05)
        return math.pi * (d1 + d2) / 2 / 1000 * l_val

    if element_type == 'offset_round':
        d = parse_round_dims(name)
        if not d:
            return 0.0
        l_val = parse_length(name, 0.2)
        c_val = 0.1
        return math.pi * (d / 1000) * math.sqrt(l_val**2 + c_val**2)

    if element_type == 'offset_rect':
        a, b = parse_rect_dims(name)
        if not a or not b:
            return 0.0
        l_val = parse_length(name, 0.2)
        c_val = 0.1
        p = 2 * (a + b) / 1000
        return p * math.sqrt(l_val**2 + c_val**2)

    if element_type == 'tee_round':
        dims = re.findall(r'Ø\s*(\d+)', name)
        d1 = float(dims[0]) / 1000 if dims else 0.1
        d2 = float(dims[1]) / 1000 if len(dims) > 1 else d1
        l1 = parse_length(name, 0.5)
        l2 = 0.25
        return math.pi * d1 * l1 + math.pi * d2 * l2

    if element_type == 'tee_rect':
        dims = re.findall(r'(\d+)\s*x\s*(\d+)', name)
        if not dims:
            return 0.0
        a1, b1 = float(dims[0][0]), float(dims[0][1])
        l1 = parse_length(name, 0.5)
        s1 = 2 * (a1 + b1) / 1000 * l1
        if len(dims) >= 2:
            a2, b2 = float(dims[1][0]), float(dims[1][1])
            s2 = 2 * (a2 + b2) / 1000 * 0.25
        else:
            s2 = 0
        return s1 + s2

    if element_type == 'flex_insert':
        m = re.search(r'(\d+)\s*x\s*(\d+)\s*x\s*(\d+)', name)
        if m:
            a, b, h_val = float(m.group(1)), float(m.group(2)), float(m.group(3))
            return 2 * (a + b) / 1000 * h_val / 1000
        a, b = parse_rect_dims(name)
        if not a or not b:
            return 0.0
        h_val = parse_length(name, 0.1)
        return 2 * (a + b) / 1000 * h_val

    return 0.0


# ──────────────────────────────────────────────
# 6. Цена за м²
# ──────────────────────────────────────────────

def _price_per_m2(elem_type: str, name: str, cfg: Optional[dict]) -> float:
    """Цена за м²: явная цена из конфига → таблицы STRAIGHT/SHAPED по толщине и форме."""
    if cfg and cfg.get("price_per_m2"):
        return float(cfg["price_per_m2"])
    straight = (cfg or {}).get("straight_prices") or STRAIGHT_PRICES
    shaped = (cfg or {}).get("shaped_prices") or SHAPED_PRICES
    thickness_mm = get_closest_thickness(extract_thickness_mm(name))
    has_round = parse_round_dims(name) is not None
    if elem_type == 'duct_straight':
        shape = 'round' if has_round else 'rect'
        table = straight[shape]
        return table.get(thickness_mm, 2098)
    if elem_type in _ROUND_SHAPED_TYPES:
        table = shaped['round']
        return table.get(thickness_mm, 2435)
    shape = 'round' if has_round else 'rect'
    table = shaped[shape]
    return table.get(thickness_mm, 2435)


# ──────────────────────────────────────────────
# 7. Основной расчёт
# ──────────────────────────────────────────────

def _nomenclature_length(elem_type: str, name: str, cfg: Optional[dict]) -> Optional[float]:
    """Номенклатурная длина изделия для прямого воздуховода (м); иначе None."""
    if elem_type != 'duct_straight':
        return None
    lengths = (cfg or {}).get("nomenclature_lengths") or NOMENCLATURE_LENGTHS
    has_round = parse_round_dims(name) is not None
    return lengths.get('round' if has_round else 'rect')


def _is_linear_unit(unit) -> bool:
    u = str(unit or '').lower().replace('.', '').replace(' ', '')
    return any(k in u for k in ('м.п', 'мп', 'пог', 'погон', 'метр', 'метров'))


def calculate_ductwork_row(spec_text: str, spec_meta: Optional[dict] = None,
                           config: Optional[dict] = None,
                           product_type: Optional[str] = None,
                           spec_context: Optional[str] = None) -> Optional[dict]:
    """Рассчитывает цену изделия для строки воздуховода/фасонной части.

    Возвращает result-dict (контракт process_row) или None, если строка НЕ
    воздуховод/фасонная часть (передаётся обычному агенту).

    spec_context — контекст спецификации: "ventilation" если файл является
    вентиляционной спецификацией (по имени файла).

    price — ЦЕНА ЗА ИЗДЕЛИЕ (одна штука номенклатурной длины для прямых
    воздуховодов, одна фасонная часть для фасонных).
    ductwork_breakdown — текстовый breakdown для колонки «Пометка».
    """
    if not is_ductwork_row(spec_text, product_type, spec_context=spec_context):
        return None
    name = fix_circle_notation(apply_ocr_fixes(str(spec_text)))
    elem_type = detect_element_type(name)
    if elem_type == "other":
        return None

    cfg = config or {}
    allowance = float(cfg.get("area_allowance", AREA_ALLOWANCE))
    coeffs = cfg.get("thickness_coeffs") or THICKNESS_COEFFS

    steel, _ = detect_steel_type(name)
    k_thick = parse_thickness_k(name, coeffs)

    s_area = calc_area(elem_type, name)
    if s_area <= 0:
        name_retry = _P_OCR_DIAM_RE.sub(r'Ø\1', name)
        if name_retry != name:
            elem_retry = detect_element_type(name_retry)
            s_area = calc_area(elem_retry, name_retry)
            if s_area > 0:
                name = name_retry
                elem_type = elem_retry
    if s_area <= 0:
        return None
    s_area *= allowance

    price_m2 = _price_per_m2(elem_type, name, cfg)
    conf = float(cfg.get("confidence", 1.0))
    requires_review = bool(cfg.get("requires_review", True))
    site_id = cfg.get("site_id", "ductwork_calculator")

    meta = spec_meta or {}
    qty_raw = meta.get("qty")
    try:
        qty = float(str(qty_raw).replace(',', '.')) if qty_raw not in (None, '') else 1.0
    except (ValueError, TypeError):
        qty = 1.0
    unit = meta.get("uom", "шт")

    l_nom = _nomenclature_length(elem_type, name, cfg)
    if l_nom is not None:
        price_item = s_area * price_m2 * k_thick * l_nom
        if _is_linear_unit(unit) and qty > 0:
            items = math.ceil(qty / l_nom)
        else:
            items = int(round(qty))
        size_note = f"L_ном={l_nom:g}м"
    else:
        price_item = s_area * price_m2 * k_thick
        items = int(round(qty))
        size_note = "шт"

    total = round(items * price_item, 2)
    price_item = round(price_item, 2)

    a, b = parse_rect_dims(name)
    d = parse_round_dims(name)
    if a and b:
        size_str = f"{int(a)}x{int(b)}"
    elif d:
        size_str = f"Ø{int(d)}"
    else:
        size_str = name[:40]

    breakdown = (
        f"модуль воздуховодов: тип={elem_type}, {size_str}, δ={extract_thickness_mm(name):g}мм "
        f"(K={k_thick:g}), S_издел={s_area:.4f} м², цена_м²={price_m2:g}, "
        f"цена_издел={price_item:g} ₽, {size_note}, изделий={items}, всего={total:g} ₽"
    )

    logger.info("Ductwork: %s → цена изделия=%.2f, изделий=%d, всего=%.2f",
                spec_text[:60], price_item, items, total)

    return {
        "spec_text": spec_text,
        "product_type": product_type or "ventilation_climate_ventilation",
        "price": price_item,
        "confidence": conf,
        "url": "",
        "site": site_id,
        "reason": "рассчитано программно: модуль воздуховодов (без обращения к сайтам)",
        "requires_review": requires_review,
        "ductwork_breakdown": breakdown,
    }
