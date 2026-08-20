import asyncio
import json
import logging
import os
import re
import threading
import urllib.parse
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from src.llm_client import LLMClient, FALLBACK_URLS
from src.tool_parser import parse_tool_calls, parse_final_response, parse_text_tools, parse_text_result
from src.mcp_bridge import MCPBridge
from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.config_loader import get_run_config, load_settings, get_antidetect_config, get_antidetect_site_overrides
from src.validator import validate_result
from src.rate_limiter import DomainRateLimiter
from src.agent_loop import _clean_snapshot, _portable_step_target, _is_hash_ref, SUMMARIZE_MAX_CHARS, SUMMARIZE_MAX_LINES

logger = logging.getLogger("pricer.study")

STUDY_PROMPT = """Ты — аналитик по настройке поиска цен на сайтах поставщиков.

Тебе дали URL товара на КОНКРЕТНОМ САЙТЕ. Твоя задача — создать ИНФРАСТРУКТУРУ, чтобы система находила ЛЮБОЙ товар этого типа на этом сайте.

ПЛАН РАБОТЫ:
1. Открой URL → найди цену → save_confirmed_price.
2. Изучи сайт: как работает поиск, где находится цена в карточке, как выглядит каталог.
3. Сохрани 3+ подхода через save_approach (с param_slots).
4. Сохрани 2+ хинта через save_hint.
5. Сохрани концепт (save_concept): тип товара SOLD_AT site.

ВАЖНО: НЕ переходи на другие сайты. Работай ТОЛЬКО с сайтом из URL."""

GRAPH_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "get_approaches",
            "description": "Получить подходы из графа. product_type + site — конкретный подход; только site — все подходы для сайта.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара"},
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
            "description": "Предложить подход. Будет показан пользователю на утверждение. ВАЖНО: подход должен работать для ЛЮБОГО товара этого типа. Используй param_slots для указания изменяемых частей (например, 'product_name' — название из спецификации). НЕ хардкодь артикул из URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string"},
                    "site": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "object"}},
                    "param_slots": {"type": "string", "description": "JSON: какие части шагов можно менять для других товаров. ОБЯЗАТЕЛЬНО: если используешь название товара — укажи 'product_name'. Пример: {\"product_name\": {\"type\": \"string\", \"description\": \"название товара из спецификации\"}}"},
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
            "name": "save_hint",
            "description": "Предложить подсказку. Будет показана пользователю на утверждение.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "Тип товара (или 'unknown' для всех)"},
                    "text": {"type": "string", "description": "Текст подсказки. Что работает/не работает на сайте, где искать."},
                    "priority": {"type": "number", "description": "Приоритет 0.0-1.0 (по умолч. 0.7)"}
                },
                "required": ["product_type", "text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_concept",
            "description": "Предложить концепт-связь. Будет показана пользователю на утверждение. Связывает тип товара с сайтом или два типа между собой. Пример: cables_telecom_cables IS_A site:example.com",
            "parameters": {
                "type": "object",
                "properties": {
                    "child": {"type": "string", "description": "Тип товара (должен существовать в графе)"},
                    "parent": {"type": "string", "description": "Что-то, с чем связываем. Если сайт — укажи site:домен (например site:example.com)"},
                    "relation": {"type": "string", "enum": ["IS_A", "EQUIVALENT_TO", "SOLD_AT"]},
                    "weight": {"type": "number", "description": "Вес 0.0-1.0"}
                },
                "required": ["child", "parent", "relation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_confirmed_prices",
            "description": "Похожие подтверждённые цены из графа.",
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
            "description": "Сохранить найденную цену в граф НЕМЕДЛЕННО.",
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
            "name": "ask_user",
            "description": "Задать вопрос пользователю. Ответ придёт в следующем шаге.",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"]
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
            "name": "get_hints",
            "description": "Получить подсказки для типа товара. Содержат общие правила работы на сайтах.",
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
            "description": "Предложить новый сайт. Будет показан пользователю на утверждение.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "name": {"type": "string"},
                    "product_type": {"type": "string"},
                    "approach_steps": {"type": "array", "items": {"type": "object"}}
                },
                "required": ["domain", "name", "product_type", "approach_steps"]
            }
        }
    },
]

