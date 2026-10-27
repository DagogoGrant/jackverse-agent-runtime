"""Server-enforced policy and verification for controlled internal self-tests."""

from __future__ import annotations

from typing import Any

from caseworker.domain.enums import PreparationPackageStatus
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.types import compute_sha256

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
SELF_TEST_SYSTEM_VERIFIER = f"system:oauth_mailbox_attestation:{CONTROLLED_TEST_OPERATOR_ID}"


class ServerEnforcedSelfTestPolicy:
    """Strictly confined, server-enforced validation for internal self-addressed integration tests.

    Constitutional boundaries:
    - Never acts as employer email-application acceptance.
    - Confined exclusively to known, operator-owned controlled test Case and Opportunity.
    - Destination must match connected operator OAuth identity character-for-character.
    - Verified by exact trusted system identity, backed independently by stored OAuth credential.
    - Identity and destination validation remain independent of runtime execution flags.
    - Cannot be reused for any external employer or other Case/Opportunity.
    """

    @classmethod
    def validate(
        cls,
        case: Any,
        opportunity: Any,
        package: Any,
        evidence: Any,
        clean_dest_email: str,
        oauth_credential: Any = None,
        sender_email: str | None = None,
    ) -> None:
        """Validate all cryptographic, identity, and routing invariants of a self-test attestation."""
        # 1. Case & Opportunity Pinning (Amendment 1: use opportunity.converted_case_id)
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

        opp_conv_case_id = getattr(opportunity, "converted_case_id", None)
        if opp_conv_case_id != case_id:
            raise DomainValidationError(
                f"Opportunity converted_case_id '{opp_conv_case_id}' does not match Case '{case_id}'."
            )

        user_id = getattr(case, "user_id", None)
        if user_id != CONTROLLED_TEST_OPERATOR_ID:
            raise DomainValidationError(
                f"Self-test policy is strictly locked to Operator '{CONTROLLED_TEST_OPERATOR_ID}' (got '{user_id}')."
            )

        opp_user_id = getattr(opportunity, "user_id", None)
        if opp_user_id != user_id:
            raise DomainValidationError(
                f"Opportunity owner '{opp_user_id}' does not match Case owner '{user_id}'."
            )

        # 2. Preparation Package Pinning & Status
        pkg_id = getattr(package, "package_id", None)
        if pkg_id != CONTROLLED_TEST_PACKAGE_ID:
            raise DomainValidationError(
                f"Self-test policy is strictly locked to Package '{CONTROLLED_TEST_PACKAGE_ID}' (got '{pkg_id}')."
            )

        pkg_case_id = getattr(package, "case_id", None)
        if pkg_case_id != case_id:
            raise DomainValidationError(
                f"Preparation package case_id '{pkg_case_id}' does not match Case '{case_id}'."
            )

        pkg_user_id = getattr(package, "user_id", None)
        if pkg_user_id != user_id:
            raise DomainValidationError(
                f"Preparation package user_id '{pkg_user_id}' does not match Case owner '{user_id}'."
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

        # 3. Independent OAuth Mailbox Verification (Amendment 2)
        if oauth_credential is None:
            raise DomainValidationError(
                "Self-test policy requires authenticated Google OAuth credential for independent verification."
            )

        cred_user_id = getattr(oauth_credential, "user_id", None)
        if cred_user_id != user_id:
            raise DomainValidationError(
                f"Connected OAuth credential owner '{cred_user_id}' does not match Case owner '{user_id}'."
            )

        cred_provider = getattr(oauth_credential, "provider", None)
        if cred_provider != "gmail":
            raise DomainValidationError(
                f"Connected OAuth credential provider must be 'gmail' (got '{cred_provider}')."
            )

        cred_email = getattr(oauth_credential, "authenticated_email", "") or ""
        cred_email_clean = cred_email.strip().lower()
        if cred_email_clean != CONTROLLED_TEST_EMAIL:
            raise DomainValidationError(
                f"Connected OAuth authenticated email '{cred_email_clean}' does not match authorized operator email '{CONTROLLED_TEST_EMAIL}'."
            )

        cred_scopes = getattr(oauth_credential, "scopes", []) or []
        if not any("gmail.send" in s for s in cred_scopes):
            raise DomainValidationError(
                "Connected OAuth credential lacks required 'https://www.googleapis.com/auth/gmail.send' scope."
            )

        # 4. Strict Self-Addressed Recipient & Sender Routing
        clean_dest = clean_dest_email.strip().lower()
        if clean_dest != CONTROLLED_TEST_EMAIL:
            raise DomainValidationError(
                f"Self-test destination '{clean_dest}' does not match authorized operator mailbox '{CONTROLLED_TEST_EMAIL}'."
            )

        if clean_dest != cred_email_clean:
            raise DomainValidationError(
                f"Self-test destination '{clean_dest}' does not match connected OAuth email '{cred_email_clean}'."
            )

        recip = getattr(evidence, "verified_recipient_email", "").strip().lower()
        if recip != CONTROLLED_TEST_EMAIL:
            raise DomainValidationError(
                f"Self-test verified recipient '{recip}' does not match authorized operator mailbox '{CONTROLLED_TEST_EMAIL}'."
            )

        if sender_email is not None:
            clean_sender = sender_email.strip().lower()
            if clean_sender != CONTROLLED_TEST_EMAIL:
                raise DomainValidationError(
                    f"Self-test sender email '{clean_sender}' does not match authorized operator mailbox '{CONTROLLED_TEST_EMAIL}'."
                )

        # 5. Attestation Metadata & Exact System Verifier Identity (Amendment 2)
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
        if verified_by != SELF_TEST_SYSTEM_VERIFIER:
            raise DomainValidationError(
                f"Self-test verifier authority mismatch: expected exact '{SELF_TEST_SYSTEM_VERIFIER}', got '{verified_by}'."
            )

        instructions = getattr(evidence, "instructions_text", "")
        if not instructions or CONTROLLED_TEST_EMAIL not in instructions.lower():
            raise DomainValidationError(
                f"Self-test instructions must cite destination email '{CONTROLLED_TEST_EMAIL}'."
            )

        expected_snap = compute_sha256(instructions.strip())
        snap_hash = getattr(evidence, "evidence_snapshot_hash", "") or getattr(evidence, "snapshot_hash", "")
        if snap_hash and snap_hash.lower() != expected_snap.lower():
            raise DomainValidationError("Self-test evidence snapshot hash does not match instructions text.")

        if hasattr(evidence, "compute_digest"):
            expected_digest = evidence.compute_digest()
            ev_digest = getattr(evidence, "evidence_digest", "")
            if ev_digest and ev_digest.lower() != expected_digest.lower():
                raise DomainValidationError("Self-test evidence digest does not match verified parameters.")
