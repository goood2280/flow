"""SplitTable → Inform 스냅샷: 500 방어와 plan 항목 담당 모듈(팀) 추천."""

import json
import math

from app_v2.modules.informs import splittable_embed
from routers import informs


def _req(rows, **kw):
    view = {
        "headers": ["#1", "#2"],
        "rows": rows,
        "root_lot_id": "A1000",
        "wafer_fab_list": ["A1000A.1", "A1000A.1"],
    }
    return informs.SplitTableSnapshotReq(
        product="PRODA", lot_id="A1000", custom_cols=[r["_param"] for r in rows],
        is_fab_lot=False, current_view=view, **kw,
    )


def _isolate(monkeypatch, *, modules=("GATE", "PC", "STI"), metas=None, knob_map=None, users=None):
    monkeypatch.setattr(informs, "_load_config", lambda: {"modules": list(modules)})
    monkeypatch.setattr(informs, "_load_step_metas", lambda product: metas or {"knob": {}, "inline": {}, "vm": {}})
    monkeypatch.setattr(informs, "_load_module_knob_map", lambda: knob_map or {})
    users = users or {}
    monkeypatch.setattr(informs, "_module_recipient_rows",
                        lambda mod: [{"username": u, "email": f"{u}@x.com"} for u in users.get(mod, [])])
    monkeypatch.setattr(splittable_embed, "_plans_for_roots", lambda *a, **k: {})


def test_plan_targets_prefers_screen_then_matching_then_knob_map(monkeypatch):
    _isolate(
        monkeypatch,
        metas={"knob": {"KNOB_EPI": {"modules": ["pc"]}}, "inline": {}, "vm": {}},
        knob_map={"STI": ["STI_DEPTH"]},
        users={"GATE": ["kim"], "PC": ["lee", "kim"], "STI": ["park"]},
    )
    rows = [
        {"_param": "KNOB_GATE_DOSE", "_process_columns": {"module": "gate"}, "_cells": {"0": {"plan": "A"}}},
        {"_param": "KNOB_EPI", "_cells": {"1": {"actual": "X", "plan": "Y"}}},
        {"_param": "KNOB_STI_DEPTH", "_cells": {"0": {"plan": "3"}}},
        {"_param": "KNOB_NOPLAN", "_process_columns": {"module": "GATE"}, "_cells": {"0": {"actual": "Z"}}},
        {"_param": "KNOB_UNKNOWN", "_cells": {"0": {"plan": "Q"}}},
    ]
    out = informs._snapshot_plan_targets(_req(rows))
    by_mod = {m["module"]: m for m in out["modules"]}
    assert by_mod["GATE"]["params"] == ["KNOB_GATE_DOSE"] and by_mod["GATE"]["sources"] == ["screen"]
    assert by_mod["PC"]["params"] == ["KNOB_EPI"] and by_mod["PC"]["sources"] == ["matching"]
    assert by_mod["STI"]["params"] == ["KNOB_STI_DEPTH"] and by_mod["STI"]["sources"] == ["knob_map"]
    assert [r["username"] for r in by_mod["PC"]["recipients"]] == ["lee", "kim"]
    # plan 없는 행은 대상이 아니고, 근거 없는 plan 은 따로 알린다.
    assert "KNOB_NOPLAN" not in out["plan_params"]
    assert out["unmapped_params"] == ["KNOB_UNKNOWN"]


def test_plan_targets_name_fallback_only_on_exact_module_token(monkeypatch):
    _isolate(monkeypatch)
    rows = [
        {"_param": "KNOB_PC_DOSE", "_cells": {"0": {"plan": "1"}}},
        {"_param": "KNOB_PCX_DOSE", "_cells": {"0": {"plan": "1"}}},
    ]
    out = informs._snapshot_plan_targets(_req(rows))
    assert [(m["module"], m["params"], m["sources"]) for m in out["modules"]] == [("PC", ["KNOB_PC_DOSE"], ["name"])]
    assert out["unmapped_params"] == ["KNOB_PCX_DOSE"]


def test_snapshot_endpoint_returns_plan_targets_and_survives_stage_failure(monkeypatch):
    _isolate(monkeypatch, users={"GATE": ["kim"]})
    monkeypatch.setattr(informs, "current_user", lambda request: {"username": "t", "role": "admin"})
    informs._SPLITTABLE_SNAPSHOT_CACHE.clear()

    def boom(embed):
        raise KeyError("broken meta")

    monkeypatch.setattr(informs, "_convert_splittable_embed_to_split_check", boom)
    rows = [{"_param": "KNOB_GATE_DOSE", "_cells": {"0": {"actual": float("nan"), "plan": "A"}}}]
    res = informs.splittable_snapshot(_req(rows, display_mode="split_check"), request=None)
    assert res["ok"] and res["embed"]["st_view"]["rows"]
    assert res["warnings"] and "Split 체크" in res["warnings"][0]
    assert res["plan_targets"]["modules"][0]["module"] == "GATE"
    # NaN 이 섞여도 응답은 표준 JSON 으로 직렬화돼야 한다 (예전엔 500).
    json.dumps(res, allow_nan=False)
    cached = informs.splittable_snapshot(_req(rows, display_mode="split_check"), request=None)
    assert cached["cached"] and cached["plan_targets"] == res["plan_targets"]


def test_json_safe_snapshot_replaces_non_finite_values():
    out = informs._json_safe_snapshot({"a": [1.5, math.nan, math.inf], "b": {"c": (1, 2)}, 3: {"x"}})
    assert out == {"a": [1.5, None, None], "b": {"c": [1, 2]}, "3": ["x"]}


def test_format_split_cell_value_keeps_huge_numbers_instead_of_raising():
    assert splittable_embed.format_split_cell_value("1e30", "INLINE_X", {"INLINE": 4}) == "1e30"
    assert splittable_embed.format_split_cell_value("1.25", "INLINE_X", {"INLINE": 1}) == "1.3"


def test_internal_view_load_passes_plain_defaults(monkeypatch):
    seen = {}
    import routers.splittable as st

    monkeypatch.setattr(st, "view_split", lambda **kw: seen.update(kw) or {})
    splittable_embed._load_view(product="ML_TABLE_PRODA", root_lot_id="A1000")
    assert seen["include_related"] is False and seen["cache_first"] is False and seen["request"] is None
