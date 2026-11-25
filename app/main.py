"""
FastAPI entrypoint. This process accepts uploads and fans out Redis events.
It does not load YOLO weights and it does not call vLLM.
"""
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis import asyncio as aioredis

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes import epdf, health, pdf
from app.config import settings
from app.core.extract.templates import load_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry = load_registry()
    app.state.templates = registry
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    print(f"Starting {settings.APP_TITLE} v{settings.APP_VERSION}")
    print(f"Templates: {', '.join(registry.names)}")
    try:
        yield
    finally:
        print("Shutting down...")
        redis_client = getattr(app.state, "redis", None)
        if redis_client is not None:
            await redis_client.aclose()
            app.state.redis = None


app = FastAPI(
    title=settings.APP_TITLE,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

_cors_origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

app.include_router(health.router)
app.include_router(pdf.router)
app.include_router(epdf.router)


@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.APP_TITLE}",
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/api/health",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
