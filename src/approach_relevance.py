"""Релевантность подхода/сайта текущему товару.

Проблема: тип товара (например ventilation_climate_ventilation) может содержать
и воздуховоды, и регуляторы скорости. Подходы, сохранённые при обучении на
регуляторе (search_query «SRE-Е-2,5...»), не должны показываться для воздуховода —
иначе агент идёт на чужой (электро) сайт и ищет не то.

Решение: подход релевантен товару, если его сохранённый search_query пересекается
со значимыми словами текущего товара (наименование + артикул). Без пересечения —
подход скрывается. Если данных недостаточно — показываем (безопасный fallback).

Правила сопоставления (стоп-слова, параметры, сокращения, контекстные правила)
настраиваются из GUI «Правила сопоставления» и хранятся в config/matching_rules.yaml.
Дефолты зашиты в _RULES_DEFAULTS; YAML-секция полностью заменяет дефолтную.

Чистый модуль (без Qt/сети) — покрыт тестами.
"""

import copy
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger("pricer.approach_relevance")

_WORD_RE = re.compile(r"[a-zа-яё0-9]{3,}")

_DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent / "config" / "matching_rules.yaml"

_RULES_DEFAULTS = {
    "stopwords": [
        "для", "из", "с", "со", "на", "по", "в", "во", "и", "не", "или", "к",
        "у", "от", "при", "за", "что", "это", "как", "так", "об", "про",
        "под", "мм", "ду", "типа", "тип", "размер", "новый", "отечественный",
    ],
    "structural_words": ["завод", "изготовитель", "производитель", "марка", "бренд", "гост", "ту"],
    "param_words": [
        "ру", "pn", "нр", "np", "kvs", "kv", "бар", "па", "атм",
        "тмакс", "макс", "мин", "max", "min", "град",
    ],
    "abbreviations": {"фл": "фланцевый"},
    "context_insignificant": [
        {"base": "Труба стальная водогазопроводная оцинкованная", "drop": "на грувлоках"},
    ],
}

_rules_lock = threading.RLock()
_rules = dict(_RULES_DEFAULTS)
_STOPWORDS_SET = frozenset(_RULES_DEFAULTS["stopwords"])
_STRUCTURAL_SET = frozenset(_RULES_DEFAULTS["structural_words"])
_PARAM_SET = frozenset(_RULES_DEFAULTS["param_words"])
_CONTEXT_RULES = []  # [(base_re, drop_re)]
_ABBR_PATTERNS = []  # [(compiled_re, full_form)]


def _phrase_to_regex(phrase: str) -> re.Pattern | None:
    """Фраза пользователя → regex: слова подряд, гибкие пробелы, без учёта регистра."""
    words = (phrase or "").strip().split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(w) for w in words), re.IGNORECASE)


def _compile_rules():
    global _STOPWORDS_SET, _STRUCTURAL_SET, _PARAM_SET, _CONTEXT_RULES, _ABBR_PATTERNS
    _STOPWORDS_SET = frozenset(_rules.get("stopwords") or [])
    _STRUCTURAL_SET = frozenset(_rules.get("structural_words") or [])
    _PARAM_SET = frozenset(_rules.get("param_words") or [])
    ctx = _rules.get("context_insignificant") or []
    rules = []
    for item in ctx:
        if not isinstance(item, dict):
            continue
        base_re = _phrase_to_regex(item.get("base", ""))
        drop_re = _phrase_to_regex(item.get("drop", ""))
        if base_re is not None and drop_re is not None:
            rules.append((base_re, drop_re))
    _CONTEXT_RULES = rules
    abbr = _rules.get("abbreviations") or {}
    patterns = []
    for abbrev, full in abbr.items():
        if abbrev and full:
            patterns.append((re.compile(rf"\b{re.escape(str(abbrev))}\b", re.IGNORECASE), str(full)))
    _ABBR_PATTERNS = patterns


_compile_rules()


