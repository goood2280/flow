"""Agent-visible Inform registration helper runtime."""
from __future__ import annotations

import datetime as _dt
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from app_v2.modules.semantic_learning import extractor as semantic_extractor
from app_v2.modules.semantic_lexicon import service as semantic_lexicon_service
from core import agent_feedback_penalties
from core import agent_semantic_service
from core.paths import PATHS
from core.utils import load_json, save_json


UNIT_AI_KEY = "inform_registration"
SESSION_TTL_SECONDS = 3600

GRAPH_NODES: tuple[dict[str, str], ...] = (
    {"id": "context_seed", "label": "Session seed", "phase": "context"},
    {"id": "semantic_layer", "label": "용어해석", "phase": "semantic"},
    {"id": "slot_extract", "label": "Slot extract", "phase": "semantic"},
    {"id": "validate_missing", "label": "필수값 확인", "phase": "validate"},
    {"id": "snapshot_preview", "label": "Snapshot preview", "phase": "preview"},
    {"id": "review", "label": "등록 검토", "phase": "review"},
    {"id": "register", "label": "Inform 저장", "phase": "write"},
)

GRAPH_EDGES: tuple[dict[str, str], ...] = (
    {"source": "context_seed", "target": "semantic_layer"},
    {"source": "semantic_layer", "target": "slot_extract"},
    {"source": "slot_extract", "target": "validate_missing"},
    {"source": "validate_missing", "target": "snapshot_preview"},
    {"source": "snapshot_preview", "target": "review"},
    {"source": "review", "target": "register"},
)

STATE_DESIGN: dict[str, dict[str, Any]] = {
    "run_id": {
        "description": "Runtime execution id for this Agent unit run.",
        "producer": "runtime",
        "public": True,
    },
    "session": {
        "description": "One-hour short memory session loaded from FLOW_DATA_ROOT.",
        "producer": "context_seed",
        "public": True,
    },
    "request": {
        "description": "Sanitized prompt, action, and explicit slot overrides.",
        "producer": "runtime",
        "public": True,
    },
    "semantic_frame": {
        "description": "Runtime vocabulary resolution, alias hits, slot hints, unknown terms, and warnings.",
        "producer": "semantic_layer",
        "public": True,
    },
    "slots": {
        "description": "Accumulated Inform registration slots.",
        "producer": "slot_extract",
        "public": True,
    },
    "missing": {
        "description": "Required slot names still needed before review/confirm.",
        "producer": "validate_missing",
        "public": True,
    },
    "snapshot": {
        "description": "Optional SplitTable snapshot preview metadata and embed payload.",
        "producer": "snapshot_preview",
        "public": True,
    },
    "draft": {
        "description": "InformCreate-compatible draft, not written until confirm.",
        "producer": "review",
        "public": True,
    },
    "created_inform": {
        "description": "Inform record returned by create_inform() after confirm.",
        "producer": "register",
        "public": True,
    },
    "trace": {
        "description": "Append-only public node trace rows for Agent UI inspection.",
        "producer": "runtime",
        "public": True,
    },
}

NODE_METADATA: dict[str, dict[str, Any]] = {
    "context_seed": {
        "persona": "Loads the short-memory session and prepares a public state seed.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.session_id", "FLOW_DATA_ROOT/agent_unit_ai_sessions"],
        "writes": ["session", "slots"],
        "shared_state": ["session.session_id", "session.updated_at", "slots"],
        "answer_attach_rule": "Expose session metadata and accumulated slots only; do not write Inform data.",
    },
    "semantic_layer": {
        "persona": "Resolves Inform registration vocabulary against the shared semantic lexicon before slot extraction.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.prompt", "slots", "data/flow-data/semantic/alias_groups.json", "data/flow-data/semantic/intent_hints.json"],
        "writes": ["semantic_frame"],
        "shared_state": ["semantic_frame.alias_hits", "semantic_frame.slot_hints", "semantic_frame.unknown_terms"],
        "answer_attach_rule": "Attach alias hits, slot hints, unknown terms, and public warnings only.",
    },
    "slot_extract": {
        "persona": "Deterministic slot extractor for product, single lot, module, note, mail target, and optional snapshot request.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.prompt", "request.slot_overrides", "semantic_frame.slot_hints", "slots"],
        "writes": ["slots"],
        "shared_state": ["slots.product", "slots.lot_id", "slots.module", "slots.note", "slots.mail_draft"],
        "answer_attach_rule": "Attach sanitized slot values and extraction warnings only.",
    },
    "validate_missing": {
        "persona": "Checks the registration contract before any write is allowed.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["slots"],
        "writes": ["missing"],
        "shared_state": ["missing"],
        "answer_attach_rule": "Attach missing slot names and a short follow-up question.",
    },
    "snapshot_preview": {
        "persona": "Builds an optional Inform SplitTable embed only when the user requested set/KNOB context.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["slots.product", "slots.lot_id", "slots.snapshot_custom_cols", "slots.attached_sets"],
        "writes": ["snapshot"],
        "shared_state": ["snapshot.requested", "snapshot.embed_ready", "draft.embed_table"],
        "answer_attach_rule": "Attach compact snapshot metadata and embed payload; never mutate SplitTable source data.",
    },
    "review": {
        "persona": "Builds the InformCreate draft and requires explicit confirm before register.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["slots", "missing", "snapshot"],
        "writes": ["draft"],
        "shared_state": ["draft.inform", "draft.mail_draft", "requires_confirmation"],
        "answer_attach_rule": "Attach the draft, missing values, and confirmation requirement.",
    },
    "register": {
        "persona": "Calls routers.informs.create_inform() only for action=confirm with complete slots.",
        "prompt": {"system": "", "mode": "deterministic_write"},
        "reads": ["request.action", "draft", "missing"],
        "writes": ["created_inform"],
        "shared_state": ["created_inform.id", "created_inform.mail_draft", "created_inform.lot_identity_snapshot"],
        "answer_attach_rule": "Attach the saved Inform summary after create_inform() returns; mail is not sent.",
    },
}

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SNAPSHOT_COL_RE = re.compile(r"\b(?:KNOB|CUSTOM|INLINE|VM)_[A-Za-z0-9_]+\b", re.IGNORECASE)

