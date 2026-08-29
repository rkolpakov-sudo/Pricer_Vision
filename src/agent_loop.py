import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse

from src.llm_client import LLMClient
from src.tool_parser import parse_tool_calls, parse_final_response, parse_text_tools, parse_text_result
from src.mcp_bridge import MCPBridge, _is_hash_ref
from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.validator import validate_result
from src.config_loader import get_run_config, get_antidetect_config, get_antidetect_site_overrides
from src.models.schemas import ExtractionResult
from src.stuck_detector import StuckDetector, StuckLevel
from src.resilience import llm_circuit
from src.adaptive_limits import AdaptiveRoundManager
from src.rate_limiter import DomainRateLimiter
from src.captcha_detector import CaptchaDetector, CaptchaType
from src.approach_relevance import (
    approach_relevant, product_name_matches, product_name_matches_ignore_brand,
    missing_required_tokens, normalize_search_text, is_standard_reference,
    search_key_tokens, mismatch_kind, model_designators, _size_key,
)
from src.session_facts import RowFacts, SessionFacts

logger = logging.getLogger("pricer.agent")

MAX_ROUNDS = get_run_config("max_rounds", 60)
MAX_ROUNDS_PER_SITE = get_run_config("max_rounds_per_site", 15)
DIAGNOSTIC_PROMPT_CAP = get_run_config("diagnostic_prompt_cap", 2)
EMPTY_PROBE_LIMIT = get_run_config("empty_probe_limit", 3)
SUMMARIZE_MAX_CHARS = get_run_config("summarize_max_chars", 8000)
SUMMARIZE_MAX_LINES = get_run_config("summarize_max_lines", 200)
CAPTCHA_KEYWORDS = get_run_config("captcha_keywords", ["ddos-guard", "hcheck", "js-check"])
SEARCH_ENGINE = get_run_config("search_engine", "Яндекс")
UNKNOWN_PT = "unknown"
_RADIATOR_PRODUCT_TYPES = {"plumbing_heating_radiators"}
CONF_TRUSTED = 0.9
CONF_GOOD = 0.8
CONF_MIN = 0.6

# Верхний предел символов для browser_evaluate-результата в сообщении LLM.
# Один JSON-результат до 8k символов проходит целиком (лимит строк не работает
# для однострочного JSON) и раздувает контекст. Цена-кандидат извлекается из
# ПОЛНОГО результата до усечения и подставляется в начало — LLM её не теряет.
EVALUATE_MESSAGE_CAP = 6000
# Повтор запроса считается дублем, если он уже вводился на том же домене.
QUERY_DUP_SKIP_AFTER = 1
# «Монстр»-запрос: вставка всего spec_text (сигнатура — ';' между частями).
MONSTER_QUERY_MARKER = ";"
MONSTER_QUERY_MIN_LEN = 70

TEMP_EXPLORATION = 0.7
TEMP_NAVIGATION = 0.3
TEMP_EXTRACTION = 0.1
TEMP_RECOVERY = 0.5

_SNAPSHOT_LINE_RE = re.compile(r'^- \d+: \[')

GRAPH_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "get_approaches",
            "description": "Получить успешные подходы из графа знаний. product_type + site — конкретный подход; только site — все подходы для сайта (из любых товаров). Вызови ПЕРВЫМ.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара (ups, cable_vvg...). Опционально — если не указан, вернутся подходы для всех товаров на сайте."},
                    "site": {"type": "string", "description": "Домен (опционально)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_approach",
            "description": "Сохранить успешный подход в граф. Вызови ПОСЛЕ того, как цена найдена.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string"},
                    "site": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "object"}},
                    "selectors": {"type": "string", "description": "JSON селекторов"},
                    "method": {"type": "string"},
                    "search_query": {"type": "string"},
                    "notes": {"type": "string"}
                },
                "required": ["product_type", "site", "steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_confirmed_prices",
            "description": "Похожие подтверждённые цены (few-shot примеры).",
            "parameters": {
                "type": "object",
                "properties": {"spec_text": {"type": "string"}},
                "required": ["spec_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_confirmed_price",
            "description": "Записать подтверждённую цену в граф. Обязательно передай product_name — точное название товара со страницы. Система проверит наименование и, если оно не соответствует спецификации, вернёт критическое замечание — тогда исправь product_name или, если уверен в товаре, вызови повторно с confirm=true. Если товар совпадает по всем атрибутам КРОМЕ бренда — передай brand_mismatch=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec_text": {"type": "string"},
                    "product_type": {"type": "string"},
                    "site": {"type": "string"},
                    "price": {"type": "number"},
                    "url": {"type": "string"},
                    "confidence": {"type": "number"},
                    "product_name": {"type": "string", "description": "Полное наименование товара с заголовка карточки (h1)"},
                    "reason": {"type": "string"},
                    "brand_mismatch": {"type": "boolean", "description": "true, если товар совпадает по всем атрибутам, кроме бренда"},
                    "confirm": {"type": "boolean", "description": "true, если подтверждаешь товар несмотря на критическое замечание системы (цена будет помечена как требующая ревью)"}
                },
                "required": ["spec_text", "product_type", "site", "price", "url", "confidence", "product_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_sites",
            "description": "Получить список сайтов для типа товара из графа.",
            "parameters": {
                "type": "object",
                "properties": {"product_type": {"type": "string"}},
                "required": ["product_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_discovered_site",
            "description": "Сохранить новый сайт, найденный через поиск. Вызови когда нашёл товар на сайте, которого нет в известных.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Домен (без https://)"},
                    "name": {"type": "string", "description": "Название сайта"},
                    "product_type": {"type": "string", "description": "Тип товара"},
                    "approach_steps": {"type": "array", "description": "Шаги для поиска на этом сайте", "items": {"type": "object"}}
                },
                "required": ["domain", "name", "product_type", "approach_steps"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_hints",
            "description": "Получить подсказки для типа товара. Подсказки содержат информацию о том, КАК искать цены на конкретных сайтах: селекторы, методы поиска, особенности. Вызови когда не уверен, как работать на незнакомом сайте.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара (обязательно)"}
                },
                "required": ["product_type"]
            }
        }
    },
]

GRAPH_TOOL_NAMES = {t["function"]["name"] for t in GRAPH_TOOL_DEFS}

SYSTEM_PROMPT = """Ты — опытный пользователь с доступом к браузеру и базе знаний.

База знаний (граф):
- get_approaches: готовые подходы для (тип_товара, сайт) — следуй им если есть
- search_sites: известные сайты для типа товара
- get_confirmed_prices: ранее найденные цены
- get_hints: подсказки по работе на сайтах

Правила:
1. Сначала проверь get_approaches. Если есть подход — используй его target/element (CSS-селекторы) и последовательность действий. ВАЖНО: каждый подход привязан к КОНКРЕТНОМУ сайту (префикс «site.ru:» перед шагами). Используй target из подхода ТОЛЬКО на сайте этого подхода. НО: url для browser_navigate должен вести на ГЛАВНУЮ сайта или страницу поиска, а не на конкретный товар из подхода. Текст для поиска (query, text) бери из КЛЮЧЕВЫХ ТОКЕНОВ и ТОВАР ДЛЯ ПОИСКА: вводи запрос, сохраняющий бренд + серию + тип + размер/Ду (например «LEMAX Premium C10 500x600»). Шаблонный хвост («в компл. с краном…», «с боковым подключением») можно опускать, НО размер/тип/Ду — НИКОГДА. При пустом результате ТОЧНОГО запроса (с размером/Ду) сначала проверь загрузку выдачи (browser_wait_for 2с + повторное извлечение) и ТОЛЬКО после повтора упрощай запрос, ОБЯЗАТЕЛЬНО сохраняя размер/тип/Ду.
2. Работай с ОДНИМ сайтом за раз. НЕ переключайся между сайтами без причины.
3. После browser_navigate на НОВЫЙ сайт: СНАЧАЛА возьми browser_snapshot (или browser_find по тексту поля, например «Поиск»/«Найти») чтобы найти поле поиска и кнопку НА ЭТОМ сайте. НЕ переноси локатор с другого сайта — role-локаторы («textbox "Поле поиска…"») с чужого подхода не работают. ТОЛЬКО после нахождения поля на текущей странице вводи запрос через browser_type. browser_snapshot даёт accessibility-tree. Если цены не видны — используй browser_evaluate с JS (querySelectorAll) для прямого извлечения данных из DOM.
4. После поиска на сайте: кликни на карточку товара → откроется страница с ценой. Если цены нет в карточке — ищи на странице через browser_evaluate.
5. Если точного совпадения нет — сохрани лучший найденный АНАЛОГ через save_confirmed_price с confidence 0.3-0.5 и requires_review=True, НО только если это товар ТОГО ЖЕ ТИПА (кран для крана, клапан для клапана, воздуховод для воздуховода). НЕ подставляй товар другого типа даже как аналог. Укажи в reason расхождение в названии. Можно найти лучшую цену на другом сайте — она перезапишет эту. НЕ трать раунды на поиск более точного совпадения на том же сайте.
6. После нахождения цены: save_confirmed_price + save_approach.
7. Если цена не найдена — верни null, не выдумывай.
8. Если get_confirmed_prices вернул цену с confidence >= 0.9 — используй её как финальную, НЕ проверяй в браузере. Сразу вызови save_confirmed_price.
9. Если ты сделал >10 шагов на одном сайте без результата — принудительно переключись на другой сайт из списка.
10. Если не знаешь, как работать на сайте — вызови get_hints. В хинтах может быть написано, где искать цену, какие селекторы использовать.
11. Если артикул не дал результата на первом сайте — на следующем сайте ищи уже по ПОЛНОМУ названию товара из спецификации, а не по артикулу.
12. Яндекс — это ТОЛЬКО поисковик для нахождения сайта магазина. Если у тебя нет сайтов для товара — иди на yandex.ru, найди товар, кликни на ссылку магазина из результатов поиска и извлеки цену ИЗ КАРТОЧКИ ТОВАРА НА САЙТЕ МАГАЗИНА. НЕ извлекай цену из сниппета Яндекса — Яндекс не источник цен.
13. После save_confirmed_price можно продолжить поиск на других сайтах для лучшей цены, но базовая цена уже сохранена.
14. Если сайт явно НЕ ПОДХОДИТ для товара (например, сантехнический сайт для кабеля, или производитель труб для электроники) — НЕМЕДЛЕННО переключайся на следующий сайт. Не трать больше 2 раундов на заведомо неподходящий сайт.
15. НЕ собирай URL поиска вручную и НЕ делай percent-кодирование кириллицы руками
    (например, не пиши %D0%B5/%D1%85 — это ломает запрос; «х» = %D1%85, а не %D0%B5 = «е»).
    Ищи через поисковую строку сайта: browser_type в поле поиска → Enter. Если сайт SPA и после Enter
    результаты не появились — введи запрос в поисковую строку, отправь, затем
    СКОПИРУЙ получившийся URL из адресной строки (browser_evaluate: location.href)
    и открой его через browser_navigate. Никогда не изобретай кодировку сам.
    Перед вводом НОВОГО запроса ОЧИСТИ поле поиска (JS: inp.value='' + dispatchEvent(new Event('input')),
    или select-all + delete) — иначе старый текст склеится с новым.
16. Если в структуре файла указан завод-изготовитель, тип/обозначение или артикул/код — используй их для правильного выбора товара. Бренд/тип не обязательно вставлять в поисковый запрос: сначала найди товар по наименованию, затем среди результатов отдай предпочтение позиции того же производителя/модели/артикула. Если товар выпускается несколькими заводами — это критично для выбора правильного аналога. Если «производитель» — это страна (например «Россия») или ссылка на стандарт (ГОСТ/ТУ/СНиП) — НЕ используй их как бренд и не вставляй в поиск.
17. При вызове save_confirmed_price ВСЕГДА передавай product_name — полное наименование товара с ЗАГОЛОВКА карточки (h1), НЕ сокращай и НЕ перефразируй. Система проверит соответствие спецификации. Если она вернёт СОВЕТ («Не ошибся ли ты…») — это НЕ отказ и НЕ приговор: система лишь советует, а решение принимаешь ТЫ после перепроверки. Перепроверь заголовок h1 и соответствие товара (тип, соединение, Ду, материал). Если наименование было неполным — исправь product_name и сохрани снова. Если уверен, что товар верен — сохрани повторно с confirm=true (цена будет помечена как требующая ревью). ВАЖНО: если ты УЖЕ находишься в карточке товара и извлёк из неё цену и h1 — ПЕРВЫМ ДЕЛОМ сохрани цену (при расхождении только в описательных словах серии/комплектации — с confirm=true), и только ПОСЛЕ сохранения, если остались серьёзные сомнения, можешь перепроверить характеристики на той же карточке. НЕ уходи с карточки и НЕ проверяй серию/комплектацию на других сайтах и в Яндексе, пока цена не сохранена: уход из карточки с уже извлечённой ценой = потерянный результат. Кран шаровой и клапан балансировочный — это РАЗНЫЕ товары, воздуховод и воздухоотводчик — РАЗНЫЕ товары.
18. Извлечение цены из карточки: цена на сайте может отображаться с любым символом — «₽», «P», «р.», «руб» или без него. НЕ ищи только символ «₽» — это самая частая ошибка. Ищи по классам: querySelectorAll('[class*="price"], [class*="price"] span, .product-price, [data-price]') и бери textContent каждого элемента. Первый элемент с классом-содержащим "price" может оказаться НЕ ценой (например, иконка «Корзина» или «В корзину») — собери ВСЕ кандидаты в массив и выбери тот, где текст похож на число с символом валюты.
19. JS в browser_evaluate: пиши КОРОТКИЙ код, который закрывается фигурной скобкой. НИКОГДА не ставь `//`-комментарий в конце строки перед закрывающей скобкой — скобка после `//` игнорируется и JS падает с SyntaxError. Если нужен комментарий — пиши его ОТДЕЛЬНОЙ строкой перед кодом.
20. Открывай карточку товара ТОЛЬКО если название результата поиска УЖЕ явно содержит тип товара, тип соединения и Ду/размер из спецификации (бренд в названии результата не обязателен — он может быть не указан). Если в названии результата нет типа соединения (фланцевый/резьбовой/муфтовый/Rp), нет Ду/размера или они ПРОТИВОРЕЧАТ спецификации (резьбовой вместо фланцевого, ручной вместо автоматического, латунь вместо чугуна) — НЕ открывай карточку «для проверки полного названия»: это потерянный раунд, ищи дальше в результатах или переключайся на другой сайт. Если открытая карточка оказалась неподходящей — НЕ извлекай цену и НЕ ищи «варианты» на странице, вернись к результатам или переключись на другой сайт (максимум 1 шаг на проверку заголовка). Если нужный размер/тип не виден на первой странице выдачи — проверь ссылки пагинации (a[href*=page], a[href*=str], a[href*=perPage]) и открой следующую страницу; НЕ извлекай одну и ту же страницу повторно.
21. Бренд — НЕ жёсткий атрибут. Если найден товар, который совпадает со спецификацией по ВСЕМ атрибутам КРОМЕ бренда (тип, тип соединения, материал, Ду/размер, автоматический/ручной — совпадают; бренд другой или не указан) — извлеки его цену и сохрани через save_confirmed_price с параметром brand_mismatch=true. Это КАНДИДАТ-ФОЛБЭК: не выводи его как финальную цену и НЕ прекращай поиск — продолжай искать точный товар (с нужным брендом) на других сайтах. Если по итогам поиска по всей строке точный товар так и не найден — строка автоматически заполняется лучшим таким кандидатом и помечается «не совпадает бренд». Если в спецификации бренда нет (не указан завод-изготовитель) — совпадение по типу/соединению/Ду уже является полным совпадением, сохраняй обычным способом без brand_mismatch.

Ограничение — 60 шагов на один товар. У тебя полная свобода действий. Кратко поясняй свои намерения перед каждым действием."""


