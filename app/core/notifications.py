"""Redis Pub/Sub notifications and shared async Redis connection management."""
from __future__ import annotations

import json
import logging
import redis
from redis import asyncio as aioredis

from app.config import settings


logger = logging.getLogger(__name__)

# Shared sync Redis client for background workers
_redis_sync: redis.Redis | None = None


def get_redis_sync() -> redis.Redis:
    global _redis_sync
    if _redis_sync is None:
        _redis_sync = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_sync


def get_async_redis(app) -> aioredis.Redis:
    """Return the shared async Redis client from app.state, initializing lazily if needed."""
    client = getattr(app.state, "redis", None)
    if client is None:
        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        app.state.redis = client
    return client


def task_channel(task_id: str) -> str:
    return f"task_updates:{task_id}"


def batch_channel(batch_id: str) -> str:
    return f"batch_updates:{batch_id}"


def publish_update(task_id: str, message: dict) -> None:
    """Publish a task update (and batch update when batch_id is present) synchronously."""
    r = get_redis_sync()
    payload = json.dumps(message, ensure_ascii=False)
    try:
        r.publish(task_channel(task_id), payload)
        batch_id = message.get("batch_id")
        if batch_id:
            r.publish(batch_channel(str(batch_id)), payload)
    except Exception as exc:
        logger.warning("Failed to publish update for task %s: %s", task_id, exc)


async def async_publish_update(redis_conn: aioredis.Redis, task_id: str, message: dict) -> None:
    """Publish a task update (and batch update when batch_id is present) asynchronously."""
    payload = json.dumps(message, ensure_ascii=False)
    try:
        await redis_conn.publish(task_channel(task_id), payload)
        batch_id = message.get("batch_id")
        if batch_id:
            await redis_conn.publish(batch_channel(str(batch_id)), payload)
    except Exception as exc:
        logger.warning("Failed to async publish update for task %s: %s", task_id, exc)
