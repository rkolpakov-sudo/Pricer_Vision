import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("pricer.pdf.mineru")

MINERU_CLI = str(Path(__file__).parents[2] / "mineru_venv" / "Scripts" / "mineru.exe")


class MinerUBackend:
    def __init__(self, lang: str = "east_slavic", method: str = "auto"):
        self._lang = lang
        self._method = method

    def parse(self, pdf_path: str, timeout: int = 300) -> str:
        self._timeout = timeout
        pdf_path = str(pdf_path)
        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        with tempfile.TemporaryDirectory(prefix="mineru_") as tmp_dir:
            cmd = [
                MINERU_CLI,
                "-p", pdf_path,
                "-o", tmp_dir,
                "-b", "pipeline",
                "-m", self._method,
                "-l", self._lang,
            ]
            logger.info(f"Running mineru: {' '.join(cmd)}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip() or "unknown error"
                raise RuntimeError(f"mineru failed (code {result.returncode}): {stderr}")

            output_files = list(Path(tmp_dir).rglob("*.md")) + list(Path(tmp_dir).rglob("*.md.txt"))
            if not output_files:
                output_files = list(Path(tmp_dir).rglob("*"))
                if not output_files:
                    logger.warning(f"mineru produced no output files in {tmp_dir}")
                    return ""
            md_file = output_files[0]
            text = md_file.read_text(encoding="utf-8", errors="replace")

            logger.info(f"mineru OK: {len(text)} chars from {md_file.name}")
            return text
