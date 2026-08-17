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


def _product_tokens(text: str) -> set:
    """Значимые слова товара без размеров/номеров («ду15», «1/2», цифры)."""
    if not text:
        return set()
    tokens = set()
    for w in _WORD_RE.findall(text.lower()):
        if w in _STOPWORDS or len(w) < 3:
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
        if n >= 4 and (tok.startswith(f) or f.startswith(tok)):
            return True
    return False


def product_name_matches(spec_text: str, found_name: str) -> bool:
    """Проверка: найденный товар соответствует позиции спецификации.

    Сравниваются значимые слова (без размеров/номеров) с учётом словоформ
    («автоматический» ≈ «автомат»). Допуск: >=2 общих слова, либо единственное
    слово короткого наименования («Воздуховод Ø100» → «Воздуховод оцинкованный»).

    «Кран шаровой Ду15» vs «Клапан балансировочный Ду15» → False (разные товары).
    Недостаточно данных (нет названия) → True (не отклоняем).
    """
    spec_tokens = _product_tokens(spec_text)
    found_tokens = _product_tokens(found_name)
    if not spec_tokens or not found_tokens:
        return True
    matched = sum(1 for s in spec_tokens if _prefix_match(s, found_tokens))
    if matched >= 2:
        return True
    if matched == 1 and len(spec_tokens) == 1:
        return True
    return False
