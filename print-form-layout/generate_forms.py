#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["reportlab"]
# ///

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


INPUT_CSV = Path("GROWING FROM SEED - FLOWERS.csv")
OUTPUT_DIR = Path("output/pdf")
OUTPUT_PDF = OUTPUT_DIR / "seed_forms_a5.pdf"

PAGE_WIDTH, PAGE_HEIGHT = A5
MARGIN = 11 * mm
CONTENT_X = MARGIN
CONTENT_Y = MARGIN
CONTENT_W = PAGE_WIDTH - (2 * MARGIN)
CONTENT_H = PAGE_HEIGHT - (2 * MARGIN)

ACCENT = HexColor("#6B8E23")
ACCENT_DARK = HexColor("#435B12")
TEXT = HexColor("#202020")
MUTED = HexColor("#6B6B6B")
LINE = HexColor("#C7C2B8")
PANEL = HexColor("#F7F2E8")
PAGE_TINT = HexColor("#FCFAF6")
PAGE_TINT = HexColor("#FFFFFF")

NOTES_LINES = 5

TITLE_CASE_FIELDS = {
    "NAME",
    "LIGHT OR DARK",
    "BOUQUET",
    "VASE LIFE",
}
SENTENCE_CASE_FIELDS = {
    "WHEN TO SOW SEED BLF",
    "HOW TO SOW",
    "REQUIREMENTS",
    "GERMINATION TIME",
    "TIME TO PLANT OUT",
    "HARDEN OFF",
    "WHEN FLOWERING",
    "OTHER NOTES",
}
LOWERCASE_UNIT_PATTERNS = {
    " cm": re.compile(r"(?<=\d)\s*cm\b", flags=re.IGNORECASE),
    " mm": re.compile(r"(?<=\d)\s*mm\b", flags=re.IGNORECASE),
    " days": re.compile(r"(?<=\d)\s*days\b", flags=re.IGNORECASE),
    " wks": re.compile(r"(?<=\d)\s*wks\b", flags=re.IGNORECASE),
}
MINOR_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}


def normalize_header(value: str) -> str:
    value = value.strip().upper()
    value = value.replace("-", " ")
    value = " ".join(value.split())
    return value


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            normalized = {
                normalize_header(k): normalize_value(normalize_header(k), (v or "").strip())
                for k, v in row.items()
                if k
            }
            rows.append(normalized)
        return rows


def is_mostly_upper(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    upper_count = sum(1 for char in letters if char.isupper())
    return upper_count / len(letters) > 0.7


def collapse_spacing(text: str) -> str:
    text = re.sub(r"\s*-\s*", " - ", text)
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s*&\s*", " & ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def smart_title_case(text: str) -> str:
    def convert_word(match: re.Match[str]) -> str:
        word = match.group(0)
        prev = text[: match.start()]
        is_first = not re.search(r"[A-Za-z]", prev)
        lower = word.lower()
        if not is_first and lower in MINOR_WORDS:
            return lower
        return lower[:1].upper() + lower[1:]

    return re.sub(r"[A-Za-z]+(?:'[A-Za-z]+)?", convert_word, text)


def smart_sentence_case(text: str) -> str:
    lowered = text.lower()

    def uppercase_after_break(match: re.Match[str]) -> str:
        prefix = match.group(1)
        letter = match.group(2).upper()
        return f"{prefix}{letter}"

    lowered = re.sub(r"(^|[.!?]\s+|\n)([a-z])", uppercase_after_break, lowered)
    return lowered


def normalize_measurements(text: str) -> str:
    text = re.sub(r"°c\b", "°C", text, flags=re.IGNORECASE)
    for replacement, pattern in LOWERCASE_UNIT_PATTERNS.items():
        text = pattern.sub(replacement, text)
    return text


def normalize_value(field: str, value: str) -> str:
    value = collapse_spacing(value)
    if not value:
        return value

    if is_mostly_upper(value):
        if field in TITLE_CASE_FIELDS:
            value = smart_title_case(value)
        elif field in SENTENCE_CASE_FIELDS:
            value = smart_sentence_case(value)
        else:
            value = value.lower()

    return normalize_measurements(value)


def fit_text(c: canvas.Canvas, text: str, max_width: float, font_name: str, max_size: int, min_size: int) -> int:
    size = max_size
    while size > min_size and stringWidth(text, font_name, size) > max_width:
        size -= 1
    return size


def draw_wrapped_text(
    c: canvas.Canvas,
    text: str,
    x: float,
    top_y: float,
    width: float,
    font_name: str,
    font_size: int,
    leading: float,
    color=TEXT,
    max_lines: int | None = None,
) -> float:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if stringWidth(candidate, font_name, font_size) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)

    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            truncated = lines[-1]
            while truncated and stringWidth(f"{truncated}...", font_name, font_size) > width:
                truncated = truncated[:-1]
            lines[-1] = f"{truncated}..."

    c.setFillColor(color)
    text_obj = c.beginText(x, top_y - font_size)
    text_obj.setFont(font_name, font_size)
    text_obj.setLeading(leading)
    for line in lines:
        text_obj.textLine(line)
    c.drawText(text_obj)
    return top_y - (len(lines) * leading)


