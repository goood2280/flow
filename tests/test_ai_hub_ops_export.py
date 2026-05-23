from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from pathlib import Path

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def test_ai_hub_ops_export_builds_obsidian_vault(monkeypatch):
    from core import ai_hub_ops_export

    monkeypatch.setattr(ai_hub_ops_export.ai_hub_readiness, "build_readiness", lambda username="", days=30: {
        "generated_at": "2099-01-01T00:00:00+00:00",
        "score": 82,
        "level": "good",
        "checks": [{"key": "tool_catalog", "label": "도구", "score": 100, "detail": "ok"}],
        "backlog": [{"severity": "medium", "title": "Wiki 보강", "target": "filebrowser", "detail": "missing refs"}],
    })
    monkeypatch.setattr(ai_hub_ops_export.ai_hub_deep_eval, "load_latest_report", lambda: {
        "status": "pass",
        "generated_at": "2099-01-01T00:00:00+00:00",
        "path": "reports/flowi_agent_deep_eval_latest.json",
        "summary": {"passed": 131, "failed": 0, "total": 131},
        "groups": {"semantic": {"passed": 108, "failed": 0, "total": 108}},
        "failed_results": [],
        "result_samples": [{"name": "semantic/step_id simple question", "group": "semantic", "ok": True}],
    })
    monkeypatch.setattr(ai_hub_ops_export.ai_hub_timeline, "build_timeline", lambda days=30, limit=30, category="": {
        "days": days,
        "items": [{
            "timestamp": "2099-01-01T00:01:00+00:00",
            "category": "workflow",
            "username": "alice",
            "title": "Lot step 확인",
            "detail": "dry_run:1",
        }],
    })
    monkeypatch.setattr(ai_hub_ops_export.ai_hub_workflow_map, "export_workflow_map", lambda **kwargs: {
        "format": "obsidian",
        "files": [{"path": "Flow AI Hub Workflow Map.md", "body": "# Flow AI Hub Workflow Map\n"}],
    })

    out = ai_hub_ops_export.build_obsidian_export(username="alice", days=7, limit=10, reference_limit=30, focus_tag="knob")

    assert out["format"] == "obsidian_ops"
    assert out["counts"]["readiness_backlog"] == 1
    paths = [row["path"] for row in out["files"]]
    assert paths[:4] == [
        "Flow AI Hub Operations.md",
        "operations/readiness.md",
        "operations/deep-eval.md",
        "operations/timeline.md",
    ]
    assert "Flow AI Hub Workflow Map.md" in paths
    index = out["files"][0]["body"]
    assert "[[operations/readiness|Readiness]]" in index
    assert "[[Flow AI Hub Workflow Map|Workflow Map]]" in index

    archive = ai_hub_ops_export.export_obsidian_zip(out)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert "operations/readiness.md" in zf.namelist()
        assert "Wiki 보강" in zf.read("operations/readiness.md").decode("utf-8")
        assert "semantic/step_id simple question" in zf.read("operations/deep-eval.md").decode("utf-8")


