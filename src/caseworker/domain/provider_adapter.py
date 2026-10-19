"""Provider adapter interfaces and simulated execution provider for Phase 5.3.3."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
import uuid

from caseworker.domain.enums import (
    ExecutionErrorCategory,
    ExecutionProviderKind,
    ExecutionReceiptStatus,
)
from caseworker.domain.execution_plan import (
    ExecutionFieldMapping,
    ExecutionScreeningResponse,
    ExecutionUploadAsset,
    ExternalExecutionPlan,
)
from caseworker.domain.package import PreparationPackage


class SimulationMode(StrEnum):
    """Deterministic simulation modes for testing the governed execution pipeline."""

    SUCCESS = "success"
    PRE_STAGE_FAILURE = "pre_stage_failure"
    STAGING_FAILURE = "staging_failure"
    SUBMIT_CONFIRMED_FAILURE = "submit_confirmed_failure"
    SUBMIT_TIMEOUT = "submit_timeout"
    SUBMIT_AMBIGUOUS = "submit_ambiguous"


@dataclass(frozen=True)
class StagingOutcome:
    """Outcome of provider pre-submit staging."""

    success: bool
    staged_field_count: int = 0
    staged_upload_count: int = 0
    error_category: ExecutionErrorCategory | None = None
    error_message: str | None = None
    submit_control_ready: bool = False


@dataclass(frozen=True)
class ProviderSubmissionResult:
    """Outcome of irreversible submission dispatch."""

    status: ExecutionReceiptStatus
    external_confirmation_id: str | None = None
    error_category: ExecutionErrorCategory | None = None
    error_message: str | None = None
    raw_response_snippet: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionProviderAdapter(ABC):
    """Abstract interface defining the provider execution protocol."""

    @property
    @abstractmethod
    def provider_kind(self) -> ExecutionProviderKind:
        """Provider adapter classification."""
        ...

    @abstractmethod
    def stage_submission(
        self,
        plan: ExternalExecutionPlan,
        package: PreparationPackage,
        upload_assets: list[ExecutionUploadAsset],
    ) -> StagingOutcome:
        """Stage materials at target destination up to the Last Reversible Point.

        Validates endpoints, matches schema fields, and prepares attachments without triggering submit.
        """
        ...

    @abstractmethod
    def execute_irreversible_submit(
        self,
        plan: ExternalExecutionPlan,
        attempt_id: str,
    ) -> ProviderSubmissionResult:
        """Cross the irreversible boundary by triggering the final submission control."""
        ...


class SimulatedExecutionProvider(ExecutionProviderAdapter):
    """Deterministic simulated provider adapter for testing governed execution and failure discipline.

    Zero outbound network requests and zero browser processes are launched.
    """

    def __init__(
        self,
        mode: SimulationMode | str | None = None,
        simulation_mode: SimulationMode | str | None = None,
    ) -> None:
        effective_mode = mode or simulation_mode or SimulationMode.SUCCESS
        if isinstance(effective_mode, str):
            effective_mode = SimulationMode(effective_mode)
        self.mode = effective_mode

    @property
    def provider_kind(self) -> ExecutionProviderKind:
        return ExecutionProviderKind.SIMULATED_TEST

    def stage_submission(
        self,
        plan: ExternalExecutionPlan,
        package: PreparationPackage,
        upload_assets: list[ExecutionUploadAsset],
    ) -> StagingOutcome:
        if self.mode == SimulationMode.PRE_STAGE_FAILURE:
            return StagingOutcome(
                success=False,
                staged_field_count=0,
                staged_upload_count=0,
                error_category=ExecutionErrorCategory.PRE_SUBMIT_NETWORK,
                error_message="Simulated connection failure during initial destination handshake.",
                submit_control_ready=False,
            )

        if self.mode == SimulationMode.STAGING_FAILURE:
            return StagingOutcome(
                success=False,
                staged_field_count=len(plan.field_mappings),
                staged_upload_count=0,
                error_category=ExecutionErrorCategory.STAGING_VALIDATION_FAILED,
                error_message="Simulated staging failure: target document upload slot rejected attachment MIME type.",
                submit_control_ready=False,
            )

        # Happy staging: verify inputs match plan
        field_count = len(plan.field_mappings) + len(plan.screening_responses)
        upload_count = len(upload_assets)
        return StagingOutcome(
            success=True,
            staged_field_count=field_count,
            staged_upload_count=upload_count,
            submit_control_ready=True,
        )

    def execute_irreversible_submit(
        self,
        plan: ExternalExecutionPlan,
        attempt_id: str,
    ) -> ProviderSubmissionResult:
        if self.mode in (SimulationMode.SUBMIT_TIMEOUT, SimulationMode.SUBMIT_AMBIGUOUS):
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.AMBIGUOUS_SUBMISSION,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_TIMEOUT,
                error_message="Simulated submission dispatch timeout: destination did not return confirmation within deadline.",
                raw_response_snippet="HTTP 504 Gateway Timeout: upstream portal acknowledgment pending",
                provider_metadata={
                    "http_status": 504,
                    "duration_ms": 30000,
                    "response_category": "timeout",
                    "mode": self.mode.value,
                },
            )

        if self.mode == SimulationMode.SUBMIT_CONFIRMED_FAILURE:
            return ProviderSubmissionResult(
                status=ExecutionReceiptStatus.CONFIRMED_FAILED,
                external_confirmation_id=None,
                error_category=ExecutionErrorCategory.SUBMIT_REJECTED,
                error_message="Simulated target rejection: destination portal responded with 422 Unprocessable Entity.",
                raw_response_snippet="HTTP 422: External submission deadline has passed for this requisition",
                provider_metadata={
                    "http_status": 422,
                    "duration_ms": 450,
                    "response_category": "rejected",
                    "mode": self.mode.value,
                },
            )

        conf_id = f"TX-APEX-{uuid.uuid4().hex[:12].upper()}"
        return ProviderSubmissionResult(
            status=ExecutionReceiptStatus.VERIFIED_SUCCESS,
            external_confirmation_id=conf_id,
            provider_metadata={
                "http_status": 200,
                "duration_ms": 620,
                "response_category": "success",
                "confirmation_reference": conf_id,
                "mode": self.mode.value,
            },
        )
