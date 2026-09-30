"""FastAPI application factory for JackVerse Caseworker API service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from caseworker.api.config import APIConfig
from caseworker.api.errors import register_error_handlers
from caseworker.api.middleware import CorrelationIdMiddleware, SecurityHeadersMiddleware
from caseworker.api.routes import (
    actions_router,
    approvals_router,
    cases_router,
    claims_router,
    context_router,
    events_router,
    health_router,
    missions_router,
    opportunities_router,
)
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


def create_app(
    config: APIConfig | None = None,
    storage: SQLiteCaseworkerStorage | None = None,
) -> FastAPI:
    """Create and configure a production-ready Caseworker FastAPI application."""
    effective_config = config or APIConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # Startup: initialize database and run schema migrations once
        active_storage = storage or SQLiteCaseworkerStorage(db_path=effective_config.db_path)
        app.state.config = effective_config
        app.state.storage = active_storage
        yield
        # Shutdown
        if active_storage:
            active_storage.close()

    app = FastAPI(
        title="JackVerse Caseworker API",
        description="Autonomous caseworker agent service for proactive opportunities, cases, and actions.",
        version="0.3.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Global app state for tests / non-lifespan test clients
    app.state.config = effective_config
    if storage is not None:
        app.state.storage = storage

    # Register error handlers for RFC 9457 Problem Details
    register_error_handlers(app)

    # Security & correlation middleware
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=effective_config.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag", "X-Request-ID"],
    )

    # Mount API routers
    app.include_router(health_router)
    app.include_router(missions_router)
    app.include_router(cases_router)
    app.include_router(opportunities_router)
    app.include_router(context_router)
    app.include_router(claims_router)
    app.include_router(actions_router)
    app.include_router(approvals_router)
    app.include_router(events_router)

    return app
