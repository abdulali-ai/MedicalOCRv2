"""Electronic PDF (e-PDF) endpoints — separate from the image/OCR pipeline."""
from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.api.upload import (
    enqueue_job,
    require_template,
    save_and_validate_pdf,
    upload_dir,
)
from app.config import settings
from app.core.epdf import (
    EpdfError,
    get_epdf_parser,
    registered_epdf_templates,
    require_selectable_text,
)
from app.core.jobs.state import batch_key, batch_meta_key, batch_tasks_key
from app.core.notifications import get_async_redis
from app.core.security import require_auth


router = APIRouter(prefix="/api/epdf", tags=["Electronic PDF Processing"])


def _require_epdf_template(request: Request, name: str) -> str:
    cleaned = (name or "").strip().lower()
    try:
        get_epdf_parser(cleaned)
    except EpdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return require_template(request, cleaned)


async def _validate_epdf_file(file: UploadFile, destination: Path) -> int:
    page_count = await save_and_validate_pdf(file, destination)
    try:
        await asyncio.to_thread(require_selectable_text, destination)
    except ValueError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return page_count


@router.get("/templates", dependencies=[Depends(require_auth)])
async def list_epdf_templates():
    """Return provider templates that have a registered electronic PDF parser."""
    return {"ok": True, "templates": registered_epdf_templates()}


@router.post("/process", dependencies=[Depends(require_auth)])
async def process_epdf(
    request: Request,
    template: str = Form(...),
    file: UploadFile = File(...),
):
    """Queue one electronic PDF after validating structure and embedded text layer."""
    template = _require_epdf_template(request, template)

    task_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "document.pdf").name
    file_path = upload_dir() / f"{task_id}_{safe_name}"
    page_count = await _validate_epdf_file(file, file_path)

    redis = get_async_redis(request.app)
    await enqueue_job(
        redis,
        task_id,
        safe_name,
        str(file_path.resolve()),
        template,
        None,
        stream=settings.STREAM_EPDF,
        pipeline="epdf",
        page_count=page_count,
    )

    return {
        "task_id": task_id,
        "filename": safe_name,
        "status": "queued",
        "template": template,
        "pipeline": "epdf",
        "page_count": page_count,
    }


@router.post("/batch", dependencies=[Depends(require_auth)])
async def batch_process_epdf(
    request: Request,
    template: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """Queue many electronic PDFs under one provider template after validating every file."""
    template = _require_epdf_template(request, template)
    valid_files = [item for item in files if item.filename and item.filename.lower().endswith(".pdf")]
    if not valid_files:
        return []
    if len(valid_files) > settings.MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Batch contains {len(valid_files)} files, exceeding the maximum of {settings.MAX_BATCH_FILES}",
        )

    staged: list[tuple[str, str, Path, int]] = []
    try:
        for upload in valid_files:
            task_id = str(uuid.uuid4())
            safe_name = Path(upload.filename).name
            file_path = upload_dir() / f"{task_id}_{safe_name}"
            page_count = await _validate_epdf_file(upload, file_path)
            staged.append((task_id, safe_name, file_path, page_count))
    except Exception:
        for _tid, _name, path, _pages in staged:
            path.unlink(missing_ok=True)
        raise

    batch_id = str(uuid.uuid4())
    created_at = time.time()
    ttl = settings.TASK_RESULT_EXPIRE_TIME
    redis = get_async_redis(request.app)
    await redis.set(batch_key(batch_id), len(staged), ex=ttl)
    await redis.hset(
        batch_meta_key(batch_id),
        mapping={
            "batch_id": batch_id,
            "template": template,
            "pipeline": "epdf",
            "status": "processing",
            "total_jobs": str(len(staged)),
            "created_at": str(created_at),
        },
    )
    await redis.expire(batch_meta_key(batch_id), ttl)
    await redis.rpush(batch_tasks_key(batch_id), *[item[0] for item in staged])
    await redis.expire(batch_tasks_key(batch_id), ttl)

    results = []
    for task_id, safe_name, file_path, page_count in staged:
        await enqueue_job(
            redis,
            task_id,
            safe_name,
            str(file_path.resolve()),
            template,
            batch_id,
            stream=settings.STREAM_EPDF,
            pipeline="epdf",
            page_count=page_count,
        )
        results.append({
            "task_id": task_id,
            "filename": safe_name,
            "status": "queued",
            "batch_id": batch_id,
            "template": template,
            "pipeline": "epdf",
            "page_count": page_count,
        })
    return results
