"""기본지식 단일 파일 설명 → 홈 챗 Trend/Corr 차트."""
import polars as pl
import pytest

from core import auth, data_chat, domain_knowledge, file_knowledge, product_semantics, semantic_measure_catalog
from core.data_chat_file_chart import dispatch
from core.paths import PATHS
from core.utils import resolve_db_single_file
from routers import filebrowser as fb

BODY = """## 단일 파일 설명
AA_yld.csv 파일이 AA product의 yld 가 있는 파일이고 tkouttimeA 가 tkout_time이고 yld01이 수율값이다. LOTID는 fab lot, WF 는 wafer 번호이다.

### BB_param.csv
| 열 | 의미 |
|---|---|
| `vth` | Vth 측정값 |
| tkout_time | 측정 시간 |
| lotno | root lot |

- CC_none.csv: zz01 이 수율값

참고: ref_only.csv 는 기준정보다.
"""


@pytest.fixture
def db(tmp_path, monkeypatch):
    root = tmp_path / "db"
    (root / "yield").mkdir(parents=True)
    (root / "credential").mkdir()
    pl.DataFrame({
        "tkouttimeA": ["2026-09-01 10:00:00", "2026-09-02 10:00:00", "2026-09-03 10:00:00"],
        "LOTID": ["A1000.1", "A1000.1", "A2000.1"],
        "WF": ["01", "02", "01"],
        "yld01": [91.0, 88.0, 70.0],
    }).write_csv(root / "AA_yld.csv")
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1000", "A2000"], "wafer_id": ["1", "2", "1"],
        "tkout_time": ["2026-09-01", "2026-09-02", "2026-09-03"], "vth": [0.30, 0.32, 0.40],
    }).write_csv(root / "yield" / "BB_param.csv")
    (root / "credential" / "secret.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    ml_path = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({"ROOT_LOT_ID": ["A1000", "A1000", "A2000"], "WAFER_ID": [1, 2, 1],
                  "INLINE_L0": [10.0, 12.0, 20.0]}).write_parquet(ml_path)

    monkeypatch.setenv("FLOW_DB_ROOT", str(root))
    monkeypatch.setattr(PATHS, "data_root", tmp_path / "state")
    monkeypatch.setattr(domain_knowledge, "_path", lambda: tmp_path / "knowledge.sqlite3")
    file_knowledge._CACHE.clear()
    domain_knowledge.save_document(title="기본지식", body=BODY, editing_guidelines="지침", base_version=0, actor="admin")

    user = {"username": "file_chart_qa", "role": "admin"}
    monkeypatch.setattr(auth, "current_user", lambda request: user)
    monkeypatch.setattr(auth, "effective_permissions", lambda current: {"tabs": []})
    monkeypatch.setattr(fb, "current_user", lambda request: user)
    monkeypatch.setattr(fb, "_chart_builder_cache_get", lambda *args: None)
    monkeypatch.setattr(fb, "_chart_builder_cache_put", lambda *args: None)
    original = fb.source_data_files
    monkeypatch.setattr(fb, "source_data_files", lambda root, product, **kw:
                        [ml_path] if root == "ML_TABLE" and product == "PRODA" else original(root=root, product=product))
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODA"])
    measure = {"product": "PRODA", "source_type": "INLINE", "term": "L0", "step_id": "IN100", "item_id": "L0"}
    monkeypatch.setattr(semantic_measure_catalog, "match_terms", lambda *a, **k: [measure])
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *a, **k: [])
    return root


def test_parse_reads_roles_measures_and_warnings(db):
    entries = {e["file"]: e for e in file_knowledge.parse(BODY)}
    aa = entries["AA_yld.csv"]
    assert aa["status"] == "ready" and aa["product"] == "AA"
    assert aa["roles"]["time"]["column"] == "tkouttimeA"
    assert aa["roles"]["lot_id"]["column"] == "LOTID"
    assert aa["roles"]["wafer_id"]["column"] == "WF"
    assert aa["measures"][0]["column"] == "yld01" and "수율" in aa["measures"][0]["aliases"]
    bb = entries["BB_param.csv"]
    assert bb["path"] == "yield/BB_param.csv"
    assert bb["measures"][0]["column"] == "vth"
    assert bb["roles"]["root_lot_id"]["basis"] == "열 이름"
    assert any("lotno" in w for w in bb["warnings"])
    assert entries["CC_none.csv"]["status"] == "missing_file"
    assert entries["ref_only.csv"]["status"] == "mentioned" and not entries["ref_only.csv"]["warnings"]


def test_single_file_source_rejects_hidden_and_escaping_paths(db):
    assert resolve_db_single_file("AA_yld.csv")
    assert resolve_db_single_file("yield/BB_param.csv")
    for bad in ("../AA_yld.csv", "credential/secret.csv", "/AA_yld.csv", "C:/x.csv", "AA_yld.txt", "yield/../AA_yld.csv"):
        assert resolve_db_single_file(bad) is None, bad


def test_trend_uses_file_description_and_chartbuilder_history(db):
    result = dispatch("AA 수율 trend 그려줘", {}, None)
    tool = result["tool"]
    assert result["ok"], result
    assert tool["action"] == "file.trend"
    assert tool["chart_result"]["x"] == "tkouttimeA" and tool["chart_result"]["y"] == "yld01"
    assert [p["y"] for p in tool["chart_result"]["points"]] == [70.0, 88.0, 91.0]
    assert "DB_FILE" in tool["definition_code"] and tool["saved_chart"]["id"]


def test_inline_corr_asks_flow_product_then_joins_by_root_lot_and_wafer(db):
    first = dispatch("AA 수율과 inline L0 corr 그려줘", {}, None)
    assert first["tool"]["missing"] == ["ml_product"]
    result = dispatch("1", first["context"], None)
    tool = result["tool"]
    assert result["ok"], result
    assert tool["action"] == "file.inline_correlation"
    assert tool["evidence"]["complete_pair_count"] == 3
    assert tool["chart_result"]["x"] == "INLINE_L0" and tool["chart_result"]["y"] == "yld01"
    assert tool["fit"]["r2"] > 0.9


def test_file_to_file_corr(db):
    result = dispatch("AA 수율과 BB_param.csv vth 상관 차트", {}, None)
    tool = result["tool"]
    assert result["ok"], result
    assert tool["action"] == "file.correlation"
    assert tool["evidence"]["complete_pair_count"] == 3


def test_unrelated_or_other_product_requests_pass_through(db):
    assert dispatch("PRODA 수율 맵 보여줘", {}, None) is None
    assert dispatch("수율 trend 그려줘", {"confirmed_product": "PRODA"}, None) is None
    assert dispatch("AA 수율 알려줘", {}, None) is None


def test_bullet_scope_keeps_sub_items_and_stops_at_sibling():
    body = "- DD_yld.csv: DD 수율 파일\n  - tkouttimeA: tkout_time\n  - yld01: 수율값\n- Reformatter: 다른 주제\n"
    scope = file_knowledge._scopes(body)["DD_yld.csv"][0]
    assert "yld01" in scope and "Reformatter" not in scope
