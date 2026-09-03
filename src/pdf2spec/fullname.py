"""Mother-child (мать-дети) row absorption for hierarchical specifications.

Implements the Hermes methodology from hierarchical-parent-child-rows.md:
- Mother row (no qty) is absorbed by its children
- Child with empty name inherits mother's name
- Child with short designation (DN15, 400 мм) → name=mother, type=(mother.type + ' ' + child.name)
- Group-inherit: empty name + type + qty → name from previous item
- Lookahead to distinguish mother vs header
"""
import re

from src.pdf2spec.clean import clean_text, clean_name

LOOKAHEAD_SHORT_RE = re.compile(
    r'^(DN|Ду|d|Ø|\d+[/xх]\d+|\d+\s*(мм|шт))',
    re.IGNORECASE,
)


def _is_short_designation(name: str, typ: str) -> bool:
    """Check if a child row has a short designation (DN15, 400 мм, 20/20, etc.)."""
    if not name and not typ:
        return False
    if not name and typ:
        if re.match(r'^\d+[/xх]\d+$', typ.strip()):
            return True
        if re.match(r'^\d+\s*(мм|шт|кг|м)$', typ.strip()):
            return True
        if LOOKAHEAD_SHORT_RE.match(typ.strip()):
            return True
        return False
    if LOOKAHEAD_SHORT_RE.match(name.strip()):
        return True
    if len(name.strip()) <= 45 and not re.search(r'[а-яё]{3,}', name.lower()):
        return True
    return False


def _is_continuation(name: str) -> bool:
    """Check if a name-only row is a continuation (lowercase start, attribute)."""
    if not name:
        return False
    first = name.strip()[0]
    return first.islower() or first in ('с', 'на', 'для', 'из')


def _is_structural_subheader(name: str) -> bool:
    """Check if a name-only row is a structural subheader (ends with ':', or ^П\\d+$)."""
    if name.endswith(':'):
        return True
    if re.fullmatch(r'П\d+', name):
        return True
    return False


def _is_all_caps_header(name: str) -> bool:
    """ALL-CAPS section headers: СИСТЕМА ОТОПЛЕНИЯ, АРМАТУРА, etc."""
    if len(name) < 5:
        return False
    return name == name.upper() and any(c.isalpha() for c in name)


def resolve_mother_child(rows: list[dict], log: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply mother-child absorption rules to classified rows.

    Modifies rows in-place and returns (rows, log).
    This is a post-processing pass after initial classify().
    """
    result = []
    current_mother = None

    i = 0
    while i < len(rows):
        row = rows[i]
        role = row.get('role', '')
        name = row.get('name', '')
        typ = row.get('type', '')
        qty = row.get('qty', '')

        if role == 'header':
            current_mother = None
            result.append(row)
            i += 1
            continue

        if role == 'item' and qty:
            if current_mother is not None and not name:
                row['name'] = current_mother['name']
                if typ:
                    mother_type = current_mother.get('type', '')
                    row['type'] = (mother_type + ' ' + typ).strip() if mother_type else typ
                log.append({
                    'type': 'FULL_NAME_INHERIT',
                    'name': row['name'][:50],
                    'child_type': typ,
                    'page': row.get('page', 0),
                })
            elif current_mother is not None and name and _is_short_designation(name, typ):
                inherited_name = current_mother['name']
                row['name'] = inherited_name
                if typ:
                    mother_type = current_mother.get('type', '')
                    row['type'] = (mother_type + ' ' + name).strip() if mother_type else name
                elif name:
                    mother_type = current_mother.get('type', '')
                    row['type'] = (mother_type + ' ' + name).strip() if mother_type else name
                log.append({
                    'type': 'FULL_NAME_CHILD',
                    'name': row['name'][:50],
                    'child_type': row.get('type', ''),
                    'page': row.get('page', 0),
                })
            elif name and not _is_short_designation(name, typ):
                current_mother = None
            result.append(row)
            i += 1
            continue

        if role == 'item' and not qty:
            if name and not _is_continuation(name) and not _is_structural_subheader(name):
                if _is_all_caps_header(name):
                    row['role'] = 'header'
                    row['qty'] = ''
                    current_mother = None
                    result.append(row)
                    i += 1
                    continue

                lookahead = None
                j = i + 1
                while j < len(rows):
                    nxt = rows[j]
                    if nxt.get('qty') or (nxt.get('name') and not _is_continuation(nxt.get('name', ''))):
                        lookahead = nxt
                        break
                    j += 1

                if lookahead and _is_short_designation(
                    lookahead.get('name', ''), lookahead.get('type', '')
                ):
                    current_mother = row
                    log.append({
                        'type': 'MOTHER_ABSORBED',
                        'name': name[:50],
                        'page': row.get('page', 0),
                    })
                    i += 1
                    continue
                elif lookahead and not _is_continuation(lookahead.get('name', '')) and (
                    lookahead.get('qty') or _is_short_designation(
                        lookahead.get('name', ''), lookahead.get('type', '')
                    )
                ):
                    current_mother = row
                    i += 1
                    continue
                elif not lookahead:
                    has_continuations = False
                    for k in range(i + 1, len(rows)):
                        nxt = rows[k]
                        if nxt.get('name') and _is_continuation(nxt.get('name', '')):
                            has_continuations = True
                            break
                        if nxt.get('qty') or (
                            nxt.get('name') and not _is_continuation(nxt.get('name', ''))
                        ):
                            break
                    if has_continuations:
                        current_mother = row
                        result.append(row)
                        i += 1
                        continue
                else:
                    result.append(row)
                    i += 1
                    continue

            if _is_structural_subheader(name):
                result.append(row)
                i += 1
                continue

            if not name and typ and current_mother is not None:
                row['name'] = current_mother['name']
                log.append({
                    'type': 'GROUP_INHERIT',
                    'name': row['name'][:50],
                    'type': typ,
                    'page': row.get('page', 0),
                })
                result.append(row)
                i += 1
                continue

            if current_mother is not None and name:
                current_mother_name = current_mother.get('name', '')
                current_mother['name'] = current_mother_name + ' ' + name
                log.append({
                    'type': 'MERGE',
                    'into': current_mother['name'][:50],
                    'frag': name[:30],
                    'page': row.get('page', 0),
                })
                i += 1
                continue

            result.append(row)
            i += 1
            continue

        if role == 'component':
            current_mother = None
            result.append(row)
            i += 1
            continue

        result.append(row)
        i += 1

    rows[:] = result
    return rows, log
