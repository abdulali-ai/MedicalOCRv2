"""Detect id cards, drop passports, orient, and enqueue NuExtract work."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.core.jobs.loop import run_consumer
from app.core.jobs.state import JobStore
from app.core.notifications import publish_update
from app.core.vision.pipeline import VisionModels, crop_box, read_bgr, top_half, write_bgr


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def handle(store: JobStore, models: VisionModels, fields: dict) -> None:
    task_id = fields["task_id"]
    page = int(fields["page"])
    job = store.read_job(task_id)
    if not job or job.get("status") in {"error", "completed"}:
        return

    image = read_bgr(fields["image_path"])
    id_boxes, saw_passport = models.detect(image)
    page_dir = Path(fields["image_path"]).parent

    if id_boxes:
        _enqueue_cards(store, models, task_id, page, image, id_boxes, page_dir, job)
        return

    if saw_passport:
        store.complete_page(task_id, page, {
            "kind": "skipped",
            "page": page,
            "reason": "passport_only",
        })
        publish_update(task_id, {
            "task_id": task_id,
            "filename": job.get("filename"),
            "batch_id": job.get("batch_id") or None,
            "event": "progress",
            "data": {"step": "vision", "page": page, "status": "passport_only"},
        })
        return

    upright = models.upright(image)
    upright_path = write_bgr(page_dir / f"page_{page}_upright.png", upright)
    classify_path = write_bgr(page_dir / f"page_{page}_top.png", top_half(upright))
    store.enqueue(settings.STREAM_EXTRACT, {
        "task_id": task_id,
        "page": page,
        "stage": "classify",
        "image_path": classify_path,
        "full_page_path": upright_path,
        "template_name": job.get("template") or fields.get("template_name"),
        "source": "",
        "crop_index": 0,
        "crop_count": 1,
    })
    publish_update(task_id, {
        "task_id": task_id,
        "filename": job.get("filename"),
        "batch_id": job.get("batch_id") or None,
        "event": "progress",
        "data": {"step": "vision", "page": page, "status": "classify"},
    })


def _enqueue_cards(store, models, task_id, page, image, boxes, page_dir: Path, job: dict) -> None:
    for index, box in enumerate(boxes):
        upright = models.upright(crop_box(image, box))
        crop_path = write_bgr(page_dir / f"page_{page}_card_{index}.png", upright)
        store.enqueue(settings.STREAM_EXTRACT, {
            "task_id": task_id,
            "page": page,
            "stage": "id_card",
            "image_path": crop_path,
            "full_page_path": "",
            "template_name": job.get("template"),
            "source": "detector",
            "crop_index": index,
            "crop_count": len(boxes),
        })
    publish_update(task_id, {
        "task_id": task_id,
        "filename": job.get("filename"),
        "batch_id": job.get("batch_id") or None,
        "event": "progress",
        "data": {"step": "vision", "page": page, "status": "id_card", "cards": len(boxes)},
    })


def main() -> None:
    store = JobStore()
    models = VisionModels()
    run_consumer(
        store,
        settings.STREAM_VISION,
        settings.GROUP_VISION,
        lambda fields: handle(store, models, fields),
    )


if __name__ == "__main__":
    main()
