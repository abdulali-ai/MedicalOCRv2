"""One NuExtract image call. The caller does not wait before sending the next image."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from PIL import Image

from app.config import settings
from app.core.extract.budget import ContextOverflow, encode_image_for_request
from app.core.extract.parse import safe_parse_json


class ExtractError(RuntimeError):
    pass


async def extract_image(
    client: httpx.AsyncClient,
    image_path: str,
    template: dict,
    max_tokens: int,
) -> dict:
    image = Image.open(image_path)
    try:
        encoded = encode_image_for_request(image, template, max_tokens)
    except ContextOverflow as exc:
        raise ExtractError(str(exc)) from exc
    finally:
        image.close()

    body = {
        "model": settings.VLLM_MODEL_NAME,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    }
                ],
            }
        ],
        "chat_template_kwargs": {
            "template": json.dumps(template, ensure_ascii=False),
            "enable_thinking": False,
        },
    }
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
