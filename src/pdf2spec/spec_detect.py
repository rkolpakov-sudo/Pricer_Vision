"""Detect specification tables within a project PDF.

Maps 9 logical columns (poz/name/type/code/supplier/unit/qty/mass/note) by header text,
rejects non-spec tables (title blocks, room schedules, node explanations) by score.
"""
import re

SPEC_MIN_COLS = 6

FRAME_RE = re.compile(
    r'(Лист\s*\d|Лист\s+08|Изм\.|Кол\.уч\.|Подп\.|Дата|ГИП|ГАП|Разраб\.|Статус|Формат)'
)


def _norm_cell(c) -> str:
    c = (c or '').replace('\n', ' ')
    c = re.sub(r'-\s+', '', c)
    return re.sub(r'\s+', ' ', c).strip()


def map_columns(header_row) -> dict:
    """Map 9 logical columns by header text (not fixed index)."""
    def find(*prefixes):
        for pref in prefixes:
            np = _norm_cell(pref)
            for i, c in enumerate(header_row):
                if _norm_cell(c).startswith(np):
                    return i
        return None

    return {
        'poz': find('Поз.', 'Позиция'),
        'name': find('Наименование'),
        'type': find('Тип, марка, обозначение', 'Тип, марка'),
        'code': find('Код продукции', 'Код оборудования'),
        'supplier': find('Поставщик', 'Завод'),
        'unit': find('Ед. изм', 'Единица'),
        'qty': find('Кол.', 'Кол-во', 'Коли'),
        'mass': find('Масса 1 ед.', 'Масса'),
        'note': find('Примечани'),
    }


def spec_score(header_row) -> int:
    """How many of 9 logical columns map from this header row."""
    cols = map_columns(header_row)
    return sum(1 for v in cols.values() if v is not None)


def find_spec_header(rows) -> int | None:
    """Index of spec header in table rows, or None if not a spec table."""
    for i, r in enumerate(rows):
        if not any((c or '').strip().startswith(('Поз.', 'Позиция')) for c in r):
            continue
        if spec_score(r) >= SPEC_MIN_COLS:
            return i
    return None


def is_frame_row(rec: dict) -> bool:
    blob = ' '.join(v for v in rec.values() if isinstance(v, str))
    if not rec.get('name') and FRAME_RE.search(blob):
        return True
    return False


def detect_template(header_row) -> str:
    """Detect 'VK' or 'OV' template from header text."""
    hdr_cells = ' '.join((c or '') for c in header_row)
    return 'OV' if 'Позиция' in hdr_cells else 'VK'