def load_rules(path=None, reload=True) -> dict:
    """Загружает правила сопоставления из YAML (секция заменяет дефолтную).

    Файла нет или он повреждён — остаются дефолты. Правила применяются к текущей
    сессии сразу (перекомпиляция regex). Qt-free.
    """
    global _rules
    path = Path(path) if path else _DEFAULT_RULES_PATH
    data = {}
    if path.exists():
        try:
            import yaml

            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to load matching rules from %s: %s", path, e)
    with _rules_lock:
        _rules = _merge_rules(data)
        _compile_rules()
    return _rules


def _merge_rules(data: dict) -> dict:
    """Собирает правила: секция из data (кроме None) заменяет дефолт целиком."""
    merged = dict(_RULES_DEFAULTS)
    for key in _RULES_DEFAULTS:
        if key in data and data[key] is not None:
            merged[key] = data[key]
    return merged


def set_rules(new_rules: dict) -> dict:
    """Заменяет правила целиком (merge с дефолтами) и применяет к сессии."""
    global _rules
    with _rules_lock:
        _rules = _merge_rules(new_rules or {})
        _compile_rules()
    return _rules


def get_rules() -> dict:
    """Текущие правила (копия для чтения)."""
    with _rules_lock:
        return copy.deepcopy(_rules)


def save_rules(path=None) -> str:
    """Сохраняет текущие правила в YAML (используется GUI-редактором)."""
    path = Path(path) if path else _DEFAULT_RULES_PATH
    with _rules_lock:
        payload = dict(_rules)
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(payload, f, default_flow_style=False, allow_unicode=True)
    return str(path)


def reset_rules() -> dict:
    """Возвращает правила к встроенным дефолтам."""
    global _rules
    with _rules_lock:
        _rules = dict(_RULES_DEFAULTS)
        _compile_rules()
    return _rules


def tokenize(text: str) -> set:
    """Значимые слова текста (>=3 символа, без стоп-слов)."""
    if not text:
        return set()
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS_SET}


def approach_relevant(approach: dict, spec_text: str, extra_text: str = "") -> bool:
    """True, если подход имеет отношение к текущему товару.

    Сравниваются значимые слова сохранённого search_query подхода со словами
    товара (наименование + артикул). Если у подхода нет запроса или у товара
    нет значимых слов — True (не можем оценить, показываем).
    """
    query = approach.get("search_query") or ""
    a_tokens = tokenize(query)
    s_tokens = tokenize((spec_text or "") + " " + (extra_text or ""))
    if not a_tokens or not s_tokens:
        return True
    return bool(a_tokens & s_tokens)


# Токены-размеры/технические, не несущие смысла при сравнении товаров
_SIZE_RE = re.compile(r"^(ду|дн|dn|dp|мм|дюйм|in|g\d+|\d+[./]?\d*)$", re.IGNORECASE)

# Маркеры бренда/ГОСТ в наименованиях
_BRAND_RE = re.compile(
    r"(?:завод[- ]изготовитель|производитель|завод|бренд|марка)"
    r"\s*[:»]?\s*([а-яёa-z0-9][а-яёa-z0-9·&\-()]{1,30})",
    re.IGNORECASE,
)

_DU_RE = re.compile(r"(?:ду|дн|dn|dp)\s?(\d{2,3})", re.IGNORECASE)
_DIM_RE = re.compile(r"(\d{1,3})\s?(?:х|x)\s?(\d{1,4})", re.IGNORECASE)
_MM_RE = re.compile(r"(?:[øØ⌀∅]\s?(\d{2,4})|(\d{2,4})\s?мм\b)")
_FRAC_RE = re.compile(r"(\d+(?:\s+\d+)?\s*/\s*\d+)\s*\"")
_INCH_RE = re.compile(r"\b(\d+)\"")
_OUTLET_RE = re.compile(r"на\s+(\d+)\s+выход\w*", re.IGNORECASE)


