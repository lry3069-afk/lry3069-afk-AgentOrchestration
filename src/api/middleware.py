"""API middleware components."""

import hashlib
import hmac
import os
import secrets
import time
import logging
from typing import Callable, Dict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

logger = logging.getLogger(__name__)

CSRF_SECRET = os.environ.get("CSRF_SECRET", secrets.token_hex(32))
CSRF_TOKEN_TTL = int(os.environ.get("CSRF_TOKEN_TTL", "3600"))


def generate_csrf_token(session_id: str, org_id: str) -> str:
    payload = f"{session_id}:{org_id}:{int(time.time())}"
    signature = hmac.new(
        CSRF_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{signature}"


def validate_csrf_token(token: str, session_id: str, org_id: str) -> bool:
    try:
        parts = token.rsplit(":", 1)
        if len(parts) != 2:
            return False
        payload, provided_sig = parts
        expected_sig = hmac.new(
            CSRF_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(provided_sig, expected_sig):
            return False
        payload_parts = payload.split(":")
        if len(payload_parts) != 3:
            return False
        tok_session, tok_org, tok_ts = payload_parts
        if tok_session != session_id:
            return False
        if tok_org != org_id:
            return False
        if int(time.time()) - int(tok_ts) > CSRF_TOKEN_TTL:
            return False
        return True
    except (ValueError, IndexError):
        return False


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path == "/api/v2/org/switch" and request.method == "POST":
            session_id = request.cookies.get("session_id", "")
            if not session_id:
                return JSONResponse(
                    status_code=401,
                    content={"error": "No active session"},
                )
            csrf_token = request.headers.get("X-CSRF-Token", "")
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid request body"},
                )
            target_org = body.get("organization_id", "")
            if not target_org:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Missing organization_id"},
                )
            if not csrf_token:
                return JSONResponse(
                    status_code=403,
                    content={"error": "Missing CSRF token"},
                )
            if not validate_csrf_token(csrf_token, session_id, target_org):
                return JSONResponse(
                    status_code=403,
                    content={"error": "Invalid or expired CSRF token"},
                )
        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith("/api/v2") and request.url.path != "/api/v2/auth/token":
            token = request.headers.get("Authorization", "")
            if not token.startswith("Bearer "):
                return Response(status_code=401, content="Unauthorized")
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window
        self._requests: Dict[str, list] = {}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        if client_ip not in self._requests:
            self._requests[client_ip] = []

        self._requests[client_ip] = [t for t in self._requests[client_ip] if now - t < self.window]

        if len(self._requests[client_ip]) >= self.max_requests:
            return Response(status_code=429, content="Too many requests")

        self._requests[client_ip].append(now)
        return await call_next(request)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        logger.info(f"{request.method} {request.url.path} {response.status_code} {duration:.3f}s")
        return response