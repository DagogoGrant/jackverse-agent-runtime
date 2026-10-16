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
from caseworker.domain.artifact import ArtifactBlock, PreparedArtifact
from caseworker.domain.case import Case
from caseworker.domain.claim import Claim
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalStatus,
    ArtifactStatus,
    CaseStatus,
    CaseType,
    ClaimStatus,
    ExternalExecutionPlanStatus,
    MissionKind,
    MissionStatus,
    OpportunityStatus,
    OpportunityType,
    PackageVerificationStatus,
    PreparationMaterialType,
    PreparationPackageStatus,
    RiskLevel,
    SensitivityLevel,
    SourceType,
    VerificationStatus,
)
from caseworker.domain.errors import (
    DomainValidationError,
    EntityNotFoundError,
    OptimisticLockError,
    PersistenceError,
)
from caseworker.domain.events import DomainEvent
from caseworker.domain.execution_plan import (
    ExecutionDestination,
    ExecutionFieldMapping,
    ExecutionPlanFinding,
    ExecutionScreeningResponse,
    ExecutionUploadAsset,
    ExternalExecutionPlan,
    TargetInteractionSchema,
    TargetSubmitControlSpec,
)
from caseworker.domain.mission import Mission
from caseworker.domain.namespaces import matches_namespace_filter
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    PreparationPackage,
    SafeClaimSnapshot,
)
from caseworker.domain.preparation import (
    EvidenceMappingItem,
    GapItem,
    PreparationIntelligenceReport,
    PreparationStrategy,
)
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
    ExecutionUploadAssetRepository,
    ExternalExecutionPlanRepository,
    MissionRepository,
    OpportunityRepository,
    PreparationArtifactRepository,
    PreparationPackageRepository,
    PreparationReportRepository,
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
                        deadline, version, archived, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        1 if mission.archived else 0,
                        to_iso_utc(mission.archived_at),
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
                    deadline = ?, version = ?, archived = ?, archived_at = ?
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
                    1 if mission.archived else 0,
                    to_iso_utc(mission.archived_at),
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
                   deadline, version, archived, archived_at
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
            archived=bool(row[12]),
            archived_at=from_iso_utc(row[13]) if row[13] else None,
        )

    def list_by_user(
        self,
        user_id: str,
        status: MissionStatus | None = None,
        archived: bool | None = False,
    ) -> list[Mission]:
        cur = self.conn.cursor()
        query = """
            SELECT mission_id, user_id, title, goal, kind, status,
                   success_criteria, constraints, created_at, updated_at,
                   deadline, version, archived, archived_at
            FROM missions WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status.value)
        if archived is not None:
            query += " AND archived = ?"
            params.append(1 if archived else 0)
        query += " ORDER BY created_at DESC"
        cur.execute(query, tuple(params))
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
                archived=bool(r[12]),
                archived_at=from_iso_utc(r[13]) if r[13] else None,
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
                        discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                        external_posting_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        opportunity.converted_case_id,
                        opportunity.fingerprint_v2,
                        opportunity.external_posting_id,
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
                    deadline = ?, version = ?, converted_case_id = ?, fingerprint_v2 = ?,
                    external_posting_id = ?
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
                    opportunity.converted_case_id,
                    opportunity.fingerprint_v2,
                    opportunity.external_posting_id,
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
            converted_case_id=row[16] if len(row) > 16 else None,
            fingerprint_v2=row[17] if len(row) > 17 else "",
            external_posting_id=row[18] if len(row) > 18 else None,
        )

    def get_by_id(self, opportunity_id: str) -> Opportunity | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                   external_posting_id
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
               discovered_at, deadline, version, converted_case_id, fingerprint_v2,
               external_posting_id
            FROM opportunities WHERE user_id = ? AND fingerprint = ?
            """,
            (user_id, fingerprint),
        )
        row = cur.fetchone()
        return self._row_to_opportunity(row) if row else None

    def get_by_fingerprint_v2(self, user_id: str, fingerprint_v2: str) -> Opportunity | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                   external_posting_id
            FROM opportunities WHERE user_id = ? AND fingerprint_v2 = ?
            ORDER BY discovered_at DESC
            LIMIT 1
            """,
            (user_id, fingerprint_v2),
        )
        row = cur.fetchone()
        return self._row_to_opportunity(row) if row else None

    def list_by_fingerprint_v2(self, user_id: str, fingerprint_v2: str) -> list[Opportunity]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                   external_posting_id
            FROM opportunities WHERE user_id = ? AND fingerprint_v2 = ?
            ORDER BY discovered_at DESC
            """,
            (user_id, fingerprint_v2),
        )
        return [self._row_to_opportunity(r) for r in cur.fetchall()]

    def list_by_external_posting_id(self, user_id: str, external_posting_id: str) -> list[Opportunity]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                   external_posting_id
            FROM opportunities WHERE user_id = ? AND external_posting_id = ?
            ORDER BY discovered_at DESC
            """,
            (user_id, external_posting_id.strip()),
        )
        return [self._row_to_opportunity(r) for r in cur.fetchall()]

    def list_by_mission(self, mission_id: str) -> list[Opportunity]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                   external_posting_id
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
                       discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                       external_posting_id
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
                       discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                       external_posting_id
                FROM opportunities WHERE user_id = ?
                ORDER BY discovered_at DESC
                """,
                (user_id,),
            )
        return [self._row_to_opportunity(r) for r in cur.fetchall()]

    def get_by_converted_case_id(self, case_id: str) -> Opportunity | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT opportunity_id, mission_id, user_id, opportunity_type,
                   title, organization, source_url, source_name, location,
                   status, requirements, metadata, fingerprint,
                   discovered_at, deadline, version, converted_case_id, fingerprint_v2,
                   external_posting_id
            FROM opportunities WHERE converted_case_id = ?
            """,
            (case_id,),
        )
        row = cur.fetchone()
        return self._row_to_opportunity(row) if row else None


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

    def list_by_user(self, user_id: str, status: ApprovalStatus | None = None) -> list[Approval]:
        cur = self.conn.cursor()
        query = """
            SELECT approval_id, action_id, case_id, user_id, action_fingerprint,
                   status, requested_at, decided_at, expires_at, reason, version
            FROM approvals WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status.value if isinstance(status, ApprovalStatus) else status)
        query += " ORDER BY requested_at DESC"
        cur.execute(query, tuple(params))
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
                        verified_at, rejection_reason, version,
                        source_report_id, source_claim_intent_id, assertion_schema,
                        assertion_json, support_snapshot_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        claim.source_report_id,
                        claim.source_claim_intent_id,
                        claim.assertion_schema,
                        claim.assertion_json,
                        claim.support_snapshot_hash,
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
                    source_report_id = ?, source_claim_intent_id = ?,
                    assertion_schema = ?, assertion_json = ?, support_snapshot_hash = ?,
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
                    claim.source_report_id,
                    claim.source_claim_intent_id,
                    claim.assertion_schema,
                    claim.assertion_json,
                    claim.support_snapshot_hash,
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
            created_at=from_iso_utc(row[8]) or now_utc(),
            updated_at=from_iso_utc(row[9]) or now_utc(),
            verified_at=from_iso_utc(row[10]),
            rejection_reason=row[11],
            version=row[12],
            source_report_id=row[13] if len(row) > 13 else None,
            source_claim_intent_id=row[14] if len(row) > 14 else None,
            assertion_schema=row[15] if len(row) > 15 else None,
            assertion_json=row[16] if len(row) > 16 else None,
            support_snapshot_hash=row[17] if len(row) > 17 else None,
        )

    def get_by_id(self, claim_id: str) -> Claim | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                   status, supporting_fact_ids, created_at, updated_at,
                   verified_at, rejection_reason, version,
                   source_report_id, source_claim_intent_id, assertion_schema,
                   assertion_json, support_snapshot_hash
            FROM claims WHERE claim_id = ?
            """,
            (claim_id,),
        )
        row = cur.fetchone()
        return self._row_to_claim(row) if row else None

    def list_by_user(
        self,
        user_id: str,
        status: ClaimStatus | None = None,
        case_id: str | None = None,
        mission_id: str | None = None,
    ) -> list[Claim]:
        cur = self.conn.cursor()
        query = """
            SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                   status, supporting_fact_ids, created_at, updated_at,
                   verified_at, rejection_reason, version,
                   source_report_id, source_claim_intent_id, assertion_schema,
                   assertion_json, support_snapshot_hash
            FROM claims WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if status is not None:
            query += " AND status = ?"
            params.append(status.value if hasattr(status, "value") else str(status))
        if case_id is not None:
            query += " AND case_id = ?"
            params.append(case_id)
        if mission_id is not None:
            query += " AND mission_id = ?"
            params.append(mission_id)

        query += " ORDER BY created_at DESC"
        cur.execute(query, tuple(params))
        return [self._row_to_claim(r) for r in cur.fetchall()]

    def list_by_case(self, case_id: str) -> list[Claim]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT claim_id, user_id, case_id, mission_id, purpose, text,
                   status, supporting_fact_ids, created_at, updated_at,
                   verified_at, rejection_reason, version,
                   source_report_id, source_claim_intent_id, assertion_schema,
                   assertion_json, support_snapshot_hash
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
                   verified_at, rejection_reason, version,
                   source_report_id, source_claim_intent_id, assertion_schema,
                   assertion_json, support_snapshot_hash
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

    def get_next_aggregate_version(self, aggregate_type: str, aggregate_id: str) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT COALESCE(MAX(aggregate_version), 0) + 1
            FROM domain_events
            WHERE aggregate_type = ? AND aggregate_id = ?
            """,
            (aggregate_type, aggregate_id),
        )
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 1

    def list_user_events(
        self,
        user_id: str,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        after_position: int | None = None,
        limit: int = 50,
    ) -> list[tuple[int, DomainEvent]]:
        cur = self.conn.cursor()
        query = """
            SELECT rowid, event_id, event_type, aggregate_type, aggregate_id,
                   aggregate_version, user_id, occurred_at, payload, schema_version
            FROM domain_events
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if aggregate_type is not None:
            query += " AND aggregate_type = ?"
            params.append(aggregate_type)
        if aggregate_id is not None:
            query += " AND aggregate_id = ?"
            params.append(aggregate_id)
        if after_position is not None:
            query += " AND rowid > ?"
            params.append(after_position)
        query += " ORDER BY rowid ASC LIMIT ?"
        params.append(limit)
        cur.execute(query, tuple(params))
        results = []
        for r in cur.fetchall():
            pos = int(r[0])
            event = self._row_to_event(r[1:])
            results.append((pos, event))
        return results


