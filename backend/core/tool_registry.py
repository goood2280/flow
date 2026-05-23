"""core/tool_registry.py — Unit AI + Function-call 통합 카탈로그.

flow 본진에는 이미 두 단계 추상화가 있다:

  1. Feature-level Unit AI 11개 (core/flowi_units/registry.py)
       - DataSources, SemanticBindings, LLM_PROFILE, HANDLER_ENTRY 메타데이터 포함
       - strangler-fig dispatcher 로 prompt → 적절한 unit AI 가 handle()

  2. Function-call 16개 (routers/llm.py 의 _flowi_function_schema)
       - FLOWI_FUNCTION_FEW_SHOTS + _flowi_function_schema 의 JSON 스키마
       - 홈 Flowi 가 LLM function-calling 으로 호출하는 micro-action

이 모듈은 양쪽을 손대지 않고 **읽기 전용 통합 카탈로그**로 노출한다.
새 Tool 추가는 기존 추상화 둘 중 하나에 등록 → 자동 카탈로그 반영.

상태 저장 (enabled toggle): data_root/tool_registry_state.json
  - admin_settings.json 절대 사용 X (사용자 룰)

통계: activity.jsonl 에서 action prefix 매치 (flowi_function:<name>, unit_ai:<key>)
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS

logger = logging.getLogger("flow.tool_registry")

STATE_FILE = PATHS.data_root / "tool_registry_state.json"

# action prefix 패턴 — activity.jsonl 에서 도구 호출을 식별한다.
# 기존 audit.record() 호출이 "flowi:<func>" 또는 "unit_ai:<key>" 형태일 때 매치.
# 도구 직접 실행은 AI Hub 가 "ai_hub_run:<name>" prefix 로 기록한다.
_ACTION_PATTERNS = (
    "flowi_function:",
    "flowi:",
    "unit_ai:",
    "ai_hub_run:",
)


# ── 태그 자동 분류 ────────────────────────────────────────────────────────────
def _infer_tags_for_function(name: str) -> list[str]:
    tags: list[str] = []
    n = name.lower()
    if n.startswith("query_"):
        tags.append("read")
    if n.startswith("preview_"):
        tags.append("read")
        tags.append("draft")
    if n.startswith("register_"):
        tags.append("write")
        tags.append("draft")
    if n.startswith("compose_"):
        tags.append("draft")
    if n.startswith("build_"):
        tags.append("chart")
        tags.append("draft")
    if n.startswith("find_") or n.startswith("search_"):
        tags.append("search")
    if "splittable" in n:
        tags.append("splittable")
    if "inform" in n:
        tags.append("inform")
    if "lot" in n or "wafer" in n or "fab" in n:
        tags.append("lot")
    if "knob" in n or "mask" in n:
        tags.append("knob")
    if "metric" in n or "dashboard" in n or "chart" in n:
        tags.append("dashboard")
    if "filebrowser" in n or "schema" in n:
        tags.append("filebrowser")
    if "progress" in n or "step" in n:
        tags.append("step")
    if "tracker" in n:
        tags.append("tracker")
    if "walkthrough" in n or "route_" in n:
        tags.append("agent")
    # dedup & 정렬
    return sorted(set(tags))


def _infer_tags_for_unit_ai(key: str, title: str) -> list[str]:
    tags = [key]
    t = (title or "").lower()
    if "dashboard" in t:
        tags.append("dashboard")
    if "tracker" in t:
        tags.append("tracker")
    if "meeting" in t or "회의" in t:
        tags.append("meeting")
    if "inform" in t or "인폼" in t:
        tags.append("inform")
    if "filebrowser" in t or "파일" in t:
        tags.append("filebrowser")
    if "wafer" in t or "layout" in t:
        tags.append("wafer")
    if "diagnosis" in t or "분석" in t:
        tags.append("diagnosis")
    if "splittable" in t:
        tags.append("splittable")
    if "calendar" in t or "일정" in t:
        tags.append("calendar")
    if "ettime" in t or "et" in t:
        tags.append("ettime")
    if "tablemap" in t:
        tags.append("tablemap")
    tags.append("unit_ai")
    return sorted(set(tags))


# ── 상태 파일 (enabled toggle) ────────────────────────────────────────────────
def _load_state() -> dict[str, Any]:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("tool_registry state load failed: %s", e)
    return {"tools": {}}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("tool_registry state save failed: %s", e)


def get_enabled(name: str) -> bool:
    state = _load_state()
    entry = (state.get("tools") or {}).get(name) or {}
    if "enabled" not in entry:
        return True  # default on
    return bool(entry.get("enabled"))


def set_enabled(name: str, enabled: bool, by: str = "system") -> dict[str, Any]:
    state = _load_state()
    tools = state.setdefault("tools", {})
    tools[name] = {
        "enabled": bool(enabled),
        "by": by,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(state)
    return tools[name]


# ── 통계 (activity.jsonl 집계) ────────────────────────────────────────────────
def _read_activity_lines(days: int = 30) -> list[dict[str, Any]]:
    log = PATHS.activity_log
    if not log.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    try:
        with log.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = row.get("timestamp") or row.get("ts") or ""
                try:
                    rts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if rts.tzinfo is None:
                        rts = rts.replace(tzinfo=timezone.utc)
                except Exception:
                    rts = None
                if rts is None or rts >= cutoff:
                    out.append(row)
    except Exception as e:
        logger.warning("activity.jsonl read failed: %s", e)
    return out


def _matches_tool(action: str, tool_name: str) -> bool:
    if not action:
        return False
    for prefix in _ACTION_PATTERNS:
        if action == prefix + tool_name:
            return True
        if action.startswith(prefix) and tool_name in action:
            return True
    return False


def get_stats(name: str, days: int = 30) -> dict[str, Any]:
    rows = _read_activity_lines(days)
    matched = [r for r in rows if _matches_tool(str(r.get("action") or ""), name)]
    users = {str(r.get("username") or "") for r in matched if r.get("username")}
    last_ts = max((str(r.get("timestamp") or "") for r in matched), default="")
    return {
        "count": len(matched),
        "user_count": len(users),
        "users": sorted(users),
        "last_run": last_ts,
    }


def get_history(name: str, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
    rows = _read_activity_lines(days)
    matched = [r for r in rows if _matches_tool(str(r.get("action") or ""), name)]
    matched.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)
    return matched[:limit]


# ── Function-call 카탈로그 (FLOWI_FUNCTION_FEW_SHOTS + _flowi_function_schema) ─
def _function_titles() -> dict[str, str]:
    return {
        "query_current_fab_lot_from_fab_db": "현재 FAB Lot 조회",
        "preview_splittable_plan_update": "SplitTable plan 미리보기",
        "query_lot_knobs_from_ml_table": "ML_TABLE Knob 조회",
        "compose_inform_module_mail": "모듈 인폼 메일 초안",
        "register_inform_log": "인폼 로그 등록 초안",
        "preview_filebrowser_data": "FileBrowser 데이터 미리보기",
        "search_filebrowser_schema": "FileBrowser 스키마 검색",
        "query_wafer_split_at_step": "Step별 Wafer Split 조회",
        "query_lot_current_step_from_progress_cache": "Lot 현재 Step 조회",
        "query_splittable_view": "SplitTable View 조회",
        "find_lots_by_knob_value": "Knob 값으로 Lot 찾기",
        "query_metric_at_step": "Step별 Metric 집계",
        "register_inform_walkthrough": "인폼 walkthrough",
        "build_dashboard_metric_chart": "Dashboard 차트 빌더",
        "query_fab_progress": "FAB Progress 조회",
        "query_tracker_lot_purpose": "Tracker Lot 목적 조회",
        "route_flowi_feature": "Flow-i 기능 라우터 (fallback)",
    }


def list_function_tools() -> list[dict[str, Any]]:
    """Function-call 단위 16개 도구 정보."""
    try:
        from routers.llm import _flowi_function_schema, FLOWI_FUNCTION_FEW_SHOTS
    except Exception as e:
        logger.warning("function tool catalog load failed: %s", e)
        return []

    titles = _function_titles()
    # FLOWI_FUNCTION_FEW_SHOTS 와 schema 양쪽을 합쳐 unique name 집합
    schema_names: list[str] = []
    try:
        # _flowi_function_schema(unknown) → fallback dict (route_flowi_feature)
        # 모든 이름을 알기 위해 schema 함수 내부 dict 키를 추출
        import inspect as _inspect
        src = _inspect.getsource(_flowi_function_schema)
        for m in re.finditer(r'"([a-z_]+(?:_[a-z_]+)+)"\s*:', src):
            schema_names.append(m.group(1))
    except Exception:
        pass

    few_shot_names = [str(s.get("function") or "") for s in (FLOWI_FUNCTION_FEW_SHOTS or [])]
    name_order: list[str] = []
    seen: set[str] = set()
    for n in schema_names + few_shot_names:
        if n and n not in seen:
            seen.add(n)
            name_order.append(n)

    out: list[dict[str, Any]] = []
    for name in name_order:
        spec = _flowi_function_schema(name) or {}
        few_shots = [s for s in (FLOWI_FUNCTION_FEW_SHOTS or []) if s.get("function") == name]
        out.append({
            "kind": "function",
            "name": name,
            "title": titles.get(name) or name,
            "description": str(spec.get("description") or "").strip(),
            "required_args": list(spec.get("required") or []),
            "input_schema": spec,
            "tags": _infer_tags_for_function(name),
            "few_shot": few_shots,
            "enabled": get_enabled(name),
        })
    return out


# ── Unit AI 카탈로그 (core/flowi_units/registry) ─────────────────────────────
def _unit_ai_descriptions() -> dict[str, str]:
    return {
        "filebrowser": "Parquet/CSV/Hive 파일 미리보기 + schema 검색.",
        "meeting": "회의 일정/아젠다/회의록 작업 라우팅.",
        "inform": "인폼 로그 등록·수정·메일 발송 작업 라우팅.",
        "tracker": "Tracker 이슈/Lot 모니터링·purpose 조회.",
        "dashboard": "Dashboard 차트 세션·KNOB coloring·raw CSV 후속 처리.",
        "splittable": "SplitTable plan/actual/KNOB/MASK/FAB matrix 조회.",
        "tablemap": "테이블 관계 그래프·join path 안내.",
        "diagnosis": "DIBL/VTH/SS/ION 등 반도체 분석·RCA 후보.",
        "calendar": "일정/캘린더/변경점 일정 조회.",
        "ettime": "ET median/elapsed 측정 집계.",
        "waferlayout": "Wafer/TEG/shot/die layout 시각화.",
    }


def list_unit_ai_tools() -> list[dict[str, Any]]:
    """Feature-level Unit AI 11개 도구 정보."""
    try:
        from core.flowi_units.registry import UNIT_AIS
    except Exception as e:
        logger.warning("unit_ai catalog load failed: %s", e)
        return []

    descs = _unit_ai_descriptions()
    out: list[dict[str, Any]] = []
    for key, unit in UNIT_AIS.items():
        try:
            title = unit.title()
            data_sources = [
                {
                    "kind": ds.kind,
                    "path": ds.path,
                    "description": ds.description,
                    "columns": [
                        {"name": c.name, "meaning": c.meaning, "unit": c.unit}
                        for c in (ds.columns or ())
                    ],
                }
                for ds in (unit.data_sources() or [])
            ]
            sb = unit.semantic_bindings() or None
            semantic = {
                "relation_ids": list(getattr(sb, "relation_ids", ()) or ()),
                "column_catalog_keys": list(getattr(sb, "column_catalog_keys", ()) or ()),
                "graph_node_ids": list(getattr(sb, "graph_node_ids", ()) or ()),
                "wiki_doc_ids": list(getattr(sb, "wiki_doc_ids", ()) or ()),
            } if sb else None
            handler = unit.handler_entry() or None
            handler_ref = {
                "module": getattr(handler, "module", "") or "",
                "function": getattr(handler, "function", "") or "",
                "lineno": int(getattr(handler, "lineno", 0) or 0),
                "description": getattr(handler, "description", "") or "",
            } if handler else None
            feature_md = unit.feature_md_path()
            out.append({
                "kind": "unit_ai",
                "name": key,
                "title": title,
                "description": descs.get(key) or "",
                "llm_profile": unit.llm_profile() or "",
                "data_sources": data_sources,
                "semantic_bindings": semantic,
                "handler_ref": handler_ref,
                "feature_md": str(feature_md) if feature_md else "",
                "tags": _infer_tags_for_unit_ai(key, title),
                "enabled": get_enabled(key),
            })
        except Exception as e:
            logger.warning("unit_ai %s catalog row failed: %s", key, e)
    return out


# ── 통합 카탈로그 ────────────────────────────────────────────────────────────
def list_tools(include_stats: bool = True, days: int = 30) -> list[dict[str, Any]]:
    """Unit AI + Function 도구를 단일 카탈로그로 반환."""
    items: list[dict[str, Any]] = []
    items.extend(list_unit_ai_tools())
    items.extend(list_function_tools())
    if include_stats:
        for it in items:
            stats = get_stats(it["name"], days=days)
            it["count_30d"] = stats["count"]
            it["user_count_30d"] = stats["user_count"]
            it["last_run"] = stats["last_run"]
    return items


def get_tool(name: str, days: int = 30) -> dict[str, Any] | None:
    for it in list_tools(include_stats=False):
        if it["name"] == name:
            stats = get_stats(name, days=days)
            it["count_30d"] = stats["count"]
            it["user_count_30d"] = stats["user_count"]
            it["users_30d"] = stats["users"]
            it["last_run"] = stats["last_run"]
            return it
    return None


def all_tags() -> list[str]:
    bag: Counter[str] = Counter()
    for it in list_tools(include_stats=False):
        for t in it.get("tags") or []:
            bag[t] += 1
    return [t for t, _ in bag.most_common()]
