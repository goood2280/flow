import json
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import ops_scan


def _touch(path, size=10, age_days=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))


@pytest.fixture
def scan_env(tmp_path, monkeypatch):
    db = tmp_path / "DB"
    data = tmp_path / "flow-data"
    data.mkdir()
    db.mkdir()
    monkeypatch.setattr(ops_scan, "PATHS", SimpleNamespace(data_root=data))
    from core import file_knowledge, product_wiki, roots

    monkeypatch.setattr(roots, "get_db_root", lambda: db)
    monkeypatch.setattr(file_knowledge, "catalog", lambda: {"entries": []})
    monkeypatch.setattr(product_wiki, "products", lambda: ["PRODA"])
    return SimpleNamespace(db=db, data=data)


def _by_key(findings, key):
    return [f for f in findings.items if f["id"].split(".")[1] == key]


def test_scan_files_reports_stale_source_missing_files_and_undocumented_csv(scan_env):
    db = scan_env.db
    _touch(db / "1.RAWDATA_DB_FAB" / "PRODA" / "date=1" / "a.parquet")
    _touch(db / "1.RAWDATA_DB_FAB" / "PRODB" / "date=1" / "b.parquet", age_days=10)   # 같은 원천 안에서 멈춘 제품
    _touch(db / "1.RAWDATA_DB_ET" / "PRODA" / "a.parquet", age_days=9)
    _touch(db / "1.RAWDATA_DB_ET" / "PRODB" / "b.parquet", age_days=9)
    (db / "1.RAWDATA_DB_VM").mkdir()                                                    # 빈 원천
    _touch(db / "step_matching.csv")
    _touch(db / "my_notes.csv")
    _touch(db / "old.csv.bak")
    (scan_env.data / "filebrowser_settings.json").write_text(
        json.dumps({"file_descriptions": {"my_notes.csv": "설명"}}), encoding="utf-8")

    findings = ops_scan._Findings()
    facts = ops_scan.scan_files(findings)

    names = {row["name"] for row in facts["sources"]}
    assert names == {"1.RAWDATA_DB_ET", "1.RAWDATA_DB_FAB", "1.RAWDATA_DB_VM"}
    assert _by_key(findings, "empty_source")[0]["evidence"]["source"] == "1.RAWDATA_DB_VM"
    stale = _by_key(findings, "source_stale")
    assert [f["evidence"]["source"] for f in stale] == ["1.RAWDATA_DB_ET"]
    assert _by_key(findings, "product_stale")[0]["evidence"]["stuck"][0][0] == "PRODB"
    missing = _by_key(findings, "expected_missing")[0]["evidence"]["missing"]
    assert "step_matching.csv" not in missing and "ppid_knob.csv" in missing
    undocumented = _by_key(findings, "undocumented")[0]["evidence"]["files"]
    assert undocumented == ["step_matching.csv"]                     # 설명 있는 파일·.bak 제외
    assert _by_key(findings, "no_csv_rules")[0]["evidence"]["files"] == ["step_matching.csv"]
    assert _by_key(findings, "leftover")[0]["evidence"]["files"] == ["old.csv.bak"]
    assert _by_key(findings, "product_no_wiki")[0]["evidence"]["products"] == ["PRODB"]


def test_scan_files_all_sources_stale_is_one_high_finding(scan_env):
    _touch(scan_env.db / "1.RAWDATA_DB_FAB" / "PRODA" / "a.parquet", age_days=20)
    _touch(scan_env.db / "1.RAWDATA_DB_ET" / "PRODA" / "a.parquet", age_days=30)

    findings = ops_scan._Findings()
    ops_scan.scan_files(findings)

    assert [f["severity"] for f in _by_key(findings, "all_stale")] == ["high"]
    assert not _by_key(findings, "source_stale")


def test_scan_files_missing_db_root_is_high(scan_env, monkeypatch):
    from core import roots

    monkeypatch.setattr(roots, "get_db_root", lambda: scan_env.db / "nope")
    findings = ops_scan._Findings()
    ops_scan.scan_files(findings)
    assert findings.items[0]["id"] == "fil.db_root_missing" and findings.items[0]["severity"] == "high"


