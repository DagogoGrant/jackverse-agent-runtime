"""MemoryManager facade for Phase 3A Persistent Long-Term Memory."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Any
import uuid

from harness.memory.base import (
    AdmissionAction,
    AdmissionDecision,
    MemoryEntry,
    MemorySource,
    MemoryStatus,
    MemoryStore,
    MemoryType,
    normalize_content,
)
from harness.memory.retrieval import MemoryRetriever
from harness.runtime.context import get_current_context
from harness.runtime.events import (
    FailureCategory,
    LifecycleEventBus,
    MemoryOperationEvent,
    MemoryOperationStatus,
    MemoryOperationType,
)

logger = logging.getLogger("harness.memory.manager")


class MemoryManager:
    """Coordinates admission, storage, and retrieval for persistent long-term memory."""

    def __init__(
        self,
        store: MemoryStore,
        admission_policy: Any,
        retriever: MemoryRetriever,
        enable_supersession: bool = True,
        indexer: Any | None = None,
        event_bus: LifecycleEventBus | None = None,
    ) -> None:
        self.store = store
        self.admission = admission_policy
        self.retriever = retriever
        self.enable_supersession = enable_supersession
        self.indexer = indexer
        self.event_bus = event_bus

    def admit_and_store(
        self,
        content: str,
        source: MemorySource,
        metadata: dict[str, Any] | None = None,
    ) -> AdmissionDecision:
        """Evaluate admission criteria and persist if admitted."""
        ctx = get_current_context()
        operation_id = uuid.uuid4().hex
        start_time = time.perf_counter()
        operation_type = MemoryOperationType.ADMIT
        status = MemoryOperationStatus.ERROR
        failure_category: FailureCategory | None = None
        error_type: str | None = None
        error_code: str | None = None
        error_message: str | None = None

        admission_event_published = False

        try:
            meta = dict(metadata or {})
            decision = self.admission.evaluate(content, source, meta)

            action = getattr(decision, "action", None)
            if action == AdmissionAction.REJECT or not decision.admitted:
                logger.debug(f"Memory rejected [{source.value}]: {decision.reason}")
                operation_type = MemoryOperationType.REJECT
                status = MemoryOperationStatus.REJECTED
                return decision

            entry_id = uuid.uuid4().hex
            created_at = datetime.now(timezone.utc).isoformat()
            entry_status = (
                MemoryStatus.QUARANTINED
                if action == AdmissionAction.QUARANTINE
                else MemoryStatus.ACCEPTED
            )
            norm = normalize_content(content.strip())
            merged_meta = {**meta, **getattr(decision, "metadata", {})}

            # Extract first-class lifecycle fields (one source of truth)
            memory_key = merged_meta.get("memory_key") if self.enable_supersession else None
            memory_value = merged_meta.get("memory_value") if self.enable_supersession else None
            expires_at = merged_meta.get("expires_at") if self.enable_supersession else None

            # Retain provenance-only fields in entry.metadata
            if self.enable_supersession:
                lifecycle_keys = ("memory_key", "memory_value", "expires_at", "supersedes_id", "superseded_by", "superseded_at")
            else:
                lifecycle_keys = ("supersedes_id", "superseded_by", "superseded_at")
            provenance_metadata = {
                k: v for k, v in merged_meta.items()
                if k not in lifecycle_keys
            }

            entry = MemoryEntry(
                id=entry_id,
                created_at=created_at,
                content=content.strip(),
                source=source,
                status=entry_status,
                normalized_content=norm,
                memory_key=memory_key,
                memory_value=memory_value,
                expires_at=expires_at,
                supersedes_id=None,
                superseded_by=None,
                superseded_at=None,
                metadata=provenance_metadata,
            )

            # Lifecycle state transition: if accepted and structured memory_key provided, atomically supersede
            if entry_status == MemoryStatus.ACCEPTED and memory_key and hasattr(self.store, "supersede_and_add"):
                superseded_id = self.store.supersede_and_add(entry, memory_key)
                if superseded_id is not None:
                    operation_type = MemoryOperationType.SUPERSEDE
                    logger.info(
                        f"Memory superseded [key={memory_key}]: previous={superseded_id} -> active={entry_id}"
                    )
                else:
                    operation_type = MemoryOperationType.ADMIT
                    logger.info(f"Memory admitted with structured key [id={entry_id}, key={memory_key}]")
                status = MemoryOperationStatus.SUCCESS
            else:
                self.store.add(entry)
                if entry_status == MemoryStatus.QUARANTINED:
                    operation_type = MemoryOperationType.QUARANTINE
                    status = MemoryOperationStatus.QUARANTINED
                    logger.warning(
                        f"Memory quarantined [id={entry_id}, source={source.value}]: {decision.reason}"
                    )
                else:
                    operation_type = MemoryOperationType.ADMIT
                    status = MemoryOperationStatus.SUCCESS
                    logger.info(f"Memory admitted and stored [id={entry_id}, source={source.value}]")

            # Publish authoritative persistence event (ADMIT / SUPERSEDE / QUARANTINE) before derived indexing
            if self.event_bus and ctx:
                duration = time.perf_counter() - start_time
                self.event_bus.publish(
                    MemoryOperationEvent(
                        timestamp=time.time(),
                        trace_id=ctx.trace_id,
                        run_id=ctx.run_id,
                        root_run_id=ctx.root_run_id,
                        parent_run_id=ctx.parent_run_id,
                        agent_id=ctx.agent_id,
                        agent_role=ctx.agent_role,
                        operation_id=operation_id,
                        operation_type=operation_type,
                        status=status,
                        duration_seconds=duration,
                        source=source.value,
                        entry_count=1,
                    )
                )
                admission_event_published = True

            # Derived dense indexing hook: embed newly committed accepted declarative memory
            if self.indexer is not None and entry_status == MemoryStatus.ACCEPTED and entry.memory_type == MemoryType.DECLARATIVE:
                idx_start = time.perf_counter()
                idx_op_id = uuid.uuid4().hex
                try:
                    self.indexer.ensure_embeddings([entry])
                    if self.event_bus and ctx:
                        idx_duration = time.perf_counter() - idx_start
                        self.event_bus.publish(
                            MemoryOperationEvent(
                                timestamp=time.time(),
                                trace_id=ctx.trace_id,
                                run_id=ctx.run_id,
                                root_run_id=ctx.root_run_id,
                                parent_run_id=ctx.parent_run_id,
                                agent_id=ctx.agent_id,
                                agent_role=ctx.agent_role,
                                operation_id=idx_op_id,
                                operation_type=MemoryOperationType.INDEX,
                                status=MemoryOperationStatus.SUCCESS,
                                duration_seconds=idx_duration,
                                source=source.value,
                                entry_count=1,
                            )
                        )
                except Exception as exc:
                    logger.warning(
                        f"Incremental embedding failed for memory '{entry_id}': {exc}. "
                        "Memory remains persisted in SQLite and retrievable via BM25."
                    )
                    if self.event_bus and ctx:
                        idx_duration = time.perf_counter() - idx_start
                        self.event_bus.publish(
                            MemoryOperationEvent(
                                timestamp=time.time(),
                                trace_id=ctx.trace_id,
                                run_id=ctx.run_id,
                                root_run_id=ctx.root_run_id,
                                parent_run_id=ctx.parent_run_id,
                                agent_id=ctx.agent_id,
                                agent_role=ctx.agent_role,
                                operation_id=idx_op_id,
                                operation_type=MemoryOperationType.INDEX,
                                status=MemoryOperationStatus.ERROR,
                                duration_seconds=idx_duration,
                                source=source.value,
                                entry_count=1,
                                failure_category=FailureCategory.INTERNAL,
                                error_type=type(exc).__name__,
                                error_message=str(exc)[:200],
                            )
                        )

            return decision
        except Exception as exc:
            status = MemoryOperationStatus.ERROR
            error_type = type(exc).__name__
            error_message = str(exc)[:200]
            failure_category = FailureCategory.INTERNAL
            raise
        finally:
            if self.event_bus and ctx and not admission_event_published:
                duration = time.perf_counter() - start_time
                self.event_bus.publish(
                    MemoryOperationEvent(
                        timestamp=time.time(),
                        trace_id=ctx.trace_id,
                        run_id=ctx.run_id,
                        root_run_id=ctx.root_run_id,
                        parent_run_id=ctx.parent_run_id,
                        agent_id=ctx.agent_id,
                        agent_role=ctx.agent_role,
                        operation_id=operation_id,
                        operation_type=operation_type,
                        status=status,
                        duration_seconds=duration,
                        source=source.value,
                        entry_count=1,
                        failure_category=failure_category,
                        error_type=error_type,
                        error_code=error_code,
                        error_message=error_message,
                    )
                )

    def retrieve(
        self,
        query: str,
        limit: int | None = None,
        now: datetime | str | None = None,
    ) -> list[MemoryEntry]:
        """Retrieve top relevant memories matching the query, evaluated at given timestamp."""
        ctx = get_current_context()
        operation_id = uuid.uuid4().hex
        start_time = time.perf_counter()
        status = MemoryOperationStatus.ERROR
        entry_count = 0
        failure_category: FailureCategory | None = None
        error_type: str | None = None
        error_code: str | None = None
        error_message: str | None = None

        try:
            results = self.retriever.retrieve(query, limit=limit, now=now)
            entry_count = len(results)
            status = MemoryOperationStatus.SUCCESS
            return results
        except Exception as exc:
            status = MemoryOperationStatus.ERROR
            error_type = type(exc).__name__
            error_message = str(exc)[:200]
            failure_category = FailureCategory.INTERNAL
            raise
        finally:
            if self.event_bus and ctx:
                duration = time.perf_counter() - start_time
                self.event_bus.publish(
                    MemoryOperationEvent(
                        timestamp=time.time(),
                        trace_id=ctx.trace_id,
                        run_id=ctx.run_id,
                        root_run_id=ctx.root_run_id,
                        parent_run_id=ctx.parent_run_id,
                        agent_id=ctx.agent_id,
                        agent_role=ctx.agent_role,
                        operation_id=operation_id,
                        operation_type=MemoryOperationType.RETRIEVE,
                        status=status,
                        duration_seconds=duration,
                        source=None,
                        entry_count=entry_count,
                        failure_category=failure_category,
                        error_type=error_type,
                        error_code=error_code,
                        error_message=error_message,
                    )
                )

    def format_context(self, memories: list[MemoryEntry]) -> str:
        """Format a list of retrieved memories into a bounded context string."""
        if not memories:
            return ""

        lines = ["[RECALLED MEMORY]"]
        for m in memories:
            lines.append(f"- ({m.created_at}) {m.content}")
        return "\n".join(lines)

    def admit_procedural_lesson(self, lesson: Any) -> AdmissionDecision:
        """Admit and atomically supersede any existing procedural lesson for the same parameter."""
        entry_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        memory_key = f"procedural:{lesson.tool_name}:{lesson.failing_parameter}"
        norm = normalize_content(lesson.lesson)

        from harness.memory.base import MemoryType

        entry = MemoryEntry(
            id=entry_id,
            created_at=created_at,
            content=lesson.lesson,
            source=MemorySource.TOOL_OBSERVATION,
            status=MemoryStatus.ACCEPTED,
            memory_type=MemoryType.PROCEDURAL,
            normalized_content=norm,
            memory_key=memory_key,
            memory_value=lesson.constraint,
            expires_at=None,
            supersedes_id=None,
            superseded_by=None,
            superseded_at=None,
            metadata={
                "tool_name": lesson.tool_name,
                "failing_parameter": lesson.failing_parameter,
                "constraint": lesson.constraint,
                **lesson.provenance,
            },
        )

        if hasattr(self.store, "supersede_and_add"):
            self.store.supersede_and_add(entry, memory_key)
        else:
            self.store.add(entry)

        logger.info(f"Procedural lesson persisted [id={entry_id}, key={memory_key}]: {lesson.lesson}")
        return AdmissionDecision(
            action=AdmissionAction.ACCEPT,
            admitted=True,
            reason="Procedural recovery lesson admitted.",
        )

    def retrieve_procedural(
        self,
        tool_names: list[str],
        limit_per_tool: int = 2,
        max_chars: int = 400,
    ) -> list[MemoryEntry]:
        """Fetch active procedural recovery lessons for available tools, bounded by character budget."""
        if not hasattr(self.store, "list_procedural"):
            return []

        candidates = self.store.list_procedural(tool_names, limit_per_tool=limit_per_tool)
        selected: list[MemoryEntry] = []
        total_chars = 0

        for entry in candidates:
            # Budget check: skip entry if adding it would exceed max_chars
            entry_len = len(entry.content) + 4
            if total_chars + entry_len > max_chars:
                continue
            selected.append(entry)
            total_chars += entry_len

        return selected

    def format_procedural_context(self, lessons: list[MemoryEntry]) -> str:
        """Format a list of retrieved procedural lessons into a bounded guidance block."""
        if not lessons:
            return ""

        lines = ["[PAST TOOL EXPERIENCE]"]
        for lesson in lessons:
            lines.append(f"- {lesson.content}")
        return "\n".join(lines)

    def close(self) -> None:
        """Close storage resources."""
        self.store.close()
