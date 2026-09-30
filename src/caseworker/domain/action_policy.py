"""Server-side safety policy for determining Action risk level and approval requirements."""

from __future__ import annotations

from dataclasses import dataclass

from caseworker.domain.enums import ActionType, RiskLevel


@dataclass(frozen=True)
class ActionPolicyDecision:
    """Server-governed risk assessment and approval requirement."""

    risk_level: RiskLevel
    requires_approval: bool


class ActionPolicy:
    """Server-side policy engine determining consequential risk and mandatory human sign-off.

    Guarantees:
    - Conservative Default: Unknown, custom, or external action types fail closed with HIGH risk and mandatory approval.
    - Non-Bypassable: Clients cannot downgrade an action's approval requirements via API payloads.
    """

    _RULES: dict[ActionType, ActionPolicyDecision] = {
        ActionType.DRAFT_APPLICATION: ActionPolicyDecision(
            risk_level=RiskLevel.LOW,
            requires_approval=False,
        ),
        ActionType.REQUEST_INFORMATION: ActionPolicyDecision(
            risk_level=RiskLevel.LOW,
            requires_approval=False,
        ),
        ActionType.FOLLOW_UP: ActionPolicyDecision(
            risk_level=RiskLevel.MEDIUM,
            requires_approval=False,
        ),
        ActionType.SEND_COMMUNICATION: ActionPolicyDecision(
            risk_level=RiskLevel.MEDIUM,
            requires_approval=True,
        ),
        ActionType.SUBMIT_FORM: ActionPolicyDecision(
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
        ),
        ActionType.REQUEST_REFUND: ActionPolicyDecision(
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
        ),
        ActionType.ESCALATE: ActionPolicyDecision(
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
        ),
        ActionType.CUSTOM: ActionPolicyDecision(
            risk_level=RiskLevel.HIGH,
            requires_approval=True,
        ),
    }

    @classmethod
    def evaluate(cls, action_type: ActionType | str) -> ActionPolicyDecision:
        """Determine risk level and approval requirements for an action type."""
        try:
            resolved_type = ActionType(action_type) if isinstance(action_type, str) else action_type
        except ValueError:
            return ActionPolicyDecision(risk_level=RiskLevel.HIGH, requires_approval=True)

        return cls._RULES.get(
            resolved_type,
            ActionPolicyDecision(risk_level=RiskLevel.HIGH, requires_approval=True),
        )