def _fake_llm(monkeypatch, obj, ok=True):
    from core import llm_adapter

    calls = []
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "get_config", lambda redact=True: {"model": "gemma4"})

    def complete_json(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return {"ok": ok, "obj": obj, "error": "" if ok else "timeout"}

    monkeypatch.setattr(llm_adapter, "complete_json", complete_json)
    return calls


FINDINGS = [
    {"id": "ser.disk_high", "area": "servers", "severity": "medium", "title": "디스크 82%", "detail": "", "suggestion": ""},
    {"id": "fil.undocumented", "area": "files", "severity": "low", "title": "설명 없는 CSV", "detail": "", "suggestion": ""},
    {"id": "fil.leftover", "area": "files", "severity": "info", "title": ".bak 파일", "detail": "", "suggestion": ""},
]


def test_ai_recommendations_keep_only_known_finding_ids(monkeypatch):
    calls = _fake_llm(monkeypatch, {
        "summary": "디스크와 문서화가 우선",
        "recommendations": [
            {"title": "캐시 정리", "area": "servers", "priority": "HIGH", "reason": "82%", "action": "캐시관리에서 정리",
             "finding_ids": ["ser.disk_high", "made.up"]},
            {"title": "일반 제안", "area": "weird", "priority": "urgent", "action": "로그 보관 정책", "finding_ids": ["x"]},
            {"title": "", "action": "제목 없음은 버림"},
            {"title": ".bak 정리", "area": "files", "action": "삭제", "finding_ids": ["fil.leftover"]},  # info 단독 → 버림
            {"title": "문서화", "area": "files", "priority": "low", "action": "설명 추가",
             "finding_ids": ["fil.undocumented", "fil.leftover"]},                                    # info 덧붙임은 유지
            "문자열은 버림",
        ],
    })

    out = ops_scan.ai_recommendations(FINDINGS, {"files": {}, "servers": {}})

    assert out["used"] is True and out["model"] == "gemma4" and out["summary"] == "디스크와 문서화가 우선"
    first, second, third = out["recommendations"]
    assert first["finding_ids"] == ["ser.disk_high"] and first["grounded"] and first["priority"] == "high"
    assert second["finding_ids"] == [] and not second["grounded"]
    assert second["area"] == "operations" and second["priority"] == "medium"
    assert third["title"] == "문서화" and third["finding_ids"] == ["fil.undocumented", "fil.leftover"]
    prompt = json.loads(calls[0][0])
    assert [f["id"] for f in prompt["findings"]] == ["ser.disk_high", "fil.undocumented", "fil.leftover"]
    assert calls[0][1]["schema"]["required"] == ["recommendations"]


def test_ai_recommendations_report_failure_and_missing_llm(monkeypatch):
    _fake_llm(monkeypatch, {}, ok=False)
    failed = ops_scan.ai_recommendations(FINDINGS, {})
    assert failed["used"] is False and failed["reason"] == "llm_call_failed" and "timeout" in failed["message"]

    from core import llm_adapter

    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    off = ops_scan.ai_recommendations(FINDINGS, {})
    assert off["used"] is False and off["reason"] == "llm_not_connected"


def test_run_saves_latest_and_history_and_rejects_parallel_scan(scan_env, monkeypatch):
    def fake_files(findings):
        findings.add("files", "low", "x", "낮음")
        findings.add("files", "high", "y", "높음")
        return {"sources": []}

    def broken(findings):
        raise ValueError("boom")

    monkeypatch.setattr(ops_scan, "scan_files", fake_files)
    monkeypatch.setattr(ops_scan, "scan_servers", broken)
    monkeypatch.setattr(ops_scan, "scan_libraries", lambda f: {})
    monkeypatch.setattr(ops_scan, "scan_operations", lambda f: {})

    report = ops_scan.run("admin", use_ai=False)

    assert [f["id"] for f in report["findings"]] == ["fil.y", "fil.x", "ser.scan_error"]
    assert report["counts"] == {"high": 1, "medium": 0, "low": 1, "info": 1}
    assert report["ai"]["used"] is False and report["ai"]["reason"] == "disabled"
    ops_scan.run("admin", use_ai=False)
    saved = ops_scan.latest()
    assert saved["report"]["scanned_at"] and len(saved["history"]) == 2 and saved["busy"] is False

    assert ops_scan._RUN_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="scan_in_progress"):
            ops_scan.run("other", use_ai=False)
    finally:
        ops_scan._RUN_LOCK.release()


def test_ops_scan_routes_are_admin_only_and_llm_path_is_allowed():
    from core import llm_adapter
    from core.auth import require_admin
    from routers import admin

    for path in ("/api/admin/ops-scan", "/api/admin/ops-scan/run", "/api/admin/ops-scan/schedule"):
        route = next(r for r in admin.router.routes if r.path == path)
        assert any(dep.call is require_admin for dep in route.dependant.dependencies)
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "u", "role": "user"}), headers={})
    with pytest.raises(HTTPException) as excinfo:
        require_admin(request)
    assert excinfo.value.status_code == 403
    assert "/api/admin/ops-scan/run" in llm_adapter._DATA_TASK_PATHS


