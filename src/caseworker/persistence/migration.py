"""Deterministic SQLite schema migration management for JackVerse Caseworker."""

from __future__ import annotations

import sqlite3

LATEST_SCHEMA_VERSION = 12

MIGRATION_V1_DDL = """
CREATE TABLE IF NOT EXISTS missions (
    mission_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    constraints TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deadline TEXT,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_missions_user ON missions(user_id, status);

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    mission_id TEXT,
    user_id TEXT NOT NULL,
    case_type TEXT NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    constraints TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deadline TEXT,
    resolved_at TEXT,
    outcome TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (mission_id) REFERENCES missions(mission_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_user ON cases(user_id, status);
CREATE INDEX IF NOT EXISTS idx_cases_mission ON cases(mission_id);

CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id TEXT PRIMARY KEY,
    mission_id TEXT,
    user_id TEXT NOT NULL,
    opportunity_type TEXT NOT NULL,
    title TEXT NOT NULL,
    organization TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    location TEXT NOT NULL,
    status TEXT NOT NULL,
    requirements TEXT NOT NULL,
    metadata TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    deadline TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (mission_id) REFERENCES missions(mission_id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunities_user_fp ON opportunities(user_id, fingerprint);
CREATE INDEX IF NOT EXISTS idx_opportunities_mission ON opportunities(mission_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_user ON opportunities(user_id, status);

CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    requires_approval INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    executed_at TEXT,
    parameters TEXT NOT NULL,
    result TEXT NOT NULL,
    idempotency_key TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_actions_idempotency ON actions(case_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_actions_case ON actions(case_id);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    action_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    decided_at TEXT,
    expires_at TEXT,
    reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (action_id) REFERENCES actions(action_id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_approvals_case ON approvals(case_id);
CREATE INDEX IF NOT EXISTS idx_approvals_action ON approvals(action_id);
CREATE INDEX IF NOT EXISTS idx_approvals_user_pending ON approvals(user_id, status);

CREATE TABLE IF NOT EXISTS context_facts (
    fact_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    confidence REAL NOT NULL,
    verification_status TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    allowed_purposes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    superseded_by_fact_id TEXT,
    superseded_at TEXT,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_context_user_ns_key ON context_facts(user_id, namespace, key);
CREATE INDEX IF NOT EXISTS idx_context_user_active ON context_facts(user_id, superseded_by_fact_id);

CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_aggregate_version ON domain_events(aggregate_type, aggregate_id, aggregate_version);
CREATE INDEX IF NOT EXISTS idx_events_user_occurred ON domain_events(user_id, occurred_at);
"""

