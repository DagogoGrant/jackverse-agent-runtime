"""Deterministic SQLite schema migration management for JackVerse Caseworker."""

from __future__ import annotations

import sqlite3

LATEST_SCHEMA_VERSION = 2

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


class SQLiteMigrator:
    """Manages transactional database schema upgrades for Caseworker persistence."""

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

        return current_version
