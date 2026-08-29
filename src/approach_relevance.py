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
    "optional_words": [
        # Описательные/серийные/комплектационные слова: могут отсутствовать в
        # названии карточки, хотя товар тот же. «Стальной панельный радиатор ...
        # LEMAX Premium Compact Hygiene, тип C10, В КОМПЛ. С КРАНОМ для выпуска
        # воздуха и креплениями» — h1 сайта «Радиатор панельный Лемакс Premium
        # C 10х500х600» (серия/комплектация опущены).
        "compact", "hygiene", "компл", "комплекте", "комплектация",
        "краном", "креплениями", "выпуска", "воздуха", "боковым",
        "подключением", "внутренняя", "резьба", "ручной", "автоматический",
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
_OPTIONAL_SET = frozenset(_RULES_DEFAULTS["optional_words"])
_CONTEXT_RULES = []  # [(base_re, drop_re)]
_ABBR_PATTERNS = []  # [(compiled_re, full_form)]


def _phrase_to_regex(phrase: str) -> re.Pattern | None:
    """Фраза пользователя → regex: слова подряд, гибкие пробелы, без учёта регистра."""
    words = (phrase or "").strip().split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(w) for w in words), re.IGNORECASE)


def _compile_rules():
    global _STOPWORDS_SET, _STRUCTURAL_SET, _PARAM_SET, _OPTIONAL_SET, _CONTEXT_RULES, _ABBR_PATTERNS
    _STOPWORDS_SET = frozenset(_rules.get("stopwords") or [])
    _STRUCTURAL_SET = frozenset(_rules.get("structural_words") or [])
    _PARAM_SET = frozenset(_rules.get("param_words") or [])
    _OPTIONAL_SET = frozenset(_rules.get("optional_words") or [])
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
_DIM_RE = re.compile(r"(\d{1,3})\s?(?:х|x)\s?(\d{1,4})(?:\s?(?:х|x)\s?(\d{1,4}))?", re.IGNORECASE)
_MM_RE = re.compile(r"(?:[øØ⌀∅]\s?(\d{2,4})|(\d{2,4})\s?мм\b)")
_SLASH_DIM_RE = re.compile(
    r"(?<![\d\"/])(\d{1,3}(?:\s*/\s*\d{1,3}){1,2})(?:\s*-\s*\d+)?(?![\d\"/])"
)
_FRAC_RE = re.compile(r"(\d+(?:\s+\d+)?\s*/\s*\d+)\s*\"")
_INCH_RE = re.compile(r"\b(\d+)\"")
_OUTLET_RE = re.compile(r"на\s+(\d+)\s+выход\w*", re.IGNORECASE)


def _size_key(text: str) -> set | None:
    """Канонические размеры в тексте: «ду15», «500x1000», «1/2"», «Ø100», «60/40-2», «на 4 выхода».

    Если размеры присутствуют в обоих сравниваемых наименованиях, но различаются —
    это разные типоразмеры («Кран шаровой Ду15» ≠ «Кран шаровой Ду20»).
    """
    low = _norm_dim_sep(text or "").lower()
    sizes = set()
    for m in _DU_RE.finditer(low):
        sizes.add(f"ду{m.group(1)}")
    for m in _MM_RE.finditer(low):
        sizes.add(f"мм{m.group(1) or m.group(2)}")
    for m in _DIM_RE.finditer(low):
        # «500x600», «10х500х600» (тип × высота × ширина — берём последнюю пару),
        # «20/20/16» не захватывается (слеш — отдельный паттерн).
        if m.group(3) is not None:
            sizes.add(f"{m.group(2)}x{m.group(3)}")
        else:
            sizes.add(f"{m.group(1)}x{m.group(2)}")
    for m in _SLASH_DIM_RE.finditer(low):
        sizes.add(m.group(1).replace(" ", ""))
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


