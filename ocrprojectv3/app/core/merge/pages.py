"""Rule-based invoice merge. Later page wins for totals and summary."""
from __future__ import annotations

import copy
import re


TOTAL_STRATEGY = "later"
SKIP_KEYS = ("sections", "summary", "page")


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_section_name(name) -> str:
    """Strip trailing prices the model sometimes glues onto a section title."""
    cleaned = _norm(name)
    if not cleaned:
        return cleaned
    cleaned = re.sub(r"(?:\s*[\d]+(?:\s[\d]{3})*(?:[.,]\d+)?)+$", "", cleaned).strip()
    return re.sub(r"[\s.:_\-]+$", "", cleaned).strip()


def sanitize_page_data(data: dict) -> dict:
    if not isinstance(data, dict):
        return data
    out = copy.deepcopy(data)
    for section in out.get("sections") or []:
        if isinstance(section, dict) and "section_name" in section:
            section["section_name"] = clean_section_name(section.get("section_name"))
    return out


def _body(page):
    raw = page["data"] if isinstance(page.get("data"), dict) else page
    return sanitize_page_data(raw) if isinstance(raw, dict) else raw


def _doc_key(body):
    patient = body.get("patient") or {}
    return _norm(patient.get("admission_no")), _norm(patient.get("medical_no"))


def _merge_dicts(old, new):
    if old is None:
        return copy.deepcopy(new)
    if new is None:
        return copy.deepcopy(old)
    if isinstance(old, dict) and isinstance(new, dict):
        out = copy.deepcopy(old)
        for key, value in new.items():
            out[key] = _merge_dicts(out.get(key), value) if key in out else copy.deepcopy(value)
        return out
    return copy.deepcopy(new)


def _merge_totals(old, new):
    if TOTAL_STRATEGY == "later" or old is None or new is None:
        return copy.deepcopy(new if new is not None else old)
    out = copy.deepcopy(new)
    for key, value in new.items():
        if isinstance(value, (int, float)) and isinstance(old.get(key), (int, float)):
            out[key] = round(old[key] + value, 2)
    return out


def _merge_section(left, right):
    out = copy.deepcopy(left)
    out["items"] = (left.get("items") or []) + copy.deepcopy(right.get("items") or [])
    if "section_total" in left or "section_total" in right:
        out["section_total"] = _merge_totals(left.get("section_total"), right.get("section_total"))
    if "summary" in left or "summary" in right:
        out["summary"] = _merge_dicts(left.get("summary"), right.get("summary"))
    return out


def _append_sections(doc, sections):
    for section in sections:
        section = copy.deepcopy(section)
        if isinstance(section, dict):
            section["section_name"] = clean_section_name(section.get("section_name"))
        last = doc["sections"][-1] if doc["sections"] else None
        if last and _norm(last.get("section_name")) == _norm(section.get("section_name")):
            doc["sections"][-1] = _merge_section(last, section)
        else:
            doc["sections"].append(section)


def merge_pages(pages):
    docs = {}
    for page in pages:
        body = _body(page)
        if not isinstance(body, dict):
            continue
        key = _doc_key(body)
        page_no = page.get("page")

        if key not in docs:
            doc = {field: copy.deepcopy(value) for field, value in body.items() if field not in SKIP_KEYS}
            doc["pages"] = [page_no] if page_no is not None else []
            doc["sections"] = []
            _append_sections(doc, body.get("sections") or [])
            if body.get("summary") is not None:
                doc["summary"] = copy.deepcopy(body["summary"])
            docs[key] = doc
            continue

        doc = docs[key]
        if page_no is not None:
            doc["pages"].append(page_no)
        for field, value in body.items():
            if field in SKIP_KEYS:
                continue
            if doc.get(field) in (None, "", {}) and value not in (None, "", {}):
                doc[field] = copy.deepcopy(value)
            elif isinstance(doc.get(field), dict) and isinstance(value, dict):
                doc[field] = {
                    **value,
                    **{key: kept for key, kept in doc[field].items() if kept not in (None, "")},
                }
        _append_sections(doc, body.get("sections") or [])
        if body.get("summary") is not None:
            doc["summary"] = _merge_dicts(doc.get("summary"), body["summary"])
    return list(docs.values())
