"""Health check endpoints."""
from datetime import datetime

from fastapi import APIRouter, Request
import redis

from app.config import settings
from app.core.extract.templates import TemplateError, load_registry
from app.models.schemas import HealthResponse


router = APIRouter(prefix="/api/health", tags=["Health"])


@router.get("/", response_model=HealthResponse)
async def health_check(request: Request):
    redis_ok = False
    try:
        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis_ok = bool(client.ping())
        client.close()
    except redis.RedisError:
        redis_ok = False

    registry = getattr(request.app.state, "templates", None)
    templates_loaded = len(registry.names) if registry is not None else 0

    return HealthResponse(
        status="healthy" if redis_ok and templates_loaded else "degraded",
        timestamp=datetime.utcnow(),
        redis_ok=redis_ok,
        templates_loaded=templates_loaded,
    )


@router.get("/templates")
async def check_templates():
    try:
        registry = load_registry()
    except TemplateError as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "templates": registry.names}
