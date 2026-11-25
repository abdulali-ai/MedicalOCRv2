"""Registry mapping provider names to their electronic PDF parsers."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.config import settings
from app.core.epdf.parsers.janzour import extract_janzour_epdf
from app.core.epdf.types import EpdfDocument, EpdfError, EpdfPagePayload


EpdfParser = Callable[[str | Path], EpdfDocument]

_PARSERS: dict[str, EpdfParser] = {
    "janzour": extract_janzour_epdf,
}


def registered_epdf_templates() -> list[str]:
    """Return sorted provider names that support electronic PDF extraction."""
    return sorted(_PARSERS)


def get_epdf_parser(template: str) -> EpdfParser:
    """Look up the registered e-PDF parser for `template`, or raise EpdfError."""
    cleaned = (template or "").strip().lower()
    parser = _PARSERS.get(cleaned)
    if parser is None:
        supported = ", ".join(registered_epdf_templates())
        raise EpdfError(
            f"Unknown e-PDF template '{template}'. Supported templates: {supported}"
        )
    return parser


def epdf_dir(task_id: str) -> Path:
    """Directory where per-page extracted e-PDF text files are stored."""
    path = Path(settings.STORAGE_DIR) / "epdf" / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = [
    "EpdfDocument",
    "EpdfError",
    "EpdfPagePayload",
    "EpdfParser",
    "epdf_dir",
    "get_epdf_parser",
    "registered_epdf_templates",
]
