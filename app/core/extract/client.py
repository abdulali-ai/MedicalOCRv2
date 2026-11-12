"""One NuExtract image call. The caller does not wait before sending the next image."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path

import httpx
from PIL import Image

from app.config import settings
from app.core.extract.budget import (
    RENDER_DPI,
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
    import base64
    from io import BytesIO

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


async def extract_image(
    client: httpx.AsyncClient,
    image_path: str,
    template: dict,
    max_tokens: int,
    examples: ExampleList | None = None,
    trace_page: int | None = None,
) -> dict:
    opened = Image.open(image_path)
    image = opened
    try:
        dpi = image_dpi(opened)
        image = cap_image_dpi(opened, RENDER_DPI if dpi is None else dpi)
        if examples and isinstance(examples[0], InvoiceExample):
            query = _fit_query_with_examples(image, template, max_tokens, examples, "Invoice")
            encoded = _jpeg_base64(query)
        elif examples and isinstance(examples[0], IdCardExample):
            query = _fit_query_with_examples(image, template, max_tokens, examples, "ID-card")
            encoded = _jpeg_base64(query)
        elif examples:
            query = _fit_query_with_examples(image, template, max_tokens, examples, "Classifier")
            encoded = _jpeg_base64(query)
        else:
            encoded = encode_image_for_request(image, template, max_tokens)
    except ContextOverflow as exc:
        raise ExtractError(str(exc)) from exc
    finally:
        opened.close()
        if image is not opened:
            image.close()

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
        "chat_template_kwargs": {
            "template": json.dumps(template, ensure_ascii=False),
            "enable_thinking": False,
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
    try:
        content = payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ExtractError(f"Unexpected vLLM response: {exc}") from exc
    return safe_parse_json(content)


def image_exists(path: str) -> bool:
    return bool(path) and Path(path).is_file()
