import csv
from types import SimpleNamespace

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
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": matched)
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_SHARED"], "area": ["ETCH"], "rows": 3},
        "AA100100": {"ppid": ["PP_OTHER"], "area": ["ETCH"], "rows": 4},
        "ZZ900000": {"ppid": ["PP_SHARED"], "area": ["ETCH"], "rows": 5},
    })

    rec = advisor.recommend({
        "vehicle": "V1", "product": "P1", "step_id": "AA100000",
        "ppids": ["PP_SHARED"], "areas": ["ETCH"],
    }, force=True, use_llm=False)

    assert rec["step_desc"] == "SAME_PPID_STEP"
    assert rec["picked_step_id"] == "ZZ900000"
    assert rec["method"] == "ppid"
    assert rec["candidates"][0]["shared_ppid"] == ["PP_SHARED"]
    assert rec["mapping_evidence"] == {
        "step_id": "ZZ900000",
        "step_desc": "SAME_PPID_STEP",
        "source": "Vehicle_matching.csv",
        "verified": True,
    }
    assert "ZZ900000" in rec["reason"] and "SAME_PPID_STEP" in rec["reason"]


def test_recommend_resolves_same_ppid_from_vehicle_matching_product_scope(
        tmp_path, monkeypatch):
    """복수 vehicle 제품은 알람 vehicle에 제품명이 들어가도 제품 행을 찾아야 한다."""
    vehicle_path = tmp_path / "Vehicle_matching.csv"
    with vehicle_path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(
            fp, fieldnames=["vehicle", "product", "step_id", "step_desc"])
        writer.writeheader()
        writer.writerows([
            {"vehicle": "V_A", "product": "P1", "step_id": "AA100100",
             "step_desc": "NEAR_STEP"},
            {"vehicle": "V_B", "product": "P1", "step_id": "ZZ900000",
             "step_desc": "SAME_PPID_STEP"},
        ])
    monkeypatch.setattr(advisor, "PATHS", SimpleNamespace(db_root=tmp_path))
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_SHARED"], "area": ["ETCH"], "rows": 3},
        "AA100100": {"ppid": ["PP_OTHER"], "area": ["ETCH"], "rows": 4},
        "ZZ900000": {"ppid": ["PP_SHARED"], "area": ["ETCH"], "rows": 5},
    })

    rec = advisor.recommend({
        # fab_matching_alerts는 복수 vehicle 제품에서 제품명을 vehicle 대용으로 쓴다.
        "vehicle": "P1", "product": "P1", "step_id": "AA100000",
        "ppids": ["PP_SHARED"], "areas": ["ETCH"],
    }, force=True, use_llm=False)

    assert rec["step_desc"] == "SAME_PPID_STEP"
    assert rec["picked_step_id"] == "ZZ900000"
    assert rec["method"] == "ppid"


def test_recommend_falls_back_to_same_eqp_id_when_no_matched_ppid_exists(monkeypatch):
    matched = [
        {"step_id": "AA100100", "step_desc": "NEAR_STEP", "product": "P1"},
        {"step_id": "ZZ900000", "step_desc": "OTHER_STEP", "product": "P1"},
    ]
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": matched)
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_NEW"], "eqp_id": ["EQP_A"],
                     "area": ["ETCH"], "rows": 3},
        "AA100100": {"ppid": ["PP_NEAR"], "eqp_id": ["EQP_A"],
                     "area": ["ETCH"], "rows": 4},
        "ZZ900000": {"ppid": ["PP_OTHER"], "eqp_id": ["EQP_B"],
                     "area": ["ETCH"], "rows": 5},
    })

    rec = advisor.recommend({
        "vehicle": "V1", "product": "P1", "step_id": "AA100000", "ppid": "PP_NEW",
        "eqp_id": "EQP_A", "area": "ETCH",
    }, force=True, use_llm=False)

    assert rec["step_desc"] == "NEAR_STEP"
    assert rec["picked_step_id"] == "AA100100"
    assert rec["method"] == "eqp_id"
    assert rec["candidates"][0]["shared_eqp_id"] == ["EQP_A"]


