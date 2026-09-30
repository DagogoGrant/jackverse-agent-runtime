"""Declarative policy rule definitions and deterministic evaluation engine."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import fnmatch
import os
from typing import Sequence

from harness.permissions.base import (
    PermissionDecision,
    PermissionRequest,
    RiskLevel,
)
from harness.tools.base import ToolSource

DECISION_PRECEDENCE: dict[PermissionDecision, int] = {
    PermissionDecision.DENY: 3,
    PermissionDecision.REQUIRE_CONFIRMATION: 2,
    PermissionDecision.ALLOW: 1,
}


@dataclass(frozen=True)
class PolicyRule:
    """Declarative authorization rule with priority and criteria."""

    name: str
    decision: PermissionDecision
    priority: int = 100
    tool_pattern: str = "*"
    source: ToolSource | None = None
    server_name: str | None = None
    role: str | None = None
    risk_level: RiskLevel = RiskLevel.MUTATING
    declaration_index: int = 0
    resource_pattern: str | None = None
    argument_matches: Mapping[str, str] | None = None
    match_risk_level: RiskLevel | None = None


class PolicyEngine:
    """Deterministic authorization policy evaluation engine.

    Precedence Algorithm:
        1. Priority integer descending (e.g., 200 > 100).
        2. Tie-break: DENY > REQUIRE_CONFIRMATION > ALLOW.
        3. Tie-break: Declaration order (earlier rules win).
        4. Closed Fallback: If no rule matches, default_stance (DENY) is applied.
    """

    def __init__(
        self,
        rules: Sequence[PolicyRule] | None = None,
        default_stance: PermissionDecision = PermissionDecision.DENY,
    ) -> None:
        self.default_stance = default_stance
        self._rules: list[PolicyRule] = []
        if rules:
            for idx, rule in enumerate(rules):
                # Ensure sequential declaration index is assigned
                indexed_rule = PolicyRule(
                    name=rule.name,
                    decision=rule.decision,
                    priority=rule.priority,
                    tool_pattern=rule.tool_pattern,
                    source=rule.source,
                    server_name=rule.server_name,
                    role=rule.role,
                    risk_level=rule.risk_level,
                    declaration_index=idx,
                    resource_pattern=rule.resource_pattern,
                    argument_matches=rule.argument_matches,
                    match_risk_level=rule.match_risk_level,
                )
                self._rules.append(indexed_rule)

    @property
    def rules(self) -> list[PolicyRule]:
        """Configured policy rules."""
        return list(self._rules)

    def matches(self, rule: PolicyRule, request: PermissionRequest) -> bool:
        """Evaluate whether a rule's criteria match the permission request."""
        # 1. Check agent role constraint
        if rule.role is not None and not fnmatch.fnmatch(request.agent_role, rule.role):
            return False

        # 2. Check tool source constraint
        if rule.source is not None and request.tool_source != rule.source:
            return False

        # 3. Check server name constraint
        if rule.server_name is not None and request.server_name != rule.server_name:
            return False

        # 4. Check tool pattern (matches against simple name or full canonical identity)
        if not (
            fnmatch.fnmatch(request.tool_name, rule.tool_pattern)
            or fnmatch.fnmatch(request.canonical_tool_identity, rule.tool_pattern)
        ):
            return False

        # 5. Check risk level constraint if rule specifies match_risk_level
        if rule.match_risk_level is not None and request.risk_level != rule.match_risk_level:
            return False

        # 6. Check resource pattern constraint (glob against resource_descriptor or basename)
        if rule.resource_pattern is not None:
            if request.resource_descriptor is None:
                return False
            res = request.resource_descriptor
            res_base = os.path.basename(res)
            pattern = rule.resource_pattern
            if not (
                fnmatch.fnmatch(res, pattern)
                or fnmatch.fnmatch(res_base, pattern)
                or fnmatch.fnmatch(res, f"*/{pattern.lstrip('/')}")
            ):
                return False

        # 7. Check argument matches constraint (exact or glob pattern on arguments)
        if rule.argument_matches is not None:
            for arg_key, arg_pattern in rule.argument_matches.items():
                if arg_key not in request.arguments:
                    return False
                arg_val = str(request.arguments[arg_key])
                arg_base = os.path.basename(arg_val)
                if not (
                    fnmatch.fnmatch(arg_val, arg_pattern)
                    or fnmatch.fnmatch(arg_base, arg_pattern)
                    or fnmatch.fnmatch(arg_val, f"*/{arg_pattern.lstrip('/')}")
                ):
                    return False

        return True

    def evaluate(self, request: PermissionRequest) -> tuple[PermissionDecision, str | None, RiskLevel]:
        """Evaluate request against rules deterministically.

        Returns:
            tuple of (decision, matched_rule_name, risk_level)
        """
        matching = [rule for rule in self._rules if self.matches(rule, request)]

        if not matching:
            fallback_risk = request.risk_level if request.risk_level is not None else RiskLevel.MUTATING
            return self.default_stance, None, fallback_risk

        # Sort by:
        # 1. priority descending
        # 2. decision precedence descending (DENY > REQUIRE_CONFIRMATION > ALLOW)
        # 3. declaration_index ascending (earlier declared rule wins -> -declaration_index in reverse)
        matching.sort(
            key=lambda r: (r.priority, DECISION_PRECEDENCE[r.decision], -r.declaration_index),
            reverse=True,
        )

        winning_rule = matching[0]
        if request.risk_level is not None:
            if request.risk_level in (RiskLevel.SENSITIVE, RiskLevel.CRITICAL):
                final_risk = request.risk_level
            elif winning_rule.risk_level != RiskLevel.MUTATING:
                final_risk = winning_rule.risk_level
            else:
                final_risk = request.risk_level
        else:
            final_risk = winning_rule.risk_level

        return winning_rule.decision, winning_rule.name, final_risk
