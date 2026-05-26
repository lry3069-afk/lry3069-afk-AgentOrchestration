"""FastAPI application server."""

import os
from typing import Dict

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from .routes import router
from .middleware import AuthMiddleware, CSRFMiddleware, RateLimitMiddleware, LoggingMiddleware


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

    app.add_middleware(CSRFMiddleware)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(LoggingMiddleware)

    app.include_router(router, prefix="/api/v2")

    @app.get("/health")
    async def health():
        return {"status": "healthy", "version": "2.4.1"}

    return app