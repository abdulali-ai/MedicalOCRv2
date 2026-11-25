"""Shared dataclasses and exceptions for electronic PDF parsing."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class EpdfError(ValueError):
    """Raised when an e-PDF template is unknown or extraction fails."""


@dataclass
class EpdfPagePayload:
    page: int
    text: str
    sections: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EpdfDocument:
    template: str
    header: list[str]
    footer: list[str]
    pages: list[EpdfPagePayload]
