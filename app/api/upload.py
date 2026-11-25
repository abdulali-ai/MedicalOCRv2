"""Shared PDF upload validation, template lookup, and Redis queue helpers."""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    import fitz
from fastapi import HTTPException, Request, UploadFile, status

from app.config import settings
from app.core.extract.templates import load_registry
from app.core.jobs.state import job_key
from app.core.notifications import async_publish_update


UPLOAD_CHUNK_BYTES = 64 * 1024
PDF_MAGIC = b"%PDF-"
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def upload_dir() -> Path:
    path = Path(settings.STORAGE_DIR) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def require_template(request: Request, name: str) -> str:
    registry = getattr(request.app.state, "templates", None)
    if registry is None:
        registry = load_registry()
        request.app.state.templates = registry
    if name not in registry:
        raise HTTPException(status_code=400, detail=f"Unknown template '{name}'")
    return name


def validate_identifier(value: str, kind: str = "identifier") -> str:
    cleaned = (value or "").strip()
    if not SAFE_ID_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid {kind}")
    return cleaned


def inspect_pdf_pages(file_path: Path) -> int:
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


async def save_and_validate_pdf(file: UploadFile, destination: Path) -> int:
    """Read an uploaded PDF with size, magic-header, and page-count validation."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    max_bytes = settings.max_upload_bytes
    total_bytes = 0
    first_chunk = True
    chunks: list[bytes] = []

    try:
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            if first_chunk:
                first_chunk = False
                if not chunk.startswith(PDF_MAGIC):
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
            page_count = await asyncio.to_thread(inspect_pdf_pages, destination)
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


async def enqueue_job(
    redis,
    task_id: str,
    filename: str,
    pdf_path: str,
    template: str,
    batch_id: str | None,
    *,
    stream: str,
    pipeline: str = "ocr",
    page_count: int = 0,
) -> None:
    """Create the Redis job record, push to the target stream, and emit the started event."""
    created_at = time.time()
    ttl = settings.TASK_RESULT_EXPIRE_TIME
    await redis.hset(
        job_key(task_id),
        mapping={
            "filename": filename,
            "batch_id": batch_id or "",
            "template": template,
            "pipeline": pipeline,
            "pdf_path": pdf_path,
            "status": "queued",
            "page_count": str(page_count),
            "created_at": str(created_at),
        },
    )
    await redis.expire(job_key(task_id), ttl)
    await redis.xadd(
        stream,
        {"task_id": task_id},
        maxlen=settings.STREAM_MAXLEN,
        approximate=True,
    )
    await async_publish_update(
        redis,
        task_id,
        {
            "task_id": task_id,
            "filename": filename,
            "batch_id": batch_id,
            "template": template,
            "pipeline": pipeline,
            "status": "started",
            "page_count": page_count,
            "message": f"Started processing {filename}",
        },
    )
