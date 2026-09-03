"""QA scanner: verify extraction quality and generate audit log.

Checks for orphan rows, artifact remnants, role counts, and completeness.
"""
import json
import re
from collections import Counter

from src.pdf2spec.clean import SPLITS


def qa(rows_out: list[dict], log: list[dict]) -> dict:
    """Run QA checks on classified rows and log.

    Returns issues dict with lists of findings.
    """
    blob = json.dumps(rows_out, ensure_ascii=False)
    blob_l = blob.lower()

    issues = {
        'word_splits': [p for p, _ in SPLITS if p.lower() in blob_l],
        'naked_diam': [
            r['name'] for r in rows_out
            if re.search(r'Ø(?!\d)', r.get('name', ''))
        ],
        'space_punct': [
            r['name'] for r in rows_out
            if re.search(r'[\w]\s[.,:;]', r.get('name', ''))
        ],
        'orphans': [
            l for l in log if l.get('type') in ('ORPHAN', 'EMPTY_NAME')
        ],
        'items_no_qty': [
            r['name'] for r in rows_out
            if r.get('role') == 'item' and not r.get('qty')
        ],
    }

    singles = set()
    for r in rows_out:
        for t in r.get('name', '').split():
            if re.fullmatch(r'[а-яёА-ЯЁ]', t) and t not in 'абв':
                singles.add(t)
    issues['single_letter_tokens'] = sorted(singles)

    counts = Counter(r.get('role', 'unknown') for r in rows_out)
    issues['role_counts'] = dict(counts)
    issues['total_rows'] = len(rows_out)

    return issues


def qa_scan_csv(path: str, name_col: int = 1) -> tuple[list, list]:
    """Scan a produced CSV for residual artifacts (reused from qa_scan_csv.py).

    Returns (header, issues) where issues is list of (row#, kind, value).
    """
    issues = []
    with open(path, newline='', encoding='utf-8-sig') as f:
        sample = f.read(1024)
    sep = ';' if ';' in sample else ','
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = list(__import__('csv').reader(f, delimiter=sep))
    header = reader[0]
    for ri, row in enumerate(reader[1:], start=2):
        if ri >= len(row):
            row = row + [''] * (len(header) - len(row))
        name = row[name_col] if name_col < len(row) else ''
        if not any(cell.strip() for cell in row):
            continue
        if '  ' in name:
            issues.append((ri, 'double_space', name))
        if re.search(r'\s[.,:;]', name):
            issues.append((ri, 'space_before_punct', name))
        if re.search(r'[х=]\s*$', name):
            issues.append((ri, 'dangling_separator', name))
        if re.search(r'Ø(?!\d)', name):
            issues.append((ri, 'naked_symbol', name))
        if re.search(r'х \d', name):
            issues.append((ri, 'h_space_digit', name))
        if not name.strip():
            issues.append((ri, 'empty_name', ' '.join(row)))
    return header, issues