class SQLitePreparationReportRepository(PreparationReportRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, report: PreparationIntelligenceReport) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT 1 FROM preparation_intelligence_reports WHERE report_id = ?",
            (report.report_id,),
        )
        row = cur.fetchone()
        if row is not None:
            raise PersistenceError(
                f"PreparationIntelligenceReport '{report.report_id}' is immutable and cannot be updated."
            )

        try:
            cur.execute(
                """
                INSERT INTO preparation_intelligence_reports (
                    report_id, case_id, opportunity_id, user_id,
                    schema_version, analyzer_version,
                    evidence_map_json, gaps_json, strategy_json,
                    input_hash, context_snapshot_hash,
                    source_evaluation_id, created_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.case_id,
                    report.opportunity_id,
                    report.user_id,
                    report.schema_version,
                    report.analyzer_version,
                    canonical_json_dumps([e.to_dict() for e in report.evidence_map]),
                    canonical_json_dumps([g.to_dict() for g in report.gaps]),
                    canonical_json_dumps(report.strategy.to_dict()),
                    report.input_hash,
                    report.context_snapshot_hash,
                    report.source_evaluation_id,
                    to_iso_utc(report.created_at),
                    report.version,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise PersistenceError(
                f"Integrity violation saving PreparationIntelligenceReport '{report.report_id}': {e}"
            ) from e

    def _row_to_report(self, row: tuple[Any, ...]) -> PreparationIntelligenceReport:
        evidence_raw = json.loads(row[6]) if row[6] else []
        gaps_raw = json.loads(row[7]) if row[7] else []
        strategy_raw = json.loads(row[8]) if row[8] else {}

        return PreparationIntelligenceReport(
            report_id=row[0],
            case_id=row[1],
            opportunity_id=row[2],
            user_id=row[3],
            schema_version=row[4],
            analyzer_version=row[5],
            evidence_map=[EvidenceMappingItem.from_dict(e) for e in evidence_raw],
            gaps=[GapItem.from_dict(g) for g in gaps_raw],
            strategy=PreparationStrategy.from_dict(strategy_raw),
            input_hash=row[9],
            context_snapshot_hash=row[10],
            source_evaluation_id=row[11],
            created_at=from_iso_utc(row[12]) or now_utc(),
            version=row[13],
        )

    def get_by_id(self, report_id: str) -> PreparationIntelligenceReport | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT report_id, case_id, opportunity_id, user_id,
                   schema_version, analyzer_version,
                   evidence_map_json, gaps_json, strategy_json,
                   input_hash, context_snapshot_hash,
                   source_evaluation_id, created_at, version
            FROM preparation_intelligence_reports WHERE report_id = ?
            """,
            (report_id,),
        )
        row = cur.fetchone()
        return self._row_to_report(row) if row else None

    def get_latest_for_case(self, case_id: str) -> PreparationIntelligenceReport | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT report_id, case_id, opportunity_id, user_id,
                   schema_version, analyzer_version,
                   evidence_map_json, gaps_json, strategy_json,
                   input_hash, context_snapshot_hash,
                   source_evaluation_id, created_at, version
            FROM preparation_intelligence_reports
            WHERE case_id = ?
            ORDER BY created_at DESC, version DESC
            LIMIT 1
            """,
            (case_id,),
        )
        row = cur.fetchone()
        return self._row_to_report(row) if row else None

    def list_for_case(self, case_id: str) -> list[PreparationIntelligenceReport]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT report_id, case_id, opportunity_id, user_id,
                   schema_version, analyzer_version,
                   evidence_map_json, gaps_json, strategy_json,
                   input_hash, context_snapshot_hash,
                   source_evaluation_id, created_at, version
            FROM preparation_intelligence_reports
            WHERE case_id = ?
            ORDER BY created_at ASC, version ASC
            """,
            (case_id,),
        )
        return [self._row_to_report(r) for r in cur.fetchall()]


