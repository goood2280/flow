from types import SimpleNamespace

import pytest

from core import knob_resolution as knobs, measurement_family, data_chat_split_query as split


@pytest.fixture
def preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(knobs, "PATHS", SimpleNamespace(data_root=tmp_path))
    return ["KNOB_7.0 AAAB", "KNOB_7.0 AAAC", "KNOB_PC_CD_DOSE", "INLINE_AAA_avg", "VM_AAA"]


def test_normalization_fuzzy_and_actual_knob_only(preferences):
    assert knobs.resolve("PRODA", "pc cd dose 조건으로 분류해줘", preferences)["exact"] == "KNOB_PC_CD_DOSE"
    assert knobs.resolve("PRODA", "PC_CD_DOSE knob으로 컬러링해줘", preferences)["exact"] == "KNOB_PC_CD_DOSE"
    candidates = knobs.resolve("PRODA", "AAA Split으로 분류해줘", preferences)
    assert not candidates["exact"]
    assert [o["value"] for o in candidates["options"]] == preferences[:2]
    assert knobs.resolve("PRODA", "PC CD DOZE 조건으로 분류해줘", preferences)["options"][0]["value"] == preferences[2]


def test_confirmations_rank_but_do_not_auto_select_and_are_scoped(preferences):
    knobs.remember("alice", "PRODA", "AAA", preferences[1], preferences)
    choices = knobs.resolve("PRODA", "AAA Split으로 분류해줘", preferences, "alice")
    assert choices["options"][0]["value"] == preferences[1]
    assert choices["options"][0]["selected_count"] == 1
    assert not choices["exact"]
    for product, user in [("PRODB", "alice"), ("PRODA", "bob")]:
        assert knobs.resolve(product, "AAA Split", preferences, user)["options"][0]["value"] == preferences[0]
    assert all(o["value"] != preferences[1] for o in knobs.resolve("PRODA", "AAA Split", preferences[:1], "alice")["options"])
    knobs.remember("alice", "PRODA", "AAA", "KNOB_not_real", preferences)
    assert "KNOB_not_real" not in knobs.counts("alice", "PRODA", "AAA")


def test_generic_split_hitl_learns_across_conversations(preferences, monkeypatch):
    monkeypatch.setattr(split.auth, "current_user", lambda req: {"username": "alice"})
    monkeypatch.setattr(split.source, "get_product_split_columns", lambda p: preferences[:2])
    monkeypatch.setattr(split.source, "get_split_value_counts", lambda *a: [{"value": "A", "count": 3}])
    first = split.dispatch("PRODA AAA 조건 종류 보여줘", {"confirmed_product": "PRODA"}, object())
    assert first["tool"]["missing"] == ["split_column"]
    chosen = split.dispatch(preferences[1], first["context"], object())
    assert chosen["ok"]
    again = split.dispatch("PRODA AAA knob 종류 보여줘", {"confirmed_product": "PRODA"}, object())
    assert again["tool"]["clarification"]["options"][0]["value"] == preferences[1]
    assert "이전 선택 1회" in again["tool"]["clarification"]["options"][0]["label"]


@pytest.mark.parametrize("metric", ["L1값", "CD", "TCD", "BCD", "MCD", "OCD", "THK", "DEPTH", "HT"])
def test_inline_defaults_and_explicit_vm_wins(metric):
    assert measurement_family.infer(f"PC {metric} 보여줘") == "INLINE"
    for alias in ["VM", "IM", "가상계측"]:
        assert measurement_family.infer(f"PC {metric} {alias} 보여줘") == "VM"
