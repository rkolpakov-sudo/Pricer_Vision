import asyncio
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
from src.mcp_bridge import MCPBridge
from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.validator import validate_result
from src.config_loader import get_run_config, get_antidetect_config
from src.models.schemas import ExtractionResult
from src.stuck_detector import StuckDetector, StuckLevel
from src.resilience import llm_circuit
from src.adaptive_limits import AdaptiveRoundManager
from src.rate_limiter import DomainRateLimiter
from src.captcha_detector import CaptchaDetector, CaptchaType
from src.approach_relevance import approach_relevant

logger = logging.getLogger("pricer.agent")

MAX_ROUNDS = get_run_config("max_rounds", 60)
MAX_ROUNDS_PER_SITE = get_run_config("max_rounds_per_site", 15)
SUMMARIZE_MAX_CHARS = get_run_config("summarize_max_chars", 8000)
SUMMARIZE_MAX_LINES = get_run_config("summarize_max_lines", 200)
CAPTCHA_KEYWORDS = get_run_config("captcha_keywords", ["ddos-guard", "hcheck", "js-check"])
SEARCH_ENGINE = get_run_config("search_engine", "Яндекс")
UNKNOWN_PT = "unknown"
CONF_TRUSTED = 0.9
CONF_GOOD = 0.8
CONF_MIN = 0.6

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
            "description": "Записать подтверждённую цену в граф. Confidence < 0.6 — не записывай. После вызова цена считается финальной.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec_text": {"type": "string"},
                    "product_type": {"type": "string"},
                    "site": {"type": "string"},
                    "price": {"type": "number"},
                    "url": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"}
                },
                "required": ["spec_text", "product_type", "site", "price", "url", "confidence"]
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
1. Сначала проверь get_approaches. Если есть подход — используй его target/element (CSS-селекторы) и последовательность действий. НО: url для browser_navigate должен вести на ГЛАВНУЮ сайта или страницу поиска, а не на конкретный товар из подхода. Текст для поиска (query, text) бери из текущего ТОВАР ДЛЯ ПОИСКА. Вводи ПОЛНОЕ наименование дословно — НЕ сокращай, НЕ перефразируй и НЕ меняй слова (например, «Воздуховод из оцинкованной стали Ø100, толщина стали 0,5мм» вводи целиком; допускается убрать только техническую часть после запятой).
2. Работай с ОДНИМ сайтом за раз. НЕ переключайся между сайтами без причины.
3. browser_snapshot даёт accessibility-tree. Если цены не видны — используй browser_evaluate с JS (querySelectorAll) для прямого извлечения данных из DOM.
4. После поиска на сайте: кликни на карточку товара → откроется страница с ценой. Если цены нет в карточке — ищи на странице через browser_evaluate.
5. Если точного совпадения нет — сохрани лучший найденный аналог НЕМЕДЛЕННО через save_confirmed_price с confidence 0.3-0.5 и requires_review=True. Укажи в reason расхождение в названии. Можно найти лучшую цену на другом сайте — она перезапишет эту. НЕ трать раунды на поиск более точного совпадения на том же сайте.
6. После нахождения цены: save_confirmed_price + save_approach.
7. Если цена не найдена — верни null, не выдумывай.
8. Если get_confirmed_prices вернул цену с confidence >= 0.9 — используй её как финальную, НЕ проверяй в браузере. Сразу вызови save_confirmed_price.
9. Если ты сделал >10 шагов на одном сайте без результата — принудительно переключись на другой сайт из списка.
10. Если не знаешь, как работать на сайте — вызови get_hints. В хинтах может быть написано, где искать цену, какие селекторы использовать.
11. Если артикул не дал результата на первом сайте — на следующем сайте ищи уже по ПОЛНОМУ названию товара из спецификации, а не по артикулу.
12. Яндекс — это ТОЛЬКО поисковик для нахождения сайта магазина. Если у тебя нет сайтов для товара — иди на yandex.ru, найди товар, кликни на ссылку магазина из результатов поиска и извлеки цену ИЗ КАРТОЧКИ ТОВАРА НА САЙТЕ МАГАЗИНА. НЕ извлекай цену из сниппета Яндекса — Яндекс не источник цен.
13. После save_confirmed_price можно продолжить поиск на других сайтах для лучшей цены, но базовая цена уже сохранена.
14. Если сайт явно НЕ ПОДХОДИТ для товара (например, сантехнический сайт для кабеля, или производитель труб для электроники) — НЕМЕДЛЕННО переключайся на следующий сайт. Не трать больше 2 раундов на заведомо неподходящий сайт.
15. Если сайт использует SPA (результаты поиска не появляются после Enter) — попробуй browser_navigate напрямую на URL поиска: site.ru/search?q=ТОВАР. Не нажимай Enter на SPA-сайтах — используй прямые URL.
16. Если в структуре файла указан завод-изготовитель, тип/обозначение или артикул/код — используй их для правильного выбора товара. Бренд/тип не обязательно вставлять в поисковый запрос: сначала найди товар по наименованию, затем среди результатов отдай предпочтение позиции того же производителя/модели/артикула. Если товар выпускается несколькими заводами — это критично для выбора правильного аналога. Если «производитель» — это страна (например «Россия») или ссылка на стандарт (ГОСТ/ТУ/СНиП) — НЕ используй их как бренд и не вставляй в поиск.

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
        target = s.get("url") or s.get("target") or s.get("element") or ""
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
        for k in ("url", "text", "target", "element", "key", "js_summary"):
            v = s.get(k)
            if v:
                val = str(v)[:80]
                parts.append(f"{k}={val}")
        lines.append(f"      {i}. {' '.join(parts)}")
    return "\n".join(lines)


