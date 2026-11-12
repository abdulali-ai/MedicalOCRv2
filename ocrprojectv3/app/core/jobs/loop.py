"""Blocking stream consumer used by the PDF and vision workers."""
from __future__ import annotations

import logging
import os
import socket

import redis

from app.config import settings
from app.core.jobs.state import JobStore


logger = logging.getLogger(__name__)


def run_consumer(store: JobStore, stream: str, group: str, handler) -> None:
    store.ensure_group(stream, group)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    client = store.redis
    logger.info("Consuming %s as %s/%s", stream, group, consumer)
    while True:
        _reclaim(store, client, stream, group, consumer, handler)
        response = client.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=1,
            block=5000,
        )
        if not response:
            continue
        for _stream, messages in response:
            for message_id, fields in messages:
                _handle(store, client, stream, group, message_id, fields, handler)


def _reclaim(store: JobStore, client: redis.Redis, stream: str, group: str, consumer: str, handler) -> None:
    try:
        _next, claimed, *_rest = client.xautoclaim(
            stream,
            group,
            consumer,
            min_idle_time=settings.STREAM_CLAIM_IDLE_MS,
            start_id="0-0",
            count=5,
        )
    except redis.ResponseError:
        return
    for message_id, fields in claimed or []:
        _handle(store, client, stream, group, message_id, fields, handler)


def _handle(store, client, stream, group, message_id, fields, handler) -> None:
    try:
        handler(fields)
        client.xack(stream, group, message_id)
    except Exception as exc:
        deliveries = _deliveries(client, stream, group, message_id)
        logger.exception("Worker failed on %s %s (delivery %s)", stream, message_id, deliveries)
        task_id = (fields or {}).get("task_id")
        if deliveries >= settings.MAX_DELIVERIES and task_id:
            store.fail_job(task_id, f"{stream} failed: {exc}")
            client.xack(stream, group, message_id)


def _deliveries(client, stream, group, message_id) -> int:
    try:
        found = client.xpending_range(stream, group, min=message_id, max=message_id, count=1)
    except redis.ResponseError:
        return 1
    if not found:
        return 1
    return int(found[0].get("times_delivered") or 1)
