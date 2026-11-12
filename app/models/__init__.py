"""API schemas."""
from app.models.schemas import (
    ErrorResponse,
    HealthResponse,
    PageResult,
    ProcessPDFRequest,
    ProcessPDFResponse,
)

__all__ = [
    "ProcessPDFRequest",
    "ProcessPDFResponse",
    "PageResult",
    "HealthResponse",
    "ErrorResponse",
]
