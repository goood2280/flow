"""Flow-i workflow catalog.

Runtime workflows live in FLOW_DATA_ROOT so admins can extend them without
editing code. The bundled JSON only supplies default templates and setup seeds.
"""
from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json, jsonl_append


DEFAULT_CATALOG_FILE = Path(__file__).with_name("flowi_workflow_defaults.json")
RUNTIME_CATALOG_FILE = PATHS.data_root / "flowi_workflows.json"
CHANGE_LOG_FILE = PATHS.data_root / "flowi_workflows.changes.jsonl"

WORKFLOW_SCHEMA_VERSION = 1
DEFAULT_TARGET_COUNT = 50
DEFAULT_RESULT_CONTRACT = {"type": "text", "download": False}
KNOWN_SOURCE_ROLES = {
    "rulebook",
    "step_matching",
    "split_base",
    "fab_db",
    "inline_db",
    "et_db",
    "vm_db",
}
KNOWN_UNIT_AIS = {
    "splittable",
    "fab_reference",
    "ppid_knob",
    "step_lookup",
    "filebrowser_ai_sql",
    "dashboard_agent",
    "home_sql_join_dashboard",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_id(value: Any, prefix: str = "wf") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9가-힣._-]+", "_", text).strip("._-")
    if not text:
        text = uuid.uuid4().hex[:10]
    if not text.startswith(prefix + "_"):
        text = f"{prefix}_{text}"
    return text[:96]


