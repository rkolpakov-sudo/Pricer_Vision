import asyncio
import logging
import threading

from PySide6.QtCore import QThread, Signal

from src.llm_client import LLMClient
from src.pdf_parser.mineru_backend import MinerUBackend
from src.pdf_parser.ocr_fallback import OCRFallback
from src.pdf_parser.review import SmartReview
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
        self._use_llm = bool(pdf_cfg.get("use_llm", False))
        self._llm_max_chars = int(pdf_cfg.get("llm_max_chars", 3000))
        self._llm_max_tokens = int(pdf_cfg.get("llm_max_tokens", 1024))
        self._llm_temperature = float(pdf_cfg.get("llm_temperature", 0.0))
        self._ocr_min_text_length = int(pdf_cfg.get("ocr_min_text_length", 100))
        self._review_threshold = float(pdf_cfg.get("review_threshold", SmartReview.CONFIDENCE_THRESHOLD))

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
            self.progress_signal.emit("Парсинг PDF через MinerU...", 0, 100)

            backend = MinerUBackend(lang=self._mineru_lang, method=self._mineru_method)
            raw_text = await self._run_parse(
                lambda: backend.parse_async(
                    self._pdf_path, timeout=self._mineru_timeout,
                    progress_callback=self._on_mineru_progress,
                )
            )
            if raw_text is None:
                self.done_signal.emit(False, "Остановлено пользователем")
                return

            ocr = OCRFallback(lang=self._mineru_lang, method=self._mineru_method)
            ocr.MIN_TEXT_LENGTH = self._ocr_min_text_length
            if ocr.needs_ocr(raw_text):
                self.progress_signal.emit("Текст короткий — повторный парсинг с OCR...", 1, 5)
                logger.info("Low text extraction, retrying via MinerU OCR")
                raw_text = await self._run_parse(
                    lambda: ocr.extract_with_ocr_async(
                        self._pdf_path, timeout=self._mineru_timeout,
                        progress_callback=self._on_mineru_progress,
                    )
                )
                if raw_text is None:
                    self.done_signal.emit(False, "Остановлено пользователем")
                    return

            if not raw_text.strip():
                self.done_signal.emit(False, "PDF пуст — не удалось извлечь текст")
                return

            self.progress_signal.emit("Структурирование...", 2, 5)

            structurer = SpecStructurer(
                self._llm_client,
                use_llm=self._use_llm,
                max_chars=self._llm_max_chars,
                max_tokens=self._llm_max_tokens,
                temperature=self._llm_temperature,
            )
            items = await structurer.structure(raw_text)

            if self._stop_event.is_set():
                self.done_signal.emit(False, "Остановлено пользователем")
                return

            if not items:
                self.done_signal.emit(False, "Не удалось извлечь позиции из PDF")
                return

            self.progress_signal.emit("Применение прошлых исправлений...", 3, 5)

            feedback = FeedbackCollector()
            items = feedback.apply_corrections(items)

            review = SmartReview(threshold=self._review_threshold)
            auto, needs = review.process_extraction(items)
            if needs:
                logger.info(f"SmartReview: {len(auto)} auto-approved, {len(needs)} need review")

            self.items_ready_signal.emit(items)
            self.done_signal.emit(True, f"PDF обработан: {len(items)} позиций")

            self.progress_signal.emit("Готово", 5, 5)

        except FileNotFoundError as e:
            self.done_signal.emit(False, f"PDF не найден: {e}")
        except RuntimeError as e:
            self.done_signal.emit(False, f"MinerU error: {e}")
        except Exception as e:
            logger.exception("PDF parser failed")
            self.done_signal.emit(False, f"Ошибка: {e}")
        finally:
            await self._llm_client.__aexit__(None, None, None)

    async def _run_parse(self, coro_factory):
        """Запускает MinerU-задачу, реагирует на Стоп (убивает дерево процессов)."""
        task = asyncio.create_task(coro_factory())
        while True:
            if self._stop_event.is_set():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
                return None
            done, _ = await asyncio.wait({task}, timeout=0.3)
            if done:
                return task.result()

    def _on_mineru_progress(self, stage: str, percent: int):
        self.progress_signal.emit(f"MinerU: {stage} {percent}%", percent, 100)
