"""
Configuration loaded from the environment and an optional .env file.
"""
from pathlib import Path
from typing import List
import json

from pydantic_settings import BaseSettings, SettingsConfigDict


_APP_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _APP_DIR.parent


class Settings(BaseSettings):
    # API
    OPENAI_API_KEY: str = "sk-not-required"
    VLLM_API_URL: str = "http://127.0.0.1:8000/v1"
    VLLM_MODEL_NAME: str = "numind/NuExtract3-FP8"

    # Must match the vLLM --max-model-len argument.
    # Checkpoint max_position_embeddings is 262144. Serving is capped here so a
    # page sent at 200 DPI (~3.8k vision tokens) plus an 8192-token invoice still fits,
    # without reserving a 262k KV cache.
    VLLM_MAX_MODEL_LEN: int = 32768
    VISION_PATCH_SIZE: int = 16
    VISION_MERGE_SIZE: int = 2
    VISION_MIN_PIXELS: int = 65536
    VISION_MAX_PIXELS: int = 16777216
    CHAT_TOKEN_OVERHEAD: int = 128

    MAX_TOKENS_CLASSIFY: int = 256
    MAX_TOKENS_ID_CARD: int = 1024
    MAX_TOKENS_INVOICE: int = 8192
    EXTRACT_ATTEMPTS: int = 3
    EXTRACT_RETRY_BACKOFF_SECONDS: float = 1.0
    MAX_CONCURRENT_EXTRACTS: int = 8
    VLLM_TIMEOUT_SECONDS: float = 600.0

    # Authentication
    AUTH_ENABLED: bool = True
    API_KEY: str = "ocr-dev-api-key-change-in-production"
    JWT_SECRET_KEY: str = "your-secret-key-here-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60

    # CORS (restricted to explicit origins by default)
    CORS_ORIGINS: str = '["http://localhost:3000","http://localhost:5000","http://127.0.0.1:5000"]'

    # Upload validation limits
    MAX_UPLOAD_SIZE_MB: int = 50
    MAX_PDF_PAGES: int = 100
    MAX_BATCH_FILES: int = 50

    # Application
    APP_TITLE: str = "Document Batch Processor"
    APP_VERSION: str = "3.0.0"

    # Redis and storage
    REDIS_URL: str = "redis://localhost:6379/0"
    STORAGE_DIR: str = str(_PROJECT_DIR / "storage")
    TEMPLATES_DIR: str = str(_APP_DIR / "templates")
    TASK_RESULT_EXPIRE_TIME: int = 72 * 3600
    MERGE_LOCK_TTL_SECONDS: int = 120
    STREAM_CLAIM_IDLE_MS: int = 60_000
    STREAM_MAXLEN: int = 10_000
    MAX_DELIVERIES: int = 5

    # Vision
    ID_MODEL_PATH: str = str(_PROJECT_DIR / "models" / "id_card_detection" / "best.pt")
    ORIENT_MODEL_PATH: str = str(_PROJECT_DIR / "models" / "orientation_classifier" / "best.pt")
    ID_DETECT_CONF: float = 0.35
    ID_DETECT_IMGSZ: int = 640
    ORIENT_IMGSZ: int = 224

    STREAM_PDF: str = "jobs:pdf"
    STREAM_VISION: str = "jobs:vision"
    STREAM_EXTRACT: str = "jobs:extract"
    GROUP_PDF: str = "pdf-workers"
    GROUP_VISION: str = "vision-workers"
    GROUP_EXTRACT: str = "extract-workers"

    @property
    def cors_origins_list(self) -> List[str]:
        try:
            parsed = json.loads(self.CORS_ORIGINS)
        except json.JSONDecodeError:
            return ["http://localhost:3000"]
        return [str(origin) for origin in parsed] if isinstance(parsed, list) else ["http://localhost:3000"]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def vllm_chat_url(self) -> str:
        return self.VLLM_API_URL.rstrip("/") + "/chat/completions"

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
