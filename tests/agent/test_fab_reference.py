from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import fab_reference  # noqa: E402
from core.flowi_units import deterministic_lookup_runtime as lookup_runtime  # noqa: E402
from core.flowi_units.ppid_knob import PpidKnobUnitAI  # noqa: E402
from core.flowi_units.registry import UNIT_AIS  # noqa: E402
from core.flowi_units.step_lookup import StepLookupUnitAI  # noqa: E402


STEP_ROWS = [
    {"product": "PRODA", "step_id": "AA100090", "function_step": "SD_EPI"},
    {"product": "PRODA", "step_id": "AA100160EC", "function_step": "VIA1_FORMATION_EC"},
    {"product": "PRODA", "step_id": "AA100290", "function_step": "SD_EPI"},
    {"product": "PRODA", "step_id": "AA100100", "function_step": "CONTACT_LITHO"},
    {"product": "PRODB", "step_id": "AB100090", "function_step": "SD_EPI"},
]

PPID_ROWS = [
    {"feature_name": "8.0 SD_EPI", "function_step": "SD_EPI", "rule_order": "RO", "operator": "eq", "value": "PPID_08_0", "category": "PPID_08_0"},
    {"feature_name": "24.0 SORT", "function_step": "FINAL_INSPECTION", "rule_order": "RO", "operator": "eq", "value": "PPID_24_3", "category": "PPID_24_0"},
]


class _DummyPaths:
    def __init__(self, root: Path):
        self.data_root = root / "flow-data"
        self.data_root.mkdir(parents=True, exist_ok=True)


# ── lookup_step (pure) ────────────────────────────────────────────────────────
def test_lookup_step_id_to_function_step():
    out = fab_reference.lookup_step("AA100090는 무슨 step이야", rows=STEP_ROWS)
    assert out["found"] is True
    assert out["direction"] == "id_to_step"
    assert out["matches"] == [{"product": "PRODA", "step_id": "AA100090", "function_step": "SD_EPI",
                               "step_desc": "", "vehicle": ""}]
    assert "SD_EPI" in out["answer"]


def test_lookup_step_id_with_suffix_to_function_step():
    out = fab_reference.lookup_step("AA100160EC는 무슨 step이야", rows=STEP_ROWS)
    assert out["found"] is True
    assert out["direction"] == "id_to_step"
    assert out["matches"] == [{"product": "PRODA", "step_id": "AA100160EC", "function_step": "VIA1_FORMATION_EC",
                               "step_desc": "", "vehicle": ""}]
    assert "VIA1_FORMATION_EC" in out["answer"]


def test_lookup_step_function_step_to_ids_groups_by_product():
    out = fab_reference.lookup_step("SD_EPI의 step_id가 뭐야", rows=STEP_ROWS)
    assert out["found"] is True
    assert out["direction"] == "step_to_id"
    ids = {m["step_id"] for m in out["matches"]}
    assert ids == {"AA100090", "AA100290", "AB100090"}
    assert "PRODA" in out["answer"] and "PRODB" in out["answer"]


def test_lookup_step_product_filter():
    out = fab_reference.lookup_step("SD_EPI step_id", product="PRODB", rows=STEP_ROWS)
    assert {m["step_id"] for m in out["matches"]} == {"AB100090"}


def test_lookup_step_no_match():
    out = fab_reference.lookup_step("그냥 잡담", rows=STEP_ROWS)
    assert out["found"] is False
    assert out["reason"] == "no_match"


def test_lookup_step_no_data():
    out = fab_reference.lookup_step("AA100090", rows=[])
    assert out["found"] is False
    assert out["reason"] == "no_data"


# ── suggest_similar_steps / extract_step_tokens ──────────────────────────────
def test_extract_step_tokens_shapes():
    tokens = fab_reference.extract_step_tokens("AB100000EC 하고 A00000, AA100100 확인")
    assert "AB100000EC" in tokens and "A00000" in tokens and "AA100100" in tokens
    # root lot(짧은 숫자부)은 step 토큰이 아니다.
    assert fab_reference.extract_step_tokens("A1000 조회") == []


