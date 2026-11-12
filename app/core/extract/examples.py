"""Few-shot images for classification, invoice, and ID-card extraction.

Each template has its own example set:

  classifier_examples/<template_name>/
  invoice_examples/<template_name>/
  id_card_examples/<template_name>/

Classifier files are the top half of a 300 DPI claim page, resized to 200 DPI.
Invoice files are full pages stored at 150 DPI. ID-card files are detector-style
card crops. Empty manifests are allowed when a template has no few-shots yet.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.core.extract.budget import image_token_count
from app.core.extract.templates import TemplateError


_ROOT = Path(__file__).resolve().parents[2]
_CLASSIFIER_DIR = _ROOT / "classifier_examples"
_INVOICE_DIR = _ROOT / "invoice_examples"
_ID_CARD_DIR = _ROOT / "id_card_examples"


@dataclass(frozen=True)
class ClassifierExample:
    encoded: str
    label: str
    tokens: int

    @property
    def output(self) -> str:
        return json.dumps({"document_type": self.label}, ensure_ascii=False)


@dataclass(frozen=True)
class InvoiceExample:
    encoded: str
    output: str
    tokens: int


@dataclass(frozen=True)
class IdCardExample:
    encoded: str
    output: str
    tokens: int


_CLASSIFIER_CACHE: dict[str, list[ClassifierExample]] = {}
_INVOICE_CACHE: dict[str, list[InvoiceExample]] = {}
_ID_CARD_CACHE: dict[str, list[IdCardExample]] = {}


def classifier_examples(template_name: str = "janzour") -> list[ClassifierExample]:
    name = template_name or "janzour"
    if name not in _CLASSIFIER_CACHE:
        _CLASSIFIER_CACHE[name] = _load_classifier(name)
    return _CLASSIFIER_CACHE[name]


def example_image_tokens(template_name: str = "janzour") -> int:
    return sum(item.tokens for item in classifier_examples(template_name))


def invoice_examples(template_name: str = "janzour") -> list[InvoiceExample]:
    name = template_name or "janzour"
    if name not in _INVOICE_CACHE:
        _INVOICE_CACHE[name] = _load_filled_examples(
            _INVOICE_DIR / name,
            "Invoice",
            InvoiceExample,
            allow_empty=True,
        )
    return _INVOICE_CACHE[name]


def invoice_example_tokens(template_name: str = "janzour") -> int:
    """Image tokens plus a conservative count of the example JSON."""
    return sum(item.tokens + (len(item.output) // 2) for item in invoice_examples(template_name))


def id_card_examples(template_name: str = "janzour") -> list[IdCardExample]:
    name = template_name or "janzour"
    if name not in _ID_CARD_CACHE:
        _ID_CARD_CACHE[name] = _load_filled_examples(
            _ID_CARD_DIR / name,
            "ID-card",
            IdCardExample,
            allow_empty=True,
        )
    return _ID_CARD_CACHE[name]


def id_card_example_tokens(template_name: str = "janzour") -> int:
    return sum(item.tokens + (len(item.output) // 2) for item in id_card_examples(template_name))


def _load_classifier(template_name: str) -> list[ClassifierExample]:
    directory = _CLASSIFIER_DIR / template_name
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise TemplateError(f"Classifier examples are missing: {manifest_path}")
    listed = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(listed, list):
        raise TemplateError(f"Classifier example manifest must be an array: {manifest_path}")

    loaded: list[ClassifierExample] = []
    for item in listed:
        label = item.get("document_type")
        if not isinstance(label, str) or not label:
            raise TemplateError(f"Classifier example label must be a non-empty string: {manifest_path}")
        path = directory / item["file"]
        if not path.is_file():
            raise TemplateError(f"Classifier example image is missing: {path}")
        with Image.open(path) as image:
            tokens = image_token_count(image.width, image.height)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        loaded.append(ClassifierExample(encoded=encoded, label=label, tokens=tokens))
    return loaded


def _load_filled_examples(directory: Path, kind: str, cls, *, allow_empty: bool = False):
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise TemplateError(f"{kind} examples are missing: {manifest_path}")
    listed = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(listed, list):
        raise TemplateError(f"{kind} example manifest must be an array: {manifest_path}")
    if not listed and not allow_empty:
        raise TemplateError(f"{kind} example manifest must be a non-empty array")

    loaded = []
    for item in listed:
        image_path = directory / item["file"]
        output_path = directory / item["output"]
        if not image_path.is_file():
            raise TemplateError(f"{kind} example image is missing: {image_path}")
        if not output_path.is_file():
            raise TemplateError(f"{kind} example output is missing: {output_path}")
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TemplateError(f"{kind} example output must be an object: {output_path}")
        output = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with Image.open(image_path) as image:
            tokens = image_token_count(image.width, image.height)
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        loaded.append(cls(encoded=encoded, output=output, tokens=tokens))
    return loaded