def _size_key(text: str) -> set | None:
    """Канонические размеры в тексте: «ду15», «500x1000», «1/2"», «Ø100», «на 4 выхода».

    Если размеры присутствуют в обоих сравниваемых наименованиях, но различаются —
    это разные типоразмеры («Кран шаровой Ду15» ≠ «Кран шаровой Ду20»).
    """
    low = (text or "").lower()
    sizes = set()
    for m in _DU_RE.finditer(low):
        sizes.add(f"ду{m.group(1)}")
    for m in _MM_RE.finditer(low):
        sizes.add(f"мм{m.group(1) or m.group(2)}")
    for m in _DIM_RE.finditer(low):
        sizes.add(f"{m.group(1)}x{m.group(2)}")
    for m in _FRAC_RE.finditer(low):
        sizes.add(f"\"{m.group(1).replace(' ', '')}")
    for m in _INCH_RE.finditer(low):
        sizes.add(f"\"{m.group(1)}\"")
    for m in _OUTLET_RE.finditer(low):
        sizes.add(f"на{m.group(1)}выходов")
    return sizes or None


def _brand_of(text: str) -> str:
    """Бренд после явного маркера («завод-изготовитель Ридан» → «ридан»).

    Если бренд есть в обоих наименованиях, но различается — это разные бренды
    («Ридан» ≠ «Пульсар»), цену переиспользовать нельзя.
    """
    m = _BRAND_RE.search(text or "")
    return m.group(1).strip().rstrip(",.;").lower() if m else ""


def _context_normalize(text: str) -> str:
    """Убирает характеристики, незначимые в контексте конкретного наименования.

    Правила берутся из _CONTEXT_RULES (конфиг «Правила сопоставления»). Например,
    «Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5»
    сопоставляется с «Труба стальная водогазопроводная оцинкованная ⌀150х4,5»:
    «на грувлоках» незначимо для этого типа трубы. В других наименованиях
    «грувлок» остаётся значимым («Труба на грувлоках» ≠ «Труба ⌀150»).
    """
    if not text:
        return text
    for base_re, drop_re in _CONTEXT_RULES:
        if base_re.search(text):
            text = drop_re.sub("", text)
    return text


