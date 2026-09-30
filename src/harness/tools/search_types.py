"""Domain types for search observation budgeting (Phase C / Hypothesis H7)."""

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class SearchResult:
    """Structured search outcome with complete or lower-bound match accounting."""

    matches: list[str]
    returned_count: int
    total_count: int
    truncated: bool
    count_complete: bool

    def to_dict(self) -> dict[str, object]:
        """Convert SearchResult to JSON-serializable dictionary."""
        return {
            "matches": list(self.matches),
            "returned_count": self.returned_count,
            "total_count": self.total_count,
            "truncated": self.truncated,
            "count_complete": self.count_complete,
        }

    def to_json(self) -> str:
        """Serialize SearchResult to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def format_text(self) -> str:
        """Format SearchResult as human/LLM-readable text with explicit truncation trailer."""
        if not self.matches and self.total_count == 0:
            return "No matches found."

        lines = list(self.matches)
        if self.truncated:
            if self.count_complete:
                lines.append(
                    f"[TRUNCATED: showing {self.returned_count} of {self.total_count} matches. "
                    f"Refine query or path to narrow results.]"
                )
            else:
                lines.append(
                    f"[TRUNCATED: showing {self.returned_count} of at least {self.total_count} matches; "
                    f"scan ceiling reached. Refine query or path to narrow results.]"
                )
        return "\n".join(lines)
