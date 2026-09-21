from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import product_wiki as wiki
from routers import product_wiki as api


@pytest.fixture
def isolated_wiki(tmp_path, monkeypatch):
    monkeypatch.setattr(wiki, "PATHS", SimpleNamespace(data_root=Path(tmp_path)))
    return tmp_path


def entry(kind="fact", title="측정", **extra):
    value = {"kind": kind, "title": title, "body": "내용", "evidence": "측정 로그"}
    value.update(extra)
    return value


def test_immutable_author_dates_and_history(isolated_wiki):
    first = wiki.save_entry("ML_TABLE_PRODA", 0, entry(), "alice")
    saved = first["entries"][0]
    second = wiki.save_entry("PRODA", 1, entry(title="수정", id=saved["id"]), "alice")
    edited = second["entries"][0]
    assert edited["author"] == "alice"
    assert edited["created_at"] == saved["created_at"]
    assert edited["updated_at"] != saved["updated_at"]
    rows = wiki.history("PRODA", saved["id"])
    assert [row["revision"] for row in rows] == [2, 1]
    assert rows[0]["entry"]["created_at"] == saved["created_at"]
    assert wiki.history("PRODA", before_revision=2)[0]["revision"] == 1


def test_conflict_and_concurrent_writers(isolated_wiki):
    def write(title):
        try:
            return wiki.save_entry("PRODA", 0, entry(title=title), title)
        except wiki.Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["a", "b"]))
    assert sum(result is not None for result in results) == 1
    with pytest.raises(wiki.Conflict):
        wiki.save_entry("PRODA", 0, entry(title="late"), "late")


def test_author_restriction_and_manager(isolated_wiki):
    saved = wiki.save_entry("PRODA", 0, entry(), "alice")["entries"][0]
    # Collaborative editing: bob can edit alice's entry, author remains alice, updated_by is bob
    updated = wiki.save_entry("PRODA", 1, entry(id=saved["id"], title="edited_by_bob"), "bob")
    assert updated["revision"] == 2
    record = updated["entries"][0]
    assert record["author"] == "alice"
    assert record["updated_by"] == "bob"


def test_report_snapshot_and_ai_evidence(isolated_wiki, monkeypatch):
    wiki.save_entry("ML_TABLE_PRODA", 0, entry(), "alice")
    report = wiki.create_report("PRODA", "alice")
    wiki.save_entry("PRODA", 1, entry(title="later"), "alice")
    assert report["source_revision"] == 1
    assert "revision 1" in report["body"]
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: {"ok": True, "text": "요약 [evidence]"})
    ai = wiki.create_report("PRODA", "alice", use_ai=True)
    assert ai["mode"] == "ai" and "근거 원문" in ai["body"]


def test_ai_failure_preserves_full_report(isolated_wiki, monkeypatch):
    wiki.save_entry("PRODA", 0, entry(), "alice")
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: {"ok": False, "error": "offline"})
    report = wiki.create_report("PRODA", "alice", use_ai=True)
    assert report["mode"] == "basic" and report["warning"]
    assert report["body"] == wiki.render_report(wiki.document("PRODA"))


def test_ai_never_mutates_or_deletes_originals(isolated_wiki, monkeypatch):
    doc = wiki.save_entry("P", 0, entry(title="최초 원문"), "alice")
    record_id = doc["entries"][0]["id"]
    wiki.save_entry("P", 1, entry(id=record_id, title="수정 원문"), "alice")
    before = wiki.document("P")
    prior_history = wiki.history("P", record_id)
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: {"ok": True, "text": "별도 요약"})
    for use_ai in (False, True, True):
        report = wiki.create_report("P", "bob", use_ai)
        assert "수정 원문" in report["body"]
    after = wiki.document("P")
    assert after["revision"] == before["revision"]
    assert after["entries"] == before["entries"]
    assert wiki.history("P", record_id) == prior_history
    assert prior_history[-1]["entry"]["title"] == "최초 원문"
    assert len(after["reports"]) == 3


def test_history_diff_and_pagination(isolated_wiki):
    doc = wiki.save_entry("PRODA", 0, entry(title="first"), "alice")
    record_id = doc["entries"][0]["id"]
    for revision in range(1, 103):
        wiki.save_entry("PRODA", revision, entry(id=record_id, title=str(revision)), "bob", manager=True)
    recent = wiki.history("PRODA", record_id)
    older = wiki.history("PRODA", record_id, recent[-1]["revision"])
    assert len(recent) == 100 and len(older) == 3
    assert recent[0]["changes"] == [{"field": "title", "before": "101", "after": "102"}]
    assert recent[0]["actor"] == "bob" and recent[0]["entry"]["author"] == "alice"
    assert older[-1]["entry"]["title"] == "first"


