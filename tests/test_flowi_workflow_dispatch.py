from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import pytest

from core import flowi_workflow_templates as wf


@pytest.fixture()
def wf_dir(tmp_path, monkeypatch):
    base = tmp_path / "workflow_templates"
    base.mkdir()
    monkeypatch.setattr(wf, "_DIR", base)
    return base


def test_execute_steps_dry_run_shows_bound_slots_and_missing(wf_dir):
    template = {
        "key": "gate_inform",
        "title": "GATE inform 작성",
        "trigger": {"prompt_contains": ["GATE"], "intent_in": ["inform"]},
        "steps": [
            {"unit_ai": "filebrowser", "action": "query_current_fab_lot", "bind_slots": ["product", "lot"]},
            {"unit_ai": "inform", "action": "create_draft", "bind_slots": ["product", "lot"], "fixed_slots": {"module": "GATE"}},
        ],
    }
    result = wf.execute_steps(template, slots={"product": "PRODA"}, dry_run=True)

    assert result["workflow"] == "gate_inform"
    assert result["dry_run"] is True
    steps = result["steps"]
    assert len(steps) == 2
    # Step 0: product bound, lot missing
    assert steps[0]["bound_slots"]["product"] == "PRODA"
    assert "lot" in steps[0]["missing_slots"]
    assert steps[0]["status"] == "dry_run"
    # Step 1: module is a fixed slot, both bound + missing tracked
    assert steps[1]["bound_slots"]["module"] == "GATE"
    assert "lot" in steps[1]["missing_slots"]


def test_execute_steps_blocks_write_actions_with_confirm_required(wf_dir):
    template = {
        "key": "gate_inform",
        "title": "GATE inform 작성",
        "steps": [
            {"unit_ai": "filebrowser", "action": "query_lot", "bind_slots": ["product", "lot"]},
            {"unit_ai": "inform", "action": "create_draft", "bind_slots": ["product", "lot"]},
        ],
    }
    result = wf.execute_steps(template, slots={"product": "PRODA", "lot": "A1000"}, dry_run=False)

    assert result["confirm_required"] is True
    # First step is read-only — it gets dispatched (no_handler is fine because
    # no real unit AI registered for synthetic prompt).
    assert result["steps"][0]["status"] in {"ok", "no_handler", "error"}
    # Second step is a write action — never auto-executed.
    assert result["steps"][1]["status"] == "confirm_required"


def test_execute_steps_skips_empty_unit_or_action(wf_dir):
    template = {
        "key": "broken",
        "title": "broken",
        "steps": [
            {"unit_ai": "", "action": "noop"},
            {"unit_ai": "filebrowser", "action": ""},
        ],
    }
    result = wf.execute_steps(template, slots={}, dry_run=True)
    assert all(s["status"] == "skipped" for s in result["steps"])


def test_match_prompt_then_execute_roundtrip(wf_dir):
    # Save a template that requires both prompt token and intent.
    wf.save_template({
        "key": "split_view",
        "title": "SplitTable view",
        "trigger": {"prompt_contains": ["splittable"], "intent_in": ["knob_analysis"]},
        "steps": [
            {"unit_ai": "splittable", "action": "view_lot", "bind_slots": ["product", "lot"]},
        ],
    }, by="hol")
    matched = wf.match_prompt("splittable PRODA A1000 보여줘", intent="knob_analysis", username="hol")
    assert matched is not None
    assert matched["key"] == "split_view"

    execution = wf.execute_steps(matched, slots={"product": "PRODA", "lot": "A1000"}, dry_run=True)
    assert execution["workflow"] == "split_view"
    assert execution["steps"][0]["bound_slots"]["product"] == "PRODA"
