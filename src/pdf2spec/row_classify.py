"""Classify each raw record: item / header / component / continuation / group.

Ported from Hermes spec-pdf-csv/scripts/extract_spec.py classify().
"""
import re

from src.pdf2spec.clean import clean_text, clean_name

HEADER_PREFIXES = [
    'Трубы и изоляция', 'Трубопроводы и изоляция', 'Оборудование и арматура',
    'Оборудование, фитинги', 'Сантехническое оборудование', 'Гидравлическое испытание',
    'Водопровод', 'Канализация', 'Водомерные узлы', 'Окраска трубопроводов',
    'Отопление', 'Фасонные изделия', 'Теплоснабжение приточных установок',
    'Общеобменная вентиляция', 'Противодымная вентиляция', 'Холодоснабжение',
    'Кондиционирование', 'Крепежный материал', 'Вентиляция и противодымная защита',
    'Хозяйственно-питьевой водопровод', 'Противопожарный водопровод',
    'Горячее водоснабжение', 'Резервное горячее водоснабжение',
]

HEADER_RE = re.compile(r'(водопровод|водоснабжение|канализация)\s*[ВТК]\d')


def is_header(name: str) -> bool:
    if any(name == p or name.startswith(p) for p in HEADER_PREFIXES):
        return True
    return bool(HEADER_RE.search(name)) and len(name) < 75


def classify(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Classify records into roles: item/header/component/continuation.

    Returns (rows_out, log) where rows_out has 'role' field added.
    """
    rows_out = []
    log = []
    prev = None

    for r in records:
        name = clean_name(r['name'])
        typ = clean_text(r['type'])
        sup = clean_text(r['supplier'])
        note = clean_text(r['note'])
        qty = re.sub(r'\s+', ' ', r['qty']).strip()
        unit = re.sub(r'\s+', ' ', r['unit']).strip()

        if re.fullmatch(r'[\d]{1,2}', name):
            continue

        base = {
            'poz': r['poz'], 'code': clean_text(r['code']), 'mass': r['mass'],
            'supplier': sup, 'type': typ, 'note': note, 'page': r['_page'],
        }

        if not name:
            if typ and qty and prev is not None and prev['role'] == 'item':
                rows_out.append({
                    'role': 'item', 'name': prev['name'], 'unit': unit,
                    'qty': qty, **base,
                })
                log.append({
                    'type': 'GROUP_INHERIT', 'name': prev['name'][:50],
                    'type': typ, 'qty': qty, 'page': r['_page'],
                })
                prev = rows_out[-1]
                continue
            log.append({'type': 'EMPTY_NAME', 'page': r['_page'], 'raw': r})
            continue

        if qty:
            rows_out.append({'role': 'item', 'name': name, 'unit': unit, 'qty': qty, **base})
            log.append({'type': 'ITEM', 'name': name[:50], 'page': r['_page']})
            prev = rows_out[-1]
            continue

        if is_header(name):
            rows_out.append({
                'role': 'header', 'name': name,
                **{k: '' for k in ('unit', 'qty')}, **base,
            })
            log.append({'type': 'HEADER', 'name': name, 'page': r['_page']})
            prev = None
            continue

        if typ:
            rows_out.append({'role': 'component', 'name': name, 'unit': '', 'qty': '', **base})
            log.append({'type': 'COMPONENT', 'name': name[:50], 'std': typ, 'page': r['_page']})
            prev = rows_out[-1]
            continue

        if prev is not None and prev['role'] in ('item', 'component'):
            prev['name'] += ' ' + name
            log.append({
                'type': 'MERGE', 'into': prev['name'][:50],
                'frag': name[:30], 'page': r['_page'],
            })
        else:
            rows_out.append({'role': 'item', 'name': name, 'unit': unit, 'qty': '', **base})
            log.append({'type': 'ORPHAN', 'frag': name[:30], 'page': r['_page']})

    return rows_out, log