def draw_field(c: canvas.Canvas, label: str, value: str, x: float, y: float, w: float, h: float, lines: int = 1) -> None:
    c.setFillColor(white)
    c.setStrokeColor(LINE)
    c.roundRect(x, y, w, h, 4, stroke=1, fill=1)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x + 4 * mm, y + h - 5 * mm, label)

    value = value.strip()
    if value:
        c.setFillColor(TEXT)
        c.setFont("Helvetica", 10)
        draw_wrapped_text(
            c,
            value,
            x + 4 * mm,
            y + h - 8.5 * mm,
            w - 8 * mm,
            "Helvetica",
            10,
            4.4 * mm,
            max_lines=max(1, lines + 1),
        )
        return

    c.setStrokeColor(HexColor("#BDB6A8"))
    for index in range(lines):
        line_y = y + h - (11 * mm) - (index * 6.5 * mm)
        c.line(x + 4 * mm, line_y, x + w - 4 * mm, line_y)


def draw_notes(c: canvas.Canvas, x: float, y: float, w: float, h: float, notes: str) -> None:
    c.setFillColor(white)
    c.setStrokeColor(LINE)
    c.roundRect(x, y, w, h, 5, stroke=1, fill=1)
    c.setFillColor(ACCENT_DARK)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 4 * mm, y + h - 5 * mm, "NOTES")

    inner_top = y + h - 9 * mm
    if notes.strip():
        draw_wrapped_text(
            c,
            notes,
            x + 4 * mm,
            inner_top,
            w - 8 * mm,
            "Helvetica",
            10,
            4.6 * mm,
            max_lines=4,
        )

    c.setStrokeColor(HexColor("#BDB6A8"))
    for index in range(NOTES_LINES):
        line_y = y + h - (13 * mm) - (index * 7.5 * mm)
        c.line(x + 4 * mm, line_y, x + w - 4 * mm, line_y)


