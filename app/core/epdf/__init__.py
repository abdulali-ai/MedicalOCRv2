"""Electronic PDF (e-PDF) validation, parser registry, and storage helpers."""
from app.core.epdf.registry import (
    EpdfDocument,
    EpdfError,
    EpdfPagePayload,
    epdf_dir,
    get_epdf_parser,
    registered_epdf_templates,
)
from app.core.epdf.validate import MIN_TEXT_CHARS, has_selectable_text, require_selectable_text

__all__ = [
    "EpdfDocument",
    "EpdfError",
    "EpdfPagePayload",
    "MIN_TEXT_CHARS",
    "epdf_dir",
    "get_epdf_parser",
    "has_selectable_text",
    "registered_epdf_templates",
    "require_selectable_text",
]