def _apply_approach(approach: dict, spec_text: str) -> dict:
    adapted = dict(approach)
    # always update search_query to current spec_text
    adapted["search_query"] = spec_text[:200]
    slots = approach.get("param_slots") or {}
    adapted["concrete"] = []
    for step in approach.get("concrete", []):
        step = dict(step)
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
    spec_meta: dict | None = None,
    semantic_cache=None,
    monitor_callback: Callable[[str, object], None] | None = None,
) -> dict:
    start_time = datetime.now()

    def _stop_check():
        if stop_event and stop_event.is_set():
            raise asyncio.CancelledError("stopped by user")

    product_type = graph_engine.classify_product_type(spec_text)
    approaches = memory_manager.get_all_approaches(product_type) if product_type != UNKNOWN_PT else memory_manager.get_all_approaches_flat()
    confirmed_prices = [] if fresh else memory_manager.get_relevant_prices(spec_text)

    # code-enforced rule 8: reuse high-confidence prices without LLM
    if not fresh and confirmed_prices:
        best = max(confirmed_prices, key=lambda p: p.get("confidence", 0))
        if best.get("confidence", 0) >= CONF_TRUSTED and (not fresh or best.get("confidence", 0) >= 0.95):
            elapsed = (datetime.now() - start_time).total_seconds()
            result = {
                "spec_text": spec_text, "price": best.get("price"),
                "confidence": best.get("confidence", 0),
                "url": best.get("url", ""), "site": best.get("site_id", ""),
                "reason": "Reused from DB (confidence >= 0.9)", "requires_review": False,
                "elapsed": elapsed,
            }
            memory_manager.save_price(
                spec_text=spec_text, product_type=product_type,
                site=best.get("site_id", ""), price=best.get("price", 0),
                url=best.get("url", ""), confidence=best.get("confidence", 0),
                reason="rule8_reuse",
            )
            logger.info("Row: price=%s validated=%.2f in %.1fs", result["price"], result["confidence"], elapsed)
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
            return _result_to_schema(result)

    sites = memory_manager.get_sites(product_type)
    # Adaptive rounds per-site: reduce for high-failure sites
    adaptive_limits = AdaptiveRoundManager(base_rounds=MAX_ROUNDS_PER_SITE)
    site_round_limits = adaptive_limits.per_site_limits(sites) if sites else {}
    hints = memory_manager.get_hints(product_type) or []
    if product_type != UNKNOWN_PT:
        hints += memory_manager.get_hints(UNKNOWN_PT)
    product_data = graph_engine._all_products.get(product_type)

    all_flat = memory_manager.get_all_approaches_flat()
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

    context = _build_context(spec_text, product_type, approaches, confirmed_prices, sites, hints, product_data, site_guides, concepts, spec_meta)

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
    logger.info("Tools: MCP=%d, graph=%d, total=%d", len(mcp_tools), len(GRAPH_TOOL_DEFS), len(all_tools))

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context},
    ]

    response = await _query_llm(llm_client, messages, all_tools, temperature=TEMP_EXPLORATION, monitor_callback=monitor_callback)
    if "error" in response:
        return _error_result(spec_text, f"LLM: {response['error']}")

    rounds = 0
    current_site = ""
    rounds_on_site = 0
    steps = []
    stuck_detector = StuckDetector()
    yandex_reminded = False
    yandex_price_saved = False
    rate_limiter = DomainRateLimiter(
        min_interval=get_antidetect_config("rate_limit_min_interval", 1.5),
        max_requests_per_minute=get_antidetect_config("rate_limit_max_requests_per_minute", 20),
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
            if result.get("confidence", 0) >= CONF_GOOD and result.get("price") is not None:
                _save_price_and_approach(memory_manager, spec_text, product_type, result, steps, record_soldat=True)
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info("Row: price=%s conf=%.2f in %.1fs rounds=%d", result.get('price'), result.get('confidence', 0), elapsed, rounds)
            final = {"spec_text": spec_text, "product_type": product_type, **result, "elapsed": elapsed}
            _store_semantic_cache(semantic_cache, spec_text, final)
            return _result_to_schema(final)

        if not tool_calls:
            messages.append({"role": "assistant", "content": content or "(no output)"})
            messages.append({"role": "user", "content": "Верни JSON с результатом поиска цены.\nФормат: {\"price\": число|null, \"confidence\": 0.0-1.0, \"url\": \"...\", \"site\": \"...\", \"reason\": \"...\", \"requires_review\": bool}"})
            _stop_check()
            response = await _query_llm(llm_client, messages, all_tools, temperature=TEMP_EXTRACTION, monitor_callback=monitor_callback)
            if "error" in response:
                return _error_result(spec_text, f"LLM: {response['error']}")
            continue
        msg = (response.get("choices") or [{}])[0].get("message", {})

        messages.append(msg)

        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("arguments", {})

            if tool_name in GRAPH_TOOL_NAMES:
                result = _execute_graph_tool(tool_name, tool_args, graph_engine, memory_manager, spec_text=spec_text)
            elif tool_name in ("browser_navigate", "navigate"):
                new_site = tool_args.get("url", "")
                # Soft Yandex reminder: warn but do NOT block navigation
                if "yandex" in current_site and not yandex_price_saved and new_site and "yandex" not in new_site.lower():
                    logger.info("ℹ️ Leaving Yandex for %s (yandex_price_saved=%s)", new_site, yandex_price_saved)
                if new_site and new_site != current_site:
                    current_site = new_site
                    rounds_on_site = 0
                if rate_limiter is not None:
                    await rate_limiter.wait_if_needed(new_site or "")
                result = await mcp_bridge.call_tool(tool_name, tool_args)
            else:
                result = await mcp_bridge.call_tool(tool_name, tool_args)

            if tool_name not in GRAPH_TOOL_NAMES:
                step = {"action": tool_name}
                if tool_name == "browser_navigate":
                    step["url"] = tool_args.get("url", "")
                elif tool_name in ("browser_type", "type_text"):
                    step["text"] = tool_args.get("text", "")
                    for k in ("target", "element", "submit", "slowly", "ref"):
                        v = tool_args.get(k)
                        if v is not None and v != "":
                            step[k] = v
                elif tool_name in ("browser_click", "click"):
                    step["target"] = str(tool_args.get("target", tool_args.get("ref", "")))
                    for k in ("element",):
                        v = tool_args.get(k)
                        if v is not None and v != "":
                            step[k] = v
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
            target = tool_args.get("target") or tool_args.get("url") or ""
            stuck_detector.record_action(
                action_type=tool_name,
                target=str(target),
                result="success" if not str(result).startswith("error:") else "no_change",
            )

            tool_content = str(result)
            if tool_name in ("browser_snapshot", "snapshot"):
                tool_content = _clean_snapshot(tool_content)
                # Sync current_site from snapshot URL (handles new tabs from browser_click)
                for line in tool_content.split("\n"):
                    if "Page URL:" in line:
                        url = line.split("Page URL:")[-1].strip().split()[0] if line.split("Page URL:")[-1].strip() else ""
                        if url and url != current_site:
                            current_site = url
                            rounds_on_site = 0
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
                    _deprecate_site_approaches(memory_manager, product_type, failed_domain, "🚫 Captcha:")
                except Exception as e:
                    logger.warning("Captcha deprecation failed: %s", e)
                tool_content = f"Сайт заблокирован captcha/проверкой бота ({captcha_type.value}). Рекомендация: {recommendation}. НЕ ПЫТАЙСЯ ЕГО ОБОЙТИ."
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
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": tool_content[:10000],
            })

            if tool_name == "save_confirmed_price":
                validated = validate_result({
                    "price": tool_args.get("price"),
                    "confidence": tool_args.get("confidence", 0.5),
                    "url": tool_args.get("url", ""),
                    "site": tool_args.get("site", ""),
                    "reason": tool_args.get("reason", ""),
                    "requires_review": True,
                }, spec_text)
                if validated.get("price") is not None:
                    yandex_price_saved = True
                    elapsed = (datetime.now() - start_time).total_seconds()
                    logger.info("Row: price=%s validated=%.2f in %.1fs", validated['price'], validated['confidence'], elapsed)
                    try:
                        _save_price_and_approach(memory_manager, spec_text, product_type, validated, steps, record_soldat=False)
                    except Exception as e:
                        logger.warning("Failed to save price/approach in save_confirmed_price: %s", e)
                    # Low confidence → save price but keep searching (rule 5)
                    if validated.get("confidence", 0) >= CONF_MIN:
                        final = {
                            "spec_text": spec_text, "product_type": product_type,
                            **validated, "elapsed": elapsed,
                        }
                        _store_semantic_cache(semantic_cache, spec_text, final)
                        return _result_to_schema(final)
                    logger.info("Low confidence (%.2f) — saved, continuing search", validated['confidence'])

        for tc_last in tool_calls:
            tn = tc_last.get("name", "")
            if tn not in ("get_approaches", "search_sites", "get_confirmed_prices",
                          "get_hints", "save_discovered_site", "save_approach"):
                rounds_on_site += 1
                break

        # StuckDetector: принудительный уход с сайта при зацикливании
        stuck_level = stuck_detector.detect()
        if stuck_level == StuckLevel.CRITICAL and rounds_on_site > 5:
            logger.warning("StuckDetector CRITICAL — forcing site switch")
            if monitor_callback:
                monitor_callback("stuck", None)
            current_site = ""
            rounds_on_site = site_round_limits.get(_extract_domain(current_site), MAX_ROUNDS_PER_SITE) + 1
            stuck_detector.reset()

        current_domain = _extract_domain(current_site)
        if rounds_on_site > site_round_limits.get(current_domain, MAX_ROUNDS_PER_SITE):
            logger.info("⚠️ Forcing site switch after %d rounds on %s", rounds_on_site, current_site or "?")
            # Track negative feedback — always, even for unknown product types
            if current_site:
                try:
                    failed_site = current_domain
                    if product_type != UNKNOWN_PT:
                        memory_manager.increment_consecutive_failures(product_type, failed_site)
                    _deprecate_site_approaches(memory_manager, product_type, failed_site, "📉 Force switch:")
                except Exception as e:
                    logger.warning("Force switch deprecation failed: %s", e)
            messages.append({
                "role": "user",
                "content": f"Ты сделал {rounds_on_site} шагов на текущем сайте без результата. Принудительно переключись на ДРУГОЙ сайт из списка."
            })
            rounds_on_site = 0
            current_site = ""
            _stop_check()
            response = await _query_llm(llm_client, messages, all_tools, temperature=TEMP_RECOVERY, monitor_callback=monitor_callback)
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
        response = await _query_llm(llm_client, messages, all_tools, temperature=TEMP_NAVIGATION, monitor_callback=monitor_callback)
        if "error" in response:
            return _error_result(spec_text, f"LLM: {response['error']}")

    elapsed = (datetime.now() - start_time).total_seconds()
    if current_site:
        try:
            failed_domain = _extract_domain(current_site)
            _deprecate_site_approaches(memory_manager, product_type, failed_domain, "📉 Max rounds:")
        except Exception as e:
            logger.warning("Max rounds deprecation failed: %s", e)
    logger.info("Row: max rounds reached in %.1fs", elapsed)
    return _error_result(spec_text, f"Max rounds ({MAX_ROUNDS}) reached", elapsed=elapsed)


