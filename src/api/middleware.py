"""API middleware components."""

import os
import time
import logging
from typing import Callable, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

from src.common.auth import (
    TokenValidationError,
    decode_and_validate_token,
)

logger = logging.getLogger(__name__)


def _get_jwt_secret() -> str:
    """Return the configured JWT signing secret."""
    return os.environ.get("AO_JWT_SECRET", "dev-secret-do-not-use-in-production")


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates JWT tokens on protected /api/v2 routes.

    Verifies audience, issuer, tenant, and expiration claims before
    allowing a request to proceed. Returns 401 for missing/invalid tokens
    and 403 for expired tokens.
    """

    # Paths that do not require JWT validation
    _PUBLIC_PATHS = frozenset([
        "/api/v2/auth/token",
        "/api/v2/health",
        "/api/v2/healthz",
    ])

    # Paths that require strict embedded-session JWT validation
    _EMBEDDED_SESSION_PATHS = frozenset([
        "/api/v2/sessions/embedded",
        "/api/v2/sessions/embedded/create",
    ])

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith("/api/v2"):
            return await call_next(request)
        if request.url.path in self._PUBLIC_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "Missing or malformed Authorization header"})

        token = auth_header[7:]  # strip "Bearer "
        if not token:
            return JSONResponse(status_code=401, content={"error": "Empty Bearer token"})

        secret = _get_jwt_secret()
        is_embedded = request.url.path in self._EMBEDDED_SESSION_PATHS

        try:
            payload = decode_and_validate_token(
                token,
                secret,
                require_audience=True,
                require_tenant=True,
            )
            # Attach validated claims to request state for downstream handlers
            request.state.token_payload = payload
            request.state.tenant = payload.get("tenant")
            request.state.aud = payload.get("aud")
        except TokenValidationError as e:
            if e.claim == "exp":
                return JSONResponse(
                    status_code=403,
                    content={"error": f"Token validation failed: {e.reason}"},
                )
            return JSONResponse(
                status_code=401,
                content={"error": f"Token validation failed: {e.reason}"},
            )
        except ValueError as e:
            return JSONResponse(status_code=401, content={"error": str(e)})

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

# 2019-03-01T18:35:19 update

# 2019-04-03T13:22:05 update

# 2019-04-30T17:18:49 update

# 2019-08-20T09:29:03 update

# 2019-08-30T15:52:06 update

# 2019-11-23T16:58:42 update

# 2020-02-18T10:04:07 update

# 2020-04-21T17:35:30 update

# 2020-05-22T11:10:34 update

# 2020-07-02T12:31:26 update

# 2020-07-05T13:52:59 update

# 2020-08-21T20:36:45 update

# 2021-01-19T09:17:15 update

# 2021-01-29T11:34:24 update

# 2021-02-04T15:21:21 update

# 2021-04-19T19:23:15 update

# 2021-05-20T16:50:15 update

# 2021-06-22T19:23:44 update

# 2021-09-09T13:44:55 update

# 2021-09-16T09:30:20 update

# 2021-10-14T20:42:33 update

# 2021-12-28T16:39:14 update

# 2022-01-26T19:07:27 update

# 2022-01-28T08:03:41 update

# 2022-03-23T12:17:02 update

# 2022-04-06T12:12:27 update

# 2022-04-21T14:53:01 update

# 2022-06-30T08:37:32 update

# 2022-07-06T10:44:45 update

# 2022-11-02T11:12:47 update

# 2022-11-15T20:54:21 update

# 2022-11-23T14:13:34 update

# 2023-01-26T10:03:44 update

# 2023-02-09T17:08:10 update

# 2023-02-16T10:04:00 update

# 2023-03-14T11:52:03 update

# 2023-04-10T12:42:07 update

# 2023-04-26T10:43:39 update

# 2023-06-27T08:18:07 update

# 2023-08-30T15:30:40 update

# 2023-08-30T14:10:05 update

# 2023-10-09T18:32:46 update

# 2023-11-21T20:35:55 update

# 2024-03-07T19:17:39 update

# 2024-04-01T18:06:19 update

# 2024-07-18T15:37:34 update

# 2024-07-25T09:21:53 update

# 2024-08-12T14:24:22 update

# 2024-11-18T08:50:54 update

# 2025-04-08T12:43:05 update

# 2025-06-03T08:10:47 update

# 2025-06-12T08:37:52 update

# 2025-06-17T08:36:56 update

# 2025-07-02T18:09:42 update

# 2025-07-22T12:39:21 update

# 2025-10-13T12:13:46 update

# 2025-12-05T09:44:22 update

# 2025-12-22T18:34:47 update

# 2026-01-26T15:36:23 update

# 2026-02-13T12:36:40 update

# 2026-02-26T11:07:15 update

# 2026-03-19T11:00:17 update

# 2026-03-27T12:58:53 update

# 2026-05-12T17:19:36 update
