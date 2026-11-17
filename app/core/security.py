"""API Key and HS256 JWT authentication for HTTP and WebSocket endpoints."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Depends, HTTPException, Request, Security, WebSocket, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer_scheme = HTTPBearer(auto_error=False)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def create_access_token(
    subject: str = "api-client",
    extra_claims: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Create a signed HS256 JWT without third-party crypto dependencies."""
    now = int(time.time())
    ttl = (expires_minutes if expires_minutes is not None else settings.JWT_EXPIRATION_MINUTES) * 60
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + ttl,
    }
    if extra_claims:
        payload.update(extra_claims)

    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def verify_jwt_token(token: str) -> dict[str, Any] | None:
    """Verify an HS256 JWT signature and expiration timestamp."""
    if not token or token.count(".") != 2:
        return None
    header_b64, payload_b64, sig_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    try:
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(actual_sig, expected_sig):
            return None
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        if header.get("alg") != "HS256":
            return None
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or time.time() >= float(exp):
        return None
    return payload


def verify_api_key(candidate: str | None) -> bool:
    """Constant-time check against the configured API_KEY."""
    configured = (settings.API_KEY or "").strip()
    if not configured or not candidate:
        return False
    return hmac.compare_digest(candidate.strip().encode("utf-8"), configured.encode("utf-8"))


def _authenticate_token_or_key(api_key: str | None, bearer_token: str | None) -> dict[str, Any] | None:
    if not settings.AUTH_ENABLED:
        return {"sub": "anonymous", "auth_type": "disabled"}

    if api_key and verify_api_key(api_key):
        return {"sub": "api-key-client", "auth_type": "api_key"}

    if bearer_token:
        if verify_api_key(bearer_token):
            return {"sub": "api-key-client", "auth_type": "api_key"}
        claims = verify_jwt_token(bearer_token)
        if claims is not None:
            return {"sub": claims.get("sub", "jwt-client"), "auth_type": "jwt", "claims": claims}

    return None


async def require_auth(
    request: Request,
    api_key: str | None = Security(_api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> dict[str, Any]:
    """FastAPI dependency enforcing API Key or Bearer JWT authentication."""
    bearer_token = bearer.credentials if bearer else None
    principal = _authenticate_token_or_key(api_key, bearer_token)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key / Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.principal = principal
    return principal


def authenticate_websocket(websocket: WebSocket) -> dict[str, Any] | None:
    """Authenticate a WebSocket connection via headers or query parameters."""
    api_key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")
    auth_header = websocket.headers.get("authorization") or ""
    bearer_token = None
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
    if not bearer_token:
        bearer_token = websocket.query_params.get("token")
    return _authenticate_token_or_key(api_key, bearer_token)

