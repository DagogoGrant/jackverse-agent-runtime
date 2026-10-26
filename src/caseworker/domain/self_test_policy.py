"""Server-enforced policy and verification for controlled internal self-tests."""

from __future__ import annotations

import os
from typing import Any

from caseworker.domain.enums import PreparationPackageStatus
from caseworker.domain.errors import DomainValidationError

# Immutable boundaries for Phase 5.3.3.2 Controlled Integration Test
CONTROLLED_TEST_CASE_ID = "case-controlled-test-5332"
CONTROLLED_TEST_OPP_ID = "opp-controlled-test-5332"
CONTROLLED_TEST_OPERATOR_ID = "operator-1"
CONTROLLED_TEST_PACKAGE_ID = "pkg_d17af0209e42"
CONTROLLED_TEST_PACKAGE_FP = "1ebfd0bbaf5c371b089d6a412fb8fc0115b9c37bab834fb7845d9f92846dea53"
CONTROLLED_TEST_EMAIL = "dagogograntjack@gmail.com"
SELF_TEST_EVIDENCE_SOURCE = "operator_self_test_v1"
SELF_TEST_EMPLOYER_DOMAIN = "jackverse.internal"
SELF_TEST_SOURCE_URI_PREFIX = "urn:jackverse:controlled-test:"


class ServerEnforcedSelfTestPolicy:
    """Strictly confined, server-enforced validation for internal self-addressed integration tests.

    Constitutional boundaries:
    - Never acts as employer email-application acceptance.
    - Confined exclusively to known, operator-owned controlled test Case and Opportunity.
    - Destination must match connected operator OAuth identity character-for-character.
    - Fails closed if execution flags are enabled.
    - Cannot be reused for any real employer application.
    """

    @classmethod
    def validate(
        cls,
        case: Any,
        opportunity: Any,
        package: Any,
        evidence: Any,
        clean_dest_email: str,
    ) -> None:
        """Validate all cryptographic, identity, and routing invariants of a self-test attestation."""
        # 1. Safety flags check
        real_exec = os.getenv("CASEWORKER_REAL_EXECUTION_ENABLED", "false").lower() == "true"
        gmail_exec = os.getenv("CASEWORKER_GMAIL_EXECUTION_ENABLED", "false").lower() == "true"
        if real_exec or gmail_exec:
            raise RuntimeError(
                "SAFETY VIOLATION: Execution flags must remain disabled during self-test preparation."
            )

        # 2. Case & Opportunity Pinning
        case_id = getattr(case, "case_id", None)
        if case_id != CONTROLLED_TEST_CASE_ID:
            raise DomainValidationError(
                f"Self-test policy is strictly locked to Case '{CONTROLLED_TEST_CASE_ID}' (got '{case_id}')."
            )

        opp_id = getattr(opportunity, "opportunity_id", None)
        if opp_id != CONTROLLED_TEST_OPP_ID:
            raise DomainValidationError(
                f"Self-test policy is strictly locked to Opportunity '{CONTROLLED_TEST_OPP_ID}' (got '{opp_id}')."
            )

        user_id = getattr(case, "user_id", None)
        if user_id != CONTROLLED_TEST_OPERATOR_ID:
            raise DomainValidationError(
                f"Self-test policy is strictly locked to Operator '{CONTROLLED_TEST_OPERATOR_ID}' (got '{user_id}')."
            )

        # 3. Preparation Package Pinning & Status
        pkg_id = getattr(package, "package_id", None)
        if pkg_id != CONTROLLED_TEST_PACKAGE_ID:
            raise DomainValidationError(
                f"Self-test policy is strictly locked to Package '{CONTROLLED_TEST_PACKAGE_ID}' (got '{pkg_id}')."
            )

        pkg_fp = getattr(package, "package_fingerprint", None)
        if pkg_fp != CONTROLLED_TEST_PACKAGE_FP:
            raise DomainValidationError(
                "Self-test policy: package fingerprint mismatch."
            )

        pkg_status = getattr(package, "status", None)
        status_val = pkg_status.value if hasattr(pkg_status, "value") else str(pkg_status)
        if status_val != PreparationPackageStatus.APPROVED.value:
            raise DomainValidationError(
                f"Self-test package must be APPROVED (got '{status_val}')."
            )

        # 4. Strict Self-Addressed Recipient Locking
        if clean_dest_email.strip().lower() != CONTROLLED_TEST_EMAIL:
            raise DomainValidationError(
                f"Self-test destination '{clean_dest_email}' does not match authorized operator mailbox '{CONTROLLED_TEST_EMAIL}'."
            )

        recip = getattr(evidence, "verified_recipient_email", "").strip().lower()
        if recip != CONTROLLED_TEST_EMAIL:
            raise DomainValidationError(
                f"Self-test verified recipient '{recip}' does not match authorized operator mailbox '{CONTROLLED_TEST_EMAIL}'."
            )

        # 5. Attestation Metadata & Provenance Invariants
        ev_source = getattr(evidence, "evidence_source", None)
        if ev_source != SELF_TEST_EVIDENCE_SOURCE:
            raise DomainValidationError(
                f"Self-test evidence_source must be '{SELF_TEST_EVIDENCE_SOURCE}' (got '{ev_source}')."
            )

        expected_uri = f"{SELF_TEST_SOURCE_URI_PREFIX}{CONTROLLED_TEST_OPERATOR_ID}"
        source_uri = getattr(evidence, "source_uri", None)
        if source_uri != expected_uri:
            raise DomainValidationError(
                f"Self-test source_uri mismatch (expected '{expected_uri}', got '{source_uri}')."
            )

        emp_domain = getattr(evidence, "employer_domain", None)
        if emp_domain != SELF_TEST_EMPLOYER_DOMAIN:
            raise DomainValidationError(
                f"Self-test employer_domain must be '{SELF_TEST_EMPLOYER_DOMAIN}' (got '{emp_domain}'). "
                f"Self-test attestation cannot be attributed to an external employer."
            )

        verified_by = getattr(evidence, "verified_by", "")
        if not (
            verified_by.startswith(f"system:self_test:{CONTROLLED_TEST_OPERATOR_ID}")
            or verified_by.startswith(f"operator:{CONTROLLED_TEST_OPERATOR_ID}")
        ):
            raise DomainValidationError(
                f"Self-test verifier authority mismatch: '{verified_by}'."
            )

        instructions = getattr(evidence, "instructions_text", "")
        if not instructions or CONTROLLED_TEST_EMAIL not in instructions.lower():
            raise DomainValidationError(
                f"Self-test instructions must cite destination email '{CONTROLLED_TEST_EMAIL}'."
            )
