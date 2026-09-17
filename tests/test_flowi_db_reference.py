from pathlib import Path

import pytest
from fastapi import HTTPException

from core import flowi_db_reference
from routers import flowi_reference


def _source(tmp_path: Path) -> None:
    (tmp_path / "ML_TABLE_REAL_A.csv").write_text("lot_id,value\nL1,2\n", encoding="utf-8")
    (tmp_path / "ML_TABLE_REAL_C.csv").write_text("wafer_id,score\nW1,3\n", encoding="utf-8")
    (tmp_path / "1.RAWDATA_DB_FAB" / "RealB" / "date=20260916").mkdir(parents=True)
    (tmp_path / "mapfile" / "VH_PRODA").mkdir(parents=True)


def test_generate_uses_observed_metadata_and_atomic_target(tmp_path, monkeypatch):
    _source(tmp_path)
    seen = {}
    monkeypatch.setattr(flowi_db_reference.llm_adapter, "is_available", lambda: True)

    def complete(prompt, **kwargs):
        seen["prompt"] = prompt
        return {"ok": True, "obj": {"overview": "관찰된 파일의 열 이름을 확인할 수 있습니다.",
                                    "usage_notes": ["행 내용과 값의 의미는 확인되지 않았습니다."]}}

    monkeypatch.setattr(flowi_db_reference.llm_adapter, "complete_json", complete)
    result = flowi_db_reference.generate_reference(tmp_path)
    content = flowi_db_reference.load_reference_context(db_root=tmp_path)

    assert result["exists"] and result["product_count"] == 3
    assert result["path"] == str(tmp_path / "confidential" / "flowi_db_reference.md")
    assert "REAL_A" in content and "RealB" in content and "REAL_C" in content
    assert "lot_id" in content and "value" in content and "wafer_id" in content
    assert "PRODA" not in content and "VH_PRODA" not in seen["prompt"]
    assert "## 필수 사용 규칙" in content
    assert "제품명을 물어봅니다" in content
    assert "LLM 설명은 참고용 해석" in content
    assert flowi_db_reference.status(tmp_path)["size_bytes"] > 0


def test_generation_failure_keeps_previous_document(tmp_path, monkeypatch):
    _source(tmp_path)
    target = flowi_db_reference.reference_path(tmp_path)
    target.parent.mkdir()
    target.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(flowi_db_reference.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(flowi_db_reference.llm_adapter, "complete_json", lambda *a, **k: {
        "ok": True, "obj": {"overview": "ML_TABLE_PRODA exists", "usage_notes": []},
    })

    with pytest.raises(ValueError, match="unobserved product or table"):
        flowi_db_reference.generate_reference(tmp_path)
    assert target.read_text(encoding="utf-8") == "previous"


def test_admin_routes_block_non_admin(monkeypatch):
    monkeypatch.setattr(flowi_reference, "current_user", lambda request: {"role": "user"})
    with pytest.raises(HTTPException) as get_error:
        flowi_reference.db_reference_status(object())
    with pytest.raises(HTTPException) as post_error:
        flowi_reference.generate_db_reference(object())
    assert get_error.value.status_code == 403
    assert post_error.value.status_code == 403


def test_context_loader_is_bounded(tmp_path):
    target = flowi_db_reference.reference_path(tmp_path)
    target.parent.mkdir()
    target.write_text("x" * 30000, encoding="utf-8")
    assert len(flowi_db_reference.load_reference_context(100, tmp_path)) == 100
    assert len(flowi_db_reference.load_reference_context(30000, tmp_path)) == 24000


def test_mixed_case_invented_product_is_rejected(tmp_path, monkeypatch):
    _source(tmp_path)
    monkeypatch.setattr(flowi_db_reference.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(flowi_db_reference.llm_adapter, "complete_json", lambda *a, **k: {
        "ok": True, "obj": {"overview": "prodA 데이터가 있습니다.", "usage_notes": []},
    })
    with pytest.raises(ValueError, match="prodA"):
        flowi_db_reference.generate_reference(tmp_path)


def test_scan_limits_are_reported_and_status_read_is_bounded(tmp_path, monkeypatch):
    for index in range(5):
        (tmp_path / f"ML_TABLE_REAL_{index}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(flowi_db_reference, "_MAX_SCAN_ENTRIES", 2)
    observed = flowi_db_reference.observe_db(tmp_path)
    assert observed["scan"]["truncated"] is True
    assert observed["scan"]["scanned_entries"] == 2
    assert "일부 경로를 확인하지 못했습니다" in flowi_db_reference._render(observed, {"overview": "", "usage_notes": []})

    target = flowi_db_reference.reference_path(tmp_path)
    target.parent.mkdir()
    target.write_text("x" * 10000, encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("unbounded read_text used"))
    assert len(flowi_db_reference.status(tmp_path)["preview"]) == 5000
