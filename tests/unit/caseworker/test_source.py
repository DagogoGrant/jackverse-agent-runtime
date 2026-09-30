"""Unit tests for ContextSource domain entity, provenance, and privacy safeguards."""

import unittest

from caseworker.domain.enums import SensitivityLevel, SourceType
from caseworker.domain.errors import DomainValidationError
from caseworker.domain.source import ContextSource


class TestContextSourceEntity(unittest.TestCase):
    def test_source_creation_and_attributes(self) -> None:
        source = ContextSource(
            user_id="user_123",
            title="Official Resume 2026",
            source_type=SourceType.DOCUMENT,
            source_reference="s3://vault/cv_2026.pdf",
            content_hash="a1b2c3d4e5f6",
            sensitivity=SensitivityLevel.PERSONAL,
            metadata={"file_size": 1048576, "pages": 2},
        )
        self.assertEqual(source.user_id, "user_123")
        self.assertEqual(source.title, "Official Resume 2026")
        self.assertEqual(source.source_type, SourceType.DOCUMENT)
        self.assertEqual(source.source_reference, "s3://vault/cv_2026.pdf")
        self.assertEqual(source.content_hash, "a1b2c3d4e5f6")
        self.assertEqual(source.sensitivity, SensitivityLevel.PERSONAL)
        self.assertFalse(source.is_inferred)
        self.assertEqual(source.version, 1)
        self.assertIsNotNone(source.created_at)
        self.assertIsNotNone(source.updated_at)

    def test_source_inferred_property(self) -> None:
        inferred = ContextSource(
            user_id="user_1",
            title="Inferred Interests",
            source_type=SourceType.AGENT_INFERENCE,
        )
        self.assertTrue(inferred.is_inferred)

        user_input = ContextSource(
            user_id="user_1",
            title="User Profile",
            source_type=SourceType.USER_INPUT,
        )
        self.assertFalse(user_input.is_inferred)

    def test_source_validation_invariants(self) -> None:
        with self.assertRaises(DomainValidationError):
            ContextSource(user_id="", title="Resume")
        with self.assertRaises(DomainValidationError):
            ContextSource(user_id="u1", title="")
        with self.assertRaises(DomainValidationError):
            ContextSource(user_id="u1", title="Resume", source_id="")
        with self.assertRaises(DomainValidationError):
            ContextSource(user_id="u1", title="Resume", version=0)

    def test_source_update_metadata(self) -> None:
        source = ContextSource(
            user_id="u1",
            title="Resume",
            metadata={"status": "uploaded"},
        )
        self.assertEqual(source.version, 1)
        source.update_metadata({"verified": True})
        self.assertEqual(source.metadata["status"], "uploaded")
        self.assertTrue(source.metadata["verified"])
        self.assertEqual(source.version, 2)

    def test_source_safe_dict_and_repr_redaction(self) -> None:
        sensitive_source = ContextSource(
            user_id="u1",
            title="Tax Return 2025",
            source_reference="s3://vault/tax_secret_123.pdf",
            sensitivity=SensitivityLevel.SENSITIVE,
            metadata={"secret_token": "xyz987"},
        )
        safe_dict = sensitive_source.to_safe_dict()
        self.assertEqual(safe_dict["source_reference"], "[REDACTED]")
        self.assertEqual(safe_dict["metadata"], {"redacted": True})

        # repr does not leak sensitive reference
        self.assertNotIn("tax_secret_123.pdf", repr(sensitive_source))
        self.assertIn("[REDACTED]", repr(sensitive_source))

        # standard to_dict retains raw values for internal storage
        raw_dict = sensitive_source.to_dict()
        self.assertEqual(raw_dict["source_reference"], "s3://vault/tax_secret_123.pdf")
        self.assertEqual(raw_dict["metadata"]["secret_token"], "xyz987")

    def test_serialization_roundtrip(self) -> None:
        source = ContextSource(
            user_id="u1",
            title="LinkedIn Import",
            source_type=SourceType.PROFILE_IMPORT,
            source_reference="https://linkedin.com/in/alice",
            content_hash="hash999",
            sensitivity=SensitivityLevel.PERSONAL,
            metadata={"connections": 500},
        )
        serialized = source.to_dict()
        restored = ContextSource.from_dict(serialized)
        self.assertEqual(restored.source_id, source.source_id)
        self.assertEqual(restored.user_id, source.user_id)
        self.assertEqual(restored.title, source.title)
        self.assertEqual(restored.source_type, SourceType.PROFILE_IMPORT)
        self.assertEqual(restored.source_reference, source.source_reference)
        self.assertEqual(restored.content_hash, source.content_hash)
        self.assertEqual(restored.metadata["connections"], 500)
        self.assertEqual(restored.version, 1)


if __name__ == "__main__":
    unittest.main()
