"""Unit and fault-injection tests for Phase B: Filesystem Mutation Safety (H4, H5, H6)."""

import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from harness.tools.base import ErrorCode
from harness.tools.filesystem import (
    CreateFileTool,
    ModifyFileTool,
    ReadFileTool,
    _compute_file_sha256,
)
from harness.tools.workspace import Workspace


class TestMutationSafety(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self._temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_dir)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- H5: Atomic Writes & Pre-Commit Failure Preservation ---

    def test_modify_file_atomic_preserves_content_on_partial_write_failure(self) -> None:
        """H5: If os.write writes partial bytes and then fails, original target content is preserved intact."""
        target = self.workspace_dir / "app.conf"
        original_content = "port=8080\nworkers=4\nenv=production\n"
        target.write_text(original_content, encoding="utf-8")

        tool = ModifyFileTool(self.workspace)

        real_write = os.write
        calls = [0]

        def partial_write_mock(fd: int, data: bytes) -> int:
            calls[0] += 1
            if calls[0] == 1:
                # Genuinely write only the first 5 bytes to disk
                return real_write(fd, data[:5])
            # Subsequent invocation raises in-flight write error
            raise OSError("Simulated disk full during partial in-flight write")

        with patch("os.write", side_effect=partial_write_mock):
            result = tool.execute({
                "path": "app.conf",
                "old_text": "port=8080",
                "new_text": "port=9090",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertEqual(calls[0], 2)
        # Verify original file was not truncated or modified
        self.assertEqual(target.read_text(encoding="utf-8"), original_content)
        # Verify partial temp file was unlinked and cleaned up
        temp_files = [p.name for p in self.workspace_dir.iterdir() if p.name.startswith(".tmp_")]
        self.assertEqual(temp_files, [])

    def test_create_file_atomic_cleans_up_temp_on_partial_write_failure(self) -> None:
        """H5: If create_file encounters partial write followed by failure, no destination is installed."""
        tool = CreateFileTool(self.workspace)
        target = self.workspace_dir / "new_data.txt"

        real_write = os.write
        calls = [0]

        def partial_write_mock(fd: int, data: bytes) -> int:
            calls[0] += 1
            if calls[0] == 1:
                return real_write(fd, data[:5])
            raise OSError("Simulated disk full during create in-flight write")

        with patch("os.write", side_effect=partial_write_mock):
            result = tool.execute({
                "path": "new_data.txt",
                "content": "critical initial payload with many bytes",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertEqual(calls[0], 2)
        # Verify no destination file was installed
        self.assertFalse(target.exists())
        # Verify partial temp file was removed
        temp_files = [p.name for p in self.workspace_dir.iterdir() if p.name.startswith(".tmp_")]
        self.assertEqual(temp_files, [])

    def test_modify_file_atomic_preserves_content_on_fsync_failure(self) -> None:
        """H5: If os.fsync fails before commit, original target content is preserved intact."""
        target = self.workspace_dir / "app.conf"
        original_content = "port=8080\nworkers=4\nenv=production\n"
        target.write_text(original_content, encoding="utf-8")

        tool = ModifyFileTool(self.workspace)

        with patch("os.fsync", side_effect=OSError("Simulated pre-commit buffer sync failure")):
            result = tool.execute({
                "path": "app.conf",
                "old_text": "port=8080",
                "new_text": "port=9090",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertEqual(target.read_text(encoding="utf-8"), original_content)
        temp_files = [p.name for p in self.workspace_dir.iterdir() if p.name.startswith(".tmp_")]
        self.assertEqual(temp_files, [])

    def test_create_file_race_collision_fails_cleanly(self) -> None:
        """H5: Concurrent file appearance before commit fails with ALREADY_EXISTS without overwrite."""
        tool = CreateFileTool(self.workspace)
        target = self.workspace_dir / "shared.txt"

        # Simulate concurrent creation right at the moment of os.link
        real_link = os.link

        def race_link(src: os.PathLike[str] | str, dst: os.PathLike[str] | str) -> None:
            Path(dst).write_text("concurrently created content", encoding="utf-8")
            real_link(src, dst)  # Will raise FileExistsError on POSIX

        with patch("os.link", side_effect=race_link):
            result = tool.execute({
                "path": "shared.txt",
                "content": "our payload",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.ALREADY_EXISTS)
        self.assertIn("file already exists", result.content)
        # Verify the concurrent content was preserved, not overwritten
        self.assertEqual(target.read_text(encoding="utf-8"), "concurrently created content")
        # Verify temp file cleaned up
        temp_files = [p.name for p in self.workspace_dir.iterdir() if p.name.startswith(".tmp_")]
        self.assertEqual(temp_files, [])

    def test_modify_file_commit_failure_preserves_target(self) -> None:
        """H5: If os.replace fails during commit, target file remains intact and temp is removed."""
        target = self.workspace_dir / "document.txt"
        target.write_text("v1 content", encoding="utf-8")

        tool = ModifyFileTool(self.workspace)

        with patch("os.replace", side_effect=OSError("Simulated replace lock failure")):
            result = tool.execute({
                "path": "document.txt",
                "old_text": "v1",
                "new_text": "v2",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertEqual(target.read_text(encoding="utf-8"), "v1 content")
        temp_files = [p.name for p in self.workspace_dir.iterdir() if p.name.startswith(".tmp_")]
        self.assertEqual(temp_files, [])

    def test_create_file_preserves_standard_umask_permissions(self) -> None:
        """H5: create_file produces file permissions matching standard open(x) under active umask."""
        tool = CreateFileTool(self.workspace)
        result = tool.execute({
            "path": "new_created.txt",
            "content": "hello permission check",
        })

        self.assertFalse(result.is_error)
        target = self.workspace_dir / "new_created.txt"
        self.assertTrue(target.is_file())

        umask = os.umask(0)
        os.umask(umask)
        expected_mode = stat.S_IMODE(0o666 & ~umask)
        actual_mode = stat.S_IMODE(target.stat().st_mode)
        self.assertEqual(actual_mode, expected_mode)

    def test_create_file_aborts_and_cleans_temp_on_chmod_failure(self) -> None:
        """H5: If permission preservation fails during create_file, operation aborts before linking."""
        tool = CreateFileTool(self.workspace)
        target = self.workspace_dir / "chmod_fail.txt"

        with patch("os.chmod", side_effect=OSError("Simulated permission setup failure")):
            result = tool.execute({
                "path": "chmod_fail.txt",
                "content": "payload",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.TRANSIENT_ERROR)
        self.assertFalse(target.exists())
        temp_files = [p.name for p in self.workspace_dir.iterdir() if p.name.startswith(".tmp_")]
        self.assertEqual(temp_files, [])

    def test_modify_file_preserves_file_mode_permissions(self) -> None:
        """H5: File permission bits (stat.S_IMODE) are preserved across atomic replacement."""
        target = self.workspace_dir / "script.sh"
        target.write_text("#!/bin/sh\necho hello\n", encoding="utf-8")
        target_mode = 0o755
        os.chmod(target, target_mode)

        expected_mode = stat.S_IMODE(target.stat().st_mode)

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({
            "path": "script.sh",
            "old_text": "echo hello",
            "new_text": "echo world",
        })

        self.assertFalse(result.is_error)
        new_mode = stat.S_IMODE(target.stat().st_mode)
        self.assertEqual(new_mode, expected_mode)
        self.assertEqual(target.read_text(encoding="utf-8"), "#!/bin/sh\necho world\n")

    # --- H4: Deterministic Postcondition Verification ---

    def test_h4_create_file_postcondition_mismatch_detected(self) -> None:
        """H4: If on-disk content after creation differs from requested content, INTERNAL_ERROR is returned."""
        tool = CreateFileTool(self.workspace)

        with patch.object(Path, "read_text", return_value="corrupted bytes on disk"):
            result = tool.execute({
                "path": "report.txt",
                "content": "intended clean report",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INTERNAL_ERROR)
        self.assertIn("Postcondition check failed", result.content)

    def test_h4_modify_file_postcondition_mismatch_detected(self) -> None:
        """H4: If on-disk content after modification differs from expected update, INTERNAL_ERROR is returned."""
        target = self.workspace_dir / "record.txt"
        target.write_text("item: apple\n", encoding="utf-8")

        tool = ModifyFileTool(self.workspace)

        real_read_text = Path.read_text
        call_count = [0]

        def corrupted_postcondition_read(*args: object, **kwargs: object) -> str:
            call_count[0] += 1
            if call_count[0] >= 2:
                return "corrupted content after commit"
            return real_read_text(target, *args, **kwargs)

        with patch.object(Path, "read_text", side_effect=corrupted_postcondition_read):
            result = tool.execute({
                "path": "record.txt",
                "old_text": "apple",
                "new_text": "banana",
            })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.INTERNAL_ERROR)
        self.assertIn("Postcondition check failed", result.content)

    # --- H6: Versioned Read & Optimistic Conflict Detection ---

    def test_h6_read_file_includes_full_64hex_sha256(self) -> None:
        """H6: read_file(include_version=True) returns a full 64-character SHA-256 hash of raw bytes."""
        target = self.workspace_dir / "sample.txt"
        payload = "versioned content with emoji 🌟\n"
        target.write_text(payload, encoding="utf-8")
        expected_hash = hashlib.sha256(target.read_bytes()).hexdigest()

        tool = ReadFileTool(self.workspace)
        result = tool.execute({"path": "sample.txt", "include_version": True})

        self.assertFalse(result.is_error)
        lines = result.content.splitlines(keepends=True)
        version_header = lines[0].strip()
        body = "".join(lines[1:])

        self.assertTrue(version_header.startswith("[Version: "))
        self.assertTrue(version_header.endswith("]"))
        token = version_header[len("[Version: ") : -1]

        self.assertEqual(len(token), 64)
        self.assertEqual(token, expected_hash)
        self.assertEqual(body, payload)

    def test_h6_modify_file_succeeds_with_matching_version(self) -> None:
        """H6: modify_file with matching expected_version succeeds and applies change."""
        target = self.workspace_dir / "settings.json"
        target.write_text('{"theme": "light"}\n', encoding="utf-8")
        version = _compute_file_sha256(target)

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({
            "path": "settings.json",
            "old_text": '"light"',
            "new_text": '"dark"',
            "expected_version": version,
        })

        self.assertFalse(result.is_error)
        self.assertIn("modified successfully", result.content)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"theme": "dark"}\n')

    def test_h6_modify_file_rejects_stale_version_with_conflict(self) -> None:
        """H6: modify_file with stale expected_version returns CONFLICT and leaves file untouched."""
        target = self.workspace_dir / "state.txt"
        target.write_text("counter=1\nstatus=idle\n", encoding="utf-8")
        observed_version = _compute_file_sha256(target)

        # External process modifies another part of the file before the agent's mutation
        target.write_text("counter=1\nstatus=busy\n", encoding="utf-8")

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({
            "path": "state.txt",
            "old_text": "counter=1",
            "new_text": "counter=2",
            "expected_version": observed_version,
        })

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, ErrorCode.CONFLICT)
        self.assertIn("Conflict detected", result.content)
        # The file remains in the externally updated state, untouched by the tool
        self.assertEqual(target.read_text(encoding="utf-8"), "counter=1\nstatus=busy\n")
