"""SQLite storage implementation for Phase 3A Persistent Long-Term Memory."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import struct
from typing import Any

from harness.memory.base import (
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryStore,
    normalize_content,
)


def build_fts_query(user_query: str, mode: str = "OR") -> str | None:
    """Build a safe FTS5 MATCH query string from arbitrary user input.

    Sanitizes input by extracting alphanumeric words, wrapping each token in double
    quotes to prevent collision with FTS5 operators/syntax, and joining with the
    specified boolean operator ('OR' or 'AND'). Returns None if no searchable
    tokens are found.
    """
    if not user_query or not user_query.strip():
        return None
    tokens = re.findall(r"\w+", user_query)
    if not tokens:
        return None
    mode_upper = mode.strip().upper()
    if mode_upper not in ("OR", "AND"):
        mode_upper = "OR"
    escaped_tokens = [f'"{t}"' for t in tokens]
    return f" {mode_upper} ".join(escaped_tokens)


class SQLiteMemoryStore:
    """Persistent SQLite-backed implementation of MemoryStore."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # Foreign keys enforce ON DELETE CASCADE constraints
        self._conn.execute("PRAGMA foreign_keys = ON;")
        # Recursive triggers enable AFTER DELETE triggers during INSERT OR REPLACE conflict resolution
        self._conn.execute("PRAGMA recursive_triggers = ON;")
        self._create_schema()

    def _create_schema(self) -> None:
        """Initialize relational table schema, indexes, and perform safe migrations."""
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'accepted',
                    normalized_content TEXT NOT NULL DEFAULT '',
                    memory_key TEXT,
                    memory_value TEXT,
                    expires_at TEXT,
                    supersedes_id TEXT,
                    superseded_by TEXT,
                    superseded_at TEXT,
                    memory_type TEXT NOT NULL DEFAULT 'declarative'
                );
                """
            )
            # Safe column migration for existing Phase 3A / 3B / 3C databases
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA table_info(memories);")
            existing_cols = {row[1] for row in cursor.fetchall()}
            migrations = [
                ("status", "TEXT NOT NULL DEFAULT 'accepted'"),
                ("normalized_content", "TEXT NOT NULL DEFAULT ''"),
                ("memory_key", "TEXT"),
                ("memory_value", "TEXT"),
                ("expires_at", "TEXT"),
                ("supersedes_id", "TEXT"),
                ("superseded_by", "TEXT"),
                ("superseded_at", "TEXT"),
                ("memory_type", "TEXT NOT NULL DEFAULT 'declarative'"),
            ]
            for col_name, col_def in migrations:
                if col_name not in existing_cols and existing_cols:
                    self._conn.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def};")

            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_content ON memories(content);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_normalized ON memories(normalized_content);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(memory_key);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_expires ON memories(expires_at);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);"
            )
            # Partial unique index: at most one active (accepted) record per structured memory_key
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_one_active_key 
                ON memories(memory_key) 
                WHERE memory_key IS NOT NULL AND status = 'accepted';
                """
            )

            # Contentless FTS5 virtual table for eligible declarative accepted memories
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    content,
                    memory_key,
                    memory_value,
                    content='',
                    tokenize='unicode61'
                );
                """
            )

            # Conditional triggers for FTS synchronization
            # 1. INSERT: only if new row is accepted declarative
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_memories_ai AFTER INSERT ON memories
                WHEN new.status = 'accepted' AND new.memory_type = 'declarative'
                BEGIN
                    INSERT INTO memory_fts(rowid, content, memory_key, memory_value)
                    VALUES (new.rowid, new.content, COALESCE(new.memory_key, ''), COALESCE(new.memory_value, ''));
                END;
                """
            )

            # 2. DELETE: only if old row was accepted declarative
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_memories_ad AFTER DELETE ON memories
                WHEN old.status = 'accepted' AND old.memory_type = 'declarative'
                BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, content, memory_key, memory_value)
                    VALUES ('delete', old.rowid, old.content, COALESCE(old.memory_key, ''), COALESCE(old.memory_value, ''));
                END;
                """
            )

            # 3. UPDATE: delete old row from FTS if it was eligible
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_memories_au_del AFTER UPDATE ON memories
                WHEN old.status = 'accepted' AND old.memory_type = 'declarative'
                BEGIN
                    INSERT INTO memory_fts(memory_fts, rowid, content, memory_key, memory_value)
                    VALUES ('delete', old.rowid, old.content, COALESCE(old.memory_key, ''), COALESCE(old.memory_value, ''));
                END;
                """
            )

            # 4. UPDATE: insert new row into FTS if it is eligible
            self._conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_memories_au_ins AFTER UPDATE ON memories
                WHEN new.status = 'accepted' AND new.memory_type = 'declarative'
                BEGIN
                    INSERT INTO memory_fts(rowid, content, memory_key, memory_value)
                    VALUES (new.rowid, new.content, COALESCE(new.memory_key, ''), COALESCE(new.memory_value, ''));
                END;
                """
            )

            # Backfill migration for pre-existing records (idempotent, prevents duplicate rowids)
            self._conn.execute(
                """
                INSERT INTO memory_fts(rowid, content, memory_key, memory_value)
                SELECT m.rowid, m.content, COALESCE(m.memory_key, ''), COALESCE(m.memory_value, '')
                FROM memories m
                WHERE m.status = 'accepted'
                  AND m.memory_type = 'declarative'
                  AND m.rowid NOT IN (SELECT rowid FROM memory_fts);
                """
            )

            # Dense embeddings table with composite primary key and foreign key cascade
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id TEXT NOT NULL,
                    model_fingerprint TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    model_revision TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    dtype TEXT NOT NULL DEFAULT 'float32',
                    content_hash TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (memory_id, model_fingerprint),
                    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_mem_emb_lookup
                ON memory_embeddings(memory_id, model_fingerprint);
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_mem_emb_model
                ON memory_embeddings(model_fingerprint);
                """
            )

    def _row_to_entry(self, row: tuple[Any, ...]) -> MemoryEntry:
        (
            entry_id,
            created_at,
            content,
            source_str,
            metadata_json,
            status_str,
            norm_content,
            mem_key,
            mem_val,
            exp_at,
            sup_id,
            sup_by,
            sup_at,
            mem_type_str,
        ) = row
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except Exception:
            metadata = {}
        try:
            status = MemoryStatus(status_str) if status_str else MemoryStatus.ACCEPTED
        except ValueError:
            status = MemoryStatus.ACCEPTED
        try:
            from harness.memory.base import MemoryType
            mem_type = MemoryType(mem_type_str) if mem_type_str else MemoryType.DECLARATIVE
        except (ValueError, ImportError):
            from harness.memory.base import MemoryType
            mem_type = MemoryType.DECLARATIVE

        return MemoryEntry(
            id=entry_id,
            created_at=created_at,
            content=content,
            source=MemorySource(source_str),
            status=status,
            memory_type=mem_type,
            normalized_content=norm_content or "",
            memory_key=mem_key,
            memory_value=mem_val,
            expires_at=exp_at,
            supersedes_id=sup_id,
            superseded_by=sup_by,
            superseded_at=sup_at,
            metadata=metadata,
        )

    def add(self, entry: MemoryEntry) -> None:
        """Insert or replace a memory entry."""
        clean_meta = {
            k: v for k, v in entry.metadata.items()
            if not (
                (k == "memory_key" and entry.memory_key is not None)
                or (k == "memory_value" and entry.memory_value is not None)
                or (k == "expires_at" and entry.expires_at is not None)
                or (k == "supersedes_id" and entry.supersedes_id is not None)
                or (k == "superseded_by" and entry.superseded_by is not None)
                or (k == "superseded_at" and entry.superseded_at is not None)
            )
        }
        metadata_json = json.dumps(clean_meta)
        norm = entry.normalized_content or normalize_content(entry.content)
        status_val = entry.status.value if isinstance(entry.status, MemoryStatus) else str(entry.status)
        type_val = (
            entry.memory_type.value
            if hasattr(entry, "memory_type") and hasattr(entry.memory_type, "value")
            else str(getattr(entry, "memory_type", "declarative"))
        )
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memories (
                    id, created_at, content, source, metadata_json, status,
                    normalized_content, memory_key, memory_value, expires_at,
                    supersedes_id, superseded_by, superseded_at, memory_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    entry.id,
                    entry.created_at,
                    entry.content,
                    entry.source.value,
                    metadata_json,
                    status_val,
                    norm,
                    entry.memory_key,
                    entry.memory_value,
                    entry.expires_at,
                    entry.supersedes_id,
                    entry.superseded_by,
                    entry.superseded_at,
                    type_val,
                ),
            )

    def get(self, entry_id: str) -> MemoryEntry | None:
        """Fetch memory entry by unique identifier."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, created_at, content, source, metadata_json, status,
                   normalized_content, memory_key, memory_value, expires_at,
                   supersedes_id, superseded_by, superseded_at, memory_type
            FROM memories WHERE id = ?;
            """,
            (entry_id,),
        )
        row = cursor.fetchone()
        return self._row_to_entry(row) if row else None

    def list_all(
        self,
        limit: int | None = 100,
        status: MemoryStatus | None = None,
        memory_type: Any | None = None,
    ) -> list[MemoryEntry]:
        """List stored memories ordered by created_at descending, optionally filtered by status and memory_type."""
        cursor = self._conn.cursor()
        query = """
            SELECT id, created_at, content, source, metadata_json, status,
                   normalized_content, memory_key, memory_value, expires_at,
                   supersedes_id, superseded_by, superseded_at, memory_type
            FROM memories
        """
        conditions: list[str] = []
        params: list[Any] = []
        if status is not None:
            status_val = status.value if isinstance(status, MemoryStatus) else str(status)
            conditions.append("status = ?")
            params.append(status_val)
        if memory_type is not None:
            type_val = memory_type.value if hasattr(memory_type, "value") else str(memory_type)
            conditions.append("memory_type = ?")
            params.append(type_val)

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        if limit is not None:
            params.append(limit)
            limit_clause = " LIMIT ?"
        else:
            limit_clause = ""
        cursor.execute(f"{query}{where_clause} ORDER BY created_at DESC{limit_clause};", tuple(params))
        return [self._row_to_entry(r) for r in cursor.fetchall()]

    def find_exact_content(self, content: str) -> MemoryEntry | None:
        """Find an existing entry with exact character-for-character matching content."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, created_at, content, source, metadata_json, status,
                   normalized_content, memory_key, memory_value, expires_at,
                   supersedes_id, superseded_by, superseded_at, memory_type
            FROM memories WHERE content = ? LIMIT 1;
            """,
            (content,),
        )
        row = cursor.fetchone()
        return self._row_to_entry(row) if row else None

    def find_normalized_content(self, normalized_content: str) -> MemoryEntry | None:
        """Find an existing entry with matching normalized content."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, created_at, content, source, metadata_json, status,
                   normalized_content, memory_key, memory_value, expires_at,
                   supersedes_id, superseded_by, superseded_at, memory_type
            FROM memories WHERE normalized_content = ? LIMIT 1;
            """,
            (normalized_content,),
        )
        row = cursor.fetchone()
        return self._row_to_entry(row) if row else None

    def find_active_by_key(self, memory_key: str) -> MemoryEntry | None:
        """Find currently accepted active entry for a structured memory key."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT id, created_at, content, source, metadata_json, status,
                   normalized_content, memory_key, memory_value, expires_at,
                   supersedes_id, superseded_by, superseded_at, memory_type
            FROM memories WHERE memory_key = ? AND status = 'accepted' LIMIT 1;
            """,
            (memory_key,),
        )
        row = cursor.fetchone()
        return self._row_to_entry(row) if row else None

    def supersede_and_add(self, new_entry: MemoryEntry, memory_key: str) -> str | None:
        """Atomically transition any active entry for memory_key to SUPERSEDED and insert new_entry.

        Returns the superseded entry ID if an existing active entry was replaced, or None.
        Enforces that at most one ACCEPTED row exists for any non-null memory_key.
        """
        clean_meta = {
            k: v for k, v in new_entry.metadata.items()
            if k not in ("memory_key", "memory_value", "expires_at", "supersedes_id", "superseded_by", "superseded_at")
        }
        metadata_json = json.dumps(clean_meta)
        norm = new_entry.normalized_content or normalize_content(new_entry.content)

        type_val = (
            new_entry.memory_type.value
            if hasattr(new_entry, "memory_type") and hasattr(new_entry.memory_type, "value")
            else str(getattr(new_entry, "memory_type", "declarative"))
        )

        with self._conn:
            cursor = self._conn.cursor()

            # 1. Find existing active entry for this key
            cursor.execute(
                "SELECT id FROM memories WHERE memory_key = ? AND status = 'accepted';",
                (memory_key,),
            )
            row = cursor.fetchone()
            superseded_id = row[0] if row else None

            # 2. Update existing entry to SUPERSEDED
            if superseded_id is not None:
                cursor.execute(
                    """
                    UPDATE memories
                    SET status = 'superseded', superseded_by = ?, superseded_at = ?
                    WHERE id = ?;
                    """,
                    (new_entry.id, new_entry.created_at, superseded_id),
                )

            # 3. Insert new entry as ACCEPTED
            cursor.execute(
                """
                INSERT INTO memories (
                    id, created_at, content, source, metadata_json, status,
                    normalized_content, memory_key, memory_value, expires_at,
                    supersedes_id, superseded_by, superseded_at, memory_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    new_entry.id,
                    new_entry.created_at,
                    new_entry.content,
                    new_entry.source.value,
                    metadata_json,
                    MemoryStatus.ACCEPTED.value,
                    norm,
                    memory_key,
                    new_entry.memory_value,
                    new_entry.expires_at,
                    superseded_id,
                    None,
                    None,
                    type_val,
                ),
            )
            return superseded_id

    def list_procedural(
        self,
        tool_names: list[str] | None = None,
        limit_per_tool: int = 2,
    ) -> list[MemoryEntry]:
        """Fetch active procedural recovery lessons, optionally filtered and bounded per tool."""
        cursor = self._conn.cursor()
        query = """
            SELECT id, created_at, content, source, metadata_json, status,
                   normalized_content, memory_key, memory_value, expires_at,
                   supersedes_id, superseded_by, superseded_at, memory_type
            FROM memories
            WHERE status = 'accepted' AND memory_type = 'procedural'
            ORDER BY created_at DESC;
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        all_procedural = [self._row_to_entry(r) for r in rows]

        if not tool_names:
            return all_procedural[:limit_per_tool]

        by_tool: dict[str, list[MemoryEntry]] = {}
        for entry in all_procedural:
            t_name = entry.metadata.get("tool_name")
            if t_name in tool_names:
                by_tool.setdefault(t_name, [])
                if len(by_tool[t_name]) < limit_per_tool:
                    by_tool[t_name].append(entry)

        results: list[MemoryEntry] = []
        for t_name in tool_names:
            results.extend(by_tool.get(t_name, []))
        return results

    def count(
        self,
        status: MemoryStatus | None = None,
        memory_key: str | None = None,
        memory_type: Any | None = None,
    ) -> int:
        """Return count of stored memories, optionally filtered by status, memory_key, and memory_type."""
        cursor = self._conn.cursor()
        conditions: list[str] = []
        params: list[Any] = []

        if status is not None:
            status_val = status.value if isinstance(status, MemoryStatus) else str(status)
            conditions.append("status = ?")
            params.append(status_val)

        if memory_key is not None:
            conditions.append("memory_key = ?")
            params.append(memory_key)

        if memory_type is not None:
            type_val = memory_type.value if hasattr(memory_type, "value") else str(memory_type)
            conditions.append("memory_type = ?")
            params.append(type_val)

        where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        cursor.execute(f"SELECT COUNT(*) FROM memories{where_clause};", tuple(params))
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def count_active(
        self,
        memory_type: Any | None = None,
        now: str | datetime | None = None,
    ) -> int:
        """Count accepted entries of given memory_type that are currently fresh (not expired).

        Phase 3C freshness invariant: an accepted memory is active/fresh iff:
        expires_at is None OR current_time < expires_at
        """
        if now is None:
            now_dt = datetime.now(timezone.utc)
        elif isinstance(now, str):
            now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        elif isinstance(now, datetime):
            now_dt = now
        else:
            now_dt = datetime.now(timezone.utc)

        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        entries = self.list_all(limit=10000, status=MemoryStatus.ACCEPTED, memory_type=memory_type)
        fresh_count = 0
        for e in entries:
            if not getattr(e, "expires_at", None):
                fresh_count += 1
                continue
            try:
                exp_dt = datetime.fromisoformat(str(e.expires_at).replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if now_dt < exp_dt:
                    fresh_count += 1
            except (ValueError, TypeError):
                continue
        return fresh_count

    def delete(self, entry_id: str) -> bool:
        """Physically delete a memory entry by ID.

        Triggers FTS conditional deletion and cascades to memory_embeddings.
        Returns True if a record was deleted, False otherwise.
        """
        with self._conn:
            cursor = self._conn.cursor()
            cursor.execute("DELETE FROM memories WHERE id = ?;", (entry_id,))
            return cursor.rowcount > 0

    def search_fts(
        self,
        query: str,
        limit: int = 50,
        mode: str = "OR",
        candidate_ids: set[str] | list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Search contentless FTS5 index for matching eligible memories.

        When candidate_ids is provided, restricts the search to only those
        authorized IDs before applying BM25 ordering and LIMIT, guaranteeing
        that stale or expired indexed entries cannot crowd out valid candidates.

        Returns [(memory_id, bm25_score), ...] ordered by bm25 score ascending
        (most relevant first).
        """
        fts_query = build_fts_query(query, mode=mode)
        if not fts_query:
            return []

        cursor = self._conn.cursor()
        params: list[Any] = [fts_query]

        if candidate_ids is not None:
            c_list = list(candidate_ids)
            if not c_list:
                return []
            placeholders = ",".join("?" for _ in c_list)
            sql = f"""
                SELECT m.id, bm25(memory_fts)
                FROM memory_fts f
                JOIN memories m ON f.rowid = m.rowid
                WHERE memory_fts MATCH ?
                  AND m.id IN ({placeholders})
                ORDER BY bm25(memory_fts) ASC
                LIMIT ?;
            """
            params.extend(c_list)
        else:
            sql = """
                SELECT m.id, bm25(memory_fts)
                FROM memory_fts f
                JOIN memories m ON f.rowid = m.rowid
                WHERE memory_fts MATCH ?
                ORDER BY bm25(memory_fts) ASC
                LIMIT ?;
            """

        params.append(limit)
        cursor.execute(sql, tuple(params))
        return [(row[0], float(row[1])) for row in cursor.fetchall()]

    def count_fts(self) -> int:
        """Return total number of records currently indexed in memory_fts."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM memory_fts;")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def save_embedding(
        self,
        memory_id: str,
        model_fingerprint: str,
        model_name: str,
        model_revision: str,
        dimension: int,
        content_hash: str,
        embedding: list[float] | bytes,
        created_at: str | None = None,
        dtype: str = "float32",
    ) -> None:
        """Insert or replace a dense vector embedding for a memory entry."""
        if isinstance(embedding, list):
            blob = struct.pack(f"{len(embedding)}f", *embedding)
        else:
            blob = embedding

        if created_at is None:
            created_at = datetime.now(timezone.utc).isoformat()

        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memory_embeddings (
                    memory_id, model_fingerprint, model_name, model_revision,
                    dimension, dtype, content_hash, embedding, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    memory_id,
                    model_fingerprint,
                    model_name,
                    model_revision,
                    dimension,
                    dtype,
                    content_hash,
                    blob,
                    created_at,
                ),
            )

    def save_embeddings_batch(
        self,
        records: list[dict[str, Any]],
    ) -> None:
        """Batch insert or replace embeddings in a single atomic transaction."""
        if not records:
            return
        rows = []
        for r in records:
            emb = r["embedding"]
            blob = struct.pack(f"{len(emb)}f", *emb) if isinstance(emb, list) else emb
            rows.append((
                r["memory_id"],
                r["model_fingerprint"],
                r["model_name"],
                r.get("model_revision", ""),
                r["dimension"],
                r.get("dtype", "float32"),
                r["content_hash"],
                blob,
                r.get("created_at") or datetime.now(timezone.utc).isoformat(),
            ))
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO memory_embeddings (
                    memory_id, model_fingerprint, model_name, model_revision,
                    dimension, dtype, content_hash, embedding, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )

    def get_embedding(
        self,
        memory_id: str,
        model_fingerprint: str,
    ) -> dict[str, Any] | None:
        """Fetch cached embedding for a memory_id and model_fingerprint."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT memory_id, model_fingerprint, model_name, model_revision,
                   dimension, dtype, content_hash, embedding, created_at
            FROM memory_embeddings
            WHERE memory_id = ? AND model_fingerprint = ?;
            """,
            (memory_id, model_fingerprint),
        )
        row = cursor.fetchone()
        if not row:
            return None
        dim = row[4]
        blob = row[7]
        if dim <= 0 or len(blob) != dim * 4:
            raise ValueError(
                f"Corrupt embedding BLOB for memory '{row[0]}': expected {dim * 4} bytes for dim={dim}, got {len(blob)}"
            )
        vector = list(struct.unpack(f"{dim}f", blob))
        return {
            "memory_id": row[0],
            "model_fingerprint": row[1],
            "model_name": row[2],
            "model_revision": row[3],
            "dimension": dim,
            "dtype": row[5],
            "content_hash": row[6],
            "embedding": vector,
            "created_at": row[8],
        }

    def get_embeddings_for_model(
        self,
        model_fingerprint: str,
        memory_ids: list[str] | None = None,
    ) -> dict[str, tuple[str, list[float]]]:
        """Fetch embeddings for a model_fingerprint, returning {memory_id: (content_hash, vector)}."""
        cursor = self._conn.cursor()
        if memory_ids is not None:
            if not memory_ids:
                return {}
            placeholders = ",".join("?" for _ in memory_ids)
            cursor.execute(
                f"""
                SELECT memory_id, dimension, content_hash, embedding
                FROM memory_embeddings
                WHERE model_fingerprint = ? AND memory_id IN ({placeholders});
                """,
                (model_fingerprint, *memory_ids),
            )
        else:
            cursor.execute(
                """
                SELECT memory_id, dimension, content_hash, embedding
                FROM memory_embeddings
                WHERE model_fingerprint = ?;
                """,
                (model_fingerprint,),
            )
        results: dict[str, tuple[str, list[float]]] = {}
        for row in cursor.fetchall():
            mem_id = row[0]
            dim = row[1]
            content_hash = row[2]
            blob = row[3]
            if dim <= 0 or len(blob) != dim * 4:
                raise ValueError(
                    f"Corrupt embedding BLOB for memory '{mem_id}': expected {dim * 4} bytes for dim={dim}, got {len(blob)}"
                )
            vector = list(struct.unpack(f"{dim}f", blob))
            results[mem_id] = (content_hash, vector)
        return results

    def count_embeddings(self, model_fingerprint: str | None = None) -> int:
        """Return total number of cached embeddings, optionally filtered by model_fingerprint."""
        cursor = self._conn.cursor()
        if model_fingerprint is not None:
            cursor.execute(
                "SELECT COUNT(*) FROM memory_embeddings WHERE model_fingerprint = ?;",
                (model_fingerprint,),
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM memory_embeddings;")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        """Close SQLite connection."""
        self._conn.close()
