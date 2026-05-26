"""API middleware components."""

import os
import time
import logging
from typing import Callable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from src.api.jwt_auth import JWTValidator

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, jwt_validator: JWTValidator = None):
        super().__init__(app)
        self.jwt_validator = jwt_validator or self._default_validator()

    @staticmethod
    def _default_validator() -> JWTValidator:
        return JWTValidator(
            expected_audience=os.getenv("JWT_AUDIENCE", "agent-orchestrator"),
            expected_issuer=os.getenv("JWT_ISSUER", "auth.agent-orchestrator.io"),
            token_secret=os.getenv("JWT_SECRET", ""),
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path.startswith("/api/v2") and request.url.path != "/api/v2/auth/token":
            # Check for embedded console session tokens
            if request.url.path.startswith("/api/v2/console/embed"):
                return await self._validate_embedded_session(request, call_next)

            token = request.headers.get("Authorization", "")
            if not token.startswith("Bearer "):
                return Response(status_code=401, content="Unauthorized")
        return await call_next(request)

    async def _validate_embedded_session(self, request: Request, call_next: Callable) -> Response:
        token = request.headers.get("Authorization", "")
        if not token.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"error": "Missing JWT token for embedded console session"},
            )

        jwt_token = token[7:]  # Strip "Bearer "
        workspace_tenant = request.headers.get("X-Workspace-Tenant", "")

        if not workspace_tenant:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing X-Workspace-Tenant header"},
            )

        is_valid, error, _payload = self.jwt_validator.validate_token(
            jwt_token, expected_tenant=workspace_tenant
        )

        if not is_valid:
            return JSONResponse(
                status_code=403,
                content={"error": f"JWT validation failed: {error}"},
            )

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
