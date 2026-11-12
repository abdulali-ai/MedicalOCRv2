"""Accept PDFs, record the template name, and enqueue a Redis stream job."""
from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from redis import asyncio as aioredis

from app.config import settings
from app.core.jobs.state import batch_key, job_key
from app.core.notifications import publish_update


router = APIRouter(tags=["PDF Processing"])


def _upload_dir() -> Path:
    path = Path(settings.STORAGE_DIR) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_template(request: Request, name: str) -> str:
    registry = request.app.state.templates
    if name not in registry:
        raise HTTPException(status_code=400, detail=f"Unknown template '{name}'")
    return name


async def _enqueue(redis, task_id: str, filename: str, pdf_path: str, template: str, batch_id: str | None) -> None:
    await redis.hset(job_key(task_id), mapping={
        "filename": filename,
        "batch_id": batch_id or "",
        "template": template,
        "pdf_path": pdf_path,
        "status": "queued",
        "page_count": "0",
        "created_at": str(time.time()),
    })
    await redis.xadd(settings.STREAM_PDF, {"task_id": task_id})
    publish_update(task_id, {
        "task_id": task_id,
        "filename": filename,
        "batch_id": batch_id,
        "template": template,
        "status": "started",
        "message": f"Started processing {filename}",
    })


@router.post("/process-pdf/")
async def process_pdf(request: Request, template: str = Form(...), file: UploadFile = File(...)):
    """Queue one PDF. `template` selects app/templates/<name>.json."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    template = _require_template(request, template)

    task_id = str(uuid.uuid4())
    safe_name = Path(file.filename).name
    file_path = _upload_dir() / f"{task_id}_{safe_name}"
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await _enqueue(redis, task_id, safe_name, str(file_path.resolve()), template, None)
    finally:
        await redis.aclose()

    return {"task_id": task_id, "filename": safe_name, "status": "queued", "template": template}


@router.post("/batch-process-pdf/")
async def batch_process_pdf(request: Request, template: str = Form(...), files: list[UploadFile] = File(...)):
    """Queue many PDFs under one template name."""
    template = _require_template(request, template)
    valid_files = [item for item in files if item.filename and item.filename.lower().endswith(".pdf")]
    if not valid_files:
        return []

    batch_id = str(uuid.uuid4())
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    results = []
    try:
        await redis.set(batch_key(batch_id), len(valid_files))
        for file in valid_files:
            task_id = str(uuid.uuid4())
            safe_name = Path(file.filename).name
            file_path = _upload_dir() / f"{task_id}_{safe_name}"
            with file_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            await _enqueue(redis, task_id, safe_name, str(file_path.resolve()), template, batch_id)
            results.append({
                "task_id": task_id,
                "filename": safe_name,
                "status": "queued",
                "batch_id": batch_id,
                "template": template,
            })
    finally:
        await redis.aclose()
    return results


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.psubscribe("task_updates:*")
    try:
        async for message in pubsub.listen():
            if message["type"] == "pmessage":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.aclose()
        await redis.aclose()
