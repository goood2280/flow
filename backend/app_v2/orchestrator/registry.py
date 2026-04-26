from __future__ import annotations

AGENT_REGISTRY = {
    "data_adapter": {
        "title": "Data Adapter Agent",
        "purpose": "Normalize file drops / batch exports / internal APIs into canonical process tables.",
        "primary_outputs": ["canonical datasets", "source profile", "adapter warnings"],
    },
    "source_contract_review": {
        "title": "Source Contract Review Agent",
        "purpose": "Validate incoming API/file tables against expected schema contracts and flag risky gaps before analysis.",
        "primary_outputs": ["schema validation report", "missing/alias columns", "join-key warnings"],
    },
    "step_auto_classification": {
        "title": "Step Auto Classification Agent",
        "purpose": "Auto-map raw step_id values into function_step/module using matching tables first and heuristics as fallback.",
        "primary_outputs": ["step mapping suggestions", "unclassified steps", "confidence bands"],
    },
    "clean_split_review": {
        "title": "Clean Split Review Agent",
        "purpose": "Find trustworthy split candidates inside lots/modules and rank them by cleanliness.",
        "primary_outputs": ["clean split score", "contamination notes", "candidate knobs"],
    },
    "process_ml_review": {
        "title": "Process ML Review Agent",
        "purpose": "Combine feature importance with process heuristics such as incoming dominance and sign priors.",
        "primary_outputs": ["reliability-ranked features", "sign violations", "module consistency review"],
    },
    "et_report_review": {
        "title": "ET Report Review Agent",
        "purpose": "Summarize ET package/time results and surface seq-level bottlenecks and repeat patterns.",
        "primary_outputs": ["seq timeline summary", "idle gap findings", "report-ready highlights"],
    },
    "plan_generation": {
        "title": "Plan Generation Agent",
        "purpose": "Turn reviewed evidence into execution-ready knob plans tied to step_id/function_step/module.",
        "primary_outputs": ["action proposals", "step targets", "plan rationale"],
    },
    "publish_mail": {
        "title": "Mail Publisher Agent",
        "purpose": "Render the current action/report package into mail-ready content with attachments.",
        "primary_outputs": ["mail preview", "attachment manifest", "distribution target summary"],
    },
    "publish_json": {
        "title": "JSON Publisher Agent",
        "purpose": "Prepare the same action/report package as JSON for internal system API posting.",
        "primary_outputs": ["json payload", "endpoint contract", "delivery metadata"],
    },
}


def list_agents() -> list[dict]:
    out = []
    for key, meta in AGENT_REGISTRY.items():
        out.append({"agent_id": key, **meta})
    return out
