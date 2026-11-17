"""Accept PDFs, validate uploads, query job/batch state, and stream scoped WebSocket updates."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

try:
    import pymupdf as fitz
except ImportError:
    import fitz
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from app.config import settings
from app.core.extract.templates import load_registry
from app.core.jobs.state import (
    batch_key,
    batch_meta_key,
    batch_tasks_key,
    job_key,
    pages_key,
    pending_key,
    raw_pages_path,
    result_path,
)
from app.core.notifications import (
    async_publish_update,
    batch_channel,
    get_async_redis,
    task_channel,
)
from app.core.security import authenticate_websocket, create_access_token, require_auth
from app.models.schemas import BatchStatusResponse, JobStatusResponse, TokenResponse


router = APIRouter(tags=["PDF Processing"])

_UPLOAD_CHUNK_BYTES = 64 * 1024
_PDF_MAGIC = b"%PDF-"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _upload_dir() -> Path:
    path = Path(settings.STORAGE_DIR) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require_template(request: Request, name: str) -> str:
    registry = getattr(request.app.state, "templates", None)
    if registry is None:
        registry = load_registry()
        request.app.state.templates = registry
    if name not in registry:
        raise HTTPException(status_code=400, detail=f"Unknown template '{name}'")
    return name


def _validate_identifier(value: str, kind: str = "identifier") -> str:
    cleaned = (value or "").strip()
    if not _SAFE_ID_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid {kind}")
    return cleaned


def _inspect_pdf_pages(file_path: Path) -> int:
    """Open a saved file with PyMuPDF and return its page count, or raise ValueError."""
    try:
        doc = fitz.open(str(file_path))
    except Exception as exc:
        raise ValueError(f"Corrupted or unreadable PDF: {exc}") from exc
    try:
        if doc.is_encrypted:
            raise ValueError("Password-protected / encrypted PDFs are not supported")
        page_count = len(doc)
    finally:
        doc.close()
    if page_count < 1:
        raise ValueError("PDF contains no pages")
    if page_count > settings.MAX_PDF_PAGES:
        raise ValueError(
            f"PDF has {page_count} pages, exceeding the maximum of {settings.MAX_PDF_PAGES} pages"
        )
    return page_count


def _write_chunks_sync(file_path: Path, chunks: list[bytes]) -> None:
    with file_path.open("wb") as buffer:
        for chunk in chunks:
            buffer.write(chunk)


async def _save_and_validate_pdf(file: UploadFile, destination: Path) -> int:
    """Read an uploaded PDF with size, magic-header, and page-count validation."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    max_bytes = settings.max_upload_bytes
    total_bytes = 0
    first_chunk = True
    chunks: list[bytes] = []

    try:
        while True:
            chunk = await file.read(_UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            if first_chunk:
                first_chunk = False
                if not chunk.startswith(_PDF_MAGIC):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid PDF file '{Path(file.filename).name}': missing %PDF- header",
                    )
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"File '{Path(file.filename).name}' exceeds maximum size of {settings.MAX_UPLOAD_SIZE_MB} MB",
                )
            chunks.append(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="Uploaded PDF file is empty")

        await asyncio.to_thread(_write_chunks_sync, destination, chunks)
        chunks.clear()

        try:
            page_count = await asyncio.to_thread(_inspect_pdf_pages, destination)
        except ValueError as exc:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return page_count
    except Exception:
        if destination.exists() and first_chunk:
            destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


async def _enqueue(
    redis,
    task_id: str,
    filename: str,
    pdf_path: str,
    template: str,
    batch_id: str | None,
    page_count: int = 0,
) -> None:
    created_at = time.time()
    await redis.hset(
        job_key(task_id),
        mapping={
            "filename": filename,
            "batch_id": batch_id or "",
            "template": template,
            "pdf_path": pdf_path,
            "status": "queued",
            "page_count": str(page_count),
            "created_at": str(created_at),
        },
    )
    await redis.xadd(settings.STREAM_PDF, {"task_id": task_id})
    await async_publish_update(
        redis,
        task_id,
        {
            "task_id": task_id,
            "filename": filename,
            "batch_id": batch_id,
            "template": template,
            "status": "started",
            "page_count": page_count,
            "message": f"Started processing {filename}",
        },
    )


def _read_json_file(path_str: str) -> dict[str, Any] | None:
    path = Path(path_str)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


