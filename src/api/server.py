"""FastAPI application server."""

import os
from typing import Dict

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from .routes import router
from .middleware import AuthMiddleware, RateLimitMiddleware, LoggingMiddleware


def create_app(config: Dict = None) -> FastAPI:
    app = FastAPI(
        title="Agent Orchestrator API",
        version="2.4.1",
        description="Enterprise Agent Orchestration Platform API",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.getenv("TRUSTED_HOSTS", "*").split(","))

    app.add_middleware(AuthMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(LoggingMiddleware)

    app.include_router(router, prefix="/api/v2")

    @app.get("/health")
    async def health():
        return {"status": "healthy", "version": "2.4.1"}

    return app

# 2019-02-28T12:25:15 update

# 2019-09-10T11:29:29 update

# 2019-10-16T19:37:18 update

# 2019-11-09T08:51:56 update

# 2020-02-18T12:49:10 update

# 2020-04-17T11:39:46 update

# 2020-05-05T10:08:24 update

# 2020-05-11T14:56:21 update

# 2020-06-25T10:25:49 update

# 2020-08-18T09:56:20 update

# 2020-08-20T10:47:20 update

# 2020-09-10T17:33:24 update

# 2020-12-07T09:49:03 update

# 2020-12-21T14:05:39 update
