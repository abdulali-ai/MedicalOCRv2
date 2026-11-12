"""Redis job state, streams, and idempotent page completion."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

import redis

from app.config import settings
from app.core.merge.assemble import assemble_document
from app.core.notifications import publish_update


COMPLETE_PAGE_LUA = """
local created = redis.call('HSETNX', KEYS[1], ARGV[1], ARGV[2])
if created == 1 then
  return redis.call('DECR', KEYS[2])
end
return -1
"""

STORE_CROP_LUA = """
local created = redis.call('HSETNX', KEYS[1], ARGV[1], ARGV[2])
local count = redis.call('HLEN', KEYS[1])
local flat = redis.call('HGETALL', KEYS[1])
return {created, count, flat}
"""

DECR_BATCH_LUA = """
if redis.call('HSETNX', KEYS[1], 'batch_decremented', '1') == 1 then
  return redis.call('DECR', KEYS[2])
end
return false
"""

RELEASE_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def job_key(task_id: str) -> str:
    return f"job:{task_id}"


def pages_key(task_id: str) -> str:
    return f"job:{task_id}:pages"


def pending_key(task_id: str) -> str:
    return f"job:{task_id}:pending"


def crops_key(task_id: str, page: int | str) -> str:
    return f"job:{task_id}:page:{page}:crops"


def merge_lock_key(task_id: str) -> str:
    return f"job:{task_id}:merge_lock"


def batch_key(batch_id: str) -> str:
    return f"batch_count:{batch_id}"


def result_path(task_id: str) -> str:
    from pathlib import Path
    return str(Path(settings.STORAGE_DIR) / "results" / f"{task_id}.json")


def raw_pages_path(task_id: str) -> str:
    from pathlib import Path
    return str(Path(settings.STORAGE_DIR) / "results" / f"{task_id}.pages.json")


class JobStore:
    def __init__(self, client: redis.Redis | None = None):
        self.redis = client or redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        self._complete_page = self.redis.register_script(COMPLETE_PAGE_LUA)
        self._store_crop = self.redis.register_script(STORE_CROP_LUA)
        self._decr_batch = self.redis.register_script(DECR_BATCH_LUA)
        self._release_lock = self.redis.register_script(RELEASE_LOCK_LUA)

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def enqueue(self, stream: str, fields: dict[str, Any]) -> str:
        payload = {key: "" if value is None else str(value) for key, value in fields.items()}
        return self.redis.xadd(stream, payload)

    def read_job(self, task_id: str) -> dict[str, str]:
        return self.redis.hgetall(job_key(task_id)) or {}

    def set_pending_once(self, task_id: str, page_count: int) -> None:
        self.redis.set(pending_key(task_id), page_count, nx=True)
        self.redis.hset(job_key(task_id), "page_count", page_count)

    def complete_page(self, task_id: str, page: int, record: dict) -> bool:
        remaining = self._complete_page(
            keys=[pages_key(task_id), pending_key(task_id)],
            args=[str(page), json.dumps(record, ensure_ascii=False)],
        )
        self._after_completion(task_id, int(remaining))
        return int(remaining) != -1

    def store_crop_and_maybe_finish(
        self,
        task_id: str,
        page: int,
        crop_index: int,
        crop_record: dict,
        expected: int,
        errors: list[dict],
    ) -> bool:
        created, count, flat = self._store_crop(
            keys=[crops_key(task_id, page)],
            args=[str(crop_index), json.dumps(crop_record, ensure_ascii=False)],
        )
        del created
        if int(count) < expected:
            return False
        cards = []
        stored = _pairs(flat)
        for index in sorted(stored, key=lambda item: int(item)):
            payload = json.loads(stored[index])
            if payload.get("error"):
                errors.append({
                    "page": page,
                    "crop_index": int(index),
                    "message": payload["error"],
                })
            else:
                cards.append({
                    "page": page,
                    "source": payload.get("source"),
                    "data": payload.get("data") or {},
                })
        return self.complete_page(task_id, page, {
            "kind": "id_cards",
            "page": page,
            "cards": cards,
            "errors": errors,
        })

    def fail_job(self, task_id: str, message: str) -> None:
        job = self.read_job(task_id)
        if not job:
            return
        first = self.redis.hsetnx(job_key(task_id), "failed_once", "1")
        if not first:
            return
        self.redis.hset(job_key(task_id), "status", "error")
        publish_update(task_id, {
            "task_id": task_id,
            "filename": job.get("filename"),
            "batch_id": job.get("batch_id") or None,
            "template": job.get("template"),
            "event": "error",
            "data": {"message": message},
        })
        self._decrement_batch(task_id, job)

    def _after_completion(self, task_id: str, remaining: int) -> None:
        if remaining == 0 or self._pending_is_zero(task_id):
            self.maybe_merge(task_id)

    def _pending_is_zero(self, task_id: str) -> bool:
        raw = self.redis.get(pending_key(task_id))
        return raw is not None and int(raw) == 0

    def maybe_merge(self, task_id: str) -> None:
        job = self.read_job(task_id)
        if not job or job.get("status") == "error":
            return
        if job.get("status") == "completed":
            self._decrement_batch(task_id, job)
            return
        if not self._pending_is_zero(task_id):
            return

        token = uuid.uuid4().hex
        locked = self.redis.set(
            merge_lock_key(task_id),
            token,
            nx=True,
            ex=settings.MERGE_LOCK_TTL_SECONDS,
        )
        if not locked:
            return
        try:
            job = self.read_job(task_id)
            if job.get("status") == "completed":
                self._decrement_batch(task_id, job)
                return
            pages = {}
            for field, raw in (self.redis.hgetall(pages_key(task_id)) or {}).items():
                pages[field] = json.loads(raw)
            assembled = assemble_document(pages, template=job.get("template"))
            raw_pages = assembled.pop("raw_pages", [])
            path = result_path(task_id)
            pages_path = raw_pages_path(task_id)
            processed_at = time.time()
            payload = {
                "task_id": task_id,
                "filename": job.get("filename"),
                "batch_id": job.get("batch_id") or None,
                "template": job.get("template"),
                "processed_at": processed_at,
                "raw_pages_path": pages_path,
                **assembled,
            }
            from pathlib import Path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            Path(pages_path).write_text(
                json.dumps(
                    {
                        "task_id": task_id,
                        "filename": job.get("filename"),
                        "template": job.get("template"),
                        "processed_at": processed_at,
                        "raw_pages": raw_pages,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            # Websocket clients can inspect unmerged page JSON without opening the sibling file.
            payload["raw_pages"] = raw_pages
            self.redis.hset(
                job_key(task_id),
                mapping={
                    "status": "completed",
                    "result_path": path,
                    "raw_pages_path": pages_path,
                },
            )
            publish_update(task_id, {
                "task_id": task_id,
                "filename": job.get("filename"),
                "batch_id": job.get("batch_id") or None,
                "template": job.get("template"),
                "event": "completed",
                "data": payload,
            })
            self._decrement_batch(task_id, job)
        finally:
            self._release_lock(keys=[merge_lock_key(task_id)], args=[token])

    def _decrement_batch(self, task_id: str, job: dict[str, str]) -> None:
        batch_id = job.get("batch_id") or ""
        if not batch_id:
            return
        remaining = self._decr_batch(keys=[job_key(task_id), batch_key(batch_id)])
        if remaining is False or remaining is None:
            return
        if int(remaining) <= 0:
            publish_update(task_id, {
                "event": "batch_completed",
                "batch_id": batch_id,
                "message": "All PDFs in this batch have been processed.",
            })
            self.redis.delete(batch_key(batch_id))


def _pairs(flat) -> dict[str, str]:
    """HGETALL inside a Lua table comes back as a flat list."""
    if isinstance(flat, dict):
        return {str(key): value for key, value in flat.items()}
    items = list(flat or [])
    return {str(items[index]): items[index + 1] for index in range(0, len(items), 2)}