class SQLitePreparationArtifactRepository(PreparationArtifactRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, artifact: PreparedArtifact) -> None:
        cur = self.conn.cursor()
        cur.execute("SELECT version FROM preparation_artifacts WHERE artifact_id = ?", (artifact.artifact_id,))
        row = cur.fetchone()
        blocks_json = json.dumps([b.to_dict() for b in artifact.blocks])
        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO preparation_artifacts (
                        artifact_id, artifact_set_id, case_id, opportunity_id,
                        user_id, material_type, schema_version, planner_version,
                        status, title, blocks_json, source_report_id,
                        input_hash, grounding_snapshot_hash, created_at,
                        updated_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.artifact_id,
                        artifact.artifact_set_id,
                        artifact.case_id,
                        artifact.opportunity_id,
                        artifact.user_id,
                        artifact.material_type.value,
                        artifact.schema_version,
                        artifact.planner_version,
                        artifact.status.value,
                        artifact.title,
                        blocks_json,
                        artifact.source_report_id,
                        artifact.input_hash,
                        artifact.grounding_snapshot_hash,
                        to_iso_utc(artifact.created_at),
                        to_iso_utc(artifact.updated_at),
                        artifact.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(f"Failed to insert PreparedArtifact '{artifact.artifact_id}': {e}") from e
        else:
            expected_version = artifact.version - 1
            cur.execute(
                """
                UPDATE preparation_artifacts SET
                    artifact_set_id = ?, case_id = ?, opportunity_id = ?,
                    user_id = ?, material_type = ?, schema_version = ?,
                    planner_version = ?, status = ?, title = ?,
                    blocks_json = ?, source_report_id = ?, input_hash = ?,
                    grounding_snapshot_hash = ?, updated_at = ?, version = ?
                WHERE artifact_id = ? AND version = ?
                """,
                (
                    artifact.artifact_set_id,
                    artifact.case_id,
                    artifact.opportunity_id,
                    artifact.user_id,
                    artifact.material_type.value,
                    artifact.schema_version,
                    artifact.planner_version,
                    artifact.status.value,
                    artifact.title,
                    blocks_json,
                    artifact.source_report_id,
                    artifact.input_hash,
                    artifact.grounding_snapshot_hash,
                    to_iso_utc(artifact.updated_at),
                    artifact.version,
                    artifact.artifact_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("PreparedArtifact", artifact.artifact_id, expected_version)

    def _row_to_artifact(self, row: tuple[Any, ...]) -> PreparedArtifact:
        raw_blocks = json.loads(row[10])
        blocks = [ArtifactBlock.from_dict(b) for b in raw_blocks]
        return PreparedArtifact(
            artifact_id=row[0],
            artifact_set_id=row[1],
            case_id=row[2],
            opportunity_id=row[3],
            user_id=row[4],
            material_type=PreparationMaterialType(row[5]),
            schema_version=row[6],
            planner_version=row[7],
            status=ArtifactStatus(row[8]),
            title=row[9],
            blocks=blocks,
            source_report_id=row[11],
            input_hash=row[12],
            grounding_snapshot_hash=row[13],
            created_at=from_iso_utc(row[14]) or now_utc(),
            updated_at=from_iso_utc(row[15]) or now_utc(),
            version=row[16],
        )

    def get_by_id(self, artifact_id: str) -> PreparedArtifact | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT artifact_id, artifact_set_id, case_id, opportunity_id,
                   user_id, material_type, schema_version, planner_version,
                   status, title, blocks_json, source_report_id,
                   input_hash, grounding_snapshot_hash, created_at,
                   updated_at, version
            FROM preparation_artifacts WHERE artifact_id = ?
            """,
            (artifact_id,),
        )
        row = cur.fetchone()
        return self._row_to_artifact(row) if row else None

    def list_by_case(self, case_id: str) -> list[PreparedArtifact]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT artifact_id, artifact_set_id, case_id, opportunity_id,
                   user_id, material_type, schema_version, planner_version,
                   status, title, blocks_json, source_report_id,
                   input_hash, grounding_snapshot_hash, created_at,
                   updated_at, version
            FROM preparation_artifacts WHERE case_id = ?
            ORDER BY created_at ASC, material_type ASC
            """,
            (case_id,),
        )
        return [self._row_to_artifact(r) for r in cur.fetchall()]

    def list_by_set(self, set_id: str) -> list[PreparedArtifact]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT artifact_id, artifact_set_id, case_id, opportunity_id,
                   user_id, material_type, schema_version, planner_version,
                   status, title, blocks_json, source_report_id,
                   input_hash, grounding_snapshot_hash, created_at,
                   updated_at, version
            FROM preparation_artifacts WHERE artifact_set_id = ?
            ORDER BY created_at ASC, material_type ASC
            """,
            (set_id,),
        )
        return [self._row_to_artifact(r) for r in cur.fetchall()]

    def update_status_for_case(self, case_id: str, old_status: ArtifactStatus, new_status: ArtifactStatus) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE preparation_artifacts
            SET status = ?, updated_at = ?, version = version + 1
            WHERE case_id = ? AND status = ?
            """,
            (
                new_status.value,
                to_iso_utc(now_utc()),
                case_id,
                old_status.value,
            ),
        )
        return cur.rowcount



class SQLitePreparationPackageRepository(PreparationPackageRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, package: PreparationPackage) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT version, user_id, case_id, artifact_set_id, source_report_id,
                   artifact_ids_json, artifact_versions_json, artifact_snapshots_json,
                   claim_snapshots_json, rendered_payload_hashes_json, verification_findings_json,
                   frozen_at, package_fingerprint, schema_version, verifier_version
            FROM preparation_packages
            WHERE package_id = ?
            """,
            (package.package_id,),
        )
        row = cur.fetchone()

        artifact_ids_json = canonical_json_dumps(package.artifact_ids)
        artifact_versions_json = canonical_json_dumps(package.artifact_versions)
        artifact_snapshots_json = canonical_json_dumps([s.to_dict() for s in package.artifact_snapshots])
        claim_snapshots_json = canonical_json_dumps([c.to_dict() for c in package.claim_snapshots])
        rendered_payload_hashes_json = canonical_json_dumps(package.rendered_payload_hashes)
        verification_findings_json = canonical_json_dumps(
            [f.to_dict() if hasattr(f, "to_dict") else f for f in package.verification_findings]
        )

        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO preparation_packages (
                        package_id, case_id, artifact_set_id, source_report_id,
                        user_id, status, artifact_ids_json, artifact_versions_json,
                        artifact_snapshots_json, claim_snapshots_json,
                        rendered_payload_hashes_json, verification_status,
                        verification_findings_json, frozen_at, package_fingerprint,
                        schema_version, verifier_version, created_at, updated_at,
                        reason, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        package.package_id,
                        package.case_id,
                        package.artifact_set_id,
                        package.source_report_id,
                        package.user_id,
                        package.status.value,
                        artifact_ids_json,
                        artifact_versions_json,
                        artifact_snapshots_json,
                        claim_snapshots_json,
                        rendered_payload_hashes_json,
                        package.verification_status.value,
                        verification_findings_json,
                        to_iso_utc(package.frozen_at),
                        package.package_fingerprint,
                        package.schema_version,
                        package.verifier_version,
                        to_iso_utc(package.created_at),
                        to_iso_utc(package.updated_at),
                        package.reason,
                        package.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(f"Failed to insert PreparationPackage '{package.package_id}': {e}") from e
        else:
            existing_version = row[0]
            existing_user_id = row[1]
            existing_case_id = row[2]
            existing_set_id = row[3]
            existing_source_report_id = row[4]
            existing_artifact_ids = row[5]
            existing_artifact_vers = row[6]
            existing_artifact_snaps = row[7]
            existing_claim_snaps = row[8]
            existing_rendered_hashes = row[9]
            existing_findings = row[10]
            existing_frozen_at = row[11]
            existing_fp = row[12]
            existing_schema_ver = row[13]
            existing_verifier_ver = row[14]

            expected_version = package.version - 1
            if expected_version != existing_version:
                raise OptimisticLockError("PreparationPackage", package.package_id, expected_version)

            if (
                package.user_id != existing_user_id
                or package.case_id != existing_case_id
                or package.artifact_set_id != existing_set_id
                or package.source_report_id != existing_source_report_id
                or artifact_ids_json != existing_artifact_ids
                or artifact_versions_json != existing_artifact_vers
                or artifact_snapshots_json != existing_artifact_snaps
                or claim_snapshots_json != existing_claim_snaps
                or rendered_payload_hashes_json != existing_rendered_hashes
                or verification_findings_json != existing_findings
                or to_iso_utc(package.frozen_at) != existing_frozen_at
                or package.package_fingerprint != existing_fp
                or package.schema_version != existing_schema_ver
                or package.verifier_version != existing_verifier_ver
            ):
                raise DomainValidationError(
                    f"Cannot mutate content-addressed fields of PreparationPackage '{package.package_id}'."
                )
            cur.execute(
                """
                UPDATE preparation_packages SET
                    status = ?, verification_status = ?,
                    verification_findings_json = ?, updated_at = ?,
                    reason = ?, version = ?
                WHERE package_id = ? AND version = ?
                """,
                (
                    package.status.value,
                    package.verification_status.value,
                    verification_findings_json,
                    to_iso_utc(package.updated_at),
                    package.reason,
                    package.version,
                    package.package_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("PreparationPackage", package.package_id, expected_version)

    def _row_to_package(self, row: tuple[Any, ...]) -> PreparationPackage:
        raw_artifacts = json.loads(row[8])
        raw_claims = json.loads(row[9])
        artifact_snaps = [ArtifactPackageSnapshot.from_dict(d) for d in raw_artifacts]
        claim_snaps = [SafeClaimSnapshot.from_dict(d) for d in raw_claims]

        return PreparationPackage(
            package_id=row[0],
            case_id=row[1],
            artifact_set_id=row[2],
            source_report_id=row[3],
            user_id=row[4],
            status=PreparationPackageStatus(row[5]),
            artifact_ids=json.loads(row[6]),
            artifact_versions=json.loads(row[7]),
            artifact_snapshots=artifact_snaps,
            claim_snapshots=claim_snaps,
            rendered_payload_hashes=json.loads(row[10]),
            verification_status=PackageVerificationStatus(row[11]),
            verification_findings=json.loads(row[12]),
            frozen_at=from_iso_utc(row[13]) or now_utc(),
            package_fingerprint=row[14],
            schema_version=row[15],
            verifier_version=row[16],
            created_at=from_iso_utc(row[17]) or now_utc(),
            updated_at=from_iso_utc(row[18]) or now_utc(),
            reason=row[19],
            version=row[20],
        )

    def get_by_id(self, package_id: str) -> PreparationPackage | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT package_id, case_id, artifact_set_id, source_report_id,
                   user_id, status, artifact_ids_json, artifact_versions_json,
                   artifact_snapshots_json, claim_snapshots_json,
                   rendered_payload_hashes_json, verification_status,
                   verification_findings_json, frozen_at, package_fingerprint,
                   schema_version, verifier_version, created_at, updated_at,
                   reason, version
            FROM preparation_packages
            WHERE package_id = ?
            """,
            (package_id,),
        )
        row = cur.fetchone()
        return self._row_to_package(row) if row else None

    def get_by_fingerprint(self, fingerprint: str) -> PreparationPackage | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT package_id, case_id, artifact_set_id, source_report_id,
                   user_id, status, artifact_ids_json, artifact_versions_json,
                   artifact_snapshots_json, claim_snapshots_json,
                   rendered_payload_hashes_json, verification_status,
                   verification_findings_json, frozen_at, package_fingerprint,
                   schema_version, verifier_version, created_at, updated_at,
                   reason, version
            FROM preparation_packages
            WHERE package_fingerprint = ?
            """,
            (fingerprint,),
        )
        row = cur.fetchone()
        return self._row_to_package(row) if row else None

    def get_latest_for_case(self, case_id: str) -> PreparationPackage | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT package_id, case_id, artifact_set_id, source_report_id,
                   user_id, status, artifact_ids_json, artifact_versions_json,
                   artifact_snapshots_json, claim_snapshots_json,
                   rendered_payload_hashes_json, verification_status,
                   verification_findings_json, frozen_at, package_fingerprint,
                   schema_version, verifier_version, created_at, updated_at,
                   reason, version
            FROM preparation_packages
            WHERE case_id = ?
            ORDER BY created_at DESC, version DESC
            LIMIT 1
            """,
            (case_id,),
        )
        row = cur.fetchone()
        return self._row_to_package(row) if row else None

    def list_for_case(self, case_id: str) -> list[PreparationPackage]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT package_id, case_id, artifact_set_id, source_report_id,
                   user_id, status, artifact_ids_json, artifact_versions_json,
                   artifact_snapshots_json, claim_snapshots_json,
                   rendered_payload_hashes_json, verification_status,
                   verification_findings_json, frozen_at, package_fingerprint,
                   schema_version, verifier_version, created_at, updated_at,
                   reason, version
            FROM preparation_packages
            WHERE case_id = ?
            ORDER BY created_at DESC, version DESC
            """,
            (case_id,),
        )
        return [self._row_to_package(row) for row in cur.fetchall()]

    def list_by_set(self, artifact_set_id: str) -> list[PreparationPackage]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT package_id, case_id, artifact_set_id, source_report_id,
                   user_id, status, artifact_ids_json, artifact_versions_json,
                   artifact_snapshots_json, claim_snapshots_json,
                   rendered_payload_hashes_json, verification_status,
                   verification_findings_json, frozen_at, package_fingerprint,
                   schema_version, verifier_version, created_at, updated_at,
                   reason, version
            FROM preparation_packages
            WHERE artifact_set_id = ?
            ORDER BY created_at DESC, version DESC
            """,
            (artifact_set_id,),
        )
        return [self._row_to_package(row) for row in cur.fetchall()]


