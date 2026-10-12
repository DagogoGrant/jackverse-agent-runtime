"""Unit tests for RequirementMatcher unified matching primitive."""

import unittest

from caseworker.domain.context import ContextFact
from caseworker.domain.enums import (
    RequirementCategory,
    RequirementMatchResult,
    VerificationStatus,
)
from caseworker.domain.normalized_requirement import NormalizedRequirement
from caseworker.domain.requirement_matcher import (
    RequirementMatcher,
    extract_cefr_level,
    is_fact_verified,
)


class TestRequirementMatcher(unittest.TestCase):
    def test_extract_cefr_level(self) -> None:
        self.assertEqual(extract_cefr_level("German B2"), 4)
        self.assertEqual(extract_cefr_level("fluent in English (C1)"), 5)
        self.assertEqual(extract_cefr_level("Mastery (C2)"), 6)
        self.assertEqual(extract_cefr_level("basic a1 knowledge"), 1)
        self.assertIsNone(extract_cefr_level("No level mentioned"))
        self.assertIsNone(extract_cefr_level(""))

    def test_is_fact_verified(self) -> None:
        self.assertTrue(is_fact_verified(VerificationStatus.USER_VERIFIED))
        self.assertTrue(is_fact_verified(VerificationStatus.SOURCE_VERIFIED))
        self.assertTrue(is_fact_verified("user_verified"))
        self.assertTrue(is_fact_verified("source_verified"))
        self.assertFalse(is_fact_verified(VerificationStatus.UNVERIFIED))
        self.assertFalse(is_fact_verified(VerificationStatus.REJECTED))
        self.assertFalse(is_fact_verified(None))

    def test_skill_matching(self) -> None:
        req = NormalizedRequirement(
            requirement_id="req-1",
            raw_text="Python programming",
            category=RequirementCategory.MANDATORY,
            criterion="skill",
            value="Python",
        )
        fact = ContextFact(
            fact_id="f-1",
            user_id="user-1",
            namespace="skills",
            key="programming",
            value="Advanced Python developer",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        facts_by_ns = {"skills": [fact]}
        res, matched, notes = RequirementMatcher.match(req, facts_by_ns, [fact])
        self.assertEqual(res, RequirementMatchResult.MET)
        self.assertEqual(matched, fact)

    def test_years_experience_threshold(self) -> None:
        req = NormalizedRequirement(
            requirement_id="req-exp",
            raw_text="5+ years experience",
            category=RequirementCategory.MANDATORY,
            criterion="years_experience",
            value=5,
        )
        fact_enough = ContextFact(
            fact_id="f-exp1",
            user_id="u1",
            namespace="experience",
            key="years_experience",
            value="6 years in software",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        res, matched, _ = RequirementMatcher.match(req, {"experience": [fact_enough]}, [fact_enough])
        self.assertEqual(res, RequirementMatchResult.MET)

        fact_insufficient = ContextFact(
            fact_id="f-exp2",
            user_id="u1",
            namespace="experience",
            key="years_experience",
            value="3 years",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        res, matched, _ = RequirementMatcher.match(req, {"experience": [fact_insufficient]}, [fact_insufficient])
        self.assertEqual(res, RequirementMatchResult.NOT_MET)

    def test_language_cefr_hierarchy(self) -> None:
        req = NormalizedRequirement(
            requirement_id="req-lang",
            raw_text="German B2 required",
            category=RequirementCategory.MANDATORY,
            criterion="language",
            value="German B2",
        )
        # Higher level meets requirement
        fact_c1 = ContextFact(
            fact_id="f-lang1",
            user_id="u1",
            namespace="languages",
            key="german",
            value="C1 Advanced",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        res, matched, _ = RequirementMatcher.match(req, {"languages": [fact_c1]}, [fact_c1])
        self.assertEqual(res, RequirementMatchResult.MET)

        # Lower level is contradicted (NOT_MET)
        fact_a2 = ContextFact(
            fact_id="f-lang2",
            user_id="u1",
            namespace="languages",
            key="german",
            value="A2 Elementary",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        res, matched, _ = RequirementMatcher.match(req, {"languages": [fact_a2]}, [fact_a2])
        self.assertEqual(res, RequirementMatchResult.NOT_MET)

        # Unknown level remains UNKNOWN
        fact_unk = ContextFact(
            fact_id="f-lang3",
            user_id="u1",
            namespace="languages",
            key="german",
            value="Some conversational German",
            verification_status=VerificationStatus.USER_VERIFIED,
        )
        res, matched, _ = RequirementMatcher.match(req, {"languages": [fact_unk]}, [fact_unk])
        self.assertEqual(res, RequirementMatchResult.UNKNOWN)

    def test_mandatory_unverified_guard(self) -> None:
        req_mandatory = NormalizedRequirement(
            requirement_id="req-m",
            raw_text="Docker proficiency",
            category=RequirementCategory.MANDATORY,
            criterion="skill",
            value="Docker",
        )
        fact_unverified = ContextFact(
            fact_id="f-unver",
            user_id="u1",
            namespace="skills",
            key="docker",
            value="Docker containers",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        res, matched, notes = RequirementMatcher.match(
            req_mandatory, {"skills": [fact_unverified]}, [fact_unverified]
        )
        # Mandatory requirement CANNOT be satisfied (MET) by unverified fact -> UNKNOWN
        self.assertEqual(res, RequirementMatchResult.UNKNOWN)
        self.assertEqual(matched, fact_unverified)
        self.assertIn("mandatory requirement requires verified evidence", notes)

        # Non-mandatory requirement can be MET even if unverified
        req_preferred = NormalizedRequirement(
            requirement_id="req-p",
            raw_text="Kubernetes experience",
            category=RequirementCategory.PREFERRED,
            criterion="skill",
            value="Kubernetes",
        )
        fact_k8s = ContextFact(
            fact_id="f-k8s",
            user_id="u1",
            namespace="skills",
            key="kubernetes",
            value="Kubernetes clusters",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        res_p, matched_p, _ = RequirementMatcher.match(
            req_preferred, {"skills": [fact_k8s]}, [fact_k8s]
        )
        self.assertEqual(res_p, RequirementMatchResult.MET)
        self.assertEqual(matched_p, fact_k8s)


if __name__ == "__main__":
    unittest.main()