_QUESTIONS = {
    "product": "제품명을 알려주세요.",
    "lot_id": "단일 LOT ID를 알려주세요.",
    "module": "Inform module을 알려주세요.",
    "note": "Inform에 저장할 note 내용을 알려주세요.",
    "mail_target": "메일 대상(to, to_users, groups, extra_emails 중 하나)을 알려주세요.",
    "snapshot_custom_cols": "첨부할 KNOB/CUSTOM/set snapshot 컬럼이나 세트를 알려주세요.",
}

_INFORM_SEMANTIC_ALIAS_SEED: dict[str, list[str]] = {
    "product": ["product", "prod", "제품", "제품명"],
    "lot_id": ["lot", "lot_id", "LOT", "로트"],
    "module": ["module", "mod", "모듈", "담당모듈"],
    "note": ["note", "text", "내용", "노트", "메시지"],
    "mail_target": ["mail", "email", "to", "담당자", "수신자", "메일"],
    "snapshot_custom_cols": ["snapshot", "knob", "custom", "split table", "splittable", "스냅샷", "노브", "세트"],
}

_SLOT_HINT_KEYS = {"product", "lot_id", "module", "note"}
_SEMANTIC_VALUE_STOPWORDS = {
    "알려줘",
    "알려주세요",
    "등록",
    "생성",
    "추가",
    "확인",
    "요청",
}


def inform_registration_graph(statuses: dict[str, str] | None = None) -> dict[str, Any]:
    statuses = statuses or {}
    return {
        "nodes": [
            {
                **node,
                **deepcopy(NODE_METADATA.get(node["id"], {})),
                "state_io": {
                    "reads": list(NODE_METADATA.get(node["id"], {}).get("reads") or []),
                    "writes": list(NODE_METADATA.get(node["id"], {}).get("writes") or []),
                },
                "status": statuses.get(node["id"], "pending"),
            }
            for node in GRAPH_NODES
        ],
        "edges": [dict(edge) for edge in GRAPH_EDGES],
        "state_design": deepcopy(STATE_DESIGN),
    }


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _session_dir() -> Path:
    return PATHS.data_root / "agent_unit_ai_sessions" / UNIT_AI_KEY


def _new_session_id() -> str:
    return "inform_reg_" + uuid.uuid4().hex[:16]


def _safe_session_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if _SESSION_ID_RE.match(text) else ""


def _session_path(session_id: str) -> Path:
    return _session_dir() / f"{session_id}.json"


def _cleanup_expired_sessions(now_ts: float | None = None) -> None:
    root = _session_dir()
    if not root.exists():
        return
    now_ts = time.time() if now_ts is None else now_ts
    for fp in root.glob("*.json"):
        try:
            data = load_json(fp, {})
            updated_ts = float(data.get("updated_ts") or 0)
            if not updated_ts:
                updated_ts = fp.stat().st_mtime
            if now_ts - updated_ts > SESSION_TTL_SECONDS:
                fp.unlink(missing_ok=True)
        except Exception:
            continue


def _load_session(raw_session_id: Any) -> tuple[dict[str, Any], bool]:
    _cleanup_expired_sessions()
    session_id = _safe_session_id(raw_session_id)
    if session_id:
        fp = _session_path(session_id)
        data = load_json(fp, {})
        if isinstance(data, dict) and data.get("session_id") == session_id:
            updated_ts = float(data.get("updated_ts") or 0)
            if not updated_ts or time.time() - updated_ts <= SESSION_TTL_SECONDS:
                data.setdefault("slots", {})
                data.setdefault("runs", [])
                return data, False
    now = _now_iso()
    new_session = {
        "session_id": _new_session_id(),
        "created_at": now,
        "updated_at": now,
        "updated_ts": time.time(),
        "slots": {},
        "runs": [],
        "status": "collecting",
    }
    return new_session, True