def test_recommend_ranks_shared_ppid_count_before_other_signature_scores(monkeypatch):
    matched = [
        {"step_id": "AA100100", "step_desc": "ONE_SHARED", "product": "P1"},
        {"step_id": "ZZ900000", "step_desc": "ALL_SHARED", "product": "P1"},
    ]
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": matched)
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_1", "PP_2"], "eqp_id": ["TARGET_EQP"],
                     "area": ["ETCH"], "rows": 10},
        "AA100100": {"ppid": ["PP_1"], "eqp_id": ["TARGET_EQP"],
                     "area": ["ETCH"], "rows": 10},
        "ZZ900000": {"ppid": ["PP_1", "PP_2"], "eqp_id": ["OTHER_EQP"],
                     "area": ["ETCH"], "rows": 10},
    })

    rec = advisor.recommend({
        "vehicle": "V1", "product": "P1", "step_id": "AA100000",
        "ppids": ["PP_1", "PP_2"], "eqp_ids": ["TARGET_EQP"], "areas": ["ETCH"],
    }, force=True, use_llm=False)

    assert rec["picked_step_id"] == "ZZ900000"
    assert rec["step_desc"] == "ALL_SHARED"
    assert rec["candidates"][0]["shared_ppid_count"] == 2


def test_recommend_never_selects_same_ppid_from_different_area(monkeypatch):
    matched = [
        {"step_id": "AA100100", "step_desc": "WRONG_AREA", "product": "P1"},
        {"step_id": "AA100200", "step_desc": "RIGHT_AREA", "product": "P1"},
    ]
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": matched)
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_SHARED"], "eqp_id": ["EQP_A"], "area": ["ETCH"]},
        "AA100100": {"ppid": ["PP_SHARED"], "eqp_id": ["EQP_A"], "area": ["PHOTO"]},
        "AA100200": {"ppid": ["PP_OTHER"], "eqp_id": ["EQP_A"], "area": ["ETCH"]},
    })

    rec = advisor.recommend({
        "vehicle": "V1", "product": "P1", "step_id": "AA100000",
        "ppid": "PP_SHARED", "eqp_id": "EQP_A", "area": "ETCH",
    }, force=True, use_llm=False)

    assert rec["picked_step_id"] == "AA100200"
    assert rec["step_desc"] == "RIGHT_AREA"
    assert rec["method"] == "eqp_id"
    assert rec["candidates"][0]["shared_area"] == ["ETCH"]


def test_recommend_returns_none_when_no_candidate_has_same_area(monkeypatch):
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": [
        {"step_id": "AA100100", "step_desc": "PHOTO_STEP", "product": "P1"},
    ])
    _patch_recommendation_io(monkeypatch, {
        "AA100000": {"ppid": ["PP_SHARED"], "area": ["ETCH"]},
        "AA100100": {"ppid": ["PP_SHARED"], "area": ["PHOTO"]},
    })

    rec = advisor.recommend({
        "vehicle": "V1", "product": "P1", "step_id": "AA100000",
        "ppid": "PP_SHARED", "area": "ETCH",
    }, force=True, use_llm=False)

    assert rec["step_desc"] == ""
    assert rec["method"] == "none"
    assert "동일 area" in rec["reason"]


def test_candidate_pool_rejects_conflicting_step_desc_for_same_step(monkeypatch):
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": [
        {"step_id": "AA100100", "step_desc": "ETCH", "product": "P1"},
        {"step_id": "AA100100", "step_desc": "CLEAN", "product": "P1"},
        {"step_id": "AA100200", "step_desc": "PHOTO", "product": "P1"},
    ])

    pool = advisor.matched_candidate_pool("V1", "AA100000", "P1")

    assert [(row["step_id"], row["step_desc"]) for row in pool] == [
        ("AA100200", "PHOTO")]


def test_old_recommendation_cache_is_rechecked_for_ppid_algorithm():
    assert advisor._stale({"algorithm_version": 2, "method": "ppid"}, "V1") is True
    assert advisor._stale({"method": "distance"}, "V1") is True


def test_successful_recommendation_is_rechecked_when_vehicle_mapping_changes(monkeypatch):
    monkeypatch.setattr(advisor, "matched_fingerprint", lambda _vehicle, _product="": "CURRENT")

    assert advisor._stale({
        "algorithm_version": advisor.ALGORITHM_VERSION,
        "method": "ppid",
        "matched_fp": "OLD",
    }, "V1", "P1") is True
    assert advisor._stale({
        "algorithm_version": advisor.ALGORITHM_VERSION,
        "method": "ppid",
        "matched_fp": "CURRENT",
    }, "V1", "P1") is False


def test_matching_fingerprint_changes_when_step_desc_changes(monkeypatch):
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": [
        {"step_id": "AA100100", "step_desc": "ETCH"},
    ])
    before = advisor.matched_fingerprint("V1", "P1")
    monkeypatch.setattr(advisor, "matched_steps", lambda _vehicle, _product="": [
        {"step_id": "AA100100", "step_desc": "CLEAN"},
    ])

    assert advisor.matched_fingerprint("V1", "P1") != before
