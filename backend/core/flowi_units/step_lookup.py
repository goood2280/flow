"""Step ID 매칭 Unit AI — step_matching.csv 기반 결정적 조회.

홈 에이전트(Flow-i)가 "SD_EPI의 step_id가 뭐야" / "AA100090는 무슨 step이야" 류 질문을
dispatcher 경로(`try_dispatch(..., only=["step_lookup"])`)에서 직접 처리한다. LLM 불필요.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


def _step_tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    matches = result.get("matches") or []
    columns = ["product", "step_id", "function_step"]
    rows = [{c: m.get(c, "") for c in columns} for m in matches]
    return {
        "handled": True,
        "type": "answer",
        "intent": "step_lookup",
        "feature": "step_lookup",
        "unit_ai": "step_lookup",
        "action": result.get("direction") or "lookup_step",
        "answer": result.get("answer") or "",
        "table": (
            {"kind": "step_matching", "title": "Step 매칭", "columns": columns, "rows": rows, "total": len(rows)}
            if rows
            else {}
        ),
    }


class StepLookupUnitAI(BaseUnitAI):
    KEY = "step_lookup"
    TITLE = "Step ID 매칭"
    DESCRIPTION = (
        "step_matching.csv(product, step_id, function_step) 단일 파일로 step_id ↔ function_step 을 "
        "양방향 조회한다. 예: 'SD_EPI의 step_id가 뭐야', 'AA100090는 무슨 step이야'."
    )
    LLM_PROFILE = "deterministic single-file lookup; no LLM"
    DATA_SOURCES = (
        DataSourceRef(
            kind="matching_csv",
            path="FLOW_DB_ROOT/step_matching.csv",
            description="product, step_id, function_step 매칭표 (matching_cache DuckDB 캐시 경유).",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.fab_reference",
        function="lookup_step_in_text",
        description="prompt 에서 step_id/function_step 토큰을 찾아 양방향 매칭",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "product": {"type": "string"},
        },
        "required": ["prompt"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "handled": {"type": "boolean"},
            "answer": {"type": "string"},
            "table": {"type": "object"},
        },
        "required": ["handled"],
    }
    EXAMPLES = (
        {"prompt": "SD_EPI의 step_id가 뭐야"},
        {"prompt": "AA100090는 무슨 step이야"},
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/flowi-agent.md")

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from core import fab_reference

        text = str(prompt or "")
        product = str((slots or {}).get("product") or "")
        result = fab_reference.lookup_step_in_text(text, product)
        if not result:
            return None
        payload = _step_tool_payload(result)
        wants_files = any(w in text for w in ("파일", "관련", "어디", "찾아"))
        token = ""
        if result.get("found"):
            matches = result.get("matches") or []
            if matches and result.get("direction") == "id_to_step":
                token = str(matches[0].get("step_id") or "")
            elif matches:
                token = str(matches[0].get("function_step") or "")
        else:
            token = str(result.get("token") or "")
            # 학습된 매핑(human-in-the-loop few-shot) 우선 — 사용자가 가르쳐준 답.
            try:
                from core import flowi_fewshots
                taught = flowi_fewshots.match_in_text(text)
            except Exception:
                taught = None
            if taught:
                payload["answer"] = (
                    f"{taught.get('term')}: {taught.get('answer')}\n"
                    f"(사용자 학습 데이터 — {taught.get('taught_by') or '알 수 없음'} 님이 가르쳐준 매핑, {int(taught.get('uses') or 0)}회 사용)"
                )
                payload["fewshot"] = {"term": taught.get("term"), "source": taught.get("source")}
                return payload
        # Files 단일 파일 횡단 검색 — 룰북/매칭테이블 안에서 이 step_id 가 쓰이는 곳.
        if token and (wants_files or not result.get("found")):
            try:
                related = fab_reference.search_related_files(token)
            except Exception:
                related = []
            related = [r for r in related if r.get("file") != "step_matching.csv" or not result.get("found")]
            if related:
                lines = [f"\n'{token}' 관련 파일 (Files 단일 파일에서 열어 수정할 수 있습니다):"]
                lines += [
                    f"- {r['file']}: {r['hit_rows']}행 ({', '.join(r['columns'][:4])} 열)"
                    for r in related[:6]
                ]
                payload["answer"] = (payload.get("answer") or "") + "\n".join(lines)
                payload["related_files"] = related
        if not result.get("found"):
            payload["answer"] = (payload.get("answer") or "") + (
                "\n\n정답을 알고 계시면 \"기억해: "
                + (token or "<용어>")
                + "는 <답>\" 형태로 알려주세요. 저장해두고 다음부터 바로 답합니다."
            )
        return payload