async def _build_job_status(
    redis,
    task_id: str,
    *,
    include_result: bool = True,
    include_raw_pages: bool = False,
) -> JobStatusResponse | None:
    job = await redis.hgetall(job_key(task_id))
    res_file = result_path(task_id)
    disk_result = None

    if not job:
        disk_result = await asyncio.to_thread(_read_json_file, res_file)
        if disk_result is None:
            return None
        if include_raw_pages and "raw_pages" not in disk_result:
            raw_data = await asyncio.to_thread(_read_json_file, raw_pages_path(task_id))
            if raw_data and "raw_pages" in raw_data:
                disk_result["raw_pages"] = raw_data["raw_pages"]
        return JobStatusResponse(
            task_id=task_id,
            filename=disk_result.get("filename"),
            batch_id=disk_result.get("batch_id"),
            template=disk_result.get("template"),
            status="completed",
            page_count=len(disk_result.get("raw_pages") or []),
            completed_pages=len(disk_result.get("raw_pages") or []),
            pending_pages=0,
            created_at=disk_result.get("processed_at"),
            result=disk_result if include_result else None,
        )

    job_status = job.get("status", "queued")
    page_count = int(job.get("page_count") or 0)
    completed_pages = int(await redis.hlen(pages_key(task_id)))
    pending_raw = await redis.get(pending_key(task_id))
    pending_pages = int(pending_raw) if pending_raw is not None else max(0, page_count - completed_pages)
    if job_status == "completed":
        pending_pages = 0
        if page_count > 0:
            completed_pages = page_count

    created_at = None
    if job.get("created_at"):
        try:
            created_at = float(job["created_at"])
        except ValueError:
            created_at = None

    result_payload = None
    if include_result and job_status == "completed":
        stored_path = job.get("result_path") or res_file
        result_payload = await asyncio.to_thread(_read_json_file, stored_path)
        if result_payload is not None and include_raw_pages and "raw_pages" not in result_payload:
            pages_file = job.get("raw_pages_path") or raw_pages_path(task_id)
            raw_data = await asyncio.to_thread(_read_json_file, pages_file)
            if raw_data and "raw_pages" in raw_data:
                result_payload["raw_pages"] = raw_data["raw_pages"]

    return JobStatusResponse(
        task_id=task_id,
        filename=job.get("filename") or None,
        batch_id=job.get("batch_id") or None,
        template=job.get("template") or None,
        status=job_status,
        page_count=page_count,
        completed_pages=completed_pages,
        pending_pages=pending_pages,
        created_at=created_at,
        error=job.get("error") or None,
        result=result_payload,
    )


@router.post("/api/auth/token", response_model=TokenResponse, tags=["Authentication"])
async def issue_token(principal: dict = Depends(require_auth)):
    """Exchange a valid API Key or Bearer token for a signed HS256 JWT."""
    token = create_access_token(subject=str(principal.get("sub") or "api-client"))
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.JWT_EXPIRATION_MINUTES * 60,
    )


@router.post("/process-pdf/", dependencies=[Depends(require_auth)])
async def process_pdf(request: Request, template: str = Form(...), file: UploadFile = File(...)):
    """Queue one PDF after validating its header, size, and page count."""
    template = _require_template(request, template)

    task_id = str(uuid.uuid4())
    safe_name = Path(file.filename or "document.pdf").name
    file_path = _upload_dir() / f"{task_id}_{safe_name}"
    page_count = await _save_and_validate_pdf(file, file_path)

    redis = get_async_redis(request.app)
    await _enqueue(
        redis,
        task_id,
        safe_name,
        str(file_path.resolve()),
        template,
        None,
        page_count=page_count,
    )

    return {
        "task_id": task_id,
        "filename": safe_name,
        "status": "queued",
        "template": template,
        "page_count": page_count,
    }


@router.post("/batch-process-pdf/", dependencies=[Depends(require_auth)])
async def batch_process_pdf(request: Request, template: str = Form(...), files: list[UploadFile] = File(...)):
    """Queue many PDFs under one template name after validating every file."""
    template = _require_template(request, template)
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
            file_path = _upload_dir() / f"{task_id}_{safe_name}"
            page_count = await _save_and_validate_pdf(upload, file_path)
            staged.append((task_id, safe_name, file_path, page_count))
    except Exception:
        for _tid, _name, path, _pages in staged:
            path.unlink(missing_ok=True)
        raise

    batch_id = str(uuid.uuid4())
    created_at = time.time()
    redis = get_async_redis(request.app)
    await redis.set(batch_key(batch_id), len(staged))
    await redis.hset(
        batch_meta_key(batch_id),
        mapping={
            "batch_id": batch_id,
            "template": template,
            "status": "processing",
            "total_jobs": str(len(staged)),
            "created_at": str(created_at),
        },
    )
    await redis.rpush(batch_tasks_key(batch_id), *[item[0] for item in staged])

    results = []
    for task_id, safe_name, file_path, page_count in staged:
        await _enqueue(
            redis,
            task_id,
            safe_name,
            str(file_path.resolve()),
            template,
            batch_id,
            page_count=page_count,
        )
        results.append({
            "task_id": task_id,
            "filename": safe_name,
            "status": "queued",
            "batch_id": batch_id,
            "template": template,
            "page_count": page_count,
        })
    return results


@router.get(
    "/api/jobs/{task_id}",
    response_model=JobStatusResponse,
    dependencies=[Depends(require_auth)],
)
async def get_job_status(
    request: Request,
    task_id: str,
    include_raw_pages: bool = Query(default=False, description="Include unmerged per-page extraction output"),
):
    """Retrieve current status, progress, and completed extraction result for a job."""
    task_id = _validate_identifier(task_id, "task_id")
    redis = get_async_redis(request.app)
    status_obj = await _build_job_status(
        redis,
        task_id,
        include_result=True,
        include_raw_pages=include_raw_pages,
    )
    if status_obj is None:
        raise HTTPException(status_code=404, detail=f"Job '{task_id}' not found")
    return status_obj


