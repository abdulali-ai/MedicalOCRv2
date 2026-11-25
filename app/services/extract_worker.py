"""Dispatch NuExtract calls with bounded concurrency and merge a PDF when every page is terminal."""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from pathlib import Path

import httpx
import redis
import redis.asyncio as aioredis

from app.config import settings
from app.core.extract.budget import CLASSIFY_IMAGE_DPI, VLLM_IMAGE_DPI
from app.core.extract.client import ExtractError, extract_image, extract_text
from app.core.extract.examples import classifier_examples, id_card_examples, invoice_examples
from app.core.extract.parse import invoice_is_empty
from app.core.extract.templates import TemplateError, exact_label, load_registry
from app.core.merge.pages import FILE_INVOICE_TEMPLATES, normalize_massara_invoice
from app.core.jobs.state import JobStore
from app.core.notifications import publish_update


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_TOKENS = {
    "classify": settings.MAX_TOKENS_CLASSIFY,
    "id_card": settings.MAX_TOKENS_ID_CARD,
    "invoice": settings.MAX_TOKENS_INVOICE,
}


async def _reclaim(client, stream: str, group: str, consumer: str, count: int):
    try:
        return await client.xautoclaim(
            stream,
            group,
            consumer,
            min_idle_time=settings.EXTRACT_CLAIM_IDLE_MS,
            start_id="0-0",
            count=max(1, count),
        )
    except redis.exceptions.ResponseError:
        return None


async def run() -> None:
    registry = load_registry()
    store = JobStore()
    store.ensure_group(settings.STREAM_EXTRACT, settings.GROUP_EXTRACT)
    max_concurrency = max(1, int(settings.MAX_CONCURRENT_EXTRACTS))
    semaphore = asyncio.Semaphore(max_concurrency)
    logger.info(
        "Extract dispatcher model=%s max_model_len=%s max_concurrency=%s mtp=off",
        settings.VLLM_MODEL_NAME,
        settings.VLLM_MAX_MODEL_LEN,
        max_concurrency,
    )
    async_redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    inflight: set[asyncio.Task] = set()
    inflight_ids: set[str] = set()
    timeout = httpx.Timeout(settings.VLLM_TIMEOUT_SECONDS)
    limits = httpx.Limits(
        max_connections=max_concurrency * 2,
        max_keepalive_connections=max_concurrency,
    )
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as http:
        while True:
            if len(inflight) >= max_concurrency:
                done, _pending = await asyncio.wait(inflight, return_when=asyncio.FIRST_COMPLETED)
                inflight.difference_update(done)

            available = max(1, max_concurrency - len(inflight))
            claimed = await _reclaim(
                async_redis,
                settings.STREAM_EXTRACT,
                settings.GROUP_EXTRACT,
                consumer,
                count=available,
            )
            if claimed:
                _next, messages, *_rest = claimed
                for message_id, fields in messages or []:
                    _spawn(inflight, inflight_ids, semaphore, store, registry, http, async_redis, message_id, fields)

            available = max_concurrency - len(inflight)
            if available <= 0:
                continue

            try:
                response = await async_redis.xreadgroup(
                    groupname=settings.GROUP_EXTRACT,
                    consumername=consumer,
                    streams={settings.STREAM_EXTRACT: ">"},
                    count=available,
                    block=2000,
                )
            except redis.TimeoutError:
                continue
            if not response:
                if inflight:
                    done, _pending = await asyncio.wait(inflight, timeout=0.2)
                    inflight.difference_update(done)
                continue
            for _stream, messages in response:
                for message_id, fields in messages:
                    _spawn(inflight, inflight_ids, semaphore, store, registry, http, async_redis, message_id, fields)


def _spawn(inflight, inflight_ids, semaphore, store, registry, http, async_redis, message_id, fields) -> bool:
    if message_id in inflight_ids:
        logger.debug("Skipping reclaim of already in-flight extract message %s", message_id)
        return False
    inflight_ids.add(message_id)
    task = asyncio.create_task(
        _process_and_ack(semaphore, store, registry, http, async_redis, message_id, fields)
    )
    inflight.add(task)

    def _done(finished_task: asyncio.Task) -> None:
        inflight.discard(finished_task)
        inflight_ids.discard(message_id)

    task.add_done_callback(_done)
    return True


async def _process_and_ack(semaphore, store, registry, http, async_redis, message_id, fields) -> None:
    async with semaphore:
        try:
            await process_message(store, registry, http, fields)
            await async_redis.xack(settings.STREAM_EXTRACT, settings.GROUP_EXTRACT, message_id)
        except Exception as exc:
            logger.exception("Extract message %s failed before ack", message_id)
            deliveries = await _deliveries(async_redis, message_id)
            if deliveries >= settings.MAX_DELIVERIES:
                task_id = (fields or {}).get("task_id")
                if task_id:
                    await asyncio.to_thread(store.fail_job, task_id, f"extract failed: {exc}")
                await async_redis.xack(settings.STREAM_EXTRACT, settings.GROUP_EXTRACT, message_id)