def draw_form_page(c: canvas.Canvas, row: dict[str, str], origin_x: float = 0, origin_y: float = 0) -> None:
    c.saveState()
    c.translate(origin_x, origin_y)

    c.setFillColor(PAGE_TINT)
    c.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)

    c.setFillColor(white)
    c.setStrokeColor(LINE)
    c.roundRect(CONTENT_X, CONTENT_Y, CONTENT_W, CONTENT_H, 8, stroke=1, fill=1)

    header_h = 24 * mm
    c.setFillColor(ACCENT)
    c.roundRect(CONTENT_X, PAGE_HEIGHT - MARGIN - header_h, CONTENT_W, header_h, 8, stroke=0, fill=1)
    c.rect(CONTENT_X, PAGE_HEIGHT - MARGIN - header_h, CONTENT_W, 8, stroke=0, fill=1)

    name = row.get("NAME", "Unnamed")
    name_size = fit_text(c, name, CONTENT_W - 20 * mm, "Helvetica-Bold", 20, 12)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", name_size)
    c.drawString(CONTENT_X + 6 * mm, PAGE_HEIGHT - MARGIN - 10 * mm, name)
    c.setFont("Helvetica", 8)
    c.drawString(CONTENT_X + 6 * mm, PAGE_HEIGHT - MARGIN - 16 * mm, "Seed-starting reference card")

    body_top = PAGE_HEIGHT - MARGIN - header_h - 5 * mm
    gap = 4 * mm
    col_gap = 4 * mm
    col_w = (CONTENT_W - col_gap) / 2

    left_x = CONTENT_X
    right_x = CONTENT_X + col_w + col_gap

    stat_h = 16 * mm
    y = body_top - stat_h
    third_gap = 3 * mm
    third_w = (CONTENT_W - (2 * third_gap)) / 3
    third_2_x = CONTENT_X + third_w + third_gap
    third_3_x = CONTENT_X + (2 * (third_w + third_gap))

    draw_field(c, "When To Sow", row.get("WHEN TO SOW SEED BLF", ""), CONTENT_X, y, third_w, stat_h)
    draw_field(c, "Light Or Dark", row.get("LIGHT OR DARK", ""), third_2_x, y, third_w, stat_h)
    draw_field(c, "Temperature", row.get("TEMPERATURE", ""), third_3_x, y, third_w, stat_h)

    y -= 18 * mm
    draw_field(c, "Germination Time", row.get("GERMINATION TIME", ""), CONTENT_X, y, third_w, stat_h)
    draw_field(c, "Plant Out", row.get("TIME TO PLANT OUT", ""), third_2_x, y, third_w, stat_h)
    draw_field(c, "Harden Off", row.get("HARDEN OFF", ""), third_3_x, y, third_w, stat_h)

    y -= 18 * mm
    draw_field(c, "When Flowering", row.get("WHEN FLOWERING", ""), CONTENT_X, y, third_w, stat_h)
    draw_field(c, "Plant Spacing", row.get("PLANT SPACING", ""), third_2_x, y, third_w, stat_h)
    draw_field(c, "Height", row.get("HEIGHT", ""), third_3_x, y, third_w, stat_h)

    y -= 28 * mm
    draw_field(c, "How To Sow", row.get("HOW TO SOW", ""), left_x, y, col_w, 24 * mm, lines=2)
    draw_field(c, "Requirements", row.get("REQUIREMENTS", ""), right_x, y, col_w, 24 * mm, lines=2)

    y -= 22 * mm
    draw_field(
        c,
        "Bouquet / Vase Life",
        " / ".join(filter(None, [row.get("BOUQUET", ""), row.get("VASE LIFE", "")])),
        CONTENT_X,
        y,
        CONTENT_W,
        18 * mm,
    )

    notes_y = CONTENT_Y #+ 6 * mm
    notes_h = y - notes_y - gap
    draw_notes(c, CONTENT_X, notes_y, CONTENT_W, notes_h, row.get("OTHER NOTES", ""))
    c.restoreState()


def create_pdf(rows: list[dict[str, str]], output_path: Path, layout: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if layout == "a5":
        c = canvas.Canvas(str(output_path), pagesize=A5)
        c.setTitle("Seed Forms")
        for row in rows:
            draw_form_page(c, row)
            c.showPage()
        c.save()
        return

    if layout == "a4-2up":
        sheet_width, sheet_height = landscape(A4)
        c = canvas.Canvas(str(output_path), pagesize=(sheet_width, sheet_height))
        c.setTitle("Seed Forms 2-up")
        gutter = (sheet_width - (2 * PAGE_WIDTH)) / 3
        left_origin_x = gutter
        right_origin_x = gutter * 2 + PAGE_WIDTH
        origin_y = 0

        for index in range(0, len(rows), 2):
            c.setFillColor(white)
            c.rect(0, 0, sheet_width, sheet_height, stroke=0, fill=1)
            draw_form_page(c, rows[index], origin_x=left_origin_x, origin_y=origin_y)
            if index + 1 < len(rows):
                draw_form_page(c, rows[index + 1], origin_x=right_origin_x, origin_y=origin_y)
            c.showPage()
        c.save()
        return

    raise SystemExit(f"Unsupported layout: {layout}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate one A5 printable form per CSV row.")
    parser.add_argument("--input", type=Path, default=INPUT_CSV, help="Path to the source CSV file.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PDF, help="Path to the generated PDF file.")
    parser.add_argument(
        "--layout",
        choices=("a5", "a4-2up"),
        default="a5",
        help="Generate single A5 pages or impose two A5 forms on each A4 landscape sheet.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_rows(args.input)
    if not rows:
        raise SystemExit(f"No data rows found in {args.input}")
    create_pdf(rows, args.output, args.layout)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
