import asyncio
import logging
import threading
import time
from datetime import datetime
from PySide6.QtCore import QThread, Signal

from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.mcp_bridge import MCPBridge
from src.agent_loop import process_row
from src.audit_logger import AuditLogger
from src.task_scheduler import TaskScheduler
from src.semantic_cache import SemanticCache
from src.learning_loop import LearningLoop
from src.session_cache import NegativeCache, SiteBlacklist
from src.session_facts import SessionFacts

logger = logging.getLogger("pricer.runner")

DB_PATH = "data/pricer.db"


def _build_metrics(total: int, processed: int, found: int, llm_times: list,
                   cache_hits: int, stuck_events: int, blocks: int,
                   prompt_tokens: int = 0, completion_tokens: int = 0) -> dict:
    """Собирает dict метрик прогона (чистая функция — тестируется без Qt)."""
    llm_calls = len(llm_times)
    avg = (sum(llm_times) / llm_calls) if llm_calls else 0.0
    return {
        "total_products": total,
        "processed": processed,
        "found": found,
        "success_rate": (found / processed) if processed else 0.0,
        "llm_calls": llm_calls,
        "avg_llm_time": avg,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_hits": cache_hits,
        "stuck_events": stuck_events,
        "blocks": blocks,
    }


async def _run_row_with_idle_timeout(coro_factory, *, idle_timeout: float,
                                     max_seconds: float, activity: dict):
    """Запускает корутину строки с таймаутом ПО БЕЗДЕЙСТВИЮ.

    activity — изменяемый dict с ключом 'last' (monotonic-метка последнего
    признака жизни). Колбэки агента (status/monitor/site_visit) обновляют его.
    Строка отменяется, только если activity['last'] не менялся дольше
    idle_timeout — то есть агент завис, а не работает. Мягкий предел
    max_seconds — страховка от вечного цикла.
    """
    task = asyncio.create_task(coro_factory())
    start = time.monotonic()
    try:
        while True:
            try:
                done, _ = await asyncio.wait({task}, timeout=1.0)
            except asyncio.CancelledError:
                task.cancel()
                raise
            if done:
                return task.result()
            now = time.monotonic()
            last_activity = activity.get("last", start)
            if now - last_activity > idle_timeout:
                logger.warning("Row idle for %.0fs (idle timeout %.0fs) — cancelling",
                               now - last_activity, idle_timeout)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                raise asyncio.TimeoutError(f"idle {now - last_activity:.0f}s")
            if now - start > max_seconds:
                logger.warning("Row hard cap %.0fs reached — cancelling", max_seconds)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                raise asyncio.TimeoutError(f"max {now - start:.0f}s")
    except asyncio.TimeoutError:
        raise
    except Exception:
        task.cancel()
        raise


