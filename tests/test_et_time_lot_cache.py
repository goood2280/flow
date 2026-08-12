def test_et_time_root_lots_reuse_splittable_cached_pool(monkeypatch):
    from routers import et_time, splittable

    monkeypatch.setattr(et_time, "current_user", lambda _request: {"username": "tester"})
    calls = []

    def fake_candidates(**kwargs):
        calls.append(kwargs)
        return {
            "col": "root_lot_id",
            "candidates": ["ROOT_A", "ROOT_B"],
            "complete": True,
            "pool_cache": "ram",
        }

    monkeypatch.setattr(splittable, "get_lot_candidates", fake_candidates)

    result = et_time.et_time_lots(
        object(), product="PRODA", prefix="ROOT", col="root_lot_id", limit=50000)

    assert result["candidates"] == ["ROOT_A", "ROOT_B"]
    assert result["pool_cache"] == "ram"
    assert calls == [{
        "product": "PRODA",
        "col": "root_lot_id",
        "prefix": "ROOT",
        "limit": 50000,
        "source": "auto",
        "root_lot_id": "",
    }]


def test_et_time_non_root_lots_keep_existing_source(monkeypatch):
    from routers import et_time
    from core import lot_step

    monkeypatch.setattr(et_time, "current_user", lambda _request: {"username": "tester"})
    monkeypatch.setattr(
        lot_step,
        "lot_id_candidates",
        lambda **_kwargs: [{"value": "LOT_A", "type": "lot_id"}],
    )

    result = et_time.et_time_lots(
        object(), product="PRODA", prefix="", col="lot_id", limit=200)

    assert result == {
        "col": "lot_id",
        "candidates": [{"value": "LOT_A", "type": "lot_id"}],
    }
