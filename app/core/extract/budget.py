"""Image-token budget for the NuExtract3 vision preprocessor.

The numbers match numind/NuExtract3-FP8 processor_config.json:
Qwen2VLImageProcessorFast, patch_size 16, merge_size 2,
shortest_edge 65536, longest_edge 16777216.
Token count is the Qwen2-VL merged grid:
    (resized_h / patch) * (resized_w / patch) / merge^2
"""
from __future__ import annotations

import json
import math
from io import BytesIO

from PIL import Image

from app.config import settings


class ContextOverflow(RuntimeError):
    """The image cannot be shrunk enough to fit max-model-len."""


# PDF pages are rendered at 300 DPI. The files dropped on the extract worker
# do not carry a DPI tag, so a missing tag means that render resolution.
RENDER_DPI = 300
VLLM_IMAGE_DPI = 200


def image_dpi(image: Image.Image) -> float | None:
    raw = image.info.get("dpi")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, tuple):
        values = [float(value) for value in raw if value]
        if values:
            return max(values)
    return None


def cap_image_dpi(image: Image.Image, dpi: float, max_dpi: float = VLLM_IMAGE_DPI) -> Image.Image:
    """Downscale an image whose DPI is above the resolution sent to vLLM."""
    if dpi <= max_dpi:
        return image
    scale = max_dpi / dpi
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    if (width, height) == image.size:
        return image
    return image.resize((width, height), Image.Resampling.LANCZOS)


def smart_resize(height: int, width: int, factor: int, min_pixels: int, max_pixels: int) -> tuple[int, int]:
    """Snap a size to the vision grid the way Qwen2VLImageProcessor does."""
    if height < 1 or width < 1:
        raise ValueError("Image dimensions must be positive")
    if max(height, width) / min(height, width) > 200:
        raise ValueError("Image aspect ratio exceeds the vision processor limit")

    height_bar = max(factor, round(height / factor) * factor)
    width_bar = max(factor, round(width / factor) * factor)
    if height_bar * width_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        height_bar = max(factor, math.floor(height / beta / factor) * factor)
        width_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif height_bar * width_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        height_bar = math.ceil(height * beta / factor) * factor
        width_bar = math.ceil(width * beta / factor) * factor
    return height_bar, width_bar


def image_token_count(width: int, height: int) -> int:
    factor = settings.VISION_PATCH_SIZE * settings.VISION_MERGE_SIZE
    resized_h, resized_w = smart_resize(
        height,
        width,
        factor,
        settings.VISION_MIN_PIXELS,
        settings.VISION_MAX_PIXELS,
    )
    merge_area = settings.VISION_MERGE_SIZE ** 2
    return (resized_h // settings.VISION_PATCH_SIZE) * (resized_w // settings.VISION_PATCH_SIZE) // merge_area


def template_token_count(template: dict) -> int:
    """Conservative text estimate. Over-counting shrinks the image instead of overflowing."""
    encoded = json.dumps(template, ensure_ascii=False)
    return (len(encoded) // 2) + settings.CHAT_TOKEN_OVERHEAD


def prompt_budget(template: dict, max_tokens: int) -> int:
    return template_token_count(template) + max_tokens


def fits(width: int, height: int, template: dict, max_tokens: int) -> bool:
    return image_token_count(width, height) + prompt_budget(template, max_tokens) <= settings.VLLM_MAX_MODEL_LEN


def encode_image_for_request(image: Image.Image, template: dict, max_tokens: int) -> str:
    """Shrink until image tokens plus the template plus max_tokens fit, then return JPEG base64."""
    import base64

    current = image.convert("RGB")
    factor = settings.VISION_PATCH_SIZE * settings.VISION_MERGE_SIZE
    while not fits(current.width, current.height, template, max_tokens):
        next_w = max(factor, int(current.width * 0.85))
        next_h = max(factor, int(current.height * 0.85))
        if next_w == current.width and next_h == current.height:
            raise ContextOverflow(
                f"Image {current.width}x{current.height} still exceeds max-model-len "
                f"{settings.VLLM_MAX_MODEL_LEN} with max_tokens={max_tokens}"
            )
        current = current.resize((next_w, next_h), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    current.save(buffer, format="JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")