class MCPAgentRunner(QThread):
    status_signal = Signal(object)
    row_done_signal = Signal(int, object)
    done_signal = Signal(bool, object)
    error_signal = Signal(str)
    monitor_signal = Signal(object)
    metrics_signal = Signal(object)

    def __init__(self, specs: list, llm_client, db_path: str = DB_PATH, parent=None, fresh: bool = True,
                 skip_registry=None, use_approaches: bool = True, use_site_ranking: bool = True,
                 ductwork_enabled: bool = False, restored_caches: dict | None = None,
                 restored_results: list | None = None):
        super().__init__(parent)
        self.specs = specs
        self.llm_client = llm_client
        self.db_path = db_path
        self._fresh = fresh
        self._use_approaches = use_approaches
        self._use_site_ranking = use_site_ranking
        self._ductwork_enabled = ductwork_enabled
        self._skip_registry = skip_registry
        self._restored_caches = restored_caches
        self._restored_results = restored_results or []
        self._stop_event = threading.Event()
        self._restart_bridge = threading.Event()
        self._restart_bridge_value = None
        self._restart_bridge_backend_value = None
        self._llm_times = []
        self._cache_hits = 0
        self._stuck_events = 0
        self._blocks = 0
        self._processed = 0
        self._found = 0
        self.results = []

    def _current_metrics(self) -> dict:
        return _build_metrics(
            total=len(self.specs),
            processed=self._processed,
            found=self._found,
            llm_times=self._llm_times,
            cache_hits=self._cache_hits,
            stuck_events=self._stuck_events,
            blocks=self._blocks,
            prompt_tokens=getattr(self.llm_client, "prompt_tokens", 0),
            completion_tokens=getattr(self.llm_client, "completion_tokens", 0),
        )

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_async())
        finally:
            loop.close()

    async def _run_async(self):
        if not self.specs:
            logger.warning("No specs to process")
            self.done_signal.emit(True, {"total": 0, "positions": [], "found_count": 0, "review_count": 0, "error_count": 0})
            return
        self._llm_times.clear()
        self._cache_hits = 0
        self._stuck_events = 0
        self._blocks = 0
        self._processed = 0
        self._found = 0
        self._last_visited_site = ""
        reset_usage = getattr(self.llm_client, "reset_usage", None)
        if reset_usage:
            reset_usage()
        self.status_signal.emit("start")
        self.monitor_signal.emit({"type": "start", "total": len(self.specs)})
        self.status_signal.emit(("progress", 0, len(self.specs), "Загрузка графа..."))
        engine = GraphEngine(self.db_path)
        engine.build()
        mm = MemoryManager(engine)
        audit = AuditLogger()
        self.audit_session_id = audit.session_id
        semantic_cache = SemanticCache()
        learning_loop = LearningLoop(engine, mm)

        yaml_path = "config/categories_and_sites.yaml"
        from pathlib import Path

        if Path(yaml_path).exists():
            self.status_signal.emit(("progress", 0, len(self.specs), "Загрузка YAML seed..."))
            engine.load_yaml_seed(yaml_path)

        self.status_signal.emit(("progress", 0, len(self.specs), "Запуск MCP браузера..."))
        from src.config_loader import load_settings
        cfg = load_settings()
        headless = cfg.get("browser", {}).get("headless", True)
        bridge = MCPBridge(headless=headless)
        if not await bridge.start():
            self.error_signal.emit("MCP сервер не запущен (pricer-mcp-server)")
            return

        self.status_signal.emit(("progress", 0, len(self.specs), "Подключение к LLM..."))
        await self.llm_client.__aenter__()
        probe = await self.llm_client.chat([{"role": "user", "content": "ping"}])
        if "error" in probe:
            self.error_signal.emit(f"LLM недоступен: {probe['error']}")
            await self.llm_client.__aexit__(None, None, None)
            await bridge.stop()
            return
        try:
            self.results = []
            total = len(self.specs)
            scheduler = TaskScheduler(mm, site_profiles=learning_loop.site_profiles)
            # Рейтинг сайтов по профилю (тип, бренд) — вычисляется per-row по бренду товара.
            def _site_ranking_for(spec_item) -> dict:
                if not self._use_site_ranking:
                    return {}
                pt = engine.classify_product_type(spec_item.text)
                brand = getattr(spec_item, "brand", "") or ""
                site_ids = [s.get("id") for s in mm.get_sites(pt)]
                return learning_loop.rank_sites(pt, brand, site_ids)
            from src.config_loader import get_run_config
            # Построчная обработка по умолчанию (порядок файла). Группировка
            # по сайтам — опция group_by_site (переупорядочивает строки).
            if get_run_config("group_by_site", False):
                ordered = scheduler.ordered_specs(self.specs)
            else:
                ordered = list(self.specs)
            original_index = {id(spec): i for i, spec in enumerate(self.specs)}
            last_health_check = datetime.now()
            # Idle-таймаут строки: строка НЕ режется по «стенам», а отменяется только
            # если агент реально завис (ни один LLM-вызов/браузерное действие не
            # завершился за row_idle_timeout_seconds). Идёт продуктивная работа
            # (пусть даже 30-50с на шаг) — строка доходит до логического конца.
            row_idle_timeout = float(get_run_config("row_idle_timeout_seconds", 180))
            row_max_seconds = float(get_run_config("row_max_seconds", 900))
            # Сессионный отрицательный кэш «не найденных» товаров (только в памяти)
            negative_cache = NegativeCache()
            # Сессионный блэклист сайтов: сайт, где несколько строк подряд не нашли
            # товар (таймаут/force-switch/макс. раундов), исключается из поиска.
            site_blacklist = SiteBlacklist()
            # Межстрочные факты: сайт × (тип|бренд) статус + рабочие паттерны.
            session_facts = SessionFacts()
            # Восстановление кэшей из предыдущей сессии
            if self._restored_caches:
                negative_cache.from_dict(self._restored_caches.get("negative_cache", {}))
                site_blacklist.from_dict(self._restored_caches.get("site_blacklist", {}))
                session_facts.from_dict(self._restored_caches.get("session_facts", {}))
                logger.info("Restored session caches: %d negative, %d blacklist, %d facts",
                            len(negative_cache), len(site_blacklist),
                            len(session_facts._status) if hasattr(session_facts, '_status') else 0)

            def _on_site_visited(site_id: str):
                self._last_visited_site = site_id

            for i, spec in enumerate(ordered):
                if self._restart_bridge.is_set():
                    self._restart_bridge.clear()
                    new_headless = self._restart_bridge_value
                    new_backend = self._restart_bridge_backend_value
                    self._restart_bridge_value = None
                    self._restart_bridge_backend_value = None
                    if new_backend:
                        logger.info("Restarting bridge with backend=%s", new_backend)
                        try:
                            await asyncio.wait_for(bridge.set_backend(new_backend), timeout=20.0)
                        except Exception:
                            logger.warning("Bridge restart for backend toggle failed")
                    if new_headless is not None:
                        logger.info("Restarting bridge with headless=%s", new_headless)
                        try:
                            await asyncio.wait_for(bridge.set_headless(new_headless), timeout=20.0)
                        except Exception:
                            logger.warning("Bridge restart for headless toggle failed")
                if self._stop_event.is_set():
                    self.status_signal.emit("stop")
                    self.monitor_signal.emit({"type": "stop"})
                    break
                result = None
                retries = 0
                spec_text = spec.text if hasattr(spec, 'text') else str(spec)
                spec_brand = spec.brand if hasattr(spec, 'brand') else ""
                # Пользователь отметил позицию (или её полный аналог) в предпросмотре
                if self._skip_registry and self._skip_registry.is_skipped(spec_text, spec_brand):
                    matched = self._skip_registry.matches(spec_text, spec_brand) or spec_text
                    result = {"spec_text": spec_text, "price": None, "confidence": 0.0,
                              "reason": f"пропуск пользователем (аналог: {matched})",
                              "requires_review": True, "error": "skipped_by_user", "elapsed": 0.0}
                    logger.info("Row %d: user skip — '%s' (аналог '%s')", i + 1, spec_text[:40], matched[:40])
                    self.status_signal.emit(("progress", i + 1, total, f"Пропуск (пользователь): {spec_text[:60]}..."))
                    result["excel_row"] = getattr(spec, "row", 0) or (original_index.get(id(spec), i) + 2)
                    self.results.append(result)
                    audit.log_extraction(spec_text, False, None)
                    row_idx = original_index.get(id(spec), i)
                    self._processed += 1
                    self.metrics_signal.emit(self._current_metrics())
                    self.monitor_signal.emit({"type": "row_done", "idx": i + 1, "total": total})
                    self.row_done_signal.emit(row_idx, result)
                    continue
                # Товар уже найден в предыдущей сессии — восстанавливаем результат без поиска
                if self._restored_results:
                    for prev in self._restored_results:
                        if prev.get("spec_text") == spec_text and prev.get("price") is not None:
                            result = dict(prev)
                            result["excel_row"] = getattr(spec, "row", 0) or (original_index.get(id(spec), i) + 2)
                            result["restored"] = True
                            self.results.append(result)
                            row_idx = original_index.get(id(spec), i)
                            self._processed += 1
                            self._found += 1
                            self.metrics_signal.emit(self._current_metrics())
                            self.monitor_signal.emit({"type": "row_done", "idx": i + 1, "total": total})
                            self.row_done_signal.emit(row_idx, result)
                            logger.info("Row %d: restored from session — '%s' = %s",
                                        i + 1, spec_text[:40], prev.get("price"))
                            break
                    else:
                        result = None  # not found in restored — will process normally
                    if result is not None:
                        continue
                # Товар уже дважды не найден в этой сессии — пропускаем без поиска
                if negative_cache.is_blocked(spec_text):
                    result = {"spec_text": spec_text, "price": None, "confidence": 0.0,
                              "reason": "negative cache: не найдено ранее в сессии",
                              "requires_review": True, "error": "not_found_cached", "elapsed": 0.0}
                    logger.info("Row %d: negative cache — '%s' not found earlier, skipping", i + 1, spec_text[:40])
                    self.status_signal.emit(("progress", i + 1, total, f"Пропуск (не найдено ранее): {spec_text[:60]}..."))
                    result["excel_row"] = getattr(spec, "row", 0) or (original_index.get(id(spec), i) + 2)
                    self.results.append(result)
                    audit.log_extraction(spec_text, False, None)
                    row_idx = original_index.get(id(spec), i)
                    self._processed += 1
                    self.metrics_signal.emit(self._current_metrics())
                    self.monitor_signal.emit({"type": "row_done", "idx": i + 1, "total": total})
                    self.row_done_signal.emit(row_idx, result)
                    continue
                while retries < 3 and result is None:
                    if self._stop_event.is_set():
                        break
                    if (datetime.now() - last_health_check).total_seconds() > 60:
                        logger.info("Periodic health check...")
                        if not await bridge.health_check():
                            logger.warning("Bridge unhealthy, restarting...")
                            await bridge.restart()
                        last_health_check = datetime.now()
                    preview = spec.text[:60] if hasattr(spec, 'text') else str(spec)[:60]
                    self.status_signal.emit(("progress", i + 1, total, f"Обработка: {preview}..."))
                    self.monitor_signal.emit({"type": "row", "idx": i + 1, "total": total,
                                              "preview": preview, "position": spec_text})

                    # Признак жизни строки: сбрасывает idle-таймер (LLM/браузер/смена сайта)
                    row_activity = {"last": time.monotonic()}

                    def _touch():
                        row_activity["last"] = time.monotonic()

                    def _status(msg):
                        _touch()
                        self.status_signal.emit(("progress", i + 1, total, str(msg)[:120]))
                        self.monitor_signal.emit({"type": "action", "text": str(msg)[:240], "idx": i + 1, "total": total})

                    def _monitor(event_type, value):
                        _touch()
                        if event_type == "llm_call":
                            self._llm_times.append(float(value))
                        elif event_type == "cache_hit":
                            self._cache_hits += 1
                        elif event_type == "stuck":
                            self._stuck_events += 1
                        elif event_type == "block":
                            self._blocks += 1
                        self.metrics_signal.emit(self._current_metrics())

                    def _site_visited(site_id):
                        _touch()
                        _on_site_visited(site_id)

                    spec_meta = {"article": spec.article, "brand": spec.brand,
                                 "name_raw": spec.name_raw, "uom": spec.uom,
                                 "spec": getattr(spec, "spec", ""),
                                 "headers": spec.headers,
                                 "qty": getattr(spec, "qty", None)} if hasattr(spec, 'article') else None

                    try:
                        price_candidate_holder = {}
                        result = await _run_row_with_idle_timeout(
                            lambda: process_row(
                                spec_text=spec_text,
                                llm_client=self.llm_client,
                                mcp_bridge=bridge,
                                graph_engine=engine,
                                memory_manager=mm,
                                stop_event=self._stop_event,
                                status_callback=_status,
                                fresh=self._fresh,
                                use_approaches=self._use_approaches,
                                use_site_ranking=self._use_site_ranking,
                                site_ranking=_site_ranking_for(spec),
                                spec_meta=spec_meta,
                                semantic_cache=semantic_cache,
                                monitor_callback=_monitor,
                                negative_cache=negative_cache,
                                site_blacklist=site_blacklist,
                                site_visit_callback=_site_visited,
                                session_facts=session_facts,
                                ductwork_enabled=self._ductwork_enabled,
                                _price_candidate_holder=price_candidate_holder,
                            ),
                            idle_timeout=row_idle_timeout,
                            max_seconds=row_max_seconds,
                            activity=row_activity,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Row {i+1} timed out (idle/max)")
                        # Таймаут — сильный сигнал, что сайт «не подходит» для этого
                        # типа/бренда: штрафуем его в сессионном блэклисте, чтобы
                        # следующая строка не тратила раунды повторно.
                        if self._last_visited_site:
                            strikes = site_blacklist.strike(self._last_visited_site, reason="timeout")
                            logger.warning(
                                "Site %s struck %d/%d (timeout feedback)",
                                self._last_visited_site, strikes, site_blacklist.limit,
                            )
                        self._last_visited_site = ""
                        elapsed = row_max_seconds
                        if price_candidate_holder.get("price") is not None:
                            logger.info("Row %d: saving timeout candidate price=%s (conf=%.2f)",
                                        i+1, price_candidate_holder["price"],
                                        price_candidate_holder.get("confidence", 0))
                            result = {
                                "spec_text": spec_text,
                                "price": price_candidate_holder["price"],
                                "confidence": price_candidate_holder.get("confidence", 0.5),
                                "url": price_candidate_holder.get("url", ""),
                                "site": price_candidate_holder.get("site", ""),
                                "product_name": price_candidate_holder.get("product_name", ""),
                                "reason": f"Timeout {elapsed:.0f}s — saved best candidate",
                                "requires_review": True,
                                "error": "timeout_with_candidate",
                                "elapsed": elapsed,
                            }
                        else:
                            result = {"spec_text": spec_text, "price": None, "confidence": 0.0,
                                      "reason": f"Timeout after {elapsed:.0f}s", "requires_review": True,
                                      "error": "timeout", "elapsed": elapsed}
                        if not self._stop_event.is_set():
                            try:
                                await asyncio.wait_for(bridge.restart(), timeout=20.0)
                            except Exception:
                                logger.warning("Bridge restart after row timeout failed")
                    except asyncio.CancelledError:
                        logger.warning(f"Row {i+1} cancelled by user")
                        result = {"spec_text": spec_text, "price": None, "confidence": 0.0,
                                  "reason": "Cancelled by user", "requires_review": True, "error": "cancelled"}
                    except Exception as e:
                        # Любая ошибка агента не должна ронять весь прогон молча —
                        # строка помечается ошибкой и прогон продолжается.
                        logger.error(f"Row {i+1} agent error: {type(e).__name__}: {e}", exc_info=True)
                        result = {"spec_text": spec_text, "price": None, "confidence": 0.0,
                                  "reason": f"Agent error: {type(e).__name__}: {str(e)[:200]}",
                                  "requires_review": True, "error": "agent_error"}
                    if result.get("error") and "bridge" in str(result.get("error", "")).lower():
                        logger.warning(f"Bridge error on row {i+1}, restarting (retry {retries+1})...")
                        await bridge.restart()
                        result = None
                        retries += 1
                if result is None:
                    result = {"spec_text": str(spec), "price": None, "confidence": 0.0,
                              "reason": "Processing stopped", "requires_review": True, "error": "Stopped"}
                if self._stop_event.is_set():
                    self.monitor_signal.emit({"type": "stop"})
                    break
                # Сессионный отрицательный кэш: не найденный товар учитываем
                # (прерывания пользователем/cancelled/Stopped — не считаем)
                if result.get("price") is None and result.get("error") not in ("cancelled", "Stopped"):
                    negative_cache.record(spec_text)
                self.results.append(result)
                audit.log_extraction(spec_text, result.get("price") is not None, result.get("price"))
                row_idx = original_index.get(id(spec), i)
                result["excel_row"] = getattr(spec, "row", 0) or (row_idx + 2)
                if hasattr(spec, "brand"):
                    result["brand"] = spec.brand or ""
                self._processed += 1
                if result.get("price") is not None:
                    self._found += 1
                    # Сайт, где в прогоне найдена цена, не штрафуется блэклистом:
                    # иначе выбиваем единственный сайт с товаром (случай mircli 26.08).
                    site_for_success = result.get("site") or ""
                    if site_for_success:
                        site_blacklist.mark_success(site_for_success)
                self.metrics_signal.emit(self._current_metrics())
                self.monitor_signal.emit({"type": "row_done", "idx": i + 1, "total": total})
                self.row_done_signal.emit(row_idx, result)

            # Phase 4: Learning Loop — обновляем граф по итогам прогона
            try:
                learning_summary = learning_loop.consolidate_after_run(self.results)
                logger.info("Learning loop: %s", learning_summary)
            except Exception as e:
                logger.warning("Learning loop consolidation failed: %s", e)

            total = len(self.results)
            found = sum(1 for r in self.results if r.get("price") is not None)
            review = sum(1 for r in self.results if r.get("requires_review"))
            errs = sum(1 for r in self.results if r.get("error"))
            spec_result = {
                "total": total,
                "positions": self.results,
                "found_count": found,
                "review_count": review,
                "error_count": errs,
            }
            self.metrics_signal.emit(self._current_metrics())
            self.monitor_signal.emit({"type": "done", "total": total, "found": found, "errors": errs})
            self.done_signal.emit(True, spec_result)
        except Exception as e:
            logger.exception("Runner failed")
            self.error_signal.emit(str(e))
        finally:
            await bridge.stop()
            await self.llm_client.__aexit__(None, None, None)

    def trigger_bridge_restart(self, headless: bool):
        self._restart_bridge_value = headless
        self._restart_bridge.set()

    def trigger_bridge_backend_restart(self, backend: str):
        self._restart_bridge_backend_value = backend
        self._restart_bridge.set()

    def set_fresh(self, fresh: bool):
        """Update fresh flag live; applies from the next row."""
        self._fresh = fresh
        logger.info("Fresh flag updated to %s", fresh)

    def set_use_approaches(self, value: bool):
        """Update use_approaches live; applies from the next row."""
        self._use_approaches = value
        logger.info("Use approaches updated to %s", value)

    def set_use_site_ranking(self, value: bool):
        """Update use_site_ranking live; applies from the next row."""
        self._use_site_ranking = value
        logger.info("Use site ranking updated to %s", value)

    def set_ductwork_enabled(self, value: bool):
        """Update ductwork module flag live; applies from the next row."""
        self._ductwork_enabled = bool(value)
        logger.info("Ductwork module updated to %s", value)

    def stop(self):
        self._stop_event.set()
