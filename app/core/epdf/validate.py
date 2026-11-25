"""Fast text-layer validation to reject scanned image PDFs on e-PDF routes."""
from __future__ import annotations

from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    import fitz


MIN_TEXT_CHARS = 20


def has_selectable_text(pdf_path: str | Path, *, min_chars: int = MIN_TEXT_CHARS) -> bool:
    """Return True when the PDF contains at least `min_chars` non-whitespace text characters."""
    doc = fitz.open(str(pdf_path))
    try:
        total = 0
        for page in doc:
            text = page.get_text("text") or ""
            total += sum(1 for ch in text if not ch.isspace())
            if total >= min_chars:
                return True
        return False
    finally:
        doc.close()


def require_selectable_text(pdf_path: str | Path, *, min_chars: int = MIN_TEXT_CHARS) -> None:
    """Raise ValueError when a PDF has no embedded text layer (likely scanned)."""
    if not has_selectable_text(pdf_path, min_chars=min_chars):
        raise ValueError(
            "PDF has no selectable text layer (appears to be scanned). "
            "Use the image OCR endpoint (/process-pdf/) instead."
        )
