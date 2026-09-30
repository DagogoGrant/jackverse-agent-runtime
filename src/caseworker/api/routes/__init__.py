"""Export all caseworker API routers."""

from __future__ import annotations

from caseworker.api.routes.actions import router as actions_router
from caseworker.api.routes.approvals import router as approvals_router
from caseworker.api.routes.cases import router as cases_router
from caseworker.api.routes.claims import router as claims_router
from caseworker.api.routes.context import router as context_router
from caseworker.api.routes.events import router as events_router
from caseworker.api.routes.health import router as health_router
from caseworker.api.routes.missions import router as missions_router
from caseworker.api.routes.opportunities import router as opportunities_router

__all__ = [
    "actions_router",
    "approvals_router",
    "cases_router",
    "claims_router",
    "context_router",
    "events_router",
    "health_router",
    "missions_router",
    "opportunities_router",
]