def search_key_tokens(spec_text: str, spec_meta: dict | None = None) -> dict:
    """Ключевые токены товара для поиска (ОТОБРАЖЕНИЕ, НЕ скриптовый запрос).

    Показывает LLM дифференциаторы (бренд/тип/размер/Ду/артикул), которые нельзя
    терять при составлении запроса — корень деградации прогона 26.08 (запрос
    без размера, «…LEMAX Premium Compact Hygiene» без «C10 500xNNNN»). Запрос
    по-прежнему собирает сам LLM; этот блок лишь не даёт ему «не заметить» размер.
    """
    meta = spec_meta or {}
    text = (spec_text or "").strip()
    out: dict[str, str] = {}
    brand = (meta.get("brand") or "").strip()
    if brand:
        latin = _translit(brand)
        label = brand
        if latin and latin not in brand.lower():
            label = f"{brand} ({latin.upper()})"
        out["brand"] = label
    spec = (meta.get("spec") or "").strip()
    if spec and not is_standard_reference(spec):
        out["type"] = spec
    article = (meta.get("article") or "").strip()
    if article:
        out["article"] = article
    sizes = _size_key(text)
    if sizes:
        out["size"] = ", ".join(sorted(sizes))
    if not out:
        toks = [w for w in _product_tokens(text) if w not in _OPTIONAL_SET]
        if toks:
            out["keywords"] = " ".join(sorted(toks)[:8])
    return out


def _product_tokens(text: str) -> set:
    """Значимые слова товара без размеров/номеров («ду15», «1/2», цифры)."""
    if not text:
        return set()
    normalized = text
    normalized = normalized.replace("Ø", "Ду").replace("ø", "ду")
    normalized = normalized.replace("DN", "Ду").replace("dn", "ду")
    # Диаметр «Ф15» → «Ду15». ТОЛЬКО когда Ф перед цифрой — иначе ломаем слова
    # с заглавной «Ф» (Фланцевый → Дуланцевый) и получаем ложные расхождения.
    normalized = re.sub(r"Ф(?=\d)", "Ду", normalized)
    tokens = set()
    for w in _WORD_RE.findall(_context_normalize(normalized).lower()):
        if w in _STOPWORDS_SET or w in _STRUCTURAL_SET or len(w) < 3:
            continue
        if w.isdigit() or _SIZE_RE.match(w):
            continue
        tokens.add(w)
    return tokens


_STANDARD_REF_RE = re.compile(
    r"^\s*(гост\s*р?\b|ту\b|снип\b|сп\b|iso\b|din\b|en\b|astm\b|фнп\b|пнст\b"
    r"|мто\b|рм\b|сбн\b|сто\b|тр\s*тс\b)",
    re.IGNORECASE,
)


def is_standard_reference(text: str) -> bool:
    """True, если значение — ссылка на стандарт (ГОСТ/ТУ/СНиП/ISO/DIN/СТО...),
    а не модель товара. Такие значения не добавляются в поисковое наименование."""
    return bool(_STANDARD_REF_RE.match((text or "").strip().lower()))


def _translit(s: str) -> str:
    """Приближённая транслитерация кириллицы → латинице для сравнения брендов.

    «лемакс» → «lemax», «ридан» → «ridan». Используется только для смягчения
    бренд-сравнения, когда сайт пишет бренд латиницей, а спецификация —
    кириллицей (или наоборот).
    """
    _MAP = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    return "".join(_MAP.get(ch, ch) for ch in s)


def _prefix_match(tok: str, found_tokens: set) -> bool:
    for f in found_tokens:
        if tok == f:
            return True
        n = min(len(tok), len(f))
        if n >= 3 and (tok.startswith(f) or f.startswith(tok)):
            return True
        # Транслитерация: «лемакс»(кир) ≈ «lemax»(лат). Сравниваем обе стороны;
        # «Лемакс»→«lemaks» vs «lemax» — общий префикс >= 4 (k/ks различие).
        tt = _translit(tok)
        tf = _translit(f)
        if n >= 4 and tt != tf and (
            tt.startswith(tf) or tf.startswith(tt) or tt[:4] == tf[:4]
        ):
            return True
    return False


def _is_optional_token(w: str) -> bool:
    """Токен, наличие которого в названии найденного товара не обязательно.

    Параметры («ру16», «kvs», «нр5-35») и значения с цифрами («220в», «1,9»,
    «b69») указываются в спецификации, но часто отсутствуют в title карточки.
    Описательные слова (серия/комплектация) здесь НЕ игнорируются — система
    не решает за LLM; их отсутствие выносится в advisory-совет (см.
    _mismatch_warning_content), где LLM сам перепроверяет карточку.
    """
    if w in _PARAM_SET:
        return True
    return bool(re.search(r"\d", w))


