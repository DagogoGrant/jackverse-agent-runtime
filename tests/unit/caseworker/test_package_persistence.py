"""Unit tests for PreparationPackage SQLite persistence, immutability, and uniqueness."""

import unittest

from caseworker.domain.case import Case
from caseworker.domain.enums import (
    ArtifactPublicationRole,
    CaseStatus,
    CaseType,
    ClaimStatus,
    OpportunityStatus,
    OpportunityType,
    PackageVerificationStatus,
    PreparationMaterialType,
    PreparationPackageStatus,
)
from caseworker.domain.errors import DomainValidationError, OptimisticLockError, PersistenceError
from caseworker.domain.opportunity import Opportunity
from caseworker.domain.package import (
    ArtifactPackageSnapshot,
    PreparationPackage,
    SafeClaimSnapshot,
)
from caseworker.domain.preparation import PreparationIntelligenceReport, PreparationStrategy
from caseworker.persistence.sqlite import SQLiteCaseworkerStorage


class TestPackagePersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = SQLiteCaseworkerStorage(":memory:")
        self.user_id = "user-1"
        self.case_id = "case-1"
        self.opp_id = "opp-1"
        self.report_id = "rep-1"

        with self.storage.unit_of_work() as uow:
            self.case = Case(
                case_id=self.case_id,
                user_id=self.user_id,
                title="Staff Pursuit",
                goal="Secure role",
                case_type=CaseType.JOB_APPLICATION,
                status=CaseStatus.PLANNING,
            )
            uow.cases.save(self.case)

            self.opportunity = Opportunity(
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                title="Staff Engineer",
                organization="JackVerse Core",
                opportunity_type=OpportunityType.JOB,
                location="Munich, Germany",
                requirements=["Python proficiency (Mandatory)"],
                converted_case_id=self.case_id,
                status=OpportunityStatus.SHORTLISTED,
            )
            uow.opportunities.save(self.opportunity)

            self.report = PreparationIntelligenceReport(
                report_id=self.report_id,
                case_id=self.case_id,
                opportunity_id=self.opp_id,
                user_id=self.user_id,
                strategy=PreparationStrategy(
                    recommended_materials=["cv", "cover_letter"],
                    strengths=[],
                    mitigations=[],
                    claim_intents=[],
                    materials_checklist=[],
                ),
                input_hash="hash_inp",
                context_snapshot_hash="hash_ctx",
                source_evaluation_id="eval-1",
            )
            uow.preparation_reports.save(self.report)

        self.art_snap = ArtifactPackageSnapshot(
            artifact_id="art-cv-1",
            artifact_version=2,
            material_type=PreparationMaterialType.CV.value,
            rendered_payload_hash="hash_cv_v2",
            grounding_snapshot_hash="ground_hash_cv",
            publication_role=ArtifactPublicationRole.SUBMISSION_MATERIAL.value,
        )
        self.claim_snap = SafeClaimSnapshot(
            claim_id="claim-python-1",
            claim_version=1,
            claim_status=ClaimStatus.SUPPORTED.value,
            support_snapshot_hash="hash_supp_1",
            assertion_hash="hash_assert_1",
        )

    def tearDown(self) -> None:
        self.storage.close()

    def _create_package(
        self,
        package_id: str = "pkg-1",
        case_id: str = "case-1",
        artifact_set_id: str = "set-1",
        source_report_id: str = "rep-1",
        version: int = 1,
    ) -> PreparationPackage:
        return PreparationPackage(
            package_id=package_id,
            case_id=case_id,
            artifact_set_id=artifact_set_id,
            source_report_id=source_report_id,
            user_id=self.user_id,
            status=PreparationPackageStatus.FROZEN,
            artifact_ids=["art-cv-1"],
            artifact_versions={"art-cv-1": 2},
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
            rendered_payload_hashes={"cv": "hash_cv_v2"},
            verification_status=PackageVerificationStatus.VERIFIED,
            verification_findings=[{"check_name": "all_passed", "passed": True, "severity": "info", "message": "OK"}],
            version=version,
        )

    def test_save_and_get_by_id(self) -> None:
        pkg = self._create_package()

        with self.storage.unit_of_work() as uow:
            uow.preparation_packages.save(pkg)

        with self.storage.unit_of_work() as uow:
            retrieved = uow.preparation_packages.get_by_id("pkg-1")
            self.assertIsNotNone(retrieved)
            assert retrieved is not None
            self.assertEqual(retrieved.package_id, "pkg-1")
            self.assertEqual(retrieved.case_id, "case-1")
            self.assertEqual(retrieved.artifact_set_id, "set-1")
            self.assertEqual(retrieved.status, PreparationPackageStatus.FROZEN)
            self.assertEqual(retrieved.verification_status, PackageVerificationStatus.VERIFIED)
            self.assertEqual(len(retrieved.artifact_snapshots), 1)
            self.assertEqual(retrieved.artifact_snapshots[0].rendered_payload_hash, "hash_cv_v2")
            self.assertEqual(len(retrieved.claim_snapshots), 1)
            self.assertEqual(retrieved.claim_snapshots[0].claim_id, "claim-python-1")
            self.assertEqual(retrieved.package_fingerprint, pkg.package_fingerprint)
            self.assertEqual(retrieved.version, 1)

    def test_get_by_fingerprint(self) -> None:
        pkg = self._create_package()

        with self.storage.unit_of_work() as uow:
            uow.preparation_packages.save(pkg)

        with self.storage.unit_of_work() as uow:
            retrieved = uow.preparation_packages.get_by_fingerprint(pkg.package_fingerprint)
            self.assertIsNotNone(retrieved)
            assert retrieved is not None
            self.assertEqual(retrieved.package_id, "pkg-1")

            missing = uow.preparation_packages.get_by_fingerprint("non_existent_fp")
            self.assertIsNone(missing)

    def test_get_latest_for_case_and_list(self) -> None:
        pkg1 = self._create_package(package_id="pkg-1", artifact_set_id="set-1")
        # Different artifact set creates a different fingerprint
        pkg2 = self._create_package(package_id="pkg-2", artifact_set_id="set-2")

        with self.storage.unit_of_work() as uow:
            uow.preparation_packages.save(pkg1)
            uow.preparation_packages.save(pkg2)

        with self.storage.unit_of_work() as uow:
            latest = uow.preparation_packages.get_latest_for_case("case-1")
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.package_id, "pkg-2")

            case_pkgs = uow.preparation_packages.list_for_case("case-1")
            self.assertEqual(len(case_pkgs), 2)
            self.assertEqual([p.package_id for p in case_pkgs], ["pkg-2", "pkg-1"])

            set1_pkgs = uow.preparation_packages.list_by_set("set-1")
            self.assertEqual(len(set1_pkgs), 1)
            self.assertEqual(set1_pkgs[0].package_id, "pkg-1")

    def test_fingerprint_uniqueness_constraint(self) -> None:
        """Amendment 1 & 14: Unique index on package_fingerprint rejects duplicate packages."""
        pkg1 = self._create_package(package_id="pkg-1")

        # pkg2 has identical content -> identical fingerprint, but different package_id
        pkg2 = PreparationPackage(
            package_id="pkg-2",
            case_id="case-1",
            artifact_set_id="set-1",
            source_report_id="rep-1",
            user_id=self.user_id,
            status=PreparationPackageStatus.FROZEN,
            artifact_ids=["art-cv-1"],
            artifact_versions={"art-cv-1": 2},
            artifact_snapshots=[self.art_snap],
            claim_snapshots=[self.claim_snap],
            rendered_payload_hashes={"cv": "hash_cv_v2"},
            verification_status=PackageVerificationStatus.VERIFIED,
        )
        self.assertEqual(pkg1.package_fingerprint, pkg2.package_fingerprint)

        with self.storage.unit_of_work() as uow:
            uow.preparation_packages.save(pkg1)

        with self.storage.unit_of_work() as uow:
            with self.assertRaises(PersistenceError):
                uow.preparation_packages.save(pkg2)

    def test_immutability_enforcement_on_update(self) -> None:
        """Amendment 4: Repository rejects updates modifying content-addressed fields."""
        pkg = self._create_package()

        with self.storage.unit_of_work() as uow:
            uow.preparation_packages.save(pkg)

        # Mutate content-addressed field (artifact_set_id)
        with self.storage.unit_of_work() as uow:
            retrieved = uow.preparation_packages.get_by_id("pkg-1")
            assert retrieved is not None
            retrieved.artifact_set_id = "set-MUTATED"
            retrieved.version = 2
            with self.assertRaises(DomainValidationError):
                uow.preparation_packages.save(retrieved)

    def test_optimistic_locking(self) -> None:
        """Test optimistic locking prevents stale updates."""
        pkg = self._create_package()

        with self.storage.unit_of_work() as uow:
            uow.preparation_packages.save(pkg)

        with self.storage.unit_of_work() as uow:
            retrieved1 = uow.preparation_packages.get_by_id("pkg-1")
            assert retrieved1 is not None
            retrieved1.approve("User approved")
            uow.preparation_packages.save(retrieved1)
            self.assertEqual(retrieved1.version, 2)

        # Attempt to save with invalid version sequence
        stale_pkg = self._create_package(version=2)
        with self.storage.unit_of_work() as uow:
            stale_pkg.status = PreparationPackageStatus.SUPERSEDED
            with self.assertRaises(OptimisticLockError):
                uow.preparation_packages.save(stale_pkg)


if __name__ == "__main__":
    unittest.main()