def test_suggest_similar_steps_suffix_base_match():
    # AA100160 (suffix 없는 변형) → 같은 base 의 AA100160EC 를 후보로.
    out = fab_reference.suggest_similar_steps("AA100160", rows=STEP_ROWS)
    assert out and out[0]["step_id"] == "AA100160EC"


def test_lookup_step_in_text_miss_with_intent_returns_similar():
    got = fab_reference.lookup_step_in_text("AA100160는 무슨 step이야", rows=STEP_ROWS)
    assert got is not None and got["found"] is False
    assert got.get("token") == "AA100160"
    assert any(m["step_id"] == "AA100160EC" for m in got.get("similar") or [])
    assert "AA100160EC" in got["answer"]


def test_lookup_step_in_text_token_without_intent_stays_none():
    # step 토큰만 있고 의도 키워드 없음 → 기존처럼 개입하지 않음 (root lot 오염 방지).
    assert fab_reference.lookup_step_in_text("ZZ999999 확인해줘", rows=STEP_ROWS) is None


# ── classify_ppid_knob (pure) ─────────────────────────────────────────────────
def test_classify_ppid_knob_eq_match():
    out = fab_reference.classify_ppid_knob("PPID_24_3", rows=PPID_ROWS)
    assert out["found"] is True
    assert out["matches"][0]["category"] == "PPID_24_0"
    assert out["matches"][0]["feature_name"] == "24.0 SORT"
    assert "PPID_24_0" in out["answer"]


def test_classify_ppid_knob_no_match_when_value_present():
    out = fab_reference.classify_ppid_knob("PPID_99_9", rows=PPID_ROWS)
    assert out["found"] is False
    assert out["reason"] == "no_match"


def test_classify_ppid_knob_value_unset():
    unset = [{**r, "value": ""} for r in PPID_ROWS]
    out = fab_reference.classify_ppid_knob("PPID_24_3", rows=unset)
    assert out["found"] is False
    assert out["reason"] == "value_unset"
    assert "value" in out["answer"]


# ── in-text intent gating ─────────────────────────────────────────────────────
def test_lookup_step_in_text_requires_intent_for_function_step_only():
    # function_step 이름만 등장 + step 의도 없음 → 가로채지 않음(None).
    assert fab_reference.lookup_step_in_text("SD_EPI 라인 상태 어때", rows=STEP_ROWS) is None
    # step 의도 있으면 처리.
    got = fab_reference.lookup_step_in_text("SD_EPI의 step_id 알려줘", rows=STEP_ROWS)
    assert got and got["found"] is True


def test_lookup_step_in_text_step_id_hit_is_strong_signal():
    got = fab_reference.lookup_step_in_text("AA100090", rows=STEP_ROWS)
    assert got and got["direction"] == "id_to_step"


def test_classify_ppid_in_text_requires_knob_intent():
    assert fab_reference.classify_ppid_in_text("PPID_24_3 진행 위치", rows=PPID_ROWS) is None
    got = fab_reference.classify_ppid_in_text("PPID_24_3는 어떤 knob으로 분류돼", rows=PPID_ROWS)
    assert got and got["found"] is True
    assert got["matches"][0]["category"] == "PPID_24_0"


# ── Unit AI handle() routing (hermetic via monkeypatched loader) ──────────────
def test_step_lookup_unit_handles_and_ignores(monkeypatch):
    monkeypatch.setattr(fab_reference, "_read_rows", lambda filename: STEP_ROWS)
    unit = StepLookupUnitAI()
    handled = unit.handle("AA100090는 무슨 step이야", {}, {})
    assert handled and handled["handled"] is True
    assert handled["feature"] == "step_lookup"
    assert "SD_EPI" in handled["answer"]
    assert handled["table"]["total"] == 1
    # 무관한 질문은 None → dispatcher 가 다음 후보로 넘어감.
    assert unit.handle("회의 일정 알려줘", {}, {}) is None