@router.get(
    "/api/batches/{batch_id}",
    response_model=BatchStatusResponse,
    dependencies=[Depends(require_auth)],
)
async def get_batch_status(
    request: Request,
    batch_id: str,
    include_results: bool = Query(default=True, description="Include extraction results for completed jobs"),
    include_raw_pages: bool = Query(default=False, description="Include unmerged per-page output on completed jobs"),
):
    """Retrieve aggregated batch progress and individual job statuses/results."""
    batch_id = _validate_identifier(batch_id, "batch_id")
    redis = get_async_redis(request.app)
    meta = await redis.hgetall(batch_meta_key(batch_id))
    task_ids = await redis.lrange(batch_tasks_key(batch_id), 0, -1)
    remaining_raw = await redis.get(batch_key(batch_id))

    if not meta and not task_ids and remaining_raw is None:
        raise HTTPException(status_code=404, detail=f"Batch '{batch_id}' not found")

    jobs: list[JobStatusResponse] = []
    for task_id in task_ids:
        job_info = await _build_job_status(
            redis,
            task_id,
            include_result=include_results,
            include_raw_pages=include_raw_pages,
        )
        if job_info is not None:
            jobs.append(job_info)

    total_jobs = int(meta.get("total_jobs") or len(jobs))
    completed_jobs = sum(1 for item in jobs if item.status == "completed")
    failed_jobs = sum(1 for item in jobs if item.status == "error")
    pending_jobs = max(0, total_jobs - completed_jobs - failed_jobs)

    if pending_jobs == 0 and total_jobs > 0:
        if failed_jobs == 0:
            batch_status = "completed"
        elif completed_jobs == 0:
            batch_status = "error"
        else:
            batch_status = "partial_error"
    elif any(item.status == "processing" for item in jobs) or completed_jobs > 0 or failed_jobs > 0:
        batch_status = "processing"
    else:
        batch_status = meta.get("status") or "queued"

    created_at = None
    if meta.get("created_at"):
        try:
            created_at = float(meta["created_at"])
        except ValueError:
            created_at = None

    return BatchStatusResponse(
        batch_id=batch_id,
        template=meta.get("template") or (jobs[0].template if jobs else None),
        status=batch_status,
        total_jobs=total_jobs,
        completed_jobs=completed_jobs,
        failed_jobs=failed_jobs,
        pending_jobs=pending_jobs,
        created_at=created_at,
        jobs=jobs,
    )


async def _stream_scoped_updates(
    websocket: WebSocket,
    *,
    task_id: str | None = None,
    batch_id: str | None = None,
) -> None:
    principal = authenticate_websocket(websocket)
    if principal is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
        return

    task_id = (task_id or websocket.query_params.get("task_id") or "").strip() or None
    batch_id = (batch_id or websocket.query_params.get("batch_id") or "").strip() or None

    if not task_id and not batch_id:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Scoped subscription required: provide task_id or batch_id",
        )
        return

    if task_id and not _SAFE_ID_RE.fullmatch(task_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid task_id")
        return
    if batch_id and not _SAFE_ID_RE.fullmatch(batch_id):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid batch_id")
        return

    await websocket.accept()
    redis = get_async_redis(websocket.app)
    pubsub = redis.pubsub()
    channels = []
    if task_id:
        channels.append(task_channel(task_id))
    if batch_id:
        channels.append(batch_channel(batch_id))

    await pubsub.subscribe(*channels)
    try:
        # If the client connected after the task already reached a terminal state,
        # emit the current state immediately so the client does not hang waiting.
        if task_id:
            snapshot = await _build_job_status(redis, task_id, include_result=True, include_raw_pages=True)
            if snapshot is not None and snapshot.status in {"completed", "error"}:
                event_payload = {
                    "task_id": snapshot.task_id,
                    "filename": snapshot.filename,
                    "batch_id": snapshot.batch_id,
                    "template": snapshot.template,
                    "event": snapshot.status,
                    "data": snapshot.result if snapshot.status == "completed" else {"message": snapshot.error},
                }
                await websocket.send_text(json.dumps(event_payload, ensure_ascii=False))

        async for message in pubsub.listen():
            if message.get("type") == "message":
                await websocket.send_text(str(message["data"]))
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.aclose()


@router.websocket("/ws/{task_id}")
async def websocket_task_endpoint(websocket: WebSocket, task_id: str):
    """Stream real-time updates scoped to a single task_id."""
    await _stream_scoped_updates(websocket, task_id=task_id)


@router.websocket("/ws/batch/{batch_id}")
async def websocket_batch_endpoint(websocket: WebSocket, batch_id: str):
    """Stream real-time updates scoped to a single batch_id."""
    await _stream_scoped_updates(websocket, batch_id=batch_id)


@router.websocket("/ws")
async def websocket_query_endpoint(websocket: WebSocket):
    """Stream real-time updates scoped via ?task_id=... or ?batch_id=... query parameters."""
    await _stream_scoped_updates(websocket)