def _fake_scans(monkeypatch, severities):
    def fake_files(findings):
        for key, sev in severities:
            findings.add("files", sev, key, f"제목 {key}")
        return {}

    monkeypatch.setattr(ops_scan, "scan_files", fake_files)
    for name in ("scan_servers", "scan_libraries", "scan_operations"):
        monkeypatch.setattr(ops_scan, name, lambda f: {})


def test_scheduled_run_is_once_per_day_after_hour_and_alerts_admins(scan_env, monkeypatch):
    import datetime as dt
    from core import notify

    _fake_scans(monkeypatch, [("a", "high"), ("b", "low")])
    ops_scan.save_schedule({"hour": 7, "use_ai": False})
    sent = []
    monkeypatch.setattr(ops_scan, "_admin_usernames", lambda: ["boss", "boss2"])
    monkeypatch.setattr(notify, "emit_event", lambda *a, **kw: sent.append((a, kw)) or True)

    assert ops_scan.run_scheduled(dt.datetime(2026, 9, 29, 6, 50)) is None
    result = ops_scan.run_scheduled(dt.datetime(2026, 9, 29, 7, 5))
    assert result and result["sent"] == 2 and result["report"]["actor"] == "자동(매일)"
    _, kw = sent[0]
    assert kw["title"].startswith("[운영 점검] 높음 1 · 보통 0") and kw["tone"] == "warning"
    assert kw["notification_id"] == "opsscan-2026-09-29"
    assert kw["payload"]["target_search"] == "?tab=ops_scan"
    assert ops_scan.run_scheduled(dt.datetime(2026, 9, 29, 22, 0)) is None
    sched = ops_scan.get_schedule(dt.datetime(2026, 9, 29, 22, 0))
    assert sched["last_result"] == "notified" and sched["next_run_at"] == "2026-09-30T07:00"
    assert ops_scan.run_scheduled(dt.datetime(2026, 9, 30, 8, 0))


def test_scheduled_run_skips_quiet_alert_and_honours_disable(scan_env, monkeypatch):
    import datetime as dt

    _fake_scans(monkeypatch, [("b", "low")])
    monkeypatch.setattr(ops_scan, "_admin_usernames", lambda: ["boss"])
    ops_scan.save_schedule({"enabled": False})
    assert ops_scan.run_scheduled(dt.datetime(2026, 9, 29, 9, 0)) is None
    assert ops_scan.get_schedule()["next_run_at"] == ""
    ops_scan.save_schedule({"enabled": True, "use_ai": False, "hour": 99, "notify": "bogus"})
    assert ops_scan.schedule_settings()["hour"] == 7 and ops_scan.schedule_settings()["notify"] == "issues"
    result = ops_scan.run_scheduled(dt.datetime(2026, 9, 29, 9, 0))
    assert result["alert"] is None and result["sent"] == 0
    assert ops_scan.get_schedule()["last_result"] == "quiet"


def test_build_alert_marks_new_issues_first():
    prev = {"findings": [{"id": "fil.old", "severity": "high", "title": "예전"}]}
    report = {"counts": {"high": 1, "medium": 1}, "findings": [
        {"id": "fil.old", "severity": "high", "title": "예전"},
        {"id": "fil.new", "severity": "medium", "title": "새것"},
    ], "ai": {"summary": "요약"}}
    alert = ops_scan.build_alert(report, prev)
    assert alert["title"] == "[운영 점검] 높음 1 · 보통 1 · 새 항목 1"
    assert alert["body"].startswith("새 · 새것 / 예전") and alert["body"].endswith("AI: 요약")
    assert ops_scan.build_alert({"counts": {}, "findings": []}, None) is None
    assert ops_scan.build_alert({"counts": {}, "findings": []}, None, "always")["title"] == "[운영 점검] 큰 문제 없음"


def test_emit_event_tone_override_and_home_alert_link(tmp_path, monkeypatch):
    from core import notify

    monkeypatch.setattr(notify, "NOTIFY_DIR", tmp_path)
    assert notify.emit_event("ops_scan_daily", target_user="boss", title="t", body="b",
                             payload={"target_tab": "admin", "target_search": "?tab=ops_scan"},
                             notification_id="opsscan-x", tone="warning")
    assert not notify.emit_event("ops_scan_daily", target_user="boss", title="t", body="b", notification_id="opsscan-x")
    rows = notify.get_notifications("boss")
    assert len(rows) == 1 and rows[0]["type"] == "warning"
