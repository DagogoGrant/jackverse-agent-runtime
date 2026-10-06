"""Domain exception hierarchy for JackVerse Caseworker."""

from __future__ import annotations


class CaseworkerError(Exception):
    """Base exception for all Caseworker domain errors."""


class EntityNotFoundError(CaseworkerError):
    """Raised when an aggregate root or entity cannot be found by its identifier."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        super().__init__(f"{entity_type} with ID '{entity_id}' was not found.")
        self.entity_type = entity_type
        self.entity_id = entity_id


class InvalidStateTransitionError(CaseworkerError):
    """Raised when an aggregate is requested to transition through an illegal state transition."""

    def __init__(self, entity_type: str, current_state: str, attempted_state: str, reason: str | None = None) -> None:
        msg = f"Cannot transition {entity_type} from '{current_state}' to '{attempted_state}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)
        self.entity_type = entity_type
        self.current_state = current_state
        self.attempted_state = attempted_state
        self.reason = reason


class OptimisticLockError(CaseworkerError):
    """Raised when a concurrent update modifies an aggregate while another transaction was processing it."""

    def __init__(self, entity_type: str, entity_id: str, expected_version: int) -> None:
        super().__init__(
            f"Optimistic lock conflict on {entity_type} '{entity_id}' (expected version {expected_version})."
        )
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.expected_version = expected_version


class DomainValidationError(CaseworkerError):
    """Raised when an entity invariant or parameter validation fails."""


class ApprovalValidationError(CaseworkerError):
    """Raised when an action approval cannot be granted because parameter fingerprint mismatch or status conflict."""


class MissionArchivalConflictError(CaseworkerError):
    """Raised when an archival or transition operation conflicts with the Mission's lifecycle or archival state."""

    def __init__(self, message: str, mission_id: str | None = None) -> None:
        super().__init__(message)
        self.mission_id = mission_id


class PersistenceError(CaseworkerError):
    """Raised when a storage, constraint, or database transaction operation fails."""