INTENT_EMOJI = {
    "click_search_button": "🔍",
    "open_product_card": "📦",
    "type_search_query": "⌨️",
    "submit_search": "↵",
    "open_site_page": "🌐",
    "extract_price_content": "💰",
    "find_price_element": "🎯",
    "observe_page": "📄",
    "wait_for_load": "⏳",
    "open_catalog": "📂",
    "close_modal": "✖",
    "click_element": "👆",
    "type_text": "📝",
    "press_key": "⌨️",
    "find_dom_element": "🔎",
    "open_search_engine": "🔍",
}


def format_steps(concrete: list[dict]) -> str:
    parts = []
    for s in concrete[:5]:
        intent = s.get("intent") or s.get("action", "?")
        emoji = INTENT_EMOJI.get(intent, "•")
        target = s.get("url") or _portable_step_target(s)
        desc = f"{emoji} {intent}"
        if target and len(target) < 60:
            desc += f"[{target}]"
        parts.append(desc)
    return " → ".join(parts)


def format_steps_detailed(concrete: list[dict]) -> str:
    lines = []
    for i, s in enumerate(concrete[:5], 1):
        action = s.get("action", s.get("intent", "?"))
        parts = [action]
        for k in ("url", "text", "key", "js_summary"):
            v = s.get(k)
            if v:
                val = str(v)[:80]
                parts.append(f"{k}={val}")
        portable = _portable_step_target(s)
        if portable:
            parts.append(f"target={portable}")
        lines.append(f"      {i}. {' '.join(parts)}")
    return "\n".join(lines)


def _portable_step_target(step: dict) -> str:
    """Портабельный локатор шага подхода для показа LLM.

    Playwright-рефы (e82, f2e17) — внутренние идентификаторы accessibility-дерева,
    они зависят от сессии и БЭКЕНДА: подход, сохранённый под Playwright, содержит
    ref `e82`, который в Camoufox не существует (другой ref-генератор). Поэтому
    для шагов click/type показываем element (роль/описание) или text — то, что
    переносимо между бэкендами.

    Если target — хеш-реф и element не сохранён — возвращаем '' (LLM сам найдёт
    элемент на странице), а НЕ text: text в шаге ввода — это значение для ввода,
    а не локатор поля; подстановка его как target вводит агента в заблуждение.
    """
    ref = str(step.get("target") or step.get("ref") or "")
    if ref and not _is_hash_ref(ref):
        return ref
    elem = str(step.get("element") or "")
    if elem:
        return elem
    if step.get("_auto_target"):
        return ""
    return str(step.get("text") or "")


def _apply_approach(approach: dict, spec_text: str) -> dict:
    adapted = dict(approach)
    # always update search_query to current spec_text
    adapted["search_query"] = spec_text[:200]
    slots = approach.get("param_slots") or {}
    adapted["concrete"] = []
    for step in approach.get("concrete", []):
        step = dict(step)
        # Исторические подходы могли сохранить target как хеш-реф accessibility-дерева
        # (e80, e81) — они недействительны между сессиями/бэкендами. Если element/роль
        # не сохранены — такой target бесполезен, LLM должен сам найти элемент на странице.
        ref = str(step.get("target") or "")
        if ref and _is_hash_ref(ref) and not step.get("element"):
            step.pop("target", None)
            step.pop("ref", None)
            step["_auto_target"] = True
        slot_name = step.get("param_slot")
        if slot_name and slot_name in slots:
            for field in ("text", "url", "value"):
                if field in step and isinstance(step[field], str):
                    step[field] = step[field].replace(f"{{{slot_name}}}", spec_text[:200])
        elif step.get("action") in ("browser_type", "type_text") and isinstance(step.get("text"), str):
            # Текст в шаге ввода: либо шаблон с плейсхолдером {slot}, либо жёстко
            # зашитый текст СТАРОГО товара (подход сохранён от другого продукта).
            # Во втором случае подменяем на текущий — иначе агент ищет чужое имя.
            text = step["text"]
            placeholders = re.findall(r"\{(\w+)\}", text)
            if placeholders and any(p in slots for p in placeholders):
                for sname in slots:
                    text = text.replace(f"{{{sname}}}", spec_text[:200])
                step["text"] = text
            else:
                step["text"] = spec_text[:200]
        adapted["concrete"].append(step)
    adapted["_adapted"] = True
    return adapted


def _summarize_tool(name: str, args: dict) -> str:
    if name == "browser_navigate":
        return f"🌐 Открываю {args.get('url', '...')}"
    if name == "browser_snapshot" or name == "snapshot":
        return "📄 Смотрю что на странице..."
    if name == "browser_type" or name == "type_text":
        return f"📝 Ввожу '{args.get('text', '')}'"
    if name == "browser_click" or name == "click":
        target = args.get('target') or args.get('ref', '?')
        return f"👆 Клик по элементу {target}"
    if name == "browser_press_key" or name == "press_key":
        return f"⌨️ Нажимаю {args.get('key', '')}"
    if name == "browser_wait_for" or name == "wait":
        return "⏳ Жду загрузки..."
    if name == "browser_evaluate":
        js = str(args.get('function', ''))[:150]
        return f"⚡ JS → {js}"
    if name == "browser_take_screenshot":
        return "📸 Делаю скриншот"
    if name in ("get_approaches", "search_sites", "get_confirmed_prices"):
        return f"📊 {name}({args.get('product_type', args.get('spec_text', ''))})"
    if name == "save_confirmed_price":
        return f"💾 Сохраняю цену {args.get('price', '')}"
    if name == "save_approach":
        return "💾 Сохраняю подход"
    if name == "save_discovered_site":
        return f"💾 Новый сайт: {args.get('domain', '')}"
    if name == "get_hints":
        return f"💡 Хинты для {args.get('product_type', '')}"
    return f"🔧 {name}"


def _summarize_result(tool_name: str, result: str) -> str:
    if not isinstance(result, str) or result.startswith("error:"):
        return result[:100] if result else "ERR"
    if result in ("ok", "OK", ""):
        return "ok"
    trimmed = result.strip()
    if trimmed.startswith("{"):
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, dict):
                parts = []
                if "price" in parsed:
                    parts.append(f"price={parsed['price']}")
                if "name" in parsed:
                    parts.append(f"name={parsed['name'][:60]}")
                if parts:
                    return ", ".join(parts)[:120]
        except json.JSONDecodeError:
            pass
    for line in trimmed.split("\n"):
        l = line.strip()
        if l and not l.startswith("###") and not l.startswith("```"):
            return l[:120]
    return "ok"