_MODEL_CODE_RE = re.compile(
    r"(?<![a-zа-яё0-9])([a-zа-яё]{1,4})\s*[-]?\s*(\d{1,3})(?![0-9])",
    re.IGNORECASE,
)
# Префиксы, которые НЕ являются моделью/типом: параметры (ру/pn/kvs…), диаметры
# (ду/dn/dp), разделитель размера (х/x/×), единицы и служебные (тип/размер/гост/ту).
_MODEL_PREFIX_EXCLUDE = frozenset({
    "ду", "дн", "dn", "dp", "мм", "тип", "размер", "гост", "ту",
    "х", "x", "×", "pn", "ру", "нр", "np", "kvs", "kv", "бар", "па",
    "атм", "тмакс", "макс", "мин", "max", "min", "град",
})

# Разделитель секций/модификаций товара: «-0,9-2», «-0,9-4» — количество секций
# в чугунных радиаторах (МС-140 Мх500-0,9-2 ≠ МС-140 Мх500-0,9-4). Без этого
# rule-8 переиспользовал цену 4-секционного радиатора для всех вариантов.
_SECTION_COUNT_RE = re.compile(r"-0,9-(\d+)", re.IGNORECASE)

# Кириллическая «х» и знак «×» в позиции после буквы/цифры и перед цифрой —
# разделитель размера/типа («500х600», «Мх500», «C10х500»). Нормализуем к латинской
# «x», чтобы «Мх500» и «Мx500», «140х500» и «140x500» считались одним и тем же
# (регрессия: rule-8/гид не находили МС-140, т.к. model_designators давал
# кириллический «мх500» в spec и латинский «мx500» в карточке).
# «х» в начале слова («характеристика», «хомутик») не затрагивается — перед ним нет
# word-символа.
_DIM_SEP_RE = re.compile(r"(?<=\w)[х×](?=\d)")


def _norm_dim_sep(text: str) -> str:
    """Заменяет «х»/«×» на «x» в позиции «…х<цифра» («500х600»→«500x600»,
    «Мх500»→«Мx500»).

    Слова («характеристика», «хомутик») не затрагиваются — перед «х» нет буквы/цифры.
    """
    if not text:
        return text
    return _DIM_SEP_RE.sub("x", text)


def model_designators(text: str) -> set[str]:
    """Коды моделей/типов товара (дифференциаторы реюза): «C10», «C 10»,
    «VC33», «MS-140» → канонические «c10», «vc33», «ms140».

    Сравнение на СЫРОМ тексте: h1 «Premium C 10х500х600» токенизируется в
    «10х500х600» БЕЗ токена «c10» (тип слит с размером) — по токенам модель
    не увидеть. Исключаются префиксы-параметры/диаметры и разделитель «х».
    """
    low = _norm_dim_sep(text or "").lower()
    if not low:
        return set()
    excluded = _MODEL_PREFIX_EXCLUDE | _PARAM_SET | _STOPWORDS_SET
    out: set[str] = set()
    for m in _MODEL_CODE_RE.finditer(low):
        prefix, digits = m.group(1), m.group(2)
        if prefix in excluded:
            continue
        code = prefix + digits
        if len(code) >= 3:
            out.add(code)
    # Разделитель секций/модификаций: «-0,9-2» → «-0,9-2» как часть модели,
    # чтобы МС-140 2 секции ≠ МС-140 4 секции (регрессия: rule-8 отдавал
    # цену 4-секционного радиатора на все варианты).
    for m in _SECTION_COUNT_RE.finditer(low):
        out.add(f"-0,9-{m.group(1)}")
    return out


_MATERIAL_SET = frozenset({
    "стальной", "латунный", "чугунный", "медный", "алюминиевый",
    "биметаллический", "оцинкованный", "нержавеющий",
    "сталь", "латунь", "чугун", "медь", "алюминий",
    "полипропиленовый", "пвх", "резиновый",
})


