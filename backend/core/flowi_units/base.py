"""Archived Flow-i Unit AI compatibility types.

The previous feature-level Unit AI implementations were moved under
archive/agent_reset_2026_05_26. Keep the small type surface so non-agent
callers that import the package can fail closed instead of crashing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable


DEFAULT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"prompt": {"type": "string"}},
    "required": ["prompt"],
}

DEFAULT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"handled": {"type": "boolean"}},
    "required": ["handled"],
}


@dataclass(frozen=True)
class ColumnDoc:
    name: str
    meaning: str = ""
    unit: str = ""
    sample_values: tuple[str, ...] = ()
    wiki_doc_id: str = ""


@dataclass(frozen=True)
class DataSourceRef:
    kind: str
    path: str
    description: str = ""
    columns: tuple[ColumnDoc, ...] = ()


@dataclass(frozen=True)
class SemanticBindings:
    relation_ids: tuple[str, ...] = ()
    column_catalog_keys: tuple[str, ...] = ()
    graph_node_ids: tuple[str, ...] = ()
    wiki_doc_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CodeRef:
    module: str
    function: str
    lineno: int = 0
    description: str = ""

    @property
    def file_path(self) -> str:
        return self.module.replace(".", "/") + ".py" if self.module else ""


@runtime_checkable
class UnitAI(Protocol):
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
    def handle(self, prompt: str, slots: dict[str, Any], ctx: dict[str, Any]) -> Optional[dict[str, Any]]: ...


class BaseUnitAI:
    KEY = ""
    TITLE = ""
    DESCRIPTION = ""
    PROMPT_TEMPLATE_PATH: Optional[Path] = None
    LLM_PROFILE = ""
    DATA_SOURCES: tuple[DataSourceRef, ...] = ()
    SEMANTIC_BINDINGS = SemanticBindings()
    HANDLER_ENTRY = CodeRef(module="", function="")
    INPUT_SCHEMA = DEFAULT_INPUT_SCHEMA
    OUTPUT_SCHEMA = DEFAULT_OUTPUT_SCHEMA
    EXAMPLES: tuple[dict[str, Any], ...] = ()

    def key(self) -> str:
        return self.KEY

    def title(self) -> str:
        return self.TITLE or self.KEY

    def description(self) -> str:
        return self.DESCRIPTION

    def feature_md_path(self) -> Path:
        return Path()

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
        return self.INPUT_SCHEMA

    def output_schema(self) -> dict[str, Any]:
        return self.OUTPUT_SCHEMA

    def examples(self) -> list[dict[str, Any]]:
        return list(self.EXAMPLES)

    def tool_schema(self) -> dict[str, Any]:
        return {
            "name": self.key(),
            "title": self.title(),
            "description": self.description() or self.title(),
            "parameters": self.input_schema(),
            "output_schema": self.output_schema(),
            "examples": self.examples(),
        }

    def handle(self, prompt: str, slots: dict[str, Any], ctx: dict[str, Any]) -> Optional[dict[str, Any]]:
        return None