_STANDARD_REF_RE = re.compile(
    r"^(гост|ту|снип|сп |iso|din|en |astm|фнп|пнст|мто|рм|сбн|гос[тт] р)\b",
    re.IGNORECASE,
)


def _is_standard_reference(spec: str) -> bool:
    """True, если «Тип/обозначение» — это ссылка на стандарт (ГОСТ/ТУ/СНиП/ISO...),
    а не модель товара. Такие значения не полезны для поиска."""
    s = spec.strip().lower()
    return bool(_STANDARD_REF_RE.match(s))


def _build_context(spec_text, product_type, approaches, confirmed_prices, sites, hints, product_data=None, site_guides=None, concepts=None, spec_meta=None):
    # фильтр релевантности: подходы, обученные на ДРУГИХ товарах того же типа
    # (например регуляторы скорости для воздуховодов), не показываются
    extra = (spec_meta or {}).get("article", "")
    approaches = [a for a in (approaches or []) if approach_relevant(a, spec_text, extra)]
    parts = [f"ТОВАР ДЛЯ ПОИСКА: {spec_text}"]
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
        success_sites = {a.get("site_id", "") for a in approaches if a.get("site_id", "") in site_ids and a.get("success_count", 0) > 0}
        price_sites = {p.get("site_id", "") for p in confirmed_prices if p.get("site_id", "") in site_ids}
        failed_sites = {a.get("site_id", "") for a in approaches if a.get("site_id", "") in site_ids and a.get("consecutive_failures", 0) >= 3}
        for s in sites:
            if s.get("consecutive_failures", 0) >= 3:
                failed_sites.add(s['id'])

        def _sort_key(s):
            sid = s['id']
            priority = s.get("priority", 2)
            if sid in success_sites:
                return 0
            if sid in approach_sites:
                return 1
            if sid in price_sites:
                return 2
            if priority == 0:
                return 3
            if priority == 1:
                return 4
            if sid in failed_sites:
                return 6
            return 5

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
                if a is approaches[0]:
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
            lines = [f"Подходов: {len(approaches)}"]
            for a in approaches[:5]:
                # адаптируем подход к текущему товару: устаревший хардкод-текст
                # в шагах ввода заменяется на актуальный spec_text
                a = _apply_approach(a, spec_text)
                pat = " -> ".join(s.get("action", "?") for s in a.get("pattern", []))
                concrete = a.get("concrete", [])
                detail_parts = []
                for s in concrete[:4]:
                    d = s.get("action", "?")
                    loc = s.get("target") or s.get("element") or ""
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
            pid = mm.save_price(
                spec_text=args.get("spec_text", ""),
                product_type=args.get("product_type", ""),
                site=args.get("site", ""),
                price=args.get("price", 0),
                url=args.get("url", ""),
                confidence=args.get("confidence", 0.95),
                reason=args.get("reason", ""),
            )
            mm.record_soldat(args.get("product_type", ""), args.get("site", ""))
            return f"Цена сохранена (ID: {pid})" if pid else "Цена не сохранена (confidence < 0.6)"

        elif name == "search_sites":
            pt = args.get("product_type", "")
            sites = mm.get_sites(pt)
            if not sites:
                return f"Нет сайтов для {pt}"
            return f"Сайты: {', '.join(s['id'] for s in sites[:10])}"

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