async def process_row(
    spec_text: str,
    llm_client: LLMClient,
    mcp_bridge: MCPBridge,
    graph_engine: GraphEngine,
    memory_manager: MemoryManager,
    stop_event: threading.Event | None = None,
    status_callback: Callable[[str], None] | None = None,
    fresh: bool = True,
    use_approaches: bool = True,
    use_site_ranking: bool = True,
    site_ranking: dict | None = None,
    spec_meta: dict | None = None,
    semantic_cache=None,
    monitor_callback: Callable[[str, object], None] | None = None,
    negative_cache=None,
    site_blacklist=None,
    site_visit_callback: Callable[[str], None] | None = None,
    session_facts: SessionFacts | None = None,
    ductwork_enabled: bool = False,
    _price_candidate_holder: dict | None = None,
) -> dict:
    start_time = datetime.now()
    spec_brand = (spec_meta or {}).get("brand", "") or ""

    def _stop_check():
        if stop_event and stop_event.is_set():
            raise asyncio.CancelledError("stopped by user")

    def _session_success(site: str, url: str = "", query: str = "") -> None:
        """Межстрочный факт: на сайте есть товар (тип|бренд) + рабочий паттерн."""
        if session_facts is not None:
            try:
                session_facts.record_success(product_type, spec_brand, site, url=url, query=query)
            except Exception as e:
                logger.warning("SessionFacts.record_success failed: %s", e)

    def _session_no_product(site: str) -> None:
        """Межстрочный факт: на сайте товара (тип|бренд) не найдено."""
        if session_facts is not None:
            try:
                session_facts.record_no_product(product_type, spec_brand, site)
            except Exception as e:
                logger.warning("SessionFacts.record_no_product failed: %s", e)

    # Сессионный отрицательный кэш: товар уже дважды не найден — не ищем снова
    if negative_cache is not None and negative_cache.is_blocked(spec_text):
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info("Row: negative cache hit — '%s' already not found this session", spec_text[:40])
        result = {
            "spec_text": spec_text, "price": None, "confidence": 0.0,
            "reason": "negative cache: не найдено ранее в сессии",
            "requires_review": True, "error": "not_found_cached", "elapsed": elapsed,
        }
        return _result_to_schema(result)

    product_type = graph_engine.classify_product_type(spec_text)

    # Воздуховоды и фасонные части: детерминированный расчёт МОДУЛЕМ, без
    # обращения в сеть (browser/LLM не вызываются). Строка НЕ кэшируется и
    # НЕ сохраняется в граф — только в Excel.
    if ductwork_enabled:
        from src.ductwork_calculator import calculate_ductwork_row
        try:
            duct_result = calculate_ductwork_row(spec_text, spec_meta,
                                                 product_type=product_type)
        except Exception as e:
            logger.warning("Ductwork calculation failed: %s", e)
            duct_result = None
        if duct_result is not None:
            elapsed = (datetime.now() - start_time).total_seconds()
            duct_result["elapsed"] = elapsed
            logger.info("Row: ductwork price=%.2f (изделие) in %.1fs",
                        duct_result.get("price", 0), elapsed)
            return _result_to_schema(duct_result)

    search_text = normalize_search_text(spec_text)
    if search_text != spec_text:
        logger.info("Search text normalized: '%s' -> '%s' (незначимые фразы убраны из поиска)",
                    spec_text[:60], search_text[:60])
    approaches = [] if not use_approaches else (
        memory_manager.get_all_approaches(product_type) if product_type != UNKNOWN_PT else memory_manager.get_all_approaches_flat()
    )
    # Строгие кандидаты на РЕЮЗ (rule 8): тот же типоразмер обязателен.
    confirmed_prices = [] if fresh else memory_manager.get_relevant_prices(spec_text, strict_sizes=True)

    # code-enforced rule 8: reuse high-confidence prices without LLM
    if not fresh and confirmed_prices:
        # Исключаем цены с невалидными URL (главная/поиск/семейная страница) —
        # они не могут быть источником для переиспользования.
        reusable = [p for p in confirmed_prices
                    if not _is_homepage_or_search_url(p.get("url", ""))
                    and not _is_family_page(p.get("url", ""))]
        if reusable:
            best = max(reusable, key=lambda p: p.get("confidence", 0))
            conf = best.get("confidence", 0)
            # Точное совпадение spec_text (нормализованно) — строка та же: модель и
            # размер гарантированно совпадают (защита П3). Реюз при >= 0.6 вместо 0.9.
            exact_match = _normalized_equal(best.get("spec_text", ""), spec_text)
            reuse_threshold = 0.6 if (exact_match and not best.get("is_stale")) else CONF_TRUSTED
            if conf >= reuse_threshold and (not fresh or conf >= 0.95):
                elapsed = (datetime.now() - start_time).total_seconds()
                result = {
                    "spec_text": spec_text, "price": best.get("price"),
                    "confidence": best.get("confidence", 0),
                    "url": best.get("url", ""), "site": best.get("site_id", ""),
                    "reason": ("Reused from DB (exact spec match)" if exact_match
                               else "Reused from DB (confidence >= 0.9)"),
                    "requires_review": False,
                    "elapsed": elapsed,
                }
                memory_manager.save_price(
                    spec_text=spec_text, product_type=product_type,
                    site=best.get("site_id", ""), price=best.get("price", 0),
                    url=best.get("url", ""), confidence=best.get("confidence", 0),
                    reason="rule8_reuse",
                )
                logger.info("Row: price=%s validated=%.2f in %.1fs", result["price"], result["confidence"], elapsed)
                _session_success(best.get("site_id", ""), url=best.get("url", ""))
                return _result_to_schema(result)

    # Semantic cache: reuse results for similar products (skipped when fresh)
    if not fresh and semantic_cache is not None:
        cached = semantic_cache.get_similar(spec_text)
        if cached and cached.get("confidence", 0) > CONF_GOOD and cached.get("price") is not None:
            if monitor_callback:
                monitor_callback("cache_hit", cached.get("similarity", 0.0))
            elapsed = (datetime.now() - start_time).total_seconds()
            result = {
                "spec_text": spec_text,
                "product_type": product_type,
                "price": cached["price"],
                "confidence": cached["confidence"],
                "url": cached.get("url", ""),
                "site": cached.get("site", ""),
                "reason": f"semantic_cache hit ({cached.get('similarity', 0.0):.2f})",
                "requires_review": False,
                "elapsed": elapsed,
            }
            logger.info("Cache hit for '%s' (similarity: %.2f)", spec_text[:40], cached.get("similarity", 0.0))
            _session_success(cached.get("site", ""), url=cached.get("url", ""))
            return _result_to_schema(result)

    sites = memory_manager.get_sites(product_type)
    if site_blacklist is not None:
        blocked = site_blacklist.blocked_sites()
        if blocked:
            sites = [s for s in sites if s.get("id") not in blocked]
            logger.info("Sites filtered by session blacklist: %s", sorted(blocked))
    # Щадящие кандидаты для ГИДА (контекст, переупорядочивание сайтов):
    # похожие цены семьи (другие типоразмеры) показываются агенту и поднимают
    # сайты в приоритете, НО не попадают в rule-8 реюз (см. confirmed_prices выше).
    guide_prices = [] if fresh else memory_manager.get_relevant_prices(spec_text, strict_sizes=False, ignore_sizes=True)
    # Adaptive rounds per-site: reduce for high-failure sites
    adaptive_limits = AdaptiveRoundManager(base_rounds=MAX_ROUNDS_PER_SITE)
    site_round_limits = adaptive_limits.per_site_limits(sites) if sites else {}
    # Хинты — «память графа»: скрыты при use_approaches=False (чистый поиск без подсказок).
    hints = [] if not use_approaches else (memory_manager.get_hints(product_type) or [])
    if product_type != UNKNOWN_PT and use_approaches:
        hints += memory_manager.get_hints(UNKNOWN_PT)
    product_data = graph_engine._all_products.get(product_type)

    # Подходы-подсказки «как работать на сайтах» — скрыты при use_approaches=False.
    all_flat = [] if not use_approaches else memory_manager.get_all_approaches_flat()
    site_guides = {}
    for a in all_flat:
        sid = a.get("site_id", "")
        if sid:
            site_guides.setdefault(sid, []).append(a)

    # Load SOLD_AT concepts for this product type
    concepts = []
    if product_type != UNKNOWN_PT:
        try:
            rows = graph_engine._conn.execute(
                "SELECT child_name, parent_name, relation FROM concept_edges WHERE child_name = ? AND relation = 'SOLD_AT' LIMIT 5",
                (product_type,)
            ).fetchall()
            concepts = [dict(r) for r in rows]
        except Exception:
            pass

    context = _build_context(search_text, product_type, approaches, guide_prices, sites, hints, product_data, site_guides, concepts, spec_meta,
                             use_site_ranking=use_site_ranking, site_ranking=site_ranking,
                             use_approaches=use_approaches, session_facts=session_facts)

    mcp_tools = await mcp_bridge.list_tools()
    # Close previous page to avoid tab accumulation
    try:
        await mcp_bridge.call_tool("browser_close", {})
    except Exception:
        pass
    # When fresh=True, hide cached prices from the LLM entirely
    if fresh:
        all_tools = mcp_tools + [t for t in GRAPH_TOOL_DEFS if t["function"]["name"] != "get_confirmed_prices"]
        system_prompt = SYSTEM_PROMPT.replace(
            "\n8. Если get_confirmed_prices вернул цену с confidence >= 0.9 — используй её как финальную, НЕ проверяй в браузере. Сразу вызови save_confirmed_price.\n", "\n"
        )
    else:
        all_tools = mcp_tools + GRAPH_TOOL_DEFS
        system_prompt = SYSTEM_PROMPT
    system_prompt = system_prompt.replace("Ограничение — 60 шагов на один товар",
                                          f"Ограничение — {MAX_ROUNDS} шагов на один товар")
    if not use_approaches:
        # «Чистый поиск»: скрываем инструменты памяти-подсказок (подходы, хинты).
        all_tools = [t for t in all_tools if t["function"]["name"] not in ("get_approaches", "get_hints")]
    logger.info("Tools: MCP=%d, graph=%d, total=%d", len(mcp_tools), len(GRAPH_TOOL_DEFS), len(all_tools))

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]

    facts = RowFacts()
    rounds = 0
    facts.set_progress(0, MAX_ROUNDS)

    async def _llm_call(messages, temperature):
        facts.set_progress(rounds, MAX_ROUNDS)
        return await _query_llm(llm_client, messages, all_tools, temperature=temperature,
                                monitor_callback=monitor_callback, facts=facts)

    response = await _llm_call(messages, TEMP_EXPLORATION)
    if "error" in response:
        return _error_result(spec_text, f"LLM: {response['error']}")

    current_site = ""
    rounds_on_site = 0
    steps = []
    stuck_detector = StuckDetector()
    # Кэш повторов browser_evaluate в рамках строки: (домен, хэш JS) -> результат.
    # Агент часто извлекает одну и ту же выдачу повторно одним и тем же JS — это
    # потерянные раунды (регрессия позиции 36). При повторе возвращаем сохранённый
    # результат и подсказываем.
    eval_cache: dict[tuple[str, str], str] = {}
    # Счётчик «мысленных» раундов: LLM возвращает только размышления без tool_calls
    # и без цены (регрессия: позиция 36 — агент 4 раза повторил «ЦМО МС-40 = полка»,
    # не двигаясь дальше). При пороге добавляем напоминание/force-совет.
    content_only_rounds = 0
    CONTENT_ONLY_REMIND = 2
    CONTENT_ONLY_FORCE = 3
    site_timeout_counts: dict[str, int] = {}
    SITE_TIMEOUT_FAIL_FAST = 2

    def _shown_approach_ids(domain: str) -> list:
        """Подходы, показанные агенту для текущего сайта (для точечного штрафа)."""
        if not domain:
            return []
        ids = [a.get("id") for a in approaches if a.get("site_id") == domain and a.get("id")]
        if not ids:
            ids = [a.get("id") for a in all_flat if a.get("site_id") == domain and a.get("id")]
        return ids

    yandex_reminded = False
    yandex_price_saved = False
    price_confirmed = False
    price_candidate_seen = False
    recent_errors: list[str] = []
    diagnostic_prompts = 0
    empty_probe_streak: dict[str, int] = {}
    empty_probe_guidance_sent: set[str] = set()
    search_page_retry_guided: set[str] = set()
    fallback_candidates: list[dict] = []
    rate_limiter = DomainRateLimiter(
        min_interval=get_antidetect_config("rate_limit_min_interval", 1.5),
        max_requests_per_minute=get_antidetect_config("rate_limit_max_requests_per_minute", 20),
        jitter=get_antidetect_config("jitter", 1.0),
        cooldown_seconds=get_antidetect_config("cooldown_seconds", 300),
        site_overrides=get_antidetect_site_overrides(),
    )

    while rounds < MAX_ROUNDS:
        rounds += 1
        _stop_check()

        tool_calls = parse_tool_calls(response)
        content = (response.get("choices") or [{}])[0].get("message", {}).get("content", "")

        if not tool_calls and content:
            tool_calls = parse_text_tools(content)

        if content:
            line = content.strip().split("\n")[0][:200]
            logger.info("🤔 %s", line)
            if status_callback:
                status_callback(f"[{rounds}/{MAX_ROUNDS}] {line}")
        elif tool_calls:
            plan = "; ".join(_summarize_tool(tc.get("name", ""), tc.get("arguments", {})) for tc in tool_calls[:3])
            logger.info("🎯 %s", plan)
            if status_callback:
                status_callback(f"[{rounds}/{MAX_ROUNDS}] {plan}")

        final_attempt = parse_final_response(response)
        if not final_attempt.get("price") and content:
            text_result = parse_text_result(content)
            if text_result and text_result.get("price") is not None:
                final_attempt = {
                    "price": text_result.get("price"),
                    "confidence": float(text_result.get("confidence", 0.5)),
                    "url": text_result.get("url", ""),
                    "site": text_result.get("site", ""),
                    "reason": text_result.get("reason", ""),
                    "requires_review": text_result.get("requires_review", True),
                }

        if final_attempt.get("price") is not None:
            result = validate_result(final_attempt, spec_text)
            if not price_confirmed:
                # Цена не проходила через save_confirmed_price с product_name —
                # проверку соответствия товара выполнить нельзя. Не допускаем её
                # до доверенного кэша: требует ревью, confidence не выше 0.7.
                result["confidence"] = min(result.get("confidence", 0), 0.7)
                result["requires_review"] = True
            if result.get("confidence", 0) >= CONF_GOOD and result.get("price") is not None:
                _save_price_and_approach(memory_manager, spec_text, product_type, result, steps, record_soldat=True, search_query=search_text)
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("Row: price=%s conf=%.2f in %.1fs rounds=%d", result.get('price'), result.get('confidence', 0), elapsed, rounds)
            final = {"spec_text": spec_text, "product_type": product_type, **result, "elapsed": elapsed}
            _store_semantic_cache(semantic_cache, spec_text, final)
            _session_success(result.get("site", "") or result.get("url", ""), url=result.get("url", ""), query=search_text)
            return _result_to_schema(final)

        if not tool_calls:
            content_only_rounds += 1
            messages.append({"role": "assistant", "content": content or "(no output)"})
            if content_only_rounds >= CONTENT_ONLY_FORCE:
                logger.warning("⚠️ Content-only loop (%d rounds) — forcing a decision", content_only_rounds)
                content_only_rounds = 0
                messages.append({
                    "role": "user",
                    "content": ("⚠️ Ты уже несколько раз размышлял без действий. Это зацикливание. "
                                "Сейчас ОБЯЗАТЕЛЬНО сделай ОДНО из двух: "
                                "1) если на текущем сайте есть цена подходящего товара — извлеки её "
                                "(открой карточку или browser_evaluate) и вызови save_confirmed_price; "
                                "2) если точного товара на сайте нет — НЕМЕДЛЕННО переключись на другой "
                                "сайт (browser_navigate). Не размышляй дальше — действуй."),
                })
            elif content_only_rounds >= CONTENT_ONLY_REMIND:
                messages.append({
                    "role": "user",
                    "content": ("⚠️ Ты размышляешь без действий. Напомни себе: либо извлеки цену "
                                "с текущего сайта (карточка/save_confirmed_price), либо переключись на "
                                "другой сайт. Не повторяй один и тот же вывод разными словами."),
                })
            messages.append({"role": "user", "content": "Верни JSON с результатом поиска цены.\nФормат: {\"price\": число|null, \"confidence\": 0.0-1.0, \"url\": \"...\", \"site\": \"...\", \"reason\": \"...\", \"requires_review\": bool}"})
            _stop_check()
            response = await _llm_call(messages, TEMP_EXTRACTION)
            if "error" in response:
                return _error_result(spec_text, f"LLM: {response['error']}")
            continue
        content_only_rounds = 0
        msg = (response.get("choices") or [{}])[0].get("message", {})

        messages.append(msg)

        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("arguments", {})
            _query_hint = ""
            _nav_hint = ""

            if tool_name in GRAPH_TOOL_NAMES:
                result = _execute_graph_tool(tool_name, tool_args, graph_engine, memory_manager, spec_text=search_text)
            elif tool_name in ("browser_navigate", "navigate"):
                new_site = tool_args.get("url", "")
                if new_site and "yandex.ru/search" in new_site.lower():
                    logger.info("🚫 Blocked yandex.ru/search navigation")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": ("error: Яндекс-поиск запрещён. Используй прямые URL магазинов. "
                                    "Если нужен магазин — ищи его название в списке sites.")
                    })
                    continue
                leaving_domain = bool(new_site and current_site
                                      and _extract_domain(new_site) != _extract_domain(current_site))
                if (leaving_domain and price_candidate_seen and not price_confirmed):
                    # Anti-deadlock: после 5+ раундов на сайте с несохранённым кандидатом
                    # НЕ блокируем навигацию — даём агенту уйти (кандидат-хинт остаётся
                    # в RowFacts, агент видит его в фактах следующего раунда).
                    if rounds_on_site > 5:
                        logger.info("Anti-deadlock: unblocking navigation after %d rounds on %s (candidate not confirmed)",
                                    rounds_on_site, _extract_domain(current_site))
                        _pc = facts.price_candidate_hint
                        _pc_part = f"\nЦена-кандидат: {_pc}." if _pc else ""
                        price_candidate_seen = False
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": (f"ℹ️ Ты {rounds_on_site} раундов на этом сайте, цена-кандидат не подтверждена{_pc_part} "
                                        "Навигация разблокирована — переходи к следующему сайту. "
                                        "Если найдена точная цена — сохрани через save_confirmed_price."),
                        })
                        continue
                    logger.warning("🚫 Navigate blocked: price candidate seen on %s, not confirmed",
                                   _extract_domain(current_site))
                    facts.record_navblock()
                    _pc = facts.price_candidate_hint
                    _pc_part = f"\nЦена-кандидат: {_pc}." if _pc else ""
                    if facts.navblocks >= 2:
                        _block_msg = (f"error: на текущем сайте УЖЕ найдена цена-кандидат{_pc_part} "
                                      f"Ты {facts.navblocks} раз пытался уйти без сохранения. НЕМЕДЛЕННО: "
                                      "если карточка открыта — извлеки h1 и цену и вызови save_confirmed_price "
                                      "(confirm=true при расхождении только в описательных словах). Только ПОСЛЕ "
                                      "сохранения переходи на другой сайт.")
                    else:
                        _block_msg = (f"error: на текущем сайте уже найдена цена-кандидат{_pc_part} "
                                      "НЕ уходи с этого сайта, пока цена не сохранена. Сейчас: либо открой "
                                      "карточку найденного товара и извлеки цену, либо сохрани её через "
                                      "save_confirmed_price с product_name (полное название с карточки). "
                                      "Только ПОСЛЕ сохранения цены можно переходить на другой сайт.")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": _block_msg,
                    })
                    continue
                # Soft Yandex reminder: warn but do NOT block navigation
                if "yandex" in current_site and not yandex_price_saved and new_site and "yandex" not in new_site.lower():
                    logger.info("ℹ️ Leaving Yandex for %s (yandex_price_saved=%s)", new_site, yandex_price_saved)
                # B6: повторная навигация на тот же URL в рамках строки — подсказка (не пропуск).
                _nav_hint = ""
                moved_to_new_domain = False
                if new_site and facts.seen_url(new_site):
                    _nav_hint = (f"ℹ️ Страница {new_site} уже открывалась в этой строке. "
                                 "Работай с текущей страницей или укажи новый URL.")
                if new_site and new_site != current_site:
                    if _extract_domain(new_site) != _extract_domain(current_site):
                        moved_to_new_domain = True
                        price_candidate_seen = False
                        recent_errors = []
                        empty_probe_streak.clear()
                        if site_visit_callback:
                            site_visit_callback(_extract_domain(new_site))
                    current_site = new_site
                    rounds_on_site = 0
                    if new_site:
                        site_timeout_counts[_extract_domain(new_site)] = 0
                if rate_limiter is not None:
                    await rate_limiter.wait_if_needed(current_site or "")
                result = await mcp_bridge.call_tool(tool_name, tool_args)
                facts.record_site_visit(_extract_domain(current_site))
                if new_site:
                    facts.record_url(new_site)
                if _is_product_card_url(current_site):
                    facts.record_card_open()
                # B7: уже посещено несколько сайтов без результата — guidance (не запрет).
                if (moved_to_new_domain and facts.distinct_sites() >= 3
                        and not price_candidate_seen and not price_confirmed):
                    messages.append({
                        "role": "user",
                        "content": (f"⚠️ Уже посещено {facts.distinct_sites()} сайтов без найденной цены. "
                                    "Если на текущем сайте нет подходящего товара — лучше сохранить лучший "
                                    "аналог (confidence снизится) или завершить строку, чем продолжать "
                                    "перебор сайтов."),
                    })
            else:
                # Rate limit EVERY browser action (not just navigate): клики, печать,
                # evaluate, снапшоты идут на тот же домен и тоже ловят бан при частых
                # запросах. Для per-site сайтов (vseinstrumenti) интервал больше.
                if rate_limiter is not None and current_site:
                    await rate_limiter.wait_if_needed(current_site)
                # Совет-предупреждения по вводу запроса (система-советник, не блокировка
                # решений): дубли запроса, «монстр»-вставка spec_text, деградация с потерей
                # модели/размера. LLM решает, но видит, что действие уже выполнялось/бесполезно.
                if tool_name in ("browser_type", "type_text"):
                    query_text = str(tool_args.get("text") or "").strip()
                    query_domain = _extract_domain(current_site or "")
                    if query_text and query_domain:
                        dup_count = facts.seen_query(query_domain, query_text)
                        unique_tried = facts.unique_queries_on_site(query_domain)
                        if dup_count >= QUERY_DUP_SKIP_AFTER:
                            if len(unique_tried) >= 2:
                                _dup_msg = (f"error: На этом сайте УЖЕ尝试ованы {len(unique_tried)} "
                                            f"варианта запроса: {', '.join(q[:40] for q in unique_tried[:3])}. "
                                            "Результатов НЕТ. ПЕРЕХОДИ к следующему сайту из списка.")
                            elif dup_count >= 2:
                                _dup_msg = (f"⚠️ Запрос «{query_text[:100]}» уже вводился на этом сайте "
                                            f"{dup_count + 1} раз подряд и результата не дал. НЕ повторяй его: "
                                            "измени текст запроса, переключись на другой сайт или сохрани "
                                            "уже найденную цену/аналог.")
                            else:
                                _dup_msg = (f"ℹ️ Запрос «{query_text[:100]}» уже вводился на этом сайте "
                                            "ранее и результата не дал. Повтор вряд ли поможет — измени "
                                            "текст запроса или переключись на другой сайт.")
                            logger.info("♻️ Запрос-дубль на %s: «%s» (unique=%d)", query_domain, query_text[:80], len(unique_tried))
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": _dup_msg,
                            })
                            continue
                        if MONSTER_QUERY_MARKER in query_text:
                            logger.info("♻️ Монстр-запрос отклонён на %s: «%s»", query_domain, query_text[:80])
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": (f"ℹ️ Запрос слишком длинный для поиска магазина "
                                            f"(вставлен весь текст позиции). Используй артикул/модель/размер: "
                                            "ключевые токены указаны в контексте. Пример: «065B8320R» "
                                            "или «BVR-DR DN40»."),
                            })
                            continue
                        if len(query_text) > MONSTER_QUERY_MIN_LEN:
                            _query_hint = ("ℹ️ Запрос длинный («{}…»). Для точного поиска используй "
                                           "артикул/модель/размер — см. ключевые токены в контексте.").format(query_text[:60])
                        prev_q = facts.last_query(query_domain)
                        if prev_q and prev_q != query_text:
                            lost = _query_lost_tokens(prev_q, query_text)
                            if lost:
                                _query_hint = (f"ℹ️ Новый запрос потерял: {', '.join(lost)}. "
                                               "Упрощай, сохраняя размер/тип/Ду, либо смени сайт — "
                                               "упрощение до неузнаваемости не найдёт товар.")
                # Автозачистка поля поиска перед вводом: SPA-сайты (digiSearch и пр.)
                # не очищают поле сами — старый текст склеивается с новым («МС-140
                # Мх500МС-140»). Очищаем нативно через JS, если передан селектор поля.
                if tool_name in ("browser_type", "type_text"):
                    clear_js = _clear_field_js(tool_args)
                    if clear_js:
                        try:
                            await mcp_bridge.call_tool("browser_evaluate", {"function": clear_js})
                            logger.info("🧹 Поле поиска очищено перед вводом (JS)")
                        except Exception:
                            logger.debug("Field clear failed (ignored)")
                # Кэш повторов browser_evaluate: если тот же JS на том же сайте уже
                # выполнялся — подставляем сохранённый результат и подсказываем, чтобы
                # агент не тратил раунды на повторное извлечение одной и той же выдачи.
                # НЕ делаем continue: кэш-значение проходит обычную обработку ниже
                # (StuckDetector/empty_probe/facts увидят повтор как no_change).
                eval_repeat = None
                _repeat_hint = ""
                if tool_name in ("browser_evaluate", "evaluate"):
                    js = str(tool_args.get("function") or "")
                    key = (_extract_domain(current_site),
                           hashlib.md5(_normalize_js(js).encode("utf-8", errors="replace")).hexdigest())
                    if key in eval_cache:
                        eval_repeat = eval_cache[key]
                        logger.info("🔁 Повтор browser_evaluate (тот же JS на %s) — кэш",
                                    _extract_domain(current_site))
                        tool_args = dict(tool_args)
                        tool_args["_repeat"] = True
                # Совет перед кликом по карточке: если в названии элемента явно указана
                # ДРУГАЯ модель, чем в спецификации — не открывай «для проверки».
                # (система-советник: LLM решает, но предупреждение жёсткое)
                if tool_name in ("browser_click", "click"):
                    click_hint = _model_mismatch_hint(tool_args, spec_text, spec_meta)
                    if click_hint:
                        logger.warning("⚠️ %s", click_hint)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": click_hint,
                        })
                        continue
                if eval_repeat is not None:
                    # Повторный evaluate: результат берём из кэша, bridge не вызываем.
                    # Результат помечаем флагом _repeat — обработка ниже (record_action/
                    # facts/empty_probe) увидит повтор как no_change, а подсказку добавим
                    # префиксом к tool-результату.
                    result = eval_repeat
                    _repeat_hint = (f"ℹ️ Повтор того же JS-извлечения на этой странице. "
                                    f"Результат не изменился: {eval_repeat[:200]}\n"
                                    "Не повторяй извлечение — используй уже полученные данные "
                                    "или примени ДРУГОЙ JS/действие.")
                else:
                    result = await mcp_bridge.call_tool(tool_name, tool_args)
                    if tool_name in ("browser_evaluate", "evaluate") and current_site:
                        js = str(tool_args.get("function") or "")
                        key = (_extract_domain(current_site),
                               hashlib.md5(_normalize_js(js).encode("utf-8", errors="replace")).hexdigest())
                        if key not in eval_cache:
                            eval_cache[key] = str(result)[:2000]

            if tool_name not in GRAPH_TOOL_NAMES:
                step = {"action": tool_name}
                if tool_name == "browser_navigate":
                    step["url"] = tool_args.get("url", "")
                elif tool_name in ("browser_type", "type_text"):
                    step["text"] = tool_args.get("text", "")
                    # Запрос считается «пробованным» ТОЛЬКО при успешном вводе.
                    # Провал (timeout/strict mode) не считается попыткой запроса —
                    # иначе повторный ввод с другим селектором ложно флагается как дубль.
                    if not str(result or "").startswith("error:"):
                        facts.record_query(_extract_domain(current_site), tool_args.get("text", ""))
                    # Hash-рефы (e82/f2e17) — внутренние id accessibility-дерева Playwright:
                    # в Camoufox они не существуют. При записи шага сохраняем портабельный
                    # локатор (element/роль) вместо ref, чтобы подход работал на любом бэкенде.
                    ref = str(tool_args.get("target") or tool_args.get("ref") or "")
                    if ref and not _is_hash_ref(ref):
                        step["target"] = ref
                    elem = tool_args.get("element")
                    if elem is not None and elem != "":
                        step["element"] = elem
                        if not step.get("target"):
                            step["target"] = elem
                    for k in ("submit", "slowly"):
                        v = tool_args.get(k)
                        if v is not None and v != "":
                            step[k] = v
                elif tool_name in ("browser_click", "click"):
                    ref = str(tool_args.get("target") or tool_args.get("ref") or "")
                    if ref and not _is_hash_ref(ref):
                        step["target"] = ref
                    elem = tool_args.get("element")
                    if elem is not None and elem != "":
                        step["element"] = elem
                        if not step.get("target"):
                            step["target"] = elem
                elif tool_name == "browser_press_key":
                    key = tool_args.get("key", "")
                    if key:
                        step["key"] = key
                elif tool_name == "browser_evaluate":
                    step["js_summary"] = str(tool_args.get("function", ""))[:80]
                # Only record successful steps — failed steps pollute approaches
                result_str = str(result)
                if not result_str.startswith("error:"):
                    steps.append(step)

            # StuckDetector: после каждого MCP-шага (не дублирует captcha-логику)
            stuck_detector.record_action(
                action_type=tool_name,
                target=_stuck_target(tool_name, tool_args),
                result="no_change" if tool_args.get("_repeat") else
                       ("success" if not str(result).startswith("error:") else "no_change"),
            )

            tool_content = str(result)
            # Фиксируем причины сбоев для диагностики восстановления
            if tool_content.startswith("error:"):
                recent_errors.append(tool_content[:120])
                if len(recent_errors) > 4:
                    recent_errors.pop(0)
                facts.record_error(tool_content)
                domain = _extract_domain(current_site)
                if domain and "timed out" in tool_content:
                    site_timeout_counts[domain] = site_timeout_counts.get(domain, 0) + 1
                    if site_timeout_counts[domain] >= SITE_TIMEOUT_FAIL_FAST:
                        logger.warning("Fail-fast: %d timeouts on %s — auto JS fallback", site_timeout_counts[domain], domain)
                        try:
                            fallback_js = ("() => { const inp = document.querySelector("
                                           "'input[type=\"search\"], input[placeholder*=\"Поиск\"], "
                                           "input[name*=\"search\"]'); "
                                           "if (inp) { inp.value = ''; inp.dispatchEvent(new Event('input')); "
                                           "return 'cleared'; } return 'no_input_found'; }")
                            fb_result = await mcp_bridge.call_tool("browser_evaluate", {"function": fallback_js})
                            if fb_result and not str(fb_result).startswith("error:"):
                                tool_content = (f"⚠️ Таймаут поиска на {domain} ({site_timeout_counts[domain]} раз). "
                                                f"Поле поиска очищено автоматически. Введи запрос заново или переключись на другой сайт.")
                                messages.append({"role": "assistant", "content": tool_content})
                            else:
                                tool_content = (f"error: Таймаут на {domain} ({site_timeout_counts[domain]} раз). "
                                                f"Переключись на другой сайт из списка.")
                                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": tool_content})
                        except Exception as e:
                            logger.warning("Fail-fast JS fallback failed: %s", e)
                            tool_content = (f"error: Таймаут на {domain} ({site_timeout_counts[domain]} раз). "
                                            f"Переключись на другой сайт из списка.")
                            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": tool_content})
                        continue
            if tool_name == "browser_evaluate":
                js_key = str(tool_args.get("function", ""))[:80]
                result_hash = hashlib.md5(tool_content.encode("utf-8", "ignore")).hexdigest()[:8]
                facts.record_browser_call(_extract_domain(current_site), "evaluate:" + js_key, result_hash)
            if tool_name == "browser_evaluate" and "SyntaxError" in tool_content:
                js_syntax_errors = facts._sites.get(_extract_domain(current_site), {}).get("js_syntax_errors", 0) if hasattr(facts, '_sites') else 0
                js_syntax_errors += 1
                site_domain = _extract_domain(current_site)
                if site_domain not in facts._sites:
                    facts._sites[site_domain] = {"queries": [], "extractions": 0, "evals_since_type": 0, "js_syntax_errors": 0}
                facts._sites[site_domain]["js_syntax_errors"] = js_syntax_errors
                if js_syntax_errors >= 2:
                    fallback_js = """() => {
                        const inp = document.querySelector('input[type="search"], input[placeholder*="Поиск"], input[name*="search"]');
                        if (inp) { inp.value = ''; inp.dispatchEvent(new Event('input')); return 'cleared'; }
                        const price = document.querySelector('[class*="price"], [data-price], .product-price');
                        if (price) return price.textContent.trim();
                        return 'no_data_found';
                    }"""
                    try:
                        result = await mcp_bridge.call_tool("browser_evaluate", {"function": fallback_js})
                        tool_content = (f"✅ JS-фолбэк выполнился: {str(result)[:200]}. "
                                       "Если цена найдена — извлеки её. Если нет — переключись на другой сайт.")
                        facts._sites[site_domain]["js_syntax_errors"] = 0
                    except Exception as e:
                        tool_content = (f"error: {js_syntax_errors} ошибки синтаксиса JS подряд. "
                                       "Переключись на другой сайт — этот сайт нестабилен.")
                else:
                    tool_content += ("\n💡 JS-ошибка синтаксиса (SyntaxError). Перепиши код ПРОСТЫМ "
                                     "однострочным выражением: без // комментариев в конце строки, "
                                     "закрой все фигурные скобки, строки в кавычках.")
            if tool_name in ("browser_snapshot", "snapshot"):
                tool_content = _clean_snapshot(tool_content)
                # Sync current_site from snapshot URL (handles new tabs from browser_click)
                for line in tool_content.split("\n"):
                    if "Page URL:" in line:
                        url = line.split("Page URL:")[-1].strip().split()[0] if line.split("Page URL:")[-1].strip() else ""
                        if url and url != current_site:
                            if _extract_domain(url) != _extract_domain(current_site):
                                price_candidate_seen = False
                                recent_errors = []
                                empty_probe_streak.clear()
                            current_site = url
                            rounds_on_site = 0
                            facts.record_site_visit(_extract_domain(current_site))
                            if _is_product_card_url(current_site):
                                facts.record_card_open()
                        break

            # Captcha/block detection — skip site immediately
            if (any(kw in tool_content.lower() for kw in CAPTCHA_KEYWORDS)
                    or CaptchaDetector.detect(tool_content) != CaptchaType.NONE) and current_site:
                captcha_type = CaptchaDetector.detect(tool_content)
                recommendation = CaptchaDetector.get_recommendation(captcha_type)
                logger.warning("🚫 Captcha (%s, %s) detected on %s — skip site",
                               captcha_type.value, recommendation, current_site)
                if monitor_callback:
                    monitor_callback("block", captcha_type.value)
                try:
                    failed_domain = _extract_domain(current_site)
                    if rate_limiter is not None:
                        rate_limiter.record_block(failed_domain)
                        logger.warning("🚫 Cooldown set for %s (%.0fs) after block",
                                       failed_domain, rate_limiter.cooldown_seconds)
                    _deprecate_site_approaches(memory_manager, product_type, failed_domain, "🚫 Captcha:")
                except Exception as e:
                    logger.warning("Captcha deprecation failed: %s", e)
                tool_content = f"Сайт заблокирован captcha/проверкой бота ({captcha_type.value}). Рекомендация: {recommendation}. Домену установлен cooldown — вернёшься к нему позже в этой сессии через паузу (rate limiter сам выдержит ожидание). НЕ пытайся обойти captcha сейчас, переключись на другой сайт."
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": tool_content,
                })
                current_site = ""
                rounds_on_site = 0
                break

            # Summarize large results to save context
            if tool_name in ("browser_snapshot", "snapshot", "browser_evaluate", "extract_text"):
                lines = tool_content.split("\n")
                short = [l for l in lines if len(l.strip()) > 0 and not l.strip().startswith("<!")]
                before = len(short)
                tool_content = "\n".join(short[:SUMMARIZE_MAX_LINES])
                after = min(len(short), SUMMARIZE_MAX_LINES)
                logger.info("📐 %s: %d→%d lines, %d chars", tool_name, before, after, len(tool_content))
            elif len(tool_content) > SUMMARIZE_MAX_CHARS:
                tool_content = tool_content[:SUMMARIZE_MAX_CHARS]

            summary = _summarize_tool(tool_name, tool_args)
            result_text = _summarize_result(tool_name, tool_content)
            if tool_name in GRAPH_TOOL_NAMES:
                logger.info("📊 %s: %s", tool_name, tool_content.replace("\n", " ")[:200])
            else:
                logger.info("%s → %s", summary, result_text)
            if status_callback:
                status_callback(f"[{rounds}/{MAX_ROUNDS}] {summary} — {result_text}")
            # Подсветка найденной цены — агент не должен её потерять в большом ответе
            price_hint = None
            if tool_name not in GRAPH_TOOL_NAMES:
                price_hint = _extract_price_candidate(tool_content)
                if price_hint and _price_is_relevant(spec_text, spec_meta, tool_content):
                    price_candidate_seen = True
                    empty_probe_streak.clear()
                    facts.record_price_candidate(str(price_hint))
            content_to_send = tool_content[:10000]
            if tool_name == "browser_evaluate" and len(tool_content) > EVALUATE_MESSAGE_CAP:
                content_to_send = tool_content[:EVALUATE_MESSAGE_CAP] + "\n…(результат усечён; цена-кандидат выше, если была)"
            if _query_hint:
                content_to_send = _query_hint + "\n" + content_to_send
            if _nav_hint:
                content_to_send = _nav_hint + "\n" + content_to_send
            if price_hint:
                content_to_send = f"💰 price_candidate: {price_hint}\n" + content_to_send
            if tool_args.get("_repeat"):
                content_to_send = _repeat_hint + "\n" + content_to_send
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": content_to_send,
            })

            # Ранний выход при серии пустых поисковых зондов на одном сайте:
            # если товара нет, дальше искать на нём бесполезно
            if tool_name in ("browser_evaluate", "evaluate", "browser_find", "find") and current_site:
                probe_domain = _extract_domain(current_site)
                if _is_empty_search_result(tool_name, tool_content):
                    facts.record_empty_result(probe_domain)
                    # 2.1: первый «пусто» на странице РЕЗУЛЬТАТОВ — выдача могла не догрузиться (SPA).
                    if _is_search_results_url(current_site) and probe_domain not in search_page_retry_guided:
                        search_page_retry_guided.add(probe_domain)
                        empty_probe_streak[probe_domain] = 0
                        logger.warning("⚠️ Первый пустой зонд на выдаче %s — guidance «дождись и повтори»",
                                       probe_domain)
                        messages.append({
                            "role": "user",
                            "content": ("⚠️ Извлечение на странице результатов поиска вернуло пусто — выдача "
                                        "могла не догрузиться (SPA). Сделай browser_wait_for 2с и ПОВТОРИ "
                                        "извлечение (тот же или уточнённый JS); если снова пусто — тогда меняй "
                                        "запрос, сохраняя размер/тип/Ду."),
                        })
                    else:
                        _session_no_product(probe_domain)
                        empty_probe_streak[probe_domain] = empty_probe_streak.get(probe_domain, 0) + 1
                        if empty_probe_streak[probe_domain] >= EMPTY_PROBE_LIMIT and probe_domain not in empty_probe_guidance_sent:
                            empty_probe_guidance_sent.add(probe_domain)
                            empty_probe_streak[probe_domain] = 0
                            logger.warning("⚠️ %d empty search probes on %s — early exit guidance",
                                           EMPTY_PROBE_LIMIT, probe_domain)
                            messages.append({
                                "role": "user",
                                "content": (f"⚠️ Поиск на {probe_domain} уже {EMPTY_PROBE_LIMIT} раз подряд вернул пустой "
                                            "результат — товар на этом сайте отсутствует. НЕ продолжай искать на нём: "
                                            "переключись на ДРУГОЙ сайт из списка (или сохрани уже найденную цену, "
                                            "если она была в результатах)."),
                            })

            if tool_name == "save_confirmed_price":
                # Программная проверка соответствия товара спецификации.
                # product_name обязателен — без названия с карточки цена не сохраняется.
                found_name = (tool_args.get("product_name") or "").strip()
                if not found_name:
                    logger.warning("⚠️ Product name missing: spec=%s", spec_text[:50])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": ("error: передай product_name — точное наименование товара "
                                    "с карточки (заголовок). НЕ сохраняю цену без него."),
                    })
                    continue
                save_url = tool_args.get("url") or current_site or ""
                if _is_family_page(save_url) or _is_family_page(current_site):
                    logger.warning("⚠️ Family page save rejected: spec=%s url=%s", spec_text[:50], save_url)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": ("error: URL — семейная страница каталога без конкретного варианта "
                                    "(.../i<id>/ без /vN/), на ней несколько модификаций с разными ценами. "
                                    "НЕ сохраняй цену с неё. Перейди на карточку конкретного варианта "
                                    "(URL вида .../i<id>/v<N>/) и сохрани цену оттуда."),
                    })
                    continue
                brand_mismatch = bool(tool_args.get("brand_mismatch"))
                confirm = bool(tool_args.get("confirm"))
                match_ok = (product_name_matches_ignore_brand(spec_text, found_name)
                            if brand_mismatch else product_name_matches(spec_text, found_name))
                if not match_ok:
                    previously_confirmed = memory_manager.has_matching_equivalence(spec_text, found_name)
                    if previously_confirmed:
                        logger.info("Row: known equivalent pair, accept: spec=%s found=%s",
                                    spec_text[:50], str(found_name)[:50])
                        match_ok = True
                    elif confirm:
                        llm_conf = float(tool_args.get("confidence", 0) or 0)
                        if llm_conf < CONF_MIN:
                            logger.warning("LLM confirm override REJECTED for mismatched product "
                                           "(conf %.2f < %.2f): spec=%s found=%s",
                                           llm_conf, CONF_MIN, spec_text[:50], str(found_name)[:50])
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.get("id", ""),
                                "content": ("error: товар НЕ соответствует спецификации по наименованию, а "
                                            "уверенность слишком низкая (conf < %.2f). НЕ сохраняю цену. "
                                            "Продолжай поиск точного товара или найди карточку с подтверждённым "
                                            "JSON-LD/ценой и передай confidence >= %.2f." % (CONF_MIN, CONF_MIN)),
                            })
                            continue
                        logger.warning("LLM confirm override for mismatched product: spec=%s found=%s",
                                       spec_text[:50], str(found_name)[:50])
                        if not brand_mismatch:
                            # Запоминаем соответствие, чтобы впредь не предупреждать
                            # (только для совпадений по типу/размеру, не по бренду).
                            try:
                                memory_manager.record_matching_equivalence(spec_text, found_name)
                            except Exception as e:
                                logger.warning("Failed to record matching equivalence: %s", e)
                    else:
                        logger.warning("⚠️ Product mismatch — advisory: spec=%s vs found=%s",
                                       spec_text[:50], str(found_name)[:50])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": _mismatch_warning_content(spec_text, found_name),
                        })
                        continue
                validated = validate_result({
                    "price": tool_args.get("price"),
                    "confidence": tool_args.get("confidence", 0.5),
                    "url": tool_args.get("url", ""),
                    "site": tool_args.get("site", ""),
                    "reason": tool_args.get("reason", ""),
                    "requires_review": True,
                }, spec_text)
                if validated.get("price") is not None:
                    if not match_ok:
                        # LLM подтвердил несоответствие наименования. Если расхождение
                        # ТОЛЬКО описательное/материал (модель, размер, бренд совпали) —
                        # товар верен, допускаем до rule-8 (confidence не режем до 0.5).
                        # Иначе (модель C20≠C10, размер, структурные ключевые слова) — 0.5.
                        validated["requires_review"] = True
                        kind = mismatch_kind(spec_text, found_name, spec_meta)
                        if kind in ("descriptive_only", "none"):
                            validated["confidence"] = round(max(validated.get("confidence", 0), 0.8), 2)
                        else:
                            validated["confidence"] = round(min(validated.get("confidence", 0), 0.5), 2)
                        validated["reason"] = (validated.get("reason", "")
                                               + " (подтверждено LLM при несоответствии наименования)").strip()
                    if brand_mismatch:
                        fallback_candidates.append({
                            "price": validated.get("price"),
                            "confidence": validated.get("confidence", 0.0),
                            "url": validated.get("url") or save_url,
                            "site": validated.get("site") or _extract_domain(save_url),
                            "product_name": found_name,
                        })
                        logger.info("Row: brand-mismatch fallback candidate: price=%s (%s)",
                                    validated["price"], str(found_name)[:60])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": ("Кандидат-фолбэк сохранён (не совпадает бренд). Продолжай искать "
                                        "товар с нужным брендом на других сайтах. Если точный товар не "
                                        "найдётся — строка будет заполнена этим кандидатом и помечена "
                                        "«не совпадает бренд»."),
                        })
                        continue
                    yandex_price_saved = True
                    price_confirmed = True
                    empty_probe_streak.clear()
                    elapsed = (datetime.now() - start_time).total_seconds()
                    logger.info("Row: price=%s validated=%.2f in %.1fs", validated['price'], validated['confidence'], elapsed)
                    if _price_candidate_holder is not None:
                        _price_candidate_holder.update({
                            "price": validated.get("price"),
                            "confidence": validated.get("confidence", 0.5),
                            "url": validated.get("url", ""),
                            "site": validated.get("site", ""),
                            "product_name": found_name,
                            "spec_text": spec_text,
                            "product_type": product_type,
                        })
                    try:
                        _save_price_and_approach(memory_manager, spec_text, product_type, validated, steps, record_soldat=False, search_query=search_text)
                    except Exception as e:
                        logger.warning("Failed to save price/approach in save_confirmed_price: %s", e)
                    try:
                        memory_manager.record_soldat(
                            product_type, validated.get("site") or _extract_domain(save_url) or ""
                        )
                    except Exception as e:
                        logger.warning("Failed to record soldat: %s", e)
                    # LLM-подтверждение несоответствия — решение LLM, возвращаем сразу (requires_review).
                    # Иначе: низкий confidence → сохраняем и продолжаем поиск (rule 5).
                    if not match_ok or validated.get("confidence", 0) >= CONF_MIN:
                        final = {
                            "spec_text": spec_text, "product_type": product_type,
                            **validated, "elapsed": elapsed,
                        }
                        _store_semantic_cache(semantic_cache, spec_text, final)
                        _session_success(validated.get("site") or _extract_domain(save_url),
                                         url=save_url, query=search_text)
                        return _result_to_schema(final)
                    logger.info("Low confidence (%.2f) — saved, continuing search", validated['confidence'])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": (f"Цена {validated['price']} ₽ сохранена (confidence "
                                    f"{validated['confidence']:.2f}, требует ревью). Продолжай поиск лучшей "
                                    f"цены или точного товара."),
                    })

        for tc_last in tool_calls:
            tn = tc_last.get("name", "")
            if tn not in ("get_approaches", "search_sites", "get_confirmed_prices",
                          "get_hints", "save_discovered_site", "save_approach"):
                rounds_on_site += 1
                break

        # StuckDetector: при зацикливании — сначала диагностика, уход только после капа
        stuck_level = stuck_detector.detect()
        if stuck_level == StuckLevel.CRITICAL and rounds_on_site > 5:
            if monitor_callback:
                monitor_callback("stuck", None)
            if diagnostic_prompts < DIAGNOSTIC_PROMPT_CAP:
                diagnostic_prompts += 1
                logger.warning("StuckDetector CRITICAL — diagnostic recovery (%d/%d)", diagnostic_prompts, DIAGNOSTIC_PROMPT_CAP)
                stuck_detector.reset()
                messages.append({
                    "role": "user",
                    "content": _build_diagnostic_message(
                        spec_text, current_site,
                        card_open=_is_product_card_url(current_site),
                        price_candidate_seen=price_candidate_seen,
                        recent_errors=recent_errors,
                    ),
                })
                _stop_check()
                response = await _llm_call(messages, TEMP_RECOVERY)
                if "error" in response:
                    return _error_result(spec_text, f"LLM: {response['error']}")
                continue
            logger.warning("StuckDetector CRITICAL — diagnostic cap reached, forcing site switch")
            if site_blacklist is not None and current_site and not price_candidate_seen:
                site_blacklist.strike(_extract_domain(current_site), reason="stuck")
            current_site = ""
            rounds_on_site = site_round_limits.get(_extract_domain(current_site), MAX_ROUNDS_PER_SITE) + 1
            stuck_detector.reset()

        current_domain = _extract_domain(current_site)
        if rounds_on_site > site_round_limits.get(current_domain, MAX_ROUNDS_PER_SITE):
            logger.info("⚠️ Forcing site switch after %d rounds on %s", rounds_on_site, current_site or "?")
            # Track negative feedback — always, even for unknown product types
            if current_site:
                if site_blacklist is not None and not price_candidate_seen:
                    site_blacklist.strike(current_domain or current_site, reason="force_switch")
                try:
                    failed_site = current_domain
                    if product_type != UNKNOWN_PT and not price_candidate_seen:
                        memory_manager.increment_consecutive_failures(product_type, failed_site)
                    if price_candidate_seen:
                        # Товар на сайте есть (видели цену) — строка не успела. Подходы НЕ штрафуем.
                        logger.info("Force switch: price candidate seen on %s — approaches preserved", failed_site)
                    else:
                        _penalize_approaches(memory_manager, _shown_approach_ids(failed_site), "📉 Force switch:")
                        _session_no_product(failed_site)
                except Exception as e:
                    logger.warning("Force switch deprecation failed: %s", e)
            force_msg = f"Ты сделал {rounds_on_site} шагов на текущем сайте без результата — лимит исчерпан."
            if price_candidate_seen and not price_confirmed:
                force_msg += (" Но в результатах УЖЕ была цена (price_candidate). Немедленно сохрани её "
                              "через save_confirmed_price с product_name, затем переключись на другой сайт.")
            else:
                force_msg += " Принудительно переключись на ДРУГОЙ сайт из списка."
            messages.append({
                "role": "user",
                "content": force_msg,
            })
            rounds_on_site = 0
            current_site = ""
            _stop_check()
            response = await _llm_call(messages, TEMP_RECOVERY)
            if "error" in response:
                return _error_result(spec_text, f"LLM: {response['error']}")
            continue

        # Yandex Rule 12 reminder: fire once per Yandex visit
        if "yandex" in current_site and not yandex_reminded and not yandex_price_saved:
            yandex_reminded = True
            messages.append({
                "role": "user",
                "content": "⚠️ НАПОМИНАНИЕ (Rule 12): Яндекс — только поисковик. Кликни на ссылку магазина из результатов поиска Яндекса и извлеки цену ИЗ КАРТОЧКИ ТОВАРА НА САЙТЕ. Не бери цену из сниппета Яндекса."
            })

        _stop_check()
        response = await _llm_call(messages, TEMP_NAVIGATION)
        if "error" in response:
            return _error_result(spec_text, f"LLM: {response['error']}")

    elapsed = (datetime.now() - start_time).total_seconds()
    if current_site:
        if site_blacklist is not None and not price_candidate_seen:
            site_blacklist.strike(_extract_domain(current_site), reason="max_rounds")
        try:
            failed_domain = _extract_domain(current_site)
            if price_candidate_seen:
                # Товар на сайте есть (видели цену) — строка не успела. Подходы НЕ штрафуем.
                logger.info("Max rounds: price candidate seen on %s — approaches preserved", failed_domain)
            else:
                _penalize_approaches(memory_manager, _shown_approach_ids(failed_domain), "📉 Max rounds:")
        except Exception as e:
            logger.warning("Max rounds deprecation failed: %s", e)
    if fallback_candidates:
        result = _fallback_result(spec_text, product_type, fallback_candidates, elapsed)
        if result:
            _store_semantic_cache(semantic_cache, spec_text, result)
            logger.info("Row: brand-mismatch fallback price=%s (conf=%.2f) in %.1fs",
                        result.get("price"), result.get("confidence", 0), elapsed)
            return result

    # Радиатор: если точное количество секций не найдено ни на одном сайте —
    # рассчитываем цену из известного варианта той же модели (цена за секцию × N).
    # ТОЛЬКО для радиаторов с суффиксом -0,9-N, ТОЛЬКО после полного перебора сайтов.
    from src.radiator_section_pricer import calculate_radiator_price, extract_sections
    if extract_sections(spec_text) is not None and product_type in _RADIATOR_PRODUCT_TYPES:
        try:
            calculated = calculate_radiator_price(memory_manager, spec_text, product_type)
            if calculated:
                logger.info("Row: radiator section-calculated price=%.2f in %.1fs",
                            calculated["price"], elapsed)
                memory_manager.save_price(
                    spec_text=spec_text, product_type=product_type,
                    site=calculated.get("site", ""), price=calculated["price"],
                    url=calculated.get("url", ""), confidence=calculated["confidence"],
                    reason=calculated.get("reason", ""),
                )
                try:
                    memory_manager.record_soldat(
                        product_type, calculated.get("site", "") or ""
                    )
                except Exception:
                    pass
                _session_success(calculated.get("site", ""), url="", query=search_text)
                final = {"spec_text": spec_text, "product_type": product_type,
                         **calculated, "elapsed": elapsed}
                return _result_to_schema(final)
        except Exception as e:
            logger.warning("Radiator section calculation failed: %s", e)

    logger.info("Row: max rounds reached in %.1fs", elapsed)
    return _error_result(spec_text, f"Max rounds ({MAX_ROUNDS}) reached", elapsed=elapsed)


