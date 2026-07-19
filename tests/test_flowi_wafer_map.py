"""WF map 핸들러 — spec out 파서·게이트·좌표 폴백(chip_x_adj > chip_x_pos > shot_x) 회귀."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import polars as pl  # noqa: E402
from routers import llm  # noqa: E402


def test_parse_spec_bounds():
    f = llm._parse_spec_bounds
    assert f("IOFF spec 0.5 이하 spec out map") == {"low": None, "high": 0.5, "label": "high 0.5"}
    assert f("spec 0.5 이상 spec out map")["low"] == 0.5
    assert f("spec 0.2~0.5 spec out map") == {"low": 0.2, "high": 0.5, "label": "low 0.2 / high 0.5"}
    assert f("USL 1.2e-6 spec out map")["high"] == 1.2e-6
    assert f("spec high 3 spec low 1 map") == {"low": 1.0, "high": 3.0, "label": "low 1 / high 3"}
    # 방향 없는 spec 값은 상한(USL)으로 가정
    assert f("spec 2.5 spec out map")["high"] == 2.5
    # spec out 인데 숫자 없음 → 되물음용 마커 (low/high 모두 None)
    marker = f("스펙아웃 맵 그려줘")
    assert marker == {"low": None, "high": None, "label": ""}
    # spec 언급 자체가 없으면 None
    assert f("IOFF WF map 그려줘") is None


def test_wafer_map_gate():
    f = llm._is_wafer_map_chart_request
    assert f("PRODA IOFF WF map 그려줘")
    assert f("IOFF 웨이퍼맵 보여줘")
    assert not f("heatmap 그려줘")
    assert not f("트리맵 그려줘")
    assert not f("step 매핑 보여줘")
    assert not f("tablemap 보여줘")


def _write_et_parquet(path: Path, x_col: str, y_col: str, extra_cols: dict | None = None):
    n = 6
    data = {
        "root_lot_id": ["AA111"] * n,
        "wafer_id": ["1"] * n,
        "item_id": ["IOFF"] * n,
        x_col: [float(i % 3) for i in range(n)],
        y_col: [float(i // 3) for i in range(n)],
        "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    }
    for k, v in (extra_cols or {}).items():
        data[k] = [v] * n
    pl.DataFrame(data).write_parquet(path)


def _run_map(monkeypatch, tmp_path, prompt: str, x_col: str, y_col: str, extra_cols: dict | None = None):
    fp = tmp_path / "et.parquet"
    _write_et_parquet(fp, x_col, y_col, extra_cols)
    monkeypatch.setattr(llm, "_et_files", lambda product: [fp])
    monkeypatch.setattr(llm, "_inline_files", lambda product: [])
    return llm._handle_wafer_map_chart(prompt, "PRODA", 12)


def test_wafer_map_coord_adj_preferred(monkeypatch, tmp_path):
    out = _run_map(
        monkeypatch, tmp_path, "PRODA IOFF WF map 그려줘",
        "chip_x_adj", "chip_y_adj", {"chip_x_pos": 9.0, "chip_y_pos": 9.0},
    )
    cr = out.get("chart_result") or {}
    assert out.get("intent") == "dashboard_wafer_map_chart"
    assert cr.get("coord_basis") == "chip_x_adj/chip_y_adj"
    assert cr.get("mode") == "value"
    assert cr.get("total") == 6


def test_wafer_map_coord_pos_rotated_by_flat_zone(monkeypatch, tmp_path):
    # flat_zone=90(vertical) → -90° 회전: (x,y) → (y,-x)
    out = _run_map(
        monkeypatch, tmp_path, "PRODA IOFF WF map 그려줘",
        "chip_x_pos", "chip_y_pos", {"flat_zone": 90},
    )
    cr = out.get("chart_result") or {}
    assert cr.get("coord_basis") == "chip_x_pos/chip_y_pos"
    assert "회전 보정" in str(out.get("answer"))
    got = {(p["x"], p["y"], p["value"]) for p in cr.get("points") or []}
    # 원본 (x=i%3, y=i//3, v=i+1) → 회전 (y, -x)
    expected = {(0.0, 0.0, 1.0), (0.0, -1.0, 2.0), (0.0, -2.0, 3.0), (1.0, 0.0, 4.0), (1.0, -1.0, 5.0), (1.0, -2.0, 6.0)}
    assert got == expected


def test_wafer_map_coord_pos_flat_zero_unchanged(monkeypatch, tmp_path):
    out = _run_map(
        monkeypatch, tmp_path, "PRODA IOFF WF map 그려줘",
        "chip_x_pos", "chip_y_pos", {"flat_zone": 0},
    )
    cr = out.get("chart_result") or {}
    got = {(p["x"], p["y"], p["value"]) for p in cr.get("points") or []}
    expected = {(0.0, 0.0, 1.0), (1.0, 0.0, 2.0), (2.0, 0.0, 3.0), (0.0, 1.0, 4.0), (1.0, 1.0, 5.0), (2.0, 1.0, 6.0)}
    assert got == expected


def test_wafer_map_coord_pos_without_flat_zone_notes_caveat(monkeypatch, tmp_path):
    out = _run_map(
        monkeypatch, tmp_path, "PRODA IOFF WF map 그려줘",
        "chip_x_pos", "chip_y_pos",
    )
    assert "회전 보정 없이" in str(out.get("answer"))


def test_wafer_map_spec_out_points(monkeypatch, tmp_path):
    out = _run_map(
        monkeypatch, tmp_path, "PRODA IOFF spec 4.5 이하 spec out map 그려줘",
        "shot_x", "shot_y",
    )
    cr = out.get("chart_result") or {}
    assert cr.get("mode") == "spec_out"
    assert cr.get("spec") == {"low": None, "high": 4.5, "label": "high 4.5"}
    pts = cr.get("points") or []
    assert [p["out"] for p in pts] == [False, False, False, False, True, True]
    assert cr.get("out_n") == 2
    tbl_cols = [c.get("key") if isinstance(c, dict) else c for c in (out.get("table") or {}).get("columns", [])]
    assert "out" in tbl_cols


def test_wafer_map_spec_out_needs_value(monkeypatch, tmp_path):
    out = _run_map(monkeypatch, tmp_path, "PRODA IOFF 스펙아웃 맵 그려줘", "shot_x", "shot_y")
    assert out.get("intent") == "dashboard_wafer_map_needs_context"
    assert out.get("missing") == ["spec"]


def _write_inline_parquet(path: Path, with_spec: bool):
    n = 4
    data = {
        "root_lot_id": ["AA111", "AA111", "AA222", "AA222"],
        "wafer_id": ["1", "2", "1", "2"],
        "item_id": ["SPACER_CD"] * n,
        "value": [10.0, 11.0, 12.0, 13.0],
        "tkout_time": [f"2026-07-0{i+1} 00:00:00" for i in range(n)],
    }
    if with_spec:
        data["spec_high"] = [15.0, 15.0, 16.0, 16.0]
        data["spec_low"] = [5.0, 5.0, 6.0, 6.0]
    pl.DataFrame(data).write_parquet(path)


def test_inline_trend_spec_overlay(monkeypatch, tmp_path):
    fp = tmp_path / "inline.parquet"
    _write_inline_parquet(fp, with_spec=True)
    monkeypatch.setattr(llm, "_inline_files", lambda product: [fp])
    out = llm._handle_inline_trend_chart("PRODA SPACER_CD trend 그려줘", "PRODA", 12)
    cr = out.get("chart_result") or {}
    assert out.get("intent") == "dashboard_inline_trend_chart"
    assert cr.get("spec_overlay") is True
    pts = sorted(cr.get("points") or [], key=lambda p: p["x_label"])
    assert [(p.get("spec_high"), p.get("spec_low")) for p in pts] == [(15.0, 5.0), (15.0, 5.0), (16.0, 6.0), (16.0, 6.0)]
    assert "spec" in str(out.get("answer"))


def test_inline_trend_without_spec_unchanged(monkeypatch, tmp_path):
    fp = tmp_path / "inline.parquet"
    _write_inline_parquet(fp, with_spec=False)
    monkeypatch.setattr(llm, "_inline_files", lambda product: [fp])
    out = llm._handle_inline_trend_chart("PRODA SPACER_CD trend 그려줘", "PRODA", 12)
    cr = out.get("chart_result") or {}
    assert cr.get("spec_overlay") is False
    assert all("spec_high" not in p for p in cr.get("points") or [])


def test_inform_mail_marker_and_confirm(monkeypatch):
    # 마커 파싱
    assert llm._extract_flowi_inform_mail_confirm("그냥 질문") is None
    p = llm._extract_flowi_inform_mail_confirm('FLOWI_INFORM_MAIL {"inform_ids": ["a1"], "confirm": true}')
    assert p == {"inform_ids": ["a1"], "confirm": True}
    # confirm=False → 발송 없음
    out = llm._flowi_send_inform_mail_confirmed({"inform_ids": [], "confirm": False}, {"username": "qa"})
    assert out["intent"] == "inform_mail_cancelled"
    # confirm=True → core 호출 (monkeypatch로 발송 대체)
    calls = []
    import routers.informs as informs_router

    def fake_core(iid, req, me, request=None):
        calls.append((iid, me.get("username")))
        return {"ok": True, "to": ["x@y.z"], "subject": "s", "dry_run": True}

    monkeypatch.setattr(informs_router, "_send_inform_mail_core", fake_core)
    out = llm._flowi_send_inform_mail_confirmed({"inform_ids": ["i1", "i2"], "confirm": True}, {"username": "qa"})
    assert out["intent"] == "inform_mail_sent"
    assert calls == [("i1", "qa"), ("i2", "qa")]
    assert "dry-run" in out["answer"]


def test_saved_custom_name_from_prompt(monkeypatch):
    import routers.splittable as splittable_router
    monkeypatch.setattr(splittable_router, "list_customs", lambda: {"customs": [
        {"name": "myset"}, {"name": "CD"}, {"name": "mysetlong"},
    ]})
    # 저장된 이름 그대로 언급 → 최장 일치
    assert llm._flowi_saved_custom_name_from_prompt("AA111 mysetlong 으로 보여줘") == "mysetlong"
    assert llm._flowi_saved_custom_name_from_prompt("AA111 myset 커스텀으로 보여줘") == "myset"
    # 짧은 이름(CD)은 custom 언급 없이는 매칭 안 됨 (오탐 방지)
    assert llm._flowi_saved_custom_name_from_prompt("AA111 CD 보여줘") == ""
    assert llm._flowi_saved_custom_name_from_prompt("AA111 CD custom으로 보여줘") == "CD"
    assert llm._flowi_saved_custom_name_from_prompt("AA111 스플릿 보여줘") == ""


def test_plan_assignment_range_and_value_order():
    a, invalid = llm._flowi_parse_splittable_plan_assignments("A1000 wafer 1~5에 3.0 VTN knob plan PPID_03_3_S1 넣어줘")
    assert len(a) == 1
    assert a[0]["wafers"] == ["1", "2", "3", "4", "5"]
    assert a[0]["value"] == "PPID_03_3_S1"
    # 기존 문법 회귀
    b, _ = llm._flowi_parse_splittable_plan_assignments("#1~#3 PPID_X plan 넣어줘")
    assert b[0]["wafers"] == ["1", "2", "3"] and b[0]["value"] == "PPID_X"
    c, _ = llm._flowi_parse_splittable_plan_assignments("웨이퍼 2~4는 CONDA로 plan")
    assert c[0]["wafers"] == ["2", "3", "4"] and c[0]["value"] == "CONDA"
