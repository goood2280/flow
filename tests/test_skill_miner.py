"""tests/test_skill_miner.py — Step 5 시퀀스 마이닝 + 승인 검증.

activity.jsonl 을 tmp 로 격리해 합성 시퀀스를 inject 후 마이너가 freq/users
조건을 정확히 적용하는지, 후보 → 정식 승격이 idempotent 한지 검증한다.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_FLOW_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _FLOW_ROOT / "backend"
for p in (_BACKEND, _FLOW_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def _write_events(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _ev(user: str, action: str, minutes_ago: int) -> dict:
    return {"username": user, "action": action, "tab": "test", "detail": "", "timestamp": _ts(minutes_ago)}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """activity.jsonl + skills/ 를 tmp 로 격리."""
    from core.paths import PATHS
    from core import skill_miner, skills_repo

    fake_log = tmp_path / "logs" / "activity.jsonl"
    fake_skills = tmp_path / "skills"
    fake_candidates = fake_skills / "_candidates"

    monkeypatch.setattr(PATHS, "activity_log", fake_log)
    monkeypatch.setattr(skills_repo, "SKILLS_DIR", fake_skills)
    monkeypatch.setattr(skills_repo, "CANDIDATES_DIR", fake_candidates)
    return {"log": fake_log, "skills": fake_skills, "candidates": fake_candidates}


def test_mine_promotes_sequence_at_threshold(isolated):
    from core import skill_miner

    # 시퀀스 A: workspace_run → orchestrate, 사용자 2명, 3회 → 후보 1건
    rows = []
    # 시퀀스 순서: workspace_run(먼저) → orchestrate(다음).
    # minutes_ago 가 큰 게 먼저 (=더 오래된 ts).
    # user1: 3 회 시퀀스
    for i in range(3):
        base = 60 * i  # 1시간 간격 시퀀스
        rows.append(_ev("user1", "filebrowser_sql:workspace_run", base + 2))
        rows.append(_ev("user1", "home_agent:orchestrate", base + 1))
    # user2: 1 회 시퀀스
    rows.append(_ev("user2", "filebrowser_sql:workspace_run", 200))
    rows.append(_ev("user2", "home_agent:orchestrate", 199))
    _write_events(isolated["log"], rows)

    out = skill_miner.mine(days=1, window_sec=300, min_freq=3, min_users=2)
    assert out["candidate_count"] >= 1
    # 후보 디렉토리에 저장되어야
    files = list(isolated["candidates"].glob("*.json"))
    assert len(files) >= 1
    cand = json.loads(files[0].read_text(encoding="utf-8"))
    assert cand["freq"] >= 3
    assert len(cand["users"]) >= 2


def test_mine_skips_when_below_freq_threshold(isolated):
    from core import skill_miner

    rows = [
        _ev("user1", "filebrowser_sql:workspace_run", 5),
        _ev("user1", "home_agent:orchestrate", 4),
        _ev("user2", "filebrowser_sql:workspace_run", 3),
        _ev("user2", "home_agent:orchestrate", 2),
    ]
    _write_events(isolated["log"], rows)
    out = skill_miner.mine(days=1, window_sec=300, min_freq=3, min_users=2)
    # 시퀀스 freq=2 → 임계 미달
    assert out["candidate_count"] == 0


def test_mine_skips_when_only_one_user(isolated):
    from core import skill_miner

    rows = []
    for i in range(5):
        base = 60 * i
        rows.append(_ev("solo_user", "tool:foo", base + 1))
        rows.append(_ev("solo_user", "tool:bar", base + 2))
    _write_events(isolated["log"], rows)
    out = skill_miner.mine(days=1, window_sec=300, min_freq=3, min_users=2)
    assert out["candidate_count"] == 0


def test_approve_moves_candidate_to_skills(isolated):
    from core import skill_miner, skills_repo

    rows = []
    for i in range(3):
        base = 60 * i
        rows.append(_ev("user1", "filebrowser_sql:workspace_run", base + 2))
        rows.append(_ev("user1", "home_agent:orchestrate", base + 1))
    rows.append(_ev("user2", "filebrowser_sql:workspace_run", 200))
    rows.append(_ev("user2", "home_agent:orchestrate", 199))
    _write_events(isolated["log"], rows)

    out = skill_miner.mine(days=1, window_sec=300, min_freq=3, min_users=2)
    cand_key = out["candidates"][0]["key"]

    saved = skills_repo.approve_candidate(cand_key, by="admin", override_title="병렬 분석")
    assert saved["title"] == "병렬 분석"
    assert saved["source"] == "skill_miner"
    # 후보 디렉토리에서 파일 사라짐
    assert not (isolated["candidates"] / f"{cand_key}.json").exists()
    # 정식 디렉토리에 등장
    assert (isolated["skills"] / f"{cand_key}.json").exists()
    # list_skills 가 잡아냄
    assert any(s["key"] == cand_key for s in skills_repo.list_skills())


def test_reject_removes_candidate(isolated):
    from core import skills_repo

    cand = {
        "key": "sk_test",
        "title": "임시",
        "description": "test",
        "kind": "chain",
        "steps": [],
        "freq": 5,
        "users": ["a", "b"],
        "first_seen": _ts(50),
        "last_seen": _ts(10),
    }
    skills_repo.save_candidate(cand)
    assert (isolated["candidates"] / "sk_test.json").exists()

    ok = skills_repo.reject_candidate("sk_test")
    assert ok is True
    assert not (isolated["candidates"] / "sk_test.json").exists()


def test_action_normalize():
    from core import skill_miner
    assert skill_miner._normalize_action("tool:foo extra") == "tool:foo"
    assert skill_miner._normalize_action("ai_hub_run:bar/baz") == "ai_hub_run:bar/baz"
    assert skill_miner._normalize_action("nothing") == "nothing"