def _is_standard_reference(spec: str) -> bool:
    """True, если «Тип/обозначение» — это ссылка на стандарт (ГОСТ/ТУ/СНиП/ISO...),
    а не модель товара. Такие значения не полезны для поиска."""
    return is_standard_reference(spec)


def _build_context(spec_text, product_type, approaches, confirmed_prices, sites, hints, product_data=None, site_guides=None, concepts=None, spec_meta=None,
                   use_site_ranking: bool = True, site_ranking: dict | None = None,
                   use_approaches: bool = True, session_facts: SessionFacts | None = None):
    # фильтр релевантности: подходы, обученные на ДРУГИХ товарах того же типа
    # (например регуляторы скорости для воздуховодов), не показываются
    extra = (spec_meta or {}).get("article", "")
    approaches = [a for a in (approaches or []) if approach_relevant(a, spec_text, extra)]
    parts = [f"ТОВАР ДЛЯ ПОИСКА: {spec_text}"]
    # Ключевые токены (ОТОБРАЖЕНИЕ, не скриптовый запрос): бренд/тип/размер/Ду —
    # дифференциаторы, которые LLM не должен терять при составлении запроса.
    key_tokens = search_key_tokens(spec_text, spec_meta)
    if key_tokens:
        parts.append("")
        parts.append("КЛЮЧЕВЫЕ ТОКЕНЫ ДЛЯ ПОИСКА (сохраняй их в запросе):")
        labels = {"brand": "Бренд", "type": "Тип/обозначение", "article": "Артикул/код",
                  "size": "Размер/Ду", "keywords": "Ключевые слова"}
        for k, v in key_tokens.items():
            parts.append(f"  {labels.get(k, k)}: {v}")
    if session_facts is not None:
        pos, neg = session_facts.to_context_blocks(product_type, (spec_meta or {}).get("brand", "") or "")
        if use_approaches and pos:
            parts.append("\nСессионные факты прогона (положительные):")
            parts.append(pos)
        if use_site_ranking and neg:
            parts.append("\nСессионные факты прогона (отрицательные):")
            parts.append(neg)
    if spec_meta:
        parts.append("")
        parts.append("СТРУКТУРА ФАЙЛА:")
        parts.append(f"  Колонки: {', '.join(spec_meta.get('headers', []))}")
        if spec_meta.get("article"):
            parts.append(f"  Артикул/код: {spec_meta['article']}")
        if spec_meta.get("brand"):
            parts.append(f"  Завод-изготовитель: {spec_meta['brand']}")
        if spec_meta.get("spec") and not _is_standard_reference(spec_meta["spec"]):
            parts.append(f"  Тип/обозначение: {spec_meta['spec']}")
        if spec_meta.get("name_raw"):
            parts.append(f"  Наименование: {spec_meta['name_raw']}")
    if product_type != UNKNOWN_PT and product_data:
        cat = product_data.get("category", "")
        name = product_data.get("name", "")
        kws = product_data.get("keywords", "")
        parts.append(f"\nТип товара: {product_type}")
        if cat:
            parts.append(f"Категория: {cat}")
        if name:
            parts.append(f"Название: {name}")
        if kws:
            parts.append(f"Keywords: {kws[:200]}")
    if sites:
        site_ids = {s['id'] for s in sites}
        approach_sites = {a.get("site_id", "") for a in approaches if a.get("site_id", "") in site_ids}
        # Суммарная успешность подходов по сайту, ОТФИЛЬТРОВАННАЯ по МОДЕЛИ текущего
        # товара. Модель — дифференциатор: успехи LEMAX-радиаторов не должны толкать
        # satro-paladin выше santech для МС-140 (регрессия: агент шёл не туда).
        # Если модель текущего товара не определена — учитываем все подходы типа.
        spec_models = model_designators(spec_text)
        success_scores: dict[str, int] = {}
        for a in approaches:
            sid = a.get("site_id", "")
            if sid not in site_ids or a.get("success_count", 0) <= 0:
                continue
            if spec_models:
                a_models = model_designators(a.get("search_query") or "")
                if not (a_models & spec_models):
                    continue  # подход другого товара того же типа — не влияет на рейтинг
            success_scores[sid] = success_scores.get(sid, 0) + int(a.get("success_count", 0) or 0)
        success_sites = set(success_scores)
        price_sites = {p.get("site_id", "") for p in confirmed_prices if p.get("site_id", "") in site_ids}
        failed_sites = {a.get("site_id", "") for a in approaches if a.get("site_id", "") in site_ids and a.get("consecutive_failures", 0) >= 3}
        for s in sites:
            if s.get("consecutive_failures", 0) >= 3:
                failed_sites.add(s['id'])

        def _sort_key(s):
            sid = s['id']
            priority = s.get("priority", 2)
            # Сайты, где УЖЕ есть цены этого товара/семьи (даже другого типоразмера) —
            # самый сильный сигнал «сюда идти» (обучение на соседних позициях).
            if sid in price_sites:
                return (0, -10_000_000)
            if not use_site_ranking:
                # Чистый поиск без памяти: порядок по белому списку (priority),
                # успешность подходов НЕ влияет.
                cat = 3 if priority == 0 else (4 if priority == 1 else (6 if sid in failed_sites else 5))
                return (cat, 0)
            # Рейтинг по профилю (тип, бренд) → сайт: выше подходов, но ниже цен.
            if site_ranking and sid in site_ranking:
                return (0.5, -success_scores.get(sid, 0))
            if sid in success_sites:
                return (1, -success_scores.get(sid, 0))
            if sid in approach_sites:
                return (2, 0)
            cat = 3 if priority == 0 else (4 if priority == 1 else (6 if sid in failed_sites else 5))
            return (cat, 0)

        ordered = sorted(sites, key=_sort_key)
        first_site = ordered[0]['id']
        parts.append(f"\nПервый сайт для поиска: {first_site}")
        if len(ordered) > 1:
            rest = ", ".join(s['id'] for s in ordered[1:5])
            parts.append(f"Остальные сайты (если на первом не нашлось): {rest}")
    else:
        parts.append(f"\n(известных сайтов нет — начни поиск через {SEARCH_ENGINE})")
    if approaches:
        parts.append("\nУспешные подходы из графа:")
        for a in approaches[:3]:
            adapted = _apply_approach(a, spec_text)
            s = adapted.get("site_id", "")
            c = adapted.get("success_count", 0)
            ls = (a.get("last_success_date") or "")[:10]
            parts.append(f"  {s}: успехов={c}, последний={ls}")
            if a.get("method"):
                parts.append(f"    метод: {a['method']}")
            sq = adapted.get("search_query", "")[:100]
            if sq:
                parts.append(f"    запрос: {sq}")
            concrete = adapted.get("concrete", [])
            if concrete:
                parts.append(f"    шаги: {format_steps(concrete)}")
                if a in approaches[:2]:
                    parts.append(format_steps_detailed(concrete))
    if site_guides:
        parts.append("\nКак работать на сайтах (подходы для других товаров):")
        for sid, s_approaches in site_guides.items():
            method = s_approaches[0].get("method", "browser_search")
            concrete = s_approaches[0].get("concrete", [])
            success_total = sum(a.get("success_count", 0) for a in s_approaches)
            parts.append(f"  {sid} (успехов: {success_total}, метод: {method})")
            if concrete:
                parts.append(f"    {format_steps(concrete)}")
    if confirmed_prices:
        parts.append("\nПохожие цены:")
        for p in confirmed_prices[:3]:
            pt = (p.get("spec_text") or "")[:60]
            conf = p.get("confidence", 0)
            stale = " ⚠️" if p.get("is_stale") else ""
            parts.append(f"  {pt} -> {p.get('price', '?')} rub на {p.get('site_id', '?')} (conf: {conf:.0%}){stale}")
    if concepts:
        parts.append("\nСвязи (SOLD_AT):")
        for c in concepts[:3]:
            parts.append(f"  {c.get('child_name', '')} → {c.get('parent_name', '')} ({c.get('relation', '')})")
    if sites or site_guides:
        parts.append("\n💡 После browser_navigate на новый сайт: если browser_snapshot показывает СТАРУЮ страницу — открой browser_tabs, переключись на последнюю вкладку.")
    return "\n".join(parts)


