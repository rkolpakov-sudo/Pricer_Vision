"""Extract raw records from PDF using PyMuPDF find_tables().

Handles rotated landscape sheets, multi-table pages, and header normalization.
"""
import re
import pymupdf

from src.pdf2spec.spec_detect import (
    find_spec_header, map_columns, is_frame_row, detect_template, spec_score,
)


def extract_records(pdf_path: str) -> tuple[list[dict], list[dict], str | None]:
    """Extract all spec records from a PDF.

    Returns (records, report, template) where:
      - records: list of raw dicts with 9 fields + _page
      - report: per-page status log
      - template: 'VK' or 'OV' or None
    """
    doc = pymupdf.open(pdf_path)
    records, report = [], []
    template = None

    for pno, page in enumerate(doc, start=1):
        tabs = page.find_tables()
        if not tabs.tables:
            report.append({'page': pno, 'status': 'NO_TABLES'})
            continue

        spec_tables = []
        rejected = []
        for table in tabs.tables:
            rows = table.extract()
            hdr_idx = find_spec_header(rows)
            if hdr_idx is not None:
                spec_tables.append((hdr_idx, rows))
            else:
                cand = [
                    spec_score(r)[1] if isinstance(spec_score(r), tuple) else spec_score(r)
                    for r in rows
                    if any((c or '').strip().startswith(('Поз.', 'Позиция')) for c in r)
                ]
                if cand:
                    rejected.append(max(cand))

        if not spec_tables:
            report.append({'page': pno, 'status': 'NO_SPEC', 'rejected_scores': rejected})
            continue

        page_n = 0
        for hdr_idx, rows in spec_tables:
            if template is None:
                template = detect_template(rows[hdr_idx])
            cols = map_columns(rows[hdr_idx])
            missing = [k for k, v in cols.items() if v is None]

            def _is_real_row(r):
                pi, ni = cols['poz'], cols['name']
                if pi is None or ni is None or pi >= len(r) or ni >= len(r):
                    return False
                poz, name = (r[pi] or '').strip(), (r[ni] or '').strip()
                return bool(poz) and not re.fullmatch(r'[\d]{1,2}', name)

            data_rows = [r for r in rows[hdr_idx + 1:] if _is_real_row(r)]

            for k, nxt in (('type', 'code'), ('code', 'supplier')):
                idx = cols.get(k)
                if idx is None:
                    continue
                col_vals = [(r[idx] or '').strip() for r in data_rows if idx < len(r)]
                next_vals = [(r[idx + 1] or '').strip() for r in data_rows if idx + 1 < len(r)]
                if not any(col_vals) and any(next_vals):
                    cols[k] = idx + 1
                    cols[nxt] = None if cols.get(nxt) is None else cols[nxt]

            for r in rows[hdr_idx + 1:]:
                rec = {}
                for k, idx in cols.items():
                    if idx is None or idx >= len(r):
                        rec[k] = ''
                    else:
                        rec[k] = re.sub(r'\s+', ' ', r[idx] or '').strip()
                rec['_page'] = pno

                if is_frame_row(rec):
                    report.append({'page': pno, 'status': 'FRAME_SKIPPED', 'poz': rec['poz'][:60]})
                    continue
                if not any(rec[k] for k in ('name', 'type', 'qty', 'supplier', 'unit', 'code')):
                    report.append({'page': pno, 'status': 'EMPTY_SKIPPED'})
                    continue
                records.append(rec)
                page_n += 1

            report.append({
                'page': pno, 'rows': page_n,
                'cols': {k: v for k, v in cols.items()},
                'missing_cols': missing,
            })

    doc.close()
    return records, report, template
