"""Shared classifier enum and the named JSON template registry."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings


CLASS_LABELS = ("invoice", "medicine_details", "id_card", "other_document")

classify_template = {
    "document_type": list(CLASS_LABELS),
}

_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


class TemplateError(ValueError):
    """The template directory does not match the registry contract."""


def exact_label(value) -> str:
    """Return the label only when it is exactly one of the four enum strings."""
    if isinstance(value, str) and value in CLASS_LABELS:
        return value
    return "other_document"


class TemplateRegistry:
    def __init__(self, directory: Path, names: list[str], schemas: dict[str, dict]):
        self.directory = directory
        self.names = names
        self.schemas = schemas

    def __contains__(self, name: str) -> bool:
        return name in self.schemas

    def get(self, name: str) -> dict:
        try:
            return self.schemas[name]
        except KeyError as exc:
            raise TemplateError(f"Unknown template '{name}'") from exc


def load_registry(directory: str | Path | None = None) -> TemplateRegistry:
    root = Path(directory or settings.TEMPLATES_DIR)
    index_path = root / "index.json"
    if not index_path.is_file():
        raise TemplateError(f"Template allow-list is missing: {index_path}")

    try:
        listed = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"Template allow-list is not valid JSON: {exc}") from exc

    if not isinstance(listed, list) or not listed:
        raise TemplateError("Template allow-list must be a non-empty JSON array")

    names: list[str] = []
    schemas: dict[str, dict] = {}
    for item in listed:
        if not isinstance(item, str) or not _NAME_RE.fullmatch(item):
            raise TemplateError(f"Illegal template name: {item!r}")
        if item in schemas:
            raise TemplateError(f"Duplicate template name: {item}")
        path = root / f"{item}.json"
        if not path.is_file():
            raise TemplateError(f"Template file is missing: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TemplateError(f"{path.name} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise TemplateError(f"{path.name} must be a JSON object")
        invoice = payload.get("invoice")
        id_card = payload.get("id_card")
        if not isinstance(invoice, dict) or not isinstance(id_card, dict):
            raise TemplateError(f"{path.name} must contain object keys 'invoice' and 'id_card'")
        names.append(item)
        schemas[item] = {"invoice": invoice, "id_card": id_card}

    return TemplateRegistry(root, names, schemas)
