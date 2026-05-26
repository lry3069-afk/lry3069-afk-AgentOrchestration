"""API middleware components."""

import os
import time
import logging
from typing import Callable, List, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware with JWT audience validation.

    Validates JWT tokens for embedded console session exchanges, ensuring
    audience, issuer, tenant, and expiration constraints are enforced.
    """

    def __init__(
        self,
        app,
        expected_audience: Optional[str] = None,
        expected_issuer: Optional[str] = None,
        allowed_issuers: Optional[List[str]] = None,
    ):
        super().__init__(app)
        self.expected_audience = expected_audience or os.environ.get(
            "JWT_AUDIENCE", "agent-orchestration"
        )
        self.expected_issuer = expected_issuer or os.environ.get("JWT_ISSUER", "")
        self.allowed_issuers = allowed_issuers or [
            i.strip()
            for i in os.environ.get("JWT_ALLOWED_ISSUERS", "").split(",")
            if i.strip()
        ]

    def _validate_jwt(self, token: str, request: Request) -> bool:
        """Validate JWT token claims including audience, issuer, and tenant."""
        import base64 as b64
        import json as _json

        try:
            parts = token.split(".")
            if len(parts) != 3:
                return False

            payload_b64 = parts[1]
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = b64.urlsafe_b64decode(payload_b64)
            payload = _json.loads(payload_bytes)

            aud = payload.get("aud", "")
            if isinstance(aud, list):
                if self.expected_audience not in aud:
                    logger.warning("JWT audience mismatch: expected %s, got %s", self.expected_audience, aud)
                    return False
            elif aud != self.expected_audience:
                logger.warning("JWT audience mismatch: expected %s, got %s", self.expected_audience, aud)
                return False

            iss = payload.get("iss", "")
            if self.expected_issuer and iss != self.expected_issuer:
                if self.allowed_issuers and iss not in self.allowed_issuers:
                    logger.warning("JWT issuer not allowed: %s", iss)
                    return False
                elif not self.allowed_issuers:
                    logger.warning("JWT issuer mismatch: expected %s, got %s", self.expected_issuer, iss)
                    return False

            exp = payload.get("exp", 0)
            if exp and time.time() > exp:
                logger.warning("JWT token has expired")
                return False

            tenant = payload.get("tenant", payload.get("workspace", ""))
            workspace = request.headers.get("X-Workspace-Id", "")
            if tenant and workspace and tenant != workspace:
                logger.warning("JWT tenant mismatch: token %s != workspace %s", tenant, workspace)
                return False

            return True

        except Exception as e:
            logger.error("JWT validation error: %s", e)
            return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith("/api/v2") and request.url.path != "/api/v2/auth/token":
            token = request.headers.get("Authorization", "")
            if not token.startswith("Bearer "):
                return Response(status_code=401, content="Unauthorized")

            jwt_token = token[7:]
            if not self._validate_jwt(jwt_token, request):
                return Response(status_code=401, content="Invalid or unauthorized token")

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
        logger.info("%s %s %s %.3fs", request.method, request.url.path, response.status_code, duration)
        return response
