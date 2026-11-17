"""Health check endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
import redis.exceptions

from app.core.extract.templates import TemplateError, load_registry
from app.core.notifications import get_async_redis
from app.core.security import require_auth
from app.models.schemas import HealthResponse


router = APIRouter(prefix="/api/health", tags=["Health"])


@router.get("/", response_model=HealthResponse)
async def health_check(request: Request):
    redis_ok = False
    try:
        client = get_async_redis(request.app)
        redis_ok = bool(await client.ping())
    except (redis.exceptions.RedisError, OSError):
        redis_ok = False

    registry = getattr(request.app.state, "templates", None)
    templates_loaded = len(registry.names) if registry is not None else 0

    return HealthResponse(
        status="healthy" if redis_ok and templates_loaded else "degraded",
        timestamp=datetime.now(timezone.utc),
        redis_ok=redis_ok,
        templates_loaded=templates_loaded,
    )


@router.get("/templates", dependencies=[Depends(require_auth)])
async def check_templates():
    try:
        registry = load_registry()
    except TemplateError as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "templates": registry.names}