GRAPH_TOOL_NAMES = {t["function"]["name"] for t in GRAPH_TOOL_DEFS}
MAX_STUDY_ROUNDS = get_run_config("max_study_rounds", 30)


def _normalize_site(domain: str) -> str:
    domain = domain.lower().strip()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


class StudyRunner(QThread):
    log_signal = Signal(str)
    question_signal = Signal(str)
    approaches_signal = Signal(dict)
    done_signal = Signal(bool, str)

    def __init__(self, url: str, spec_text: str, product_type: str,
                 llm_config: dict, failure_context: str = "",
                 db_path: str = "data/pricer.db", parent=None):
        super().__init__(parent)
        self._url = url
        self._spec = spec_text
        self._pt = product_type
        self._llm_config = llm_config
        self._failure_context = failure_context
        self._db_path = db_path
        self._stop_event = threading.Event()
        self._question_event = threading.Event()
        self._user_answer = ""
        self._proposed_approaches: list[dict] = []
        self._proposed_hints: list[dict] = []
        self._proposed_concepts: list[dict] = []
        self._proposed_sites: list[dict] = []
        self._log_buffer: list[str] = []

    def log(self, msg: str):
        logger.info(msg)
        self.log_signal.emit(msg)
        self._log_buffer.append(msg)

    def _save_log(self, site: str):
        try:
            logs_dir = "logs"
            os.makedirs(logs_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_pt = re.sub(r"[^\w_-]", "_", self._pt)
            safe_site = re.sub(r"[^\w_-]", "_", site)
            path = os.path.join(logs_dir, f"study_{ts}_{safe_pt}_{safe_site}.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"=== Study Log: {self._spec} ({self._pt}) @ {site} ===\n")
                f.write(f"URL: {self._url}\n\n")
                for msg in self._log_buffer:
                    f.write(msg + "\n")
                f.write(f"\n=== Approaches: {len(self._proposed_approaches)} ===\n")
                for a in self._proposed_approaches:
                    f.write(f"  - {a.get('site', '?')}: {a.get('method', '?')} ({a.get('notes', '')[:100]})\n")
                f.write(f"\n=== Hints: {len(self._proposed_hints)} ===\n")
                for h in self._proposed_hints:
                    f.write(f"  - {h.get('hint_text', '')[:100]}\n")
                f.write(f"\n=== Concepts: {len(self._proposed_concepts)} ===\n")
                for c in self._proposed_concepts:
                    f.write(f"  - {c.get('child', '?')} -[{c.get('relation', '?')}]-> {c.get('parent', '?')}\n")
                f.write(f"\n=== Sites: {len(self._proposed_sites)} ===\n")
                for s in self._proposed_sites:
                    f.write(f"  - {s.get('domain', '?')}: {s.get('name', '?')}\n")
            self.log(f"💾 Лог сохранён: {path}")
        except Exception as e:
            logger.warning("Failed to save study log: %s", e)

    def stop(self):
        self._stop_event.set()
        self._question_event.set()

    def answer_user(self, text: str):
        self._user_answer = text
        self._question_event.set()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    async def _run(self):
        self.log("🔧 Запуск обучения...")

        parsed = urllib.parse.urlparse(self._url)
        if not parsed.netloc:
            self.done_signal.emit(False, f"Некорректный URL: {self._url}")
            return
        site = _normalize_site(parsed.netloc)

        engine = GraphEngine(self._db_path)
        engine.build()
        mm = MemoryManager(engine)

        lm = self._llm_config
        llm = LLMClient(
            url=lm.get("url", "http://localhost:1234/v1/chat/completions"),
            model=lm.get("model", ""),
            temperature=get_run_config("study_temperature", 0.5),
            timeout=int(lm.get("timeout", 120)),
        )
        llm.set_fallbacks(FALLBACK_URLS)

        cfg = load_settings()
        headless = cfg.get("browser", {}).get("headless", True)
        bridge = MCPBridge(headless=headless)
        self.log("🔄 Запуск MCP браузера...")
        if not await bridge.start():
            self.done_signal.emit(False, "MCP сервер не запущен")
            return

        self.rate_limiter = DomainRateLimiter(
            min_interval=get_antidetect_config("rate_limit_min_interval", 1.5),
            max_requests_per_minute=get_antidetect_config("rate_limit_max_requests_per_minute", 20),
            jitter=get_antidetect_config("jitter", 1.0),
            cooldown_seconds=get_antidetect_config("cooldown_seconds", 300),
            site_overrides=get_antidetect_site_overrides(),
        )

        self.log("🔄 Подключение к LLM...")
        await llm.__aenter__()
        # verify LLM is available before starting
        probe = await llm.chat([{"role": "user", "content": "ping"}])
        if "error" in probe:
            self.log(f"❌ LLM не отвечает: {probe['error']}")
            self.done_signal.emit(False, f"LLM недоступен: {probe['error']}")
            await llm.__aexit__(None, None, None)
            await bridge.stop()
            return
        try:
            await self._study_loop(llm, bridge, engine, mm, site)
        except asyncio.CancelledError:
            self.done_signal.emit(False, "Остановлено пользователем")
            return
        except Exception as e:
            logger.exception("Study failed")
            self.done_signal.emit(False, str(e))
            return
        finally:
            await bridge.stop()
            await llm.__aexit__(None, None, None)

        self._save_log(site)
        self.approaches_signal.emit({
            "approaches": self._proposed_approaches,
            "hints": self._proposed_hints,
            "concepts": self._proposed_concepts,
            "sites": self._proposed_sites,
        })
        count = len(self._proposed_approaches)
        self.done_signal.emit(True, f"Обучение завершено. Подходов: {count}")

    async def _study_loop(self, llm, bridge, engine, mm, site):
        self.log(f"📋 URL: {self._url}")
        self.log(f"📋 Сайт: {site}")
        self.log(f"📋 Товар: {self._spec}")
        self.log(f"📋 Тип: {self._pt}")

        context_parts = [
            f"URL: {self._url}",
            f"Товар: {self._spec}",
            f"Тип товара: {self._pt}",
            f"Сайт: {site}",
        ]

        if self._failure_context:
            context_parts.append(f"\nОшибка из основного поиска:\n{self._failure_context[:500]}")

        existing = [a for a in (mm.get_all_approaches(self._pt) if self._pt != "unknown" else []) if a.get("site_id", "") == site]
        if existing:
            context_parts.append(f"\nСУЩЕСТВУЮЩИЕ ПОДХОДЫ для {self._pt} на {site} ({len(existing)}):")
            for a in existing:
                sid = a.get("site_id", "?")
                sc = a.get("success_count", 0)
                fail = a.get("consecutive_failures", 0)
                ls = (a.get("last_success_date") or "")[:10]
                method = a.get("method", "?")
                concrete = a.get("concrete", [])
                steps_detail = []
                for s in concrete[:8]:
                    action = s.get("action", "?")
                    target = _portable_step_target(s)
                    txt = s.get("text", "")
                    if target:
                        action += f"[{target}]"
                    if txt and len(txt) < 60:
                        action += f"='{txt}'"
                    key = s.get("key", "")
                    if key:
                        action += f"({key})"
                    steps_detail.append(action)
                steps_str = " → ".join(steps_detail)
                context_parts.append(f"  [ID {a.get('id', '?')}] метод={method}, успехов={sc}, неудач={fail}, последний={ls}")
                context_parts.append(f"    шаги: {steps_str}")
        else:
            context_parts.append(f"\n⚠️ НЕТ подходов для {self._pt} на {site} — коренная причина.")

        # Site guides — approaches from THIS site for OTHER product types
        all_flat = mm.get_all_approaches_flat()
        site_guides = [a for a in all_flat if a.get("site_id", "") == site and a.get("product_type_id", "") != self._pt]
        if site_guides:
            context_parts.append(f"\nПодходы для {site} (другие товары):")
            for a in site_guides[:3]:
                steps = " → ".join(s.get("action", "?") for s in a.get("concrete", [])[:4])
                context_parts.append(f"  [{a.get('product_type_id', '?')}] {steps}")

        hints = mm.get_hints(self._pt) + mm.get_hints("unknown")
        if hints:
            context_parts.append(f"\nХинты ({len(hints)}):")
            for h in hints[:3]:
                context_parts.append(f"  • {h.get('hint_text', '')}")

        context = "\n".join(context_parts)
        self.log("📤 Отправка задачи LLM...")

        mcp_tools = await bridge.list_tools()
        all_tools = mcp_tools + GRAPH_TOOL_DEFS
        self.log(f"🔧 Инструментов: MCP={len(mcp_tools)}, граф={len(GRAPH_TOOL_DEFS)}")

        # Track actual browser actions during study
        study_steps: list[dict] = []

        messages = [
            {"role": "system", "content": STUDY_PROMPT},
            {"role": "user", "content": context},
        ]

        for round_num in range(MAX_STUDY_ROUNDS):
            if self._stop_event.is_set():
                return

            response = await llm.chat(messages, all_tools)
            if "error" in response:
                self.log(f"❌ LLM ошибка: {response['error']}")
                break

            content = (response.get("choices") or [{}])[0].get("message", {}).get("content", "")
            tool_calls = parse_tool_calls(response)

            if not tool_calls and content:
                tool_calls = parse_text_tools(content)

            if content:
                line = content.strip().split("\n")[0][:200]
                self.log(f"🤔 {line}")

            final_attempt = parse_final_response(response)
            if not final_attempt.get("price") and content:
                text_result = parse_text_result(content)
                if text_result and text_result.get("price") is not None:
                    final_attempt = text_result

            if final_attempt.get("price") is not None:
                validated = validate_result(final_attempt, self._spec)
                if validated.get("price"):
                    self.log(f"💰 Цена: {validated['price']} (conf: {validated.get('confidence', 0):.0%})")

            if not tool_calls:
                if content and "заверш" in content.lower():
                    if len(self._proposed_approaches) >= 3 and len(self._proposed_hints) >= 2:
                        break
                    if len(self._proposed_approaches) < 3:
                        force_msg = f"Нужно МИНИМУМ 3 подхода. Сейчас {len(self._proposed_approaches)}. Создай через save_approach с param_slots."
                    elif len(self._proposed_hints) < 2:
                        force_msg = f"Нужно МИНИМУМ 2 хинта. Сейчас {len(self._proposed_hints)}. Создай через save_hint — опиши КАК искать цену на этом сайте (селекторы, метод поиска)."
                    else:
                        force_msg = "Продолжай анализ. Обогащай граф: подходы, хинты, концепты."
                    self.log(f"⚠️ {force_msg}")
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append({"role": "user", "content": force_msg})
                    continue
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "user", "content": "Продолжай анализ. Обогащай граф: подходы, хинты, концепты."})
                continue

            msg = (response.get("choices") or [{}])[0].get("message", {})
            messages.append(msg)

            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("arguments", {})

                if tool_name == "ask_user":
                    question = tool_args.get("question", "")
                    self.log(f"❓ {question}")
                    self._question_event.clear()
                    self._user_answer = ""
                    self.question_signal.emit(question)
                    self._question_event.wait()
                    if self._stop_event.is_set():
                        return
                    answer = self._user_answer
                    self.log(f"💬 Ответ: {answer}")
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": answer})
                    continue

                if tool_name in GRAPH_TOOL_NAMES:
                    result = await self._exec_graph_tool(tool_name, tool_args, engine, mm, site, study_steps)
                else:
                    if self.rate_limiter is not None:
                        await self.rate_limiter.wait_if_needed(self._url)
                    result = await bridge.call_tool(tool_name, tool_args)
                    # Record actual browser action (only if successful)
                    if not str(result).startswith("error:"):
                        step = {"action": tool_name}
                        if tool_name == "browser_navigate":
                            step["url"] = tool_args.get("url", "")
                        elif tool_name in ("browser_type", "type_text"):
                            step["text"] = tool_args.get("text", "")
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
                        study_steps.append(step)

                if tool_name in ("browser_snapshot", "snapshot"):
                    result = _clean_snapshot(str(result))

                result_text = str(result)[:200]
                if tool_name in ("save_hint", "save_confirmed_price", "save_discovered_site"):
                    self.log(f"💾 {tool_name} → {result_text}")
                else:
                    self.log(f"  {tool_name} → {result_text}")

                result_str = str(result)
                # Aggressively truncate HTML results
                if result_str.strip().startswith("<") or result_str.strip().startswith('"<'):
                    result_str = result_str[:1500]
                # Strip markdown code blocks and artifacts from Playwright MCP output
                if result_str.startswith("### Ran Playwright code"):
                    parts = result_str.split("###")
                    result_str = "###".join(parts[1:]) if len(parts) > 1 else result_str
                result_str = re.sub(r'```\w*\n?', '', result_str)
                # Summarize large results to save context
                if tool_name in ("browser_snapshot", "snapshot", "browser_evaluate", "extract_text"):
                    lines = result_str.split("\n")
                    short = [l for l in lines if len(l.strip()) > 0 and not l.strip().startswith("<!")]
                    result_str = "\n".join(short[:SUMMARIZE_MAX_LINES])[:SUMMARIZE_MAX_CHARS]
                elif len(result_str) > SUMMARIZE_MAX_CHARS:
                    result_str = result_str[:SUMMARIZE_MAX_CHARS]
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result_str})

        self.log(f"⏱ Анализ завершён. Подходов: {len(self._proposed_approaches)}")

    def _proposal_key(self, proposal: dict) -> str:
        site = proposal.get("site", "")
        pt = proposal.get("product_type", "")
        steps = proposal.get("concrete_steps", [])
        first_actions = [s.get("action", "") for s in steps[:3] if s.get("action")]
        return f"{site}|{pt}|{','.join(first_actions)}"

    async def _exec_graph_tool(self, name: str, args: dict, engine, mm, site_fallback: str, study_steps: list[dict] | None = None) -> str:
        try:
            if "site" in args and args["site"]:
                args["site"] = _normalize_site(args["site"])

            if name == "get_approaches":
                pt = args.get("product_type", self._pt)
                site = args.get("site")
                if pt and site:
                    approaches = mm.get_site_approaches(pt, site)
                elif site:
                    approaches = mm.get_approaches_by_site(site)
                elif pt:
                    approaches = mm.get_all_approaches(pt)
                else:
                    approaches = mm.get_all_approaches_flat()
                if not approaches:
                    return "Нет сохранённых подходов"
                lines = [f"Подходов: {len(approaches)}"]
                for a in approaches[:5]:
                    steps = " → ".join(s.get("action", "?") for s in a.get("concrete", [])[:4])
                    lines.append(f"  {a.get('site_id', '?')} [{a.get('product_type_id', '?')}]: {steps}")
                return "\n".join(lines)

            elif name == "save_approach":
                # Use actual browser actions instead of LLM-proposed steps
                actual_steps = MemoryManager.clean_steps(study_steps) if study_steps else []
                cleaned = actual_steps or MemoryManager.clean_steps(args.get("steps", []))
                proposal = {
                    "product_type": args.get("product_type", self._pt),
                    "site": args.get("site", site_fallback),
                    "concrete_steps": cleaned or [{"action": "navigate", "url": self._url}],
                    "method": args.get("method", "study"),
                    "search_query": args.get("search_query", self._spec[:200]),
                    "notes": args.get("notes", f"study: {self._spec[:60]}"),
                    "selectors_cache": json.loads(args.get("selectors", "{}")) if args.get("selectors") else None,
                    "param_slots": json.loads(args.get("param_slots", "{}")) if args.get("param_slots") else None,
                }
                key = self._proposal_key(proposal)
                if any(self._proposal_key(p) == key for p in self._proposed_approaches):
                    return "Дубликат — пропущен"
                # Skip garbage: only navigate/snapshot, no meaningful actions
                meaningful = [s for s in cleaned if s.get("action") not in ("browser_navigate", "browser_snapshot", "snapshot", "browser_take_screenshot")]
                if not meaningful:
                    return "Подход не содержит полезных действий (только навигация/снимки) — пропущен"
                self._proposed_approaches.append(proposal)
                return f"Подход предложен (требует утверждения) — всего: {len(self._proposed_approaches)}"

            elif name == "save_hint":
                pt = args.get("product_type", self._pt)
                text = args.get("text", "")
                priority = args.get("priority", 0.7)
                if text:
                    self._proposed_hints.append({
                        "product_type": pt, "hint_text": text,
                        "priority": priority,
                    })
                    return f"Хинт предложен на утверждение — всего: {len(self._proposed_hints)}"
                return "Нет текста хинта"

            elif name == "save_concept":
                child = args.get("child", "")
                parent = args.get("parent", "")
                relation = args.get("relation", "IS_A")
                weight = args.get("weight", 1.0)
                if child and parent:
                    self._proposed_concepts.append({
                        "child": child, "parent": parent,
                        "relation": relation, "weight": weight,
                    })
                    return f"Концепт предложен на утверждение: {child} {relation} {parent}"
                return "Укажи child и parent"

            elif name == "get_confirmed_prices":
                prices = mm.get_relevant_prices(args.get("spec_text", self._spec))
                if not prices:
                    return "Нет похожих цен"
                lines = [f"Похожих цен: {len(prices)}"]
                for p in prices[:5]:
                    pt = (p.get("spec_text") or "")[:60]
                    lines.append(f"  {pt} -> {p.get('price', '?')} rub (conf: {p.get('confidence', 0):.0%})")
                return "\n".join(lines)

            elif name == "save_confirmed_price":
                pid = mm.save_price(
                    spec_text=args.get("spec_text", self._spec),
                    product_type=args.get("product_type", self._pt),
                    site=args.get("site", site_fallback),
                    price=args.get("price", 0),
                    url=args.get("url", self._url),
                    confidence=args.get("confidence", 0.95),
                    reason=args.get("reason", "study"),
                )
                if pid:
                    mm.record_soldat(args.get("product_type", self._pt), args.get("site", site_fallback))
                return f"Цена сохранена (ID: {pid})" if pid else "Цена не сохранена"

            elif name == "search_sites":
                sites = mm.get_sites(args.get("product_type", self._pt))
                return f"Сайты: {', '.join(s['id'] for s in sites[:10])}" if sites else "Нет сайтов"

            elif name == "save_discovered_site":
                domain = _normalize_site(args.get("domain", ""))
                all_sites = mm.get_all_sites()
                if domain in all_sites:
                    return f"❌ Сайт {domain} УЖЕ есть в графе. Не нужно добавлять — создай подход через save_approach."
                self._proposed_sites.append({
                    "domain": domain,
                    "name": args.get("name", domain),
                    "product_type": self._pt,
                })
                return f"Новый сайт предложен на утверждение: {domain}"

            elif name == "get_hints":
                pt = args.get("product_type", self._pt)
                hints = mm.get_hints(pt) + mm.get_hints("unknown")
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