def test_access_inheritance_and_denial(monkeypatch):
    request = SimpleNamespace()
    monkeypatch.setattr(api, "current_user", lambda request: {"role": "user", "tabs": "splittable"})
    assert api.require_access(request)["tabs"] == "splittable"
    monkeypatch.setattr(api, "current_user", lambda request: {"role": "user", "tabs": "dashboard"})
    with pytest.raises(Exception) as exc:
        api.require_access(request)
    assert getattr(exc.value, "status_code", None) == 403


def test_api_validation_fact_and_date(monkeypatch):
    user = {"role": "user", "tabs": "productwiki", "username": "alice"}
    monkeypatch.setattr(api, "current_user", lambda request: user)
    request = SimpleNamespace()
    bad_fact = api.SaveRequest(product="PRODA", expected_revision=0, entry=entry(evidence=""))
    with pytest.raises(Exception) as exc:
        api.save(bad_fact, user=user)
    assert getattr(exc.value, "status_code", None) == 400
    bad_date = api.SaveRequest(product="PRODA", expected_revision=0, entry=entry(occurred_on="2024-99-99"))
    with pytest.raises(Exception) as exc:
        api.save(bad_date, user=user)
    assert getattr(exc.value, "status_code", None) == 400


def test_http_auth_attribution_and_validation(isolated_wiki, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    def authenticate(request):
        name = request.headers.get("x-test-user")
        if not name:
            raise HTTPException(401, "login required")
        return {"username": name, "role": "user", "tabs": "productwiki" if name == "alice" else ""}
    monkeypatch.setattr(api, "current_user", authenticate)
    monkeypatch.setattr(api, "is_page_manager", lambda *a: False)
    client = TestClient(app)
    assert client.get("/api/product-wiki/product?product=P").status_code == 401
    assert client.get("/api/product-wiki/product?product=P", headers={"x-test-user": "bob"}).status_code == 403
    payload = {"product": "P", "expected_revision": 0, "entry": entry(author="spoofed")}
    response = client.post("/api/product-wiki/entries", json=payload, headers={"x-test-user": "alice"})
    assert response.status_code == 200
    assert response.json()["entries"][0]["author"] == "alice"
    assert client.post("/api/product-wiki/entries", json=payload, headers={"x-test-user": "alice"}).status_code == 409


def extraction(**extra):
    value = {"title": "조건 변경 결과", "kind": "fact", "structure": "S1", "split": "A/B",
             "lot_ids": ["LOT-7"], "purpose": "수율 확인", "expected_effect": "수율 상승",
             "observed_effect": "수율 2% 상승", "evidence": "측정 로그 #7",
             "status": "validated", "occurred_on": "2026-09-14",
             "body": "조건 변경 뒤 관찰한 결과를 정리했다."}
    value.update(extra)
    return {"ok": True, "text": json.dumps(value, ensure_ascii=False)}


def test_intake_extracts_bounded_fields_and_server_identity(isolated_wiki, monkeypatch):
    source = "2026-09-14 LOT-7 S1 A/B 조건. 수율 확인, 수율 상승, 수율 2% 상승. 측정 로그 #7"
    response = extraction(author="mallory", related_ids=["invented"], id="invented")
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: response)
    doc = wiki.intake_entry("PRODA", 0, source, "alice")
    saved = doc["entries"][0]
    assert doc["saved_entry_id"] == saved["id"] and doc["intake_warning"] == ""
    assert saved["author"] == "alice" and saved["source_text"] == source
    assert saved["lot_ids"] == ["LOT-7"] and saved["occurred_on"] == "2026-09-14"
    assert saved["related_ids"] == [] and saved["body"] == "조건 변경 뒤 관찰한 결과를 정리했다."


@pytest.mark.parametrize("answer", [
    {"ok": False, "error": "offline"},
    {"ok": True, "text": "not json"},
    extraction(kind="command"),
    extraction(title="x" * 201),
])
def test_intake_fallback_never_loses_raw_text(isolated_wiki, monkeypatch, answer):
    source = "원문 첫 줄\n작성자가 붙여 넣은 내용"
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: answer)
    doc = wiki.intake_entry("P", 0, source, "alice")
    saved = doc["entries"][0]
    assert doc["intake_warning"]
    assert saved["kind"] == "opinion" and saved["title"] == "원문 첫 줄"
    assert saved["source_text"] == source and saved["body"] == source


def test_intake_removes_ungrounded_claims_and_downgrades_fact(isolated_wiki, monkeypatch):
    source = "장비 조건을 바꿨다는 메모"
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: extraction(
        lot_ids=["LOT-DOES-NOT-EXIST"], evidence="꾸며낸 측정 로그", occurred_on="2026-09-14"))
    saved = wiki.intake_entry("P", 0, source, "alice")["entries"][0]
    assert saved["kind"] == "opinion"
    assert saved["lot_ids"] == [] and saved["evidence"] == "" and saved["occurred_on"] == ""


