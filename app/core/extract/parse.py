"""Parse a NuExtract completion into a JSON object."""
import json
import re


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


def invoice_is_empty(data) -> bool:
    """True when an invoice extract has no usable field, section, or amount."""
    return not _has_content(data)


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