async def _deliveries(client, message_id: str) -> int:
    try:
        found = await client.xpending_range(
            settings.STREAM_EXTRACT,
            settings.GROUP_EXTRACT,
            min=message_id,
            max=message_id,
            count=1,
        )
    except redis.exceptions.ResponseError:
        return 1
    if not found:
        return 1
    return int(found[0].get("times_delivered") or 1)


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
    max_attempts = max(1, int(settings.EXTRACT_ATTEMPTS))
    base_backoff = max(0.0, float(settings.EXTRACT_RETRY_BACKOFF_SECONDS))
    for attempt in range(max_attempts):
        try:
            parsed = await _call_extract(http, fields, job, stage, template)
            last_error = None
            break
        except (ExtractError, httpx.HTTPError, OSError) as exc:
            last_error = exc
            logger.warning(
                "page %s stage %s attempt %s/%s failed: %s",
                page,
                stage,
                attempt + 1,
                max_attempts,
                exc,
            )
            if attempt + 1 < max_attempts and base_backoff > 0:
                await asyncio.sleep(base_backoff * (2 ** attempt))
    if last_error is not None:
        await asyncio.to_thread(_finish_error, store, task_id, page, fields, str(last_error))
        return

    if stage == "classify":
        await asyncio.to_thread(
            _route_classification,
            store,
            task_id,
            page,
            fields,
            job,
            schema,
            parsed or {},
        )
        return
    if _is_invoice_stage(stage):
        data = parsed or {}
        template_name = fields.get("template_name") or job.get("template") or "janzour"
        if invoice_is_empty(data):
            logger.warning("page %s stage %s returned an empty invoice; retrying", page, stage)
            if base_backoff > 0:
                await asyncio.sleep(base_backoff)
            try:
                data = await _call_extract(http, fields, job, stage, template) or {}
            except (ExtractError, httpx.HTTPError, OSError) as exc:
                await asyncio.to_thread(_finish_error, store, task_id, page, fields, f"empty invoice: {exc}")
                return
            if invoice_is_empty(data):
                finished = await asyncio.to_thread(
                    store.complete_page,
                    task_id,
                    page,
                    {"kind": "error", "page": page, "message": "empty invoice", "document_type": stage},
                )
                if finished:
                    _page_done(task_id, job, page, "error")
                return
        if template_name in FILE_INVOICE_TEMPLATES:
            data = normalize_massara_invoice(data)
        finished = await asyncio.to_thread(
            store.complete_page,
            task_id,
            page,
            {"kind": "invoice", "page": page, "data": data, "document_type": stage},
        )
        if finished:
            _page_done(task_id, job, page, stage)
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


def _is_invoice_stage(stage: str) -> bool:
    return stage == "invoice" or stage.endswith("_invoice")


async def _call_extract(http, fields: dict, job: dict, stage: str, template: dict):
    template_name = fields.get("template_name") or job.get("template") or "janzour"
    page = int(fields.get("page") or 0)
    instructions = None
    if stage == "classify":
        raw = template.get("instructions")
        instructions = raw.strip() if isinstance(raw, str) and raw.strip() else None
        template = {"document_type": list(template["document_type"])}
    elif _is_invoice_stage(stage):
        template = dict(template)
        raw = template.pop("instructions", None)
        instructions = raw.strip() if isinstance(raw, str) and raw.strip() else None

    if fields.get("input_mode") == "text":
        text_path = fields.get("text_path") or ""
        if not text_path:
            raise ExtractError(f"page {page} missing text_path for text extraction")
        text = await asyncio.to_thread(Path(text_path).read_text, "utf-8")
        return await extract_text(
            http,
            text,
            template,
            _max_tokens_for(stage),
            instructions=instructions,
            page_id=page,
        )

    if stage == "classify":
        examples = classifier_examples(template_name) or None
    elif _is_invoice_stage(stage):
        examples = invoice_examples(template_name, stage) or None
    elif stage == "id_card":
        examples = id_card_examples(template_name) or None
    else:
        examples = None

    return await extract_image(
        http,
        fields["image_path"],
        template,
        _max_tokens_for(stage),
        examples=examples,
        instructions=instructions,
        page_id=page,
        guard_query=_is_invoice_stage(stage),
        max_dpi=CLASSIFY_IMAGE_DPI if stage == "classify" else VLLM_IMAGE_DPI,
    )


def _max_tokens_for(stage: str) -> int:
    if stage in MAX_TOKENS:
        return MAX_TOKENS[stage]
    if _is_invoice_stage(stage):
        return settings.MAX_TOKENS_INVOICE
    return settings.MAX_TOKENS_ID_CARD


def _template_for(stage: str, schema: dict) -> dict:
    if stage == "classify":
        return schema["classify"]
    if stage in schema and isinstance(schema[stage], dict) and stage != "classify":
        return schema[stage]
    if _is_invoice_stage(stage):
        return schema["invoice"]
    return schema["id_card"]


def _route_classification(
    store: JobStore,
    task_id: str,
    page: int,
    fields: dict,
    job: dict,
    schema: dict,
    parsed: dict,
) -> None:
    labels = schema["classify"]["document_type"]
    label = exact_label(parsed.get("document_type"), labels)
    # Plain "invoice" plus variants like doctor_invoice / inpatient_invoice.
    # Prefer a label-specific schema when the template defines one.
    if label == "invoice" or label.endswith("_invoice"):
        stage = label if label in schema else "invoice"
        store.enqueue(settings.STREAM_EXTRACT, {
            "task_id": task_id,
            "page": page,
            "stage": stage,
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
