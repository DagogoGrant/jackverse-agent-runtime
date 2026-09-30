from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from harness.tools.base import ErrorCode, ToolResult, ToolSpec
from harness.tools.search_types import SearchResult
from harness.tools.workspace import Workspace, WorkspaceBoundaryError

MAX_SEARCH_RESULTS: int = 50
# MAX_COUNTED_MATCHES bounds how many matching occurrences we continue counting
# after the returned result set has already been filled. It is not a general
# filesystem scanning resource budget (e.g. bytes scanned or lines read).
MAX_COUNTED_MATCHES: int = 10_000


def _compute_file_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of raw on-disk file bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_valid_sha256(digest: str) -> bool:
    """Validate that digest is a 64-character lowercase hex string."""
    if len(digest) != 64:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in digest)


def _write_bytes_to_fd(fd: int, data: bytes) -> None:
    """Write bytes to a file descriptor and request OS buffer synchronization."""
    total_written = 0
    while total_written < len(data):
        chunk_written = os.write(fd, data[total_written:])
        if chunk_written == 0:
            raise OSError("Zero bytes written during file write.")
        total_written += chunk_written
    os.fsync(fd)


def _atomic_write_new(target: Path, content: str) -> None:
    """Atomically create a new file with content, failing if target already exists.

    Uses tempfile.mkstemp in the same directory, writes bytes, requests fsync,
    preserves standard creation permissions (0o666 & ~umask), and installs to
    target via os.link (atomic no-overwrite on POSIX).
    Unlinks tempfile in finally.

    Concurrency Note:
        os.umask() temporarily modifies process-global state while reading the
        active umask. This is acceptable for our single-threaded agent
        runtime, but would require architectural revision in a multithreaded process.
    """
    parent = target.parent
    encoded = content.encode("utf-8")
    fd, temp_path_str = tempfile.mkstemp(
        dir=parent, prefix=f".tmp_{target.name}_", text=False
    )
    temp_path = Path(temp_path_str)
    try:
        try:
            _write_bytes_to_fd(fd, encoded)
        finally:
            os.close(fd)

        # Preserve standard open(x) creation permissions filtered by active umask.
        # Do not swallow chmod failures: if permission preservation fails,
        # abort before linking to prevent installing restrictive 0600 mode.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(temp_path, stat.S_IMODE(0o666 & ~umask))

        os.link(temp_path, target)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _atomic_write_replace(target: Path, content: str) -> None:
    """Atomically replace target file with content.

    Preserves permission bits via stat.S_IMODE(target.stat().st_mode).
    Uses tempfile.mkstemp in the same directory, writes bytes, requests fsync,
    and atomically replaces target via os.replace.
    Unlinks tempfile in finally if replace did not succeed.
    """
    parent = target.parent
    encoded = content.encode("utf-8")

    original_mode: int | None = None
    try:
        original_mode = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        pass

    fd, temp_path_str = tempfile.mkstemp(
        dir=parent, prefix=f".tmp_{target.name}_", text=False
    )
    temp_path = Path(temp_path_str)
    committed = False
    try:
        try:
            _write_bytes_to_fd(fd, encoded)
        finally:
            os.close(fd)

        if original_mode is not None:
            os.chmod(temp_path, original_mode)

        os.replace(temp_path, target)
        committed = True
    finally:
        if not committed:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass


def _extract_str(
    arguments: Mapping[str, object], key: str, allow_empty: bool = False
) -> tuple[str | None, str | None]:
    """Extract and validate a string argument.

    Returns:
        (value, error_message)
    """
    if key not in arguments:
        return None, f"Missing required argument '{key}'."
    val = arguments[key]
    if not isinstance(val, str):
        return None, f"Argument '{key}' must be a string."
    if not allow_empty and not val.strip():
        return None, f"Argument '{key}' cannot be empty."
    return val, None


