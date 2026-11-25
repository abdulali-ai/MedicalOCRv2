"""Janzour (CliniSysErp) electronic PDF parser built on pdfplumber + python-bidi."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber

try:
    from bidi import get_display
except ImportError:
    from bidi.algorithm import get_display

from app.core.epdf.types import EpdfDocument, EpdfPagePayload


_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_TRAILING_NUM_RE = re.compile(r"[\d.\s]+$")
_MERGED_TIME_DATE_PRICE_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2})\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+([\d\s.,]+)\s*$"
)

_SUMMARY_KEYWORDS = (
    "الإجمالي",
    "الاجمالي",
    "الصافي",
    "التخفيض",
    "الخدمة الطبية",
    "الدمغة",
    "القيمة",
    "VAT",
)

# Common Arabic font-ligature artifacts exported by CliniSysErp.
_LIGATURE_FIXES = (
    ("مددد", "محمد"),
)


def fix_line(text: str) -> str:
    """Convert visual-order Arabic text to logical order without disturbing LTR-only strings."""
    if not text:
        return ""
    fixed = get_display(text) if _ARABIC_RE.search(text) else text
    for bad, good in _LIGATURE_FIXES:
        if bad in fixed:
            fixed = fixed.replace(bad, good)
    return fixed


def fix_cell(cell: str | None) -> str:
    """Fix each wrapped line of a table cell separately while keeping top-to-bottom order."""
    if not cell:
        return ""
    return " ".join(fix_line(line.strip()) for line in str(cell).split("\n") if line.strip())


def rtl_words(words: list[dict[str, Any]]) -> list[str]:
    """Given words sorted right-to-left, restore LTR ordering inside adjacent Latin-only runs."""
    out: list[str] = []
    latin_run: list[str] = []
    for word in words:
        token = str(word.get("text") or "")
        if _LATIN_RE.search(token) and not _ARABIC_RE.search(token):
            latin_run.append(token)
        else:
            if latin_run:
                out.extend(reversed(latin_run))
                latin_run = []
            out.append(token)
    if latin_run:
        out.extend(reversed(latin_run))
    return out


def _extract_region_lines(page, y0: float, y1: float, *, merge_latin_continuations: bool = True) -> list[str]:
    top = max(0.0, float(y0))
    bottom = min(float(page.height), float(y1))
    if bottom - top < 2:
        return []
    crop = page.crop((0, top, page.width, bottom))
    rows: dict[int, list[dict[str, Any]]] = {}
    for word in crop.extract_words() or []:
        rows.setdefault(round(word["top"] / 5), []).append(word)

    lines: list[str] = []
    for bucket in sorted(rows):
        ordered = sorted(rows[bucket], key=lambda item: -item["x0"])
        line = " ".join(fix_line(token) for token in rtl_words(ordered)).strip()
        if not line:
            continue
        if (
            merge_latin_continuations
            and lines
            and _LATIN_RE.search(line)
            and not _ARABIC_RE.search(line)
            and ":" not in line
        ):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return lines


def header_lines(page, y_limit: float) -> list[str]:
    """Extract the top invoice header box in logical reading order."""
    return _extract_region_lines(page, 0.0, y_limit, merge_latin_continuations=True)


def footer_lines(page, *, min_y: float | None = None, band_height: float = 24.0) -> list[str]:
    """Extract the bottom footer band (USER, RUN DATE, RUN TIME) in LTR order without table bleed."""
    y0 = max(0.0, float(page.height) - band_height)
    if min_y is not None:
        y0 = max(y0, float(min_y) + 2.0)
    y1 = float(page.height)
    if y1 - y0 < 2:
        return []
    crop = page.crop((0, y0, page.width, y1))
    words = crop.extract_words() or []
    if not words:
        return []

    rows: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        rows.setdefault(round(word["top"] / 5), []).append(word)

    lines: list[str] = []
    for bucket in sorted(rows):
        ordered = sorted(rows[bucket], key=lambda item: item["x0"])
        line = " ".join(str(word.get("text") or "").strip() for word in ordered if word.get("text")).strip()
        if not line:
            continue
        upper = line.upper()
        if any(tag in upper for tag in ("USER", "RUN DATE", "RUN TIME")):
            lines.append(line)
    return lines


def norm_title(title: str) -> str:
    """Strip stray trailing numbers such as '0.00' attached to section titles."""
    return _TRAILING_NUM_RE.sub("", title or "").strip()


def section_title(page, top: float, prev_bottom: float | None) -> str:
    """Extract and normalize the section title printed immediately above a table."""
    y0 = prev_bottom if prev_bottom is not None else max(top - 40.0, 0.0)
    if top - y0 < 5:
        return ""
    raw = page.crop((0, y0, page.width, top)).extract_text() or ""
    joined = " ".join(fix_line(line.strip()) for line in raw.splitlines() if line.strip())
    return norm_title(joined)


def dedupe_headers(headers: list[str]) -> list[str]:
    """Ensure repeated column names (e.g. two 'الساعة' columns on surgery tables) stay distinct."""
    counts: dict[str, int] = {}
    for header in headers:
        counts[header] = counts.get(header, 0) + 1

    seen: dict[str, int] = {}
    result: list[str] = []
    for idx, header in enumerate(headers):
        key = header or f"col_{idx}"
        if counts.get(header, 0) <= 1:
            result.append(key)
            continue
        occurrence = seen.get(header, 0) + 1
        seen[header] = occurrence
        prev_label = headers[idx - 1] if idx > 0 else ""
        if prev_label in {"من", "إلى", "الى"}:
            result.append(f"{key} ({prev_label})")
        else:
            result.append(f"{key}_{occurrence}")
    return result


def is_total_row(row: dict[str, str]) -> bool:
    """True when a table record represents the Arabic section total footer row."""
    if row.get("الرمز"):
        return False
    return any(("الاجمالي" in str(val) or "الإجمالي" in str(val)) for val in row.values())


def _is_summary_table(rows: list[list[str]]) -> bool:
    flat_text = " ".join(cell for row in rows for cell in row if cell)
    hits = sum(1 for kw in _SUMMARY_KEYWORDS if kw in flat_text)
    return hits >= 2


def _repair_merged_pharmacy_cells(record: dict[str, str]) -> dict[str, str]:
    """Unpack rows where missing inner vertical borders merge time+date+unit_price into 'الساعة'."""
    if (
        "سعر الوحدة" in record
        and "التاريخ" in record
        and "الساعة" in record
        and not record["سعر الوحدة"]
        and not record["التاريخ"]
        and record["الساعة"]
    ):
        match = _MERGED_TIME_DATE_PRICE_RE.match(record["الساعة"])
        if match:
            record["الساعة"] = match.group(1).strip()
            record["التاريخ"] = match.group(2).strip()
            record["سعر الوحدة"] = match.group(3).strip()
    return record


def table_to_records(rows: list[list[str]]) -> tuple[str, list[Any]]:
    """Classify an RTL-ordered table into ('items' | 'summary' | 'empty' | 'raw', payload)."""
    cleaned_rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not cleaned_rows:
        return "empty", []

    first = cleaned_rows[0]
    if first and first[0] == "الرمز":
        if len(cleaned_rows) <= 1:
            # Header-only orphan table (e.g. bottom of page 1 before continuation on page 2).
            return "empty", []
        keys = dedupe_headers(first)
        records = [_repair_merged_pharmacy_cells(dict(zip(keys, row))) for row in cleaned_rows[1:]]
        return "items", records

    if _is_summary_table(cleaned_rows):
        return "summary", cleaned_rows

    return "raw", cleaned_rows


def _format_total_row(row: dict[str, str]) -> str:
    parts = []
    for col in ("صافي السعر", "المريض", "الجهة"):
        val = (row.get(col) or "").strip()
        if val:
            parts.append(f"{col}: {val}")
    if parts:
        return "[SECTION_TOTAL] " + " | ".join(parts)
    fallback = " ".join(val.strip() for val in row.values() if val and val.strip())
    return f"[SECTION_TOTAL] {fallback}" if fallback else ""


def rows_to_lines(kind: str, rows: list[Any]) -> list[str]:
    """Render parsed table rows into compact pipe-delimited lines for NuExtract."""
    if not rows or kind == "empty":
        return []
    if kind != "items" or isinstance(rows[0], list):
        return [" | ".join(str(cell) for cell in row) for row in rows]

    cols = list(rows[0].keys())
    lines = [" | ".join(cols)]
    for row in rows:
        if is_total_row(row):
            total_line = _format_total_row(row)
            if total_line:
                lines.append(total_line)
        else:
            lines.append(" | ".join(row.get(col, "") for col in cols))
    return lines


def format_page_text(
    header: list[str],
    footer: list[str],
    page_number: int,
    sections: list[dict[str, Any]],
) -> str:
    """Build the NuExtract text prompt for a single e-PDF page."""
    out = ["[HEADER]", *header]
    if footer:
        out += ["", "[FOOTER]", *footer]
    out += ["", f"=== PAGE {page_number} ==="]
    for sec in sections:
        label = "[SUMMARY]" if sec["kind"] == "summary" else f"[SECTION] {sec['title']}".rstrip()
        out += ["", label, *rows_to_lines(sec["kind"], sec["rows"])]
    return "\n".join(out)


def extract_janzour_epdf(pdf_path: str | Path) -> EpdfDocument:
    """Parse a Janzour CliniSysErp electronic PDF into per-page structured sections and text."""
    header: list[str] = []
    footer: list[str] = []
    pages: list[EpdfPagePayload] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            tables = sorted(page.find_tables(), key=lambda tbl: tbl.bbox[1])
            if page_number == 1:
                y_limit = (tables[0].bbox[1] - 40.0) if tables else min(260.0, float(page.height))
                last_bottom = max((tbl.bbox[3] for tbl in tables), default=None)
                header = header_lines(page, y_limit)
                footer = footer_lines(page, min_y=last_bottom)

            if not tables:
                continue

            page_sections: list[dict[str, Any]] = []
            prev_bottom: float | None = None
            for tbl in tables:
                raw_rows = tbl.extract() or []
                rtl_rows = [[fix_cell(cell) for cell in reversed(row)] for row in raw_rows]
                kind, records = table_to_records(rtl_rows)
                if kind == "empty":
                    prev_bottom = tbl.bbox[3]
                    continue
                title = "" if kind == "summary" else section_title(page, tbl.bbox[1], prev_bottom)
                page_sections.append({
                    "page": page_number,
                    "kind": kind,
                    "title": title,
                    "rows": records,
                })
                prev_bottom = tbl.bbox[3]

            if not page_sections:
                continue

            text = format_page_text(header, footer, page_number, page_sections)
            pages.append(
                EpdfPagePayload(
                    page=page_number,
                    text=text,
                    sections=page_sections,
                )
            )

    return EpdfDocument(
        template="janzour",
        header=header,
        footer=footer,
        pages=pages,
    )
