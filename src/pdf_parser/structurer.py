import logging
import re

from src.llm_client import LLMClient

logger = logging.getLogger("pricer.pdf.structurer")

MAX_INPUT_CHARS = 24000


def _html_to_text(text: str) -> str:
    """Convert HTML table to pipe-delimited text for parsing."""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(?:td|th)>\s*<(?:td|th)[^>]*>', ' | ', text, flags=re.IGNORECASE)
    text = re.sub(r'<(?:td|th)[^>]*>(.*?)</(?:td|th)>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip()


class SpecStructurer:
    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def structure(self, raw_text: str) -> list[dict]:
        if not raw_text.strip():
            return []

        cleaned = _html_to_text(raw_text)
        if not cleaned:
            logger.warning("MinerU output was only HTML markup, nothing after stripping")
            return []

        max_chars = MAX_INPUT_CHARS
        if len(cleaned) > max_chars:
            logger.warning(f"Text truncated from {len(cleaned)} to {max_chars} chars")

        safe_text = cleaned[:max_chars]

        return self._fallback_parse(safe_text)

    def _fallback_parse(self, text: str) -> list[dict]:
        items = []
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("---"):
                continue

            if "|" in line:
                item = self._parse_pipe_line(line)
                if item:
                    item["pos"] = len(items) + 1
                    items.append(item)
                continue

            m = re.match(r"^(?:(\d+)[\.\)]\s*)?(.+?)(?:\s+[-–—]\s+|\s{2,})(\d+[\.\d]*)\s*(м|шт|кг|м2|м3|л|уп|компл|рул|шт\.|м\.)$", line, re.UNICODE)
            if m:
                items.append({
                    "pos": int(m.group(1)) if m.group(1) else len(items) + 1,
                    "name": m.group(2).strip(),
                    "specs": "",
                    "code": "",
                    "manufacturer": "",
                    "qty": self._to_float(m.group(3)),
                    "unit": m.group(4).replace(".", "").strip(),
                    "weight": 0,
                })
            else:
                m2 = re.match(r"^(?:(\d+)[\.\)]\s*)?(.+?)\s+(\d+[\.\d]*)\s*(м|шт|кг|м2|м3|л|уп|компл)", line, re.UNICODE)
                if m2:
                    items.append({
                        "pos": int(m2.group(1)) if m2.group(1) else len(items) + 1,
                        "name": m2.group(2).strip(),
                        "specs": "",
                        "code": "",
                        "manufacturer": "",
                        "qty": self._to_float(m2.group(3)),
                        "unit": m2.group(4).strip(),
                        "weight": 0,
                    })
        return items

    def _parse_pipe_line(self, line: str) -> dict | None:
        """Parse a pipe-delimited table row.

        Scans from right for qty/unit/weight numbers, then classifies
        left-side columns by content:
          [position] | name [| code] [| manufacturer] | unit | qty [| weight]
        """
        lowered = line.lower()
        header_kw = ("позиция", "наименование", "единица измерения", "количество",
                     "завод-изготовитель", "тип, марка", "код оборудования",
                     "масса единицы", "примечание", "оборудования и материалы")
        if any(kw in lowered for kw in header_kw):
            return None

        stripped = line.strip()
        if re.match(r'^[\d\s|]+$', stripped):
            return None
        if re.match(r'^[_\-.\s|]+$', stripped):
            return None
        if re.match(r'^[а-яА-Я]\s*\|', stripped):
            return None

        parts = [p.strip() for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) < 2:
            return None

        def _is_num(s: str) -> bool:
            return bool(re.match(r'^\d+[\.\d]*$', s.replace(",", ".")))

        # Scan from right for numerics (original robust approach)
        num_idx = [i for i in range(len(parts) - 1, -1, -1) if _is_num(parts[i])]

        qty = 0.0
        unit = ""
        weight = 0.0
        split_at = len(parts)

        if len(num_idx) >= 2:
            weight = self._to_float(parts[num_idx[0]])
            qty = self._to_float(parts[num_idx[1]])
            split_at = num_idx[1]
            if num_idx[1] > 0 and not _is_num(parts[num_idx[1] - 1]):
                unit = parts[num_idx[1] - 1]
                split_at = num_idx[1] - 1
        elif len(num_idx) == 1:
            qty = self._to_float(parts[num_idx[0]])
            split_at = num_idx[0]
            if num_idx[0] > 0 and not _is_num(parts[num_idx[0] - 1]):
                unit = parts[num_idx[0] - 1]
                split_at = num_idx[0] - 1

        # Classify left-side columns by content
        left = parts[:split_at]
        if not left:
            return None

        code = ""
        manufacturer = ""

        # 1: first field — position marker?
        if len(left) > 1:
            first = left[0]
            if (re.match(r'^\d{1,4}$', first)
                    or re.match(r'^(?=.*\d)[А-ЯA-Z0-9а-я][А-Яа-яA-Z0-9.,()/\-]{0,20}$', first)):
                left = left[1:]

        # 2: last field — manufacturer?
        if len(left) > 1:
            last = left[-1]
            # Normalise Latin lookalikes to Cyrillic before matching
            _norm = last.lower().replace('a', 'а').replace('e', 'е').replace('c', 'с').replace('o', 'о').replace('p', 'р').replace('x', 'х')
            if (re.search(r'(?:ооо|зао|ип|оао|ао|гк|ltd|inc|gmbh|corp)\b', _norm)
                    or re.search(r'аналог', _norm)
                    or re.search(r'["\']', last)):
                manufacturer = last
                left = left[:-1]

        # 3: new last field — article/code?
        if len(left) > 1:
            last = left[-1]
            if (re.match(r'^(?=.*\d)[А-ЯA-Z0-9][А-Яа-яA-Za-z0-9.,()/\-№\s]{0,50}$', last)
                    or re.match(r'^[A-Za-z][A-Za-z0-9/.\-]*(?:\s+[A-Za-z][A-Za-z0-9/.\-]*)*$', last)):
                code = last
                left = left[:-1]

        name = " | ".join(left) if left else ""
        if not name:
            return None

        return {
            "pos": 0,
            "name": name.strip(),
            "specs": "",
            "code": code or "",
            "manufacturer": manufacturer or "",
            "qty": qty,
            "unit": unit.replace("ШT", "шт").replace("ШТ", "шт").replace("ШT", "шт"),
            "weight": weight,
        }

    def _to_float(self, val) -> float:
        try:
            return float(str(val).replace(",", ".").replace(" ", ""))
        except (ValueError, TypeError):
            return 0.0