def _execute_graph_tool(name: str, args: dict, engine, mm, spec_text: str = "") -> str:
    try:
        if name == "get_approaches":
            pt = args.get("product_type", "")
            site = args.get("site")
            if not pt and site:
                approaches = mm.get_approaches_by_site(site)
            elif pt and site:
                approaches = mm.get_site_approaches(pt, site)
            else:
                approaches = mm.get_all_approaches(pt) if pt else mm.get_all_approaches_flat()
            if not approaches:
                return "Нет сохранённых подходов"
            # релевантность текущему товару: чужие подходы того же типа не показываем
            approaches = [a for a in approaches if approach_relevant(a, spec_text)]
            if not approaches:
                return "Нет подходов, релевантных текущему товару"
            level_names = {1: "точный тип+сайт", 2: "категория+сайт", 3: "только сайт", 4: "категория", 5: "тип товара"}
            sl = approaches[0].get("source_level")
            level_hint = f" (уровень: {level_names.get(sl, '?')})" if sl else ""
            lines = [f"Подходов: {len(approaches)}{level_hint}"]
            site_ids = {a.get("site_id") for a in approaches}
            if len(site_ids) > 1:
                lines.append("⚠️ Подходы с РАЗНЫХ сайтов. Используй подход ТОЛЬКО на сайте, указанном в начале строки (site.ru: ...)."
                             " Если ты на другом сайте — найди поле поиска на ТЕКУЩЕЙ странице через browser_find/browser_snapshot,"
                             " НЕ переноси локатор с чужого сайта.")
            for a in approaches[:5]:
                # адаптируем подход к текущему товару: устаревший хардкод-текст
                # в шагах ввода заменяется на актуальный spec_text
                a = _apply_approach(a, spec_text)
                pat = " -> ".join(s.get("action", "?") for s in a.get("pattern", []))
                concrete = a.get("concrete", [])
                detail_parts = []
                for s in concrete[:4]:
                    d = s.get("action", "?")
                    loc = _portable_step_target(s)
                    if loc:
                        d += "[" + loc + "]"
                    txt = s.get("text", "")
                    if txt and len(txt) < 50:
                        d += "='" + txt + "'"
                    key = s.get("key", "")
                    if key:
                        d += "(" + key + ")"
                    detail_parts.append(d)
                detail = " -> ".join(detail_parts) if detail_parts else pat
                line = f"  {a.get('site_id', '?')}: {detail} (успехов: {a.get('success_count', 0)})"
                sq = a.get("search_query", "")[:80]
                if sq:
                    line += f" запрос={sq}"
                lines.append(line)
            return "\n".join(lines)

        elif name == "save_approach":
            aid = mm.save_approach(
                product_type=args.get("product_type", ""),
                site=args.get("site", ""),
                concrete_steps=MemoryManager.clean_steps(args.get("steps", [])),
                selectors_cache=json.loads(args.get("selectors", "{}")) if args.get("selectors") else None,
                param_slots=json.loads(args.get("param_slots", "{}")) if args.get("param_slots") else None,
                method=args.get("method", ""),
                search_query=args.get("search_query", ""),
                notes=args.get("notes", ""),
            )
            return f"Подход сохранён (ID: {aid})"

        elif name == "get_confirmed_prices":
            prices = mm.get_relevant_prices(args.get("spec_text", ""))
            if not prices:
                return "Нет похожих цен"
            lines = [f"Похожих цен: {len(prices)}"]
            for p in prices[:5]:
                pt = (p.get("spec_text") or "")[:60]
                lines.append(f"  {pt} -> {p.get('price', '?')} rub (conf: {p.get('confidence', 0):.0%})")
            return "\n".join(lines)

        elif name == "save_confirmed_price":
            found_name = (args.get("product_name") or "").strip()
            if not found_name:
                return ("error: передай product_name — точное наименование товара с карточки "
                        "(заголовок). НЕ сохраняю цену без него.")
            if _is_family_page(args.get("url", "")):
                logger.warning("⚠️ Family page save rejected: url=%s", args.get("url", ""))
                return ("error: URL — семейная страница каталога без конкретного варианта "
                        "(.../i<id>/ без /vN/), на ней несколько модификаций с разными ценами. "
                        "НЕ сохраняй цену с неё. Перейди на карточку конкретного варианта "
                        "(URL вида .../i<id>/v<N>/) и сохрани цену оттуда.")
            # Решение по save_confirmed_price принимает инлайн-обработчик process_row:
            # проверка наименования (совпадение/критическое замечание/confirm), запись в БД/кэш.
            # Здесь — только пассивный статус, чтобы не было двух противоречивых сообщений.
            return ("(save_confirmed_price: проверка наименования и запись цены обрабатываются "
                    "системой. Следующий результат — решение: сохранено / кандидат-фолбэк / "
                    "критическое замечание.)")

        elif name == "search_sites":
            pt = args.get("product_type", "")
            sites = mm.get_sites(pt)
            if not sites:
                return f"Нет сайтов для {pt}"
            incompatible = get_run_config("site_incompatible_types", {})
            incompatible_sites = set()
            for site_name, types in (incompatible or {}).items():
                if pt in types:
                    incompatible_sites.add(site_name)
            filtered = [s for s in sites if s["id"] not in incompatible_sites]
            if not filtered:
                return f"Нет совместимых сайтов для {pt} (несовместимые: {', '.join(incompatible_sites)})"
            if incompatible_sites:
                logger.info("🚫 Filtered incompatible sites for %s: %s", pt, incompatible_sites)
            return f"Сайты: {', '.join(s['id'] for s in filtered[:10])}"

        elif name == "save_discovered_site":
            domain = args.get("domain", "")
            mm.add_site(domain, args.get("name", domain), args.get("product_type", ""))
            if args.get("approach_steps"):
                mm.save_approach(
                    product_type=args.get("product_type", ""),
                    site=domain,
                    concrete_steps=args["approach_steps"],
                    method="auto_discovered",
                )
            return f"Новый сайт сохранён: {domain}"

        elif name == "get_hints":
            pt = args.get("product_type", "")
            hints = mm.get_hints(pt) + mm.get_hints(UNKNOWN_PT)
            if not hints:
                return "Нет подсказок для этого типа товара"
            lines = [f"Подсказки ({len(hints)}):"]
            for h in hints[:5]:
                text = h.get("hint_text", "")
                priority = h.get("priority", 0.5)
                lines.append(f"  [{priority:.1f}] {text}")
            return "\n".join(lines)

        return f"error: unknown tool {name}"

    except Exception as e:
        logger.exception(f"Graph tool {name} failed")
        return f"error: {e}"