class SQLiteExecutionUploadAssetRepository(ExecutionUploadAssetRepository):
    """SQLite implementation of ExecutionUploadAssetRepository."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, asset: ExecutionUploadAsset) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO execution_upload_assets (
                asset_id, plan_id, case_id, user_id, source_artifact_id,
                source_artifact_version, role, filename, mime_type,
                byte_size, content_sha256, content_bytes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                byte_size = excluded.byte_size,
                content_sha256 = excluded.content_sha256,
                content_bytes = excluded.content_bytes
            """,
            (
                asset.asset_id,
                asset.plan_id,
                asset.case_id,
                asset.user_id,
                asset.source_artifact_id,
                asset.source_artifact_version,
                asset.role,
                asset.filename,
                asset.mime_type,
                asset.byte_size,
                asset.content_sha256,
                asset.content_bytes or b"",
                to_iso_utc(asset.created_at),
            ),
        )

    def _row_to_asset(self, row: tuple[Any, ...]) -> ExecutionUploadAsset:
        return ExecutionUploadAsset(
            asset_id=row[0],
            plan_id=row[1],
            case_id=row[2],
            user_id=row[3],
            source_artifact_id=row[4],
            source_artifact_version=row[5],
            role=row[6],
            filename=row[7],
            mime_type=row[8],
            byte_size=row[9],
            content_sha256=row[10],
            content_bytes=row[11] if isinstance(row[11], bytes) else (row[11].encode("utf-8") if row[11] else b""),
            created_at=from_iso_utc(row[12]) or now_utc(),
            materialized=bool(row[9] > 0 and row[10]),
        )

    def get_by_id(self, asset_id: str) -> ExecutionUploadAsset | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT asset_id, plan_id, case_id, user_id, source_artifact_id,
                   source_artifact_version, role, filename, mime_type,
                   byte_size, content_sha256, content_bytes, created_at
            FROM execution_upload_assets
            WHERE asset_id = ?
            """,
            (asset_id,),
        )
        row = cur.fetchone()
        return self._row_to_asset(row) if row else None

    def list_by_plan(self, plan_id: str) -> list[ExecutionUploadAsset]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT asset_id, plan_id, case_id, user_id, source_artifact_id,
                   source_artifact_version, role, filename, mime_type,
                   byte_size, content_sha256, content_bytes, created_at
            FROM execution_upload_assets
            WHERE plan_id = ?
            ORDER BY created_at ASC
            """,
            (plan_id,),
        )
        return [self._row_to_asset(row) for row in cur.fetchall()]

    def list_by_case(self, case_id: str) -> list[ExecutionUploadAsset]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT asset_id, plan_id, case_id, user_id, source_artifact_id,
                   source_artifact_version, role, filename, mime_type,
                   byte_size, content_sha256, content_bytes, created_at
            FROM execution_upload_assets
            WHERE case_id = ?
            ORDER BY created_at ASC
            """,
            (case_id,),
        )
        return [self._row_to_asset(row) for row in cur.fetchall()]


class SQLiteExternalExecutionPlanRepository(ExternalExecutionPlanRepository):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(self, plan: ExternalExecutionPlan) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT version, user_id, case_id, opportunity_id, package_id,
                   package_fingerprint, destination_provenance, destination_json,
                   target_schema_json, target_schema_hash, field_mappings_json,
                   upload_mappings_json, screening_responses_json,
                   final_submit_control_json, execution_input_hash,
                   execution_plan_fingerprint, schema_version, planner_version
            FROM external_execution_plans
            WHERE plan_id = ?
            """,
            (plan.plan_id,),
        )
        row = cur.fetchone()

        destination_provenance = plan.destination_provenance or plan.destination.destination_provenance
        destination_json = canonical_json_dumps(plan.destination.to_dict())
        target_schema_json = canonical_json_dumps(plan.target_schema.to_dict()) if plan.target_schema else None
        target_schema_hash = plan.target_schema_hash or (plan.target_schema.schema_hash if plan.target_schema else "")
        field_mappings_json = canonical_json_dumps([m.to_dict() for m in plan.field_mappings])
        upload_mappings_json = canonical_json_dumps([u.to_dict() for u in plan.upload_mappings])
        screening_responses_json = canonical_json_dumps([s.to_dict() for s in plan.screening_responses])
        final_submit_control_json = (
            canonical_json_dumps(plan.final_submit_control.to_dict()) if plan.final_submit_control else None
        )
        planning_findings_json = canonical_json_dumps([f.to_dict() for f in plan.findings])

        if row is None:
            try:
                cur.execute(
                    """
                    INSERT INTO external_execution_plans (
                        plan_id, case_id, opportunity_id, package_id,
                        package_fingerprint, user_id, status, destination_provenance,
                        destination_json, target_schema_json, target_schema_hash,
                        field_mappings_json, upload_mappings_json,
                        screening_responses_json, final_submit_control_json,
                        planning_findings_json, execution_input_hash, execution_plan_fingerprint,
                        schema_version, planner_version, created_at, updated_at,
                        version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.plan_id,
                        plan.case_id,
                        plan.opportunity_id,
                        plan.package_id,
                        plan.package_fingerprint,
                        plan.user_id,
                        plan.status.value,
                        destination_provenance,
                        destination_json,
                        target_schema_json,
                        target_schema_hash,
                        field_mappings_json,
                        upload_mappings_json,
                        screening_responses_json,
                        final_submit_control_json,
                        planning_findings_json,
                        plan.execution_input_hash,
                        plan.execution_plan_fingerprint,
                        plan.schema_version,
                        plan.planner_version,
                        to_iso_utc(plan.created_at),
                        to_iso_utc(plan.updated_at),
                        plan.version,
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise PersistenceError(f"Failed to insert ExternalExecutionPlan '{plan.plan_id}': {e}") from e
        else:
            existing_version = row[0]
            existing_user_id = row[1]
            existing_case_id = row[2]
            existing_opportunity_id = row[3]
            existing_package_id = row[4]
            existing_package_fingerprint = row[5]
            existing_dest_provenance = row[6]
            existing_dest = row[7]
            existing_schema = row[8]
            existing_schema_hash = row[9]
            existing_fields = row[10]
            existing_uploads = row[11]
            existing_screening = row[12]
            existing_control = row[13]
            existing_input_hash = row[14]
            existing_plan_fp = row[15]
            existing_schema_ver = row[16]
            existing_planner_ver = row[17]

            expected_version = plan.version - 1
            if expected_version != existing_version:
                raise OptimisticLockError("ExternalExecutionPlan", plan.plan_id, expected_version)

            if (
                plan.user_id != existing_user_id
                or plan.case_id != existing_case_id
                or plan.opportunity_id != existing_opportunity_id
                or plan.package_id != existing_package_id
                or plan.package_fingerprint != existing_package_fingerprint
                or destination_provenance != existing_dest_provenance
                or destination_json != existing_dest
                or target_schema_json != existing_schema
                or target_schema_hash != existing_schema_hash
                or field_mappings_json != existing_fields
                or upload_mappings_json != existing_uploads
                or screening_responses_json != existing_screening
                or final_submit_control_json != existing_control
                or plan.execution_input_hash != existing_input_hash
                or plan.execution_plan_fingerprint != existing_plan_fp
                or plan.schema_version != existing_schema_ver
                or plan.planner_version != existing_planner_ver
            ):
                raise DomainValidationError(
                    f"Cannot mutate content-addressed fields of ExternalExecutionPlan '{plan.plan_id}'."
                )

            cur.execute(
                """
                UPDATE external_execution_plans SET
                    status = ?, planning_findings_json = ?, updated_at = ?, version = ?
                WHERE plan_id = ? AND version = ?
                """,
                (
                    plan.status.value,
                    planning_findings_json,
                    to_iso_utc(plan.updated_at),
                    plan.version,
                    plan.plan_id,
                    expected_version,
                ),
            )
            if cur.rowcount == 0:
                raise OptimisticLockError("ExternalExecutionPlan", plan.plan_id, expected_version)

        # Durable upload byte asset persistence (Clarification 2)
        for u in plan.upload_mappings:
            cur.execute(
                """
                INSERT INTO execution_upload_assets (
                    asset_id, plan_id, case_id, user_id, source_artifact_id,
                    source_artifact_version, role, filename, mime_type,
                    byte_size, content_sha256, content_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    byte_size = excluded.byte_size,
                    content_sha256 = excluded.content_sha256,
                    content_bytes = excluded.content_bytes
                """,
                (
                    u.asset_id,
                    u.plan_id or plan.plan_id,
                    u.case_id or plan.case_id,
                    u.user_id or plan.user_id,
                    u.source_artifact_id,
                    u.source_artifact_version,
                    u.role,
                    u.filename,
                    u.mime_type,
                    u.byte_size,
                    u.content_sha256,
                    u.content_bytes or b"",
                    to_iso_utc(u.created_at),
                ),
            )

    def _row_to_plan(self, row: tuple[Any, ...]) -> ExternalExecutionPlan:
        destination_provenance = row[7]
        dest_dict = json.loads(row[8])
        target_schema_dict = json.loads(row[9]) if row[9] else None
        target_schema_hash = row[10]
        fields_list = json.loads(row[11])
        uploads_list = json.loads(row[12])
        screening_list = json.loads(row[13])
        control_dict = json.loads(row[14]) if row[14] else None
        findings_list = json.loads(row[15])

        return ExternalExecutionPlan(
            plan_id=row[0],
            case_id=row[1],
            opportunity_id=row[2],
            package_id=row[3],
            package_fingerprint=row[4],
            user_id=row[5],
            status=ExternalExecutionPlanStatus(row[6]),
            destination_provenance=destination_provenance,
            destination=ExecutionDestination.from_dict(dest_dict),
            target_schema=TargetInteractionSchema.from_dict(target_schema_dict) if target_schema_dict else None,
            target_schema_hash=target_schema_hash,
            field_mappings=[ExecutionFieldMapping.from_dict(m) for m in fields_list],
            upload_mappings=[ExecutionUploadAsset.from_dict(u) for u in uploads_list],
            screening_responses=[ExecutionScreeningResponse.from_dict(s) for s in screening_list],
            final_submit_control=TargetSubmitControlSpec.from_dict(control_dict) if control_dict else None,
            findings=[ExecutionPlanFinding.from_dict(f) for f in findings_list],
            execution_input_hash=row[16],
            execution_plan_fingerprint=row[17],
            schema_version=row[18],
            planner_version=row[19],
            created_at=from_iso_utc(row[20]) or now_utc(),
            updated_at=from_iso_utc(row[21]) or now_utc(),
            version=row[22],
        )

    def get_by_id(self, plan_id: str) -> ExternalExecutionPlan | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT plan_id, case_id, opportunity_id, package_id,
                   package_fingerprint, user_id, status, destination_provenance,
                   destination_json, target_schema_json, target_schema_hash,
                   field_mappings_json, upload_mappings_json,
                   screening_responses_json, final_submit_control_json,
                   planning_findings_json, execution_input_hash, execution_plan_fingerprint,
                   schema_version, planner_version, created_at, updated_at,
                   version
            FROM external_execution_plans
            WHERE plan_id = ?
            """,
            (plan_id,),
        )
        row = cur.fetchone()
        return self._row_to_plan(row) if row else None

    def get_by_fingerprint(self, case_id: str, plan_fingerprint: str) -> ExternalExecutionPlan | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT plan_id, case_id, opportunity_id, package_id,
                   package_fingerprint, user_id, status, destination_provenance,
                   destination_json, target_schema_json, target_schema_hash,
                   field_mappings_json, upload_mappings_json,
                   screening_responses_json, final_submit_control_json,
                   planning_findings_json, execution_input_hash, execution_plan_fingerprint,
                   schema_version, planner_version, created_at, updated_at,
                   version
            FROM external_execution_plans
            WHERE case_id = ? AND execution_plan_fingerprint = ?
            """,
            (case_id, plan_fingerprint),
        )
        row = cur.fetchone()
        return self._row_to_plan(row) if row else None

    def get_latest_for_case(self, case_id: str) -> ExternalExecutionPlan | None:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT plan_id, case_id, opportunity_id, package_id,
                   package_fingerprint, user_id, status, destination_provenance,
                   destination_json, target_schema_json, target_schema_hash,
                   field_mappings_json, upload_mappings_json,
                   screening_responses_json, final_submit_control_json,
                   planning_findings_json, execution_input_hash, execution_plan_fingerprint,
                   schema_version, planner_version, created_at, updated_at,
                   version
            FROM external_execution_plans
            WHERE case_id = ?
            ORDER BY created_at DESC, version DESC
            LIMIT 1
            """,
            (case_id,),
        )
        row = cur.fetchone()
        return self._row_to_plan(row) if row else None

    def list_for_case(self, case_id: str) -> list[ExternalExecutionPlan]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT plan_id, case_id, opportunity_id, package_id,
                   package_fingerprint, user_id, status, destination_provenance,
                   destination_json, target_schema_json, target_schema_hash,
                   field_mappings_json, upload_mappings_json,
                   screening_responses_json, final_submit_control_json,
                   planning_findings_json, execution_input_hash, execution_plan_fingerprint,
                   schema_version, planner_version, created_at, updated_at,
                   version
            FROM external_execution_plans
            WHERE case_id = ?
            ORDER BY created_at DESC, version DESC
            """,
            (case_id,),
        )
        return [self._row_to_plan(row) for row in cur.fetchall()]

    def list_active_for_case(self, case_id: str) -> list[ExternalExecutionPlan]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT plan_id, case_id, opportunity_id, package_id,
                   package_fingerprint, user_id, status, destination_provenance,
                   destination_json, target_schema_json, target_schema_hash,
                   field_mappings_json, upload_mappings_json,
                   screening_responses_json, final_submit_control_json,
                   planning_findings_json, execution_input_hash, execution_plan_fingerprint,
                   schema_version, planner_version, created_at, updated_at,
                   version
            FROM external_execution_plans
            WHERE case_id = ? AND status != ?
            ORDER BY created_at DESC, version DESC
            """,
            (case_id, ExternalExecutionPlanStatus.SUPERSEDED.value),
        )
        return [self._row_to_plan(row) for row in cur.fetchall()]


