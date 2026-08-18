"""Релевантность подхода/сайта текущему товару.

Проблема: тип товара (например ventilation_climate_ventilation) может содержать
и воздуховоды, и регуляторы скорости. Подходы, сохранённые при обучении на
регуляторе (search_query «SRE-Е-2,5...»), не должны показываться для воздуховода —
иначе агент идёт на чужой (электро) сайт и ищет не то.

Решение: подход релевантен товару, если его сохранённый search_query пересекается
со значимыми словами текущего товара (наименование + артикул). Без пересечения —
подход скрывается. Если данных недостаточно — показываем (безопасный fallback).

Чистый модуль (без Qt/сети) — покрыт тестами.
"""

import re

_WORD_RE = re.compile(r"[a-zа-яё0-9]{3,}")

_STOPWORDS = {
    "для", "из", "с", "со", "на", "по", "в", "во", "и", "не", "или", "к",
    "у", "от", "при", "за", "что", "это", "как", "так", "об", "про",
    "мм", "ду", "типа", "тип", "размер", "новый", "отечественный",
}


def tokenize(text: str) -> set:
    """Значимые слова текста (>=3 символа, без стоп-слов)."""
    if not text:
        return set()
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


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

# Структурные слова: маркеры бренда/ГОСТ. Сами по себе не доказывают сходство —
# «Теплосчетчик, завод-изготовитель Пульсар» и «Кран, завод-изготовитель Ридан»
# не должны считаться похожими из-за общего «завод-изготовитель».
_STRUCTURAL_WORDS = {"завод", "изготовитель", "производитель", "марка", "бренд", "гост", "ту"}

_BRAND_RE = re.compile(
    r"(?:завод[- ]изготовитель|производитель|завод|бренд|марка)"
    r"\s*[:»]?\s*([а-яёa-z0-9][а-яёa-z0-9·&\-()]{1,30})",
    re.IGNORECASE,
)

_DU_RE = re.compile(r"(?:ду|дн|dn|dp)\s?(\d{2,3})", re.IGNORECASE)
_DIM_RE = re.compile(r"(\d{1,3})\s?(?:х|x)\s?(\d{1,4})", re.IGNORECASE)
_MM_RE = re.compile(r"(?:[øØ]\s?(\d{2,4})|(\d{2,4})\s?мм\b)")
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


def _product_tokens(text: str) -> set:
    """Значимые слова товара без размеров/номеров («ду15», «1/2», цифры)."""
    if not text:
        return set()
    tokens = set()
    for w in _WORD_RE.findall(text.lower()):
        if w in _STOPWORDS or w in _STRUCTURAL_WORDS or len(w) < 3:
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


# Параметрические слова: могут отсутствовать в названии карточки на сайте
# (указаны в характеристиках/описании) — не обязательны при сравнении товаров.
_PARAM_WORDS = {
    "ру", "pn", "нр", "np", "kvs", "kv", "бар", "па", "атм",
    "тмакс", "макс", "мин", "max", "min", "град",
}


def _is_optional_token(w: str) -> bool:
    """Токен, наличие которого в названии найденного товара не обязательно.

    Параметры («ру16», «kvs», «нр5-35») и значения с цифрами («220в», «1,9»,
    «b69») указываются в спецификации, но часто отсутствуют в title карточки.
    """
    if w in _PARAM_WORDS:
        return True
    return bool(re.search(r"\d", w))


def _expand_conn_abbrev(text: str) -> str:
    """Разворачивает сокращения типов соединения, отбрасываемые токенизатором.

    «фл» — 2 символа, выпадает из _WORD_RE, из-за чего обязательный токен
    «фланцевый» не находится в названиях карточек («Ду 100 Ру16 фл Kvs=...»).
    """
    if not text:
        return text
    return re.sub(r"\bфл\b", "фланцевый", text, flags=re.IGNORECASE)


def product_name_matches(spec_text: str, found_name: str) -> bool:
    """Проверка: найденный товар соответствует позиции спецификации (с брендом)."""
    return _product_matches_core(spec_text, found_name, check_brand=True)


def product_name_matches_ignore_brand(spec_text: str, found_name: str) -> bool:
    """Совпадение по всем атрибутам, кроме бренда.

    Для кандидатов-фолбэков: товар того же типа/размера/соединения, но другого
    или неизвестного бренда. Бренд в сравнении игнорируется, «фл» расширяется
    до «фланцевый» (частая аббревиатура соединения в названиях карточек).
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