def _clean_snapshot(content: str) -> str:
    lines = content.split("\n")
    cleaned = []
    in_preamble = True
    for line in lines:
        s = line.strip()
        if in_preamble and (not s or s.startswith("###") or s == "---" or _SNAPSHOT_LINE_RE.match(s)):
            continue
        in_preamble = False
        cleaned.append(line)
    return "\n".join(cleaned) if cleaned else content


def _penalize_approaches(memory_manager, approach_ids, reason_prefix=""):
    """Штрафует ТОЛЬКО переданные подходы (по одному счёту на подход).

    Пороги внутри graph_engine.update_approach_failure: 3 неудачи → cooldown 24ч,
    10 → deprecate. Раньше _deprecate_site_approaches штрафовала ВСЕ подходы
    (product_type, site) за один фейл строки — успешные подходы сгорали за 2-3
    неудачные строки (в прогоне 26.08 уничтожено ~150 подходов).
    """
    if not approach_ids:
        return
    ids = []
    for aid in approach_ids:
        try:
            aid_int = int(aid)
        except (TypeError, ValueError):
            continue
        if aid_int not in ids:
            ids.append(aid_int)
    for aid in ids:
        try:
            memory_manager.record_failure(aid)
        except Exception as e:
            logger.warning("record_failure(%s) failed: %s", aid, e)
    if ids:
        logger.warning("%s penalized %d approaches", reason_prefix, len(ids))