def _deprecate_site_approaches(memory_manager, product_type, domain, reason_prefix=""):
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
    try:
        semantic_cache.store(spec_text, result)
    except Exception as e:
        logger.warning("Semantic cache store failed: %s", e)


def _save_price_and_approach(memory_manager, spec_text, product_type, price_data, steps, record_soldat=False):
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
            concrete_steps=MemoryManager.clean_steps(steps) or [{"action": "search", "query": spec_text[:100]}],
            method="browser_search",
            search_query=spec_text[:200],
            notes=f"price {price_data['price']} in {len(steps)} steps",
            param_slots={"product_name": {"type": "string", "description": "название товара из спецификации"}},
        )
        if saved_id:
            memory_manager.record_success(saved_id)
            step_summary = " → ".join(
                f"{s.get('action','?')}[{s.get('target') or s.get('element') or s.get('ref','') or ''}]"
                for s in (MemoryManager.clean_steps(steps) or [])[:5]
            )
            logger.info("✅ Approach saved (ID=%d) for %s on %s: %.2f rub | steps: %s",
                        saved_id, product_type, price_data.get("site", ""), price_data['price'], step_summary)
    except Exception as e:
        logger.warning("Failed to save approach/success for %.2f price: %s", price_data['price'], e)


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


def _error_result(spec_text: str, error: str, elapsed: float | None = None) -> dict:
    logger.error("Row failed: %s -- %s", spec_text[:60], error)
    return {
        "spec_text": spec_text, "price": None, "confidence": 0.0,
        "reason": error, "requires_review": True, "error": error,
        "elapsed": elapsed,
    }


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
        )
        return model.model_dump()
    except Exception as e:
        logger.warning("Schema validation failed: %s", e)
        return result


