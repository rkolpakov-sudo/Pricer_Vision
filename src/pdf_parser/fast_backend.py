"""pdf-inspector wrapper: fast PDF classification and text extraction.

Qt-free. Guarded import: если пакета нет, бэкенд недоступен и роутер
уходит на MinerU. Единая точка маршрутизации fast/mineru — route_pdf().
"""
import logging

logger = logging.getLogger("pricer.pdf.fast")

try:
    import pdf_inspector as _pi
    AVAILABLE = True
except ImportError:  # pragma: no cover - зависит от окружения
    _pi = None
    AVAILABLE = False


class FastBackendError(RuntimeError):
    """Ошибка классификации/извлечения через pdf-inspector."""


class FastBackend:
    """Тонкая обёртка над pdf-inspector (~10–50 мс detect, ~200 мс extract)."""

    def available(self) -> bool:
        return AVAILABLE

    def classify(self, pdf_path: str) -> dict:
        """Быстрая классификация без полного парсинга документа."""
        if not AVAILABLE:
            raise FastBackendError("pdf-inspector не установлен")
        try:
            r = _pi.detect_pdf(str(pdf_path))
            return {
                "pdf_type": r.pdf_type,
                "confidence": float(r.confidence),
                "encoding_issues": bool(r.has_encoding_issues),
                "pages_needing_ocr": list(r.pages_needing_ocr),
                "page_count": int(r.page_count),
            }
        except Exception as e:  # noqa: BLE001
            raise FastBackendError(str(e)) from e

    def extract_markdown(self, pdf_path: str) -> str:
        """Полное извлечение в Markdown (только для text_based)."""
        if not AVAILABLE:
            raise FastBackendError("pdf-inspector не установлен")
        try:
            r = _pi.process_pdf(str(pdf_path))
            return r.markdown or ""
        except Exception as e:  # noqa: BLE001
            raise FastBackendError(str(e)) from e


def route_pdf(classification: dict, fast_enabled: bool = True,
              min_confidence: float = 0.8) -> str:
    """Маршрут обработки: 'fast' | 'mineru' (чистая функция, тестируется без Qt).

    - scanned / image_based / mixed → MinerU (OCR + layout-модели);
    - битые шрифты (has_encoding_issues) → MinerU (перерендер + OCR);
    - низкая уверенность классификации → MinerU;
    - пакет не установлен или fast_path отключён → MinerU.
    """
    if not fast_enabled or not AVAILABLE:
        return "mineru"
    if classification.get("pdf_type") != "text_based":
        return "mineru"
    if float(classification.get("confidence", 0.0)) < min_confidence:
        return "mineru"
    if classification.get("encoding_issues"):
        return "mineru"
    return "fast"