from core import valve_step_advisor as advisor


def _patch_recommendation_io(monkeypatch, signatures):
    monkeypatch.setattr(advisor, "get_record", lambda *_args: None)
    monkeypatch.setattr(advisor, "put_record", lambda _rec: None)
    monkeypatch.setattr(advisor, "settings", lambda: {
        "enabled": True,
        "lookback_days": 14,
        "neighbors": 3,
        "max_alerts_per_run": 10,
        "use_llm": False,
    })
    monkeypatch.setattr(
        advisor,
        "fab_signatures",
        lambda _product, _step_ids, _days: (signatures, {"source": "test", "error": ""}),
    )


def test_recommend_prefers_matched_step_with_same_ppid_across_step_prefixes(monkeypatch):
    matched = [
        {"step_id": "AA100100", "step_desc": "NEAR_STEP", "product": "P1"},
        {"step_id": "ZZ900000", "step_desc": "SAME_PPID_STEP", "product": "P1"},
    ]
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle: matched)
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_SHARED"], "rows": 3},
        "AA100100": {"ppid": ["PP_OTHER"], "rows": 4},
        "ZZ900000": {"ppid": ["PP_SHARED"], "rows": 5},
    })

    rec = advisor.recommend({
        "vehicle": "V1", "product": "P1", "step_id": "AA100000",
        "ppids": ["PP_SHARED"],
    }, force=True, use_llm=False)

    assert rec["step_desc"] == "SAME_PPID_STEP"
    assert rec["picked_step_id"] == "ZZ900000"
    assert rec["method"] == "ppid"
    assert rec["candidates"][0]["shared_ppid"] == ["PP_SHARED"]


def test_recommend_falls_back_to_near_step_when_no_matched_ppid_exists(monkeypatch):
    matched = [
        {"step_id": "AA100100", "step_desc": "NEAR_STEP", "product": "P1"},
        {"step_id": "ZZ900000", "step_desc": "OTHER_STEP", "product": "P1"},
    ]
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle: matched)
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_NEW"], "rows": 3},
        "AA100100": {"ppid": ["PP_NEAR"], "rows": 4},
        "ZZ900000": {"ppid": ["PP_OTHER"], "rows": 5},
    })

    rec = advisor.recommend({
        "vehicle": "V1", "product": "P1", "step_id": "AA100000", "ppid": "PP_NEW",
    }, force=True, use_llm=False)

    assert rec["step_desc"] == "NEAR_STEP"
    assert rec["picked_step_id"] == "AA100100"
    assert rec["method"] == "signature"


def test_old_recommendation_cache_is_rechecked_for_ppid_algorithm():
    assert advisor._stale({"algorithm_version": 1, "method": "signature"}, "V1") is True
    assert advisor._stale({"method": "distance"}, "V1") is True