def _save_session(session: dict[str, Any]) -> None:
    now = _now_iso()
    session["updated_at"] = now
    session["updated_ts"] = time.time()
    _session_dir().mkdir(parents=True, exist_ok=True)
    save_json(_session_path(str(session.get("session_id") or "")), session, indent=2)


def _clean_text(value: Any, max_len: int = 2000) -> str:
    return str(value or "").replace("\x00", " ").strip()[:max(1, max_len)]


def _string_list(value: Any, limit: int = 40) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _clean_text(item, 160)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _clean_email_list(value: Any, limit: int = 40) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in _string_list(value, limit=limit):
        if "@" not in item:
            continue
        marker = item.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out


def _merge_mail_draft(base: Any, update: Any) -> dict[str, Any]:
    current = base if isinstance(base, dict) else {}
    incoming = update if isinstance(update, dict) else {}
    out = {
        "to": _clean_email_list(current.get("to")),
        "recipients": _clean_email_list(current.get("recipients")),
        "to_users": _string_list(current.get("to_users")),
        "groups": _string_list(current.get("groups")),
        "extra_emails": _clean_email_list(current.get("extra_emails")),
        "subject": _clean_text(current.get("subject"), 300),
        "body": _clean_text(current.get("body"), 5000),
    }
    for key in ("to", "recipients", "extra_emails"):
        out[key] = _clean_email_list([*out.get(key, []), *(_clean_email_list(incoming.get(key)))])
    for key in ("to_users", "groups"):
        out[key] = _string_list([*out.get(key, []), *(_string_list(incoming.get(key)))])
    for key, max_len in (("subject", 300), ("body", 5000)):
        text = _clean_text(incoming.get(key), max_len)
        if text:
            out[key] = text
    return {k: v for k, v in out.items() if v or k in ("subject", "body")}


def _mail_targets_present(mail_draft: Any) -> bool:
    if not isinstance(mail_draft, dict):
        return False
    return any(mail_draft.get(key) for key in ("to", "recipients", "to_users", "groups", "extra_emails"))


def _snapshot_requested(prompt: str, slots: dict[str, Any]) -> bool:
    if bool(slots.get("wants_snapshot")):
        return True
    if re.search(r"\b(knob|custom|split\s*table|splittable|snapshot|set)\b", prompt, re.IGNORECASE):
        return True
    return any(token in prompt for token in ("노브", "세트", "스냅샷"))


def _first_regex(patterns: list[str], text: str, max_len: int = 160) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _clean_text(match.group(1), max_len).strip(" ,;")
    return ""