class CreateDirectoryTool:
    """Tool to create directories inside the workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._spec = ToolSpec(
            name="create_directory",
            description="Create a directory at the specified path inside the workspace. Missing parent directories are created automatically if needed. Operation is idempotent.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the directory to create relative to the workspace root.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            is_mutating=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        path, err = _extract_str(arguments, "path")
        if err or path is None:
            return ToolResult(content=err or "Invalid path argument.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        try:
            target = self._workspace.resolve(path)
        except WorkspaceBoundaryError as e:
            return ToolResult(content=str(e), is_error=True, error_code=ErrorCode.BOUNDARY_VIOLATION)

        if target.exists() and not target.is_dir():
            return ToolResult(
                content=f"Cannot create directory '{path}': target exists and is a file.",
                is_error=True,
                error_code=ErrorCode.CONFLICT,
            )

        already_existed = target.is_dir()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ToolResult(content=f"Failed to create directory '{path}': {e}", is_error=True, error_code=ErrorCode.TRANSIENT_ERROR)

        # H4 Postcondition verification
        if not target.is_dir():
            return ToolResult(
                content=f"Postcondition check failed: directory '{path}' was not created.",
                is_error=True,
                error_code=ErrorCode.INTERNAL_ERROR,
            )

        msg = f"Directory '{path}' already exists." if already_existed else f"Directory '{path}' created successfully."
        return ToolResult(content=msg, is_error=False)


class CreateFileTool:
    """Tool to create new UTF-8 text files without overwriting existing files."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._spec = ToolSpec(
            name="create_file",
            description="Create a new UTF-8 text file with content inside the workspace. Fails if the file already exists or if parent directory is missing.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to create relative to the workspace root.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write into the new file.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            is_mutating=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        path, err = _extract_str(arguments, "path")
        if err or path is None:
            return ToolResult(content=err or "Invalid path argument.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        content, err = _extract_str(arguments, "content", allow_empty=True)
        if err or content is None:
            return ToolResult(content=err or "Invalid content argument.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        try:
            target = self._workspace.resolve(path)
        except WorkspaceBoundaryError as e:
            return ToolResult(content=str(e), is_error=True, error_code=ErrorCode.BOUNDARY_VIOLATION)

        parent = target.parent
        if not parent.exists():
            return ToolResult(
                content=f"Cannot create file '{path}': parent directory does not exist.",
                is_error=True,
                error_code=ErrorCode.NOT_FOUND,
            )
        if not parent.is_dir():
            return ToolResult(
                content=f"Cannot create file '{path}': parent is not a directory.",
                is_error=True,
                error_code=ErrorCode.CONFLICT,
            )

        if target.exists():
            return ToolResult(
                content=f"Cannot create file '{path}': file already exists.",
                is_error=True,
                error_code=ErrorCode.ALREADY_EXISTS,
            )

        try:
            _atomic_write_new(target, content)
        except FileExistsError:
            return ToolResult(
                content=f"Cannot create file '{path}': file already exists.",
                is_error=True,
                error_code=ErrorCode.ALREADY_EXISTS,
            )
        except OSError as e:
            return ToolResult(content=f"Failed to create file '{path}': {e}", is_error=True, error_code=ErrorCode.TRANSIENT_ERROR)

        # H4 Postcondition verification (detection of false-success state)
        try:
            if not target.is_file() or target.read_text(encoding="utf-8") != content:
                return ToolResult(
                    content=f"Postcondition check failed: file '{path}' content on disk does not match expected content.",
                    is_error=True,
                    error_code=ErrorCode.INTERNAL_ERROR,
                )
        except OSError as e:
            return ToolResult(
                content=f"Postcondition check failed: unable to verify file '{path}' after creation: {e}",
                is_error=True,
                error_code=ErrorCode.INTERNAL_ERROR,
            )

        return ToolResult(content=f"File '{path}' created successfully.", is_error=False)


class ReadFileTool:
    """Tool to read UTF-8 text file contents."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._spec = ToolSpec(
            name="read_file",
            description="Read the UTF-8 text contents of a file inside the workspace. Optionally returns version token for optimistic conflict detection.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to read relative to the workspace root.",
                    },
                    "include_version": {
                        "type": "boolean",
                        "description": "If true, prepends a 64-character SHA-256 version token for optimistic conflict detection.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            is_mutating=False,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        path, err = _extract_str(arguments, "path")
        if err or path is None:
            return ToolResult(content=err or "Invalid path argument.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        include_version = arguments.get("include_version", False)
        if not isinstance(include_version, bool):
            return ToolResult(
                content="Argument 'include_version' must be a boolean.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            )

        try:
            target = self._workspace.resolve(path)
        except WorkspaceBoundaryError as e:
            return ToolResult(content=str(e), is_error=True, error_code=ErrorCode.BOUNDARY_VIOLATION)

        if not target.exists():
            return ToolResult(content=f"Cannot read file '{path}': file does not exist.", is_error=True, error_code=ErrorCode.NOT_FOUND)

        if not target.is_file():
            return ToolResult(
                content=f"Cannot read '{path}': path is a directory, not a regular file.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                content=f"Cannot read file '{path}': file is not valid UTF-8 text.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            )
        except OSError as e:
            return ToolResult(content=f"Failed to read file '{path}': {e}", is_error=True, error_code=ErrorCode.TRANSIENT_ERROR)

        if include_version:
            try:
                version = _compute_file_sha256(target)
                content = f"[Version: {version}]\n{content}"
            except OSError as e:
                return ToolResult(
                    content=f"Failed to compute version for file '{path}': {e}",
                    is_error=True,
                    error_code=ErrorCode.TRANSIENT_ERROR,
                )

        return ToolResult(content=content, is_error=False)


class ListDirectoryTool:
    """Tool to list entries in a workspace directory."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._spec = ToolSpec(
            name="list_directory",
            description="List immediate files and subdirectories at a given path inside the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the directory to list relative to the workspace root (defaults to '.').",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
            is_mutating=False,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        raw_path = arguments.get("path", ".")
        if not isinstance(raw_path, str):
            return ToolResult(content="Argument 'path' must be a string.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        path = raw_path.strip() if raw_path.strip() else "."

        try:
            target = self._workspace.resolve(path)
        except WorkspaceBoundaryError as e:
            return ToolResult(content=str(e), is_error=True, error_code=ErrorCode.BOUNDARY_VIOLATION)

        if not target.exists():
            return ToolResult(
                content=f"Cannot list directory '{path}': directory does not exist.",
                is_error=True,
                error_code=ErrorCode.NOT_FOUND,
            )

        if not target.is_dir():
            return ToolResult(
                content=f"Cannot list '{path}': path is a file, not a directory.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            )

        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            return ToolResult(content=f"Failed to list directory '{path}': {e}", is_error=True, error_code=ErrorCode.TRANSIENT_ERROR)

        if not entries:
            return ToolResult(content="Directory is empty.", is_error=False)

        lines: list[str] = []
        for entry in entries:
            prefix = "DIR" if entry.is_dir() else "FILE"
            lines.append(f"{prefix} {entry.name}")

        return ToolResult(content="\n".join(lines), is_error=False)


class SearchFilesTool:
    """Tool to recursively search text inside files under a workspace directory or within a specific file."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._spec = ToolSpec(
            name="search_files",
            description="Search regular UTF-8 text files for matching text under a workspace directory or within a specific file.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text query to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path of the directory or file to search within, relative to workspace root (defaults to '.').",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return (default 50, range 1-500).",
                    },
                    "output_format": {
                        "type": "string",
                        "description": "Output representation format: 'text' (default) returns matching lines with truncation trailer; 'json' returns structured SearchResult.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            is_mutating=False,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        query, err = _extract_str(arguments, "query")
        if err or query is None:
            return ToolResult(content=err or "Invalid query argument.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        raw_path = arguments.get("path", ".")
        if not isinstance(raw_path, str):
            return ToolResult(content="Argument 'path' must be a string.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        path = raw_path.strip() if raw_path.strip() else "."

        # Semantic validation: max_results
        max_results = arguments.get("max_results", MAX_SEARCH_RESULTS)
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            return ToolResult(content="Argument 'max_results' must be an integer.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)
        if not (1 <= max_results <= 500):
            return ToolResult(content="Argument 'max_results' must be an integer between 1 and 500.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        # Semantic validation: output_format
        output_format = arguments.get("output_format", "text")
        if not isinstance(output_format, str):
            return ToolResult(content="Argument 'output_format' must be a string.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)
        if output_format not in {"text", "json"}:
            return ToolResult(content="Argument 'output_format' must be 'text' or 'json'.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        try:
            target = self._workspace.resolve(path)
        except WorkspaceBoundaryError as e:
            return ToolResult(content=str(e), is_error=True, error_code=ErrorCode.BOUNDARY_VIOLATION)

        if not target.exists():
            return ToolResult(content=f"Cannot search '{path}': path does not exist.", is_error=True, error_code=ErrorCode.NOT_FOUND)

        if target.is_file():
            if target.is_symlink():
                return ToolResult(content=f"Cannot search '{path}': symlinks are not supported.", is_error=True, error_code=ErrorCode.BOUNDARY_VIOLATION)
            results: list[str] = []
            total_count = 0
            count_complete = True
            try:
                rel_path = target.resolve(strict=False).relative_to(self._workspace.root).as_posix()
            except ValueError:
                rel_path = path

            try:
                with open(target, "r", encoding="utf-8") as f:
                    for line_num, line in enumerate(f, start=1):
                        if query in line:
                            total_count += 1
                            if len(results) < max_results:
                                stripped_line = line.rstrip("\r\n")
                                results.append(f"{rel_path}:{line_num}: {stripped_line}")
                            if total_count >= MAX_COUNTED_MATCHES:
                                count_complete = False
                                break
            except UnicodeDecodeError:
                return ToolResult(content=f"Cannot search '{path}': file is not valid UTF-8 text.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)
            except OSError as e:
                return ToolResult(content=f"Failed to read file '{path}': {e}", is_error=True, error_code=ErrorCode.TRANSIENT_ERROR)

            search_result = SearchResult(
                matches=results,
                returned_count=len(results),
                total_count=total_count,
                truncated=total_count > len(results),
                count_complete=count_complete,
            )
            content = search_result.to_json() if output_format == "json" else search_result.format_text()
            return ToolResult(content=content, is_error=False)

        if not target.is_dir():
            return ToolResult(content=f"Cannot search '{path}': path is not a regular file or directory.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        results: list[str] = []
        total_count = 0
        count_complete = True
        for root_dir, dirs, files in os.walk(target, followlinks=False):
            dirs[:] = [
                name
                for name in dirs
                if not (Path(root_dir) / name).is_symlink()
            ]
            dirs.sort(key=str.lower)
            files.sort(key=str.lower)
            for filename in files:
                file_path = Path(root_dir) / filename
                if file_path.is_symlink() or not file_path.is_file():
                    continue

                try:
                    rel_path = file_path.resolve(strict=False).relative_to(self._workspace.root).as_posix()
                except ValueError:
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for line_num, line in enumerate(f, start=1):
                            if query in line:
                                total_count += 1
                                if len(results) < max_results:
                                    stripped_line = line.rstrip("\r\n")
                                    results.append(f"{rel_path}:{line_num}: {stripped_line}")
                                if total_count >= MAX_COUNTED_MATCHES:
                                    count_complete = False
                                    break
                except (UnicodeDecodeError, OSError):
                    continue

                if total_count >= MAX_COUNTED_MATCHES:
                    break
            if total_count >= MAX_COUNTED_MATCHES:
                break

        search_result = SearchResult(
            matches=results,
            returned_count=len(results),
            total_count=total_count,
            truncated=total_count > len(results),
            count_complete=count_complete,
        )
        content = search_result.to_json() if output_format == "json" else search_result.format_text()
        return ToolResult(content=content, is_error=False)


class ModifyFileTool:
    """Tool to modify an existing UTF-8 text file by replacing a unique exact text match."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._spec = ToolSpec(
            name="modify_file",
            description="Modify an existing UTF-8 text file by replacing an exact, unique text match with new text. Optionally verifies expected_version to reject stale modifications.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to modify relative to the workspace root.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to be replaced. Must appear exactly once in the file.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "New text to substitute in place of old_text.",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "Optional 64-character SHA-256 version token from a prior read. If file changed on disk, rejects with CONFLICT.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
            is_mutating=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: Mapping[str, object]) -> ToolResult:
        path, err = _extract_str(arguments, "path")
        if err or path is None:
            return ToolResult(content=err or "Invalid path argument.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        old_text, err = _extract_str(arguments, "old_text")
        if err or old_text is None:
            return ToolResult(content=err or "Argument 'old_text' must be a non-empty string.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        new_text, err = _extract_str(arguments, "new_text", allow_empty=True)
        if err or new_text is None:
            return ToolResult(content=err or "Argument 'new_text' must be a string.", is_error=True, error_code=ErrorCode.INVALID_ARGUMENT)

        raw_expected_version = arguments.get("expected_version")
        expected_version: str | None = None
        if raw_expected_version is not None:
            if not isinstance(raw_expected_version, str):
                return ToolResult(
                    content="Argument 'expected_version' must be a string.",
                    is_error=True,
                    error_code=ErrorCode.INVALID_ARGUMENT,
                )
            expected_version = raw_expected_version.strip().lower()
            if not _is_valid_sha256(expected_version):
                return ToolResult(
                    content="Argument 'expected_version' must be a valid 64-character hexadecimal SHA-256 hash.",
                    is_error=True,
                    error_code=ErrorCode.INVALID_ARGUMENT,
                )

        try:
            target = self._workspace.resolve(path)
        except WorkspaceBoundaryError as e:
            return ToolResult(content=str(e), is_error=True, error_code=ErrorCode.BOUNDARY_VIOLATION)

        if not target.exists():
            return ToolResult(content=f"Cannot modify file '{path}': file does not exist.", is_error=True, error_code=ErrorCode.NOT_FOUND)

        if not target.is_file():
            return ToolResult(
                content=f"Cannot modify '{path}': path is a directory, not a regular file.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            )

        # H6 Optimistic Conflict Detection
        if expected_version is not None:
            try:
                current_version = _compute_file_sha256(target)
            except OSError as e:
                return ToolResult(
                    content=f"Failed to read file '{path}' for version verification: {e}",
                    is_error=True,
                    error_code=ErrorCode.TRANSIENT_ERROR,
                )
            if current_version.lower() != expected_version:
                return ToolResult(
                    content=(
                        f"Conflict detected for file '{path}': file content on disk has changed since observation. "
                        f"Expected version '{expected_version}', but current version is '{current_version}'."
                    ),
                    is_error=True,
                    error_code=ErrorCode.CONFLICT,
                )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                content=f"Cannot modify file '{path}': file is not valid UTF-8 text.",
                is_error=True,
                error_code=ErrorCode.INVALID_ARGUMENT,
            )
        except OSError as e:
            return ToolResult(content=f"Failed to read file '{path}' for modification: {e}", is_error=True, error_code=ErrorCode.TRANSIENT_ERROR)

        count = content.count(old_text)
        if count == 0:
            return ToolResult(
                content=f"Modification rejected: old_text not found in '{path}'.",
                is_error=True,
                error_code=ErrorCode.NOT_FOUND,
            )
        if count > 1:
            return ToolResult(
                content=(
                    f"Modification rejected: target text occurs {count} times in '{path}'. "
                    "Provide a more specific old_text value to disambiguate the target."
                ),
                is_error=True,
                error_code=ErrorCode.AMBIGUOUS,
            )

        updated_content = content.replace(old_text, new_text, 1)
        try:
            _atomic_write_replace(target, updated_content)
        except OSError as e:
            return ToolResult(content=f"Failed to write modifications to file '{path}': {e}", is_error=True, error_code=ErrorCode.TRANSIENT_ERROR)

        # H4 Postcondition verification (detection of false-success state)
        try:
            if not target.is_file() or target.read_text(encoding="utf-8") != updated_content:
                return ToolResult(
                    content=f"Postcondition check failed: file '{path}' content on disk does not match expected modifications.",
                    is_error=True,
                    error_code=ErrorCode.INTERNAL_ERROR,
                )
        except OSError as e:
            return ToolResult(
                content=f"Postcondition check failed: unable to verify file '{path}' after modification: {e}",
                is_error=True,
                error_code=ErrorCode.INTERNAL_ERROR,
            )

        return ToolResult(content=f"File '{path}' modified successfully.", is_error=False)
