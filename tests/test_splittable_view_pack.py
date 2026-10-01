"""SplitTable RAM view 캐시: 최근 항목은 dict, 오래된 항목은 orjson bytes 로 접는다."""
from collections import OrderedDict

import pytest

pytest.importorskip("orjson")


@pytest.fixture
def st(monkeypatch, tmp_path):
    from routers import splittable

    monkeypatch.setattr(splittable, "_VIEW_CACHE", OrderedDict())
    monkeypatch.setattr(splittable, "_VIEW_CACHE_BYTES", 0)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_view_cache_max_bytes", lambda: 10_000_000)
    monkeypatch.setattr(splittable, "_view_disk_cache_enabled", lambda: False)
    monkeypatch.setattr(splittable, "_view_pack_soon", lambda: None)  # 테스트는 직접 접는다
    monkeypatch.delenv("FLOW_SPLITTABLE_VIEW_PACK", raising=False)
    monkeypatch.setenv("FLOW_SPLITTABLE_VIEW_HOT_FRACTION", "0.05")
    return splittable


def _payload(tag, rows=300):
    return {"rows_compact": [{"k": f"KNOB_{r}", "v": [f"{tag}_{r}_{c}" for c in range(25)], "p": {"3": "X"}, "m": []}
                             for r in range(rows)],
            "headers": [f"W{c}" for c in range(25)], "product": "P"}


def test_old_entries_are_packed_and_restore_identically(st):
    for i in range(6):
        st._split_view_cache_put_memory(("P", f"R{i}"), ("h",), ("s",), _payload(i))
    before = st._VIEW_CACHE_BYTES
    packed = st._view_cache_pack_cold(st._view_cache_max_bytes())
    assert packed >= 1
    kinds = [isinstance(entry[2], st._PackedView) for entry in st._VIEW_CACHE.values()]
    # 가장 오래된 쪽(앞)이 접히고, 가장 최근 항목은 dict 로 남는다.
    assert kinds[0] is True and kinds[-1] is False
    assert st._VIEW_CACHE_BYTES < before

    freshness, restored = st._split_view_cache_get(("P", "R0"), ("h",), ("s",))
    assert freshness == "fresh"
    assert restored == _payload(0)
    # 다시 쓰인 항목은 dict 로 올라온다.
    assert not isinstance(st._VIEW_CACHE[("P", "R0")][2], st._PackedView)
    stats = st.view_cache_pack_stats()
    assert stats["promoted_total"] >= 1


def test_packed_entry_still_checks_hard_signature(st):
    for i in range(6):
        st._split_view_cache_put_memory(("P", f"R{i}"), ("h",), ("s",), _payload(i))
    st._view_cache_pack_cold(st._view_cache_max_bytes())
    assert isinstance(st._VIEW_CACHE[("P", "R0")][2], st._PackedView)
    assert st._split_view_cache_get(("P", "R0"), ("edited",), ("s",)) == ("miss", None)
    assert ("P", "R0") not in st._VIEW_CACHE
    # soft 만 다르면 stale 로 그대로 준다(재검증은 호출측이 예약).
    st._split_view_cache_get(("P", "R1"), ("h",), ("s",))
    assert st._split_view_cache_get(("P", "R1"), ("h",), ("s2",))[0] == "stale"


def test_pack_can_be_disabled(st, monkeypatch):
    monkeypatch.setenv("FLOW_SPLITTABLE_VIEW_PACK", "0")
    for i in range(6):
        st._split_view_cache_put_memory(("P", f"R{i}"), ("h",), ("s",), _payload(i))
    assert st._view_cache_pack_cold(st._view_cache_max_bytes()) == 0
    assert not any(isinstance(entry[2], st._PackedView) for entry in st._VIEW_CACHE.values())


def test_emergency_evict_handles_packed_entries(st):
    for i in range(6):
        st._split_view_cache_put_memory(("P", f"R{i}"), ("h",), ("s",), _payload(i))
    st._view_cache_pack_cold(st._view_cache_max_bytes())
    freed = st.emergency_evict_view_cache(10 ** 12)
    assert freed > 0 and len(st._VIEW_CACHE) == 0 and st._VIEW_CACHE_BYTES == 0
