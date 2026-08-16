import asyncio
import logging
import threading
from datetime import datetime, timedelta
from PySide6.QtCore import QThread, Signal

from src.graph_engine import GraphEngine
from src.memory_manager import MemoryManager
from src.llm_client import LLMClient
from src.mcp_bridge import MCPBridge
from src.agent_loop import process_row
from src.audit_logger import AuditLogger
from src.task_scheduler import TaskScheduler
from src.semantic_cache import SemanticCache
from src.learning_loop import LearningLoop

logger = logging.getLogger("pricer.runner")

DB_PATH = "data/pricer.db"


def _build_metrics(total: int, processed: int, found: int, llm_times: list,
                   cache_hits: int, stuck_events: int, blocks: int) -> dict:
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
        "cache_hits": cache_hits,
        "stuck_events": stuck_events,
        "blocks": blocks,
    }


class MCPAgentRunner(QThread):
    status_signal = Signal(object)
    row_done_signal = Signal(int, object)
    done_signal = Signal(bool, object)
    error_signal = Signal(str)
    monitor_signal = Signal(object)
    metrics_signal = Signal(object)

    def __init__(self, specs: list, llm_client, db_path: str = DB_PATH, parent=None, fresh: bool = True):
        super().__init__(parent)
        self.specs = specs
        self.llm_client = llm_client
        self.db_path = db_path
        self._fresh = fresh
        self._stop_event = threading.Event()
        self._restart_bridge = threading.Event()
        self._restart_bridge_value = None
        self._llm_times = []
        self._cache_hits = 0
        self._stuck_events = 0
        self._blocks = 0
        self._processed = 0
        self._found = 0

    def _current_metrics(self) -> dict:
        return _build_metrics(
            total=len(self.specs),
            processed=self._processed,
            found=self._found,
            llm_times=self._llm_times,
            cache_hits=self._cache_hits,
            stuck_events=self._stuck_events,
            blocks=self._blocks,
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
        self.status_signal.emit("start")
        self.monitor_signal.emit({"type": "start", "total": len(self.specs)})
        self.status_signal.emit(("progress", 0, len(self.specs), "Загрузка графа..."))
        engine = GraphEngine(self.db_path)
        engine.build()
        mm = MemoryManager(engine)
        audit = AuditLogger()
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
            results = []
            total = len(self.specs)
            scheduler = TaskScheduler(mm, site_profiles=learning_loop.site_profiles)
            from src.config_loader import get_run_config
            # Построчная обработка по умолчанию (порядок файла). Группировка
            # по сайтам — опция group_by_site (переупорядочивает строки).
            if get_run_config("group_by_site", False):
                ordered = scheduler.ordered_specs(self.specs)
            else:
                ordered = list(self.specs)
            original_index = {id(spec): i for i, spec in enumerate(self.specs)}
            last_health_check = datetime.now()
            for i, spec in enumerate(ordered):
                if self._stop_event.is_set():
                    self.status_signal.emit("stop")
                    self.monitor_signal.emit({"type": "stop"})
                    break
                result = None
                retries = 0
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
                    self.monitor_signal.emit({"type": "row", "idx": i + 1, "total": total, "preview": preview})
                    spec_text = spec.text if hasattr(spec, 'text') else str(spec)

                    def _status(msg):
                        self.status_signal.emit(("progress", i + 1, total, str(msg)[:120]))
                        self.monitor_signal.emit({"type": "action", "text": str(msg)[:240], "idx": i + 1, "total": total})

                    def _monitor(event_type, value):
                        if event_type == "llm_call":
                            self._llm_times.append(float(value))
                        elif event_type == "cache_hit":
                            self._cache_hits += 1
                        elif event_type == "stuck":
                            self._stuck_events += 1
                        elif event_type == "block":
                            self._blocks += 1
                        self.metrics_signal.emit(self._current_metrics())

                    spec_meta = {"article": spec.article, "brand": spec.brand,
                                 "name_raw": spec.name_raw, "uom": spec.uom,
                                 "spec": getattr(spec, "spec", ""),
                                 "headers": spec.headers} if hasattr(spec, 'article') else None

                    try:
                        result = await asyncio.wait_for(
                            process_row(
                                spec_text=spec_text,
                                llm_client=self.llm_client,
                                mcp_bridge=bridge,
                                graph_engine=engine,
                                memory_manager=mm,
                                stop_event=self._stop_event,
                                status_callback=_status,
                                fresh=self._fresh,
                                spec_meta=spec_meta,
                                semantic_cache=semantic_cache,
                                monitor_callback=_monitor,
                            ),
                            timeout=300.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Row {i+1} timed out after 300s")
                        result = {"spec_text": spec_text, "price": None, "confidence": 0.0,
                                  "reason": "Timeout after 300s", "requires_review": True, "error": "timeout",
                                  "elapsed": 300.0}
                    except asyncio.CancelledError:
                        logger.warning(f"Row {i+1} cancelled by user")
                        result = {"spec_text": spec_text, "price": None, "confidence": 0.0,
                                  "reason": "Cancelled by user", "requires_review": True, "error": "cancelled"}
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
                results.append(result)
                audit.log_extraction(spec_text, result.get("price") is not None, result.get("price"))
                row_idx = original_index.get(id(spec), i)
                self._processed += 1
                if result.get("price") is not None:
                    self._found += 1
                self.metrics_signal.emit(self._current_metrics())
                self.monitor_signal.emit({"type": "row_done", "idx": i + 1, "total": total})
                self.row_done_signal.emit(row_idx, result)

            if self._restart_bridge.is_set():
                new_headless = self._restart_bridge_value
                self._restart_bridge.clear()
                self._restart_bridge_value = None
                logger.info("Restarting bridge with headless=%s due to toggle", new_headless)
                await bridge.set_headless(new_headless)

            # Phase 4: Learning Loop — обновляем граф по итогам прогона
            try:
                learning_summary = learning_loop.consolidate_after_run(results)
                logger.info("Learning loop: %s", learning_summary)
            except Exception as e:
                logger.warning("Learning loop consolidation failed: %s", e)

            total = len(results)
            found = sum(1 for r in results if r.get("price") is not None)
            review = sum(1 for r in results if r.get("requires_review"))
            errs = sum(1 for r in results if r.get("error"))
            spec_result = {
                "total": total,
                "positions": results,
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

    def set_fresh(self, fresh: bool):
        """Update fresh flag live; applies from the next row."""
        self._fresh = fresh
        logger.info("Fresh flag updated to %s", fresh)

    def stop(self):
        self._stop_event.set()
