import asyncio
import logging
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("pricer.pdf.mineru")

# MinerU живёт в том же venv, что и проект (Python 3.13 поддерживает оба).
# Запуск через python -c, а не через trampoline mineru.exe: uv-трамплин
# зашивает абсолютный путь окружения и ломается при переименовании venv.
MINERU_PYTHON = str(Path(__file__).parents[2] / "venv" / "Scripts" / "python.exe")
_MINERU_ENTRY = "from mineru.cli.client import main; main()"

# "Layout Predict:  66%|######5   | 38/58 [...]" — стадия и процент из stderr tqdm
_STAGE_RE = re.compile(r"([A-Za-z][A-Za-z0-9_\- ]{2,40}?):\s*(\d+)%")


def _kill_tree(proc):
    """Прибивает процесс и всё его дерево (Windows: taskkill /T /F).

    MinerU 3.4 запускает временный API-сервис и multiprocessing-воркеров —
    простой proc.kill() оставляет потомков, которые держат пайпы и вешают
    родителя навечно (известный Windows-deadlock subprocess.run с timeout).
    """
    if proc is None or proc.returncode is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=10,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


class MinerUBackend:
    def __init__(self, lang: str = "east_slavic", method: str = "auto"):
        self._lang = lang
        self._method = method

    def _build_cmd(self, pdf_path: str, output_dir: str) -> list[str]:
        return [
            MINERU_PYTHON,
            "-c", _MINERU_ENTRY,
            "-p", str(pdf_path),
            "-o", output_dir,
            "-b", "pipeline",
            "-m", self._method,
            "-l", self._lang,
        ]

    def _read_output(self, tmp_dir: str) -> str:
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

    def parse(self, pdf_path: str, timeout: int = 300) -> str:
        """Синхронная обёртка (для тестов/обратной совместимости).

        Продакшен-путь — parse_async (см. runner.py): здесь тот же
        process-tree kill при таймауте, но без стриминга прогресса.
        """
        pdf_path = str(pdf_path)
        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        with tempfile.TemporaryDirectory(prefix="mineru_") as tmp_dir:
            cmd = self._build_cmd(pdf_path, tmp_dir)
            logger.info(f"Running mineru: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                stdout, stderr = proc.communicate()
                raise TimeoutError(f"mineru timeout after {timeout}s")
            if proc.returncode != 0:
                stderr = stderr.strip() or "unknown error"
                raise RuntimeError(f"mineru failed (code {proc.returncode}): {stderr}")
            return self._read_output(tmp_dir)

    async def parse_async(self, pdf_path: str, timeout: int = 300,
                          progress_callback=None) -> str:
        """Асинхронный запуск MinerU.

        - Таймаут и отмена убивают ВСЁ дерево процессов (иначе потомки MinerU
          держат пайпы и subprocess-таймаут на Windows не срабатывает).
        - progress_callback(stage: str, percent: int) вызывается по прогрессу
          из stderr (Layout/MFR/Table-OCR и т.д.).
        """
        pdf_path = str(pdf_path)
        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        with tempfile.TemporaryDirectory(prefix="mineru_") as tmp_dir:
            cmd = self._build_cmd(pdf_path, tmp_dir)
            logger.info(f"Running mineru (async): {' '.join(cmd)}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                stdout = await asyncio.wait_for(
                    self._pump(proc, progress_callback),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                _kill_tree(proc)
                await asyncio.sleep(0.2)
                try:
                    await proc.wait()
                except Exception:
                    pass
                raise TimeoutError(f"mineru timeout after {timeout}s") from None
            except asyncio.CancelledError:
                _kill_tree(proc)
                await asyncio.sleep(0.2)
                try:
                    await proc.wait()
                except Exception:
                    pass
                raise
            if proc.returncode != 0:
                stderr = stdout.decode("utf-8", errors="replace").strip() or "unknown error"
                raise RuntimeError(f"mineru failed (code {proc.returncode}): {stderr}")
            return self._read_output(tmp_dir)

    async def _pump(self, proc, progress_callback) -> bytes:
        """Читает stdout и stderr параллельно (иначе переполнение пайпа вешает процесс).

        Возвращает stdout как bytes для сообщений об ошибке.
        """
        err_buf = b""

        async def _read_out() -> bytes:
            return await proc.stdout.read()

        async def _read_err():
            nonlocal err_buf
            while True:
                chunk = await proc.stderr.read(16384)
                if not chunk:
                    break
                err_buf = (err_buf + chunk)[-16384:]
                if progress_callback:
                    self._emit_progress(err_buf, progress_callback)

        out_task = asyncio.create_task(_read_out())
        err_task = asyncio.create_task(_read_err())
        await asyncio.gather(out_task, err_task)
        await proc.wait()
        return out_task.result()

    def _emit_progress(self, chunk: bytes, progress_callback):
        try:
            text = chunk.decode("utf-8", errors="replace")
        except Exception:
            return
        matches = _STAGE_RE.findall(text)
        if matches:
            stage, pct = matches[-1]
            progress_callback(stage.strip(), int(pct))
