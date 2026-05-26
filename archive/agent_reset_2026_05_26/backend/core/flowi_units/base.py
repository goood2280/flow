"""Flow-i Unit AI base types.

Each Feature-level Unit AI exposes declarative metadata that the Agent tab
can render as a 4-layer view:

    Data Sources       -> data_sources()
    Semantic Layer     -> semantic_bindings()
    LLM (prompt/profile) -> prompt_template_path(), llm_profile()
    Results (handler)  -> handler_entry(), handle()

`tool_schema()` exposes an OpenAI function-calling compatible JSON Schema so
the home orchestrator (and any external MCP-style caller) can pick and call
unit AIs by name without a custom adapter per unit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from core.paths import PATHS

# Common input shape for unit AIs that take a free-form prompt plus product
# scope + row cap. Individual units can override INPUT_SCHEMA to add fields.
DEFAULT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "사용자 자연어 요청. 단위 AI가 자체 파싱한다.",
        },
        "product": {
            "type": "string",
            "description": "제품 코드 (예: PRODA, PRODB). 비어 있으면 단위 AI 기본값.",
            "default": "",
        },
        "max_rows": {
            "type": "integer",
            "description": "응답에 포함할 최대 row 수.",
            "default": 12,
            "minimum": 1,
            "maximum": 1000,
        },
    },
    "required": ["prompt"],
}

# Common output shape returned by handle(). Specific units may add extra keys
# (rows, chart, table) but always keep `handled` + `feature`.
DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "handled": {"type": "boolean", "description": "단위 AI가 prompt를 처리했는지 여부."},
        "feature": {"type": "string", "description": "처리한 unit AI key."},
        "text": {"type": "string", "description": "자연어 응답 본문 (있을 때)."},
        "answer": {"type": "string", "description": "요약된 한줄 답변 (있을 때)."},
        "rows": {"type": "array", "description": "표 형태 결과 (있을 때)."},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["handled"],
}


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
    def description(self) -> str: ...
    def feature_md_path(self) -> Path: ...
    def prompt_template_path(self) -> Optional[Path]: ...
    def data_sources(self) -> list[DataSourceRef]: ...
    def semantic_bindings(self) -> SemanticBindings: ...
    def llm_profile(self) -> str: ...
    def handler_entry(self) -> CodeRef: ...
    def input_schema(self) -> dict[str, Any]: ...
    def output_schema(self) -> dict[str, Any]: ...
    def examples(self) -> list[dict[str, Any]]: ...
    def tool_schema(self) -> dict[str, Any]: ...
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

    Subclasses override class attributes and the `handle` method.
    A subclass that does not implement `handle` falls through to the legacy
    `_run_flowi_chat` if/elif chain via the dispatcher.
    """

    KEY: str = ""
    TITLE: str = ""
    DESCRIPTION: str = ""
    PROMPT_TEMPLATE_PATH: Optional[Path] = None
    LLM_PROFILE: str = ""
    DATA_SOURCES: tuple[DataSourceRef, ...] = ()
    SEMANTIC_BINDINGS: SemanticBindings = SemanticBindings()
    HANDLER_ENTRY: CodeRef = CodeRef(module="", function="")
    INPUT_SCHEMA: dict[str, Any] = DEFAULT_INPUT_SCHEMA
    OUTPUT_SCHEMA: dict[str, Any] = DEFAULT_OUTPUT_SCHEMA
    EXAMPLES: tuple[dict[str, Any], ...] = ()

    def key(self) -> str:
        return self.KEY

    def title(self) -> str:
        return self.TITLE or self.KEY

    def description(self) -> str:
        return self.DESCRIPTION

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

    def input_schema(self) -> dict[str, Any]:
        return self.INPUT_SCHEMA or DEFAULT_INPUT_SCHEMA

    def output_schema(self) -> dict[str, Any]:
        return self.OUTPUT_SCHEMA or DEFAULT_OUTPUT_SCHEMA

    def examples(self) -> list[dict[str, Any]]:
        return list(self.EXAMPLES)

    def tool_schema(self) -> dict[str, Any]:
        """OpenAI function-calling 호환 도구 정의.

        반환 dict 형식은 `{type: "function", function: {name, description,
        parameters, ...}}` 의 inner `function` payload 와 동일. LLM adapter는
        `{"type": "function", "function": tool_schema()}` 으로 감싸서 사용한다.
        """
        return {
            "name": self.key(),
            "title": self.title(),
            "description": self.description() or self.title(),
            "parameters": self.input_schema(),
            "output_schema": self.output_schema(),
            "examples": self.examples(),
        }

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
