"""API middleware components."""

import os
import time
import hmac
import hashlib
import logging
from typing import Callable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


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
        self._requests = {}

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


class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF protection that binds tokens to session and target organization.

    Organization switch actions require a CSRF token cryptographically bound
    to both the session cookie and the target organization ID. Tokens cannot
    be reused across different organizations.
    """

    def __init__(self, app, secret_key: str = None):
        super().__init__(app)
        self.secret_key = secret_key or os.environ.get("CSRF_SECRET", "agent-orch-csrf-default")

    def _generate_token(self, session_id: str, org_id: str) -> str:
        payload = f"{session_id}:{org_id}"
        signature = hmac.new(
            self.secret_key.encode(),
            payload.encode(),
            hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(signature + b":" + payload.encode()).decode()

    def _validate_token(self, token: str, session_id: str, org_id: str) -> bool:
        try:
            decoded = base64.urlsafe_b64decode(token.encode())
            sig, payload = decoded.split(b":", 1)
            expected_sig = hmac.new(
                self.secret_key.encode(),
                payload,
                hashlib.sha256
            ).digest()
            if not hmac.compare_digest(sig, expected_sig):
                return False
            tok_session, tok_org = payload.decode().split(":", 1)
            return tok_session == session_id and tok_org == org_id
        except Exception:
            return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path == "/api/v2/org/switch" and request.method == "POST":
            session_id = request.cookies.get("session_id", "")
            csrf_token = request.headers.get("X-CSRF-Token", "")

            try:
                body = await request.json()
            except Exception:
                return Response(status_code=400, content="Invalid request body")

            target_org = body.get("organization", "")

            if not csrf_token or not self._validate_token(csrf_token, session_id, target_org):
                return Response(status_code=403, content="Invalid or missing CSRF token")

        return await call_next(request)
