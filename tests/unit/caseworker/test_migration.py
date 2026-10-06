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
        self.assertEqual(version, 3)

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
        }
        for table in expected_tables:
            self.assertIn(table, tables, f"Expected table '{table}' missing after migration.")

        # Verify archived columns exist on missions
        cur.execute("PRAGMA table_info(missions);")
        columns = {row[1] for row in cur.fetchall()}
        self.assertIn("archived", columns)
        self.assertIn("archived_at", columns)

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

        # 2. Run migration to v3
        SQLiteMigrator.migrate(conn)

        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 3)

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

        # 2. Run migration to v3
        SQLiteMigrator.migrate(conn)

        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 3)

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

    def test_migration_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        SQLiteMigrator.migrate(conn)
        # Calling migrate again should be a no-op
        SQLiteMigrator.migrate(conn)

        cur = conn.cursor()
        cur.execute("PRAGMA user_version;")
        self.assertEqual(cur.fetchone()[0], 3)
        conn.close()


if __name__ == "__main__":
    unittest.main()
