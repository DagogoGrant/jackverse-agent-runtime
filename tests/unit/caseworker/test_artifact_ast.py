"""Unit tests for PreparedArtifact and ArtifactBlock AST models."""

from datetime import datetime, timezone
import unittest

from caseworker.domain.artifact import ArtifactBlock, ArtifactSet, PreparedArtifact
from caseworker.domain.enums import ArtifactGroundingType, ArtifactStatus, PreparationMaterialType
from caseworker.domain.errors import DomainValidationError


class TestArtifactAST(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime.now(timezone.utc)

    def test_artifact_block_grounding_invariants(self) -> None:
        # 1. CLAIM grounding requires at least one claim_id
        with self.assertRaises(DomainValidationError):
            ArtifactBlock(
                content="Demonstrated 5+ years experience.",
                grounding_type=ArtifactGroundingType.CLAIM,
                claim_ids=[],
            )

        # Valid CLAIM block
        blk_claim = ArtifactBlock(
            content="Demonstrated 5+ years experience.",
            grounding_type=ArtifactGroundingType.CLAIM,
            claim_ids=["claim-123"],
        )
        self.assertEqual(blk_claim.claim_ids, ["claim-123"])

        # 2. CONTEXT_FACT grounding requires at least one fact_id
        with self.assertRaises(DomainValidationError):
            ArtifactBlock(
                content="Email: test@example.com",
                grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                fact_ids=[],
            )

        blk_fact = ArtifactBlock(
            content="Email: test@example.com",
            grounding_type=ArtifactGroundingType.CONTEXT_FACT,
            fact_ids=["fact-123"],
        )
        self.assertEqual(blk_fact.fact_ids, ["fact-123"])

        # 3. OPPORTUNITY grounding requires at least one opportunity_field
        with self.assertRaises(DomainValidationError):
            ArtifactBlock(
                content="Application for Senior Engineer",
                grounding_type=ArtifactGroundingType.OPPORTUNITY,
                opportunity_fields=[],
            )

        blk_opp = ArtifactBlock(
            content="Application for Senior Engineer",
            grounding_type=ArtifactGroundingType.OPPORTUNITY,
            opportunity_fields=["title"],
        )
        self.assertEqual(blk_opp.opportunity_fields, ["title"])

        # 4. STRUCTURAL requires no references
        blk_struct = ArtifactBlock(
            content="Sincerely,\nApplicant",
            grounding_type=ArtifactGroundingType.STRUCTURAL,
        )
        self.assertEqual(blk_struct.grounding_type, ArtifactGroundingType.STRUCTURAL)

    def test_block_serialization_roundtrip(self) -> None:
        blk = ArtifactBlock(
            block_id="b-1",
            block_type="cv_bullet",
            content="Engineered resilient distributed systems.",
            grounding_type=ArtifactGroundingType.CLAIM,
            claim_ids=["c-1", "c-2"],
            metadata={"priority": "high"},
        )
        d = blk.to_dict()
        blk_rt = ArtifactBlock.from_dict(d)
        self.assertEqual(blk.block_id, blk_rt.block_id)
        self.assertEqual(blk.content, blk_rt.content)
        self.assertEqual(blk.grounding_type, blk_rt.grounding_type)
        self.assertEqual(blk.claim_ids, blk_rt.claim_ids)
        self.assertEqual(blk.metadata, blk_rt.metadata)

    def test_prepared_artifact_rendering_and_zero_citations(self) -> None:
        blocks = [
            ArtifactBlock(
                block_type="header",
                content="Jane Doe",
                grounding_type=ArtifactGroundingType.STRUCTURAL,
            ),
            ArtifactBlock(
                block_type="contact_info",
                content="Email: jane@example.com | San Francisco, CA",
                grounding_type=ArtifactGroundingType.CONTEXT_FACT,
                fact_ids=["f-email", "f-loc"],
            ),
            ArtifactBlock(
                block_type="section_title",
                content="Professional Summary",
                grounding_type=ArtifactGroundingType.STRUCTURAL,
            ),
            ArtifactBlock(
                block_type="summary",
                content="Staff Software Engineer with deep expertise in distributed ledger technology.",
                grounding_type=ArtifactGroundingType.CLAIM,
                claim_ids=["c-summary"],
            ),
            ArtifactBlock(
                block_type="section_title",
                content="Experience",
                grounding_type=ArtifactGroundingType.STRUCTURAL,
            ),
            ArtifactBlock(
                block_type="cv_bullet",
                content="Architected high-throughput ingestion pipelines handling 50k events/sec.",
                grounding_type=ArtifactGroundingType.CLAIM,
                claim_ids=["c-exp-1"],
            ),
            ArtifactBlock(
                block_type="cv_bullet",
                content="Led cross-functional migration of relational persistence to event sourcing.",
                grounding_type=ArtifactGroundingType.CLAIM,
                claim_ids=["c-exp-2"],
            ),
        ]

        artifact = PreparedArtifact(
            artifact_id="art-cv-1",
            artifact_set_id="artset-1",
            case_id="case-1",
            opportunity_id="opp-1",
            user_id="user-1",
            material_type=PreparationMaterialType.CV,
            title="Tailored CV - Acme Corp - Staff Engineer",
            blocks=blocks,
            source_report_id="rep-1",
            input_hash="hash-in",
            grounding_snapshot_hash="hash-ground",
        )

        md = artifact.render_markdown()
        # Verify markdown headers and bullets
        self.assertIn("# Jane Doe", md)
        self.assertIn("## Professional Summary", md)
        self.assertIn("- Architected high-throughput ingestion pipelines", md)
        self.assertIn("- Led cross-functional migration", md)

        # Critical invariant: Zero synthetic citation markers in rendered output
        self.assertNotIn("[claim:", md)
        self.assertNotIn("[fact:", md)
        self.assertNotIn("[citation:", md)

        # Plain text rendering
        plain = artifact.render_plain_text()
        self.assertNotIn("#", plain)
        self.assertIn("Jane Doe", plain)
        self.assertIn("Architected high-throughput", plain)

    def test_artifact_provenance_map(self) -> None:
        blk = ArtifactBlock(
            block_type="paragraph",
            content="I am writing to express interest in the role.",
            grounding_type=ArtifactGroundingType.OPPORTUNITY,
            opportunity_fields=["title"],
        )
        artifact = PreparedArtifact(
            artifact_id="art-cl-1",
            artifact_set_id="artset-1",
            case_id="case-1",
            opportunity_id="opp-1",
            user_id="user-1",
            material_type=PreparationMaterialType.COVER_LETTER,
            title="Cover Letter",
            blocks=[blk],
            source_report_id="rep-1",
            input_hash="hash-1",
            grounding_snapshot_hash="hash-2",
        )

        prov = artifact.render_provenance_map()
        self.assertEqual(prov["artifact_id"], "art-cl-1")
        self.assertEqual(prov["material_type"], "cover_letter")
        self.assertEqual(len(prov["blocks"]), 1)
        self.assertEqual(prov["blocks"][0]["grounding_type"], "opportunity")
        self.assertEqual(prov["blocks"][0]["opportunity_fields"], ["title"])

    def test_artifact_lifecycle_transitions(self) -> None:
        artifact = PreparedArtifact(
            artifact_id="art-1",
            artifact_set_id="artset-1",
            case_id="case-1",
            opportunity_id="opp-1",
            user_id="user-1",
            material_type=PreparationMaterialType.CV,
            title="CV",
            source_report_id="rep-1",
        )
        self.assertEqual(artifact.status, ArtifactStatus.DRAFT)
        self.assertEqual(artifact.version, 1)

        # Mark stale
        artifact.mark_stale()
        self.assertEqual(artifact.status, ArtifactStatus.STALE)
        self.assertEqual(artifact.version, 2)

        # Mark superseded
        artifact.mark_superseded()
        self.assertEqual(artifact.status, ArtifactStatus.SUPERSEDED)
        self.assertEqual(artifact.version, 3)

        # Freeze
        artifact.freeze()
        self.assertEqual(artifact.status, ArtifactStatus.FROZEN)
        self.assertEqual(artifact.version, 4)

    def test_artifact_serialization_roundtrip(self) -> None:
        blk = ArtifactBlock(
            content="Proven skill in Rust and Python.",
            grounding_type=ArtifactGroundingType.CLAIM,
            claim_ids=["c-1"],
        )
        artifact = PreparedArtifact(
            artifact_id="art-1",
            artifact_set_id="artset-1",
            case_id="case-1",
            opportunity_id="opp-1",
            user_id="user-1",
            material_type=PreparationMaterialType.APPLICATION_ANSWER_BANK,
            title="Answer Bank",
            blocks=[blk],
            source_report_id="rep-1",
            input_hash="inhash",
            grounding_snapshot_hash="gnhash",
        )

        d = artifact.to_dict()
        art_rt = PreparedArtifact.from_dict(d)
        self.assertEqual(artifact.artifact_id, art_rt.artifact_id)
        self.assertEqual(artifact.material_type, art_rt.material_type)
        self.assertEqual(len(art_rt.blocks), 1)
        self.assertEqual(art_rt.blocks[0].content, blk.content)
