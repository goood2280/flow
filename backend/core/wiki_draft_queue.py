"""core/wiki_draft_queue.py — KnowledgeEvent → Wiki draft 큐.

회의/이슈/lot 코멘트 같은 mutation 이벤트(`knowledge_vault.append_event`)가
같은 entity 에 N개 이상 모이면 LLM 에 markdown draft 를 부탁해 review 큐에
저장한다. 사용자가 KnowledgeReviewQueueTab 에서 승인하면 기존 `upsert_doc`
로 wiki page 가 publish 된다.

새 저장소를 만들지 않고 다음 자산을 그대로 활용한다:
  - 입력 이벤트 채널: `data/flow-data/knowledge/raw/events/events.jsonl`
  - 출력 wiki: `core.knowledge_vault.upsert_doc()` → `wiki/<kind>/<doc_id>.md`
  - LLM: `core.llm_adapter.complete()` (없으면 단순 template fallback)
  - 본 모듈 전용 store: `data/flow-data/knowledge/index/wiki_draft_queue.jsonl`

설계 원칙:
  - 자동 publish 없음. 사용자 승인 후에만 wiki upsert.
  - draft 생성 실패해도 raw event 는 보존.
  - LLM 미설정 환경에서도 동작 (제목 + event list 만으로도 draft).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from core import knowledge_vault as kv

logger = logging.getLogger("flow.wiki_draft_queue")

DRAFT_QUEUE_FILE = kv.INDEX_DIR / "wiki_draft_queue.jsonl"
DEFAULT_GROUP_THRESHOLD = 2  # 동일 wiki_target 에 event N개 이상 모이면 draft 생성
DEFAULT_WINDOW_DAYS = 7


# ── 저장소 helper ────────────────────────────────────────────────────────────
def _ensure_dirs() -> None:
    kv.INDEX_DIR.mkdir(parents=True, exist_ok=True)


def _read_jsonl(fp: Path) -> list[dict[str, Any]]:
    if not fp.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in fp.read_text("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception as exc:
        logger.warning("read %s failed: %s", fp, exc)
    return out


def _write_jsonl(fp: Path, rows: Iterable[dict[str, Any]]) -> None:
    _ensure_dirs()
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    tmp.replace(fp)


def _append_jsonl(fp: Path, row: dict[str, Any]) -> None:
    _ensure_dirs()
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── 이벤트 grouping ──────────────────────────────────────────────────────────
def _wiki_targets(event: dict[str, Any]) -> list[str]:
    """event 에서 wiki_targets 또는 entity 기반 fallback target list 반환."""
    targets = event.get("wiki_targets")
    if isinstance(targets, list) and targets:
        return [str(t) for t in targets if t]
    # fallback: entity 기반 자동 target.
    entity = event.get("entity") or {}
    parts: list[str] = []
    if entity.get("root_lot_id"):
        parts.append(f"lot/{entity['root_lot_id']}")
    if entity.get("wafer_id"):
        parts.append(f"wafer/{entity['wafer_id']}")
    return parts


def _parse_target(target: str) -> tuple[str, str]:
    """`issue/X-123` → ("issue", "X-123"). kind 모르면 ("manual", target) fallback."""
    if "/" in target:
        kind, _, rest = target.partition("/")
        return (kind or "manual", rest or target)
    return ("manual", target)


def _kind_to_wiki(kind: str) -> str:
    """wiki_target kind 를 KnowledgeDoc.kind 로 정규화 (Literal 제한)."""
    valid = {"product", "lot", "wafer", "knob", "issue", "meeting", "report", "decision", "agent_wiki", "schema_doc", "ontology", "manual"}
    if kind == "inform":
        return "report"
    return kind if kind in valid else "manual"


def _recent_events(window_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, window_days))
    out: list[dict[str, Any]] = []
    for row in kv.list_events(limit=2000):
        ts_raw = row.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
        except Exception:
            pass
        out.append(row)
    return out


def _existing_draft_keys() -> set[str]:
    """이미 draft 큐에 있는(아직 미처리) target+source_id 조합. 중복 enqueue 방지용."""
    keys: set[str] = set()
    for row in _read_jsonl(DRAFT_QUEUE_FILE):
        if row.get("status") not in ("pending", "edited"):
            continue
        target = str(row.get("target") or "")
        for src in row.get("source_event_ids") or []:
            keys.add(f"{target}|{src}")
    return keys


def _build_markdown(target: str, group_events: list[dict[str, Any]]) -> tuple[str, str]:
    """LLM 또는 fallback template 으로 (title, markdown body) 생성."""
    kind, rest = _parse_target(target)
    first = group_events[0] if group_events else {}
    fallback_title = str(first.get("title") or "").strip() or f"{kind.title()} {rest}"
    event_lines = []
    for ev in group_events[:20]:
        when = str(ev.get("created_at") or "")[:19]
        actor = str(ev.get("actor") or "?")
        summary = str(ev.get("summary") or ev.get("title") or "").strip().replace("\n", " ")
        if len(summary) > 200:
            summary = summary[:199] + "…"
        event_lines.append(f"- `{when}` ({actor}) — {summary}")
    fallback_body = (
        f"## 요약\n\n"
        f"이 페이지는 `{kind}/{rest}` 에 대한 최근 활동 {len(group_events)}건을 모은 초안입니다. "
        f"사용자가 검토 후 정식 wiki 로 승격하세요.\n\n"
        f"## 근거 이벤트\n\n" + "\n".join(event_lines) + "\n"
    )
    try:
        from core import llm_adapter
        if not llm_adapter.is_available() or not llm_adapter.should_attempt_llm():
            return fallback_title, fallback_body
        system = (
            "You are Flow-i's wiki drafter. Summarize related Knowledge events into a Korean "
            "markdown page. Stay factual — never invent data not in events. Use sections "
            "'요약', '핵심 변경', '근거 이벤트', '다음 액션'."
        )
        events_json = json.dumps([{
            "title": ev.get("title"),
            "summary": ev.get("summary"),
            "actor": ev.get("actor"),
            "created_at": ev.get("created_at"),
            "tags": ev.get("tags") or [],
        } for ev in group_events[:30]], ensure_ascii=False, default=str)
        prompt = (
            f"# 대상 entity\n{target}\n\n"
            f"# 입력 이벤트 (JSON)\n{events_json}\n\n"
            "# 출력 (markdown only, 다른 텍스트 금지)\n"
            "# <한줄 제목>\n\n## 요약\n...\n\n## 핵심 변경\n...\n\n## 근거 이벤트\n- ...\n\n## 다음 액션\n- ..."
        )
        out = llm_adapter.complete(prompt, system=system, timeout=30)
        if out.get("ok") and out.get("text"):
            text = str(out["text"]).strip()
            # 첫 헤더 (#) 를 title 로 분리
            m = re.match(r"^#\s+(.+)\n", text)
            if m:
                title = m.group(1).strip()
                body = text[m.end():].lstrip()
                return title or fallback_title, body or fallback_body
            return fallback_title, text
    except Exception as exc:
        logger.info("llm draft failed for %s: %s", target, exc)
    return fallback_title, fallback_body


# ── 공개 API ─────────────────────────────────────────────────────────────────
def enqueue_from_events(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    threshold: int = DEFAULT_GROUP_THRESHOLD,
    actor: str = "system",
) -> dict[str, Any]:
    """최근 window_days 내 events 를 wiki_target 별로 묶어 N개 이상이면 draft 생성."""
    _ensure_dirs()
    events = _recent_events(window_days)
    by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        for tgt in _wiki_targets(ev):
            by_target[tgt].append(ev)
    existing = _existing_draft_keys()
    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    for target, group in by_target.items():
        if len(group) < threshold:
            skipped.append(target)
            continue
        # 모두 이미 처리된 event 라면 skip.
        fresh_events = [ev for ev in group if f"{target}|{ev.get('event_id')}" not in existing]
        if not fresh_events:
            skipped.append(target)
            continue
        title, body = _build_markdown(target, group)
        draft_id = f"draft_{uuid.uuid4().hex[:10]}"
        kind, rest = _parse_target(target)
        row = {
            "draft_id": draft_id,
            "target": target,
            "kind": _kind_to_wiki(kind),
            "doc_id_hint": rest,
            "title": title,
            "body": body,
            "source_event_ids": [str(ev.get("event_id") or "") for ev in group if ev.get("event_id")],
            "event_count": len(group),
            "status": "pending",
            "created_at": _now_iso(),
            "created_by": actor,
            "approved_at": "",
            "approved_by": "",
        }
        _append_jsonl(DRAFT_QUEUE_FILE, row)
        created.append(row)
    return {"created": len(created), "skipped": len(skipped), "drafts": created}


def list_drafts(*, status: str = "pending", limit: int = 50) -> list[dict[str, Any]]:
    """status filter 로 draft 목록 반환. 최신순."""
    rows = _read_jsonl(DRAFT_QUEUE_FILE)
    if status:
        rows = [r for r in rows if str(r.get("status") or "") == status]
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[: max(1, min(limit, 500))]


def get_draft(draft_id: str) -> dict[str, Any] | None:
    for row in _read_jsonl(DRAFT_QUEUE_FILE):
        if str(row.get("draft_id") or "") == str(draft_id):
            return row
    return None


def _save_all(rows: list[dict[str, Any]]) -> None:
    _write_jsonl(DRAFT_QUEUE_FILE, rows)


def _replace_one(draft_id: str, mutator) -> dict[str, Any] | None:
    rows = _read_jsonl(DRAFT_QUEUE_FILE)
    target_index = -1
    for i, row in enumerate(rows):
        if str(row.get("draft_id") or "") == str(draft_id):
            target_index = i
            break
    if target_index < 0:
        return None
    updated = mutator(dict(rows[target_index]))
    rows[target_index] = updated
    _save_all(rows)
    return updated


def edit_draft(draft_id: str, *, title: str | None = None, body: str | None = None, actor: str = "") -> dict[str, Any] | None:
    def _mutate(row: dict[str, Any]) -> dict[str, Any]:
        if title is not None:
            row["title"] = title
        if body is not None:
            row["body"] = body
        row["status"] = "edited"
        row["edited_at"] = _now_iso()
        row["edited_by"] = actor or row.get("edited_by") or ""
        return row
    return _replace_one(draft_id, _mutate)


def reject_draft(draft_id: str, *, actor: str = "", reason: str = "") -> dict[str, Any] | None:
    def _mutate(row: dict[str, Any]) -> dict[str, Any]:
        row["status"] = "rejected"
        row["rejected_at"] = _now_iso()
        row["rejected_by"] = actor or ""
        row["reject_reason"] = reason or ""
        return row
    return _replace_one(draft_id, _mutate)


def approve_draft(draft_id: str, *, actor: str = "") -> dict[str, Any] | None:
    """draft 를 wiki page 로 publish. 기존 upsert_doc 호출."""
    row = get_draft(draft_id)
    if not row:
        return None
    if row.get("status") not in ("pending", "edited"):
        return row  # 이미 처리됨
    doc_payload = {
        "doc_id": str(row.get("doc_id_hint") or "")[:80] or None,  # None 이면 upsert_doc 가 자동 생성
        "kind": row.get("kind") or "manual",
        "title": row.get("title") or row.get("target") or "Untitled",
        "body": row.get("body") or "",
        "actor": actor or row.get("created_by") or "",
        "source_event_ids": row.get("source_event_ids") or [],
        "tags": ["wiki_draft_queue"],
    }
    # doc_id 가 None 이면 키에서 제거 (upsert_doc 가 자동 생성).
    if doc_payload["doc_id"] is None:
        doc_payload.pop("doc_id")
    try:
        published = kv.upsert_doc(doc_payload)
    except Exception as exc:
        logger.exception("draft approve upsert failed: %s", exc)
        return None

    def _mutate(r: dict[str, Any]) -> dict[str, Any]:
        r["status"] = "approved"
        r["approved_at"] = _now_iso()
        r["approved_by"] = actor or ""
        r["published_doc_id"] = published.get("doc_id") or ""
        r["published_path"] = published.get("path") or ""
        return r
    return _replace_one(draft_id, _mutate)


def stats() -> dict[str, int]:
    rows = _read_jsonl(DRAFT_QUEUE_FILE)
    out = {"pending": 0, "edited": 0, "approved": 0, "rejected": 0, "total": len(rows)}
    for r in rows:
        s = str(r.get("status") or "")
        if s in out:
            out[s] += 1
    return out
