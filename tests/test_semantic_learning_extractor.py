from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import pytest

from app_v2.modules.semantic_learning import extractor, inbox, proposer


@pytest.fixture()
def inbox_dir(tmp_path, monkeypatch):
    base = tmp_path / "proposals"
    base.mkdir()
    monkeypatch.setattr(inbox, "INBOX_DIR", base)
    return base


def test_extract_terms_hangul_english_and_special_prefix():
    text = "GATE 산화막 두께 KNOB_FOO 적용 후 wafer_id 1번에서 MTS_X 변경"
    terms = extractor.extract_terms(text)
    assert "산화막" in terms
    assert "두께" in terms
    assert "KNOB_FOO" in terms
    assert "wafer_id" in terms
    assert "MTS_X" in terms


def test_extract_terms_drops_single_char_and_digits():
    text = "이거 1 2 KNOB"
    terms = extractor.extract_terms(text)
    # "이거" is in stopwords; "1"/"2" are digits-only; "KNOB" passes
    assert "이거" not in terms
    assert "1" not in terms
    assert "2" not in terms
    assert "KNOB" in terms


def test_extract_from_meeting_pulls_agenda_minutes_decisions():
    meeting = {
        "title": "GATE 산화막 정기 리뷰",
        "agendas": [{"title": "PPID_TEST 적용"}, "MTS 변경 검토"],
        "minutes": "리세션 점검 후 anchor_item_change 결정",
        "decisions": [{"text": "WAFER_DEPOSITION 진행"}],
        "action_items": [{"title": "lot_anomaly 모니터링", "owner": "hol"}],
    }
    terms = extractor.extract_from_meeting(meeting)
    assert "산화막" in terms
    assert "PPID_TEST" in terms
    assert "anchor_item_change" in terms
    assert "WAFER_DEPOSITION" in terms
    assert "lot_anomaly" in terms


def test_extract_from_inform_concatenates_module_reason_message():
    inform = {"module": "GATE", "reason": "산화막 두께", "message": "KNOB_FOO 적용 통보"}
    terms = extractor.extract_from_inform(inform)
    assert "GATE" in terms
    assert "산화막" in terms
    assert "KNOB_FOO" in terms


def test_extract_from_activity_log_returns_empty_when_missing(tmp_path):
    missing = tmp_path / "nope.jsonl"
    assert extractor.extract_from_activity_log(missing) == []


def test_extract_from_activity_log_filters_to_low_coverage(tmp_path):
    log = tmp_path / "flowi_activity.jsonl"
    rows = [
        # High coverage — should NOT contribute
        {
            "timestamp": "2026-05-20T10:00:00",
            "trace": {
                "semantic": {
                    "coverage": 0.9,
                    "tokens": ["ignored_high_cov"],
                    "normalized_terms": {"ignored_high_cov": "ignored_high_cov"},
                    "warnings": [],
                }
            },
        },
        # Low coverage — contributes
        {
            "timestamp": "2026-05-21T10:00:00",
            "trace": {
                "semantic": {
                    "coverage": 0.1,
                    "tokens": ["산화막", "두께", "resolved_token"],
                    "normalized_terms": {"resolved_token": "wafer_id"},
                    "warnings": ["low semantic coverage; add schema column docs or wiki aliases"],
                }
            },
        },
    ]
    log.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    terms = extractor.extract_from_activity_log(log)
    assert "산화막" in terms
    assert "두께" in terms
    assert "ignored_high_cov" not in terms
    # resolved_token was in normalized_terms — skipped
    assert "resolved_token" not in terms


def test_classify_proposal_returns_mapping_when_alias_matches():
    alias_groups = {"wafer_id": ["wafer", "웨이퍼"], "knob": ["knob", "split"]}
    out = proposer.classify_proposal("웨이퍼", alias_groups=alias_groups)
    assert out["category"] == "mapping"
    assert out["canonical_match"] == "wafer_id"
    assert 0.0 < out["confidence"] <= 1.0


def test_classify_proposal_returns_new_canonical_when_no_match():
    alias_groups = {"wafer_id": ["wafer", "웨이퍼"]}
    out = proposer.classify_proposal("산화막", alias_groups=alias_groups)
    assert out["category"] == "new_canonical"
    assert out["canonical_match"] is None
    assert 0.0 < out["confidence"] <= 1.0


def test_classify_proposal_rejects_short_and_digit_terms():
    out = proposer.classify_proposal("a", alias_groups={})
    assert out["category"] == "reject"
    assert out["confidence"] == 0.0

    out2 = proposer.classify_proposal("123", alias_groups={})
    assert out2["category"] == "reject"


def test_enqueue_proposal_idempotent_for_pending_duplicates(inbox_dir):
    first = inbox.enqueue_proposal({
        "term": "산화막",
        "category": "new_canonical",
        "canonical_match": None,
        "confidence": 0.6,
        "rationale": "no match",
        "origin": {"kind": "meeting", "ref": "mtg-2026-05-22"},
    })
    second = inbox.enqueue_proposal({
        "term": "산화막",
        "category": "new_canonical",
        "confidence": 0.6,
        "origin": {"kind": "meeting", "ref": "mtg-2026-05-22"},
    })
    assert first["id"] == second["id"]
    # Only one file on disk
    files = list(inbox_dir.glob("*.json"))
    assert len(files) == 1


def test_list_and_update_proposal_status_round_trip(inbox_dir):
    record = inbox.enqueue_proposal({
        "term": "MTS_X",
        "category": "new_canonical",
        "confidence": 0.55,
        "origin": {"kind": "inform", "ref": "if-001"},
    })
    pending = inbox.list_proposals(status="pending")
    assert any(r["id"] == record["id"] for r in pending)

    decided = inbox.update_proposal_status(record["id"], status="approved", by="hol")
    assert decided is not None
    assert decided["status"] == "approved"
    assert decided["decided_by"] == "hol"

    # Idempotent — re-approving keeps it approved
    again = inbox.update_proposal_status(record["id"], status="rejected", by="hol")
    assert again is not None
    assert again["status"] == "approved"  # already decided, no change

    # Pending list no longer contains the decided proposal
    pending_after = inbox.list_proposals(status="pending")
    assert all(r["id"] != record["id"] for r in pending_after)