def _string_list(value: Any, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    out: list[str] = []
    for item in raw:
        text = _text(item, 240)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _slot_list(value: Any) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    slots: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = re.sub(r"[^A-Za-z0-9_]+", "_", str(item.get("name") or "").strip()).strip("_")
        if not name:
            continue
        slot = {
            "name": name[:80],
            "type": _text(item.get("type") or "text", 80),
            "required": bool(item.get("required")),
        }
        for key in ("example", "default", "match", "description"):
            text = _text(item.get(key), 240)
            if text:
                slot[key] = text
        slots.append(slot)
    return slots[:20]


def _slot(name: str, typ: str, *, required: bool = True, example: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "type": typ, "required": required}
    if example:
        row["example"] = example
    return row


def _question_template(value: Any, examples: list[str], slots: list[dict[str, Any]]) -> str:
    explicit = _text(value, 300)
    if explicit:
        return explicit
    template = ""
    best_placeholder_count = -1
    for example in examples:
        placeholder_count = len(re.findall(r"\{[A-Za-z0-9_]+\}", example or ""))
        if placeholder_count > best_placeholder_count:
            template = example
            best_placeholder_count = placeholder_count
    template = template or (examples[0] if examples else "")
    for slot in slots:
        name = str(slot.get("name") or "").strip()
        if not name:
            continue
        placeholder = "{" + name + "}"
        if placeholder in template:
            continue
        for key in ("example", "default"):
            sample = str(slot.get(key) or "").strip()
            if sample:
                template = re.sub(re.escape(sample), placeholder, template, flags=re.I)
    return _text(template, 300)


def _normalize_source_roles(value: Any) -> list[str]:
    roles = []
    for role in _string_list(value, limit=12):
        clean = re.sub(r"[^A-Za-z0-9_]+", "_", role.strip().lower()).strip("_")
        if clean and clean not in roles:
            roles.append(clean)
    return roles


def normalize_workflow(raw: dict[str, Any], *, actor: str = "", base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a stable workflow object that is safe to persist and show."""
    raw = raw if isinstance(raw, dict) else {}
    base = deepcopy(base) if isinstance(base, dict) else {}
    title = _text(raw.get("title") or base.get("title") or raw.get("id") or "Flow-i workflow", 160)
    workflow_id = _safe_id(raw.get("id") or base.get("id") or title)
    examples = _string_list(raw.get("examples") if "examples" in raw else base.get("examples"), limit=12)
    trigger_terms = _string_list(raw.get("trigger_terms") if "trigger_terms" in raw else base.get("trigger_terms"), limit=24)
    steps = _string_list(raw.get("steps") if "steps" in raw else base.get("steps"), limit=12)
    slots = _slot_list(raw.get("slots") if "slots" in raw else base.get("slots"))
    question_template = _question_template(
        raw.get("question_template") if "question_template" in raw else base.get("question_template"),
        examples,
        slots,
    )
    orchestration = _string_list(raw.get("orchestration") if "orchestration" in raw else base.get("orchestration"), limit=12) or steps
    source_roles = _normalize_source_roles(raw.get("source_roles") if "source_roles" in raw else base.get("source_roles"))
    unit_ai = _text(raw.get("unit_ai") or base.get("unit_ai") or "filebrowser_ai_sql", 80)
    action = _text(raw.get("action") or base.get("action") or unit_ai, 120)
    category = _text(raw.get("category") or base.get("category") or unit_ai, 80)
    result_contract = raw.get("result_contract") if isinstance(raw.get("result_contract"), dict) else base.get("result_contract")
    if not isinstance(result_contract, dict):
        result_contract = deepcopy(DEFAULT_RESULT_CONTRACT)
    try:
        priority = int(raw.get("priority", base.get("priority", 50)))
    except (TypeError, ValueError):
        priority = 50
    now = _now()
    created_at = str(base.get("created_at") or raw.get("created_at") or now)
    updated_by = _text(actor or raw.get("updated_by") or base.get("updated_by") or "system", 80)
    return {
        "id": workflow_id,
        "title": title,
        "enabled": bool(raw.get("enabled", base.get("enabled", True))),
        "priority": max(0, min(1000, priority)),
        "category": category,
        "unit_ai": unit_ai,
        "action": action,
        "examples": examples,
        "question_template": question_template,
        "trigger_terms": trigger_terms,
        "slots": slots,
        "source_roles": source_roles,
        "steps": steps,
        "orchestration": orchestration,
        "result_contract": deepcopy(result_contract),
        "created_at": created_at,
        "updated_at": now,
        "updated_by": updated_by,
    }


def _generated_default_variants() -> list[dict[str, Any]]:
    """Supplement the seed JSON with similar Home Flow-i scenarios."""
    variants: list[dict[str, Any]] = []

    def add(
        suffix: str,
        title: str,
        category: str,
        unit_ai: str,
        action: str,
        examples: list[str],
        trigger_terms: list[str],
        slots: list[dict[str, Any]],
        source_roles: list[str],
        result_type: str,
        steps: list[str],
        priority: int,
        *,
        download: bool = False,
    ) -> None:
        variants.append({
            "id": f"wf_auto_{suffix}",
            "title": title,
            "enabled": True,
            "priority": priority,
            "category": category,
            "unit_ai": unit_ai,
            "action": action,
            "examples": examples,
            "trigger_terms": trigger_terms,
            "slots": slots,
            "source_roles": source_roles,
            "steps": steps,
            "result_contract": {"type": result_type, "download": download},
        })

    split_steps = [
        "root_lot_id, wafer_id, knob_name 후보를 분리한다.",
        "SplitTable source와 rulebook source를 read-only로 확인한다.",
        "Home에 인라인 표 또는 SplitTable block으로 정리한다.",
    ]
    add("split_wafer_detail", "Root lot wafer별 SplitTable 상세", "splittable", "splittable", "show_split_table_wafer", ["{root_lot_id} #{wafer_id} split detail 보여줘"], ["split", "wafer", "detail"], [_slot("root_lot_id", "lot", example="A1001"), _slot("wafer_id", "wafer", example="#3")], ["split_base", "rulebook"], "split_view", split_steps, 94)
    add("split_knob_compare", "두 Knob 조건 비교", "splittable", "splittable", "compare_knob_conditions", ["{root_lot_id} {knob_name}와 {knob_name_2} 비교해줘"], ["compare", "비교", "knob"], [_slot("root_lot_id", "lot", example="A1001"), _slot("knob_name", "knob", example="1.0 STI"), _slot("knob_name_2", "knob", example="1.1 STI")], ["split_base", "rulebook"], "table", split_steps, 93)
    add("split_plan_actual_mismatch", "Plan actual mismatch 조회", "splittable", "splittable", "find_plan_actual_mismatch", ["{root_lot_id} plan actual 안 맞는 split 찾아줘"], ["mismatch", "불일치", "actual", "plan"], [_slot("root_lot_id", "lot", example="A1001")], ["split_base", "rulebook"], "table", split_steps, 92)
    add("split_custom_set", "CUSTOM SET 기준 Split 조회", "splittable", "splittable", "show_custom_set_split", ["{root_lot_id} CUSTOM SET {split_set} 기준으로 보여줘"], ["custom set", "set", "세트"], [_slot("root_lot_id", "lot", example="A1001"), _slot("split_set", "split_set", example="SET_A")], ["split_base", "rulebook"], "split_view", split_steps, 91)
    add("split_mask_fab_columns", "MASK/FAB 컬럼 중심 Split 조회", "splittable", "splittable", "show_mask_fab_split_columns", ["{root_lot_id} MASK FAB split 컬럼만 정리해줘"], ["mask", "fab", "split"], [_slot("root_lot_id", "lot", example="A1001")], ["split_base"], "table", split_steps, 90)
    add("split_lot_summary", "Root lot Split 요약", "splittable", "splittable", "summarize_split_lot", ["{root_lot_id} split 요약만 알려줘"], ["summary", "요약", "split"], [_slot("root_lot_id", "lot", example="A1001")], ["split_base"], "text", split_steps, 89)

    file_steps = [
        "요청에서 product, source, filter, selected column을 추출한다.",
        "FileBrowser AI SQL read-only 계약으로 preview 또는 export를 만든다.",
        "표와 다운로드 가능 여부를 Home 응답에 붙인다.",
    ]
    add("filebrowser_filtered_preview", "조건 필터 preview table", "filebrowser", "filebrowser_ai_sql", "preview_filtered_rows", ["{product} {root_lot_id} 조건 raw data 표로 보여줘"], ["raw data", "preview", "표"], [_slot("product", "product", example="PRODA"), _slot("root_lot_id", "lot", example="A1001")], ["split_base"], "table", file_steps, 88)
    add("filebrowser_selected_columns", "선택 컬럼 SQL preview", "filebrowser", "filebrowser_ai_sql", "preview_selected_columns", ["{product} {columns} 컬럼만 SQL로 보여줘"], ["sql", "columns", "컬럼"], [_slot("product", "product", example="PRODA"), _slot("columns", "columns", example="root_lot_id,wafer_id")], ["split_base"], "table", file_steps, 87)
    add("filebrowser_latest_fab_rows", "FAB latest row preview", "filebrowser", "filebrowser_ai_sql", "preview_latest_fab_rows", ["{root_lot_id} latest FAB row 표로 보여줘"], ["latest", "fab", "row"], [_slot("root_lot_id", "lot", example="A1001")], ["fab_db"], "table", file_steps, 86)
    add("filebrowser_inline_item_sql", "INLINE item SQL 초안", "filebrowser", "filebrowser_ai_sql", "draft_inline_item_sql", ["{product} {item_id} INLINE SQL 초안 만들어줘"], ["inline", "item", "sql"], [_slot("product", "product", example="PRODA"), _slot("item_id", "item", example="CA_BCD")], ["inline_db"], "table", file_steps, 85)
    add("filebrowser_root_lot_file_preview", "Root lot source file preview", "filebrowser", "filebrowser_ai_sql", "preview_root_lot_source_file", ["{root_lot_id} 관련 source file preview"], ["source file", "preview", "root_lot"], [_slot("root_lot_id", "lot", example="A1001")], ["split_base", "fab_db"], "table", file_steps, 84)
    add("filebrowser_guarded_csv_export", "Wide table guarded CSV export", "raw_export", "filebrowser_ai_sql", "export_guarded_csv", ["{product} {knob_name} 결과 csv로 내려받게 해줘"], ["csv", "download", "다운로드"], [_slot("product", "product", example="PRODA"), _slot("knob_name", "knob", example="1.0 STI")], ["split_base"], "table", file_steps, 83, download=True)

    chart_steps = [
        "축/그룹/집계 후보를 semantic layer에서 정리한다.",
        "Dashboard Agent가 chart_result shape로 검증한다.",
        "Home에서 Plotly chart와 raw data export 경로를 함께 보여준다.",
    ]
    add("chart_inline_trend", "INLINE item trend chart", "chart", "dashboard_agent", "plot_inline_item_trend", ["{product} {item_id} inline trend 차트"], ["inline", "trend", "차트"], [_slot("product", "product", example="PRODA"), _slot("item_id", "item", example="CA_BCD")], ["inline_db"], "chart", chart_steps, 82)
    add("chart_et_box", "ET item box chart", "chart", "dashboard_agent", "plot_et_item_box", ["{product} {item_id} ET box plot"], ["et", "box", "plot"], [_slot("product", "product", example="PRODA"), _slot("item_id", "item", example="PCCB_CHAIN")], ["et_db"], "chart", chart_steps, 81)
    add("chart_split_knob_distribution", "Knob 분포 bar chart", "chart", "dashboard_agent", "plot_knob_distribution", ["{product} {knob_name} 분포 bar chart"], ["bar", "distribution", "분포", "knob"], [_slot("product", "product", example="PRODA"), _slot("knob_name", "knob", example="1.0 STI")], ["split_base"], "chart", chart_steps, 80)
    add("chart_fab_progress_timeline", "FAB progress timeline", "chart", "dashboard_agent", "plot_fab_progress_timeline", ["{root_lot_id} fab progress timeline 차트"], ["fab", "timeline", "progress"], [_slot("root_lot_id", "lot", example="A1001")], ["fab_db"], "chart", chart_steps, 79)
    add("chart_inline_et_correlation", "INLINE ET correlation scatter", "chart_join", "home_sql_join_dashboard", "plot_inline_et_correlation", ["{product} {inline_item}와 {et_item} correlation scatter"], ["correlation", "corr", "scatter"], [_slot("product", "product", example="PRODA"), _slot("inline_item", "item", example="CA_BCD"), _slot("et_item", "item", example="PCCB_CHAIN")], ["inline_db", "et_db"], "chart", chart_steps, 78)
    add("chart_wafer_map", "Wafer map chart", "chart", "dashboard_agent", "plot_wafer_map", ["{root_lot_id} {item_id} wafer map 그려줘"], ["wafer map", "map", "wf map"], [_slot("root_lot_id", "lot", example="A1001"), _slot("item_id", "item", example="CA_BCD")], ["inline_db"], "chart", chart_steps, 77)
    add("chart_group_by_knob", "Knob별 group chart", "chart_join", "home_sql_join_dashboard", "plot_metric_by_knob", ["{product} {item_id} 값을 {knob_name}별로 차트"], ["group", "by", "knob", "별"], [_slot("product", "product", example="PRODA"), _slot("item_id", "item", example="CA_BCD"), _slot("knob_name", "knob", example="1.0 STI")], ["split_base", "inline_db"], "chart", chart_steps, 76)
    add("chart_product_compare", "제품별 trend 비교", "chart", "dashboard_agent", "plot_product_compare_trend", ["{item_id} 제품별 trend 비교 차트"], ["product", "compare", "trend"], [_slot("item_id", "item", example="CA_BCD")], ["inline_db", "et_db"], "chart", chart_steps, 75)
    add("chart_raw_data_followup", "Chart raw data follow-up", "raw_export", "filebrowser_ai_sql", "export_chart_raw_data", ["방금 차트 raw data csv로 줘"], ["raw data", "chart session", "csv"], [_slot("chart_session_id", "chart_session", required=False)], ["split_base", "inline_db", "et_db"], "table", chart_steps, 74, download=True)

    step_steps = [
        "matching/rulebook CSV source를 read-only로 연다.",
        "step_id, function_step, PPID, feature_name 후보를 정규화한다.",
        "근거 source와 후보 표를 함께 반환한다.",
    ]
    add("step_function_to_ids", "Function step to step_id", "step_matching", "step_lookup", "lookup_function_step_ids", ["{function_step}의 step_id가 뭐야"], ["function_step", "step_id", "공정"], [_slot("function_step", "step", example="SD_EPI")], ["step_matching"], "table", step_steps, 73)
    add("step_vehicle_matching", "Vehicle matching step 설명", "step_matching", "step_lookup", "lookup_vehicle_matching_step", ["Vehicle_matching에서 {step_id} 설명 알려줘"], ["vehicle_matching", "step_desc"], [_slot("step_id", "step_id", example="AA100090")], ["step_matching"], "table", step_steps, 72)
    add("rulebook_feature_to_steps", "Feature name 영향 step_id", "rulebook", "ppid_knob", "lookup_feature_step_ids", ["{knob_name}이 어떤 step_id에 영향 받냐"], ["feature_name", "영향", "step_id"], [_slot("knob_name", "knob", example="3.0 VTN")], ["rulebook", "step_matching"], "table", step_steps, 71)
    add("rulebook_ppid_classify", "PPID value knob 분류", "rulebook", "ppid_knob", "classify_ppid_knob", ["{ppid}는 어떤 knob으로 분류돼"], ["ppid", "분류", "knob"], [_slot("ppid", "ppid", example="PPID_03_0")], ["rulebook"], "table", step_steps, 70)
    add("rulebook_rule_order", "Rule order 확인", "rulebook", "ppid_knob", "inspect_rule_order", ["{knob_name} rule_order 순서 보여줘"], ["rule_order", "룰", "order"], [_slot("knob_name", "knob", example="3.0 VTN")], ["rulebook"], "table", step_steps, 69)
    add("step_unknown_candidate", "Unknown step 후보 source 안내", "step_matching", "step_lookup", "explain_unknown_step_search", ["{step_id} 못 찾으면 어디서 확인해?"], ["unknown", "못 찾", "source"], [_slot("step_id", "step_id", example="AA999999")], ["step_matching"], "text", step_steps, 68)

    fab_steps = [
        "FAB progress source와 latest cache 후보를 고른다.",
        "lot/wafer/step 조건으로 read-only 조회한다.",
        "현재 위치와 근거 row 수를 표 또는 텍스트로 정리한다.",
    ]
    add("fab_latest_step_root_lot", "Root lot latest step", "fab", "fab_reference", "query_latest_step_by_root_lot", ["{root_lot_id} latest step 알려줘"], ["latest step", "현재 step"], [_slot("root_lot_id", "lot", example="A1001")], ["fab_db", "step_matching"], "table", fab_steps, 67)
    add("fab_equipment_history", "Equipment history 조회", "fab", "fab_reference", "query_equipment_history", ["{root_lot_id} #{wafer_id} equipment history"], ["equipment", "history", "장비"], [_slot("root_lot_id", "lot", example="A1001"), _slot("wafer_id", "wafer", example="#3")], ["fab_db"], "table", fab_steps, 66)
    add("fab_step_eta", "Step 도착 예상/체류 조회", "fab", "fab_reference", "query_step_eta", ["{root_lot_id} {step_id} 언제 도착해?"], ["eta", "도착", "체류"], [_slot("root_lot_id", "lot", example="A1001"), _slot("step_id", "step_id", example="AA100090")], ["fab_db", "step_matching"], "text", fab_steps, 65)
    add("fab_lot_to_root_wafer", "Fab lot to root/wafer mapping", "fab", "fab_reference", "map_fab_lot_to_root_wafer", ["{fab_lot_id}가 어떤 root lot wafer야"], ["fab_lot", "root", "wafer"], [_slot("fab_lot_id", "lot", example="F1001")], ["fab_db"], "table", fab_steps, 64)
    add("fab_current_location_batch", "여러 wafer 현재 위치", "fab", "fab_reference", "query_batch_current_location", ["{root_lot_id} wafer 전체 현재 위치 표"], ["wafer 전체", "현재 위치", "표"], [_slot("root_lot_id", "lot", example="A1001")], ["fab_db", "step_matching"], "table", fab_steps, 63)

    measurement_steps = [
        "semantic measurement term을 source_type, item_id, spec으로 해석한다.",
        "관련 INLINE/ET source를 read-only로 조회한다.",
        "wafer별 표 또는 chart_result로 정리한다.",
    ]
    add("measure_inline_value_by_wafer", "INLINE semantic term wafer value", "measurement", "filebrowser_ai_sql", "query_semantic_measurement", ["{root_lot_id} CA BCD 값 wafer별로 보여줘"], ["값", "측정값", "wafer"], [_slot("root_lot_id", "lot", example="A1001"), _slot("measurement_term", "semantic_measurement", example="CA BCD")], ["inline_db"], "table", measurement_steps, 62)
    add("measure_et_median", "ET semantic term median", "measurement", "filebrowser_ai_sql", "query_et_semantic_measurement", ["{root_lot_id} PCCB Chain median 값"], ["median", "ET", "PCCB"], [_slot("root_lot_id", "lot", example="A1001"), _slot("measurement_term", "semantic_measurement", example="PCCB Chain")], ["et_db"], "table", measurement_steps, 61)
    add("measure_spec_check", "Measurement spec check", "measurement", "filebrowser_ai_sql", "check_measurement_spec", ["{root_lot_id} {measurement_term} spec out 여부"], ["spec", "target", "out"], [_slot("root_lot_id", "lot", example="A1001"), _slot("measurement_term", "semantic_measurement", example="CA BCD")], ["inline_db", "et_db"], "table", measurement_steps, 60)
    add("measure_trend_chart", "Measurement trend chart", "measurement", "dashboard_agent", "plot_measurement_trend", ["{measurement_term} 최근 trend 차트"], ["measurement", "trend", "차트"], [_slot("measurement_term", "semantic_measurement", example="CA BCD")], ["inline_db", "et_db"], "chart", measurement_steps, 59)
    add("measure_source_evidence", "Measurement source evidence", "measurement", "filebrowser_ai_sql", "explain_measurement_source", ["{measurement_term} 어떤 DB/ITEM에서 가져와?"], ["source", "item_id", "어떤 DB"], [_slot("measurement_term", "semantic_measurement", example="CA BCD")], ["inline_db", "et_db"], "text", measurement_steps, 58)

    add("join_split_inline_table", "Split + INLINE join table", "chart_join", "home_sql_join_dashboard", "join_split_inline_table", ["{product} {knob_name}별 {item_id} 값을 표로"], ["join", "inline", "knob", "표"], [_slot("product", "product", example="PRODA"), _slot("knob_name", "knob", example="1.0 STI"), _slot("item_id", "item", example="CA_BCD")], ["split_base", "inline_db"], "table", chart_steps, 57)
    add("join_split_et_chart", "Split + ET chart", "chart_join", "home_sql_join_dashboard", "plot_split_et_chart", ["{product} {knob_name}별 {item_id} ET 차트"], ["join", "et", "chart"], [_slot("product", "product", example="PRODA"), _slot("knob_name", "knob", example="1.0 STI"), _slot("item_id", "item", example="PCCB_CHAIN")], ["split_base", "et_db"], "chart", chart_steps, 56)
    add("join_inline_fab_progress", "INLINE value + FAB progress", "chart_join", "home_sql_join_dashboard", "join_inline_fab_progress", ["{root_lot_id} FAB step별 {item_id} inline 값"], ["fab", "inline", "step별"], [_slot("root_lot_id", "lot", example="A1001"), _slot("item_id", "item", example="CA_BCD")], ["fab_db", "inline_db"], "chart", chart_steps, 55)
    add("join_et_inline_corr_table", "ET INLINE correlation table", "chart_join", "home_sql_join_dashboard", "table_inline_et_correlation", ["{inline_item}와 {et_item} 상관 데이터 표"], ["correlation", "상관", "표"], [_slot("inline_item", "item", example="CA_BCD"), _slot("et_item", "item", example="PCCB_CHAIN")], ["inline_db", "et_db"], "table", chart_steps, 54)
    add("join_source_needs_input", "모호한 source clarification", "chart_join", "home_sql_join_dashboard", "clarify_ambiguous_source", ["{item_id} 차트 그려줘"], ["clarify", "ambiguous", "source"], [_slot("item_id", "item", example="CA_BCD")], ["inline_db", "et_db", "split_base"], "text", chart_steps, 53)
    return variants


def _default_payload() -> dict[str, Any]:
    data = load_json(DEFAULT_CATALOG_FILE, {})
    target_count = int(data.get("default_target_count") or DEFAULT_TARGET_COUNT)
    workflows = [normalize_workflow(row, actor="default_seed") for row in data.get("workflows", []) if isinstance(row, dict)]
    existing = {str(row.get("id") or "") for row in workflows}
    for row in _generated_default_variants():
        if len(workflows) >= target_count:
            break
        normalized = normalize_workflow(row, actor="default_seed")
        if str(normalized.get("id") or "") in existing:
            continue
        existing.add(str(normalized.get("id") or ""))
        workflows.append(normalized)
    return {
        "version": int(data.get("version") or WORKFLOW_SCHEMA_VERSION),
        "default_target_count": target_count,
        "description": _text(data.get("description") or "", 500),
        "workflows": sorted(workflows, key=lambda w: (-int(w.get("priority") or 0), str(w.get("id") or ""))),
    }


def default_workflows() -> list[dict[str, Any]]:
    return deepcopy(_default_payload()["workflows"])


def _catalog_payload(workflows: list[dict[str, Any]], *, created_at: str = "", updated_by: str = "system") -> dict[str, Any]:
    defaults = _default_payload()
    return {
        "version": WORKFLOW_SCHEMA_VERSION,
        "default_target_count": defaults.get("default_target_count") or DEFAULT_TARGET_COUNT,
        "description": defaults.get("description") or "",
        "created_at": created_at or _now(),
        "updated_at": _now(),
        "updated_by": updated_by,
        "workflows": sorted(workflows, key=lambda w: (-int(w.get("priority") or 0), str(w.get("id") or ""))),
    }


def ensure_runtime_catalog(*, actor: str = "setup.py") -> dict[str, Any]:
    """Create or merge the runtime catalog, preserving admin-edited workflows."""
    defaults = default_workflows()
    existing = load_json(RUNTIME_CATALOG_FILE, None)
    if not isinstance(existing, dict) or not isinstance(existing.get("workflows"), list):
        payload = _catalog_payload(defaults, updated_by=actor)
        save_json(RUNTIME_CATALOG_FILE, payload, indent=2)
        return {**deepcopy(payload), "installed_defaults": len(defaults), "preserved": 0}

    by_id = {
        str(row.get("id") or ""): normalize_workflow(row, actor=str(row.get("updated_by") or actor))
        for row in existing.get("workflows", [])
        if isinstance(row, dict) and row.get("id")
    }
    added = 0
    refreshed = 0
    for row in defaults:
        workflow_id = str(row.get("id") or "")
        if workflow_id and workflow_id not in by_id:
            by_id[workflow_id] = row
            added += 1
        elif workflow_id and str((by_id.get(workflow_id) or {}).get("updated_by") or "") in {"default_seed", "setup.py", "runtime"}:
            by_id[workflow_id] = row
            refreshed += 1
    payload = _catalog_payload(list(by_id.values()), created_at=str(existing.get("created_at") or ""), updated_by=actor)
    payload["description"] = existing.get("description") or payload.get("description") or ""
    save_json(RUNTIME_CATALOG_FILE, payload, indent=2)
    return {**deepcopy(payload), "installed_defaults": added, "refreshed_defaults": refreshed, "preserved": len(by_id) - added - refreshed}


def load_catalog(*, ensure: bool = True) -> dict[str, Any]:
    if ensure:
        ensure_runtime_catalog(actor="runtime")
    data = load_json(RUNTIME_CATALOG_FILE, {})
    if not isinstance(data, dict) or not isinstance(data.get("workflows"), list):
        data = _catalog_payload(default_workflows())
    data = deepcopy(data)
    data["workflows"] = [
        normalize_workflow(row, actor=str(row.get("updated_by") or "runtime"))
        for row in data.get("workflows", [])
        if isinstance(row, dict)
    ]
    data["workflows"] = sorted(data["workflows"], key=lambda w: (-int(w.get("priority") or 0), str(w.get("id") or "")))
    data["path"] = str(RUNTIME_CATALOG_FILE)
    data["default_path"] = str(DEFAULT_CATALOG_FILE)
    return data


def save_workflow(workflow: dict[str, Any], *, actor: str = "admin") -> dict[str, Any]:
    catalog = load_catalog(ensure=True)
    existing = {str(row.get("id") or ""): row for row in catalog.get("workflows", []) if isinstance(row, dict)}
    workflow_id = str(workflow.get("id") or "").strip()
    base = existing.get(workflow_id) if workflow_id else None
    normalized = normalize_workflow(workflow, actor=actor, base=base)
    existing[normalized["id"]] = normalized
    payload = _catalog_payload(list(existing.values()), created_at=str(catalog.get("created_at") or ""), updated_by=actor)
    save_json(RUNTIME_CATALOG_FILE, payload, indent=2)
    jsonl_append(CHANGE_LOG_FILE, {
        "action": "save_workflow",
        "actor": actor,
        "workflow_id": normalized["id"],
        "title": normalized["title"],
    })
    return normalized


def _infer_unit(prompt: str) -> tuple[str, str, str, list[str], list[str]]:
    text = prompt.casefold()
    if any(t in text for t in ("split", "스플릿", "knob", "노브")):
        return "splittable", "show_split_table", "splittable", ["split_base", "rulebook"], ["split", "스플릿", "knob", "노브"]
    if any(t in text for t in ("어디", "current location", "fab", "progress", "진행")):
        return "fab_reference", "query_current_location", "fab", ["fab_db", "step_matching"], ["어디", "current location", "fab", "progress"]
    if any(t in text for t in ("rule", "룰", "ppid")):
        return "ppid_knob", "query_knob_rulebook", "rulebook", ["rulebook"], ["rule", "룰", "ppid", "knob"]
    if re.search(r"\b[A-Z]{2}\d{6}\b", prompt, re.I):
        return "step_lookup", "lookup_step_desc", "step_matching", ["step_matching"], ["step", "step_desc", "function step"]
    if any(t in text for t in ("corr", "correlation", "상관")):
        return "home_sql_join_dashboard", "plot_cross_source_corr_chart", "chart_join", ["split_base"], ["corr", "correlation", "상관", "scatter"]
    if any(t in text for t in ("측정값", "값", "몇이야", "measurement")):
        return "filebrowser_ai_sql", "query_semantic_measurement", "measurement", ["inline_db", "et_db"], ["값", "측정값", "몇이야", "measurement"]
    if any(t in text for t in ("trend", "차트", "그래프", "chart", "plot", "추이")):
        return "dashboard_agent", "plot_item_trend", "chart", ["split_base"], ["trend", "차트", "그래프", "chart"]
    if any(t in text for t in ("raw data", "csv", "다운로드", "공유")):
        return "filebrowser_ai_sql", "export_raw_data", "raw_export", ["split_base"], ["raw data", "csv", "다운로드", "공유"]
    return "filebrowser_ai_sql", "query_data", "data_query", ["split_base"], []


def _infer_slots(prompt: str) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    text = prompt or ""

    def add(name: str, typ: str, required: bool = True, example: str = "") -> None:
        if any(row["name"] == name for row in slots):
            return
        row: dict[str, Any] = {"name": name, "type": typ, "required": required}
        if example:
            row["example"] = example
        slots.append(row)

    if re.search(r"\b[A-Z][A-Z0-9]?\d{3,}[A-Z0-9_.-]*\b", text, re.I):
        add("root_lot_id", "lot", True, "A1001")
    if re.search(r"(?:#|WF|WAFER)\s*\d{1,2}\b", text, re.I):
        add("wafer_id", "wafer", True, "#3")
    if re.search(r"\b[A-Z]{2}\d{6}\b", text, re.I):
        add("step_id", "step_id", True, "AA100250")
    if re.search(r"\b[A-Z]{3,}[A-Z0-9_]*\b", text) and any(t in text.casefold() for t in ("raw", "trend", "corr", "product", "제품")):
        add("product", "product", False, "PROD")
    if any(t in text.casefold() for t in ("knob", "노브", "split", "스플릿", "rule", "룰")):
        add("knob_name", "knob", True, "1.0 STI")
    if any(t in text.casefold() for t in ("trend", "차트", "그래프", "corr", "inline", "et", "vm")):
        add("item_id", "item", True, "15.0 M2")
    if any(t in text for t in ("값", "측정값", "몇이야")):
        add("measurement_term", "semantic_measurement", True, "CA BCD")
    if not slots:
        add("natural_language", "text", True)
    return slots


def _template_prompt(prompt: str) -> str:
    text = _text(prompt, 240)
    text = re.sub(r"\b[A-Z]{2}\d{6}\b", "{step_id}", text, flags=re.I)
    text = re.sub(r"\b[A-Z][A-Z0-9]?\d{3,}[A-Z0-9_.-]*\b", "{root_lot_id}", text, flags=re.I)
    text = re.sub(r"(?:#|WF|WAFER)\s*\d{1,2}\b", "{wafer_id}", text, flags=re.I)
    return text or "{natural_language}"


def draft_workflow(prompt: str, *, base: dict[str, Any] | None = None, actor: str = "admin") -> dict[str, Any]:
    """Deterministically shape a user request into the workflow schema.

    This is the local fallback for the UI's "AI formatting" flow. It avoids
    hidden reasoning and returns only editable public fields.
    """
    prompt = _text(prompt, 1000)
    unit_ai, action, category, source_roles, triggers = _infer_unit(prompt)
    title = prompt[:80] or "새 Flow-i workflow"
    steps = [
        "질문에서 필요한 슬롯을 추출한다.",
        "semantic source catalog와 단위기능 권한을 확인한다.",
        f"{unit_ai} 단위기능으로 read-only 실행한다.",
        "결과를 표/차트/텍스트 계약에 맞춰 정리한다.",
    ]
    if base:
        title = _text(base.get("title") or title, 160)
    raw = {
        "id": (base or {}).get("id") or _safe_id(title),
        "title": title,
        "enabled": True,
        "priority": int((base or {}).get("priority") or 80),
        "category": category,
        "unit_ai": unit_ai,
        "action": action,
        "examples": [_template_prompt(prompt)],
        "trigger_terms": triggers or [w for w in re.findall(r"[A-Za-z가-힣0-9_.]{2,}", prompt)[:8]],
        "slots": _infer_slots(prompt),
        "source_roles": source_roles,
        "steps": steps,
        "result_contract": {"type": "table" if unit_ai in {"splittable", "filebrowser_ai_sql", "ppid_knob"} else ("chart" if "chart" in category else "text")},
    }
    return normalize_workflow(raw, actor=actor, base=base)


def workflow_few_shots(limit: int = 50) -> list[dict[str, Any]]:
    rows = []
    for wf in load_catalog(ensure=True).get("workflows", []):
        if not wf.get("enabled"):
            continue
        slots = wf.get("slots") if isinstance(wf.get("slots"), list) else []
        arguments = {
            str(slot.get("name")): "{" + str(slot.get("name")) + "}"
            for slot in slots
            if slot.get("name")
        }
        rows.append({
            "workflow_id": wf.get("id"),
            "function": wf.get("action") or wf.get("unit_ai"),
            "unit_ai": wf.get("unit_ai"),
            "prompt": (wf.get("examples") or [""])[0],
            "question_template": wf.get("question_template") or (wf.get("examples") or [""])[0],
            "arguments": arguments,
            "orchestration": wf.get("orchestration") or wf.get("steps") or [],
            "source_roles": wf.get("source_roles") or [],
        })
        if len(rows) >= max(1, int(limit or 50)):
            break
    return rows


def match_workflows(prompt: str, *, limit: int = 5) -> list[dict[str, Any]]:
    text = (prompt or "").casefold()
    matches: list[dict[str, Any]] = []
    for wf in load_catalog(ensure=True).get("workflows", []):
        if not wf.get("enabled"):
            continue
        workflow_id = str(wf.get("id") or "")
        if workflow_id == "wf_split_table_root_lot_knob_custom_set" and not re.search(r"\d+(?:\.\d+)+\s+[A-Za-z0-9_]+", prompt or ""):
            continue
        if workflow_id in {"wf_chart_knob_coloring_followup", "wf_chart_raw_data_followup", "wf_chart_raw_data_provenance_followup"} and not re.search(
            r"(chart_demo|chart[_ -]?session|방금|직전|위\s*(?:chart|차트)|이\s*(?:chart|차트)|해당\s*(?:chart|차트)|raw\s+data|join)",
            prompt or "",
            re.I,
        ):
            continue
        score = 0.0
        for term in wf.get("trigger_terms") or []:
            token = str(term or "").casefold().strip()
            if token and token in text:
                score += 4.0
        for example in wf.get("examples") or []:
            for token in re.findall(r"[A-Za-z가-힣0-9_.]{3,}", str(example or "").replace("{", " ").replace("}", " ")):
                if token.casefold() in text:
                    score += 0.4
        for slot in wf.get("slots") or []:
            name = str((slot or {}).get("name") or "")
            if name == "root_lot_id" and re.search(r"\b[A-Z][A-Z0-9]?\d{3,}[A-Z0-9_.-]*\b", prompt or "", re.I):
                score += 1.5
            elif name == "wafer_id" and re.search(r"(?:#|WF|WAFER)\s*\d{1,2}\b", prompt or "", re.I):
                score += 1.5
            elif name == "step_id" and re.search(r"\b[A-Z]{2}\d{6}\b", prompt or "", re.I):
                score += 2.0
            elif name == "knob_name" and (
                re.search(r"\d+(?:\.\d+)+\s+[A-Za-z0-9_]+", prompt or "")
                or re.search(r"(knob|노브|룰|rule)", prompt or "", re.I)
            ):
                score += 2.0
            elif name == "measurement_term" and re.search(r"(값|측정값|몇이야|measurement)", prompt or "", re.I):
                score += 2.0
        if workflow_id == "wf_leading_lot_by_knob_value" and re.search(r"(leading\s+lot|리딩랏|가장\s+빠른)", prompt or "", re.I):
            score += 3.0
        if score <= 0:
            continue
        if workflow_id and not workflow_id.startswith("wf_auto_"):
            score += 2.5
        if str(wf.get("id") or "") == "wf_semantic_measurement_value_lookup" and not re.search(r"\b[A-Z][A-Z0-9]?\d{3,}[A-Z0-9_.-]*\b", prompt or "", re.I):
            continue
        row = {
            "id": wf.get("id"),
            "title": wf.get("title"),
            "category": wf.get("category"),
            "unit_ai": wf.get("unit_ai"),
            "action": wf.get("action"),
            "score": round(score + (int(wf.get("priority") or 0) / 1000.0), 3),
            "source_roles": wf.get("source_roles") or [],
            "slots": wf.get("slots") or [],
            "examples": wf.get("examples") or [],
            "question_template": wf.get("question_template") or (wf.get("examples") or [""])[0],
            "steps": wf.get("steps") or [],
            "orchestration": wf.get("orchestration") or wf.get("steps") or [],
        }
        matches.append(row)
    return sorted(matches, key=lambda row: (-float(row.get("score") or 0), str(row.get("id") or "")))[: max(1, int(limit or 5))]
