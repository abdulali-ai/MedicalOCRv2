"""Turn terminal page records into the webhook payload."""
from __future__ import annotations

from app.core.merge.pages import merge_pages


def assemble_document(page_records: dict[str, dict]) -> dict:
    invoice_pages = []
    id_cards = []
    other_pages = []
    errors = []

    def page_sort_key(field: str) -> int:
        try:
            return int(field)
        except (TypeError, ValueError):
            return 0

    for field in sorted(page_records, key=page_sort_key):
        record = page_records[field]
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        if kind == "skipped":
            continue
        if kind == "invoice":
            invoice_pages.append({"page": record.get("page"), "data": record.get("data") or {}})
        elif kind == "id_cards":
            id_cards.extend(record.get("cards") or [])
            errors.extend(record.get("errors") or [])
        elif kind == "other":
            other_pages.append({
                "page": record.get("page"),
                "document_type": record.get("document_type"),
            })
        elif kind == "error":
            errors.append({"page": record.get("page"), "message": record.get("message")})

    return {
        "invoices": merge_pages(invoice_pages),
        "id_cards": id_cards,
        "other_pages": other_pages,
        "errors": errors,
    }
