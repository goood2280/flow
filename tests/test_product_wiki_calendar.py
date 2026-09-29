"""제품 위키 기록 → 변경점 관리(달력) 동기화."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import product_wiki as wiki
from routers import calendar as cal


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki, "PATHS", SimpleNamespace(data_root=Path(tmp_path)))
    cal_dir = Path(tmp_path) / "calendar"
    monkeypatch.setattr(cal, "CAL_DIR", cal_dir)
    monkeypatch.setattr(cal, "EVENTS_FILE", cal_dir / "events.json")
    return cal_dir


def events(cal_dir):
    path = cal_dir / "events.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else []


def issue(title, **extra):
    value = {"kind": "issue", "title": title, "body": "Gate CD 변경 후 Vth 상승", "status": "investigating"}
    value.update(extra)
    return value


def test_save_update_delete_mirror_to_calendar(isolated):
    doc = wiki.save_entry("PRODB", 0, issue("Inline CD 변경점", occurred_on="2026-09-20",
                                           summary="Gate CD 5nm 축소"), "alice")
    entry_id = doc["entries"][0]["id"]
    rows = events(isolated)
    assert len(rows) == 1
    ev = rows[0]
    assert ev["source_type"] == "product_wiki"
    assert ev["title"] == "[PRODB 위키] Inline CD 변경점"
    assert ev["date"] == "2026-09-20"
    assert ev["status"] == "in_progress"
    assert ev["category"] == "제품 위키"
    assert ev["author"] == "alice"
    assert ev["wiki_ref"] == {"product": "PRODB", "product_key": "prodb", "entry_id": entry_id,
                              "anchor": f"entry-{entry_id[:8]}", "author": "alice"}
    assert "제품 위키 · PRODB · 조사 중" in ev["body"] and "Gate CD 5nm 축소" in ev["body"]

    wiki.save_entry("PRODB", 1, issue("Inline CD 변경점 (확정)", id=entry_id, status="closed"), "bob")
    rows = events(isolated)
    assert len(rows) == 1 and rows[0]["id"] == ev["id"]
    assert rows[0]["title"].endswith("(확정)") and rows[0]["status"] == "done"
    assert rows[0]["version"] == 2 and rows[0]["history"][-1]["action"] == "wiki_sync_update"

    wiki.delete_entry("PRODB", 2, entry_id, "bob")
    assert events(isolated) == []


def test_manual_events_untouched_and_read_resyncs(isolated, monkeypatch):
    isolated.mkdir(parents=True, exist_ok=True)
    manual = {"id": "cal_x", "version": 1, "date": "2026-09-01", "title": "PM", "source_type": "manual"}
    (isolated / "events.json").write_text(json.dumps([manual]), "utf-8")
    # 위키 저장 직후 훅이 실패해도 다음 달력 조회가 맞춘다.
    monkeypatch.setattr(wiki, "_sync_change_calendar", lambda product: None)
    wiki.save_entry("PRODB", 0, issue("첫 기록"), "alice")
    wiki.save_entry("PRODC", 0, issue("다른 제품"), "carol")
    assert [e["id"] for e in events(isolated)] == ["cal_x"]
    result = cal.sync_product_wiki_events()
    assert result["created"] == 2
    titles = sorted(e["title"] for e in events(isolated))
    assert titles == ["PM", "[PRODB 위키] 첫 기록", "[PRODC 위키] 다른 제품"]
    # 변화가 없으면 다시 쓰지 않는다.
    assert cal.sync_product_wiki_events() == {"created": 0, "updated": 0, "removed": 0}


def test_wiki_events_are_read_only_in_calendar(isolated):
    wiki.save_entry("PRODB", 0, issue("읽기 전용"), "alice")
    ev = events(isolated)[0]
    with pytest.raises(HTTPException) as err:
        cal._reject_wiki_event(ev)
    assert err.value.status_code == 409
    cal._reject_wiki_event({"source_type": "manual"})
