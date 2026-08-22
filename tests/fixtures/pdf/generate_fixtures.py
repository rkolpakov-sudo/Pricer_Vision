# -*- coding: utf-8 -*-
"""Generate PDF test fixtures for the pdf_parser suite (run once, outputs are committed).

Run with mineru_venv python (has reportlab + Pillow):
    mineru_venv\\Scripts\\python.exe tests\\fixtures\\pdf\\generate_fixtures.py
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).parent

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))


def _table_pdf(name: str, header: list, rows: list):
    doc = SimpleDocTemplate(str(OUT / name), pagesize=A4)
    t = Table([header] + rows)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 1, (0, 0, 0)),
        ("FONTNAME", (0, 0), (-1, -1), "Arial"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    doc.build([t])


def classic():
    """|№|Наименование|Завод-изготовитель|Ед.|Кол.|Масса| — с массой."""
    _table_pdf(
        "table_classic.pdf",
        ["№", "Наименование", "Завод-изготовитель", "Ед.", "Кол.", "Масса"],
        [
            ["1", "Кран шаровой Ду15 Ру16", '"Ридан"', "шт", "10", "0.42"],
            ["2", "Труба стальная ВГП Ду50х3.5 ГОСТ 10704-91", "", "м", "120.5", "4.38"],
            ["3", "Кабель ВВГнг(А)-LS 3х2.5", '"Кольчугино"', "м", "450", "0.15"],
        ],
    )


def no_weight():
    """Без колонки «Масса» и без ячеек массы — регрессия P1."""
    _table_pdf(
        "table_no_weight.pdf",
        ["№", "Наименование", "Завод-изготовитель", "Ед.", "Кол."],
        [
            ["1", "Кран шаровой Ду15 Ру16", '"Ридан"', "шт", "48"],
            ["2", "Воздухоотводчик автоматический Ду20", "", "шт", "12"],
            ["3", "Клапан балансировочный авт. Ду32", '"Ридан"', "шт", "2"],
        ],
    )


def unit_qty_merged():
    """Формат реальной спецификации: юнит+кол-во в одной ячейке «шт. 48», без позиции."""
    _table_pdf(
        "table_unit_qty_merged.pdf",
        ["Наименование и техническая характеристика", "Завод-изготовитель", "Кол."],
        [
            ["Кран шаровой Ду15", '"Ридан"', "шт. 48"],
            ["Компенсатор сильфонный под приварку Ду25", '"Ридан"', "шт. 8"],
            ["Радиатор стальной Ventil Compact CV22 500x600", '"SPL"', "шт. 5"],
        ],
    )


def gost_in_name():
    """Числа внутри наименования (ГОСТ, размеры) не должны становиться qty."""
    _table_pdf(
        "gost_in_name.pdf",
        ["№", "Наименование", "Ед.", "Кол."],
        [
            ["1", "Труба стальная электросварная Ду100х4 ГОСТ 10704-91", "м", "85"],
            ["2", "Фланец стальной плоский ГОСТ 12820-80 Ду50 Ру16", "шт", "6"],
            ["3", "Отвод гнутый Р=1.5ДУ Ду65 ГОСТ 17375-2001", "шт", "14"],
        ],
    )


def scan():
    """Image-only PDF (текстового слоя нет) — маршрут на MinerU."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 16)
    img = Image.new("RGB", (900, 400), "white")
    d = ImageDraw.Draw(img)
    rows = [
        ["№", "Наименование", "Ед.", "Кол."],
        ["1", "Кран шаровой Ду15 Ру16", "шт", "10"],
        ["2", "Труба стальная ВГП Ду50х3.5", "м", "120.5"],
    ]
    x0, y0, col_w = 40, 30, [50, 480, 90, 120]
    y = y0
    for r_i, row in enumerate(rows):
        x = x0
        for c_i, cell in enumerate(row):
            if r_i == 0:
                d.rectangle([x, y, x + col_w[c_i], y + 34], outline="black")
            else:
                d.rectangle([x, y, x + col_w[c_i], y + 34], outline="black")
            d.text((x + 8, y + 8), cell, fill="black", font=font)
            x += col_w[c_i]
        y += 34
    img.save(str(OUT / "scan_spec.pdf"), "PDF", resolution=100)


def broken_fonts():
    """Cyrillic через Helvetica (без ToUnicode) → мусор в текстовом слое."""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(OUT / "broken_fonts.pdf"), pagesize=A4)
    y = 780
    for row in ["1 Кран шаровой Ду15 шт 10", "2 Труба ВГП Ду50 м 120"]:
        c.setFont("Helvetica", 9)
        c.drawString(50, y, row)
        y -= 18
    c.save()


REFERENCE = {
    "table_classic.pdf": [
        {"name": "Кран шаровой Ду15 Ру16", "manufacturer": "Ридан", "unit": "шт", "qty": 10, "weight": 0.42},
        {"name": "Труба стальная ВГП Ду50х3.5 ГОСТ 10704-91", "manufacturer": "", "unit": "м", "qty": 120.5, "weight": 4.38},
        {"name": "Кабель ВВГнг(А)-LS 3х2.5", "manufacturer": "Кольчугино", "unit": "м", "qty": 450, "weight": 0.15},
    ],
    "table_no_weight.pdf": [
        {"name": "Кран шаровой Ду15 Ру16", "manufacturer": "Ридан", "unit": "шт", "qty": 48},
        {"name": "Воздухоотводчик автоматический Ду20", "manufacturer": "", "unit": "шт", "qty": 12},
        {"name": "Клапан балансировочный авт. Ду32", "manufacturer": "Ридан", "unit": "шт", "qty": 2},
    ],
    "table_unit_qty_merged.pdf": [
        {"name": "Кран шаровой Ду15", "manufacturer": "Ридан", "unit": "шт", "qty": 48},
        {"name": "Компенсатор сильфонный под приварку Ду25", "manufacturer": "Ридан", "unit": "шт", "qty": 8},
        {"name": "Радиатор стальной Ventil Compact CV22 500x600", "manufacturer": "SPL", "unit": "шт", "qty": 5},
    ],
    "gost_in_name.pdf": [
        {"name": "Труба стальная электросварная Ду100х4 ГОСТ 10704-91", "unit": "м", "qty": 85},
        {"name": "Фланец стальной плоский ГОСТ 12820-80 Ду50 Ру16", "unit": "шт", "qty": 6},
        {"name": "Отвод гнутый Р=1.5ДУ Ду65 ГОСТ 17375-2001", "unit": "шт", "qty": 14},
    ],
}


def main():
    classic()
    no_weight()
    unit_qty_merged()
    gost_in_name()
    scan()
    broken_fonts()
    (OUT / "expected_items.json").write_text(
        json.dumps(REFERENCE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("fixtures written to", OUT)


if __name__ == "__main__":
    sys.exit(main())
