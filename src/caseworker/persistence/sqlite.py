"""SQLite persistence implementation for JackVerse Caseworker.

Features:
- Deterministic schema migrations via SQLiteMigrator with user_version tracking.
- Foreign keys enabled (`PRAGMA foreign_keys = ON`).
- WAL journal mode for filesystem databases.
- Full Unit-of-Work transaction boundary ensuring atomic state + event commits.
- Optimistic concurrency locking via aggregate/entity version checks.
- Partial indexes for idempotency and fingerprint constraints.
- Repositories for Missions, Cases, Opportunities, Actions, Approvals, ContextSources, ContextFacts, Claims, and DomainEvents.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from caseworker.domain.action import Action
from caseworker.domain.approval import Approval
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
    MissionKind,
    MissionStatus,
    OpportunityStatus,
    OpportunityType,
    RiskLevel,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
)
from caseworker.domain.errors import (
    EntityNotFoundError,
    OptimisticLockError,
    PersistenceError,
)
from caseworker.domain.events import DomainEvent
from caseworker.domain.mission import Mission
from caseworker.domain.namespaces import matches_namespace_filter
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.source import ContextSource
from caseworker.domain.types import (
    canonical_json_dumps,
    from_iso_utc,
    now_utc,
    to_iso_utc,
)
from caseworker.persistence.base import (
    ActionRepository,
    ApprovalRepository,
    CaseRepository,
    CaseworkerUnitOfWork,
    ClaimRepository,
    ContextRepository,
    ContextSourceRepository,
    EventStore,
    MissionRepository,
    OpportunityRepository,
)
from caseworker.persistence.migration import SQLiteMigrator


class SQLiteMissionRepository(MissionRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, mission: Mission) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM missions WHERE mission_id = ?", (mission.mission_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO missions (
                        mission_id, user_id, title, goal, kind, status,
                        success_criteria, constraints, created_at, updated_at,
                        deadline, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        mission.mission_id,
                        mission.user_id,
                        mission.title,
                        mission.goal,
                        mission.kind.value,
                        mission.status.value,
                        canonical_json_dumps(mission.success_criteria),
                        canonical_json_dumps(mission.constraints),
                        to_iso_utc(mission.created_at),
                        to_iso_utc(mission.updated_at),
                        to_iso_utc(mission.deadline),
                        mission.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(f"Failed to insert Mission '{mission.mission_id}': {e}") from e
        else:
            expected_version = mission.version - 1
            cur.execute(
                """
                UPDATE missions SET
                    user_id = ?, title = ?, goal = ?, kind = ?, status = ?,
                    success_criteria = ?, constraints = ?, updated_at = ?,
                    deadline = ?, version = ?
                WHERE mission_id = ? AND version = ?
                """,
                (
                    mission.user_id,
                    mission.title,
                    mission.goal,
                    mission.kind.value,
                    mission.status.value,
                    canonical_json_dumps(mission.success_criteria),
                    canonical_json_dumps(mission.constraints),
                    to_iso_utc(mission.updated_at),
                    to_iso_utc(mission.deadline),
                    mission.version,
                    mission.mission_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("Mission", mission.mission_id, expected_version)

    def get_by_id(self, mission_id: str) -> Mission | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT mission_id, user_id, title, goal, kind, status,
                   success_criteria, constraints, created_at, updated_at,
                   deadline, version
            FROM missions WHERE mission_id = ?
            """,
            (mission_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return Mission(
            mission_id=row[0],
            user_id=row[1],
            title=row[2],
            goal=row[3],
            kind=MissionKind(row[4]),
            status=MissionStatus(row[5]),
            success_criteria=json.loads(row[6]),
            constraints=json.loads(row[7]),
            created_at=from_iso_utc(row[8]),
            updated_at=from_iso_utc(row[9]),
            deadline=from_iso_utc(row[10]),
            version=row[11],
        )

    def list_by_user(self, user_id: str, status: MissionStatus | None = None) -> list[Mission]:
        cur = self.conn.cursor()
        if status is not None:
            cur.execute(
                """
                SELECT mission_id, user_id, title, goal, kind, status,
                       success_criteria, constraints, created_at, updated_at,
                       deadline, version
                FROM missions WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (user_id, status.value),
            )
        else:
            cur.execute(
                """
                SELECT mission_id, user_id, title, goal, kind, status,
                       success_criteria, constraints, created_at, updated_at,
                       deadline, version
                FROM missions WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
        rows = cur.fetchall()
        return [
            Mission(
                mission_id=r[0],
                user_id=r[1],
                title=r[2],
                goal=r[3],
                kind=MissionKind(r[4]),
                status=MissionStatus(r[5]),
                success_criteria=json.loads(r[6]),
                constraints=json.loads(r[7]),
                created_at=from_iso_utc(r[8]),
                updated_at=from_iso_utc(r[9]),
                deadline=from_iso_utc(r[10]),
                version=r[11],
            )
            for r in rows
        ]


class SQLiteCaseRepository(CaseRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, case: Case) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM cases WHERE case_id = ?", (case.case_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO cases (
                        case_id, mission_id, user_id, case_type, title, goal,
                        status, success_criteria, constraints, created_at,
                        updated_at, deadline, resolved_at, outcome, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case.case_id,
                        case.mission_id,
                        case.user_id,
                        case.case_type_str,
                        case.title,
                        case.goal,
                        case.status.value,
                        canonical_json_dumps(case.success_criteria),
                        canonical_json_dumps(case.constraints),
                        to_iso_utc(case.created_at),
                        to_iso_utc(case.updated_at),
                        to_iso_utc(case.deadline),
                        to_iso_utc(case.resolved_at),
                        case.outcome,
                        case.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(f"Failed to insert Case '{case.case_id}': {e}") from e
        else:
            expected_version = case.version - 1
            cur.execute(
                """
                UPDATE cases SET
                    mission_id = ?, user_id = ?, case_type = ?, title = ?, goal = ?,
                    status = ?, success_criteria = ?, constraints = ?, updated_at = ?,
                    deadline = ?, resolved_at = ?, outcome = ?, version = ?
                WHERE case_id = ? AND version = ?
                """,
                (
                    case.mission_id,
                    case.user_id,
                    case.case_type_str,
                    case.title,
                    case.goal,
                    case.status.value,
                    canonical_json_dumps(case.success_criteria),
                    canonical_json_dumps(case.constraints),
                    to_iso_utc(case.updated_at),
                    to_iso_utc(case.deadline),
                    to_iso_utc(case.resolved_at),
                    case.outcome,
                    case.version,
                    case.case_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("Case", case.case_id, expected_version)

    def _row_to_case(self, row: tuple[Any, ...]) -> Case:
        return Case(
            case_id=row[0],
            mission_id=row[1],
            user_id=row[2],
            case_type=row[3],
            title=row[4],
            goal=row[5],
            status=CaseStatus(row[6]),
            success_criteria=json.loads(row[7]),
            constraints=json.loads(row[8]),
            created_at=from_iso_utc(row[9]),
            updated_at=from_iso_utc(row[10]),
            deadline=from_iso_utc(row[11]),
            resolved_at=from_iso_utc(row[12]),
            outcome=row[13],
            version=row[14],
        )

    def get_by_id(self, case_id: str) -> Case | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT case_id, mission_id, user_id, case_type, title, goal,
                   status, success_criteria, constraints, created_at,
                   updated_at, deadline, resolved_at, outcome, version
            FROM cases WHERE case_id = ?
            """,
            (case_id,),
        )
        row = cur.fetchone()
        return self._row_to_case(row) if row else None

    def list_by_user(self, user_id: str, status: CaseStatus | None = None) -> list[Case]:
        cur = self.conn.cursor()
        if status is not None:
            cur.execute(
                """
                SELECT case_id, mission_id, user_id, case_type, title, goal,
                       status, success_criteria, constraints, created_at,
                       updated_at, deadline, resolved_at, outcome, version
                FROM cases WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (user_id, status.value),
            )
        else:
            cur.execute(
                """
                SELECT case_id, mission_id, user_id, case_type, title, goal,
                       status, success_criteria, constraints, created_at,
                       updated_at, deadline, resolved_at, outcome, version
                FROM cases WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
        return [self._row_to_case(r) for r in cur.fetchall()]

    def list_by_mission(self, mission_id: str) -> list[Case]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT case_id, mission_id, user_id, case_type, title, goal,
                   status, success_criteria, constraints, created_at,
                   updated_at, deadline, resolved_at, outcome, version
            FROM cases WHERE mission_id = ?
            ORDER BY created_at DESC
            """,
            (mission_id,),
        )
        return [self._row_to_case(r) for r in cur.fetchall()]


class SQLiteOpportunityRepository(OpportunityRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, opportunity: Opportunity) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM opportunities WHERE opportunity_id = ?", (opportunity.opportunity_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO opportunities (
                        opportunity_id, mission_id, user_id, opportunity_type,
                        title, organization, source_url, source_name, location,
                        status, requirements, metadata, fingerprint,
                        discovered_at, deadline, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        opportunity.opportunity_id,
                        opportunity.mission_id,
                        opportunity.user_id,
                        opportunity.opportunity_type_str,
                        opportunity.title,
                        opportunity.organization,
                        opportunity.source_url,
                        opportunity.source_name,
                        opportunity.location,
                        opportunity.status.value,
                        canonical_json_dumps(opportunity.requirements),
                        canonical_json_dumps(opportunity.metadata),
                        opportunity.fingerprint,
                        to_iso_utc(opportunity.discovered_at),
                        to_iso_utc(opportunity.deadline),
                        opportunity.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(
                    f"Integrity violation saving Opportunity '{opportunity.opportunity_id}': {e}"
                ) from e
        else:
            expected_version = opportunity.version - 1
            cur.execute(
                """
                UPDATE opportunities SET
                    mission_id = ?, user_id = ?, opportunity_type = ?, title = ?,
                    organization = ?, source_url = ?, source_name = ?, location = ?,
                    status = ?, requirements = ?, metadata = ?, fingerprint = ?,
                    deadline = ?, version = ?
                WHERE opportunity_id = ? AND version = ?
                """,
                (
                    opportunity.mission_id,
                    opportunity.user_id,
                    opportunity.opportunity_type_str,
                    opportunity.title,
                    opportunity.organization,
                    opportunity.source_url,
                    opportunity.source_name,
                    opportunity.location,
                    opportunity.status.value,
                    canonical_json_dumps(opportunity.requirements),
                    canonical_json_dumps(opportunity.metadata),
                    opportunity.fingerprint,
                    to_iso_utc(opportunity.deadline),
                    opportunity.version,
                    opportunity.opportunity_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("Opportunity", opportunity.opportunity_id, expected_version)

    def _row_to_opportunity(self, row: tuple[Any, ...]) -> Opportunity:
        return Opportunity(
            opportunity_id=row[0],
            mission_id=row[1],
            user_id=row[2],
            opportunity_type=row[3],
            title=row[4],
            organization=row[5],
            source_url=row[6],
            source_name=row[7],
            location=row[8],
            status=OpportunityStatus(row[9]),
            requirements=json.loads(row[10]),
            metadata=json.loads(row[11]),
            fingerprint=row[12],
            discovered_at=from_iso_utc(row[13]),
            deadline=from_iso_utc(row[14]),
            version=row[15],
        )

    def get_by_id(self, opportunity_id: str) -> Opportunity | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version
            FROM opportunities WHERE opportunity_id = ?
            """,
            (opportunity_id,),
        )
        row = cur.fetchone()
        return self._row_to_opportunity(row) if row else None

    def get_by_fingerprint(self, user_id: str, fingerprint: str) -> Opportunity | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version
            FROM opportunities WHERE user_id = ? AND fingerprint = ?
            """,
            (user_id, fingerprint),
        )
        row = cur.fetchone()
        return self._row_to_opportunity(row) if row else None

    def list_by_mission(self, mission_id: str) -> list[Opportunity]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version
            FROM opportunities WHERE mission_id = ?
            ORDER BY discovered_at DESC
            """,
            (mission_id,),
        )
        return [self._row_to_opportunity(r) for r in cur.fetchall()]

    def list_by_user(self, user_id: str, status: OpportunityStatus | None = None) -> list[Opportunity]:
        cur = self.conn.cursor()
        if status is not None:
            cur.execute(
                """
                SELECT opportunity_id, mission_id, user_id, opportunity_type,
                       title, organization, source_url, source_name, location,
                       status, requirements, metadata, fingerprint,
                       discovered_at, deadline, version
                FROM opportunities WHERE user_id = ? AND status = ?
                ORDER BY discovered_at DESC
                """,
                (user_id, status.value),
            )
        else:
            cur.execute(
                """
                SELECT opportunity_id, mission_id, user_id, opportunity_type,
                       title, organization, source_url, source_name, location,
                       status, requirements, metadata, fingerprint,
                       discovered_at, deadline, version
                FROM opportunities WHERE user_id = ?
                ORDER BY discovered_at DESC
                """,
                (user_id,),
            )
        return [self._row_to_opportunity(r) for r in cur.fetchall()]


class SQLiteActionRepository(ActionRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, action: Action) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM actions WHERE action_id = ?", (action.action_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO actions (
                        action_id, case_id, action_type, description, status,
                        risk_level, requires_approval, created_at, executed_at,
                        parameters, result, idempotency_key, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action.action_id,
                        action.case_id,
                        action.action_type_str,
                        action.description,
                        action.status.value,
                        action.risk_level.value,
                        1 if action.requires_approval else 0,
                        to_iso_utc(action.created_at),
                        to_iso_utc(action.executed_at),
                        canonical_json_dumps(action.parameters),
                        canonical_json_dumps(action.result),
                        action.idempotency_key,
                        action.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(
                    f"Integrity violation saving Action '{action.action_id}': {e}"
                ) from e
        else:
            expected_version = action.version - 1
            cur.execute(
                """
                UPDATE actions SET
                    case_id = ?, action_type = ?, description = ?, status = ?,
                    risk_level = ?, requires_approval = ?, executed_at = ?,
                    parameters = ?, result = ?, idempotency_key = ?, version = ?
                WHERE action_id = ? AND version = ?
                """,
                (
                    action.case_id,
                    action.action_type_str,
                    action.description,
                    action.status.value,
                    action.risk_level.value,
                    1 if action.requires_approval else 0,
                    to_iso_utc(action.executed_at),
                    canonical_json_dumps(action.parameters),
                    canonical_json_dumps(action.result),
                    action.idempotency_key,
                    action.version,
                    action.action_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("Action", action.action_id, expected_version)

    def _row_to_action(self, row: tuple[Any, ...]) -> Action:
        return Action(
            action_id=row[0],
            case_id=row[1],
            action_type=row[2],
            description=row[3],
            status=ActionStatus(row[4]),
            risk_level=RiskLevel(row[5]),
            requires_approval=bool(row[6]),
            created_at=from_iso_utc(row[7]),
            executed_at=from_iso_utc(row[8]),
            parameters=json.loads(row[9]),
            result=json.loads(row[10]),
            idempotency_key=row[11],
            version=row[12],
        )

    def get_by_id(self, action_id: str) -> Action | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT action_id, case_id, action_type, description, status,
                   risk_level, requires_approval, created_at, executed_at,
                   parameters, result, idempotency_key, version
            FROM actions WHERE action_id = ?
            """,
            (action_id,),
        )
        row = cur.fetchone()
        return self._row_to_action(row) if row else None

    def get_by_idempotency_key(self, case_id: str, idempotency_key: str) -> Action | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT action_id, case_id, action_type, description, status,
                   risk_level, requires_approval, created_at, executed_at,
                   parameters, result, idempotency_key, version
            FROM actions WHERE case_id = ? AND idempotency_key = ?
            """,
            (case_id, idempotency_key),
        )
        row = cur.fetchone()
        return self._row_to_action(row) if row else None

    def list_by_case(self, case_id: str) -> list[Action]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT action_id, case_id, action_type, description, status,
                   risk_level, requires_approval, created_at, executed_at,
                   parameters, result, idempotency_key, version
            FROM actions WHERE case_id = ?
            ORDER BY created_at ASC
            """,
            (case_id,),
        )
        return [self._row_to_action(r) for r in cur.fetchall()]


class SQLiteApprovalRepository(ApprovalRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, approval: Approval) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM approvals WHERE approval_id = ?", (approval.approval_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO approvals (
                        approval_id, action_id, case_id, user_id,
                        action_fingerprint, status, requested_at, decided_at,
                        expires_at, reason, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.approval_id,
                        approval.action_id,
                        approval.case_id,
                        approval.user_id,
                        approval.action_fingerprint,
                        approval.status.value,
                        to_iso_utc(approval.requested_at),
                        to_iso_utc(approval.decided_at),
                        to_iso_utc(approval.expires_at),
                        approval.reason,
                        approval.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(
                    f"Integrity violation saving Approval '{approval.approval_id}': {e}"
                ) from e
        else:
            expected_version = approval.version - 1
            cur.execute(
                """
                UPDATE approvals SET
                    action_id = ?, case_id = ?, user_id = ?, action_fingerprint = ?,
                    status = ?, requested_at = ?, decided_at = ?, expires_at = ?,
                    reason = ?, version = ?
                WHERE approval_id = ? AND version = ?
                """,
                (
                    approval.action_id,
                    approval.case_id,
                    approval.user_id,
                    approval.action_fingerprint,
                    approval.status.value,
                    to_iso_utc(approval.requested_at),
                    to_iso_utc(approval.decided_at),
                    to_iso_utc(approval.expires_at),
                    approval.reason,
                    approval.version,
                    approval.approval_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("Approval", approval.approval_id, expected_version)

    def _row_to_approval(self, row: tuple[Any, ...]) -> Approval:
        return Approval(
            approval_id=row[0],
            action_id=row[1],
            case_id=row[2],
            user_id=row[3],
            action_fingerprint=row[4],
            status=ApprovalStatus(row[5]),
            requested_at=from_iso_utc(row[6]),
            decided_at=from_iso_utc(row[7]),
            expires_at=from_iso_utc(row[8]),
            reason=row[9],
            version=row[10],
        )

    def get_by_id(self, approval_id: str) -> Approval | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT approval_id, action_id, case_id, user_id, action_fingerprint,
                   status, requested_at, decided_at, expires_at, reason, version
            FROM approvals WHERE approval_id = ?
            """,
            (approval_id,),
        )
        row = cur.fetchone()
        return self._row_to_approval(row) if row else None

    def get_by_action_id(self, action_id: str) -> Approval | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT approval_id, action_id, case_id, user_id, action_fingerprint,
                   status, requested_at, decided_at, expires_at, reason, version
            FROM approvals WHERE action_id = ?
            ORDER BY requested_at DESC LIMIT 1
            """,
            (action_id,),
        )
        row = cur.fetchone()
        return self._row_to_approval(row) if row else None

    def list_by_case(self, case_id: str) -> list[Approval]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT approval_id, action_id, case_id, user_id, action_fingerprint,
                   status, requested_at, decided_at, expires_at, reason, version
            FROM approvals WHERE case_id = ?
            ORDER BY requested_at DESC
            """,
            (case_id,),
        )
        return [self._row_to_approval(r) for r in cur.fetchall()]

    def list_pending_by_user(self, user_id: str) -> list[Approval]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT approval_id, action_id, case_id, user_id, action_fingerprint,
                   status, requested_at, decided_at, expires_at, reason, version
            FROM approvals WHERE user_id = ? AND status = ?
            ORDER BY requested_at DESC
            """,
            (user_id, ApprovalStatus.PENDING.value),
        )
        return [self._row_to_approval(r) for r in cur.fetchall()]


class SQLiteContextSourceRepository(ContextSourceRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, source: ContextSource) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM context_sources WHERE source_id = ?", (source.source_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO context_sources (
                        source_id, user_id, source_type, title, source_reference,
                        content_hash, sensitivity, created_at, updated_at,
                        metadata, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source.source_id,
                        source.user_id,
                        source.source_type.value,
                        source.title,
                        source.source_reference,
                        source.content_hash,
                        source.sensitivity.value,
                        to_iso_utc(source.created_at),
                        to_iso_utc(source.updated_at),
                        canonical_json_dumps(source.metadata),
                        source.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(f"Failed to insert ContextSource '{source.source_id}': {e}") from e
        else:
            expected_version = source.version - 1
            cur.execute(
                """
                UPDATE context_sources SET
                    user_id = ?, source_type = ?, title = ?, source_reference = ?,
                    content_hash = ?, sensitivity = ?, updated_at = ?,
                    metadata = ?, version = ?
                WHERE source_id = ? AND version = ?
                """,
                (
                    source.user_id,
                    source.source_type.value,
                    source.title,
                    source.source_reference,
                    source.content_hash,
                    source.sensitivity.value,
                    to_iso_utc(source.updated_at),
                    canonical_json_dumps(source.metadata),
                    source.version,
                    source.source_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("ContextSource", source.source_id, expected_version)

    def _row_to_source(self, row: tuple[Any, ...]) -> ContextSource:
        return ContextSource(
            source_id=row[0],
            user_id=row[1],
            source_type=SourceType(row[2]),
            title=row[3],
            source_reference=row[4],
            content_hash=row[5],
            sensitivity=SensitivityLevel(row[6]),
            created_at=from_iso_utc(row[7]),
            updated_at=from_iso_utc(row[8]),
            metadata=json.loads(row[9]),
            version=row[10],
        )

    def get_by_id(self, source_id: str) -> ContextSource | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT source_id, user_id, source_type, title, source_reference,
                   content_hash, sensitivity, created_at, updated_at,
                   metadata, version
            FROM context_sources WHERE source_id = ?
            """,
            (source_id,),
        )
        row = cur.fetchone()
        return self._row_to_source(row) if row else None

    def list_by_user(self, user_id: str) -> list[ContextSource]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT source_id, user_id, source_type, title, source_reference,
                   content_hash, sensitivity, created_at, updated_at,
                   metadata, version
            FROM context_sources WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return [self._row_to_source(r) for r in cur.fetchall()]


class SQLiteContextRepository(ContextRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, fact: ContextFact) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM context_facts WHERE fact_id = ?", (fact.fact_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO context_facts (
                        fact_id, user_id, namespace, key, value, source_type,
                        source_reference, source_id, confidence, verification_status,
                        rejection_reason, sensitivity, allowed_purposes, created_at,
                        updated_at, expires_at, superseded_by_fact_id,
                        superseded_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fact.fact_id,
                        fact.user_id,
                        fact.namespace,
                        fact.key,
                        canonical_json_dumps(fact.value),
                        fact.source_type.value,
                        fact.source_reference,
                        fact.source_id,
                        fact.confidence,
                        fact.verification_status.value,
                        fact.rejection_reason,
                        fact.sensitivity.value,
                        canonical_json_dumps(fact.allowed_purposes),
                        to_iso_utc(fact.created_at),
                        to_iso_utc(fact.updated_at),
                        to_iso_utc(fact.expires_at),
                        fact.superseded_by_fact_id,
                        to_iso_utc(fact.superseded_at),
                        fact.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(
                    f"Integrity violation saving ContextFact '{fact.fact_id}': {e}"
                ) from e
        else:
            expected_version = fact.version - 1
            cur.execute(
                """
                UPDATE context_facts SET
                    user_id = ?, namespace = ?, key = ?, value = ?, source_type = ?,
                    source_reference = ?, source_id = ?, confidence = ?,
                    verification_status = ?, rejection_reason = ?, sensitivity = ?,
                    allowed_purposes = ?, updated_at = ?, expires_at = ?,
                    superseded_by_fact_id = ?, superseded_at = ?, version = ?
                WHERE fact_id = ? AND version = ?
                """,
                (
                    fact.user_id,
                    fact.namespace,
                    fact.key,
                    canonical_json_dumps(fact.value),
                    fact.source_type.value,
                    fact.source_reference,
                    fact.source_id,
                    fact.confidence,
                    fact.verification_status.value,
                    fact.rejection_reason,
                    fact.sensitivity.value,
                    canonical_json_dumps(fact.allowed_purposes),
                    to_iso_utc(fact.updated_at),
                    to_iso_utc(fact.expires_at),
                    fact.superseded_by_fact_id,
                    to_iso_utc(fact.superseded_at),
                    fact.version,
                    fact.fact_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("ContextFact", fact.fact_id, expected_version)

    def _row_to_fact(self, row: tuple[Any, ...]) -> ContextFact:
        # Gracefully handle both 17-col (v1) and 19-col (v2) schema rows
        if len(row) >= 19:
            return ContextFact(
                fact_id=row[0],
                user_id=row[1],
                namespace=row[2],
                key=row[3],
                value=json.loads(row[4]),
                source_type=SourceType(row[5]),
                source_reference=row[6],
                source_id=row[7],
                confidence=float(row[8]),
                verification_status=VerificationStatus(row[9]),
                rejection_reason=row[10],
                sensitivity=SensitivityLevel(row[11]),
                allowed_purposes=json.loads(row[12]),
                created_at=from_iso_utc(row[13]),
                updated_at=from_iso_utc(row[14]),
                expires_at=from_iso_utc(row[15]),
                superseded_by_fact_id=row[16],
                superseded_at=from_iso_utc(row[17]),
                version=row[18],
            )
        # Fallback for old schema during migration/inspection
        return ContextFact(
            fact_id=row[0],
            user_id=row[1],
            namespace=row[2],
            key=row[3],
            value=json.loads(row[4]),
            source_type=SourceType(row[5]),
            source_reference=row[6],
            confidence=float(row[7]),
            verification_status=VerificationStatus(row[8]),
            sensitivity=SensitivityLevel(row[9]),
            allowed_purposes=json.loads(row[10]),
            created_at=from_iso_utc(row[11]),
            updated_at=from_iso_utc(row[12]),
            expires_at=from_iso_utc(row[13]),
            superseded_by_fact_id=row[14],
            superseded_at=from_iso_utc(row[15]),
            version=row[16],
        )

    def get_by_id(self, fact_id: str) -> ContextFact | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT fact_id, user_id, namespace, key, value, source_type,
                   source_reference, source_id, confidence, verification_status,
                   rejection_reason, sensitivity, allowed_purposes, created_at,
                   updated_at, expires_at, superseded_by_fact_id, superseded_at,
                   version
            FROM context_facts WHERE fact_id = ?
            """,
            (fact_id,),
        )
        row = cur.fetchone()
        return self._row_to_fact(row) if row else None

    def list_active(self, user_id: str, namespace: str | None = None) -> list[ContextFact]:
        cur = self.conn.cursor()
        now_iso = to_iso_utc(now_utc())
        if namespace is not None:
            cur.execute(
                """
                SELECT fact_id, user_id, namespace, key, value, source_type,
                       source_reference, source_id, confidence, verification_status,
                       rejection_reason, sensitivity, allowed_purposes, created_at,
                       updated_at, expires_at, superseded_by_fact_id, superseded_at,
                       version
                FROM context_facts
                WHERE user_id = ?
                  AND namespace = ?
                  AND superseded_by_fact_id IS NULL
                  AND verification_status != 'rejected'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY updated_at DESC
                """,
                (user_id, namespace, now_iso),
            )
        else:
            cur.execute(
                """
                SELECT fact_id, user_id, namespace, key, value, source_type,
                       source_reference, source_id, confidence, verification_status,
                       rejection_reason, sensitivity, allowed_purposes, created_at,
                       updated_at, expires_at, superseded_by_fact_id, superseded_at,
                       version
                FROM context_facts
                WHERE user_id = ?
                  AND superseded_by_fact_id IS NULL
                  AND verification_status != 'rejected'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY updated_at DESC
                """,
                (user_id, now_iso),
            )
        return [self._row_to_fact(r) for r in cur.fetchall()]

    def list_history(self, user_id: str, namespace: str, key: str) -> list[ContextFact]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT fact_id, user_id, namespace, key, value, source_type,
                   source_reference, source_id, confidence, verification_status,
                   rejection_reason, sensitivity, allowed_purposes, created_at,
                   updated_at, expires_at, superseded_by_fact_id, superseded_at,
                   version
            FROM context_facts
            WHERE user_id = ? AND namespace = ? AND key = ?
            ORDER BY version ASC
            """,
            (user_id, namespace, key),
        )
        return [self._row_to_fact(r) for r in cur.fetchall()]

    def list_by_namespace(self, user_id: str, namespace_prefix: str) -> list[ContextFact]:
        """List active facts matching a hierarchical namespace prefix (e.g. 'career.*')."""
        all_active = self.list_active(user_id)
        return [f for f in all_active if matches_namespace_filter(f.namespace, namespace_prefix)]

    def get_by_source_id(self, source_id: str) -> list[ContextFact]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT fact_id, user_id, namespace, key, value, source_type,
                   source_reference, source_id, confidence, verification_status,
                   rejection_reason, sensitivity, allowed_purposes, created_at,
                   updated_at, expires_at, superseded_by_fact_id, superseded_at,
                   version
            FROM context_facts
            WHERE source_id = ?
            ORDER BY created_at ASC
            """,
            (source_id,),
        )
        return [self._row_to_fact(r) for r in cur.fetchall()]


class SQLiteClaimRepository(ClaimRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, claim: Claim) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM claims WHERE claim_id = ?", (claim.claim_id,))
        row = cur.fetchone()
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO claims (
                        claim_id, user_id, case_id, mission_id, purpose, text,
                        status, supporting_fact_ids, created_at, updated_at,
                        verified_at, rejection_reason, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        claim.claim_id,
                        claim.user_id,
                        claim.case_id,
                        claim.mission_id,
                        claim.purpose,
                        claim.text,
                        claim.status.value,
                        canonical_json_dumps(claim.supporting_fact_ids),
                        to_iso_utc(claim.created_at),
                        to_iso_utc(claim.updated_at),
                        to_iso_utc(claim.verified_at),
                        claim.rejection_reason,
                        claim.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(f"Failed to insert Claim '{claim.claim_id}': {e}") from e
        else:
            expected_version = claim.version - 1
            cur.execute(
                """
                UPDATE claims SET
                    user_id = ?, case_id = ?, mission_id = ?, purpose = ?,
                    text = ?, status = ?, supporting_fact_ids = ?,
                    updated_at = ?, verified_at = ?, rejection_reason = ?,
                    version = ?
                WHERE claim_id = ? AND version = ?
                """,
                (
                    claim.user_id,
                    claim.case_id,
                    claim.mission_id,
                    claim.purpose,
                    claim.text,
                    claim.status.value,
                    canonical_json_dumps(claim.supporting_fact_ids),
                    to_iso_utc(claim.updated_at),
                    to_iso_utc(claim.verified_at),
                    claim.rejection_reason,
                    claim.version,
                    claim.claim_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("Claim", claim.claim_id, expected_version)

    def _row_to_claim(self, row: tuple[Any, ...]) -> Claim:
        return Claim(
            claim_id=row[0],
            user_id=row[1],
            case_id=row[2],
            mission_id=row[3],
            purpose=row[4],
            text=row[5],
            status=ClaimStatus(row[6]),
            supporting_fact_ids=json.loads(row[7]),
            created_at=from_iso_utc(row[8]),
            updated_at=from_iso_utc(row[9]),
            verified_at=from_iso_utc(row[10]),
            rejection_reason=row[11],
            version=row[12],
        )

    def get_by_id(self, claim_id: str) -> Claim | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                   status, supporting_fact_ids, created_at, updated_at,
                   verified_at, rejection_reason, version
            FROM claims WHERE claim_id = ?
            """,
            (claim_id,),
        )
        row = cur.fetchone()
        return self._row_to_claim(row) if row else None

    def list_by_user(self, user_id: str, status: ClaimStatus | None = None) -> list[Claim]:
        cur = self.conn.cursor()
        if status is not None:
            cur.execute(
                """
                SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                       status, supporting_fact_ids, created_at, updated_at,
                       verified_at, rejection_reason, version
                FROM claims WHERE user_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (user_id, status.value),
            )
        else:
            cur.execute(
                """
                SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                       status, supporting_fact_ids, created_at, updated_at,
                       verified_at, rejection_reason, version
                FROM claims WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
        return [self._row_to_claim(r) for r in cur.fetchall()]

    def list_by_case(self, case_id: str) -> list[Claim]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                   status, supporting_fact_ids, created_at, updated_at,
                   verified_at, rejection_reason, version
            FROM claims WHERE case_id = ?
            ORDER BY created_at DESC
            """,
            (case_id,),
        )
        return [self._row_to_claim(r) for r in cur.fetchall()]

    def list_by_purpose(self, user_id: str, purpose: str) -> list[Claim]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                   status, supporting_fact_ids, created_at, updated_at,
                   verified_at, rejection_reason, version
            FROM claims WHERE user_id = ? AND purpose = ?
            ORDER BY created_at DESC
            """,
            (user_id, purpose),
        )
        return [self._row_to_claim(r) for r in cur.fetchall()]


class SQLiteEventStore(EventStore):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def append(self, event: DomainEvent) -> None:
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO domain_events (
                    event_id, event_type, aggregate_type, aggregate_id,
                    aggregate_version, user_id, occurred_at, payload, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.aggregate_type,
                    event.aggregate_id,
                    event.aggregate_version,
                    event.user_id,
                    to_iso_utc(event.occurred_at),
                    canonical_json_dumps(event.payload),
                    event.schema_version,
                ),
            )
        except sqlite3.IntegrityError as e:
            if "idx_events_aggregate_version" in str(e) or "UNIQUE" in str(e):
                raise OptimisticLockError(
                    event.aggregate_type,
                    event.aggregate_id,
                    event.aggregate_version,
                ) from e
            raise PersistenceError(f"Failed to append domain event '{event.event_id}': {e}") from e

    def append_many(self, events: Sequence[DomainEvent]) -> None:
        for event in events:
            self.append(event)

    def _row_to_event(self, row: tuple[Any, ...]) -> DomainEvent:
        return DomainEvent(
            event_id=row[0],
            event_type=row[1],
            aggregate_type=row[2],
            aggregate_id=row[3],
            aggregate_version=row[4],
            user_id=row[5],
            occurred_at=from_iso_utc(row[6]),
            payload=json.loads(row[7]),
            schema_version=row[8],
        )

    def get_events_for_aggregate(self, aggregate_type: str, aggregate_id: str) -> list[DomainEvent]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT event_id, event_type, aggregate_type, aggregate_id,
                   aggregate_version, user_id, occurred_at, payload, schema_version
            FROM domain_events
            WHERE aggregate_type = ? AND aggregate_id = ?
            ORDER BY aggregate_version ASC
            """,
            (aggregate_type, aggregate_id),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    def get_events_by_user(self, user_id: str, limit: int = 100) -> list[DomainEvent]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT event_id, event_type, aggregate_type, aggregate_id,
                   aggregate_version, user_id, occurred_at, payload, schema_version
            FROM domain_events
            WHERE user_id = ?
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]


class SQLiteCaseworkerUnitOfWork(CaseworkerUnitOfWork):
    """Transaction / Unit-of-Work boundary using SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.missions = SQLiteMissionRepository(conn)
        self.cases = SQLiteCaseRepository(conn)
        self.opportunities = SQLiteOpportunityRepository(conn)
        self.actions = SQLiteActionRepository(conn)
        self.approvals = SQLiteApprovalRepository(conn)
        self.sources = SQLiteContextSourceRepository(conn)
        self.context = SQLiteContextRepository(conn)
        self.claims = SQLiteClaimRepository(conn)
        self.events = SQLiteEventStore(conn)
        self._in_transaction = False

    def __enter__(self) -> SQLiteCaseworkerUnitOfWork:
        if not self._in_transaction:
            self.conn.execute("BEGIN IMMEDIATE")
            self._in_transaction = True
        return self

    def commit(self) -> None:
        if self._in_transaction:
            self.conn.execute("COMMIT")
            self._in_transaction = False

    def rollback(self) -> None:
        if self._in_transaction:
            self.conn.execute("ROLLBACK")
            self._in_transaction = False

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()


class SQLiteCaseworkerStorage:
    """Entry point for Caseworker SQLite database management and transactions."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._is_memory = db_path == ":memory:"

        if not self._is_memory:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._shared_conn: sqlite3.Connection | None = None
        if self._is_memory:
            self._shared_conn = sqlite3.connect(
                ":memory:",
                check_same_thread=False,
                isolation_level=None,
            )
            self._init_db(self._shared_conn)
        else:
            init_conn = self._get_connection()
            try:
                self._init_db(init_conn)
            finally:
                init_conn.close()

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA foreign_keys = ON;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        # Apply versioned migrations to ensure current schema
        SQLiteMigrator.migrate(conn)

    def _get_connection(self) -> sqlite3.Connection:
        if self._is_memory and self._shared_conn is not None:
            return self._shared_conn
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.execute("PRAGMA foreign_keys = ON;")
        if not self._is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def unit_of_work(self) -> SQLiteCaseworkerUnitOfWork:
        """Create a new transactional UnitOfWork."""
        conn = self._get_connection()
        return SQLiteCaseworkerUnitOfWork(conn)

    def close(self) -> None:
        """Close shared resources."""
        if self._shared_conn:
            self._shared_conn.close()
            self._shared_conn = None
