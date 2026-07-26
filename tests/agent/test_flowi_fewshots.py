from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import flowi_fewshots  # noqa: E402


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(flowi_fewshots, "FEWSHOT_FILE", tmp_path / "flowi_fewshots.json")


def test_teach_lookup_forget_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    entry = flowi_fewshots.teach("ab100000ec", "VIA1_FORMATION_EC 공정", by="hol")
    assert entry and entry["term"] == "AB100000EC"

    got = flowi_fewshots.lookup("AB100000EC")
    assert got and got["answer"].startswith("VIA1_FORMATION_EC")
    assert got["uses"] == 1

    assert flowi_fewshots.forget("AB100000EC") is True
    assert flowi_fewshots.lookup("AB100000EC") is None


def test_teach_updates_existing_term(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    flowi_fewshots.teach("XY123456", "첫 답", by="a")
    flowi_fewshots.teach("xy123456", "교정된 답", by="b", source="feedback")
    entries = flowi_fewshots.list_entries()
    assert len(entries) == 1
    assert entries[0]["answer"] == "교정된 답"
    assert entries[0]["source"] == "feedback"


def test_match_in_text_word_boundary(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    flowi_fewshots.teach("AB100000EC", "답변1", by="a")
    assert flowi_fewshots.match_in_text("AB100000EC는 무슨 공정이야") is not None
    # 경계 없이 이어진 문자열은 매칭하지 않음.
    assert flowi_fewshots.match_in_text("XAB100000ECY 조회") is None
    assert flowi_fewshots.match_in_text("관련 없는 질문") is None


def test_teach_rejects_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert flowi_fewshots.teach("", "답") is None
    assert flowi_fewshots.teach("TERM", "") is None