def mismatch_kind(spec_text: str, found_name: str, spec_meta: dict | None = None) -> str:
    """Тип расхождения наименований: 'none' | 'descriptive_only' | 'key'.

    key — различается МОДЕЛЬ (C10 vs C20), размер (Ду15 vs Ду20), бренд, или
    отсутствуют структурные ключевые слова (панельный vs секционный, кран vs
    клапан) — вероятно другой товар;
    descriptive_only — отличаются только описательные слова (серия/комплектация)
    и/или МАТЕРИАЛ-прилагательные (сайт опускает их в сокращённом h1, карточка
    подтверждает). Модель сравнивается на сыром тексте («C 10х500х600»).

    Артикул/код из spec_meta — железный идентификатор: если он совпадает с
    найденным названием, товар тот же (расхождение не может быть "key").
    """
    # Артикул — решающий признак: если он совпадает, товар тот же.
    if spec_meta:
        article = (spec_meta.get("article") or "").strip()
        if article and article.lower() in (found_name or "").lower():
            return "descriptive_only"
    spec_models = model_designators(spec_text)
    if spec_models and model_designators(found_name) != spec_models:
        return "key"
    spec_sizes = _size_key(spec_text)
    found_sizes = _size_key(found_name)
    if spec_sizes and found_sizes and spec_sizes != found_sizes:
        return "key"
    spec_brand = _brand_of(spec_text)
    found_brand = _brand_of(found_name)
    if spec_brand and found_brand and spec_brand != found_brand:
        return "key"
    missing = missing_required_tokens(spec_text, found_name)
    if not missing:
        return "none"
    if all(w in _OPTIONAL_SET or w in _MATERIAL_SET for w in missing):
        return "descriptive_only"
    return "key"


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


def product_name_matches(spec_text: str, found_name: str,
                         strict_sizes: bool = False,
                         ignore_sizes: bool = False) -> bool:
    """Проверка: найденный товар соответствует позиции спецификации (с брендом).

    strict_sizes=True — расхождение размеров отклоняет совпадение, даже если
    размер указан только с одной стороны. Для авто-реюза цен (rule 8, кэш),
    где ошибка дороже пропуска.
    ignore_sizes=True — размеры в сравнении не участвуют вовсе. Для ГИДА
    («похожие цены семьи», переупорядочивание сайтов): соседний типоразмер
    того же товара — сигнал «сюда идти», но не кандидат на реюз.
    """
    return _product_matches_core(spec_text, found_name, check_brand=True,
                                 strict_sizes=strict_sizes,
                                 ignore_sizes=ignore_sizes)


def product_name_matches_ignore_brand(spec_text: str, found_name: str,
                                      strict_sizes: bool = False,
                                      ignore_sizes: bool = False) -> bool:
    """Совпадение по всем атрибутам, кроме бренда.

    Для кандидатов-фолбэков: товар того же типа/размера/соединения, но другого
    или неизвестного бренда. Бренд в сравнении игнорируется, сокращения соединения
    (по умолчанию «фл») расширяются до полной формы.
    """
    return _product_matches_core(
        _expand_conn_abbrev(spec_text),
        _expand_conn_abbrev(found_name),
        check_brand=False,
        strict_sizes=strict_sizes,
        ignore_sizes=ignore_sizes,
    )


def _product_matches_core(spec_text: str, found_name: str, check_brand: bool = True,
                          strict_sizes: bool = False, ignore_sizes: bool = False) -> bool:
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

    # Модель/тип — дифференциатор на путях РЕЮЗА/ГИДА (strict_sizes/ignore_sizes):
    # «LEMAX Premium C20 500x600» ≠ «LEMAX Premium C10 500x600». Сравнение на
    # сыром тексте (h1 «C 10х500х600» токенизатор не разбивает на «c10»).
    if strict_sizes:
        spec_models = model_designators(spec_text)
        if spec_models and model_designators(found_name) != spec_models:
            return False

    spec_sizes = _size_key(spec_text)
    found_sizes = _size_key(found_name)
    if ignore_sizes:
        pass
    elif strict_sizes:
        # Строгий режим отклоняет, когда размер ИЗВЕСТЕН в спецификации, но не
        # совпадает с найденным (или в найденном отсутствует). Если в спецификации
        # размер не извлечён (None) — нечего проверять, товар может совпадать
        # (МС-140: «Мх500» не даёт пару размеров, а в карточке «140х500»).
        if spec_sizes is not None and spec_sizes != found_sizes:
            return False
    elif spec_sizes and found_sizes and spec_sizes != found_sizes:
        return False

    if check_brand:
        spec_brand = _brand_of(spec_text)
        found_brand = _brand_of(found_name)
        if spec_brand and found_brand and spec_brand != found_brand:
            return False

    return True