def test_ppid_knob_unit_handles_and_ignores(monkeypatch):
    monkeypatch.setattr(fab_reference, "_read_rows", lambda filename: PPID_ROWS)
    unit = PpidKnobUnitAI()
    handled = unit.handle("PPID_24_3는 어떤 knob으로 분류돼", {}, {})
    assert handled and handled["handled"] is True
    assert handled["feature"] == "ppid_knob"
    assert "PPID_24_0" in handled["answer"]
    # knob 의도 없는 질문은 None.
    assert unit.handle("PPID_24_3 진행 위치 알려줘", {}, {}) is None


def test_new_units_registered_in_catalog():
    assert "step_lookup" in UNIT_AIS
    assert "ppid_knob" in UNIT_AIS


def test_step_lookup_runtime_graph_run_and_history(monkeypatch, tmp_path):
    monkeypatch.setattr(fab_reference, "_read_rows", lambda filename: STEP_ROWS)
    monkeypatch.setattr(lookup_runtime, "PATHS", _DummyPaths(tmp_path))

    graph = lookup_runtime.deterministic_lookup_graph("step_lookup")
    assert [node["id"] for node in graph["nodes"]] == [
        "prompt_input",
        "semantic_parse",
        "lookup_execute",
        "answer_render",
    ]
    assert graph["layout"]["rankdir"] == "LR"

    out = lookup_runtime.run_deterministic_lookup_runtime(
        "step_lookup",
        {"prompt": "AA100090는 무슨 step이야", "product": "PRODA"},
        username="tester",
    )
    assert out["ok"] is True
    assert out["unit_ai"] == "step_lookup"
    assert out["table"]["total"] == 1
    assert [row["node_id"] for row in out["trace"]] == [
        "prompt_input",
        "semantic_parse",
        "lookup_execute",
        "answer_render",
    ]

    history = lookup_runtime.list_deterministic_lookup_history("step_lookup", username="tester")
    assert history[0]["run_id"] == out["run_id"]
    assert history[0]["table_summary"]["total"] == 1


def test_ppid_knob_runtime_graph_run_and_history(monkeypatch, tmp_path):
    monkeypatch.setattr(fab_reference, "_read_rows", lambda filename: PPID_ROWS)
    monkeypatch.setattr(lookup_runtime, "PATHS", _DummyPaths(tmp_path))

    out = lookup_runtime.run_deterministic_lookup_runtime(
        "ppid_knob",
        {"prompt": "PPID_24_3는 어떤 knob으로 분류돼"},
        username="tester",
    )

    assert out["ok"] is True
    assert out["unit_ai"] == "ppid_knob"
    assert out["lookup_result"]["feature"] == "ppid_knob"
    assert "PPID_24_0" in out["answer"]
    history = lookup_runtime.list_deterministic_lookup_history("ppid_knob", username="tester")
    assert history[0]["run_id"] == out["run_id"]
    assert history[0]["unit_ai"] == "ppid_knob"


# ── Integration against real seed CSV (DuckDB-free to avoid cross-process lock) ─
def test_real_seed_data_via_direct_csv():
    import csv as _csv

    import core.paths as paths_mod

    seed = paths_mod.PATHS.db_root / "step_matching.csv"
    ppid = paths_mod.PATHS.db_root / "ppid_knob.csv"
    if not seed.is_file() or not ppid.is_file():
        import pytest

        pytest.skip("seed CSVs not present in this environment")

    def _rows(fp):
        with open(fp, encoding="utf-8-sig", newline="") as fh:
            return [{k: (v or "").strip() for k, v in r.items()} for r in _csv.DictReader(fh)]

    step = fab_reference.lookup_step("AA100090는 무슨 step이야", rows=_rows(seed))
    assert step["found"] is True
    assert step["matches"][0]["function_step"] == "SD_EPI"
    # ppid_knob.csv 에 value 열이 추가되었는지 + 분류가 되는지 (seed 보정 검증).
    knob = fab_reference.classify_ppid_knob("PPID_08_0", rows=_rows(ppid))
    assert knob["found"] is True
    assert knob["matches"][0]["category"] == "PPID_08_0"