def _norm_semantic_token(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _inform_alias_groups() -> dict[str, list[str]]:
    try:
        return semantic_lexicon_service.effective_alias_groups(_INFORM_SEMANTIC_ALIAS_SEED)
    except Exception:
        return deepcopy(_INFORM_SEMANTIC_ALIAS_SEED)


def _inform_intent_hints() -> dict[str, list[str]]:
    try:
        return semantic_lexicon_service.effective_intent_hints({})
    except Exception:
        return {}


def _semantic_alias_hits(prompt: str, alias_groups: dict[str, list[str]]) -> tuple[list[dict[str, str]], set[str]]:
    prompt_norm = _norm_semantic_token(prompt)
    hits: list[dict[str, str]] = []
    matched_norms: set[str] = set()
    if not prompt_norm:
        return hits, matched_norms
    for canonical, aliases in (alias_groups or {}).items():
        canonical_text = str(canonical or "").strip()
        if not canonical_text:
            continue
        for alias in [canonical_text, *list(aliases or [])]:
            alias_text = str(alias or "").strip()
            alias_norm = _norm_semantic_token(alias_text)
            if len(alias_norm) < 2:
                continue
            if alias_norm and alias_norm in prompt_norm:
                hits.append({"canonical": canonical_text, "alias": alias_text})
                matched_norms.add(alias_norm)
                break
    return hits, matched_norms


def _semantic_value_after_alias(prompt: str, aliases: list[str], max_len: int = 160) -> str:
    for alias in aliases:
        alias_text = str(alias or "").strip()
        if len(_norm_semantic_token(alias_text)) < 2:
            continue
        pattern = rf"{re.escape(alias_text)}\s*[:=]?\s*([A-Za-z0-9가-힣_.@/\-]+)"
        value = _first_regex([pattern], prompt, max_len=max_len)
        if value and _norm_semantic_token(value) not in _SEMANTIC_VALUE_STOPWORDS:
            return value
    return ""


def _semantic_slot_hints(prompt: str, alias_hits: list[dict[str, str]], alias_groups: dict[str, list[str]]) -> dict[str, Any]:
    hit_canonicals = {str(hit.get("canonical") or "") for hit in alias_hits}
    hints: dict[str, Any] = {}
    for key in sorted(_SLOT_HINT_KEYS & hit_canonicals):
        aliases = [key, *list(alias_groups.get(key) or [])]
        value = _semantic_value_after_alias(prompt, aliases, max_len=5000 if key == "note" else 160)
        if value:
            hints[key] = value
    if "snapshot_custom_cols" in hit_canonicals or _snapshot_requested(prompt, hints):
        hints["wants_snapshot"] = True
        cols = _string_list(_SNAPSHOT_COL_RE.findall(prompt), limit=80)
        if cols:
            hints["snapshot_custom_cols"] = cols
    return hints


def _semantic_unknown_terms(
    prompt: str,
    alias_groups: dict[str, list[str]],
    matched_norms: set[str],
    ignored_values: list[Any] | None = None,
) -> list[str]:
    known_norms: set[str] = set(matched_norms)
    for canonical, aliases in (alias_groups or {}).items():
        for value in [canonical, *list(aliases or [])]:
            norm = _norm_semantic_token(value)
            if norm:
                known_norms.add(norm)
    for value in ignored_values or []:
        norm = _norm_semantic_token(value)
        if norm:
            known_norms.add(norm)
    out: list[str] = []
    for term in semantic_extractor.extract_terms(prompt):
        norm = _norm_semantic_token(term)
        if not norm or norm in known_norms:
            continue
        if any(norm in known or known in norm for known in known_norms if len(known) >= 3):
            continue
        out.append(term)
        if len(out) >= 20:
            break
    return out


def _semantic_layer(prompt: str, current_slots: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    resolved = agent_semantic_service.resolve(prompt)
    alias_hits = list(resolved.get("alias_hits") or [])
    slot_hints = deepcopy(resolved.get("slot_hints") or {})
    unknown_terms = list(resolved.get("unknown_terms") or [])
    intent_matches = dict(resolved.get("intent_matches") or {})
    warnings = list(resolved.get("warnings") or [])
    semantic_frame = {
        "alias_hits": alias_hits,
        "slot_hints": deepcopy(slot_hints),
        "unknown_terms": unknown_terms,
        "source_catalog_matches": list(resolved.get("source_catalog_matches") or []),
        "intent_matches": intent_matches,
        "current_slot_keys": sorted((current_slots or {}).keys()),
        "warnings": warnings,
    }
    return semantic_frame, slot_hints, warnings


def _extract_slots_from_prompt(prompt: str) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    if not prompt:
        return slots

    product = _first_regex([
        r"\bproduct\s*[:=]?\s*([A-Za-z0-9_-]+)",
        r"\bprod\s*[:=]?\s*([A-Za-z0-9_-]+)",
        r"제품\s*[:=]?\s*([A-Za-z0-9_-]+)",
    ], prompt)
    lot_id = _first_regex([
        r"\blot_id\s*[:=]?\s*([A-Za-z0-9_.\-/]+)",
        r"\blot\s*[:=]?\s*([A-Za-z0-9_.\-/]+)",
        r"LOT\s*[:=]?\s*([A-Za-z0-9_.\-/]+)",
        r"로트\s*[:=]?\s*([A-Za-z0-9_.\-/]+)",
    ], prompt)
    module = _first_regex([
        r"\bmodule\s*[:=]?\s*([A-Za-z0-9_-]+)",
        r"\bmod\s*[:=]?\s*([A-Za-z0-9_-]+)",
        r"모듈\s*[:=]?\s*([A-Za-z0-9_-]+)",
    ], prompt)
    note = _first_regex([
        r"(?s)\bnote\s*[:=]\s*(.+)$",
        r"(?s)\btext\s*[:=]\s*(.+)$",
        r"(?s)내용\s*[:=]\s*(.+)$",
        r"(?s)노트\s*[:=]\s*(.+)$",
    ], prompt, max_len=5000)
    if not note and re.search(r"이상|불량|문제|확인|요청|알려|issue|fail|failure|change|변경", prompt, re.IGNORECASE):
        note = _clean_text(prompt, 5000)

    if product:
        slots["product"] = product
    if lot_id:
        slots["lot_id"] = lot_id
    if module:
        slots["module"] = module
    if note:
        slots["note"] = note

    emails = _clean_email_list(_EMAIL_RE.findall(prompt))
    groups = _string_list(_first_regex([r"\bgroups?\s*[:=]\s*([^;\n]+)", r"그룹\s*[:=]\s*([^;\n]+)"], prompt))
    users = _string_list(_first_regex([r"\bto_users?\s*[:=]\s*([^;\n]+)", r"\buser\s*[:=]\s*([^;\n]+)", r"담당자\s*[:=]\s*([^;\n]+)"], prompt))
    mail_draft: dict[str, Any] = {}
    if emails:
        mail_draft["to"] = emails
    if groups:
        mail_draft["groups"] = groups
    if users:
        mail_draft["to_users"] = users
    if mail_draft:
        slots["mail_draft"] = mail_draft

    snapshot_cols = _string_list(_SNAPSHOT_COL_RE.findall(prompt))
    explicit_cols = _first_regex([
        r"\bsnapshot_cols?\s*[:=]\s*([^;\n]+)",
        r"\bcustom_cols?\s*[:=]\s*([^;\n]+)",
        r"\bknobs?\s*[:=]\s*([^;\n]+)",
    ], prompt)
    if explicit_cols:
        snapshot_cols = _string_list([*snapshot_cols, *_string_list(explicit_cols)])
    if snapshot_cols:
        slots["snapshot_custom_cols"] = snapshot_cols
    if _snapshot_requested(prompt, slots):
        slots["wants_snapshot"] = True
    return slots


def _normalize_overrides(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    slots: dict[str, Any] = {}
    for source_key, target_key, max_len in (
        ("product", "product", 160),
        ("lot_id", "lot_id", 160),
        ("module", "module", 160),
        ("note", "note", 5000),
        ("text", "note", 5000),
        ("reason", "reason", 160),
        ("fab_lot_id_at_save", "fab_lot_id_at_save", 160),
    ):
        text = _clean_text(raw.get(source_key), max_len)
        if text:
            slots[target_key] = text
    if raw.get("mail_draft") is not None:
        slots["mail_draft"] = _merge_mail_draft({}, raw.get("mail_draft"))
    mail_update: dict[str, Any] = {}
    for key in ("to", "recipients", "to_users", "groups", "extra_emails", "subject", "body"):
        if raw.get(key) is not None:
            mail_update[key] = raw.get(key)
    if mail_update:
        slots["mail_draft"] = _merge_mail_draft(slots.get("mail_draft"), mail_update)
    cols = raw.get("snapshot_custom_cols", raw.get("custom_cols"))
    if cols is not None:
        slots["snapshot_custom_cols"] = _string_list(cols, limit=80)
        if slots["snapshot_custom_cols"]:
            slots["wants_snapshot"] = True
    if raw.get("attached_sets") is not None:
        attached = raw.get("attached_sets")
        slots["attached_sets"] = attached if isinstance(attached, list) else []
        if slots["attached_sets"]:
            slots["wants_snapshot"] = True
    if raw.get("wants_snapshot") is not None:
        slots["wants_snapshot"] = bool(raw.get("wants_snapshot"))
    return slots


def _merge_slots(base: dict[str, Any], *updates: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base or {})
    for update in updates:
        for key, value in (update or {}).items():
            if key == "mail_draft":
                out["mail_draft"] = _merge_mail_draft(out.get("mail_draft"), value)
            elif key == "snapshot_custom_cols":
                out[key] = _string_list([*(out.get(key) or []), *(_string_list(value, limit=80))], limit=80)
            elif key == "attached_sets":
                out[key] = value if isinstance(value, list) else []
            elif value not in (None, "", []):
                out[key] = value
    return out


def _validate_missing(slots: dict[str, Any]) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    warnings: list[str] = []
    for key in ("product", "lot_id", "module", "note"):
        if not _clean_text(slots.get(key)):
            missing.append(key)
    lot_id = _clean_text(slots.get("lot_id"), 240)
    if lot_id and re.search(r"[,;\s]+", lot_id):
        warnings.append("v1 supports one Inform registration per run; provide a single lot_id.")
        if "lot_id" not in missing:
            missing.append("lot_id")
    if not _mail_targets_present(slots.get("mail_draft")):
        missing.append("mail_target")
    if bool(slots.get("wants_snapshot")) and not slots.get("snapshot_custom_cols") and not slots.get("attached_sets"):
        missing.append("snapshot_custom_cols")
    return missing, warnings


def _question_for_missing(missing: list[str]) -> str:
    return _QUESTIONS.get(missing[0], "") if missing else ""


def _snapshot_row_count(embed: Any) -> int:
    if not isinstance(embed, dict):
        return 0
    rows = embed.get("rows")
    if isinstance(rows, list) and rows:
        return len(rows)
    st_view = embed.get("st_view") if isinstance(embed.get("st_view"), dict) else {}
    st_rows = st_view.get("rows") if isinstance(st_view.get("rows"), list) else []
    return len(st_rows)


def _build_snapshot(slots: dict[str, Any], missing: list[str], warnings: list[str]) -> dict[str, Any]:
    requested = bool(slots.get("wants_snapshot"))
    custom_cols = _string_list(slots.get("snapshot_custom_cols"), limit=80)
    attached_sets = slots.get("attached_sets") if isinstance(slots.get("attached_sets"), list) else []
    snapshot: dict[str, Any] = {
        "requested": requested,
        "custom_cols": custom_cols,
        "attached_sets_count": len(attached_sets),
        "embed_ready": False,
        "status": "skipped" if not requested else "pending",
    }
    if not requested:
        return snapshot
    if any(key in missing for key in ("product", "lot_id", "snapshot_custom_cols")) and not attached_sets:
        snapshot["status"] = "waiting_for_slots"
        return snapshot
    if custom_cols:
        try:
            from routers import informs

            embed = informs._build_splittable_snapshot_embed(
                informs.SplitTableSnapshotReq(
                    product=_clean_text(slots.get("product"), 160),
                    lot_id=_clean_text(slots.get("lot_id"), 160),
                    custom_cols=custom_cols,
                )
            )
            snapshot.update({
                "status": "ready",
                "embed_ready": True,
                "embed_table": embed,
                "row_count": _snapshot_row_count(embed),
                "source": embed.get("source") if isinstance(embed, dict) else "",
            })
        except Exception as exc:
            msg = f"SplitTable snapshot preview failed: {type(exc).__name__}: {exc}"
            warnings.append(msg)
            snapshot.update({"status": "failed", "error": msg})
    elif attached_sets:
        snapshot.update({"status": "ready", "embed_ready": True, "row_count": 0})
    return snapshot


def _build_draft(slots: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    mail_draft = _merge_mail_draft({}, slots.get("mail_draft"))
    inform_req: dict[str, Any] = {
        "lot_id": _clean_text(slots.get("lot_id"), 160),
        "product": _clean_text(slots.get("product"), 160),
        "module": _clean_text(slots.get("module"), 160),
        "reason": _clean_text(slots.get("reason"), 160) or "PEMS",
        "text": _clean_text(slots.get("note"), 5000),
        "mail_draft": mail_draft,
    }
    fab_lot = _clean_text(slots.get("fab_lot_id_at_save"), 160)
    if fab_lot:
        inform_req["fab_lot_id_at_save"] = fab_lot
    embed_table = snapshot.get("embed_table") if isinstance(snapshot.get("embed_table"), dict) else None
    if embed_table:
        inform_req["embed_table"] = embed_table
    attached_sets = slots.get("attached_sets") if isinstance(slots.get("attached_sets"), list) else []
    if attached_sets:
        inform_req["attached_sets"] = attached_sets
    return {
        "inform": inform_req,
        "slots": deepcopy(slots),
        "mail_draft": mail_draft,
        "snapshot": {
            key: value
            for key, value in snapshot.items()
            if key != "embed_table"
        },
        "embed_table": embed_table,
    }


def _register_inform(draft: dict[str, Any], request: Request | None) -> dict[str, Any]:
    if request is None:
        raise HTTPException(status_code=400, detail="request is required for Inform registration")
    from routers import informs

    req = informs.InformCreate(**(draft.get("inform") or {}))
    return informs.create_inform(req, request).get("inform") or {}


def _trace_row(node_id: str, status: str, output: Any, warnings: list[str], started: float, input_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    label = next((node["label"] for node in GRAPH_NODES if node["id"] == node_id), node_id)
    return {
        "node_id": node_id,
        "label": label,
        "status": status,
        "input_summary": input_summary or {},
        "output": output,
        "warnings": list(warnings or []),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _status_from_warnings(warnings: list[str], default: str = "success") -> str:
    return "warning" if warnings else default


def _history_entry(
    *,
    run_id: str,
    username: str,
    prompt: str,
    action: str,
    status: str,
    answer: str,
    question: str,
    missing: list[str],
    semantic_frame: dict[str, Any],
    slots: dict[str, Any],
    draft: dict[str, Any],
    requires_confirmation: bool,
    created_inform: dict[str, Any] | None,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "history_id": run_id,
        "run_id": run_id,
        "timestamp": _now_iso(),
        "username": _clean_text(username, 80),
        "prompt": prompt,
        "natural_language": prompt,
        "action": action,
        "status": status,
        "answer": answer,
        "question": question,
        "missing": list(missing),
        "semantic_frame": deepcopy(semantic_frame or {}),
        "slots": deepcopy(slots),
        "draft": {
            "inform": deepcopy((draft or {}).get("inform") or {}),
            "snapshot": deepcopy((draft or {}).get("snapshot") or {}),
            "mail_draft": deepcopy((draft or {}).get("mail_draft") or {}),
        },
        "requires_confirmation": bool(requires_confirmation),
        "created_inform": deepcopy(created_inform or {}),
        "warnings": list(warnings or []),
    }


def run_inform_registration_runtime(
    payload: dict[str, Any],
    *,
    username: str = "",
    request: Request | None = None,
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del agent_context
    body = deepcopy(payload or {})
    prompt = _clean_text(body.get("prompt") or body.get("natural_language"), 5000)
    action = _clean_text(body.get("action") or "continue", 40).casefold()
    if action not in {"continue", "confirm", "cancel"}:
        raise HTTPException(status_code=400, detail="action must be continue, confirm, or cancel")
    run_id = "agent_inform_" + uuid.uuid4().hex[:12]
    session, new_session = _load_session(body.get("session_id"))
    slots = deepcopy(session.get("slots") or {})
    request_payload = {
        "prompt": prompt,
        "session_id": session.get("session_id") or "",
        "action": action,
        "slot_overrides": body.get("slot_overrides") if isinstance(body.get("slot_overrides"), dict) else {},
    }
    trace: list[dict[str, Any]] = []
    warnings: list[str] = []
    semantic_frame: dict[str, Any] = {}

    started = time.perf_counter()
    context_warnings = ["new short-memory session created"] if new_session else []
    trace.append(_trace_row(
        "context_seed",
        _status_from_warnings(context_warnings),
        {
            "session_id": session.get("session_id"),
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "existing_slots": deepcopy(slots),
        },
        context_warnings,
        started,
        {"requested_session_id": _clean_text(body.get("session_id"), 100)},
    ))
    warnings.extend(context_warnings)

    if action == "cancel":
        session["status"] = "cancelled"
        session["slots"] = slots
        answer = "Inform 등록 도우미 session을 취소했습니다."
        missing: list[str] = []
        draft: dict[str, Any] = {}
        created_inform: dict[str, Any] = {}
        requires_confirmation = False
        statuses = {row["node_id"]: row["status"] for row in trace}
        for node_id in ("semantic_layer", "slot_extract", "validate_missing", "snapshot_preview", "review", "register"):
            statuses[node_id] = "skipped"
        history = _history_entry(
            run_id=run_id,
            username=username,
            prompt=prompt,
            action=action,
            status="cancelled",
            answer=answer,
            question="",
            missing=missing,
            semantic_frame=semantic_frame,
            slots=slots,
            draft=draft,
            requires_confirmation=requires_confirmation,
            created_inform=created_inform,
            warnings=warnings,
        )
        session.setdefault("runs", []).append(history)
        session["runs"] = session["runs"][-50:]
        _save_session(session)
        return {
            "ok": True,
            "unit_ai": UNIT_AI_KEY,
            "run_id": run_id,
            "session_id": session.get("session_id"),
            "status": "cancelled",
            "answer": answer,
            "question": "",
            "missing": missing,
            "semantic_frame": semantic_frame,
            "slots": slots,
            "draft": draft,
            "requires_confirmation": requires_confirmation,
            "created_inform": created_inform,
            "graph": inform_registration_graph(statuses),
            "trace": trace,
            "warnings": warnings,
        }

    started = time.perf_counter()
    semantic_frame, semantic_slot_hints, semantic_warnings = _semantic_layer(prompt, slots)
    trace.append(_trace_row(
        "semantic_layer",
        _status_from_warnings(semantic_warnings),
        semantic_frame,
        semantic_warnings,
        started,
        {"prompt_chars": len(prompt), "existing_slot_keys": sorted(slots.keys())},
    ))
    warnings.extend(semantic_warnings)

    started = time.perf_counter()
    extracted = _extract_slots_from_prompt(prompt)
    overrides = _normalize_overrides(body.get("slot_overrides"))
    slots = _merge_slots(slots, semantic_slot_hints, extracted, overrides)
    trace.append(_trace_row(
        "slot_extract",
        "success",
        {
            "semantic_slot_hints": semantic_slot_hints,
            "extracted": extracted,
            "overrides": overrides,
            "slots": deepcopy(slots),
        },
        [],
        started,
        {"prompt_chars": len(prompt), "override_keys": sorted(list((body.get("slot_overrides") or {}).keys())) if isinstance(body.get("slot_overrides"), dict) else []},
    ))

    started = time.perf_counter()
    missing, validate_warnings = _validate_missing(slots)
    trace.append(_trace_row(
        "validate_missing",
        "warning" if missing or validate_warnings else "success",
        {"missing": missing, "question": _question_for_missing(missing)},
        validate_warnings,
        started,
        {"slot_keys": sorted(slots.keys())},
    ))
    warnings.extend(validate_warnings)

    started = time.perf_counter()
    snapshot_warnings: list[str] = []
    snapshot = _build_snapshot(slots, missing, snapshot_warnings)
    snapshot_status = "skipped"
    if snapshot.get("requested"):
        snapshot_status = "success" if snapshot.get("embed_ready") else ("warning" if snapshot_warnings or snapshot.get("status") != "waiting_for_slots" else "skipped")
    trace.append(_trace_row(
        "snapshot_preview",
        snapshot_status,
        {key: value for key, value in snapshot.items() if key != "embed_table"},
        snapshot_warnings,
        started,
        {
            "requested": bool(snapshot.get("requested")),
            "custom_cols": snapshot.get("custom_cols") or [],
        },
    ))
    warnings.extend(snapshot_warnings)

    started = time.perf_counter()
    draft = _build_draft(slots, snapshot)
    requires_confirmation = not missing and action != "confirm"
    if missing:
        status = "collecting"
        question = _question_for_missing(missing)
        answer = "누락된 Inform 등록값이 있습니다."
    elif action == "confirm":
        status = "registering"
        question = ""
        answer = "확인 요청을 받아 Inform 저장을 진행합니다."
    else:
        status = "review"
        question = "위 draft로 Inform을 저장하려면 등록 버튼으로 confirm 해주세요."
        answer = "Inform draft가 준비되었습니다. 아직 저장하지 않았습니다."
    trace.append(_trace_row(
        "review",
        "warning" if missing else "success",
        {
            "status": status,
            "missing": missing,
            "requires_confirmation": requires_confirmation,
            "draft": {
                "inform": draft.get("inform"),
                "snapshot": draft.get("snapshot"),
                "mail_draft": draft.get("mail_draft"),
            },
        },
        [],
        started,
        {"action": action},
    ))

    created_inform: dict[str, Any] = {}
    started = time.perf_counter()
    register_warnings: list[str] = []
    register_status = "skipped"
    if action == "confirm" and not missing:
        try:
            created_inform = _register_inform(draft, request)
            status = "registered"
            answer = "Inform이 저장되었습니다. 메일은 발송하지 않고 mail_draft만 보존했습니다."
            register_status = "success"
            requires_confirmation = False
        except Exception as exc:
            register_warnings.append(f"{type(exc).__name__}: {exc}")
            status = "blocked"
            answer = "Inform 저장에 실패했습니다."
            register_status = "failed"
    trace.append(_trace_row(
        "register",
        register_status,
        {
            "created_inform": {
                "id": created_inform.get("id") if isinstance(created_inform, dict) else "",
                "mail_draft": created_inform.get("mail_draft") if isinstance(created_inform, dict) else {},
                "lot_identity_snapshot": created_inform.get("lot_identity_snapshot") if isinstance(created_inform, dict) else {},
            } if created_inform else {},
            "write_attempted": action == "confirm" and not missing,
        },
        register_warnings,
        started,
        {"action": action, "missing": missing},
    ))
    warnings.extend(register_warnings)

    session["status"] = status
    session["slots"] = slots
    session["last_draft"] = draft
    session["last_missing"] = missing
    history = _history_entry(
        run_id=run_id,
        username=username,
        prompt=prompt,
        action=action,
        status=status,
        answer=answer,
        question=question,
        missing=missing,
        semantic_frame=semantic_frame,
        slots=slots,
        draft=draft,
        requires_confirmation=requires_confirmation,
        created_inform=created_inform,
        warnings=warnings,
    )
    session.setdefault("runs", []).append(history)
    session["runs"] = session["runs"][-50:]
    _save_session(session)

    statuses = {str(row.get("node_id")): str(row.get("status") or "pending") for row in trace}
    result = {
        "ok": status not in {"blocked"},
        "unit_ai": UNIT_AI_KEY,
        "run_id": run_id,
        "session_id": session.get("session_id"),
        "status": status,
        "answer": answer,
        "question": question,
        "missing": missing,
        "semantic_frame": semantic_frame,
        "slots": slots,
        "draft": draft,
        "requires_confirmation": requires_confirmation,
        "created_inform": created_inform,
        "graph": inform_registration_graph(statuses),
        "trace": trace,
        "warnings": warnings,
    }
    return agent_feedback_penalties.annotate_result(UNIT_AI_KEY, result)


def list_inform_registration_history(limit: int = 50, *, username: str = "") -> list[dict[str, Any]]:
    _cleanup_expired_sessions()
    rows: list[dict[str, Any]] = []
    root = _session_dir()
    if not root.exists():
        return []
    for fp in root.glob("*.json"):
        data = load_json(fp, {})
        if not isinstance(data, dict):
            continue
        session_id = str(data.get("session_id") or "")
        for order, run in enumerate(data.get("runs") or []):
            if not isinstance(run, dict):
                continue
            row = deepcopy(run)
            row["session_id"] = session_id
            row["_session_order"] = order
            if username and row.get("username") and row.get("username") != username:
                continue
            rows.append(row)
    rows.sort(key=lambda item: (str(item.get("timestamp") or ""), int(item.get("_session_order") or 0)), reverse=True)
    out = rows[: max(1, min(int(limit or 50), 200))]
    for row in out:
        row.pop("_session_order", None)
    return out
