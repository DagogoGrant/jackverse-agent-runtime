"""Unit tests for SQLiteMigrator versioned migrations and schema backward compatibility."""

import sqlite3
import unittest

from caseworker.persistence.migration import SQLiteMigrator


class TestSQLiteMigration(unittest.TestCase):
    def test_clean_database_migrates_to_current_version(self) -> None:
        conn = sqlite3.connect(":memory:")
        SQLiteMigrator.migrate(conn)

        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        version = cur.fetchone()[0]
        self.assertEqual(version, 6)

        # Verify all tables exist
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        expected_tables = {
            "missions",
            "cases",
            "opportunities",
            "actions",
            "approvals",
            "context_sources",
            "context_facts",
            "claims",
            "domain_events",
            "preparation_intelligence_reports",
        }
        for table in expected_tables:
            self.assertIn(table, tables, f"Expected table '{table}' missing after migration.")

        # Verify archived columns exist on missions
        cur.execute("PRAGMA table_info(missions);")
        columns = {row[1] for row in cur.fetchall()}
        self.assertIn("archived", columns)
        self.assertIn("archived_at", columns)

        # Verify v5 columns exist on opportunities
        cur.execute("PRAGMA table_info(opportunities);")
        opp_columns = {row[1] for row in cur.fetchall()}
        self.assertIn("converted_case_id", opp_columns)
        self.assertIn("fingerprint_v2", opp_columns)
        self.assertIn("external_posting_id", opp_columns)

        conn.close()

    def test_migration_from_v1_schema_preserves_data(self) -> None:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()

        # 1. Setup exact Milestone 1 (v1) schema
        cur.execute("""
            CREATE TABLE missions (
                mission_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                target_outcome TEXT NOT NULL,
                constraints TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );
        """)
        cur.execute("""
            CREATE TABLE context_facts (
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
        """)
        cur.execute("PRAGMA user_version = 1;")

        # Insert sample v1 fact
        cur.execute("""
            INSERT INTO context_facts (
                fact_id, user_id, namespace, key, value, source_type,
                source_reference, confidence, verification_status, sensitivity,
                allowed_purposes, created_at, updated_at, version
            ) VALUES (
                'fact_v1_001', 'user_old', 'education', 'degree', '"BSc Computer Science"',
                'USER_INPUT', '', 1.0, 'USER_VERIFIED', 'PERSONAL', '[]',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 1
            );
        """)
        conn.commit()

        # 2. Run migration to v5
        SQLiteMigrator.migrate(conn)

        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 6)

        # 3. Verify new columns exist in context_facts and missions
        cur.execute("PRAGMA table_info(context_facts);")
        columns = {row[1] for row in cur.fetchall()}
        self.assertIn("source_id", columns)
        self.assertIn("rejection_reason", columns)

        cur.execute("PRAGMA table_info(missions);")
        mission_columns = {row[1] for row in cur.fetchall()}
        self.assertIn("archived", mission_columns)
        self.assertIn("archived_at", mission_columns)

        # 4. Verify existing data preserved and queryable
        cur.execute("SELECT fact_id, user_id, key, source_id, rejection_reason FROM context_facts WHERE fact_id = 'fact_v1_001';")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "fact_v1_001")
        self.assertEqual(row[1], "user_old")
        self.assertEqual(row[2], "degree")
        self.assertIsNone(row[3])  # source_id default NULL
        self.assertIsNone(row[4])  # rejection_reason default NULL

        # 5. Verify new tables are usable
        cur.execute("SELECT COUNT(*) FROM context_sources;")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT COUNT(*) FROM claims;")
        self.assertEqual(cur.fetchone()[0], 0)

        conn.close()

    def test_migration_from_v2_schema_preserves_data(self) -> None:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()

        # 1. Setup exact Milestone 2 (v2) schema missions table without archived columns
        cur.execute("""
            CREATE TABLE missions (
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
        """)
        cur.execute("PRAGMA user_version = 2;")

        # Insert sample v2 mission
        cur.execute("""
            INSERT INTO missions (
                mission_id, user_id, title, goal, kind, status,
                success_criteria, constraints, created_at, updated_at, version
            ) VALUES (
                'm_v2_001', 'user_v2', 'Original Mission', 'Goal 1', 'general_goal',
                'active', '[]', '[]', '2026-02-01T00:00:00Z', '2026-02-01T00:00:00Z', 1
            );
        """)
        conn.commit()

        # 2. Run migration to v5
        SQLiteMigrator.migrate(conn)

        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 6)

        # 3. Verify columns added
        cur.execute("PRAGMA table_info(missions);")
        columns = {row[1] for row in cur.fetchall()}
        self.assertIn("archived", columns)
        self.assertIn("archived_at", columns)

        # 4. Verify existing record preserved and defaulted
        cur.execute("SELECT mission_id, title, status, archived, archived_at FROM missions WHERE mission_id = 'm_v2_001';")
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "m_v2_001")
        self.assertEqual(row[1], "Original Mission")
        self.assertEqual(row[2], "active")
        self.assertEqual(row[3], 0)  # archived defaults to 0
        self.assertIsNone(row[4])   # archived_at defaults to NULL

        conn.close()

    def test_migration_from_v3_schema_preserves_data(self) -> None:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()

        # 1. Setup exact v3 schema opportunities table without converted_case_id and fingerprint_v2
        cur.execute("""
            CREATE TABLE opportunities (
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
                version INTEGER NOT NULL DEFAULT 1
            );
        """)
        cur.execute("PRAGMA user_version = 3;")

        # Insert sample v3 opportunity
        cur.execute("""
            INSERT INTO opportunities (
                opportunity_id, user_id, opportunity_type, title, organization,
                source_url, source_name, location, status, requirements, metadata,
                fingerprint, discovered_at, version
            ) VALUES (
                'opp_v3_001', 'user_v3', 'job', 'AI Engineer', 'DeepMind',
                'https://example.com/job', 'Manual', 'Berlin', 'discovered',
                '[]', '{}', 'fp_legacy', '2026-03-01T00:00:00Z', 1
            );
        """)
        conn.commit()

        # 2. Run migration to v5
        SQLiteMigrator.migrate(conn)

        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 6)

        # 3. Verify columns added
        cur.execute("PRAGMA table_info(opportunities);")
        columns = {row[1] for row in cur.fetchall()}
        self.assertIn("converted_case_id", columns)
        self.assertIn("fingerprint_v2", columns)
        self.assertIn("external_posting_id", columns)

        # 4. Verify existing record preserved and backfilled
        cur.execute(
            "SELECT opportunity_id, title, status, converted_case_id, fingerprint_v2, external_posting_id FROM opportunities WHERE opportunity_id = 'opp_v3_001';"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "opp_v3_001")
        self.assertEqual(row[1], "AI Engineer")
        self.assertEqual(row[2], "discovered")
        self.assertIsNone(row[3])  # converted_case_id defaults to NULL
        self.assertTrue(bool(row[4]))  # fingerprint_v2 backfilled!
        self.assertIsNone(row[5])  # external_posting_id defaults to NULL

        conn.close()

    def test_migration_from_v4_schema_preserves_data(self) -> None:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()

        # 1. Setup exact v4 schema opportunities table
        cur.execute("""
            CREATE TABLE opportunities (
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
                converted_case_id TEXT,
                fingerprint_v2 TEXT
            );
        """)
        cur.execute("PRAGMA user_version = 4;")

        # Insert sample v4 opportunity
        cur.execute("""
            INSERT INTO opportunities (
                opportunity_id, user_id, opportunity_type, title, organization,
                source_url, source_name, location, status, requirements, metadata,
                fingerprint, discovered_at, version, converted_case_id, fingerprint_v2
            ) VALUES (
                'opp_v4_001', 'user_v4', 'job', 'Senior Researcher', 'OpenAI',
                'https://example.com/job', 'Manual', 'San Francisco', 'discovered',
                '[]', '{}', 'fp_legacy', '2026-04-01T00:00:00Z', 1, NULL, 'fp_v2_existing'
            );
        """)
        conn.commit()

        # 2. Run migration to v5
        SQLiteMigrator.migrate(conn)

        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 6)

        # 3. Verify columns added
        cur.execute("PRAGMA table_info(opportunities);")
        columns = {row[1] for row in cur.fetchall()}
        self.assertIn("external_posting_id", columns)

        # 4. Verify existing record preserved
        cur.execute(
            "SELECT opportunity_id, title, status, converted_case_id, fingerprint_v2, external_posting_id FROM opportunities WHERE opportunity_id = 'opp_v4_001';"
        )
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "opp_v4_001")
        self.assertEqual(row[1], "Senior Researcher")
        self.assertEqual(row[4], "fp_v2_existing")
        self.assertIsNone(row[5])

        conn.close()

    def test_migration_from_v5_schema_creates_prep_reports_table(self) -> None:
        """Verify Migration 5->6 creates preparation_intelligence_reports table and indexes."""
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()

        # Run migration baseline up to v5 by simulating schema
        conn.executescript("""
            CREATE TABLE cases (
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
                version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE opportunities (
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
                converted_case_id TEXT,
                fingerprint_v2 TEXT,
                external_posting_id TEXT
            );
        """)
        cur.execute("PRAGMA user_version = 5;")
        conn.commit()

        # Run migration to v6
        SQLiteMigrator.migrate(conn)

        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 6)

        # Verify table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='preparation_intelligence_reports';")
        self.assertIsNotNone(cur.fetchone())

        # Verify columns
        cur.execute("PRAGMA table_info(preparation_intelligence_reports);")
        columns = {row[1] for row in cur.fetchall()}
        expected_cols = {
            "report_id", "case_id", "opportunity_id", "user_id",
            "schema_version", "analyzer_version", "evidence_map_json",
            "gaps_json", "strategy_json", "input_hash",
            "context_snapshot_hash", "source_evaluation_id", "created_at", "version"
        }
        self.assertTrue(expected_cols.issubset(columns))

        # Verify indexes
        cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_prep_reports_%';")
        index_names = {row[0] for row in cur.fetchall()}
        self.assertIn("idx_prep_reports_case", index_names)
        self.assertIn("idx_prep_reports_user", index_names)

        conn.close()

    def test_migration_v5_backfill_always_uses_fallback_fingerprint(self) -> None:
        """Verify Migration 4->5 backfill computes fallback fingerprint_v2 even if external_posting_id is present."""
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()

        # Setup v4 schema
        cur.execute("""
            CREATE TABLE opportunities (
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
                converted_case_id TEXT,
                fingerprint_v2 TEXT
            );
        """)
        cur.execute("PRAGMA user_version = 4;")

        # Insert opportunity without fingerprint_v2
        cur.execute("""
            INSERT INTO opportunities (
                opportunity_id, user_id, opportunity_type, title, organization,
                source_url, source_name, location, status, requirements, metadata,
                fingerprint, discovered_at, version, converted_case_id, fingerprint_v2
            ) VALUES (
                'opp_backfill_001', 'user_test', 'job', 'Platform Engineer', 'JackVerse',
                'https://jackverse.com/jobs/1', 'Direct', 'Berlin', 'discovered',
                '[]', '{}', 'legacy_fp', '2026-04-01T00:00:00Z', 1, NULL, ''
            );
        """)
        conn.commit()

        SQLiteMigrator.migrate(conn)

        from caseworker.domain.opportunity import Opportunity, OpportunityType
        expected_opp = Opportunity(
            opportunity_id="opp_backfill_001",
            user_id="user_test",
            opportunity_type=OpportunityType.JOB,
            title="Platform Engineer",
            organization="JackVerse",
            location="Berlin",
        )
        cur.execute("SELECT fingerprint_v2 FROM opportunities WHERE opportunity_id = 'opp_backfill_001';")
        backfilled_fp = cur.fetchone()[0]
        self.assertEqual(backfilled_fp, expected_opp.fingerprint_v2)
        conn.close()

    def test_migration_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        SQLiteMigrator.migrate(conn)
        # Calling migrate again should be a no-op
        SQLiteMigrator.migrate(conn)

        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 6)
        conn.close()



if __name__ == "__main__":
    unittest.main()

