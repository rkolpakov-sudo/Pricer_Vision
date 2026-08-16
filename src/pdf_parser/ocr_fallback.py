import asyncio
import logging

from src.pdf_parser.mineru_backend import MinerUBackend

logger = logging.getLogger("pricer.pdf.ocr")


class OCRFallback:
    """OCR for scanned PDFs without a text layer.

    The real backend is MinerUBackend (mineru_venv), NOT PaddleOCR/Tesseract —
    no new dependencies are required. MinerU already handles scans (built-in OCR).
    """

    MIN_TEXT_LENGTH = 100

    def __init__(self, mineru_backend: MinerUBackend | None = None,
                 lang: str = "east_slavic", method: str = "auto"):
        self.mineru_backend = mineru_backend or MinerUBackend(lang=lang, method=method)
        self.ocr_engine = None

    def needs_ocr(self, extracted_text: str) -> bool:
        """Decide whether a PDF needs OCR because too little text was extracted."""
        return len((extracted_text or "").strip()) < self.MIN_TEXT_LENGTH

    async def extract_with_ocr(self, pdf_path: str, timeout: int = 300) -> str:
        """Extract text via MinerU (handles scans and text PDFs) without blocking."""
        if not self.mineru_backend:
            return ""
        return await asyncio.to_thread(self.mineru_backend.parse, pdf_path, timeout)
