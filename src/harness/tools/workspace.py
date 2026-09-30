from pathlib import Path


class WorkspaceBoundaryError(Exception):
    """Raised when a path resolves outside the designated workspace boundary."""


class Workspace:
    """Enforces a strict filesystem boundary around a canonical workspace root."""

    def __init__(self, root: str | Path) -> None:
        if isinstance(root, str) and not root.strip():
            raise ValueError("Workspace root cannot be empty.")
        self._root = Path(root).resolve(strict=False)

    @property
    def root(self) -> Path:
        """The canonicalized root path of the workspace."""
        return self._root

    def resolve(self, path: str | Path) -> Path:
        """Resolve a user/model-supplied path against the workspace root.

        Interprets relative paths relative to workspace root. Absolute paths
        are permitted only if they resolve within the workspace root.

        Returns:
            The canonical Path inside the workspace.

        Raises:
            WorkspaceBoundaryError: If the resolved path falls outside the workspace root.
        """
        if isinstance(path, str) and not path.strip():
            candidate = self._root
        else:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self._root / candidate

        resolved = candidate.resolve(strict=False)

        if not resolved.is_relative_to(self._root):
            raise WorkspaceBoundaryError(
                f"Access denied: path '{path}' resolves to '{resolved}', "
                f"which is outside the workspace boundary '{self._root}'."
            )

        return resolved
