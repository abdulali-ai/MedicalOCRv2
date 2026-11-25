"""Parse electronic PDFs into per-page structured text and enqueue direct invoice extraction."""
from __future__ import annotations

import logging

from app.config import settings
from app.core.epdf import epdf_dir, get_epdf_parser
from app.core.jobs.loop import run_consumer
from app.core.jobs.state import JobStore, job_key
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

    template_name = job.get("template") or ""
    parser = get_epdf_parser(template_name)

    publish_update(task_id, {
        "task_id": task_id,
        "filename": job.get("filename"),
        "batch_id": job.get("batch_id") or None,
        "template": template_name,
        "pipeline": "epdf",
        "event": "progress",
        "data": {"step": "extract_epdf_text", "message": "Extracting tables and text from electronic PDF"},
    })

    document = parser(job["pdf_path"])
    out_dir = epdf_dir(task_id)
    page_files: list[tuple[int, str]] = []
    for item in document.pages:
        text_file = out_dir / f"page_{item.page}.txt"
        text_file.write_text(item.text, encoding="utf-8")
        page_files.append((item.page, str(text_file.resolve())))

    store.set_pending_once(task_id, len(page_files))
    store.redis.hset(job_key(task_id), "status", "processing")

    publish_update(task_id, {
        "task_id": task_id,
        "filename": job.get("filename"),
        "batch_id": job.get("batch_id") or None,
        "template": template_name,
        "pipeline": "epdf",
        "event": "progress",
        "data": {"step": "extract_epdf_text", "pages": len(page_files), "status": "done"},
    })

    if not page_files:
        store.maybe_merge(task_id)
        return

    # e-PDFs contain invoices only: bypass vision-worker and page classification completely.
    for page_number, text_path in page_files:
        store.enqueue(settings.STREAM_EXTRACT, {
            "task_id": task_id,
            "page": page_number,
            "stage": "invoice",
            "input_mode": "text",
            "text_path": text_path,
            "template_name": template_name,
            "source": "epdf",
            "crop_index": 0,
            "crop_count": 1,
        })


def main() -> None:
    store = JobStore()
    run_consumer(
        store,
        settings.STREAM_EPDF,
        settings.GROUP_EPDF,
        lambda fields: handle(store, fields),
    )


if __name__ == "__main__":
    main()
