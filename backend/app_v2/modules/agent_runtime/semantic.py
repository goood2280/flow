"""Minimal semantic resolver placeholder after the Agent reset."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

_ALIAS_GROUPS: dict[str, list[str]] = {}


@dataclass(frozen=True)
class SemanticCandidate:
    raw: str = ""
    normalized: str = ""
    canonical_alias: str = ""

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticFrame:
    intent: str = "archived"
    coverage: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=lambda: ["agent_runtime_archived"])
    candidates: list[SemanticCandidate] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        out = asdict(self)
        out["candidates"] = [c.model_dump() for c in self.candidates]
        return out


def _catalog_rows() -> list[dict[str, Any]]:
    return []


def resolve_semantic_frame(prompt: str = "", max_terms: int = 32, **_: Any) -> SemanticFrame:
    return SemanticFrame(slots={"prompt": str(prompt or "")[:200]})
