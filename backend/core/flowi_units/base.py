"""Flow-i Unit AI base types.

Each Feature-level Unit AI exposes declarative metadata that the Agent tab
can render as a 4-layer view:

    Data Sources       -> data_sources()
    Semantic Layer     -> semantic_bindings()
    LLM (prompt/profile) -> prompt_template_path(), llm_profile()
    Results (handler)  -> handler_entry(), handle()

M1 (this PR) registers metadata only. M2 PRs add `handle()` wiring via
delegation to existing handlers in backend/routers/*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from core.paths import PATHS


@dataclass(frozen=True)
class ColumnDoc:
    """Per-column meta for the Agent tab Semantic Layer view.

    `wiki_doc_id` (when set) points to a `schema_doc` kind wiki page under
    `data/flow-data/knowledge/wiki/schema_doc/<doc_id>.md` that holds the
    full natural-language description and rules. Inline fields are short
    summaries the Agent tab can render without a wiki lookup.
    """
    name: str
    meaning: str = ""
    unit: str = ""
    sample_values: tuple[str, ...] = ()
    wiki_doc_id: str = ""


@dataclass(frozen=True)
class DataSourceRef:
    """A data source the unit AI reads from.

    `path` is for display in the Agent tab; resolution at runtime is the
    handler's responsibility. Kind values: parquet, csv, json, duckdb,
    cache, fab_db, ml_table, runtime_data.

    `description` documents what THIS FILE/DB is. `columns` documents what
    each column means. The Semantic Layer reads these to deepen term
    resolution before workflow execution.
    """
    kind: str
    path: str
    description: str = ""
    columns: tuple[ColumnDoc, ...] = ()


@dataclass(frozen=True)
class SemanticBindings:
    """Semantic Layer items this unit AI depends on.

    Used by the Agent tab to show which schema_relations / column_catalog /
    knowledge graph / wiki documents the unit AI's term resolution and
    filter generation read from.
    """
    relation_ids: tuple[str, ...] = ()
    column_catalog_keys: tuple[str, ...] = ()
    graph_node_ids: tuple[str, ...] = ()
    wiki_doc_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodeRef:
    """Display-only pointer to the handler function.

    `module` uses the on-disk path style (e.g. "backend.routers.llm") so the
    Agent tab can render `backend/routers/llm.py:lineno`. This is metadata
    only — never used for dynamic import.
    """
    module: str
    function: str
    lineno: int = 0
    description: str = ""

    @property
    def file_path(self) -> str:
        return self.module.replace(".", "/") + ".py"


@runtime_checkable
class UnitAI(Protocol):
    """One Feature-level Unit AI of Flow-i."""

    def key(self) -> str: ...
    def title(self) -> str: ...
    def feature_md_path(self) -> Path: ...
    def prompt_template_path(self) -> Optional[Path]: ...
    def data_sources(self) -> list[DataSourceRef]: ...
    def semantic_bindings(self) -> SemanticBindings: ...
    def llm_profile(self) -> str: ...
    def handler_entry(self) -> CodeRef: ...
    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Try to handle the prompt.

        Return a result dict on success (must be compatible with the
        `_attach_flowi_trace` contract in backend/routers/llm.py).
        Return None to defer to the next dispatcher level.
        """
        ...


class BaseUnitAI:
    """Default declarative-metadata base.

    Subclasses override class attributes and (in M2) the `handle` method.
    M1's `handle` always returns None so the dispatcher falls through to
    the existing `_run_flowi_chat` if/elif chain.
    """

    KEY: str = ""
    TITLE: str = ""
    PROMPT_TEMPLATE_PATH: Optional[Path] = None
    LLM_PROFILE: str = "internal_gpt_oss_120b"
    DATA_SOURCES: tuple[DataSourceRef, ...] = ()
    SEMANTIC_BINDINGS: SemanticBindings = SemanticBindings()
    HANDLER_ENTRY: CodeRef = CodeRef(module="", function="")

    def key(self) -> str:
        return self.KEY

    def title(self) -> str:
        return self.TITLE or self.KEY

    def feature_md_path(self) -> Path:
        return PATHS.data_root / "flowi_agent_features" / f"{self.KEY}.md"

    def prompt_template_path(self) -> Optional[Path]:
        return self.PROMPT_TEMPLATE_PATH

    def data_sources(self) -> list[DataSourceRef]:
        return list(self.DATA_SOURCES)

    def semantic_bindings(self) -> SemanticBindings:
        return self.SEMANTIC_BINDINGS

    def llm_profile(self) -> str:
        return self.LLM_PROFILE

    def handler_entry(self) -> CodeRef:
        return self.HANDLER_ENTRY

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
