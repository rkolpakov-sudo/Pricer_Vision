import asyncio
import logging
import threading

from PySide6.QtCore import QThread, Signal

from src.llm_client import LLMClient
from src.pdf_parser.mineru_backend import MinerUBackend
from src.pdf_parser.structurer import SpecStructurer
from src.pdf_parser.feedback import FeedbackCollector

logger = logging.getLogger("pricer.pdf.runner")


class PdfParserRunner(QThread):
    progress_signal = Signal(str, int, int)
    items_ready_signal = Signal(list)
    done_signal = Signal(bool, str)

    def __init__(self, pdf_path: str, llm_client: LLMClient,
                 config: dict | None = None, parent=None):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._llm_client = llm_client
        self._config = config or {}
        self._stop_event = threading.Event()

        pdf_cfg = self._config.get("pdf_parser", {})
        self._mineru_lang = pdf_cfg.get("lang", "east_slavic")
        self._mineru_method = pdf_cfg.get("method", "auto")
        self._mineru_timeout = int(pdf_cfg.get("timeout", 300))

    def stop(self):
        self._stop_event.set()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    async def _run(self):
        await self._llm_client.__aenter__()
        try:
            self.progress_signal.emit("Парсинг PDF через MinerU...", 0, 4)

            backend = MinerUBackend(lang=self._mineru_lang, method=self._mineru_method)
            raw_text = backend.parse(self._pdf_path, timeout=self._mineru_timeout)

            if self._stop_event.is_set():
                self.done_signal.emit(False, "Остановлено пользователем")
                return

            if not raw_text.strip():
                self.done_signal.emit(False, "PDF пуст — не удалось извлечь текст")
                return

            self.progress_signal.emit("Структурирование через LLM...", 1, 4)

            structurer = SpecStructurer(self._llm_client)
            items = await structurer.structure(raw_text)

            if self._stop_event.is_set():
                self.done_signal.emit(False, "Остановлено пользователем")
                return

            if not items:
                self.done_signal.emit(False, "Не удалось извлечь позиции из PDF")
                return

            self.progress_signal.emit("Применение прошлых исправлений...", 2, 4)

            feedback = FeedbackCollector()
            items = feedback.apply_corrections(items)

            self.items_ready_signal.emit(items)
            self.done_signal.emit(True, f"PDF обработан: {len(items)} позиций")

            self.progress_signal.emit("Готово", 4, 4)

        except FileNotFoundError as e:
            self.done_signal.emit(False, f"PDF не найден: {e}")
        except RuntimeError as e:
            self.done_signal.emit(False, f"MinerU error: {e}")
        except Exception as e:
            logger.exception("PDF parser failed")
            self.done_signal.emit(False, f"Ошибка: {e}")
        finally:
            await self._llm_client.__aexit__(None, None, None)
