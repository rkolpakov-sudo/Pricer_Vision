"""QThread runner for pdf2spec v2 pipeline.

Signals are compatible with PdfParserRunner so main.py integration is minimal.
Supports OCR fallback via MinerU for scanned PDFs.
"""
import asyncio
import logging
import threading

from PySide6.QtCore import QThread, Signal

from src.pdf2spec.orchestrator import run_deterministic, run_full
from src.pdf2spec.extract import extract_records
from src.pdf2spec.export_xlsx import export_xlsx

logger = logging.getLogger("pricer.pdf2spec.runner")


def _needs_ocr(pdf_path: str) -> bool:
    """Check if PDF needs OCR (no tables found via PyMuPDF)."""
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        total_tables = 0
        for page in doc:
            tabs = page.find_tables()
            total_tables += len(tabs.tables)
            if total_tables > 0:
                doc.close()
                return False
        doc.close()
        return total_tables == 0
    except Exception:
        return False


class Pdf2SpecRunner(QThread):
    """QThread runner for the v2 PDF pipeline.

    Signals match PdfParserRunner for drop-in replacement:
      - progress_signal(str, int, int)
      - items_ready_signal(list)
      - done_signal(bool, str)
    """
    progress_signal = Signal(str, int, int)
    items_ready_signal = Signal(list)
    done_signal = Signal(bool, str)

    def __init__(
        self,
        pdf_path: str,
        llm_client=None,
        config: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._llm_client = llm_client
        self._config = config or {}
        self._stop_event = threading.Event()

        v2_cfg = self._config.get('pdf_parser', {}).get('v2', {})
        self._max_iterations = int(v2_cfg.get('max_iterations', 3))
        self._llm_review = bool(v2_cfg.get('llm_review', True))
        self._ocr_timeout = int(self._config.get('pdf_parser', {}).get('timeout', 900))

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            self.progress_signal.emit("Анализ PDF...", 0, 100)

            needs_ocr = _needs_ocr(self._pdf_path)
            if needs_ocr:
                self.progress_signal.emit("PDF без таблиц — запуск OCR (MinerU)...", 5, 100)
                result = self._run_with_ocr()
            elif self._llm_review and self._llm_client:
                self.progress_signal.emit("Извлечение таблиц из PDF...", 10, 100)
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(
                        run_full(
                            self._pdf_path,
                            llm_client=self._llm_client,
                            max_iterations=self._max_iterations,
                        )
                    )
                finally:
                    loop.close()
            else:
                self.progress_signal.emit("Извлечение таблиц из PDF...", 10, 100)
                result = run_deterministic(self._pdf_path)

            if self._stop_event.is_set():
                self.done_signal.emit(False, "Остановлено пользователем")
                return

            rows = result['rows']
            issues = result['issues']
            template = result.get('template', 'OV')

            if not rows:
                self.done_signal.emit(False, "Не удалось извлечь позиции из PDF")
                return

            self.progress_signal.emit("Сохранение XLSX...", 90, 100)

            from pathlib import Path
            out_dir = self._config.get('paths', {}).get('data_output', 'data/output')
            pdf_name = Path(self._pdf_path).stem
            output_path = Path(out_dir) / f"spec_{pdf_name}.xlsx"

            xlsx_path = export_xlsx(rows, output_path, template=template)

            items = []
            for r in rows:
                item = {
                    'pos': r.get('poz', ''),
                    'name': r.get('name', ''),
                    'specs': r.get('type', ''),
                    'article': r.get('code', ''),
                    'brand': r.get('supplier', ''),
                    'qty': r.get('qty', ''),
                    'unit': r.get('unit', ''),
                    'weight': r.get('mass', ''),
                    'note': r.get('note', ''),
                    'role': r.get('role', 'item'),
                    'confidence': 1.0 if r.get('role') == 'item' else 0.7,
                }
                items.append(item)

            self.items_ready_signal.emit(items)

            ocr_tag = " [OCR]" if needs_ocr else ""
            summary = (
                f"PDF v2{ocr_tag}: {len(rows)} строк "
                f"({issues.get('role_counts', {})}) "
                f"→ {xlsx_path}"
            )
            self.done_signal.emit(True, summary)

        except Exception as e:
            logger.exception("pdf2spec v2 failed")
            self.done_signal.emit(False, f"Ошибка v2: {e}")

    def _run_with_ocr(self) -> dict:
        """Run MinerU OCR then process the text through v2 pipeline."""
        try:
            from src.pdf_parser.mineru_backend import MinerUBackend

            pdf_cfg = self._config.get('pdf_parser', {})
            backend = MinerUBackend(
                lang=pdf_cfg.get('lang', 'east_slavic'),
                method=pdf_cfg.get('method', 'auto'),
            )

            raw_text = ""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                raw_text = loop.run_until_complete(
                    backend.parse_async(
                        self._pdf_path,
                        timeout=self._ocr_timeout,
                        progress_callback=lambda s, p: self.progress_signal.emit(
                            f"MinerU: {s} {p}%", 5 + p // 4, 30
                        ),
                    )
                )
            finally:
                loop.close()

            if not raw_text or len(raw_text.strip()) < 100:
                logger.warning("OCR produced insufficient text: %d chars", len(raw_text))
                return {
                    'rows': [], 'log': [], 'issues': {
                        'total_rows': 0, 'role_counts': {},
                        'orphans': [], 'items_no_qty': [], 'word_splits': [],
                    },
                    'report': [], 'template': None,
                }

            self.progress_signal.emit("Структурирование OCR-текста...", 30, 100)

            from src.pdf_parser.structurer import SpecStructurer
            structurer = SpecStructurer(
                self._llm_client,
                use_llm=self._llm_review,
                max_chars=8000,
                max_tokens=4096,
                temperature=0.1,
            )

            loop2 = asyncio.new_event_loop()
            asyncio.set_event_loop(loop2)
            try:
                items = loop2.run_until_complete(structurer.structure(raw_text))
            finally:
                loop2.close()

            rows = []
            for it in (items or []):
                rows.append({
                    'role': 'item',
                    'name': it.get('name', ''),
                    'type': it.get('specs', ''),
                    'code': it.get('article', ''),
                    'supplier': it.get('brand', ''),
                    'unit': it.get('unit', 'шт'),
                    'qty': str(it.get('qty', '')),
                    'mass': it.get('weight', ''),
                    'note': '',
                    'poz': str(it.get('pos', '')),
                    'page': 0,
                })

            from src.pdf2spec.qa import qa
            issues = qa(rows, [])

            return {
                'rows': rows,
                'log': [],
                'issues': issues,
                'report': [{'page': 0, 'status': 'OCR', 'rows': len(rows)}],
                'template': 'OV',
            }

        except Exception as e:
            logger.exception("OCR fallback failed")
            return {
                'rows': [], 'log': [], 'issues': {
                    'total_rows': 0, 'role_counts': {},
                    'orphans': [], 'items_no_qty': [], 'word_splits': [],
                },
                'report': [], 'template': None,
            }