def normalize_search_text(text: str) -> str:
    """Наименование для поиска без контекстно-незначимых фраз.

    «Труба стальная водогазопроводная оцинкованная на грувлоках ⌀150х4,5»
    → «Труба стальная водогазопроводная оцинкованная ⌀150х4,5»: фраза
    «на грувлоках» незначима для этого типа трубы и не должна попадать
    в поисковый запрос агента. В других наименованиях «грувлок» остаётся
    значимым («Труба на грувлоках» ≠ «Труба ⌀150»).

    Оригинальное наименование спецификации не меняется — оно используется
    для проверки соответствия и записи цены.
    """
    if not text:
        return text
    normalized = _context_normalize(text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _product_tokens(text: str) -> set:
    """Значимые слова товара без размеров/номеров («ду15», «1/2», цифры)."""
    if not text:
        return set()
    tokens = set()
    for w in _WORD_RE.findall(_context_normalize(text).lower()):
        if w in _STOPWORDS_SET or w in _STRUCTURAL_SET or len(w) < 3:
            continue
        if w.isdigit() or _SIZE_RE.match(w):
            continue
        tokens.add(w)
    return tokens


def _prefix_match(tok: str, found_tokens: set) -> bool:
    for f in found_tokens:
        if tok == f:
            return True
        n = min(len(tok), len(f))
        if n >= 3 and (tok.startswith(f) or f.startswith(tok)):
            return True
    return False


def _is_optional_token(w: str) -> bool:
    """Токен, наличие которого в названии найденного товара не обязательно.

    Параметры («ру16», «kvs», «нр5-35») и значения с цифрами («220в», «1,9»,
    «b69») указываются в спецификации, но часто отсутствуют в title карточки.
    """
    if w in _PARAM_SET:
        return True
    return bool(re.search(r"\d", w))


def missing_required_tokens(spec_text: str, found_name: str) -> list[str]:
    """Обязательные значимые слова спецификации, отсутствующие в названии карточки.

    Используется для точной обратной связи агенту: «в переданном названии не хватает
    «приварку»» — вместо абстрактного «товар не соответствует спецификации».
    """
    spec_tokens = _product_tokens(spec_text)
    found_tokens = _product_tokens(found_name)
    if not spec_tokens or not found_tokens:
        return []
    required = {t for t in spec_tokens if not _is_optional_token(t)}
    if not required:
        required = spec_tokens
    return sorted(t for t in required if not _prefix_match(t, found_tokens))


def _expand_conn_abbrev(text: str) -> str:
    """Разворачивает сокращения типов соединения из правил.

    По умолчанию «фл»→«фланцевый»: «фл» — 2 символа, выпадает из _WORD_RE,
    из-за чего обязательный токен «фланцевый» не находится в названиях карточек
    («Ду 100 Ру16 фл Kvs=...»).
    """
    if not text:
        return text
    for pat, full in _ABBR_PATTERNS:
        text = pat.sub(full, text)
    return text


def product_name_matches(spec_text: str, found_name: str) -> bool:
    """Проверка: найденный товар соответствует позиции спецификации (с брендом)."""
    return _product_matches_core(spec_text, found_name, check_brand=True)


def product_name_matches_ignore_brand(spec_text: str, found_name: str) -> bool:
    """Совпадение по всем атрибутам, кроме бренда.

    Для кандидатов-фолбэков: товар того же типа/размера/соединения, но другого
    или неизвестного бренда. Бренд в сравнении игнорируется, сокращения соединения
    (по умолчанию «фл») расширяются до полной формы.
    """
    return _product_matches_core(
        _expand_conn_abbrev(spec_text),
        _expand_conn_abbrev(found_name),
        check_brand=False,
    )


def _product_matches_core(spec_text: str, found_name: str, check_brand: bool = True) -> bool:
    """Проверка: найденный товар соответствует позиции спецификации.

    Три измерения совпадения:
    - тип: ВСЕ значимые слова спецификации должны присутствовать в названии
      найденного товара («статический» обязан быть в названии, если он есть в
      спецификации; «Клапан балансировочный статический» ≠ «Клапан
      балансировочный авт.» — это разные подтипы). Исключение — параметрические
      слова и значения (ру/kvs/220в), которые могут быть только в описании.
      Словоформы и аббревиатуры сравниваются по префиксу
      («автоматический» ≈ «авт», «баланс.» ≈ «балансировочный»).
    - размер (если есть в обоих — обязаны совпадать: «Ду15» ≠ «Ду20»),
    - бренд (если есть в обоих — обязан совпадать: «Ридан» ≠ «Пульсар»);
      при check_brand=False бренд в сравнении не участвует.

    Структурные слова («завод», «изготовитель», «производитель») сходство НЕ доказывают.

    «Кран шаровой Ду15» vs «Клапан балансировочный Ду15» → False (разные товары).
    «Кран шаровой Ду15» vs «Кран шаровой Ду20 Ридан» → False (разный размер).
    «Кран Ду15 Ридан» vs «Кран Ду15 Пульсар» → False (разный бренд).
    «Клапан баланс. статический Ду15» vs «Клапан балансировочный авт. Ду15» → False
    (разные подтипы: статический ≠ автоматический).
    Недостаточно данных (нет названия) → True (не отклоняем).
    """
    spec_tokens = _product_tokens(spec_text)
    found_tokens = _product_tokens(found_name)
    if not spec_tokens or not found_tokens:
        return True

    if not check_brand:
        spec_brand = _brand_of(spec_text)
        found_brand = _brand_of(found_name)
        if spec_brand:
            spec_tokens = {t for t in spec_tokens if not _prefix_match(spec_brand, {t})}
        if found_brand:
            found_tokens = {t for t in found_tokens if not _prefix_match(found_brand, {t})}
        if not spec_tokens or not found_tokens:
            return True

    required = {t for t in spec_tokens if not _is_optional_token(t)}
    if not required:
        required = spec_tokens
    if not all(_prefix_match(s, found_tokens) for s in required):
        return False

    spec_sizes = _size_key(spec_text)
    found_sizes = _size_key(found_name)
    if spec_sizes and found_sizes and spec_sizes != found_sizes:
        return False

    if check_brand:
        spec_brand = _brand_of(spec_text)
        found_brand = _brand_of(found_name)
        if spec_brand and found_brand and spec_brand != found_brand:
            return False

    return True
