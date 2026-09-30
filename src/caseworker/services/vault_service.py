"""Application service managing Personal Context Vault operations, sources, facts, and access packaging."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from caseworker.domain.completeness import (
    CompletenessResult,
    ProfileCompletenessEvaluator,
    RequirementSet,
)
from caseworker.domain.context import ContextFact
from caseworker.domain.context_package import ContextPackage, ContextPackageBuilder
from caseworker.domain.enums import SensitivityLevel, SourceType, VerificationStatus
from caseworker.domain.errors import DomainValidationError, EntityNotFoundError
from caseworker.domain.events import (
    make_context_access_denied_event,
    make_context_access_granted_event,
    make_context_fact_created_event,
    make_context_fact_rejected_event,
    make_context_fact_superseded_event,
    make_context_fact_verified_event,
    make_context_source_registered_event,
)

from caseworker.domain.source import ContextSource
from caseworker.domain.vault_policy import ContextAccessPolicy

if TYPE_CHECKING:
    from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class ContextVaultService:
    """Application service orchestrating personal context with transactional state + event persistence."""

    def __init__(
        self,
        storage: SQLiteCaseworkerStorage,
        access_policy: ContextAccessPolicy | None = None,
    ) -> None:
        self.storage = storage
        self.access_policy = access_policy or ContextAccessPolicy()
        self.builder = ContextPackageBuilder(self.access_policy)

    # -------------------------------------------------------------------------
    # Context Sources
    # -------------------------------------------------------------------------

    def register_source(
        self,
        user_id: str,
        title: str,
        source_type: SourceType | str = SourceType.USER_INPUT,
        source_reference: str = "",
        content_hash: str = "",
        sensitivity: SensitivityLevel | str = SensitivityLevel.PERSONAL,
        metadata: dict[str, Any] | None = None,
        display_name: str | None = None,
    ) -> ContextSource:
        """Register a new context provenance source atomically with a domain event."""
        effective_title = display_name if display_name is not None else title
        with self.storage.unit_of_work() as uow:
            source = ContextSource(
                user_id=user_id,
                title=effective_title,
                source_type=source_type,
                source_reference=source_reference,
                content_hash=content_hash,
                sensitivity=sensitivity,
                metadata=metadata or {},
            )
            uow.sources.save(source)

            event = make_context_source_registered_event(
                source_id=source.source_id,
                user_id=source.user_id,
                aggregate_version=source.version,
                source_type=source.source_type.value,
                title=source.title,
            )
            uow.events.append(event)

        return source

    def get_source(self, source_id: str) -> ContextSource | None:
        """Retrieve a context source by its identifier."""
        with self.storage.unit_of_work() as uow:
            return uow.sources.get_by_id(source_id)

    def get_source_for_user(self, user_id: str, source_id: str) -> ContextSource | None:
        """Retrieve a context source by identifier, returning None if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            source = uow.sources.get_by_id(source_id)
            if source is None or source.user_id != user_id:
                return None
            return source

    def list_sources(self, user_id: str) -> list[ContextSource]:
        """List all context sources belonging to a user."""
        with self.storage.unit_of_work() as uow:
            return uow.sources.list_by_user(user_id)

    # -------------------------------------------------------------------------
    # Context Facts
    # -------------------------------------------------------------------------

    def record_fact(
        self,
        user_id: str,
        namespace: str,
        key: str,
        value: Any,
        source_id: str | None = None,
        source_type: SourceType | str = SourceType.USER_INPUT,
        source_reference: str = "",
        confidence: float = 1.0,
        sensitivity: SensitivityLevel | str = SensitivityLevel.PERSONAL,
        allowed_purposes: list[str] | None = None,
        verification_status: VerificationStatus | str = VerificationStatus.UNVERIFIED,
        expires_at: datetime | None = None,
    ) -> ContextFact:
        """Record a personal context fact, verifying source provenance and emitting created event."""
        with self.storage.unit_of_work() as uow:
            resolved_source_type = SourceType(source_type) if isinstance(source_type, str) else source_type

            if source_id is not None:
                source = uow.sources.get_by_id(source_id)
                if source is None:
                    raise EntityNotFoundError("ContextSource", source_id)
                if source.user_id != user_id:
                    raise DomainValidationError(
                        f"ContextSource '{source_id}' does not belong to user '{user_id}'."
                    )
                if source_type == SourceType.USER_INPUT and source.source_type != SourceType.USER_INPUT:
                    resolved_source_type = source.source_type

            # Rule: Agent inferences cannot self-verify upon ingestion
            resolved_status = (
                VerificationStatus(verification_status)
                if isinstance(verification_status, str)
                else verification_status
            )
            if resolved_source_type == SourceType.AGENT_INFERENCE:
                resolved_status = VerificationStatus.UNVERIFIED

            fact = ContextFact(
                user_id=user_id,
                namespace=namespace,
                key=key,
                value=value,
                source_id=source_id,
                source_type=resolved_source_type,
                source_reference=source_reference,
                confidence=confidence,
                sensitivity=sensitivity,
                allowed_purposes=list(allowed_purposes or []),
                verification_status=resolved_status,
                expires_at=expires_at,
            )
            uow.context.save(fact)

            event = make_context_fact_created_event(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                aggregate_version=fact.version,
                payload=fact.to_audit_payload(),
            )
            uow.events.append(event)


        return fact

    def supersede_fact(
        self,
        old_fact_id: str,
        new_value: Any,
        new_source_id: str | None = None,
        new_source_type: SourceType | str | None = None,
        new_source_reference: str = "",
        new_confidence: float = 1.0,
        sensitivity: SensitivityLevel | str | None = None,
        allowed_purposes: list[str] | None = None,
        verification_status: VerificationStatus | str = VerificationStatus.UNVERIFIED,
        expires_at: datetime | None = None,
        reason: str | None = None,
    ) -> tuple[ContextFact, ContextFact]:
        """Supersede an existing fact with a newer version atomically."""
        with self.storage.unit_of_work() as uow:
            old_fact = uow.context.get_by_id(old_fact_id)
            if old_fact is None:
                raise EntityNotFoundError("ContextFact", old_fact_id)

            if old_fact.is_superseded:
                raise DomainValidationError(
                    f"ContextFact '{old_fact_id}' has already been superseded by '{old_fact.superseded_by_fact_id}'."
                )

            target_source_id = new_source_id if new_source_id is not None else old_fact.source_id
            if target_source_id is not None:
                source = uow.sources.get_by_id(target_source_id)
                if source is None:
                    raise EntityNotFoundError("ContextSource", target_source_id)
                if source.user_id != old_fact.user_id:
                    raise DomainValidationError(
                        f"ContextSource '{target_source_id}' belongs to user '{source.user_id}', "
                        f"not user '{old_fact.user_id}'."
                    )

            target_source_type = (
                new_source_type
                if new_source_type is not None
                else (old_fact.source_type if target_source_id is None else source.source_type)
            )

            new_fact = ContextFact(
                user_id=old_fact.user_id,
                namespace=old_fact.namespace,
                key=old_fact.key,
                value=new_value,
                source_id=target_source_id,
                source_type=target_source_type,
                source_reference=new_source_reference or old_fact.source_reference,
                confidence=new_confidence,
                sensitivity=sensitivity if sensitivity is not None else old_fact.sensitivity,
                allowed_purposes=(
                    list(allowed_purposes)
                    if allowed_purposes is not None
                    else list(old_fact.allowed_purposes)
                ),
                verification_status=verification_status,
                expires_at=expires_at,
            )
            uow.context.save(new_fact)

            new_event = make_context_fact_created_event(
                fact_id=new_fact.fact_id,
                user_id=new_fact.user_id,
                aggregate_version=new_fact.version,
                payload=new_fact.to_audit_payload(),
            )
            uow.events.append(new_event)


            old_fact.supersede(new_fact.fact_id)
            uow.context.save(old_fact)

            superseded_event = make_context_fact_superseded_event(
                fact_id=old_fact.fact_id,
                user_id=old_fact.user_id,
                aggregate_version=old_fact.version,
                superseded_by_fact_id=new_fact.fact_id,
            )
            uow.events.append(superseded_event)

        return old_fact, new_fact

    def verify_fact(
        self,
        fact_id: str,
        status: VerificationStatus | str = VerificationStatus.USER_VERIFIED,
    ) -> ContextFact:
        """Verify a context fact, bumping its version and recording a domain event atomically."""
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(fact_id)
            if fact is None:
                raise EntityNotFoundError("ContextFact", fact_id)

            fact.verify(status)
            uow.context.save(fact)

            event = make_context_fact_verified_event(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                aggregate_version=fact.version,
                status=fact.verification_status.value,
            )
            uow.events.append(event)

        return fact

    def reject_fact(
        self,
        fact_id: str,
        reason: str | None = None,
    ) -> ContextFact:
        """Reject a context fact, deactivating it and recording a domain event atomically."""
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(fact_id)
            if fact is None:
                raise EntityNotFoundError("ContextFact", fact_id)

            fact.reject(reason)
            uow.context.save(fact)

            event = make_context_fact_rejected_event(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                aggregate_version=fact.version,
                reason=reason,
            )
            uow.events.append(event)

        return fact

    def get_fact(self, fact_id: str) -> ContextFact | None:
        """Retrieve a context fact by ID."""
        with self.storage.unit_of_work() as uow:
            return uow.context.get_by_id(fact_id)

    def get_fact_for_user(self, user_id: str, fact_id: str) -> ContextFact | None:
        """Retrieve a context fact by identifier, returning None if non-existent or owned by another user."""
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(fact_id)
            if fact is None or fact.user_id != user_id:
                return None
            return fact

    def verify_fact_for_user(
        self,
        user_id: str,
        fact_id: str,
        status: VerificationStatus | str = VerificationStatus.USER_VERIFIED,
    ) -> ContextFact:
        """Verify a context fact, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(fact_id)
            if fact is None or fact.user_id != user_id:
                raise EntityNotFoundError("ContextFact", fact_id)

            fact.verify(status)
            uow.context.save(fact)

            event = make_context_fact_verified_event(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                aggregate_version=fact.version,
                status=fact.verification_status.value,
            )
            uow.events.append(event)

        return fact

    def reject_fact_for_user(
        self,
        user_id: str,
        fact_id: str,
        reason: str | None = None,
    ) -> ContextFact:
        """Reject a context fact, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            fact = uow.context.get_by_id(fact_id)
            if fact is None or fact.user_id != user_id:
                raise EntityNotFoundError("ContextFact", fact_id)

            fact.reject(reason)
            uow.context.save(fact)

            event = make_context_fact_rejected_event(
                fact_id=fact.fact_id,
                user_id=fact.user_id,
                aggregate_version=fact.version,
                reason=reason,
            )
            uow.events.append(event)

        return fact

    def supersede_fact_for_user(
        self,
        user_id: str,
        old_fact_id: str,
        new_value: Any,
        new_source_id: str | None = None,
        new_source_type: SourceType | str | None = None,
        new_source_reference: str = "",
        new_confidence: float = 1.0,
        sensitivity: SensitivityLevel | str | None = None,
        allowed_purposes: list[str] | None = None,
        verification_status: VerificationStatus | str = VerificationStatus.UNVERIFIED,
        expires_at: datetime | None = None,
        reason: str | None = None,
    ) -> tuple[ContextFact, ContextFact]:
        """Supersede a context fact, asserting user ownership."""
        with self.storage.unit_of_work() as uow:
            old_fact = uow.context.get_by_id(old_fact_id)
            if old_fact is None or old_fact.user_id != user_id:
                raise EntityNotFoundError("ContextFact", old_fact_id)

        return self.supersede_fact(
            old_fact_id=old_fact_id,
            new_value=new_value,
            new_source_id=new_source_id,
            new_source_type=new_source_type,
            new_source_reference=new_source_reference,
            new_confidence=new_confidence,
            sensitivity=sensitivity,
            allowed_purposes=allowed_purposes,
            verification_status=verification_status,
            expires_at=expires_at,
            reason=reason,
        )

    def list_active_facts(self, user_id: str, namespace: str | None = None) -> list[ContextFact]:
        """List all active (unexpired, unsuperseded, unrejected) facts for a user."""
        with self.storage.unit_of_work() as uow:
            return uow.context.list_active(user_id=user_id, namespace=namespace)

    def list_history(self, user_id: str, namespace: str, key: str) -> list[ContextFact]:
        """List full revision history for a specific fact key."""
        with self.storage.unit_of_work() as uow:
            return uow.context.list_history(user_id=user_id, namespace=namespace, key=key)

    def list_by_namespace(self, user_id: str, namespace_prefix: str) -> list[ContextFact]:
        """List active facts matching a hierarchical namespace prefix (e.g. 'career.*')."""
        with self.storage.unit_of_work() as uow:
            return uow.context.list_by_namespace(user_id=user_id, namespace_prefix=namespace_prefix)

    def get_facts_by_source(self, source_id: str) -> list[ContextFact]:
        """List all facts derived from a specific context source."""
        with self.storage.unit_of_work() as uow:
            return uow.context.get_by_source_id(source_id)

    # -------------------------------------------------------------------------
    # Context Packages & Policy Gating
    # -------------------------------------------------------------------------

    def create_context_package(
        self,
        user_id: str,
        purpose: str,
        namespaces: list[str] | None = None,
        expires_at: datetime | None = None,
        require_verified: bool = False,
    ) -> ContextPackage:
        """Build a verifiable, purpose-scoped ContextPackage with comprehensive audit logging."""
        with self.storage.unit_of_work() as uow:
            all_facts = uow.context.list_active(user_id=user_id)
            pkg, granted_decisions, denied_decisions = self.builder.build_with_report(
                user_id=user_id,
                purpose=purpose,
                available_facts=all_facts,
                namespaces=namespaces,
                expires_at=expires_at,
                require_verified=require_verified,
            )

            current_version = uow.events.get_next_aggregate_version("context_vault", user_id)

            if pkg.facts:
                granted_event = make_context_access_granted_event(
                    user_id=user_id,
                    aggregate_version=current_version,
                    purpose=purpose,
                    fact_ids=[f.fact_id for f in pkg.facts],
                    namespaces=list(namespaces or []),
                )
                uow.events.append(granted_event)
                current_version += 1

            for decision in denied_decisions:
                denied_event = make_context_access_denied_event(
                    user_id=user_id,
                    aggregate_version=current_version,
                    purpose=purpose,
                    fact_id=decision.fact_id,
                    reason=decision.reason,
                    reason_code=decision.reason_code,
                    namespace=decision.namespace,
                    key=decision.key,
                )
                uow.events.append(denied_event)
                current_version += 1

        return pkg


    # -------------------------------------------------------------------------
    # Profile Completeness
    # -------------------------------------------------------------------------

    def evaluate_completeness(
        self,
        user_id: str,
        requirement_set: RequirementSet,
    ) -> CompletenessResult:
        """Evaluate user facts against a requirement set to determine profile readiness."""
        with self.storage.unit_of_work() as uow:
            facts = uow.context.list_active(user_id=user_id)
            return ProfileCompletenessEvaluator.evaluate(requirement_set, facts)
