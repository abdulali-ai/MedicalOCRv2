"""
Pydantic models for API requests and responses.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Request Models
class ProcessPDFRequest(BaseModel):
    """Request model for PDF processing (if needed for additional parameters)"""
    max_new_tokens: int = Field(default=8192, description="Maximum tokens for inference")


# Response Models
class PageResult(BaseModel):
    """Single page processing result"""
    page_number: int
    text: str
    parsed_data: Optional[Dict[str, Any]] = None


class ProcessPDFResponse(BaseModel):
    """Response model for PDF processing"""
    pdf_path: str
    processed_pages: List[Dict[str, Any]]
    raw_text: str
    skipped: List[Dict[str, Any]] = []


class JobStatusResponse(BaseModel):
    """Status and optional extraction result for a single PDF job."""
    task_id: str
    filename: Optional[str] = None
    batch_id: Optional[str] = None
    template: Optional[str] = None
    status: str
    page_count: int = 0
    completed_pages: int = 0
    pending_pages: int = 0
    created_at: Optional[float] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class BatchStatusResponse(BaseModel):
    """Aggregated status and per-job details for a batch upload."""
    batch_id: str
    template: Optional[str] = None
    status: str
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    pending_jobs: int = 0
    created_at: Optional[float] = None
    jobs: List[JobStatusResponse] = []


class TokenResponse(BaseModel):
    """Signed JWT token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: datetime = Field(default_factory=_utc_now)
    redis_ok: bool = False
    templates_loaded: int = 0


class ErrorResponse(BaseModel):
    """Error response model"""
    error: str
    detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=_utc_now)


# Model exports
__all__ = [
    "ProcessPDFRequest",
    "ProcessPDFResponse",
    "JobStatusResponse",
    "BatchStatusResponse",
    "TokenResponse",
    "PageResult",
    "HealthResponse",
    "ErrorResponse",
]
