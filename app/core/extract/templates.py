"""Named JSON template registry, including per-template classify prompts."""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import settings


# Fallback used by tests and older call sites that do not pass a template schema.
CLASS_LABELS = ("invoice", "medicine_details", "discharge_sheet", "other_document")

classify_template = {
    "document_type": list(CLASS_LABELS),
}

_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


class TemplateError(ValueError):
    """The template directory does not match the registry contract."""


def exact_label(value, labels: tuple[str, ...] | list[str] | None = None) -> str:
    """Return the label only when it is exactly one of the allowed enum strings."""
    allowed = tuple(labels) if labels is not None else CLASS_LABELS
    if isinstance(value, str) and value in allowed:
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


def _parse_classify(path: Path, payload: dict) -> dict:
    classify = payload.get("classify")
    if not isinstance(classify, dict):
        raise TemplateError(f"{path.name} must contain object key 'classify'")
    labels = classify.get("document_type")
    if not isinstance(labels, list) or not labels:
        raise TemplateError(f"{path.name} classify.document_type must be a non-empty array")
    if not all(isinstance(label, str) and label for label in labels):
        raise TemplateError(f"{path.name} classify.document_type entries must be non-empty strings")
    if len(set(labels)) != len(labels):
        raise TemplateError(f"{path.name} classify.document_type has duplicate labels")
    if "other_document" not in labels:
        raise TemplateError(f"{path.name} classify.document_type must include 'other_document'")
    return {"document_type": list(labels)}


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
        classify = _parse_classify(path, payload)
        schema = {"classify": classify, "invoice": invoice, "id_card": id_card}
        # Optional label-specific extraction schemas, e.g. doctor_invoice.
        for key, value in payload.items():
            if key in schema or key == "classify":
                continue
            if isinstance(value, dict):
                schema[key] = value
        names.append(item)
        schemas[item] = schema

    return TemplateRegistry(root, names, schemas)
