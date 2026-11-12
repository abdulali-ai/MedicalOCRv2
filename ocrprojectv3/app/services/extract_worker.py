"""Dispatch NuExtract calls concurrently and merge a PDF when every page is terminal."""
from __future__ import annotations

import asyncio
import logging
import os
import socket

import httpx
import redis.asyncio as aioredis

from app.config import settings
from app.core.extract.client import ExtractError, extract_image
from app.core.extract.templates import TemplateError, classify_template, exact_label, load_registry
from app.core.jobs.state import JobStore
from app.core.notifications import publish_update


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_TOKENS = {
    "classify": settings.MAX_TOKENS_CLASSIFY,
    "id_card": settings.MAX_TOKENS_ID_CARD,
    "invoice": settings.MAX_TOKENS_INVOICE,
}


async def _reclaim(client, stream: str, group: str, consumer: str):
    try:
        return await client.xautoclaim(
            stream,
            group,
            consumer,
            min_idle_time=settings.STREAM_CLAIM_IDLE_MS,
            start_id="0-0",
        )
    except redis.exceptions.ResponseError:
        return None


async def run() -> None:
    registry = load_registry()
    store = JobStore()
    store.ensure_group(settings.STREAM_EXTRACT, settings.GROUP_EXTRACT)
    logger.info(
        "Extract dispatcher model=%s max_model_len=%s mtp=off",
        settings.VLLM_MODEL_NAME,
        settings.VLLM_MAX_MODEL_LEN,
    )
    async_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    inflight: set[asyncio.Task] = set()
    timeout = httpx.Timeout(settings.VLLM_TIMEOUT_SECONDS)
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as http:
        while True:
            claimed = await _reclaim(async_redis, settings.STREAM_EXTRACT, settings.GROUP_EXTRACT, consumer)
            if claimed:
                _next, messages, *_rest = claimed
                for message_id, fields in messages or []:
                    _spawn(inflight, store, registry, http, async_redis, message_id, fields)
            response = await async_redis.xreadgroup(
                groupname=settings.GROUP_EXTRACT,
                consumername=consumer,
                streams={settings.STREAM_EXTRACT: ">"},
                block=2000,
            )
            if not response:
                if inflight:
                    done, _pending = await asyncio.wait(inflight, timeout=0.2)
                    inflight.difference_update(done)
                continue
            for _stream, messages in response:
                for message_id, fields in messages:
                    _spawn(inflight, store, registry, http, async_redis, message_id, fields)


def _spawn(inflight, store, registry, http, async_redis, message_id, fields) -> None:
    task = asyncio.create_task(
        _process_and_ack(store, registry, http, async_redis, message_id, fields)
    )
    inflight.add(task)
    task.add_done_callback(inflight.discard)


async def _process_and_ack(store, registry, http, async_redis, message_id, fields) -> None:
    try:
        await process_message(store, registry, http, fields)
        await async_redis.xack(settings.STREAM_EXTRACT, settings.GROUP_EXTRACT, message_id)
    except Exception:
        logger.exception("Extract message %s failed before ack", message_id)


async def process_message(store: JobStore, registry, http: httpx.AsyncClient, fields: dict) -> None:
    task_id = fields["task_id"]
    page = int(fields["page"])
    stage = fields["stage"]
    job = await asyncio.to_thread(store.read_job, task_id)
    if not job or job.get("status") in {"error", "completed"}:
        return

    template_name = fields.get("template_name") or job.get("template")
    try:
        schema = registry.get(template_name)
    except TemplateError as exc:
        await asyncio.to_thread(
            store.complete_page,
            task_id,
            page,
            {"kind": "error", "page": page, "message": str(exc)},
        )
        return
    template = _template_for(stage, schema)
    last_error = None
    parsed = None
    for _attempt in range(settings.EXTRACT_ATTEMPTS):
        try:
            parsed = await extract_image(http, fields["image_path"], template, MAX_TOKENS[stage])
            last_error = None
            break
        except (ExtractError, httpx.HTTPError, OSError) as exc:
            last_error = exc
            logger.warning("page %s stage %s attempt failed: %s", page, stage, exc)
    if last_error is not None:
        await asyncio.to_thread(_finish_error, store, task_id, page, fields, str(last_error))
        return

    if stage == "classify":
        await asyncio.to_thread(_route_classification, store, task_id, page, fields, job, parsed or {})
        return
    if stage == "invoice":
        finished = await asyncio.to_thread(
            store.complete_page,
            task_id,
            page,
            {"kind": "invoice", "page": page, "data": parsed or {}},
        )
        if finished:
            _page_done(task_id, job, page, "invoice")
        return

    finished = await asyncio.to_thread(
        store.store_crop_and_maybe_finish,
        task_id,
        page,
        int(fields.get("crop_index") or 0),
        {"source": fields.get("source") or "detector", "data": parsed or {}},
        int(fields.get("crop_count") or 1),
        [],
    )
    if finished:
        _page_done(task_id, job, page, "id_card")


def _template_for(stage: str, schema: dict) -> dict:
    if stage == "classify":
        return classify_template
    if stage == "invoice":
        return schema["invoice"]
    return schema["id_card"]


def _route_classification(store: JobStore, task_id: str, page: int, fields: dict, job: dict, parsed: dict) -> None:
    label = exact_label(parsed.get("document_type"))
    if label == "invoice":
        store.enqueue(settings.STREAM_EXTRACT, {
            "task_id": task_id,
            "page": page,
            "stage": "invoice",
            "image_path": fields["full_page_path"],
            "full_page_path": fields["full_page_path"],
            "template_name": fields.get("template_name") or job.get("template"),
            "source": "",
            "crop_index": 0,
            "crop_count": 1,
        })
        return
    if label == "id_card":
        store.enqueue(settings.STREAM_EXTRACT, {
            "task_id": task_id,
            "page": page,
            "stage": "id_card",
            "image_path": fields["full_page_path"],
            "full_page_path": fields["full_page_path"],
            "template_name": fields.get("template_name") or job.get("template"),
            "source": "classifier",
            "crop_index": 0,
            "crop_count": 1,
        })
        return
    finished = store.complete_page(task_id, page, {
        "kind": "other",
        "page": page,
        "document_type": label,
    })
    if finished:
        _page_done(task_id, job, page, label)


def _finish_error(store: JobStore, task_id: str, page: int, fields: dict, message: str) -> None:
    stage = fields["stage"]
    if stage == "id_card" and int(fields.get("crop_count") or 1) > 1:
        store.store_crop_and_maybe_finish(
            task_id,
            page,
            int(fields.get("crop_index") or 0),
            {"source": fields.get("source") or "detector", "error": message},
            int(fields.get("crop_count") or 1),
            [],
        )
        return
    store.complete_page(task_id, page, {"kind": "error", "page": page, "message": message})


def _page_done(task_id: str, job: dict, page: int, label: str) -> None:
    publish_update(task_id, {
        "task_id": task_id,
        "filename": job.get("filename"),
        "batch_id": job.get("batch_id") or None,
        "template": job.get("template"),
        "event": "step_completed",
        "data": {"step": "extraction", "page": page, "label": label},
    })


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