class SQLiteCaseworkerUnitOfWork(CaseworkerUnitOfWork):
    """Transaction / Unit-of-Work boundary using SQLite."""

    def __init__(self, conn: sqlite3.Connection, close_on_exit: bool = False) -> None:
        self.conn = conn
        self._close_on_exit = close_on_exit
        self.missions = SQLiteMissionRepository(conn)
        self.cases = SQLiteCaseRepository(conn)
        self.opportunities = SQLiteOpportunityRepository(conn)
        self.actions = SQLiteActionRepository(conn)
        self.approvals = SQLiteApprovalRepository(conn)
        self.sources = SQLiteContextSourceRepository(conn)
        self.context = SQLiteContextRepository(conn)
        self.claims = SQLiteClaimRepository(conn)
        self.preparation_reports = SQLitePreparationReportRepository(conn)
        self.preparation_artifacts = SQLitePreparationArtifactRepository(conn)
        self.preparation_packages = SQLitePreparationPackageRepository(conn)
        self.execution_plans = SQLiteExternalExecutionPlanRepository(conn)
        self.upload_assets = SQLiteExecutionUploadAssetRepository(conn)
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
        try:
            if exc_type is not None:
                self.rollback()
            else:
                self.commit()
        finally:
            if self._close_on_exit:
                self.conn.close()


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
        return SQLiteCaseworkerUnitOfWork(conn, close_on_exit=not self._is_memory)

    def close(self) -> None:
        """Close shared resources."""
        if self._shared_conn:
            self._shared_conn.close()
            self._shared_conn = None
