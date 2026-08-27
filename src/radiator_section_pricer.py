"""Расчёт цены секционного радиатора при ненахождении точного количества секций.

ТОЛЬКО для радиаторов (plumbing_heating_radiators) с суффиксом -0,9-N.
Если агент исчерпал все сайты, но точное количество секций не найдено —
рассчитывает цену из известного варианта той же модели (цена за секцию × N).

Применимость проверена: цена за секцию МС-140 стабильна (4 секции = 5636.8,
7 секций = 9865.8, расхождение 0.014% — погрешность округления).
"""

import logging
import re
from typing import Optional

from src.approach_relevance import model_designators

logger = logging.getLogger("pricer.radiator")

_SECTION_RE = re.compile(r"-0,9-(\d+)", re.IGNORECASE)
_RADIATOR_PRODUCT_TYPES = {"plumbing_heating_radiators"}


def extract_sections(spec_text: str) -> Optional[int]:
    """Извлекает количество секций из spec_text: «МС-140 Мх500-0,9-4» → 4."""
    if not spec_text:
        return None
    m = _SECTION_RE.search(spec_text)
    return int(m.group(1)) if m else None


def _models_without_section(spec_text: str) -> set:
    """Модельные коды без суффикса секций — для поиска совпадающей модели.

    «МС-140 Мх500-0,9-4» → {мс140, мх500} (без -0,9-4).
    """
    models = model_designators(spec_text)
    return {m for m in models if not _SECTION_RE.match(m)}


def _find_base_price(mm, product_type: str, spec_text: str) -> Optional[dict]:
    """Ищет в confirmed_prices запись той же модели с другим количеством секций.

    Возвращает dict с ценой за секцию, известным количеством секций и сайтом:
    {'price_per_section': float, 'base_sections': int, 'base_price': float, 'site_id': str}
    или None, если подходящая база не найдена.

    Предпочитает базу с БОЛЬШИМ количеством секций (более точное усреднение).
    """
    if product_type not in _RADIATOR_PRODUCT_TYPES:
        return None
    need_sections = extract_sections(spec_text)
    if need_sections is None:
        return None
    models = _models_without_section(spec_text)
    if not models:
        return None

    from src.graph_engine import _is_invalid_price_url

    candidates = mm.get_relevant_prices(spec_text, strict_sizes=False, ignore_sizes=True)
    found = []
    for c in candidates:
        c_spec = c.get("spec_text") or ""
        c_sections = extract_sections(c_spec)
        if c_sections is None or c_sections == need_sections:
            continue
        c_models = _models_without_section(c_spec)
        if not c_models or c_models != models:
            continue
        c_price = c.get("price")
        if not c_price or c_price <= 0:
            continue
        if _is_invalid_price_url(c.get("url") or ""):
            continue
        price_per_section = c_price / c_sections
        found.append((c_sections, c_price, price_per_section, c.get("site_id", "?")))

    if not found:
        return None
    # Сортируем: больше секций — лучше усреднение, при равных — ниже цена за секцию
    found.sort(key=lambda x: (-x[0], x[2]))
    best_sections, best_price, best_pps, best_site = found[0]
    return {
        "price_per_section": round(best_pps, 2),
        "base_sections": best_sections,
        "base_price": best_price,
        "site_id": best_site,
    }


def calculate_radiator_price(mm, spec_text: str, product_type: str) -> Optional[dict]:
    """Рассчитывает цену радиатора на основе цены другой секции той же модели.

    Вызывается ТОЛЬКО после того, как все сайты проверены и точная цена не найдена.
    Возвращает result-dict или None, если расчёт невозможен.
    """
    need_sections = extract_sections(spec_text)
    if need_sections is None:
        return None
    if product_type not in _RADIATOR_PRODUCT_TYPES:
        return None

    base = _find_base_price(mm, product_type, spec_text)
    if base is None:
        return None

    calculated_price = round(base["price_per_section"] * need_sections, 2)
    logger.info("🔢 Радиатор: рассчитана цена %s секций = %.2f × %s = %.2f (из %s секций за %.2f на %s)",
                need_sections, base["price_per_section"], need_sections,
                calculated_price, base["base_sections"], base["base_price"], base["site_id"])

    return {
        "price": calculated_price,
        "confidence": 0.70,
        "url": "",
        "site": base["site_id"],
        "reason": (f"рассчитано программно: {need_sections} секций × {base['price_per_section']} "
                   f"руб/секция (из {base['base_sections']} секций за {base['base_price']} руб на {base['site_id']})"),
        "requires_review": True,
    }