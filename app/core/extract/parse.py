"""Parse a NuExtract completion into a JSON object."""
import json
import re


_PRICE_KEYS = ("net_price", "unit_price", "patient_share", "payer_share")


def safe_parse_json(output_str: str) -> dict:
    cleaned = (output_str or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned).strip()
    cleaned = re.sub(r"<reasoning>[\s\S]*?</reasoning>", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _has_nonzero_number(node) -> bool:
    if isinstance(node, bool) or node is None:
        return False
    if isinstance(node, (int, float)):
        return float(node) != 0.0
    if isinstance(node, str):
        try:
            return float(node.strip()) != 0.0
        except ValueError:
            return False
    if isinstance(node, dict):
        return any(_has_nonzero_number(val) for val in node.values())
    if isinstance(node, list):
        return any(_has_nonzero_number(val) for val in node)
    return False


def invoice_is_empty(data) -> bool:
    """True when an invoice extract has no usable field, section, or non-zero amount."""
    if not isinstance(data, dict) or not _has_content(data):
        return True

    sections = data.get("sections")
    summary = data.get("summary")
    if isinstance(sections, list):
        items = [
            item
            for sec in sections
            if isinstance(sec, dict)
            for item in (sec.get("items") or [])
            if isinstance(item, dict)
        ]
        if not items and not _has_nonzero_number(summary):
            return True
        if items and any(key in item for item in items for key in _PRICE_KEYS):
            if not _has_nonzero_number(sections) and not _has_nonzero_number(summary):
                return True

    return False


def _has_content(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, dict):
        return any(_has_content(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_content(item) for item in value)
    return True
