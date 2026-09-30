"""Procedural failure recovery detection and deterministic lesson synthesis for Phase 3D."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any

from harness.tools.base import ErrorCode, ToolResult

logger = logging.getLogger("harness.memory.recovery")


@dataclass(frozen=True)
class ToolAttempt:
    """Record of a tool invocation attempt within a single run turn."""

    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    step: int


@dataclass(frozen=True)
class ProceduralLesson:
    """Verified, reusable procedural lesson derived from an observed recovery."""

    tool_name: str
    failing_parameter: str
    constraint: str
    lesson: str
    provenance: dict[str, Any] = field(default_factory=dict)


# Deterministic attribution rules: (regex_pattern, param_group, constraint)
SCHEMA_ATTRIBUTION_RULES: list[tuple[re.Pattern, str | None, str]] = [
    # Required argument missing
    (re.compile(r"Missing required argument '(?P<param>[^']+)'", re.IGNORECASE), "param", "required"),
    # Property type mismatch
    (re.compile(r"Argument '(?P<param>[^']+)' (?:expected type|must be an?) '?(?P<type>[^',]+)'?", re.IGNORECASE), "param", "type_mismatch"),
    # Unexpected argument
    (re.compile(r"Unexpected argument '(?P<param>[^']+)'", re.IGNORECASE), "param", "unexpected_argument"),
    # ISO datetime mismatch
    (re.compile(r"Invalid departure_time format.*Expected ISO 8601 string", re.IGNORECASE), "departure_time", "iso_8601_datetime"),
    # Bounded integer: max_results between 1 and 5
    (re.compile(r"Parameter 'max_results' must be.*between 1 and 5", re.IGNORECASE), "max_results", "bounds_1_to_5"),
    # Non-empty string requirement
    (re.compile(r"Parameter '(?P<param>[^']+)' must be a non-empty string", re.IGNORECASE), "param", "non_empty_string"),
]

# Non-procedural transient patterns that must NEVER form procedural rules
TRANSIENT_PATTERNS: list[re.Pattern] = [
    re.compile(r"503", re.IGNORECASE),
    re.compile(r"service unavailable", re.IGNORECASE),
    re.compile(r"timed out", re.IGNORECASE),
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"429", re.IGNORECASE),
    re.compile(r"rate limit", re.IGNORECASE),
    re.compile(r"connection error", re.IGNORECASE),
]


class RecoveryDetector:
    """Tracks in-turn tool execution attempts and detects verified recoveries."""

    def __init__(self) -> None:
        self._failed_attempts: list[ToolAttempt] = []

    def clear(self) -> None:
        """Reset tracked attempts for a new turn."""
        self._failed_attempts.clear()

    @staticmethod
    def is_transient_failure(result: ToolResult) -> bool:
        """Check if failure is transient/infrastructure related."""
        if result.error_code == ErrorCode.TRANSIENT_ERROR:
            return True
        for pat in TRANSIENT_PATTERNS:
            if pat.search(result.content):
                return True
        return False

    @staticmethod
    def attribute_failure(result: ToolResult) -> tuple[str, str] | None:
        """Deterministically attribute failure to a parameter and constraint without guessing.

        Returns (failing_parameter, constraint) or None.
        """
        if RecoveryDetector.is_transient_failure(result):
            return None

        # Only procedural / client error codes are eligible
        if result.error_code not in (ErrorCode.INVALID_ARGUMENT, ErrorCode.BOUNDARY_VIOLATION):
            return None

        for pattern, param_target, constraint in SCHEMA_ATTRIBUTION_RULES:
            match = pattern.search(result.content)
            if match:
                if param_target and param_target in match.groupdict():
                    param_name = match.group(param_target)
                elif param_target:
                    param_name = param_target
                else:
                    param_name = "unknown"
                return param_name, constraint

        return None

    @staticmethod
    def synthesize_lesson(tool_name: str, failing_parameter: str, constraint: str) -> str:
        """Synthesize a concise, normalized procedural rule from verified attribution."""
        if constraint == "iso_8601_datetime":
            return f"{tool_name}.{failing_parameter} requires ISO-8601 datetime format (e.g. 'YYYY-MM-DDTHH:MM:SS')."
        elif constraint == "required":
            return f"{tool_name} requires parameter '{failing_parameter}'."
        elif constraint == "bounds_1_to_5":
            return f"{tool_name}.{failing_parameter} must be an integer between 1 and 5."
        elif constraint == "non_empty_string":
            return f"{tool_name}.{failing_parameter} must be a non-empty string."
        elif constraint == "type_mismatch":
            return f"{tool_name}.{failing_parameter} argument must match expected schema type."
        elif constraint == "unexpected_argument":
            return f"{tool_name} does not accept unexpected argument '{failing_parameter}'."
        return f"{tool_name}.{failing_parameter} must conform to schema contract ({constraint})."

    def record_attempt(self, attempt: ToolAttempt) -> None:
        """Record an attempt if it failed."""
        if attempt.result.is_error:
            self._failed_attempts.append(attempt)

    def detect_recovery(self, success_attempt: ToolAttempt) -> ProceduralLesson | None:
        """Evaluate whether a successful attempt constitutes a verified recovery from a prior failure.

        Requirements:
        1. Prior failure from the same tool.
        2. Procedural/non-transient failure.
        3. Deterministically attributed failing parameter.
        4. Success occurred strictly after the failure step.
        5. Targeted argument changed in the recovering invocation.
        """
        if success_attempt.result.is_error:
            return None

        for failed in reversed(self._failed_attempts):
            if failed.tool_name != success_attempt.tool_name:
                continue

            if failed.step >= success_attempt.step:
                continue

            attribution = self.attribute_failure(failed.result)
            if attribution is None:
                continue

            failing_param, constraint = attribution

            # Delta verification: the failing parameter must have changed
            if constraint == "required":
                # Parameter was absent in failed call, present in successful call
                if failing_param in failed.arguments:
                    continue
                if failing_param not in success_attempt.arguments:
                    continue
            else:
                # Value must differ between failed and successful invocation
                failed_val = failed.arguments.get(failing_param)
                succ_val = success_attempt.arguments.get(failing_param)
                if failed_val == succ_val:
                    # Coincidental success: the failing parameter was unchanged!
                    continue

            # Verified recovery: synthesize deterministic lesson
            lesson_text = self.synthesize_lesson(failed.tool_name, failing_param, constraint)
            provenance = {
                "tool_name": failed.tool_name,
                "failing_parameter": failing_param,
                "constraint": constraint,
                "error_code": failed.result.error_code.value if failed.result.error_code else "ERROR",
                "failed_step": failed.step,
                "successful_step": success_attempt.step,
                "recovery_kind": "parameter_correction",
            }

            logger.info(
                f"Procedural recovery verified [tool={failed.tool_name}, param={failing_param}]: {lesson_text}"
            )
            return ProceduralLesson(
                tool_name=failed.tool_name,
                failing_parameter=failing_param,
                constraint=constraint,
                lesson=lesson_text,
                provenance=provenance,
            )

        return None
