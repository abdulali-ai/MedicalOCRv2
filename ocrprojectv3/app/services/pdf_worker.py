"""Render a PDF and enqueue one vision job per page without waiting on vLLM."""
from __future__ import annotations

import logging

from app.config import settings
from app.core.document.render import extract_images_from_pdf, page_dir
from app.core.jobs.loop import run_consumer
from app.core.jobs.state import JobStore
from app.core.notifications import publish_update


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def handle(store: JobStore, fields: dict) -> None:
    task_id = fields["task_id"]
    job = store.read_job(task_id)
    if not job:
        raise RuntimeError(f"Missing job {task_id}")
    if job.get("status") in {"error", "completed"}:
        return

    publish_update(task_id, {
        "task_id": task_id,
        "filename": job.get("filename"),
        "batch_id": job.get("batch_id") or None,
        "template": job.get("template"),
        "event": "progress",
        "data": {"step": "extract_images", "message": "Extracting images from PDF"},
    })

    images = extract_images_from_pdf(job["pdf_path"], str(page_dir(task_id)))
    store.set_pending_once(task_id, len(images))
    store.redis.hset(f"job:{task_id}", "status", "processing")

    publish_update(task_id, {
        "task_id": task_id,
        "filename": job.get("filename"),
        "batch_id": job.get("batch_id") or None,
        "template": job.get("template"),
        "event": "progress",
        "data": {"step": "extract_images", "pages": len(images), "status": "done"},
    })

    if not images:
        store.maybe_merge(task_id)
        return

    for index, image_path in enumerate(images, start=1):
        store.enqueue(settings.STREAM_VISION, {
            "task_id": task_id,
            "page": index,
            "image_path": image_path,
            "template_name": job.get("template"),
        })


def main() -> None:
    store = JobStore()
    run_consumer(
        store,
        settings.STREAM_PDF,
        settings.GROUP_PDF,
        lambda fields: handle(store, fields),
    )


if __name__ == "__main__":
    main()
