"""Text cleanup: Ø-corruption, word-splits, ОВ-фитинги, number artifacts.

Ported from Hermes spec-pdf-csv/scripts/extract_spec.py clean_text/clean_name.
"""
import re

DIAMETERS = {
    15, 20, 25, 32, 40, 50, 65, 80, 100, 110, 125, 150, 160,
    200, 219, 250, 300, 400, 500, 1000,
}

SPLITS = [
    ('во д огазопров о д ная', 'водогазопроводная'),
    ('оцинков анная', 'оцинкованная'),
    ('электрос варная', 'электросварная'),
    ('прямошов ная', 'прямошовная'),
    ('Гофриров анная', 'Гофрированная'),
    ('Ги б кая', 'Гибкая'),
    ('присоед инения', 'присоединения'),
    ('Патруб ок', 'Патрубок'),
    ('переход ной', 'переходной'),
    ('Трой ник', 'Тройник'),
    ('О т во д', 'Отвод'),
    ('В од омерный', 'Водомерный'),
    ('К ов ер', 'Ковёр'),
    ('гиб ком', 'гибком'),
    ('Труб а', 'Труба'),
    ('на гиб ком', 'на гибком'),
]

SUPPLIER_FIX = {
    'Ekopl astik': 'Ekoplastik',
    'Агпа йп': 'Агпайп',
}

OV_FITTING_BASES = {
    'Отвод-45': 'Отвод-45 стальной',
    'Отвод 45': 'Отвод 45 стальной',
    'Отвод-90': 'Отвод-90 стальной',
    'Отвод 90': 'Отвод 90 стальной',
    'Тройник-90': 'Тройник-90 стальной',
    'Тройник 90': 'Тройник 90 стальной',
    'Тройник-45': 'Тройник-45 стальной',
    'Тройник 45': 'Тройник 45 стальной',
    'Переход': 'Переход стальной',
    'Муфта': 'Муфта стальная',
    'Заглушка': 'Заглушка стальная',
    'Ниппель': 'Ниппель стальной',
}


def _fix_split(s: str, a: str, b: str) -> str:
    if a.lower() not in s.lower():
        return s
    pat = re.compile(re.escape(a), re.IGNORECASE)
    return pat.sub(lambda m: m.group(0).replace(' ', ''), s)


def is_diam(token: str) -> bool:
    m = re.match(r'^(\d+)', token)
    return bool(m) and int(m.group(1)) in DIAMETERS


def clean_text(s: str) -> str:
    """Basic cleanup for type/supplier/note fields."""
    if not s:
        return ''
    for a, b in SUPPLIER_FIX.items():
        s = s.replace(a, b)
    for a, b in SPLITS:
        s = _fix_split(s, a, b)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'(ТУ|ГОСТ)\s+-', r'\1-', s)
    s = re.sub(r'(-)\s+(\d)', r'\1\2', s)
    s = re.sub(r'(\d)\s+(-)', r'\1\2', s)
    s = s.replace(' ,', ',')
    s = re.sub(r'(\d),\s+(\d)', r'\1,\2', s)
    s = re.sub(r'(\d)\s+(\.\d)', r'\1\2', s)
    return re.sub(r'\s+', ' ', s).strip()


def clean_name(s: str) -> str:
    """Full cleanup for name field: splits + Ø-token + dash + ОВ-фитинги."""
    if not s:
        return ''
    s = clean_text(s)
    s = re.sub(r'^-\s*', '', s)
    s = s.replace('м .', 'м.').replace('Д =', 'Д=')
    s = re.sub(r'= (\d)', r'=\1', s)
    s = re.sub(r'(\d)х\s+(\d)', r'\1х\2', s)

    toks = s.split()
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == '6' and i + 1 < len(toks) and is_diam(toks[i + 1]):
            out.append('Ø' + re.sub(r'\s', '', toks[i + 1]))
            i += 2
            continue
        m6 = re.match(r'^6(\d+)[хx]([\d.,]+)$', t)
        if m6 and int(m6.group(1)) in DIAMETERS:
            thick = m6.group(2).replace(',', '.')
            if float(thick) < 100:
                out.append('Ø' + m6.group(1) + 'х' + m6.group(2))
                i += 1
                continue
        if t.startswith('ф') and re.match(r'ф\d', t) and is_diam(t[1:]):
            out.append('Ø' + t[1:])
            i += 1
            continue
        out.append(t)
        i += 1

    s2 = re.sub(r'Ø\s+(\d)', r'Ø\1', ' '.join(out)).replace('∅', 'Ø')
    return re.sub(r'\s+', ' ', s2).strip()


def add_ov_steel(name: str) -> str:
    """Add 'стальной/стальная' to ОВ-fittings without material specification."""
    if not name or 'стальн' in name.lower():
        return name
    for base, replacement in OV_FITTING_BASES.items():
        if name.startswith(base):
            return replacement + name[len(base):]
    return name
