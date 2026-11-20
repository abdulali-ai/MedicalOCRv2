"""Rule-based invoice merge. Later page wins for totals and summary."""
from __future__ import annotations

import copy
import re
import unicodedata
from difflib import SequenceMatcher


TOTAL_STRATEGY = "later"
SKIP_KEYS = ("sections", "summary", "page")
SECTION_FUZZY_THRESHOLD = 85

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LETTER_VARIANTS = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ة": "ه",
    "ى": "ي",
})
# Harakat, dagger alef, and tatweel. Arabic-Indic digits are mapped separately.
_DIACRITICS = dict.fromkeys(
    (
        0x064B, 0x064C, 0x064D, 0x064E, 0x064F, 0x0650, 0x0651, 0x0652,
        0x0653, 0x0654, 0x0655, 0x0656, 0x0657, 0x0658, 0x0659, 0x065A,
        0x065B, 0x065C, 0x065D, 0x065E, 0x065F, 0x0670, 0x0640,
    ),
    None,
)


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_section_name(name) -> str:
    """Strip trailing prices the model sometimes glues onto a section title."""
    cleaned = _norm(name)
    if not cleaned:
        return cleaned
    cleaned = re.sub(r"(?:\s*[\d]+(?:\s[\d]{3})*(?:[.,]\d+)?)+$", "", cleaned).strip()
    return re.sub(r"[\s.:_\-]+$", "", cleaned).strip()


