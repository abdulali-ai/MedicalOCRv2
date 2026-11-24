"""One NuExtract image call. The caller does not wait before sending the next image."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

from app.config import settings
from app.core.extract.budget import (
    RENDER_DPI,
    VLLM_IMAGE_DPI,
    ContextOverflow,
    cap_image_dpi,
    encode_image_for_request,
    image_dpi,
    image_token_count,
    template_token_count,
)
from app.core.extract.examples import ClassifierExample, IdCardExample, InvoiceExample
from app.core.extract.parse import safe_parse_json


class ExtractError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

# A full page capped at 200 DPI is about 1700×2200. The silent shrink that
# produced 640×827 stayed hidden because nothing checked the dimensions after fit.
MIN_QUERY_LONG_EDGE = 1600

# Longer names first so "verbatim-string" wins over "string" and "date-time" over "date".
_TYPE_NAMES = (
    "verbatim-string",
    "date-time",
    "integer",
    "number",
    "string",
    "boolean",
    "date",
    "time",
)


_SCHEMA_TYPES = {
    "verbatim-string": {"type": "string"},
    "string": {"type": "string"},
    "date": {"type": "string"},
    "time": {"type": "string"},
    "date-time": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
}


def template_to_json_schema(node):
    """Turn a NuExtract template into a JSON Schema xgrammar can enforce.

    A list of strings is an enum, as in classify.document_type. A one-item
    list of objects is an array. A bare null accepts string, number, or null
    because the template does not name a type for that field.
    """
    if isinstance(node, dict):
        return {
            "type": "object",
            "properties": {key: template_to_json_schema(value) for key, value in node.items()},
            "required": list(node),
            "additionalProperties": False,
        }
    if isinstance(node, list):
        if len(node) == 1 and isinstance(node[0], dict):
            return {"type": "array", "items": template_to_json_schema(node[0])}
        if node and all(isinstance(item, str) for item in node):
            return {"type": "string", "enum": list(node)}
        return {"type": "array"}
    if isinstance(node, str):
        return dict(_SCHEMA_TYPES.get(node, {"type": "string"}))
    if isinstance(node, bool):
        return {"type": "boolean"}
    if isinstance(node, int):
        return {"type": "integer"}
    if isinstance(node, float):
        return {"type": "number"}
    if node is None:
        return {"type": ["string", "number", "null"]}
    return {"type": "string"}


def separate_field_notes(template: dict) -> tuple[dict, str | None]:
    """Turn "verbatim-string. Do not return null." into a type plus an instruction.

    A note written after the type is not a type. NuExtract only reads instructions
    from the instructions prompt, so the note has to be sent there.
    """
    notes: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        if isinstance(node, str):
            for name in _TYPE_NAMES:
                prefix = name + "."
                if node.startswith(prefix):
                    note = node[len(prefix):].strip()
                    if note:
                        notes.append(note)
                    return name
        return node

    cleaned = walk(template)
    return cleaned, " ".join(notes) or None


def request_fingerprint(body: dict) -> dict:
    """Hash the JPEG and the message payload exactly as httpx serializes them."""
    messages = body["messages"]
    url = messages[-1]["content"][0]["image_url"]["url"]
    marker = "base64,"
    jpeg = base64.b64decode(url[url.index(marker) + len(marker):])
    serialized = json.dumps(
        {"messages": messages, "chat_template_kwargs": body["chat_template_kwargs"]},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "query_jpeg_sha256": hashlib.sha256(jpeg).hexdigest(),
        "messages_sha256": hashlib.sha256(serialized).hexdigest(),
        "message_count": len(messages),
        "message_roles": [message["role"] for message in messages],
    }


ExampleList = list[ClassifierExample] | list[InvoiceExample] | list[IdCardExample]


def _image_part(encoded: str) -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }


def _jpeg_base64(image: Image.Image) -> str:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _example_text_tokens(examples: ExampleList) -> int:
    return sum(len(item.output) // 2 for item in examples)


def _fit_query_with_examples(
    image: Image.Image,
    template: dict,
    max_tokens: int,
    examples: ExampleList,
    stage: str,
) -> Image.Image:
    """Shrink the page only when packed examples would overflow the context."""
    current = image.convert("RGB")
    reserved = (
        sum(item.tokens for item in examples)
        + _example_text_tokens(examples)
        + template_token_count(template)
        + max_tokens
    )
    factor = settings.VISION_PATCH_SIZE * settings.VISION_MERGE_SIZE
    while image_token_count(current.width, current.height) + reserved > settings.VLLM_MAX_MODEL_LEN:
        next_w = max(factor, int(current.width * 0.85))
        next_h = max(factor, int(current.height * 0.85))
        if next_w == current.width and next_h == current.height:
            raise ContextOverflow(
                f"{stage} prompt still exceeds max-model-len {settings.VLLM_MAX_MODEL_LEN} "
                f"with {len(examples)} examples"
            )
        current = current.resize((next_w, next_h), Image.Resampling.LANCZOS)
    return current


def _prepare_encoded_query(
    image_path: str,
    template: dict,
    max_tokens: int,
    examples: ExampleList | None,
    page_id: int | None,
    guard_query: bool,
    max_dpi: float = VLLM_IMAGE_DPI,
) -> str:
    """Open, downscale/fit to context budget, guard resolution, and JPEG-encode off the event loop."""
    opened = Image.open(image_path)
    image = opened
    try:
        dpi = image_dpi(opened)
        image = cap_image_dpi(opened, RENDER_DPI if dpi is None else dpi, max_dpi=max_dpi)
        if examples and isinstance(examples[0], InvoiceExample):
            query = _fit_query_with_examples(image, template, max_tokens, examples, "Invoice")
            if guard_query and max(query.size) < MIN_QUERY_LONG_EDGE:
                raise ExtractError(f"page {page_id} downscaled to {query.size}, refusing to send")
            return _jpeg_base64(query)
        if examples and isinstance(examples[0], IdCardExample):
            query = _fit_query_with_examples(image, template, max_tokens, examples, "ID-card")
            return _jpeg_base64(query)
        if examples:
            query = _fit_query_with_examples(image, template, max_tokens, examples, "Classifier")
            return _jpeg_base64(query)
        if guard_query and max(image.size) < MIN_QUERY_LONG_EDGE:
            raise ExtractError(f"page {page_id} downscaled to {image.size}, refusing to send")
        return encode_image_for_request(image, template, max_tokens)
    except ContextOverflow as exc:
        raise ExtractError(str(exc)) from exc
    finally:
        opened.close()
        if image is not opened:
            image.close()


async def extract_image(
    client: httpx.AsyncClient,
    image_path: str,
    template: dict,
    max_tokens: int,
    examples: ExampleList | None = None,
    trace_page: int | None = None,
    instructions: str | None = None,
    page_id: int | None = None,
    guard_query: bool = False,
    max_dpi: float = VLLM_IMAGE_DPI,
) -> dict:
    encoded = await asyncio.to_thread(
        _prepare_encoded_query,
        image_path,
        template,
        max_tokens,
        examples,
        page_id if page_id is not None else trace_page,
        guard_query,
        max_dpi,
    )

    template, field_notes = separate_field_notes(template)
    extra = (instructions or "").strip()
    instructions = " ".join(part for part in (field_notes, extra) if part) or None
    messages: list[dict] = []
    for example in examples or []:
        messages.append({
            "role": "developer",
            "content": [
                _image_part(example.encoded),
                {"type": "text", "text": example.output},
            ],
        })
    messages.append({
        "role": "user",
        "content": [_image_part(encoded)],
    })

    body = {
        "model": settings.VLLM_MODEL_NAME,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "extract",
                "schema": template_to_json_schema(template),
            },
        },
        "chat_template_kwargs": {
            "template": json.dumps(template, ensure_ascii=False),
            "enable_thinking": False,
            **({"instructions": instructions} if instructions else {}),
        },
    }
    if trace_page is not None:
        fingerprint = request_fingerprint(body)
        logger.info(
            "live-request page=%s query_jpeg_sha256=%s messages_sha256=%s message_count=%s roles=%s",
            trace_page,
            fingerprint["query_jpeg_sha256"],
            fingerprint["messages_sha256"],
            fingerprint["message_count"],
            ",".join(fingerprint["message_roles"]),
        )
    response = await client.post(settings.vllm_chat_url, json=body)
    if response.status_code >= 400:
        raise ExtractError(f"vLLM {response.status_code}: {response.text[:500]}")
    payload = response.json()
    usage = payload.get("usage") if isinstance(payload, dict) else None
    logger.info(
        "page=%s prompt_tokens=%s",
        page_id if page_id is not None else trace_page,
        (usage or {}).get("prompt_tokens"),
    )
    try:
        content = payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ExtractError(f"Unexpected vLLM response: {exc}") from exc
    return safe_parse_json(content)


def image_exists(path: str) -> bool:
    return bool(path) and Path(path).is_file()
