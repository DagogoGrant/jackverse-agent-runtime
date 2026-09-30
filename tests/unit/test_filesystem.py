import os
import tempfile
import unittest
from pathlib import Path

from harness.tools.filesystem import (
    CreateDirectoryTool,
    CreateFileTool,
    ListDirectoryTool,
    ModifyFileTool,
    ReadFileTool,
    SearchFilesTool,
)
from harness.tools.workspace import Workspace


class TestFilesystemTools(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.workspace_dir = Path(self._temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_dir)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- CreateDirectoryTool ---

    def test_create_directory_success_and_nested(self) -> None:
        tool = CreateDirectoryTool(self.workspace)
        result = tool.execute({"path": "nested/folder/sub"})

        self.assertFalse(result.is_error)
        self.assertTrue((self.workspace_dir / "nested" / "folder" / "sub").is_dir())
        self.assertIn("created successfully", result.content)

    def test_create_directory_idempotent(self) -> None:
        tool = CreateDirectoryTool(self.workspace)
        tool.execute({"path": "docs"})
        result = tool.execute({"path": "docs"})

        self.assertFalse(result.is_error)
        self.assertIn("already exists", result.content)

    def test_create_directory_target_is_file_error(self) -> None:
        file_path = self.workspace_dir / "existing_file.txt"
        file_path.write_text("content", encoding="utf-8")

        tool = CreateDirectoryTool(self.workspace)
        result = tool.execute({"path": "existing_file.txt"})

        self.assertTrue(result.is_error)
        self.assertIn("target exists and is a file", result.content)

    # --- CreateFileTool ---

    def test_create_file_success(self) -> None:
        tool = CreateFileTool(self.workspace)
        result = tool.execute({"path": "notes.txt", "content": "Hello, world! 🚀"})

        self.assertFalse(result.is_error)
        file_path = self.workspace_dir / "notes.txt"
        self.assertTrue(file_path.is_file())
        self.assertEqual(file_path.read_text(encoding="utf-8"), "Hello, world! 🚀")

    def test_create_file_refuses_existing_file(self) -> None:
        file_path = self.workspace_dir / "notes.txt"
        file_path.write_text("original content", encoding="utf-8")

        tool = CreateFileTool(self.workspace)
        result = tool.execute({"path": "notes.txt", "content": "new content"})

        self.assertTrue(result.is_error)
        self.assertIn("already exists", result.content)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "original content")

    def test_create_file_refuses_missing_parent(self) -> None:
        tool = CreateFileTool(self.workspace)
        result = tool.execute({"path": "missing_dir/notes.txt", "content": "hello"})

        self.assertTrue(result.is_error)
        self.assertIn("parent directory does not exist", result.content)

    def test_create_file_refuses_parent_is_not_directory(self) -> None:
        file_path = self.workspace_dir / "file_as_parent"
        file_path.write_text("content", encoding="utf-8")

        tool = CreateFileTool(self.workspace)
        result = tool.execute({"path": "file_as_parent/child.txt", "content": "hello"})

        self.assertTrue(result.is_error)
        self.assertIn("parent is not a directory", result.content)

    # --- ReadFileTool ---

    def test_read_file_success(self) -> None:
        file_path = self.workspace_dir / "data.txt"
        file_path.write_text("Line 1\nLine 2\n", encoding="utf-8")

        tool = ReadFileTool(self.workspace)
        result = tool.execute({"path": "data.txt"})

        self.assertFalse(result.is_error)
        self.assertEqual(result.content, "Line 1\nLine 2\n")

    def test_read_file_missing_file_error(self) -> None:
        tool = ReadFileTool(self.workspace)
        result = tool.execute({"path": "nonexistent.txt"})

        self.assertTrue(result.is_error)
        self.assertIn("file does not exist", result.content)

    def test_read_file_target_is_directory_error(self) -> None:
        dir_path = self.workspace_dir / "somedir"
        dir_path.mkdir()

        tool = ReadFileTool(self.workspace)
        result = tool.execute({"path": "somedir"})

        self.assertTrue(result.is_error)
        self.assertIn("path is a directory", result.content)

    def test_read_file_non_utf8_error(self) -> None:
        bin_path = self.workspace_dir / "binary.dat"
        bin_path.write_bytes(b"\x80\x81\xFF\xFE")

        tool = ReadFileTool(self.workspace)
        result = tool.execute({"path": "binary.dat"})

        self.assertTrue(result.is_error)
        self.assertIn("not valid UTF-8", result.content)

    # --- ListDirectoryTool ---

    def test_list_directory_success_and_deterministic_order(self) -> None:
        (self.workspace_dir / "b_file.txt").write_text("b", encoding="utf-8")
        (self.workspace_dir / "a_dir").mkdir()
        (self.workspace_dir / "c_dir").mkdir()
        (self.workspace_dir / "a_file.txt").write_text("a", encoding="utf-8")

        tool = ListDirectoryTool(self.workspace)
        result = tool.execute({"path": "."})

        self.assertFalse(result.is_error)
        expected = "DIR a_dir\nFILE a_file.txt\nFILE b_file.txt\nDIR c_dir"
        self.assertEqual(result.content, expected)

    def test_list_directory_default_path_is_root(self) -> None:
        (self.workspace_dir / "root_file.txt").write_text("hello", encoding="utf-8")
        tool = ListDirectoryTool(self.workspace)
        result = tool.execute({})

        self.assertFalse(result.is_error)
        self.assertIn("FILE root_file.txt", result.content)

    def test_list_directory_schema_required(self) -> None:
        tool = ListDirectoryTool(self.workspace)
        self.assertEqual(tool.spec.input_schema["required"], [])

    def test_list_directory_target_is_file_error(self) -> None:
        file_path = self.workspace_dir / "plain.txt"
        file_path.write_text("content", encoding="utf-8")

        tool = ListDirectoryTool(self.workspace)
        result = tool.execute({"path": "plain.txt"})

        self.assertTrue(result.is_error)
        self.assertIn("path is a file, not a directory", result.content)

    def test_list_directory_missing_directory_error(self) -> None:
        tool = ListDirectoryTool(self.workspace)
        result = tool.execute({"path": "nonexistent_dir"})

        self.assertTrue(result.is_error)
        self.assertIn("directory does not exist", result.content)

    # --- SearchFilesTool ---

    def test_search_files_recursive_and_workspace_relative(self) -> None:
        sub = self.workspace_dir / "sub"
        sub.mkdir()
        (self.workspace_dir / "root.txt").write_text("Target item on line 1\nOther line\n", encoding="utf-8")
        (sub / "nested.txt").write_text("Header\nSecond line has Target item\n", encoding="utf-8")

        tool = SearchFilesTool(self.workspace)
        result = tool.execute({"query": "Target item", "path": "."})

        self.assertFalse(result.is_error)
        lines = result.content.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("root.txt:1: Target item on line 1", lines)
        self.assertIn("sub/nested.txt:2: Second line has Target item", lines)

    def test_search_files_default_path_is_root(self) -> None:
        (self.workspace_dir / "root.txt").write_text("Find Target in root file\n", encoding="utf-8")
        tool = SearchFilesTool(self.workspace)
        result = tool.execute({"query": "Target"})

        self.assertFalse(result.is_error)
        self.assertIn("root.txt:1: Find Target in root file", result.content)

    def test_search_files_schema_required(self) -> None:
        tool = SearchFilesTool(self.workspace)
        self.assertEqual(tool.spec.input_schema["required"], ["query"])

    def test_search_files_no_matches_message(self) -> None:
        (self.workspace_dir / "file.txt").write_text("Some text", encoding="utf-8")

        tool = SearchFilesTool(self.workspace)
        result = tool.execute({"query": "NonExistentPattern", "path": "."})

        self.assertFalse(result.is_error)
        self.assertEqual(result.content, "No matches found.")

    def test_search_files_skips_non_utf8(self) -> None:
        (self.workspace_dir / "binary.bin").write_bytes(b"\x80\x81 Target \xFF\xFE")
        (self.workspace_dir / "valid.txt").write_text("Found Target here", encoding="utf-8")

        tool = SearchFilesTool(self.workspace)
        result = tool.execute({"query": "Target", "path": "."})

        self.assertFalse(result.is_error)
        self.assertEqual(result.content, "valid.txt:1: Found Target here")

    def test_search_files_stops_at_max_results(self) -> None:
        lines = [f"Match {i}" for i in range(100)]
        (self.workspace_dir / "large.txt").write_text("\n".join(lines), encoding="utf-8")

        tool = SearchFilesTool(self.workspace)
        result = tool.execute({"query": "Match", "path": "."})

        self.assertFalse(result.is_error)
        match_lines = result.content.splitlines()
        # 50 matches plus explicit Phase C truncation notice trailer
        self.assertEqual(len(match_lines), 51)
        self.assertIn("[TRUNCATED: showing 50 of 100 matches.", match_lines[-1])

    def test_search_files_does_not_escape_directory_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_path = Path(outside_dir).resolve()
            (outside_path / "secret.txt").write_text("Secret query keyword", encoding="utf-8")

            symlink_dir = self.workspace_dir / "symlinked_folder"
            try:
                os.symlink(outside_path, symlink_dir)
            except (OSError, NotImplementedError) as e:
                self.skipTest(f"Symlinks not supported: {e}")

            tool = SearchFilesTool(self.workspace)
            result = tool.execute({"query": "Secret query keyword", "path": "."})

            self.assertFalse(result.is_error)
            self.assertEqual(result.content, "No matches found.")

    def test_search_files_accepts_single_file_path(self) -> None:
        demo_dir = self.workspace_dir / "demo"
        demo_dir.mkdir()
        source_file = demo_dir / "source.txt"
        source_file.write_text(
            "/docs/Web/HTML\n"
            "/docs/Web/CSS\n"
            "/about\n"
            "/docs/Web/JavaScript\n",
            encoding="utf-8",
        )

        tool = SearchFilesTool(self.workspace)

        # 1 & 2: Direct file path search returns matches with correct relative path and line numbers
        match_result = tool.execute({"query": "/docs/Web", "path": "demo/source.txt"})
        self.assertFalse(match_result.is_error)
        expected_lines = [
            "demo/source.txt:1: /docs/Web/HTML",
            "demo/source.txt:2: /docs/Web/CSS",
            "demo/source.txt:4: /docs/Web/JavaScript",
        ]
        self.assertEqual(match_result.content.splitlines(), expected_lines)

        # 3: Non-matching query returns "No matches found."
        no_match_result = tool.execute({"query": "/nonexistent", "path": "demo/source.txt"})
        self.assertFalse(no_match_result.is_error)
        self.assertEqual(no_match_result.content, "No matches found.")

        # 4: Existing directory search behavior remains intact
        dir_result = tool.execute({"query": "/docs/Web", "path": "demo"})
        self.assertFalse(dir_result.is_error)
        self.assertEqual(dir_result.content.splitlines(), expected_lines)

    # --- ModifyFileTool ---

    def test_modify_file_replaces_single_match(self) -> None:
        file_path = self.workspace_dir / "config.txt"
        file_path.write_text("port=8080\nhost=localhost\nthreads=4\n", encoding="utf-8")

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({"path": "config.txt", "old_text": "port=8080", "new_text": "port=9090"})

        self.assertFalse(result.is_error)
        self.assertIn("modified successfully", result.content)
        self.assertEqual(
            file_path.read_text(encoding="utf-8"),
            "port=9090\nhost=localhost\nthreads=4\n",
        )

    def test_modify_file_rejects_zero_matches(self) -> None:
        file_path = self.workspace_dir / "config.txt"
        file_path.write_text("port=8080\n", encoding="utf-8")

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({"path": "config.txt", "old_text": "missing_key", "new_text": "val"})

        self.assertTrue(result.is_error)
        self.assertIn("old_text not found", result.content)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "port=8080\n")

    def test_modify_file_rejects_ambiguous_multiple_matches(self) -> None:
        file_path = self.workspace_dir / "code.py"
        file_path.write_text("return True\n# ...\nreturn True\n", encoding="utf-8")

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({"path": "code.py", "old_text": "return True", "new_text": "return False"})

        self.assertTrue(result.is_error)
        self.assertIn("occurs 2 times", result.content)
        self.assertIn("disambiguate", result.content)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "return True\n# ...\nreturn True\n")

    def test_modify_file_rejects_empty_old_text(self) -> None:
        file_path = self.workspace_dir / "file.txt"
        file_path.write_text("some content", encoding="utf-8")

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({"path": "file.txt", "old_text": "", "new_text": "new"})

        self.assertTrue(result.is_error)
        self.assertIn("old_text", result.content)

    def test_modify_file_rejects_missing_file(self) -> None:
        tool = ModifyFileTool(self.workspace)
        result = tool.execute({"path": "missing.txt", "old_text": "a", "new_text": "b"})

        self.assertTrue(result.is_error)
        self.assertIn("file does not exist", result.content)

    def test_modify_file_rejects_non_utf8_file(self) -> None:
        bin_path = self.workspace_dir / "binary.dat"
        bin_path.write_bytes(b"\x80\x81\xFF\xFE")

        tool = ModifyFileTool(self.workspace)
        result = tool.execute({"path": "binary.dat", "old_text": "foo", "new_text": "bar"})

        self.assertTrue(result.is_error)
        self.assertIn("not valid UTF-8", result.content)

    # --- Boundary Integration across tools ---

    def test_tools_reject_workspace_traversal_attempts(self) -> None:
        tools = [
            CreateDirectoryTool(self.workspace),
            CreateFileTool(self.workspace),
            ReadFileTool(self.workspace),
            ListDirectoryTool(self.workspace),
            SearchFilesTool(self.workspace),
            ModifyFileTool(self.workspace),
        ]

        for tool in tools:
            args = {"path": "../outside_escape"}
            if tool.spec.name == "create_file":
                args["content"] = "data"
            elif tool.spec.name == "search_files":
                args["query"] = "search"
            elif tool.spec.name == "modify_file":
                args["old_text"] = "old"
                args["new_text"] = "new"

            result = tool.execute(args)
            self.assertTrue(
                result.is_error,
                f"Tool {tool.spec.name} should fail on workspace traversal attempt",
            )
            self.assertIn("outside the workspace boundary", result.content)


if __name__ == "__main__":
    unittest.main()
