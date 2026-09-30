import os
import tempfile
import unittest
from pathlib import Path

from harness.tools.workspace import Workspace, WorkspaceBoundaryError


class TestWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self._temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_dir)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_root_property(self) -> None:
        self.assertEqual(self.workspace.root, self.workspace_dir)

    def test_empty_root_raises_error(self) -> None:
        with self.assertRaises(ValueError):
            Workspace("")

    def test_resolve_relative_file(self) -> None:
        resolved = self.workspace.resolve("notes.txt")
        self.assertEqual(resolved, self.workspace_dir / "notes.txt")

    def test_resolve_nested_relative_file(self) -> None:
        resolved = self.workspace.resolve("docs/report.md")
        self.assertEqual(resolved, self.workspace_dir / "docs" / "report.md")

    def test_resolve_current_directory_dot(self) -> None:
        resolved = self.workspace.resolve(".")
        self.assertEqual(resolved, self.workspace_dir)

    def test_resolve_safe_parent_normalization(self) -> None:
        resolved = self.workspace.resolve("docs/../notes.txt")
        self.assertEqual(resolved, self.workspace_dir / "notes.txt")

    def test_resolve_parent_escape_rejected(self) -> None:
        with self.assertRaises(WorkspaceBoundaryError) as ctx:
            self.workspace.resolve("../outside.txt")
        self.assertIn("outside", str(ctx.exception).lower())

    def test_resolve_deep_traversal_rejected(self) -> None:
        with self.assertRaises(WorkspaceBoundaryError):
            self.workspace.resolve("../../etc/passwd")

    def test_resolve_absolute_path_outside_rejected(self) -> None:
        outside_path = Path("/etc/passwd")
        with self.assertRaises(WorkspaceBoundaryError):
            self.workspace.resolve(outside_path)

    def test_resolve_absolute_path_inside_allowed(self) -> None:
        inside_path = self.workspace_dir / "data" / "file.csv"
        resolved = self.workspace.resolve(inside_path)
        self.assertEqual(resolved, inside_path)

    def test_resolve_nonexistent_safe_target(self) -> None:
        resolved = self.workspace.resolve("new_folder/future_file.txt")
        self.assertEqual(resolved, self.workspace_dir / "new_folder" / "future_file.txt")

    def test_resolve_symlink_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as outside_temp_dir:
            outside_path = Path(outside_temp_dir).resolve()
            symlink_path = self.workspace_dir / "outside_link"

            try:
                os.symlink(outside_path, symlink_path)
            except (OSError, NotImplementedError) as e:
                self.skipTest(f"Symlinks not supported on this platform: {e}")

            with self.assertRaises(WorkspaceBoundaryError):
                self.workspace.resolve("outside_link/secret.txt")


if __name__ == "__main__":
    unittest.main()