def section_key(name) -> str:
    """Normalize a section title for equality checks only; never display this."""
    text = clean_section_name(name)
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_DIACRITICS)
    text = text.translate(_LETTER_VARIANTS)
    text = text.translate(_ARABIC_DIGITS)
    text = text.casefold()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _section_similarity(left, right) -> float:
    a, b = section_key(left), section_key(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio() * 100


def same_section(left, right, *, fuzzy: bool = False) -> bool:
    """True when two raw titles refer to the same section."""
    key_a, key_b = section_key(left), section_key(right)
    if key_a and key_a == key_b:
        return True
    if fuzzy and key_a and key_b:
        return _section_similarity(left, right) >= SECTION_FUZZY_THRESHOLD
    return False


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


# Inpatient/outpatient templates keyed by file number and receipt/invoice number.
FILE_INVOICE_TEMPLATES = frozenset({"massara", "musafat"})


def normalize_massara_invoice(data: dict) -> dict:
    """Rename receipt_number → invoice_number on file-and-invoice extracts (in place)."""
    if not isinstance(data, dict):
        return data
    receipt = data.get("receipt")
    if isinstance(receipt, dict) and "receipt_number" in receipt:
        if "invoice_number" not in receipt or receipt.get("invoice_number") in (None, ""):
            receipt["invoice_number"] = receipt.get("receipt_number")
        del receipt["receipt_number"]
    return data


def _invoice_class(document_type) -> str:
    """Inpatient and outpatient stay separate for safwa even when IDs match."""
    if _is_inpatient(document_type):
        return "inpatient"
    if document_type == "outpatient_invoice":
        return "outpatient"
    return ""


def _doc_key(body, template: str | None = None, document_type=None):
    patient = body.get("patient") or {}
    if template in FILE_INVOICE_TEMPLATES:
        receipt = body.get("receipt") or {}
        return (
            _norm(patient.get("file_number")),
            _norm(receipt.get("invoice_number") or receipt.get("receipt_number")),
        )
    key = (_norm(patient.get("admission_no")), _norm(patient.get("medical_no")))
    if template == "safwa":
        return (*key, _invoice_class(document_type))
    return key


def _key_has_ids(key: tuple[str, str]) -> bool:
    return bool(key[0] or key[1])


def _page_sort_key(page) -> tuple[int, int]:
    raw = page.get("page")
    try:
        return (0, int(raw))
    except (TypeError, ValueError):
        return (1, 0)


def _copy_patient_ids(target: dict, source: dict, template: str | None = None) -> None:
    """Overwrite merge-key IDs on target from source (OCR sandwich repair)."""
    src_patient = source.get("patient") if isinstance(source.get("patient"), dict) else {}
    patient = target.get("patient")
    if not isinstance(patient, dict):
        patient = {}
        target["patient"] = patient
    if template in FILE_INVOICE_TEMPLATES:
        patient["file_number"] = src_patient.get("file_number")
        src_receipt = source.get("receipt") if isinstance(source.get("receipt"), dict) else {}
        receipt = target.get("receipt")
        if not isinstance(receipt, dict):
            receipt = {}
            target["receipt"] = receipt
        receipt["invoice_number"] = (
            src_receipt.get("invoice_number")
            if src_receipt.get("invoice_number") not in (None, "")
            else src_receipt.get("receipt_number")
        )
        receipt.pop("receipt_number", None)
        return
    patient["admission_no"] = src_patient.get("admission_no")
    patient["medical_no"] = src_patient.get("medical_no")


def repair_sandwiched_patient_ids(pages: list, template: str | None = None) -> list:
    """If page N-1 and N+1 share IDs but N differs, treat N as the same claim.

    Catches one-page OCR glitches like a blurry medical_no between matching neighbors.
    Only runs when the three pages are consecutive page numbers.
    """
    prepared = []
    for page in pages:
        body = _body(page)
        if not isinstance(body, dict):
            continue
        prepared.append({
            "page": page.get("page"),
            "body": body,
            "key": _doc_key(body, template, page.get("document_type")),
            "document_type": page.get("document_type"),
        })
    prepared.sort(key=lambda item: _page_sort_key({"page": item["page"]}))

    for index in range(1, len(prepared) - 1):
        prev_item = prepared[index - 1]
        curr_item = prepared[index]
        next_item = prepared[index + 1]
        try:
            prev_no = int(prev_item["page"])
            curr_no = int(curr_item["page"])
            next_no = int(next_item["page"])
        except (TypeError, ValueError):
            continue
        if curr_no != prev_no + 1 or next_no != curr_no + 1:
            continue
        neighbor_key = prev_item["key"]
        if not _key_has_ids(neighbor_key):
            continue
        if neighbor_key != next_item["key"]:
            continue
        if curr_item["key"] == neighbor_key:
            continue
        if template == "safwa" and _invoice_class(curr_item["document_type"]) != _invoice_class(prev_item["document_type"]):
            continue
        _copy_patient_ids(curr_item["body"], prev_item["body"], template)
        curr_item["key"] = neighbor_key

    return [
        {
            "page": item["page"],
            "data": item["body"],
            "document_type": item.get("document_type"),
        }
        for item in prepared
    ]

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


_INPATIENT_TYPES = frozenset({"invoice", "inpatient_invoice"})


def _is_inpatient(document_type) -> bool:
    return document_type in _INPATIENT_TYPES


# Masarra ERP codes 321 (InPatient Medicine) and 322 (Pharmacy Medicine) always
# belong under أدوية ومستلزمات on inpatient invoices.
_MASSARA_MEDICINE_CODES = frozenset({"321", "322"})
_MASSARA_MEDICINE_SECTION = "أدوية ومستلزمات"


def _split_massara_medicine_sections(sections: list[dict]) -> list[dict]:
    """Move Masarra inpatient medicine rows (codes 321/322) out of non-medicine sections."""
    medicine_key = section_key(_MASSARA_MEDICINE_SECTION)
    out: list[dict] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        items = section.get("items") or []
        if same_section(section.get("section_name"), _MASSARA_MEDICINE_SECTION):
            out.append(section)
            continue
        kept: list[dict] = []
        moved: list[dict] = []
        for item in items:
            code = _norm(item.get("code")) if isinstance(item, dict) else ""
            if code in _MASSARA_MEDICINE_CODES:
                moved.append(item)
            else:
                kept.append(item)
        if not moved:
            out.append(section)
            continue
        if kept:
            trimmed = copy.deepcopy(section)
            trimmed["items"] = kept
            out.append(trimmed)
        if out and section_key(out[-1].get("section_name")) == medicine_key:
            out[-1]["items"] = (out[-1].get("items") or []) + moved
        else:
            out.append({
                "section_name": _MASSARA_MEDICINE_SECTION,
                "items": moved,
                "section_total": {"net_price": 0.0},
            })
    return out


def _recompute_section_total(section: dict) -> None:
    """Recompute section_total.net_price from item net_prices when items carry numeric prices."""
    items = section.get("items") or []
    prices = [
        float(item["net_price"])
        for item in items
        if isinstance(item, dict) and isinstance(item.get("net_price"), (int, float))
    ]
    if not prices:
        return
    total_block = section.get("section_total")
    if isinstance(total_block, dict) and set(total_block) <= {"net_price"}:
        section["section_total"] = {"net_price": round(sum(prices), 3)}


def _append_sections(doc, sections, *, cross_page: bool = False, blank_continues: bool = False):
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        items = section.get("items") or []
        if not items:
            continue
        section = copy.deepcopy(section)
        section["section_name"] = clean_section_name(section.get("section_name"))
        last = doc["sections"][-1] if doc["sections"] else None
        # A nameless first section on the next inpatient page continues the previous section.
        blank_continuation = (
            blank_continues
            and index == 0
            and last
            and not section_key(section.get("section_name"))
        )
        fuzzy = cross_page and index == 0
        if blank_continuation or (
            last and same_section(last.get("section_name"), section.get("section_name"), fuzzy=fuzzy)
        ):
            doc["sections"][-1] = _merge_section(last, section)
        else:
            doc["sections"].append(section)


def merge_pages(pages, template: str | None = None):
    docs = {}
    for page in repair_sandwiched_patient_ids(pages, template):
        body = page["data"]
        key = _doc_key(body, template, page.get("document_type"))
        page_no = page.get("page")

        if key not in docs:
            doc = {field: copy.deepcopy(value) for field, value in body.items() if field not in SKIP_KEYS}
            doc["pages"] = [page_no] if page_no is not None else []
            doc["sections"] = []
            doc["_document_type"] = page.get("document_type")
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
                    **{kept_key: kept for kept_key, kept in doc[field].items() if kept not in (None, "")},
                }
        blank_continues = (
            template in FILE_INVOICE_TEMPLATES
            and _is_inpatient(doc.get("_document_type"))
            and _is_inpatient(page.get("document_type"))
        )
        _append_sections(doc, body.get("sections") or [], cross_page=True, blank_continues=blank_continues)
        if body.get("summary") is not None:
            doc["summary"] = _merge_dicts(doc.get("summary"), body["summary"])
    merged = []
    for doc in docs.values():
        kind = doc.pop("_document_type", None)
        if template == "massara" and _is_inpatient(kind):
            doc["sections"] = _split_massara_medicine_sections(doc.get("sections") or [])
        if template in FILE_INVOICE_TEMPLATES:
            for section in doc.get("sections") or []:
                if isinstance(section, dict):
                    _recompute_section_total(section)
        if template == "safwa" and kind:
            doc["document_type"] = kind
        merged.append(doc)
    return merged
