import json
from pathlib import Path

from fastapi import APIRouter, Request

from core.auth import current_user
from core.paths import PATHS

router = APIRouter(prefix="/api/home", tags=["home"])
RELEASE_NOTES_FILE = PATHS.data_root / "release_notes.json"


@router.get("/summary")
def home_summary(request: Request):
    me = current_user(request)
    username = me.get("username", "")
    return {
        "username": username,
        "suggested_actions": [
            {
                "id": "tracker_triage",
                "question": "지금 막힌 이슈가 있나요?",
                "title": "Tracker 에서 우선순위 높은 이슈부터 정리",
                "description": "최근 변경과 댓글 흐름을 한 곳에서 보고, 다음 액션을 바로 남깁니다.",
                "tab": "tracker",
                "cta": "이슈 추적으로 이동",
                "tone": "warn",
            },
            {
                "id": "splittable_gap",
                "question": "Plan 과 actual 차이를 먼저 봐야 하나요?",
                "title": "SplitTable 에서 mismatch 구간 확인",
                "description": "root lot 기준으로 차이를 빠르게 찾고 plan 누락을 바로 채웁니다.",
                "tab": "splittable",
                "cta": "SplitTable 열기",
                "tone": "info",
            },
            {
                "id": "inform_followup",
                "question": "전달이 끊긴 모듈 인폼이 있나요?",
                "title": "Inform 에서 담당자/마감 인폼 점검",
                "description": "담당자와 제품 컨텍스트를 묶어서 후속 조치를 바로 이어갑니다.",
                "tab": "inform",
                "cta": "Inform 열기",
                "tone": "ok",
            },
        ],
        "highlights": [
            f"{username or '현재 사용자'} 기준으로 첫 진입 행동을 세 개 질문으로 압축했습니다.",
            "추천 카드는 Tracker, SplitTable, Inform 의 대표 진입점으로 바로 연결됩니다.",
            "기존 기능 카드 그리드는 그대로 유지되고, 상단 섹션만 가치 제안용으로 추가됩니다.",
        ],
    }


@router.get("/release-notes")
def release_notes(request: Request):
    me = current_user(request)
    notes = {"generated_at": "", "total_archived": 0, "recent": []}
    if RELEASE_NOTES_FILE.exists():
        try:
            data = json.loads(RELEASE_NOTES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                notes = data
        except Exception:
            pass
    return {
        "generated_at": notes.get("generated_at", ""),
        "total_archived": notes.get("total_archived", 0),
        "recent": notes.get("recent") or [],
        "build_needed": False,
        "is_admin": me.get("role") == "admin",
    }