def test_intake_body_keeps_context_and_rejects_invented_values(isolated_wiki, monkeypatch):
    source = "가설 단계다. LOT-7에서 2% 상승 가능성을 검토한다."
    prose = "아직 가설 단계이며 LOT-7의 2% 상승 가능성을 검토하고 있다."
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: extraction(
        kind="opinion", evidence="", occurred_on="", body=prose))
    saved = wiki.intake_entry("P", 0, source, "alice")["entries"][0]
    assert saved["body"] == prose

    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: extraction(
        kind="opinion", evidence="", occurred_on="", body="검증 결과는 9% 상승이다."))
    saved = wiki.intake_entry("P", 1, source, "alice")["entries"][0]
    assert saved["body"] == source


def test_report_includes_preserved_intake_source_text(isolated_wiki, monkeypatch):
    source = "보고서가 보존해야 하는 전체 입력 원문"
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: extraction(
        kind="opinion", evidence="", occurred_on="", lot_ids=[], body="읽기 쉬운 요약"))
    doc = wiki.intake_entry("P", 0, source, "alice")
    report = wiki.render_report(doc)
    assert "- 내용: 읽기 쉬운 요약" in report
    assert f"- 입력 원문: {source}" in report


def test_intake_edit_retains_original_history_and_related_ids(isolated_wiki, monkeypatch):
    source1 = "2026-09-14 LOT-7 측정 로그 #7 최초 원문"
    source2 = "2026-09-14 LOT-7 측정 로그 #7 수정 원문"
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: extraction())
    first = wiki.intake_entry("P", 0, source1, "alice")
    record_id = first["saved_entry_id"]
    # Simulate an existing relationship that intake is not allowed to accept or erase.
    other = wiki.save_entry("P", 1, entry(kind="opinion", title="other"), "alice")
    wiki.save_entry("P", 2, entry(id=record_id, related_ids=[other["entries"][0]["id"]]), "alice")
    edited = wiki.intake_entry("P", 3, source2, "alice", record_id)
    saved = next(row for row in edited["entries"] if row["id"] == record_id)
    assert saved["source_text"] == source2
    assert saved["related_ids"] == [other["entries"][0]["id"]]
    rows = wiki.history("P", record_id)
    assert rows[0]["entry"]["source_text"] == source2
    assert rows[-1]["entry"]["source_text"] == source1
    structured = entry(id=record_id, title="구조화 편집", source_text=None,
                       related_ids=saved["related_ids"])
    assert wiki.save_entry("P", 4, structured, "alice")["entries"][0]["source_text"] == source2


def test_intake_preflight_conflict_and_permission_skip_llm(isolated_wiki, monkeypatch):
    saved = wiki.save_entry("P", 0, entry(), "alice")["entries"][0]
    calls = []
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: calls.append(1) or extraction())
    with pytest.raises(wiki.Conflict):
        wiki.intake_entry("P", 0, "raw", "alice", saved["id"])
    with pytest.raises(ValueError):
        wiki.intake_entry("P", 1, "raw", "bob", "non_existent_id")
    assert calls == []


def test_intake_concurrent_change_cannot_overwrite(isolated_wiki, monkeypatch):
    saved = wiki.save_entry("P", 0, entry(), "alice")["entries"][0]

    def complete_after_race(*args, **kwargs):
        wiki.save_entry("P", 1, entry(id=saved["id"], title="winner"), "alice")
        return extraction()

    monkeypatch.setattr("core.llm_adapter.complete", complete_after_race)
    with pytest.raises(wiki.Conflict):
        wiki.intake_entry("P", 1, "2026-09-14 LOT-7 측정 로그 #7", "alice", saved["id"])
    assert wiki.document("P")["entries"][0]["title"] == "winner"