def _deprecate_site_approaches(memory_manager, product_type, domain, reason_prefix="", approach_ids=None):
    """Слепая деприкация всех подходов сайта — только для captcha (сайт реально
    заблокирован). Для force-switch/max-rounds используйте _penalize_approaches
    с подмножеством подходов, показанных агенту."""
    if approach_ids:
        _penalize_approaches(memory_manager, approach_ids, reason_prefix)
        return
    approaches = memory_manager.get_site_approaches(product_type, domain)
    if not approaches:
        approaches = memory_manager.get_approaches_by_site(domain)
    for appr in approaches:
        if appr.get("id"):
            memory_manager.record_failure(appr["id"])
    if approaches:
        logger.warning("%s deprecated %d approaches on %s", reason_prefix, len(approaches), domain)


def _store_semantic_cache(semantic_cache, spec_text, result):
    if semantic_cache is None or result.get("price") is None:
        return
    if _is_family_page(result.get("url", "")):
        logger.warning("⚠️ Not caching family-page price: url=%s spec=%s", result.get("url", ""), spec_text[:50])
        return
    try:
        semantic_cache.store(spec_text, result)
    except Exception as e:
        logger.warning("Semantic cache store failed: %s", e)


def _save_price_and_approach(memory_manager, spec_text, product_type, price_data, steps, record_soldat=False, search_query=None):
    if _is_family_page(price_data.get("url", "")):
        logger.warning("⚠️ Skipping family-page price save: url=%s price=%.2f spec=%s",
                       price_data.get("url", ""), price_data["price"], spec_text[:50])
        return
    if _is_homepage_or_search_url(price_data.get("url", "")):
        logger.warning("⚠️ Skipping homepage/search price save: url=%s price=%.2f spec=%s",
                       price_data.get("url", ""), price_data["price"], spec_text[:50])
        return
    query = (search_query or spec_text)[:200]
    memory_manager.save_price(
        spec_text=spec_text, product_type=product_type,
        site=price_data.get("site", ""), price=price_data["price"],
        url=price_data.get("url", ""), confidence=price_data["confidence"],
        reason=price_data.get("reason", ""),
    )
    if record_soldat:
        memory_manager.record_soldat(product_type, price_data.get("site", ""))
    try:
        saved_id = memory_manager.save_approach(
            product_type=product_type,
            site=price_data.get("site", ""),
            concrete_steps=MemoryManager.clean_steps(steps) or [{"action": "search", "query": query[:100]}],
            method="browser_search",
            search_query=query,
            notes=f"price {price_data['price']} in {len(steps)} steps",
            param_slots={"product_name": {"type": "string", "description": "название товара из спецификации"}},
        )
        if saved_id:
            memory_manager.record_success(saved_id)
            step_summary = " → ".join(
                f"{s.get('action','?')}[{_portable_step_target(s)}]"
                for s in (MemoryManager.clean_steps(steps) or [])[:5]
            )
            logger.info("✅ Approach saved (ID=%d) for %s on %s: %.2f rub | steps: %s",
                        saved_id, product_type, price_data.get("site", ""), price_data['price'], step_summary)
        # 3.4: персистим выигрышный паттерн как hint (тип, сайт) — «как найти этот товар».
        # Переносит микро-стратегию (запрос с размером, URL-паттерн) между строками.
        if product_type != UNKNOWN_PT:
            site_domain = (price_data.get("site") or "").split("//")[-1].split("/")[0].removeprefix("www.")
            if site_domain and query:
                try:
                    hint_text = f"{site_domain}: этот товар найден по запросу «{query}»"
                    if price_data.get("url"):
                        hint_text += f"; карточка: {price_data['url'][:120]}"
                    # 2.3: рабочий селектор извлечения — переиспользуемый, не переизобретать.
                    extract_js = next(
                        (s.get("js_summary") for s in (steps or [])
                         if s.get("action") == "browser_evaluate" and s.get("js_summary")),
                        "",
                    )
                    if extract_js:
                        hint_text += f"; извлечение: {extract_js}"
                    memory_manager.add_hint(
                        product_type=product_type,
                        text=hint_text,
                        site=site_domain,
                        priority=0.7,
                    )
                except Exception as e:
                    logger.warning("Failed to save winning-pattern hint: %s", e)
    except Exception as e:
        logger.warning("Failed to save approach/success for %.2f price: %s", price_data['price'], e)


def _normalized_equal(a: str, b: str) -> bool:
    """Нормализованное равенство строк (lowercase + схлопывание пробелов)."""
    return " ".join((a or "").lower().split()) == " ".join((b or "").lower().split())


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlparse(url).hostname or ""
        return host.removeprefix("www.") if host else ""
    except Exception:
        return url.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]


def _is_product_card_url(url: str) -> bool:
    """True, если URL похож на страницу карточки товара."""
    if not url:
        return False
    return bool(
        re.search(r"/catalog/.*/i\d+", url, re.IGNORECASE)
        or re.search(r"/(?:product|item|p|products)/", url, re.IGNORECASE)
        or re.search(r"[?&]id=\d+", url, re.IGNORECASE)
    )


def _is_homepage_or_search_url(url: str) -> bool:
    """True, если URL — главная страница сайта или поисковая выдача
    (не карточка товара). Такие цены недопустимы как источник."""
    if not url:
        return True
    u = (url or "").split("?")[0].rstrip("/")
    path = u.split("//")[-1]
    domain = path.split("/")[0]
    rest = path[len(domain):].strip("/")
    if not rest:
        return True
    return bool(re.search(r"/search", u, re.IGNORECASE))


def _is_family_page(url: str) -> bool:
    """True для страницы-каталога без конкретного варианта (santech
    /catalog/N/M/i<id>/), где цена неоднозначна — на ней несколько
    модификаций товара с разными ценами."""
    if not url:
        return False
    u = url.strip().rstrip("/")
    return bool(re.search(r"/catalog/\d+/\d+/i\d+$", u, re.IGNORECASE))


def _is_empty_search_result(tool_name: str, content: str) -> bool:
    """True, если поисковый зонд (evaluate/find) вернул пустой результат —
    товар на сайте не найден."""
    if tool_name not in ("browser_evaluate", "evaluate", "browser_find", "find"):
        return False
    if content.startswith("error:"):
        return False
    if _extract_price_candidate(content):
        return False
    c = (content or "").strip().lower()
    if "no matches found" in c:
        return True
    return c in ("", "[]", "{}", "null", "none", "undefined", "nan", "n/a", "ok", "empty")


def _is_search_results_url(url: str) -> bool:
    """True, если URL — страница результатов поиска (выдача могла не догрузиться)."""
    u = (url or "").lower()
    return bool(re.search(r"/search|\bterm=|keyword=|search\?|&\?q=|[\?&]q=", u))


_PRICE_RE = re.compile(r"\d(?:[\d\s.,]{0,11})\s*(?:руб|р\.|₽|Р|P)(?!\w)", re.IGNORECASE)