MIGRATION_V2_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS context_sources (
    source_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    source_reference TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    sensitivity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_sources_user ON context_sources(user_id);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    case_id TEXT,
    mission_id TEXT,
    purpose TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    supporting_fact_ids TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    verified_at TEXT,
    rejection_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE SET NULL,
    FOREIGN KEY (mission_id) REFERENCES missions(mission_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_user ON claims(user_id, status);
CREATE INDEX IF NOT EXISTS idx_claims_case ON claims(case_id);
"""

MIGRATION_V6_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS preparation_intelligence_reports (
    report_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    opportunity_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    evidence_map_json TEXT NOT NULL,
    gaps_json TEXT NOT NULL,
    strategy_json TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    context_snapshot_hash TEXT NOT NULL,
    source_evaluation_id TEXT,
    created_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prep_reports_case ON preparation_intelligence_reports(case_id, created_at);
CREATE INDEX IF NOT EXISTS idx_prep_reports_user ON preparation_intelligence_reports(user_id);
"""


MIGRATION_V8_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS preparation_packages (
    package_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    artifact_set_id TEXT NOT NULL,
    source_report_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL,
    artifact_versions_json TEXT NOT NULL,
    artifact_snapshots_json TEXT NOT NULL,
    claim_snapshots_json TEXT NOT NULL,
    rendered_payload_hashes_json TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    verification_findings_json TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    package_fingerprint TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    verifier_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (source_report_id) REFERENCES preparation_intelligence_reports(report_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_prep_packages_fingerprint ON preparation_packages(package_fingerprint);
CREATE INDEX IF NOT EXISTS idx_prep_packages_case ON preparation_packages(case_id, status);
CREATE INDEX IF NOT EXISTS idx_prep_packages_set ON preparation_packages(artifact_set_id);
CREATE INDEX IF NOT EXISTS idx_prep_packages_user ON preparation_packages(user_id);
"""

MIGRATION_V9_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS external_execution_plans (
    plan_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    opportunity_id TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_fingerprint TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    destination_provenance TEXT NOT NULL,
    destination_json TEXT NOT NULL,
    target_schema_json TEXT,
    target_schema_hash TEXT NOT NULL,
    field_mappings_json TEXT NOT NULL,
    upload_mappings_json TEXT NOT NULL,
    screening_responses_json TEXT NOT NULL,
    final_submit_control_json TEXT,
    planning_findings_json TEXT NOT NULL,
    execution_input_hash TEXT NOT NULL,
    execution_plan_fingerprint TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    planner_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    email_manifest_json TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (package_id) REFERENCES preparation_packages(package_id) ON DELETE RESTRICT,
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_plans_fingerprint ON external_execution_plans(execution_plan_fingerprint);
CREATE INDEX IF NOT EXISTS idx_exec_plans_case ON external_execution_plans(case_id, status);
CREATE INDEX IF NOT EXISTS idx_exec_plans_package ON external_execution_plans(package_id);
CREATE INDEX IF NOT EXISTS idx_exec_plans_user ON external_execution_plans(user_id);

CREATE TABLE IF NOT EXISTS execution_upload_assets (
    asset_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_artifact_id TEXT NOT NULL,
    source_artifact_version INTEGER NOT NULL,
    role TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_bytes BLOB NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES external_execution_plans(plan_id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_exec_upload_assets_plan ON execution_upload_assets(plan_id);
CREATE INDEX IF NOT EXISTS idx_exec_upload_assets_case ON execution_upload_assets(case_id);
"""


MIGRATION_V7_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS preparation_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_set_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    opportunity_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    material_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    planner_version TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    source_report_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    grounding_snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(opportunity_id) ON DELETE CASCADE,
    FOREIGN KEY (source_report_id) REFERENCES preparation_intelligence_reports(report_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prep_artifacts_set ON preparation_artifacts(artifact_set_id);
CREATE INDEX IF NOT EXISTS idx_prep_artifacts_case ON preparation_artifacts(case_id, material_type);
CREATE INDEX IF NOT EXISTS idx_prep_artifacts_user ON preparation_artifacts(user_id);
"""


MIGRATION_V10_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS execution_leases (
    lease_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    lease_token TEXT NOT NULL UNIQUE,
    fencing_token INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES external_execution_plans(plan_id) ON DELETE CASCADE,
    FOREIGN KEY (action_id) REFERENCES actions(action_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_leases_active ON execution_leases(case_id, plan_id, action_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_exec_leases_case ON execution_leases(case_id);
CREATE INDEX IF NOT EXISTS idx_exec_leases_token ON execution_leases(lease_token);

CREATE TABLE IF NOT EXISTS execution_attempts (
    attempt_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    lease_token TEXT NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    provider_kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    last_reversible_point_reached_at TEXT,
    irreversible_action_taken INTEGER NOT NULL DEFAULT 0,
    staged_field_count INTEGER NOT NULL DEFAULT 0,
    staged_upload_count INTEGER NOT NULL DEFAULT 0,
    error_category TEXT,
    error_message TEXT,
    raw_response_snippet TEXT,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES external_execution_plans(plan_id) ON DELETE CASCADE,
    FOREIGN KEY (action_id) REFERENCES actions(action_id) ON DELETE CASCADE,
    FOREIGN KEY (approval_id) REFERENCES approvals(approval_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_exec_attempts_case ON execution_attempts(case_id);
CREATE INDEX IF NOT EXISTS idx_exec_attempts_action ON execution_attempts(action_id);
CREATE INDEX IF NOT EXISTS idx_exec_attempts_plan ON execution_attempts(plan_id);

CREATE TABLE IF NOT EXISTS execution_receipts (
    receipt_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE,
    action_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL,
    destination_hostname TEXT NOT NULL,
    destination_url TEXT NOT NULL,
    destination_type TEXT NOT NULL,
    execution_plan_fingerprint TEXT NOT NULL,
    authorized_action_fingerprint TEXT NOT NULL,
    package_fingerprint TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    external_reference_id TEXT,
    field_count INTEGER NOT NULL DEFAULT 0,
    upload_asset_ids_json TEXT NOT NULL,
    upload_asset_shas_json TEXT NOT NULL,
    screening_response_count INTEGER NOT NULL DEFAULT 0,
    safe_provider_metadata_json TEXT NOT NULL,
    receipt_hash TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (attempt_id) REFERENCES execution_attempts(attempt_id) ON DELETE CASCADE,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    FOREIGN KEY (plan_id) REFERENCES external_execution_plans(plan_id) ON DELETE CASCADE,
    FOREIGN KEY (action_id) REFERENCES actions(action_id) ON DELETE CASCADE,
    FOREIGN KEY (approval_id) REFERENCES approvals(approval_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_exec_receipts_case ON execution_receipts(case_id);
CREATE INDEX IF NOT EXISTS idx_exec_receipts_action ON execution_receipts(action_id);
CREATE INDEX IF NOT EXISTS idx_exec_receipts_plan ON execution_receipts(plan_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_receipts_hash ON execution_receipts(receipt_hash);
"""

MIGRATION_V12_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS user_oauth_credentials (
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    google_sub TEXT NOT NULL,
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT,
    token_expiry TEXT NOT NULL,
    authenticated_email TEXT NOT NULL,
    scopes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_user_oauth_creds_user ON user_oauth_credentials(user_id);
CREATE INDEX IF NOT EXISTS idx_user_oauth_creds_sub ON user_oauth_credentials(google_sub);

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_user ON oauth_states(user_id);
"""


class SQLiteMigrator:
    """Manages transactional database schema upgrades for Caseworker persistence."""

    LATEST_SCHEMA_VERSION = LATEST_SCHEMA_VERSION

    @staticmethod
    def get_current_version(conn: sqlite3.Connection) -> int:
        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        row = cur.fetchone()
        return int(row[0]) if row else 0

    @classmethod
    def migrate(cls, conn: sqlite3.Connection) -> int:
        """Apply all pending migrations sequentially within an immediate transaction."""
        current_version = cls.get_current_version(conn)

        if current_version >= LATEST_SCHEMA_VERSION:
            return current_version

        # Ensure foreign keys are active
        conn.execute("PRAGMA foreign_keys = ON;")

        # Migrate 0 -> 1 (Milestone 1 baseline schema)
        if current_version < 1:
            conn.executescript(MIGRATION_V1_DDL)
            conn.execute("PRAGMA user_version = 1;")
            current_version = 1

        # Migrate 1 -> 2 (Milestone 2 Personal Context Vault & Claim Ledger)
        if current_version < 2:
            conn.executescript(MIGRATION_V2_TABLES_DDL)

            # Check if columns already exist in context_facts before adding
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(context_facts);")
            columns = {row[1] for row in cur.fetchall()}

            if "source_id" not in columns:
                conn.execute(
                    "ALTER TABLE context_facts ADD COLUMN source_id TEXT REFERENCES context_sources(source_id) ON DELETE SET NULL;"
                )
            if "rejection_reason" not in columns:
                conn.execute("ALTER TABLE context_facts ADD COLUMN rejection_reason TEXT;")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_context_source ON context_facts(source_id);")
            conn.execute("PRAGMA user_version = 2;")
            current_version = 2

        # Migrate 2 -> 3 (Mission Archival columns & index)
        if current_version < 3:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(missions);")
            columns = {row[1] for row in cur.fetchall()}

            if "archived" not in columns:
                conn.execute("ALTER TABLE missions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0;")
            if "archived_at" not in columns:
                conn.execute("ALTER TABLE missions ADD COLUMN archived_at TEXT;")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_missions_user_archived ON missions(user_id, archived);")
            conn.execute("PRAGMA user_version = 3;")
            current_version = 3

        # Migrate 3 -> 4 (Phase 5.1: Opportunity Engine - converted_case_id & fingerprint_v2)
        if current_version < 4:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities';")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(opportunities);")
                columns = {row[1] for row in cur.fetchall()}

                if "converted_case_id" not in columns:
                    conn.execute(
                        "ALTER TABLE opportunities ADD COLUMN converted_case_id TEXT REFERENCES cases(case_id) ON DELETE SET NULL;"
                    )
                if "fingerprint_v2" not in columns:
                    conn.execute("ALTER TABLE opportunities ADD COLUMN fingerprint_v2 TEXT;")

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_opportunities_converted_case ON opportunities(converted_case_id);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_opportunities_user_fp_v2 ON opportunities(user_id, fingerprint_v2);"
                )
            conn.execute("PRAGMA user_version = 4;")
            current_version = 4

        # Migrate 4 -> 5 (Phase 5.1.1: Opportunity Engine Integrity - external_posting_id, indexing & V2 backfill)
        if current_version < 5:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='opportunities';")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(opportunities);")
                columns = {row[1] for row in cur.fetchall()}

                if "external_posting_id" not in columns:
                    conn.execute("ALTER TABLE opportunities ADD COLUMN external_posting_id TEXT;")

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_opportunities_ext_posting ON opportunities(user_id, external_posting_id);"
                )

                # Safe legacy fingerprint_v2 backfill for rows where fingerprint_v2 IS NULL or empty
                import hashlib
                import json
                cur.execute(
                    "SELECT opportunity_id, opportunity_type, organization, title, location, external_posting_id FROM opportunities WHERE fingerprint_v2 IS NULL OR fingerprint_v2 = '';"
                )
                rows = cur.fetchall()
                for opp_id, opp_type, org, title, loc, _ in rows:
                    components = [
                        "opp_v2",
                        opp_type or "",
                        (org or "").strip().lower(),
                        (title or "").strip().lower(),
                        (loc or "").strip().lower(),
                    ]
                    canonical = json.dumps(components, separators=(",", ":"), ensure_ascii=True)
                    fp_v2 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                    conn.execute(
                        "UPDATE opportunities SET fingerprint_v2 = ? WHERE opportunity_id = ?;",
                        (fp_v2, opp_id),
                    )

            conn.execute("PRAGMA user_version = 5;")
            current_version = 5

        # Migrate 5 -> 6 (Phase 5.2.1: Preparation Intelligence Reports)
        if current_version < 6:
            conn.executescript(MIGRATION_V6_TABLES_DDL)
            conn.execute("PRAGMA user_version = 6;")
            current_version = 6

        # Migrate 6 -> 7 (Phase 5.2.2: Claim-Bound Artifact Preparation)
        if current_version < 7:
            conn.executescript(MIGRATION_V7_TABLES_DDL)

            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='claims';")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(claims);")
                columns = {row[1] for row in cur.fetchall()}

                if "source_report_id" not in columns:
                    conn.execute("ALTER TABLE claims ADD COLUMN source_report_id TEXT;")
                if "source_claim_intent_id" not in columns:
                    conn.execute("ALTER TABLE claims ADD COLUMN source_claim_intent_id TEXT;")
                if "assertion_schema" not in columns:
                    conn.execute("ALTER TABLE claims ADD COLUMN assertion_schema TEXT;")
                if "assertion_json" not in columns:
                    conn.execute("ALTER TABLE claims ADD COLUMN assertion_json TEXT;")
                if "support_snapshot_hash" not in columns:
                    conn.execute("ALTER TABLE claims ADD COLUMN support_snapshot_hash TEXT;")

                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_claims_case_intent ON claims(case_id, source_claim_intent_id);"
                )

            conn.execute("PRAGMA user_version = 7;")
            current_version = 7

        # Migrate 7 -> 8 (Phase 5.2.3: Preparation Package Verification & Review)
        if current_version < 8:
            conn.executescript(MIGRATION_V8_TABLES_DDL)
            conn.execute("PRAGMA user_version = 8;")
            current_version = 8

        # Migrate 8 -> 9 (Phase 5.3.1: Exact External Action Planning)
        if current_version < 9:
            conn.executescript(MIGRATION_V9_TABLES_DDL)
            conn.execute("PRAGMA user_version = 9;")
            current_version = 9

        # Migrate 9 -> 10 (Phase 5.3.3: Governed External Execution)
        if current_version < 10:
            conn.executescript(MIGRATION_V10_TABLES_DDL)
            conn.execute("PRAGMA user_version = 10;")
            current_version = 10

        # Migrate 10 -> 11 (Phase 5.3.3.1: Governed Email Application Execution)
        if current_version < 11:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='external_execution_plans';")
            if cur.fetchone():
                cur.execute("PRAGMA table_info(external_execution_plans);")
                columns = {row[1] for row in cur.fetchall()}
                if "email_manifest_json" not in columns:
                    conn.execute("ALTER TABLE external_execution_plans ADD COLUMN email_manifest_json TEXT;")
            conn.execute("PRAGMA user_version = 11;")
            current_version = 11

        # Migrate 11 -> 12 (Phase 5.3.3.1 Hardening: Encrypted OAuth Credentials & PKCE OAuth States)
        if current_version < 12:
            conn.executescript(MIGRATION_V12_TABLES_DDL)
            conn.execute("PRAGMA user_version = 12;")
            current_version = 12

        return current_version