def test_ai_hub_ops_export_builds_n8n_operations_workflow(monkeypatch):
    from core import ai_hub_ops_export

    monkeypatch.setattr(ai_hub_ops_export.ai_hub_readiness, "build_readiness", lambda username="", days=30: {
        "score": 74,
        "level": "attention",
        "checks": [{"label": "워크플로우", "score": 50, "detail": "needs validation"}],
        "backlog": [{"severity": "high", "title": "검증 필요", "target": "ops_lot_step_review", "detail": "no recent run", "action": "dry-run"}],
    })
    monkeypatch.setattr(ai_hub_ops_export.ai_hub_deep_eval, "load_latest_report", lambda: {
        "status": "fail",
        "path": "reports/flowi_agent_deep_eval_latest.json",
        "summary": {"passed": 130, "failed": 1, "total": 131},
    })
    monkeypatch.setattr(ai_hub_ops_export.ai_hub_timeline, "build_timeline", lambda days=30, limit=30, category="": {
        "days": days,
        "items": [{"category": "validation", "title": "Agent deep-eval 재검증", "username": "alice"}],
    })
    monkeypatch.setattr(ai_hub_ops_export.ai_hub_workflow_map, "build_workflow_map", lambda **kwargs: {
        "counts": {"tools_visible": 3, "tools_total": 4, "workflow_templates_visible": 1, "nodes": 10, "edges": 9},
        "warnings": [{"key": "workflow_unverified"}],
        "nodes": [{"id": "tool:filebrowser"}],
    })

    out = ai_hub_ops_export.build_n8n_export(username="alice", days=7, limit=12, focus_tag="knob")

    assert out["format"] == "n8n_ops"
    workflow = out["workflow"]
    assert workflow["name"] == "Flow AI Hub operations"
    node_ids = {node["id"] for node in workflow["nodes"]}
    assert {"ops:index", "ops:readiness", "ops:deep_eval", "ops:timeline", "ops:workflow_map", "ops:backlog:1"} <= node_ids
    assert workflow["connections"]["ops:index"]["main"][0][0]["node"] == "ops:readiness"
    assert workflow["connections"]["ops:readiness"]["main"][0][0]["node"] == "ops:deep_eval"
    assert any(row["node"] == "ops:backlog:1" for row in workflow["connections"]["ops:readiness"]["main"][0])
    assert workflow["staticData"]["readiness_score"] == 74
    assert workflow["staticData"]["deep_eval_status"] == "fail"


def test_ai_hub_ops_export_download_endpoint_streams_zip(monkeypatch):
    from routers import ai_hub

    def fake_build_obsidian_export(username="", days=30, limit=40, reference_limit=160, focus_tag=""):
        assert username == "alice"
        assert days == 7
        assert limit == 9
        assert reference_limit == 30
        assert focus_tag == "knob"
        return {
            "format": "obsidian_ops",
            "files": [{"path": "Flow AI Hub Operations.md", "body": "# Ops"}],
        }

    monkeypatch.setattr(ai_hub.ai_hub_ops_export, "build_obsidian_export", fake_build_obsidian_export)

    response = ai_hub.ops_export_download(
        _req(),
        days=7,
        limit=9,
        reference_limit=30,
        focus_tag="knob",
    )

    assert response.media_type == "application/zip"
    assert "flow-ai-hub-operations.obsidian.zip" in response.headers["content-disposition"]
    archive = asyncio.run(_streaming_response_body(response))
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        assert zf.read("Flow AI Hub Operations.md").decode("utf-8") == "# Ops"


def test_ai_hub_ops_export_download_endpoint_streams_n8n_json(monkeypatch):
    import json

    from routers import ai_hub

    def fake_build_n8n_export(username="", days=30, limit=40, focus_tag=""):
        assert username == "alice"
        assert days == 7
        assert limit == 9
        assert focus_tag == "knob"
        return {
            "format": "n8n_ops",
            "filename": "flow-ai-hub-operations.n8n.json",
            "workflow": {"name": "Flow AI Hub operations", "nodes": [], "connections": {}},
        }

    monkeypatch.setattr(ai_hub.ai_hub_ops_export, "build_n8n_export", fake_build_n8n_export)

    response = ai_hub.ops_export_download(
        _req(),
        format="n8n",
        days=7,
        limit=9,
        reference_limit=30,
        focus_tag="knob",
    )

    assert response.media_type == "application/json"
    assert "flow-ai-hub-operations.n8n.json" in response.headers["content-disposition"]
    payload = json.loads(asyncio.run(_streaming_response_body(response)).decode("utf-8"))
    assert payload["format"] == "n8n_ops"
    assert payload["workflow"]["name"] == "Flow AI Hub operations"


async def _streaming_response_body(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8"))
    return b"".join(chunks)


class _State:
    user = {"username": "alice", "role": "admin"}


class _Req:
    state = _State()
    headers = {}


def _req():
    return _Req()