def _extract_price_candidate(text: str) -> str | None:
    """Первый похожий на цену фрагмент вида «7 201,30 Р» или None."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    return m.group(0)[:30] if m else None


_GENERIC_PRODUCT_WORDS = {
    "радиатор", "кран", "клапан", "вентиль", "задвижка", "труба", "трубка", "кабель",
    "насос", "фитинг", "отвод", "тройник", "переход", "муфта", "устройство", "аппарат",
    "смеситель", "батарея", "изделие", "элемент", "деталь", "арматура",
}


def _price_is_relevant(spec_text: str, spec_meta: dict | None, tool_content: str) -> bool:
    """Цена-кандидат релевантна спецификации, если рядом с ней в контенте есть
    ключевой токен товара: тип/обозначение или артикул из spec_meta, либо специфичное
    значимое слово спецификации (МС-140, чугунный, гигиенический и т.п.).

    Защита от ложного срабатывания: цена в ВЫДАЧЕ поиска от чужого товара
    (например, электрический щиток 3 555 ₽ рядом с запросом «МС-140») не должна
    считаться кандидатом — иначе гейт блокирует уход с сайта, где точного товара
    нет, и агент в отчаянии сохраняет чужой товар (регрессия 27.08).

    Родовые слова («радиатор», «кран») НЕ считаются достаточными: они есть у любого
    товара категории, и цена соседнего радиатора (LEMAX VC) не должна быть принята
    за кандидата МС-140.
    """
    from src.approach_relevance import is_standard_reference
    low = (tool_content or "").lower()
    if not low:
        return False
    meta = spec_meta or {}
    for key in ("spec", "article"):
        val = str(meta.get(key) or "").strip()
        if val and not is_standard_reference(val) and val.lower() in low:
            return True
    # Специфичные значимые слова спецификации (без родовых наименований).
    from src.approach_relevance import _product_tokens, _OPTIONAL_SET
    toks = [t for t in _product_tokens(spec_text)
            if t not in _OPTIONAL_SET and t not in _GENERIC_PRODUCT_WORDS and len(t) >= 4]
    return any(t in low for t in toks)


def _model_mismatch_hint(tool_args: dict, spec_text: str, spec_meta: dict | None = None) -> str | None:
    """Совет перед кликом по карточке: если название элемента содержит модель,
    размер/Ду или артикул, отличный от спецификации — не открывай «для проверки».

    Регрессия 27.08: агент открыл карточку «ЦМО МС-40» (полка для шкафа) как
    «лучший аналог» МС-140. Регрессия MVT-R: агент открыл карточку Ду20 для
    товара с артикулом 003Z4040R (DN15 LF), хотя размеры и артикулы разные.
    Система-советник: НЕ блокирует, но жёстко предупреждает.
    """
    from src.approach_relevance import _size_key
    elem = str(tool_args.get("element") or tool_args.get("target") or tool_args.get("text") or "").strip()
    if not elem:
        return None

    hints = []

    # 1. Проверка модели (буквенно-цифровой код)
    spec_models = model_designators(spec_text)
    elem_models = model_designators(elem)
    if spec_models and elem_models and not (elem_models & spec_models):
        hints.append(f"модель «{'», «'.join(sorted(elem_models))}» (спецификация: «{'», «'.join(sorted(spec_models))}»)")

    # 2. Проверка размера/Ду
    spec_sizes = _size_key(spec_text)
    elem_sizes = _size_key(elem)
    if spec_sizes and elem_sizes and spec_sizes != elem_sizes:
        hints.append(f"размер/Ду «{'», «'.join(sorted(elem_sizes))}» (спецификация: «{'», «'.join(sorted(spec_sizes))}»)")

    # 3. Проверка артикула — только если уже есть другое расхождение (размер/модель)
    article = (spec_meta or {}).get("article") or ""
    if article and article.lower() not in elem.lower() and hints:
        hints.append(f"артикул «{article}» отсутствует в названии карточки")

    if not hints:
        return None

    return (
        f"⚠️ ВНИМАНИЕ: {'; '.join(hints)}. "
        "Это, вероятно, ДРУГОЙ товар. НЕ открывай карточку «для проверки» — это "
        "потерянный раунд. Продолжай поиск в выдаче или переключись на другой сайт."
    )


def _stuck_target(tool_name: str, tool_args: dict) -> str:
    """Подпись действия для StuckDetector. Для browser_evaluate включает
    отпечаток JS: разные скрипты ≠ зацикливание, одинаковые — сигнал цикла."""
    target = tool_args.get("target") or tool_args.get("url") or ""
    if tool_name == "browser_evaluate":
        js = str(tool_args.get("function") or "")
        target = f"js:{hashlib.md5(js.encode('utf-8', errors='replace')).hexdigest()[:10]}"
    return str(target)


def _query_lost_tokens(prev_query: str, new_query: str) -> list[str]:
    """Что новый запрос потерял относительно предыдущего (модель/размер).

    Возвращает список потерянных дискриминаторов: «модель», «размер».
    Используется как совет: упрощение запроса допустимо, но потеря модели/размера
    делает товар неузнаваемым для поиска магазина.
    """
    lost = []
    prev_models = model_designators(prev_query or "")
    new_models = model_designators(new_query or "")
    if prev_models and not (new_models & prev_models):
        lost.append("модель")
    prev_sizes = _size_key(prev_query or "") or set()
    new_sizes = _size_key(new_query or "") or set()
    if prev_sizes and not (new_sizes & prev_sizes):
        lost.append("размер")
    return lost


def _normalize_js(js: str) -> str:
    """Нормализует JS-код для кэша повторов browser_evaluate.

    Агент пишет «почти те же» скрипты (отличия только в пробелах/кавычках) —
    точный md5 их не объединяет, и кэш бесполезен (248 повторов из 941 в прогоне).
    Схлопываем пробелы/переносы и унифицируем кавычки: эквивалентные скрипты
    получают один хэш.
    """
    if not js:
        return ""
    out = re.sub(r"\s+", " ", js)
    out = out.replace("'", '"')
    return out


def _clear_field_js(tool_args: dict) -> str | None:
    """JS-сниппет очистки поля ввода перед browser_type.

    SPA-сайты (satro-paladin digiSearch и пр.) не очищают поле сами: повторный
    ввод склеивает старый текст с новым («МС-140 Мх500МС-140»). Если агент передал
    CSS-селектор target («input[name="search"]», «input.search_input») или element,
    очищаем поле нативно через value setter + input event, чтобы SPA-фреймворк
    увидел изменение. Возвращает JS-код или None (нет надёжного селектора).
    """
    target = str(tool_args.get("target") or tool_args.get("element") or tool_args.get("ref") or "").strip()
    if not target or _is_hash_ref(target):
        return None
    # Роль-локатор («textbox "Поиск"») не является CSS-селектором — пропускаем,
    # чтобы не падать на парсинге. Чистим только по явному CSS.
    if "textbox" in target or target.startswith("a:has") or "role=" in target:
        return None
    sel = target.replace("'", "\\'")
    return (
        "() => { const inp = document.querySelector('" + sel +
        "'); if (!inp) return 'no input'; const proto = inp instanceof HTMLTextAreaElement "
        "? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; "
        "const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; "
        "setter.call(inp, ''); inp.dispatchEvent(new Event('input', {bubbles:true})); "
        "return 'cleared'; }"
    )


def _build_diagnostic_message(spec_text: str, current_site: str, card_open: bool = False,
                              price_candidate_seen: bool = False,
                              recent_errors: list | None = None) -> str:
    """Сообщение-диагноз при зацикливании: даёт LLM шанс исправить причину,
    а не уходить вслепую с сайта."""
    parts = [f"⚠️ Товар: {spec_text}. Ты зациклился — последние действия повторяются. "
             "ПРОАНАЛИЗИРУЙ причину и исправь её, а не повторяй тот же код:"]
    if card_open:
        parts.append(f"- Карточка товара ОТКРЫТА ({current_site}). Если цена ещё не извлечена — "
                     "извлеки её из ОТКРЫТОЙ карточки корректным селектором: "
                     "querySelectorAll('[class*=\"price\"], [data-price], .product-price, .price'), "
                     "выбери элемент с числом и символом валюты (₽/Р/р/руб). "
                     "Затем сохрани цену через save_confirmed_price с product_name.")
    else:
        parts.append("- Карточка товара НЕ открыта. Вернись к поиску товара на текущем сайте "
                     "или переключись на другой сайт.")
    if price_candidate_seen:
        parts.append("- В результатах УЖЕ была цена (price_candidate). Не теряй её: извлеки "
                     "и сохрани через save_confirmed_price с product_name.")
    if recent_errors:
        parts.append(f"- Последние ошибки: {', '.join(recent_errors[-3:])}. "
                     "Исправь код/селектор, а не повторяй ту же команду.")
    parts.append("- Только если сайт действительно бесполезен — переключись на другой сайт из списка.")
    return "\n".join(parts)


def _mismatch_warning_content(spec_text: str, found_name: str) -> str:
    """Совет агенту при расхождении наименования (система НЕ решает — советует).

    Сообщает, каких слов не хватает, разделяя КЛЮЧЕВЫЕ (тип/материал/соединение/
    бренд/размер — должны совпадать) и ОПИСАТЕЛЬНЫЕ (серия/комплектация/детали,
    которые сайт часто опускает в сокращённом заголовке). Предлагает LLM
    перепроверить карточку и решить: исправить product_name либо сохранить с
    confirm=true. НЕ блокирует и НЕ принимает решение за LLM.
    """
    from src.approach_relevance import _OPTIONAL_SET, model_designators
    missing = missing_required_tokens(spec_text, found_name)
    descriptive = sorted(w for w in missing if w in _OPTIONAL_SET)
    key = sorted(w for w in missing if w not in _OPTIONAL_SET)
    # Модель/тип (C10 vs C20) — дифференциатор, сравнивается на сыром тексте.
    spec_models = model_designators(spec_text)
    model_diff = spec_models and model_designators(found_name) != spec_models
    parts = [f"⚠️ СОВЕТ: наименование «{(found_name or '')[:80]}» не полностью "
             f"совпадает со спецификацией «{(spec_text or '')[:80]}»."]
    if model_diff:
        parts.append(f" КЛЮЧЕВОЕ расхождение: МОДЕЛЬ/ТИП спецификации «{'», «'.join(sorted(spec_models))}» "
                     "не совпадает с карточкой — вероятно, ДРУГОЙ товар (например C10 ≠ C20). "
                     "Перепроверь заголовок (h1) и характеристики.")
    if key:
        parts.append(f" КЛЮЧЕВЫЕ слова спецификации отсутствуют: «{'», «'.join(key)}» — "
                     "это тип/материал/соединение/бренд; если их правда нет на карточке, "
                     "вероятно, это ДРУГОЙ товар — перепроверь заголовок (h1) и характеристики.")
    if descriptive:
        parts.append(f" Описательные/комплектационные слова отсутствуют: "
                     f"«{'», «'.join(descriptive)}» — сайт может опускать их в сокращённом "
                     "заголовке; это НЕ обязательно значит, что товар другой.")
    parts.append(
        " Перепроверь НА КАРТОЧКЕ (не на других сайтах и не в Яндексе): "
        "1) полный h1 — не сокращай и не перефразируй; "
        "2) характеристики: тип, размер/Ду, материал, соединение, бренд. "
        "Если по ключевым атрибутам товар верен — вызови save_confirmed_price "
        "повторно с confirm=true (цена будет помечена как требующая ревью). "
        "Если ошибся — исправь product_name и сохрани снова."
    )
    return "".join(parts)


def _error_result(spec_text: str, error: str, elapsed: float | None = None) -> dict:
    logger.error("Row failed: %s -- %s", spec_text[:60], error)
    return {
        "spec_text": spec_text, "price": None, "confidence": 0.0,
        "reason": error, "requires_review": True, "error": error,
        "elapsed": elapsed,
    }


def _pick_best_fallback(candidates: list[dict]) -> dict | None:
    """Лучший кандидат-фолбэк: максимальная confidence. None, если кандидатов нет."""
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.get("confidence", 0.0))


def _fallback_result(spec_text: str, product_type: str, candidates: list[dict],
                     elapsed: float | None = None) -> dict | None:
    """Результат строки из кандидата-фолбэка (точный товар с нужным брендом не найден).

    Возвращает schema-результат с brand_mismatch=True и пометкой в reason, либо
    None, если кандидатов нет. В доверенный кэш не пишется — requires_review=True,
    confidence ограничена 0.5.
    """
    best = _pick_best_fallback(candidates)
    if best is None:
        return None
    result = {
        "spec_text": spec_text,
        "product_type": product_type,
        "price": best.get("price"),
        "confidence": round(min(best.get("confidence", 0.0), 0.5), 2),
        "url": best.get("url", ""),
        "site": best.get("site", ""),
        "reason": (f"не совпадает бренд: найден аналог «{best.get('product_name', '')}» "
                   f"({best.get('url', '')}); товар с нужным брендом на сайтах не найден"),
        "requires_review": True,
        "brand_mismatch": True,
        "elapsed": elapsed,
    }
    return _result_to_schema(result)


def _result_to_schema(result: dict) -> dict:
    """Переводит result-dict в ExtractionResult для строгой валидации.
    Наружу отдаём .model_dump() чтобы не ломать MCPAgentRunner/ExcelWriter."""
    try:
        model = ExtractionResult(
            spec_text=result.get("spec_text", ""),
            product_type=result.get("product_type", "unknown"),
            found=result.get("price") is not None,
            price=result.get("price"),
            confidence=result.get("confidence", 0.0),
            url=result.get("url", ""),
            site=result.get("site", ""),
            reason=result.get("reason", ""),
            requires_review=result.get("requires_review", True),
            error=result.get("error"),
            elapsed=result.get("elapsed"),
            brand_mismatch=result.get("brand_mismatch", False),
        )
        out = model.model_dump()
    except Exception as e:
        logger.warning("Schema validation failed: %s", e)
        return result
    # Служебные ключи, не входящие в контракт ExtractionResult, но нужные
    # GUI (например, ductwork_breakdown для колонки «Пометка»).
    for extra in ("ductwork_breakdown",):
        if result.get(extra) is not None:
            out[extra] = result[extra]
    return out


CONTEXT_TOKEN_BUDGET = 12000


_CYR_RE = re.compile(r'[А-Яа-яЁё]')
_ASCII_RE = re.compile(r'[A-Za-z0-9{}()<>\[\]/\\:;,._+\-*&%$#@!?\'"`~^|=]')


def _estimate_tokens(text: str) -> int:
    """Честная оценка токенов: кириллица ~2 симв./токен, ASCII ~4, прочее ~3.

    Раньше len//4 занижал счёт для кириллицы в ~1.7–2 раза (системный промпт на
    ~69% кириллица): «8000» по факту означало 12–16k реальных токенов в LM Studio.
    Теперь число бюджета соответствует реальному счёту (бюджет 12000 ≈ прежний
    эффективный реальный контекст — поведение сохранено, цифра стала честной).
    """
    if not text:
        return 0
    cyr = len(_CYR_RE.findall(text))
    ascii_chars = len(_ASCII_RE.findall(text))
    other = max(0, len(text) - cyr - ascii_chars)
    return max(1, cyr // 2 + ascii_chars // 4 + other // 3)


def _message_size(msg: dict) -> int:
    size = _estimate_tokens(str(msg.get("content") or ""))
    size += _estimate_tokens(str(msg.get("role") or ""))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        size += _estimate_tokens(fn.get("name", "") + str(fn.get("arguments", "")))
    return size


def _keep_newest_exchanges(messages: list[dict], budget: int) -> list[dict]:
    """Возвращает НОВЕЙШИЕ сообщения из `messages`, укладывающиеся в `budget`.

    Сообщения группируются в атомарные блоки «assistant + следующие за ним
    tool-результаты» — связка tool_call_id ↔ tool не рвётся: LLM не получит
    tool-ответ без его вызова (нарушение протокола OpenAI).
    """
    if budget <= 0 or not messages:
        return []
    blocks: list[list[dict]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "tool":
            blocks.append([m])
            i += 1
            continue
        block = [m]
        i += 1
        if m.get("role") == "assistant":
            while i < n and messages[i].get("role") == "tool":
                block.append(messages[i])
                i += 1
        blocks.append(block)
    kept: list[list[dict]] = []
    kept_size = 0
    for b in reversed(blocks):
        bsz = sum(_message_size(m) for m in b)
        if kept_size + bsz <= budget:
            kept.insert(0, b)
            kept_size += bsz
        else:
            break
    return [m for b in kept for m in b]


def _trim_messages_for_budget(messages: list[dict], budget: int = CONTEXT_TOKEN_BUDGET) -> list[dict]:
    """Сжимает историю, если суммарный объём превышает бюджет.

    Гарантии:
    1. system сохраняется;
    2. ПЕРВОЕ user-сообщение (задача: спека + контекст) сохраняется как якорь —
       RowFacts не дублирует spec_text, без задачи LLM теряет цель строки;
    3. НОВЕЙШИЕ полные обмены (assistant+tool) сохраняются в слайдинг-окне (3 пары),
       связка tool_call_id ↔ tool не рвётся;
    4. Старые раунды заменяются СВОДКОЙ (summary), содержащей посещённые сайты и ключевые факты;
    5. Итог НЕ превышает budget (якорь усекается по контенту в крайнем случае).

    Память строки не теряется: RowFacts пересоздаётся per-call и инжектится после трима.
    """
    if not messages:
        return messages
    total = sum(_message_size(m) for m in messages)
    if total <= budget:
        return messages

    system = messages[:1] if messages and messages[0].get("role") == "system" else []
    rest = messages[len(system):]

    # Якорь-задача: первое user-сообщение (спека + контекст). Не выбрасываем,
    # но не даём монополизировать бюджет (в крайнем случае усекаем контент).
    first_user = next((m for m in rest if m.get("role") == "user"), None)
    anchor = None
    if first_user is not None:
        anchor = dict(first_user)
        anchor_cap_tokens = max(64, budget // 3)
        if _message_size(anchor) > anchor_cap_tokens:
            content = str(anchor.get("content") or "")
            anchor["content"] = content[:anchor_cap_tokens * 4]
        rest = [m for m in rest if m is not first_user]

    reserved = sum(_message_size(m) for m in system)
    if anchor is not None:
        reserved += _message_size(anchor)
    newest = _keep_newest_exchanges(rest, max(0, budget - reserved))

    out = system + ([anchor] if anchor is not None else []) + newest
    out_size = sum(_message_size(m) for m in out)
    logger.info("Context trim: %d → %d tokens (kept %d of %d messages)",
                total, out_size, len(out), len(messages))
    return out


def _inject_facts_block(messages: list[dict], block: str) -> list[dict]:
    """Вставляет свежий блок фактов после system (локальный список — накопления нет)."""
    if not block:
        return messages
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    return messages[:insert_at] + [{"role": "user", "content": block}] + messages[insert_at:]


async def _query_llm(llm_client, messages, tools, temperature: float | None = None, monitor_callback: Callable[[str, object], None] | None = None,
                     facts: RowFacts | None = None):
    """Обёртка над llm_client.chat с Circuit Breaker и температурой фазы.
    chat() возвращает {"error": ...} вместо исключения — состояние фиксируем вручную."""
    if not llm_circuit.allow_request():
        logger.error("LLM unavailable, pausing agent...")
        await asyncio.sleep(30)
        return {"error": "LLM circuit open"}
    messages = _trim_messages_for_budget(messages)
    if facts is not None:
        # Операционная память строки переживает trim: блок пересоздаётся per-call.
        messages = _inject_facts_block(messages, facts.to_prompt_block())
    t0 = time.monotonic()
    response = await llm_client.chat(messages, tools, temperature=temperature)
    elapsed = time.monotonic() - t0
    if "error" in response:
        llm_circuit.record_failure()
    else:
        llm_circuit.record_success()
        if monitor_callback:
            monitor_callback("llm_call", elapsed)
    return response
