"""Unit tests for ClaimAssertionSpec, ClaimAssertionValidator, and CanonicalClaimRenderer."""

import unittest
from datetime import datetime, timezone

from caseworker.domain.assertion import (
    CanonicalClaimRenderer,
    ClaimAssertionSpec,
    ClaimAssertionValidator,
)
from caseworker.domain.context import ContextFact
from caseworker.domain.enums import AssertionKind, VerificationStatus


class TestClaimAssertionSpec(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)
        self.skill_fact = ContextFact(
            fact_id="fact-skill-1",
            user_id="user-1",
            namespace="skills",
            key="python",
            value="Python and FastAPI",
            source_type="user_input",
            source_reference="resume",
            confidence=1.0,
            verification_status=VerificationStatus.USER_VERIFIED,
            sensitivity="personal",
            allowed_purposes=["job_application"],
            created_at=self.now,
            updated_at=self.now,
        )
        self.role_fact = ContextFact(
            fact_id="fact-role-1",
            user_id="user-1",
            namespace="experience",
            key="senior_engineer",
            value="Senior Software Engineer at Acme Corp",
            source_type="user_input",
            source_reference="resume",
            confidence=1.0,
            verification_status=VerificationStatus.USER_VERIFIED,
            sensitivity="personal",
            allowed_purposes=["job_application"],
            created_at=self.now,
            updated_at=self.now,
        )
        self.edu_fact = ContextFact(
            fact_id="fact-edu-1",
            user_id="user-1",
            namespace="education",
            key="bachelor_degree",
            value="B.S. in Computer Science",
            source_type="user_input",
            source_reference="diploma",
            confidence=1.0,
            verification_status=VerificationStatus.USER_VERIFIED,
            sensitivity="personal",
            allowed_purposes=["job_application"],
            created_at=self.now,
            updated_at=self.now,
        )
        self.lang_fact = ContextFact(
            fact_id="fact-lang-1",
            user_id="user-1",
            namespace="language",
            key="english",
            value="Fluent English C1 certified",
            source_type="user_input",
            source_reference="cert",
            confidence=1.0,
            verification_status=VerificationStatus.USER_VERIFIED,
            sensitivity="personal",
            allowed_purposes=["job_application"],
            created_at=self.now,
            updated_at=self.now,
        )
        self.auth_fact = ContextFact(
            fact_id="fact-auth-1",
            user_id="user-1",
            namespace="authorization",
            key="work_permit",
            value="Authorized citizen without sponsorship required",
            source_type="user_input",
            source_reference="passport",
            confidence=1.0,
            verification_status=VerificationStatus.USER_VERIFIED,
            sensitivity="personal",
            allowed_purposes=["job_application"],
            created_at=self.now,
            updated_at=self.now,
        )

    def test_skill_possession_validation_and_rendering(self) -> None:
        spec = ClaimAssertionSpec(
            kind=AssertionKind.SKILL_POSSESSION,
            fact_ids=["fact-skill-1"],
            fields={"skill": "Python"},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [self.skill_fact])
        self.assertTrue(valid, msg=err)
        self.assertIsNone(err)

        rendered = CanonicalClaimRenderer.render(spec)
        self.assertEqual(rendered, "Demonstrated production expertise in Python.")
        self.assertNotIn("[claim:", rendered)

    def test_skill_possession_rejection_when_unsubstantiated(self) -> None:
        spec = ClaimAssertionSpec(
            kind=AssertionKind.SKILL_POSSESSION,
            fact_ids=["fact-skill-1"],
            fields={"skill": "Quantum Physics"},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [self.skill_fact])
        self.assertFalse(valid)
        self.assertIn("not substantiated", err or "")

    def test_employment_role_validation_and_rendering(self) -> None:
        spec = ClaimAssertionSpec(
            kind=AssertionKind.EMPLOYMENT_ROLE,
            fact_ids=["fact-role-1"],
            fields={"role": "Senior Software Engineer", "organization": "Acme Corp"},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [self.role_fact])
        self.assertTrue(valid, msg=err)

        rendered = CanonicalClaimRenderer.render(spec)
        self.assertEqual(rendered, "Served as Senior Software Engineer at Acme Corp.")
        self.assertNotIn("[claim:", rendered)

    def test_employment_duration_validation(self) -> None:
        spec = ClaimAssertionSpec(
            kind=AssertionKind.EMPLOYMENT_DURATION,
            fact_ids=["fact-role-1"],
            fields={"duration_years": 5},
        )
        # role fact has no numbers, should fail duration
        valid, err = ClaimAssertionValidator.validate(spec, [self.role_fact])
        self.assertFalse(valid)

        # Fact with 7 years
        dur_fact = ContextFact(
            fact_id="fact-dur-1",
            user_id="user-1",
            namespace="experience",
            key="duration",
            value="7 years of distributed systems engineering",
            source_type="user_input",
            source_reference="resume",
            confidence=1.0,
            verification_status=VerificationStatus.USER_VERIFIED,
            sensitivity="personal",
            allowed_purposes=["job_application"],
            created_at=self.now,
            updated_at=self.now,
        )
        spec_dur = ClaimAssertionSpec(
            kind=AssertionKind.EMPLOYMENT_DURATION,
            fact_ids=["fact-dur-1"],
            fields={"duration_years": 5},
        )
        valid, err = ClaimAssertionValidator.validate(spec_dur, [dur_fact])
        self.assertTrue(valid, msg=err)
        rendered = CanonicalClaimRenderer.render(spec_dur)
        self.assertEqual(rendered, "Possesses 5+ years of professional engineering experience.")

    def test_education_credential_validation_and_rendering(self) -> None:
        spec = ClaimAssertionSpec(
            kind=AssertionKind.EDUCATION_CREDENTIAL,
            fact_ids=["fact-edu-1"],
            fields={"degree": "B.S.", "field_of_study": "Computer Science"},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [self.edu_fact])
        self.assertTrue(valid, msg=err)

        rendered = CanonicalClaimRenderer.render(spec)
        self.assertEqual(rendered, "Holds a verified B.S. in Computer Science.")

    def test_language_proficiency_cefr_validation(self) -> None:
        # Fact says C1 certified
        spec_c1 = ClaimAssertionSpec(
            kind=AssertionKind.LANGUAGE_PROFICIENCY,
            fact_ids=["fact-lang-1"],
            fields={"language": "English", "cefr_level": "C1"},
        )
        valid, err = ClaimAssertionValidator.validate(spec_c1, [self.lang_fact])
        self.assertTrue(valid, msg=err)

        # C2 should fail because evidence only substantiates C1
        spec_c2 = ClaimAssertionSpec(
            kind=AssertionKind.LANGUAGE_PROFICIENCY,
            fact_ids=["fact-lang-1"],
            fields={"language": "English", "cefr_level": "C2"},
        )
        valid, err = ClaimAssertionValidator.validate(spec_c2, [self.lang_fact])
        self.assertFalse(valid)
        self.assertIn("exceeds evidence level", err or "")

    def test_work_authorization_validation_and_rendering(self) -> None:
        spec = ClaimAssertionSpec(
            kind=AssertionKind.WORK_AUTHORIZATION,
            fact_ids=["fact-auth-1"],
            fields={"status": "Authorized citizen", "jurisdiction": "United States"},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [self.auth_fact])
        self.assertTrue(valid, msg=err)
        rendered = CanonicalClaimRenderer.render(spec)
        self.assertEqual(rendered, "Authorized to work in United States without employer sponsorship.")

    def test_quantified_achievement_validation(self) -> None:
        achieve_fact = ContextFact(
            fact_id="fact-achieve-1",
            user_id="user-1",
            namespace="experience",
            key="perf",
            value="Reduced latency by 45% across microservices",
            source_type="user_input",
            source_reference="metrics",
            confidence=1.0,
            verification_status=VerificationStatus.USER_VERIFIED,
            sensitivity="personal",
            allowed_purposes=["job_application"],
            created_at=self.now,
            updated_at=self.now,
        )
        spec = ClaimAssertionSpec(
            kind=AssertionKind.QUANTIFIED_ACHIEVEMENT,
            fact_ids=["fact-achieve-1"],
            fields={"metric": 45, "metric_unit": "%"},
        )
        valid, err = ClaimAssertionValidator.validate(spec, [achieve_fact])
        self.assertTrue(valid, msg=err)
        rendered = CanonicalClaimRenderer.render(spec)
        self.assertEqual(rendered, "Delivered 45 % measurable improvement in production system metrics.")

    def test_serialization_roundtrip(self) -> None:
        spec = ClaimAssertionSpec(
            kind=AssertionKind.SKILL_POSSESSION,
            fact_ids=["fact-1", "fact-2"],
            fields={"skill": "Distributed Systems"},
        )
        d = spec.to_dict()
        spec_rt = ClaimAssertionSpec.from_dict(d)
        self.assertEqual(spec.kind, spec_rt.kind)
        self.assertEqual(spec.fact_ids, spec_rt.fact_ids)
        self.assertEqual(spec.fields, spec_rt.fields)
