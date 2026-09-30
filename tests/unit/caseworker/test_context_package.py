"""Unit tests for ContextPackage and ContextPackageBuilder cryptographic packaging."""

import unittest

from caseworker.domain.context import ContextFact
from caseworker.domain.context_package import ContextPackage, ContextPackageBuilder
from caseworker.domain.enums import SensitivityLevel, VerificationStatus


class TestContextPackage(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = ContextPackageBuilder()

    def test_package_fingerprint_deterministic(self) -> None:
        f1 = ContextFact(fact_id="f1", user_id="u1", namespace="identity", key="name", value="Alice")
        f2 = ContextFact(fact_id="f2", user_id="u1", namespace="skills", key="python", value="Expert")

        pkg1 = ContextPackage(package_id="pkg_fixed", user_id="u1", purpose="job", facts=[f1, f2])
        pkg2 = ContextPackage(package_id="pkg_fixed", user_id="u1", purpose="job", facts=[f2, f1])  # different input order

        # Fingerprint must be identical because facts are sorted deterministically
        self.assertEqual(pkg1.fingerprint, pkg2.fingerprint)
        self.assertEqual(len(pkg1.fingerprint), 64)

    def test_package_tamper_detection(self) -> None:
        f1 = ContextFact(fact_id="f1", user_id="u1", namespace="identity", key="name", value="Alice")
        pkg1 = ContextPackage(package_id="pkg_fixed", user_id="u1", purpose="job", facts=[f1])

        f1_tampered = ContextFact(fact_id="f1", user_id="u1", namespace="identity", key="name", value="Bob")
        pkg2 = ContextPackage(package_id="pkg_fixed", user_id="u1", purpose="job", facts=[f1_tampered])

        self.assertNotEqual(pkg1.fingerprint, pkg2.fingerprint)

    def test_builder_namespace_filtering(self) -> None:
        facts = [
            ContextFact(fact_id="f1", user_id="u1", namespace="career.experience", key="title", value="Engineer"),
            ContextFact(fact_id="f2", user_id="u1", namespace="career.skills", key="python", value="Expert"),
            ContextFact(fact_id="f3", user_id="u1", namespace="housing.budget", key="max_rent", value=1500),
        ]
        pkg = self.builder.build(
            user_id="u1",
            purpose="job_application",
            available_facts=facts,
            namespaces=["career.*"],
        )
        self.assertEqual(len(pkg.facts), 2)
        fact_keys = [f.key for f in pkg.facts]
        self.assertIn("title", fact_keys)
        self.assertIn("python", fact_keys)
        self.assertNotIn("max_rent", fact_keys)

    def test_builder_purpose_gating(self) -> None:
        facts = [
            ContextFact(
                fact_id="f1",
                user_id="u1",
                namespace="skills",
                key="python",
                value="Expert",
                allowed_purposes=["job_application"],
            ),
            ContextFact(
                fact_id="f2",
                user_id="u1",
                namespace="housing",
                key="city",
                value="Munich",
                allowed_purposes=["housing_search"],
            ),
        ]
        pkg = self.builder.build(
            user_id="u1",
            purpose="job_application",
            available_facts=facts,
        )
        self.assertEqual(len(pkg.facts), 1)
        self.assertEqual(pkg.facts[0].key, "python")

    def test_builder_require_verified_filter(self) -> None:
        facts = [
            ContextFact(
                fact_id="f1",
                user_id="u1",
                namespace="skills",
                key="python",
                value="Expert",
                verification_status=VerificationStatus.UNVERIFIED,
            ),
            ContextFact(
                fact_id="f2",
                user_id="u1",
                namespace="education",
                key="degree",
                value="MSc",
                verification_status=VerificationStatus.USER_VERIFIED,
            ),
        ]
        pkg = self.builder.build(
            user_id="u1",
            purpose="job_application",
            available_facts=facts,
            require_verified=True,
        )
        self.assertEqual(len(pkg.facts), 1)
        self.assertEqual(pkg.facts[0].key, "degree")

    def test_safe_dict_redaction(self) -> None:
        fact = ContextFact(
            fact_id="f1",
            user_id="u1",
            namespace="identity",
            key="tax_id",
            value="SECRET-999",
            sensitivity=SensitivityLevel.SENSITIVE,
            allowed_purposes=["tax_filing"],
        )
        pkg = ContextPackage(
            user_id="u1",
            purpose="tax_filing",
            facts=[fact],
        )
        safe_repr = pkg.to_safe_dict()
        self.assertEqual(safe_repr["facts"][0]["value"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
