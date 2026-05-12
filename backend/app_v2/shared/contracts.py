from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FlowEntityKey(BaseModel):
    """Canonical business key used to connect Flow pages.

    `lot_id` can change during processing, so it is intentionally not part of
    the stable key. Use `lot_wf` when a wafer-level string key is needed.
    """

    product: str = ""
    root_lot_id: str = ""
    wafer_id: str = ""

    @property
    def lot_wf(self) -> str:
        root = str(self.root_lot_id or "").strip()
        wafer = str(self.wafer_id or "").strip()
        return f"{root}_{wafer}" if root and wafer else ""


class FileArtifactKey(BaseModel):
    file: str
    artifact_type: Literal[
        "matching_table",
        "rulebook",
        "product_yaml",
        "reformatter",
        "db_schema_profile",
        "single_file",
    ] = "single_file"
    product: str = ""
    editable: bool = False
    versioned: bool = False


class FileVersionMeta(BaseModel):
    version: str
    file: str
    artifact_type: str = "single_file"
    actor: str = ""
    action: Literal["edit", "rollback", "pre-rollback", "legacy-backup", "system_import"] = "edit"
    created_at: str = ""
    size: int | None = None
    rows: int | None = None
    columns: int | None = None
    checksum: str = ""
    note: str = ""
    change_summary: dict[str, Any] = Field(default_factory=dict)


class SchemaProfileSnapshot(BaseModel):
    schema_version: str = ""
    source_id: str = ""
    source_type: str = ""
    root: str = ""
    product: str = ""
    file: str = ""
    columns: list[str] = Field(default_factory=list)
    column_count: int = 0
    total_rows: int | None = None
    created_at: str = ""
    actor: str = ""
    checksum: str = ""
    note: str = ""


class AgentActionProposal(BaseModel):
    action_id: str = ""
    action_type: Literal[
        "create_inform",
        "update_split_plan",
        "create_tracker_issue",
        "add_meeting_agenda",
        "send_mail",
        "run_report",
        "edit_file",
        "rollback_file",
        "save_schema_snapshot",
    ]
    target: FlowEntityKey = Field(default_factory=FlowEntityKey)
    file: str = ""
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = True
    risk_level: Literal["read", "write", "external_send", "admin_change"] = "write"


class AgentExecutionTrace(BaseModel):
    run_id: str = ""
    user: str = ""
    prompt: str = ""
    resolved_intent: str = ""
    resolved_slots: dict[str, Any] = Field(default_factory=dict)
    tool_name: str = ""
    data_source: dict[str, Any] = Field(default_factory=dict)
    permission_result: str = ""
    confirmation_id: str = ""
    status: str = ""
    created_at: str = ""


class KnowledgeEvent(BaseModel):
    event_id: str = ""
    source_type: Literal[
        "edm_version",
        "split_note",
        "issue",
        "meeting",
        "report",
        "agent_action",
        "agent_wiki_source",
        "agent_wiki_ingest",
        "manual",
        "system",
    ] = "manual"
    source_id: str = ""
    title: str = ""
    summary: str = ""
    actor: str = ""
    created_at: str = ""
    entity: FlowEntityKey = Field(default_factory=FlowEntityKey)
    tags: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_path: str = ""
    wiki_targets: list[str] = Field(default_factory=list)


class KnowledgeDoc(BaseModel):
    doc_id: str = ""
    kind: Literal[
        "product",
        "lot",
        "wafer",
        "knob",
        "issue",
        "meeting",
        "report",
        "decision",
        "agent_wiki",
        "schema_doc",
        "ontology",
        "manual",
    ] = "manual"
    title: str = ""
    summary: str = ""
    body: str = ""
    actor: str = ""
    created_at: str = ""
    updated_at: str = ""
    entity: FlowEntityKey = Field(default_factory=FlowEntityKey)
    tags: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    path: str = ""


class KnowledgeEdge(BaseModel):
    edge_id: str = ""
    source: str = ""
    target: str = ""
    relation: str = ""
    confidence: float = 1.0
    evidence: str = ""


class KnowledgeSearchResult(BaseModel):
    result_type: Literal["wiki", "event"] = "wiki"
    id: str = ""
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    path: str = ""
    entity: FlowEntityKey = Field(default_factory=FlowEntityKey)
    tags: list[str] = Field(default_factory=list)
