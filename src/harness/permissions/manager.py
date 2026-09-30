"""Permission manager orchestrating policy evaluation, confirmation tokens, and telemetry."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import logging
import threading
import time
from typing import TYPE_CHECKING
import uuid

from harness.permissions.base import (
    AuthorizationResult,
    ConfirmationHandler,
    ConfirmationRecord,
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
)
from harness.permissions.confirmation import DeterministicConfirmationHandler
from harness.permissions.policy import PolicyEngine, PolicyRule
from harness.tools.base import ToolSource
from harness.tools.workspace import Workspace

if TYPE_CHECKING:
    from harness.config import PermissionsConfig
    from harness.runtime.events import LifecycleEventBus

logger = logging.getLogger("harness.permissions")


@dataclass
class PermissionManager:
    """Orchestrates authorization policy evaluation and human confirmation handling."""

    policy_engine: PolicyEngine
    confirmation_handler: ConfirmationHandler
    workspace: Workspace | None = None
    event_bus: LifecycleEventBus | None = None
    _confirmations: dict[str, ConfirmationRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def authorize(self, request: PermissionRequest) -> AuthorizationResult:
        """Evaluate request against policy rules and confirmation protocol if required."""
        if request.risk_level is None:
            from harness.permissions.risk import RiskClassifier
            classified_risk = RiskClassifier.classify(request)
            request = replace(request, risk_level=classified_risk)

        decision, matched_rule, risk_level = self.policy_engine.evaluate(request)
        from harness.runtime.context import get_current_context
        ctx = get_current_context()

        # Publish initial policy evaluation decision event
        if self.event_bus and ctx:
            from harness.runtime.events import PermissionEvaluatedEvent
            self.event_bus.publish(
                PermissionEvaluatedEvent(
                    timestamp=time.time(),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                    root_run_id=ctx.root_run_id,
                    parent_run_id=ctx.parent_run_id,
                    agent_id=ctx.agent_id,
                    agent_role=ctx.agent_role,
                    call_id=request.call_id,
                    canonical_tool_identity=request.canonical_tool_identity,
                    tool_name=request.tool_name,
                    tool_source=request.tool_source,
                    decision=decision,
                    matched_rule=matched_rule,
                    risk_level=risk_level,
                    arguments_fingerprint=request.arguments_fingerprint,
                )
            )

        if decision == PermissionDecision.ALLOW:
            return AuthorizationResult(
                decision=PermissionDecision.ALLOW,
                matched_rule=matched_rule,
                risk_level=risk_level,
            )

        if decision == PermissionDecision.DENY:
            reason = f"Action '{request.canonical_tool_identity}' denied by policy rule '{matched_rule or 'default_stance'}'."
            return AuthorizationResult(
                decision=PermissionDecision.DENY,
                matched_rule=matched_rule,
                risk_level=risk_level,
                reason=reason,
            )

        # Decision is REQUIRE_CONFIRMATION: prompt confirmation handler
        start_time = time.perf_counter()
        approved = self.confirmation_handler.request_confirmation(request)
        duration = time.perf_counter() - start_time
        confirmation_id = uuid.uuid4().hex

        record = ConfirmationRecord(
            confirmation_id=confirmation_id,
            run_id=request.run_id,
            call_id=request.call_id,
            agent_id=request.agent_id,
            agent_role=request.agent_role,
            canonical_tool_identity=request.canonical_tool_identity,
            arguments_fingerprint=request.arguments_fingerprint,
            created_at=time.time(),
            consumed=False,
        )

        with self._lock:
            self._confirmations[confirmation_id] = record

        if self.event_bus and ctx:
            from harness.runtime.events import ConfirmationResolvedEvent
            self.event_bus.publish(
                ConfirmationResolvedEvent(
                    timestamp=time.time(),
                    trace_id=ctx.trace_id,
                    run_id=ctx.run_id,
                    root_run_id=ctx.root_run_id,
                    parent_run_id=ctx.parent_run_id,
                    agent_id=ctx.agent_id,
                    agent_role=ctx.agent_role,
                    call_id=request.call_id,
                    confirmation_id=confirmation_id,
                    canonical_tool_identity=request.canonical_tool_identity,
                    approved=approved,
                    duration_seconds=duration,
                )
            )

        if approved:
            return AuthorizationResult(
                decision=PermissionDecision.ALLOW,
                matched_rule=matched_rule,
                risk_level=risk_level,
                confirmation_record=record,
            )
        else:
            return AuthorizationResult(
                decision=PermissionDecision.DENY,
                matched_rule=matched_rule,
                risk_level=risk_level,
                reason=f"Action '{request.canonical_tool_identity}' denied by user confirmation.",
                confirmation_record=record,
            )

    def verify_confirmation_token(self, confirmation_id: str, request: PermissionRequest) -> bool:
        """Verify token binding across all 6 contextual fields and single-use guarantee for anti-TOCTOU / anti-replay."""
        with self._lock:
            record = self._confirmations.get(confirmation_id)
            if record is None or record.consumed:
                return False
            # Check run isolation
            if record.run_id != request.run_id:
                return False
            # Check call isolation
            if record.call_id != request.call_id:
                return False
            # Check agent identity and role isolation
            if record.agent_id != request.agent_id or record.agent_role != request.agent_role:
                return False
            # Check canonical identity binding
            if record.canonical_tool_identity != request.canonical_tool_identity:
                return False
            # Check SHA-256 fingerprint binding (anti-TOCTOU)
            if record.arguments_fingerprint != request.arguments_fingerprint:
                return False
            return True

    def consume_confirmation_token(self, confirmation_id: str) -> bool:
        """Mark a confirmation token as consumed for single-use anti-replay enforcement."""
        with self._lock:
            record = self._confirmations.get(confirmation_id)
            if record is None or record.consumed:
                return False
            record.consumed = True
            return True

    @classmethod
    def create_default(
        cls,
        confirmation_handler: ConfirmationHandler | None = None,
        workspace: Workspace | None = None,
        event_bus: LifecycleEventBus | None = None,
        default_stance: PermissionDecision = PermissionDecision.DENY,
    ) -> PermissionManager:
        """Construct standard fallback policy manager.

        Default Stance: DENY (closed fallback; unknown actions are denied).
        Rules:
            1. Filesystem read queries: ALLOW (read_only).
            2. Filesystem mutations: REQUIRE_CONFIRMATION (mutating).
            3. MCP transport queries: ALLOW (read_only).
            4. Common test tools (dummy, echo, fail): ALLOW (for test suite isolation).
        """
        handler = confirmation_handler or DeterministicConfirmationHandler(always_allow=True)
        rules = [
            PolicyRule(
                name="allow_filesystem_reads",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="read_file",
                source=ToolSource.BUILTIN,
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_filesystem_list",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="list_directory",
                source=ToolSource.BUILTIN,
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_filesystem_search",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="search_files",
                source=ToolSource.BUILTIN,
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="confirm_filesystem_modify",
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                priority=100,
                tool_pattern="modify_file",
                source=ToolSource.BUILTIN,
                risk_level=RiskLevel.MUTATING,
            ),
            PolicyRule(
                name="confirm_filesystem_create_file",
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                priority=100,
                tool_pattern="create_file",
                source=ToolSource.BUILTIN,
                risk_level=RiskLevel.MUTATING,
            ),
            PolicyRule(
                name="confirm_filesystem_create_dir",
                decision=PermissionDecision.REQUIRE_CONFIRMATION,
                priority=100,
                tool_pattern="create_directory",
                source=ToolSource.BUILTIN,
                risk_level=RiskLevel.MUTATING,
            ),
            PolicyRule(
                name="allow_transport_mcp",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="*",
                source=ToolSource.MCP,
                server_name="transport_service",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_transport_find",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="find_connection",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_transport_station",
                decision=PermissionDecision.ALLOW,
                priority=100,
                tool_pattern="get_station_info",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_tools",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="dummy*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_echo",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="*echo*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_fail",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="fail*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_platform",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="fetch_platform_info",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_add",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="add",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_sample",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="*sample*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_exploding",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="*exploding*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_error",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="*error*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_weather",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="*weather*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_numbered_tools",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="tool_*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_stations",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="*station*",
                risk_level=RiskLevel.READ_ONLY,
            ),
            PolicyRule(
                name="allow_test_http",
                decision=PermissionDecision.ALLOW,
                priority=10,
                tool_pattern="http_*",
                risk_level=RiskLevel.READ_ONLY,
            ),
        ]
        engine = PolicyEngine(rules=rules, default_stance=default_stance)
        return cls(
            policy_engine=engine,
            confirmation_handler=handler,
            workspace=workspace,
            event_bus=event_bus,
        )

    @classmethod
    def from_config(
        cls,
        config: PermissionsConfig,
        confirmation_handler: ConfirmationHandler,
        workspace: Workspace | None = None,
        event_bus: LifecycleEventBus | None = None,
    ) -> PermissionManager:
        """Construct PermissionManager from validated application configuration."""
        default_stance = PermissionDecision(config.default_stance.lower())
        rules: list[PolicyRule] = []

        for rule_cfg in config.rules:
            src = ToolSource(rule_cfg.source.lower()) if rule_cfg.source else None
            risk = RiskLevel(rule_cfg.risk_level.lower()) if rule_cfg.risk_level else RiskLevel.MUTATING
            match_risk = (
                RiskLevel(rule_cfg.match_risk_level.lower())
                if getattr(rule_cfg, "match_risk_level", None)
                else None
            )
            rules.append(
                PolicyRule(
                    name=rule_cfg.name,
                    decision=PermissionDecision(rule_cfg.decision.lower()),
                    priority=rule_cfg.priority,
                    tool_pattern=rule_cfg.tool_pattern,
                    source=src,
                    server_name=rule_cfg.server_name,
                    role=rule_cfg.role,
                    risk_level=risk,
                    resource_pattern=getattr(rule_cfg, "resource_pattern", None),
                    argument_matches=getattr(rule_cfg, "argument_matches", None),
                    match_risk_level=match_risk,
                )
            )

        engine = PolicyEngine(rules=rules, default_stance=default_stance)
        return cls(
            policy_engine=engine,
            confirmation_handler=confirmation_handler,
            workspace=workspace,
            event_bus=event_bus,
        )
