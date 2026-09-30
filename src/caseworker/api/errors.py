"""RFC 9457 Problem Details for HTTP APIs error representations and exception handlers."""

from __future__ import annotations

import logging
from typing import Any
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError

from caseworker.domain.errors import (
    ApprovalValidationError,
    CaseworkerError,
    DomainValidationError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    OptimisticLockError,
)

logger = logging.getLogger("caseworker.api.errors")

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemDetail(BaseModel):
    """RFC 9457 compliant error detail structure."""

    type: str = Field(..., description="A URI reference that identifies the problem type.")
    title: str = Field(..., description="A short, human-readable summary of the problem type.")
    status: int = Field(..., description="The HTTP status code.")
    detail: str = Field(..., description="A human-readable explanation of this specific error.")
    instance: str | None = Field(None, description="A URI reference identifying the specific occurrence.")
    extensions: dict[str, Any] = Field(default_factory=dict, description="Additional context fields.")

    def to_response(self, headers: dict[str, str] | None = None) -> JSONResponse:
        content = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
        }
        if self.instance:
            content["instance"] = self.instance
        if self.extensions:
            content.update(self.extensions)

        resp_headers = dict(headers or {})
        return JSONResponse(
            status_code=self.status,
            content=content,
            media_type=PROBLEM_MEDIA_TYPE,
            headers=resp_headers,
        )


def _get_request_instance(request: Request) -> str:
    req_id = getattr(request.state, "correlation_id", None) or request.headers.get("X-Request-ID")
    if req_id:
        return f"urn:request:{req_id}"
    return str(request.url.path)


async def entity_not_found_handler(request: Request, exc: EntityNotFoundError) -> JSONResponse:
    problem = ProblemDetail(
        type="urn:caseworker:error:not-found",
        title="Resource Not Found",
        status=404,
        detail=str(exc),
        instance=_get_request_instance(request),
    )
    return problem.to_response()


async def invalid_transition_handler(request: Request, exc: InvalidStateTransitionError) -> JSONResponse:
    problem = ProblemDetail(
        type="urn:caseworker:error:conflict",
        title="Invalid State Transition",
        status=409,
        detail=str(exc),
        instance=_get_request_instance(request),
        extensions={
            "entity_type": getattr(exc, "entity_type", "unknown"),
            "current_state": getattr(exc, "current_state", getattr(exc, "current_status", None)),
            "attempted_state": getattr(exc, "attempted_state", getattr(exc, "attempted_status", None)),
        },
    )
    return problem.to_response()


async def optimistic_lock_handler(request: Request, exc: OptimisticLockError) -> JSONResponse:
    problem = ProblemDetail(
        type="urn:caseworker:error:precondition-failed",
        title="Precondition Failed",
        status=412,
        detail=str(exc),
        instance=_get_request_instance(request),
        extensions={
            "aggregate_type": exc.aggregate_type,
            "aggregate_id": exc.aggregate_id,
            "expected_version": exc.expected_version,
            "actual_version": exc.actual_version,
        },
    )
    return problem.to_response()


async def approval_validation_handler(request: Request, exc: ApprovalValidationError) -> JSONResponse:
    problem = ProblemDetail(
        type="urn:caseworker:error:conflict",
        title="Approval Validation Failed",
        status=409,
        detail=str(exc),
        instance=_get_request_instance(request),
    )
    return problem.to_response()


async def domain_validation_handler(request: Request, exc: DomainValidationError) -> JSONResponse:
    problem = ProblemDetail(
        type="urn:caseworker:error:validation",
        title="Validation Error",
        status=422,
        detail=str(exc),
        instance=_get_request_instance(request),
    )
    return problem.to_response()


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Sanitize errors for client
    cleaned_errors: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = [str(x) for x in err.get("loc", ())]
        cleaned_errors.append({
            "loc": loc,
            "msg": err.get("msg", "Invalid value"),
            "type": err.get("type", "value_error"),
        })

    problem = ProblemDetail(
        type="urn:caseworker:error:validation",
        title="Request Validation Error",
        status=422,
        detail="The request payload or parameters failed validation.",
        instance=_get_request_instance(request),
        extensions={"errors": cleaned_errors},
    )
    return problem.to_response()


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    titles = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        412: "Precondition Failed",
        422: "Unprocessable Content",
        428: "Precondition Required",
    }
    title = titles.get(exc.status_code, "HTTP Error")
    type_slug = title.lower().replace(" ", "-")

    problem = ProblemDetail(
        type=f"urn:caseworker:error:{type_slug}",
        title=title,
        status=exc.status_code,
        detail=str(exc.detail) if exc.detail else title,
        instance=_get_request_instance(request),
    )
    return problem.to_response(headers=exc.headers)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server exception processing request %s", request.url.path, exc_info=exc)
    problem = ProblemDetail(
        type="urn:caseworker:error:internal",
        title="Internal Server Error",
        status=500,
        detail="An unexpected internal error occurred. Please contact system administrator.",
        instance=_get_request_instance(request),
    )
    return problem.to_response()


def register_error_handlers(app: FastAPI) -> None:
    """Register all domain and framework exception handlers to produce RFC 9457 responses."""
    app.add_exception_handler(EntityNotFoundError, entity_not_found_handler)
    app.add_exception_handler(InvalidStateTransitionError, invalid_transition_handler)
    app.add_exception_handler(OptimisticLockError, optimistic_lock_handler)
    app.add_exception_handler(ApprovalValidationError, approval_validation_handler)
    app.add_exception_handler(DomainValidationError, domain_validation_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