CONTEXT_TOKEN_BUDGET = 8000


def _estimate_tokens(text: str) -> int:
    """Грубая оценка токенов (~4 символа на токен для кириллицы/ASCII)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _message_size(msg: dict) -> int:
    size = _estimate_tokens(str(msg.get("content") or ""))
    size += _estimate_tokens(str(msg.get("role") or ""))
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        size += _estimate_tokens(fn.get("name", "") + str(fn.get("arguments", "")))
    return size


def _trim_messages_for_budget(messages: list[dict], budget: int = CONTEXT_TOKEN_BUDGET) -> list[dict]:
    """Сжимает историю, если суммарный объём превышает бюджет.

    Сохраняет system, последнее user-сообщение и хвост диалога;
    выкидывает самые старые tool/assistant сообщения (они не входят в
    system+хвост, чтобы не порвать связку tool_call_id ↔ tool).
    """
    if not messages:
        return messages
    total = sum(_message_size(m) for m in messages)
    if total <= budget:
        return messages

    # Находим последнее user-сообщение — всё после него сохраняем целиком
    last_user_idx = -1
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            last_user_idx = i
    tail_start = last_user_idx if last_user_idx >= 0 else len(messages)

    tail = messages[tail_start:]
    tail_size = sum(_message_size(m) for m in tail)
    system = messages[:1] if messages and messages[0].get("role") == "system" else []

    # Пробегаем сообщения между system и хвостом, усекая до бюджета
    kept = []
    kept_size = tail_size
    for m in messages[len(system):tail_start]:
        size = _message_size(m)
        if kept_size + size <= budget:
            kept.append(m)
            kept_size += size
        else:
            break
    logger.info("Context trim: %d → %d tokens (kept %d of %d messages)",
                total, kept_size, len(system) + len(kept) + len(tail), len(messages))
    return system + kept + tail


async def _query_llm(llm_client, messages, tools, temperature: float | None = None, monitor_callback: Callable[[str, object], None] | None = None):
    """Обёртка над llm_client.chat с Circuit Breaker и температурой фазы.
    chat() возвращает {"error": ...} вместо исключения — состояние фиксируем вручную."""
    if not llm_circuit.allow_request():
        logger.error("LLM unavailable, pausing agent...")
        await asyncio.sleep(30)
        return {"error": "LLM circuit open"}
    messages = _trim_messages_for_budget(messages)
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
