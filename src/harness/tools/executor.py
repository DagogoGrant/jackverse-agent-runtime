from collections.abc import Mapping
import logging
import time
from typing import Any
import uuid

from harness.permissions.base import (
    PermissionDecision,
    PermissionRequest,
    canonical_tool_identity,
    compute_arguments_fingerprint,
    summarize_arguments,
)
from harness.permissions.manager import PermissionManager
from harness.runtime.context import (
    call_id_scope,
    get_current_context,
)
from harness.runtime.events import (
    FailureCategory,
    LifecycleEventBus,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
    ToolCallStatus,
)
from harness.tools.base import ErrorCode, Tool, ToolResult
from harness.tools.validation import ToolContractValidator

logger = logging.getLogger("harness.executor")


class ToolExecutor:
    """Single execution boundary for validating and executing tools."""

    def __init__(
        self,
        validator: ToolContractValidator | None = None,
        max_observation_chars: int | None = None,
        event_bus: LifecycleEventBus | None = None,
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self._validator = validator or ToolContractValidator()
        self._max_observation_chars = max_observation_chars
        self._event_bus = event_bus
        self._permission_manager = permission_manager or PermissionManager.create_default(event_bus=event_bus)

    @property
    def max_observation_chars(self) -> int | None:
        return self._max_observation_chars

    @max_observation_chars.setter
    def max_observation_chars(self, value: int | None) -> None:
        self._max_observation_chars = value

    @property
    def event_bus(self) -> LifecycleEventBus | None:
        """The LifecycleEventBus configured for tool execution telemetry."""
        return self._event_bus

    @property
    def permission_manager(self) -> PermissionManager:
        """The authorization and policy manager for tool execution."""
        return self._permission_manager

    @staticmethod
    def _apply_observation_ceiling(content: str, max_chars: int) -> str:
        """Deterministically bound oversized observation content into an explicit textual envelope.

        Invariant:
            len(final_envelope) <= max_chars
        """
        if len(content) <= max_chars:
            return content

        original_chars = len(content)

        def build_envelope(k: int) -> str:
            prefix = content[:k]
            return (
                f"[OBSERVATION PARTIALLY SHOWN]\n"
                f"original_chars: {original_chars}\n"
                f"shown_chars: {len(prefix)}\n"
                f"content:\n"
                f"{prefix}"
            )

        rough_overhead = len(build_envelope(0)) + 6
        available = max(0, max_chars - rough_overhead)
        envelope = build_envelope(available)

        while len(envelope) > max_chars and available > 0:
            available -= (len(envelope) - max_chars)
            available = max(0, available)
            envelope = build_envelope(available)

        while available < len(content):
            next_env = build_envelope(available + 1)
            if len(next_env) <= max_chars:
                available += 1
                envelope = next_env
            else:
                break

        if len(envelope) > max_chars:
            return content[:max_chars]

        return envelope

    def execute(
        self,
        tool: Tool,
        arguments: Mapping[str, object],
        *,
        call_id: str | None = None,
    ) -> ToolResult:
        """Execute the tool across the central validation and safety boundary.

        1. Syntactic validation: Validates argument schema against tool.spec.input_schema.
        2. Semantic execution: Invokes tool.execute() where tool-specific logic runs.
        3. Exception containment: Traps unexpected exceptions, logs full traceback internally,
           and returns a clean generic error without leaking internals.
        4. Observation ceiling: Bounds oversized output into an explicit textual envelope
           satisfying len(content) <= max_observation_chars.
        """
        effective_call_id = call_id or uuid.uuid4().hex
        ctx = get_current_context()

        if self._event_bus and ctx:
            self._event_bus.publish(
                ToolCallStartedEvent(
                    timestamp=time.time(),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                    root_run_id=ctx.root_run_id,
                    parent_run_id=ctx.parent_run_id,
                    agent_id=ctx.agent_id,
                    agent_role=ctx.agent_role,
                    call_id=effective_call_id,
                    tool_name=tool.spec.name,
                    tool_source=tool.spec.source,
                    server_name=tool.spec.server_name,
                )
            )

        start_time = time.perf_counter()
        status = ToolCallStatus.EXECUTION_ERROR
        is_error = False
        observation_length = 0
        failure_category: FailureCategory | None = None
        error_type: str | None = None
        error_code: str | None = None
        error_message: str | None = None
        result: ToolResult | None = None

        try:
            # Step 1: Syntactic contract validation at boundary
            is_valid, err_msg = self._validator.validate(tool.spec.input_schema, arguments)
            if not is_valid:
                status = ToolCallStatus.VALIDATION_ERROR
                is_error = True
                failure_category = FailureCategory.VALIDATION
                error_type = "ContractValidationError"
                error_code = ErrorCode.INVALID_ARGUMENT.value
                error_message = err_msg[:200]
                result = ToolResult(
                    content=f"Schema validation error: {err_msg}",
                    is_error=True,
                    error_code=ErrorCode.INVALID_ARGUMENT,
                )
                observation_length = len(result.content)
                return result

            # Step 1.5: Action authorization gate (mandatory policy boundary)
            run_id = ctx.run_id if ctx else "default-run"
            agent_id = ctx.agent_id if ctx else "default-agent"
            agent_role = ctx.agent_role if ctx else "agent"
            delegation_depth = ctx.delegation_depth if ctx else 0

            canon_id = canonical_tool_identity(
                tool.spec.source,
                tool.spec.server_name,
                tool.spec.name,
            )
            fingerprint = compute_arguments_fingerprint(arguments)
            summary = summarize_arguments(tool.spec.name, arguments)

            # Canonicalize target filesystem or service resource descriptor
            resource_descriptor: str | None = None
            for arg_key in ("path", "target_file", "directory"):
                if arg_key in arguments and isinstance(arguments[arg_key], str):
                    raw_path = arguments[arg_key]
                    if self._permission_manager.workspace:
                        try:
                            resolved_path = self._permission_manager.workspace.resolve(raw_path)
                            resource_descriptor = str(resolved_path)
                        except Exception:
                            resource_descriptor = str(raw_path)
                    else:
                        resource_descriptor = str(raw_path)
                    break

            if resource_descriptor is None and "station_id" in arguments:
                resource_descriptor = f"station:{arguments['station_id']}"
            elif resource_descriptor is None and "origin" in arguments and "destination" in arguments:
                resource_descriptor = f"{arguments['origin']}->{arguments['destination']}"

            perm_request = PermissionRequest(
                run_id=run_id,
                agent_id=agent_id,
                agent_role=agent_role,
                delegation_depth=delegation_depth,
                call_id=effective_call_id,
                canonical_tool_identity=canon_id,
                tool_name=tool.spec.name,
                tool_source=tool.spec.source,
                server_name=tool.spec.server_name,
                arguments=arguments,
                arguments_fingerprint=fingerprint,
                arguments_summary=summary,
                resource_descriptor=resource_descriptor,
            )

            auth_result = self._permission_manager.authorize(perm_request)
            if auth_result.decision != PermissionDecision.ALLOW:
                status = ToolCallStatus.PERMISSION_DENIED
                is_error = True
                failure_category = FailureCategory.PERMISSION
                error_type = "PermissionDeniedError"
                error_code = ErrorCode.PERMISSION_DENIED.value
                error_message = (auth_result.reason or "Permission denied.")[:200]
                result = ToolResult(
                    content=f"Permission denied: {auth_result.reason or 'Action authorization rejected.'}",
                    is_error=True,
                    error_code=ErrorCode.PERMISSION_DENIED,
                )
                observation_length = len(result.content)
                return result

            # If confirmation was required and granted, verify token binding and consume it
            if auth_result.confirmation_record is not None:
                token_id = auth_result.confirmation_record.confirmation_id
                if not self._permission_manager.verify_confirmation_token(token_id, perm_request):
                    status = ToolCallStatus.PERMISSION_DENIED
                    is_error = True
                    failure_category = FailureCategory.PERMISSION
                    error_type = "PermissionDeniedError"
                    error_code = ErrorCode.PERMISSION_DENIED.value
                    error_message = "Confirmation token binding verification failed."
                    result = ToolResult(
                        content="Permission denied: Confirmation token binding verification failed.",
                        is_error=True,
                        error_code=ErrorCode.PERMISSION_DENIED,
                    )
                    observation_length = len(result.content)
                    return result
                self._permission_manager.consume_confirmation_token(token_id)

            # Step 2: Tool execution with exception containment
            try:
                with call_id_scope(effective_call_id):
                    result = tool.execute(arguments)
            except Exception as e:
                logger.exception(f"Unhandled exception during execution of tool '{tool.spec.name}': {e}")
                status = ToolCallStatus.EXECUTION_ERROR
                is_error = True
                failure_category = FailureCategory.TOOL
                error_type = type(e).__name__
                error_code = ErrorCode.INTERNAL_ERROR.value
                error_message = str(e)[:200]
                result = ToolResult(
                    content="An internal tool execution error occurred.",
                    is_error=True,
                    error_code=ErrorCode.INTERNAL_ERROR,
                )
                observation_length = len(result.content)
                return result

            if result.is_error:
                status = ToolCallStatus.EXECUTION_ERROR
                is_error = True
                failure_category = FailureCategory.TOOL
                error_type = "ToolExecutionError"
                error_code = result.error_code.value if result.error_code else None
                error_message = result.content[:200]
            else:
                status = ToolCallStatus.SUCCESS
                is_error = False

            # Step 3: Layer 2 observation ceiling (format-agnostic emergency safety guard)
            if self._max_observation_chars is not None and len(result.content) > self._max_observation_chars:
                bounded_content = self._apply_observation_ceiling(result.content, self._max_observation_chars)
                status = ToolCallStatus.CEILING_APPLIED
                result = ToolResult(
                    content=bounded_content,
                    is_error=result.is_error,
                    error_code=result.error_code,
                )

            observation_length = len(result.content)
            return result
        finally:
            if self._event_bus and ctx:
                duration = time.perf_counter() - start_time
                self._event_bus.publish(
                    ToolCallFinishedEvent(
                        timestamp=time.time(),
                        trace_id=ctx.trace_id,
                        run_id=ctx.run_id,
                        root_run_id=ctx.root_run_id,
                        parent_run_id=ctx.parent_run_id,
                        agent_id=ctx.agent_id,
                        agent_role=ctx.agent_role,
                        call_id=effective_call_id,
                        tool_name=tool.spec.name,
                        tool_source=tool.spec.source,
                        duration_seconds=duration,
                        status=status,
                        is_error=is_error,
                        observation_length=observation_length,
                        server_name=tool.spec.server_name,
                        failure_category=failure_category,
                        error_type=error_type,
                        error_code=error_code,
                        error_message=error_message,
                    )
                )