def test_http_intake_contract(isolated_wiki, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(api.router)
    monkeypatch.setattr(api, "current_user", lambda request: {
        "username": "alice", "role": "user", "tabs": "productwiki"})
    monkeypatch.setattr(api, "is_page_manager", lambda *a: False)
    monkeypatch.setattr("core.llm_adapter.complete", lambda *a, **k: extraction())
    response = TestClient(app).post("/api/product-wiki/intake", json={
        "product": "P", "expected_revision": 0,
        "text": "2026-09-14 LOT-7 측정 로그 #7"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] == 1 and payload["saved_entry_id"] == payload["entries"][0]["id"]
    assert payload["intake_warning"] == ""


def test_delete_entry_and_wiki_compilation(isolated_wiki):
    saved = wiki.save_entry("PRODA", 0, entry(title="삭제할 이슈"), "alice")["entries"][0]
    doc = wiki.delete_entry("PRODA", 1, saved["id"], "charlie")
    assert doc["revision"] == 2
    assert not any(e["id"] == saved["id"] for e in doc["entries"])
    hist = wiki.history("PRODA", saved["id"])
    assert hist[0]["action"] == "delete"
    assert hist[0]["actor"] == "charlie"
    compiled = wiki.compile_product_wiki("PRODA", actor="charlie", use_ai=False)
    assert "PRODA" in compiled["wiki_document"]
    assert isinstance(compiled["wiki_toc"], list)


def test_toc_entry_ids_match_rendered_sections(isolated_wiki):
    first = wiki.save_entry("PRODA", 0, entry(title="첫 이슈"), "alice")["entries"][0]
    second = wiki.save_entry("PRODA", 1, entry(title="둘째 이슈"), "bob")["entries"][0]
    compiled = wiki.compile_product_wiki("PRODA", actor="alice", use_ai=False)
    toc_ids = [item["id"] for item in compiled["wiki_toc"]]
    assert f"entry-{first['id'][:8]}" in toc_ids
    assert f"entry-{second['id'][:8]}" in toc_ids
    # Every entry-based TOC id must match the rendered header id rule
    # (frontend mirrors: trailing [8hex] on ### -> entry-xxx).
    for line in compiled["wiki_document"].splitlines():
        stripped = line.strip()
        if stripped.startswith("### ") and stripped.rstrip().endswith("]"):
            import re as _re
            tag = _re.search(r"\[([0-9a-fA-F]{8})\]\s*$", stripped)
            if tag:
                assert f"entry-{tag.group(1).lower()}" in toc_ids
    # One section per entry, each footer intact
    doc_entries = wiki.document("PRODA")["entries"]
    ok, reason = wiki._validate_single_entry_document(compiled["wiki_document"], doc_entries)
    assert ok, reason


def _t6_entry(entry_id, title, updated_at, **extra):
    value = {"id": entry_id, "title": title, "kind": "issue", "status": "open",
             "author": "alice", "created_at": "2026-09-01T00:00:00+00:00",
             "updated_by": "alice", "updated_at": updated_at,
             "body": f"{title} 본문", "source_text": f"{title} 원문",
             "lot_ids": [], "related_ids": []}
    value.update(extra)
    return value


def test_single_entry_validation_rejects_merged_tables_and_tampered_footers():
    entries = [_t6_entry("aaa11111-0000", "첫째", "2026-09-02T00:00:00+00:00"),
               _t6_entry("bbb22222-0000", "둘째", "2026-09-03T00:00:00+00:00")]
    clean = wiki._build_deterministic_document("PRODA", entries, [])
    assert wiki._validate_single_entry_document(clean, entries) == (True, "")
    # Two entry ids mixed in one markdown table -> merged synthesis, reject.
    merged = clean + "\n| Knob 조건 | 적용 결과 |\n|---|---|\n| aaa11111 bbb22222 혼합 | 결과 |\n"
    ok, reason = wiki._validate_single_entry_document(merged, entries)
    assert not ok and reason == "merged table across entries"
    # A single-entry table using only its own record stays valid.
    own_table = clean + "\n| Knob 조건 | 적용 결과 |\n|---|---|\n| aaa11111 단독 | 결과 |\n"
    assert wiki._validate_single_entry_document(own_table, entries) == (True, "")
    # Footer verbatim: one altered character must fail closed.
    tampered = clean.replace(entries[0]["id"], entries[0]["id"][:-1] + "x")
    ok, _ = wiki._validate_single_entry_document(tampered, entries)
    assert not ok


def test_deterministic_module_sections_follow_updated_descending():
    rows = [{"module": "GATE", "path": "Etch", "step_ids": [], "description": ""}]
    entries = [_t6_entry("aaa11111-0000", "오래된 기록", "2026-09-02T00:00:00+00:00", structure="Etch"),
               _t6_entry("bbb22222-0000", "최신 기록", "2026-09-05T00:00:00+00:00", structure="Etch"),
               _t6_entry("ccc33333-0000", "중간 기록", "2026-09-03T00:00:00+00:00", structure="Etch")]
    markdown = wiki._build_deterministic_document("PRODA", entries, rows)
    positions = [markdown.index(f"[{e['id'][:8]}]") for e in entries]
    # entries input order: old, newest, middle -> rendered must be newest, middle, old.
    assert positions[1] < positions[2] < positions[0]
    assert "이 모듈의 기록 3건 (갱신순)" in markdown
