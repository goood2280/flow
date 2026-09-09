"""Bridge abnormal TEG mapfile checks into the in-app notification feed.

The mapfile verifier owns inspection and caching.  This module only decides
which inspection results require attention, resolves the issue-specific
``TEG_CHECK`` group, and publishes one critical notification per file version
and recipient.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from core.paths import PATHS
from core.utils import load_json, save_json

logger = logging.getLogger("flow.mapfile_alerts")

GROUP_NAME = "TEG_CHECK"
EVENT_TYPE = "teg_mapfile_abnormal"
STATE_FILE_NAME = "mapfile_alert_state.json"


def get_alert_state_path() -> Path:
    return PATHS.data_root / "teg_map" / STATE_FILE_NAME


def _load_groups() -> list[dict[str, Any]]:
    """Use the same normalized group records as the Groups UI."""
    from routers import groups

    return [row for row in groups._load() if isinstance(row, dict)]


def _teg_check_members(group_loader: Callable[[], list[dict[str, Any]]]) -> tuple[str, list[str]]:
    group = next(
        (
            row
            for row in group_loader()
            if str(row.get("name") or "").strip().casefold() == GROUP_NAME.casefold()
        ),
        None,
    )
    if group is None:
        return "missing", []

    members: list[str] = []
    seen: set[str] = set()
    for raw in group.get("members") or []:
        username = str(raw or "").strip()
        key = username.casefold()
        if username and key not in seen:
            seen.add(key)
            members.append(username)
    return ("ready" if members else "empty"), members


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _abnormal_reasons(file_result: dict[str, Any]) -> list[str]:
    """Return evidence strings; an empty list means the file is verified green."""
    light = str(
        file_result.get("traffic_light") or file_result.get("overall_light") or "gray"
    ).strip().casefold()
    status = str(file_result.get("status") or "").strip().casefold()
    error = str(file_result.get("error") or "").strip()
    summary = file_result.get("summary") or {}
    targets = file_result.get("targets") or {}

    reasons: list[str] = []
    if status and status != "ok":
        reasons.append(f"검사 상태 {status}")
    if error:
        reasons.append(f"검사 오류: {error}")
    if light in {"red", "yellow", "orange"}:
        reasons.append(f"신호등 {light}")
    elif light in {"gray", "grey", "dim", "none", ""}:
        reasons.append("판정 불가(gray)")
    elif light != "green":
        reasons.append(f"알 수 없는 신호등 {light}")

    missing = _positive_int(targets.get("missing"))
    if missing:
        reasons.append(f"필수 대상 {missing}개 누락")

    red = _positive_int(summary.get("red") or summary.get("mismatch"))
    yellow = _positive_int(summary.get("yellow") or summary.get("warning"))
    orange = _positive_int(summary.get("orange"))
    if red:
        reasons.append(f"red {red}건")
    if yellow or orange:
        reasons.append(f"yellow {yellow + orange}건")

    # Keep a clean green result quiet.  All other outcomes, including gray and
    # missing targets, need a person to confirm rather than being treated safe.
    return list(dict.fromkeys(reasons))


def _first_issue(file_result: dict[str, Any]) -> str:
    issues = file_result.get("issues") or []
    if not isinstance(issues, list):
        return ""
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        name = str(issue.get("teg_name") or issue.get("ref_teg") or "").strip()
        reason = str(issue.get("reason") or issue.get("status") or "").strip()
        text = " · ".join(part for part in (name, reason) if part)
        if text:
            return text[:240]
    return ""


def _delivery_key(filename: str, signature: str, recipient: str) -> str:
    raw = "\0".join((filename, signature, recipient.casefold())).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _notification_id(filename: str, signature: str, recipient: str) -> str:
    # Stable across retries so a crash after append but before state persistence
    # does not create a visibly distinct notification version.
    return "teg-" + _delivery_key(filename, signature, recipient)[:20]


def _empty_group_warning(status: str) -> str:
    if status == "missing":
        return "TEG_CHECK 그룹이 없습니다. 그룹을 만든 뒤 다음 검사에서 알림을 재시도합니다."
    return "TEG_CHECK 그룹에 멤버가 없습니다. 멤버를 추가한 뒤 다음 검사에서 알림을 재시도합니다."


def publish_mapfile_alerts(
    inspection: dict[str, Any],
    *,
    state_path: Path | None = None,
    group_loader: Callable[[], list[dict[str, Any]]] | None = None,
    emitter: Callable[..., bool] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Publish critical alerts for abnormal mapfiles in one product result.

    Deduplication is persisted per ``filename + signature + recipient``.  A
    missing/empty group never consumes the version, so the next scan retries as
    soon as membership is configured.  User notification opt-outs remain
    authoritative through :func:`core.notify.emit_event`.
    """
    if group_loader is None:
        group_loader = _load_groups
    group_status, recipients = _teg_check_members(group_loader)
    if group_status != "ready":
        warning = _empty_group_warning(group_status)
        logger.warning(warning)
        return {
            "ok": False,
            "group": GROUP_NAME,
            "group_status": group_status,
            "retry_required": True,
            "warning": warning,
            "abnormal_files": 0,
            "published": 0,
            "duplicates": 0,
            "suppressed": 0,
            "failed": 0,
        }

    files = inspection.get("files") or []
    abnormal: list[tuple[dict[str, Any], list[str]]] = []
    for row in files if isinstance(files, list) else []:
        if not isinstance(row, dict):
            continue
        reasons = _abnormal_reasons(row)
        if reasons:
            abnormal.append((row, reasons))
    if not abnormal:
        return {
            "ok": True,
            "group": GROUP_NAME,
            "group_status": "ready",
            "retry_required": False,
            "warning": "",
            "abnormal_files": 0,
            "published": 0,
            "duplicates": 0,
            "suppressed": 0,
            "failed": 0,
        }

    if emitter is None:
        from core.notify import emit_event

        emitter = emit_event
    if state_path is None:
        state_path = get_alert_state_path()
    state_path = Path(state_path)
    timestamp = (now or dt.datetime.now()).isoformat(timespec="seconds")
    vehicle = str(inspection.get("vehicle") or "").strip()
    product_code = str(inspection.get("product_code") or "").strip()

    published = duplicates = suppressed = failed = 0
    failures: list[str] = []

    from core.file_transaction import file_transaction

    with file_transaction(state_path):
        state = load_json(state_path, {})
        if not isinstance(state, dict):
            state = {}
        deliveries = state.get("deliveries")
        if not isinstance(deliveries, dict):
            deliveries = {}
            state["deliveries"] = deliveries
        state["version"] = 1

        for row, reasons in abnormal:
            filename = str(row.get("filename") or "(이름 없음)").strip()
            signature = str(row.get("signature") or "").strip()
            issue = _first_issue(row)
            reason_text = " · ".join(reasons)
            body = reason_text + (f" · 첫 근거: {issue}" if issue else "")
            target_search = "?" + urlencode(
                {
                    "vehicle": vehicle,
                    "view": "traffic",
                    "filename": filename,
                }
            )
            payload = {
                "category": GROUP_NAME,
                "product": vehicle,
                "product_code": product_code,
                "vehicle": vehicle,
                "filename": filename,
                "signature": signature,
                "traffic_light": row.get("traffic_light") or row.get("overall_light") or "gray",
                "reasons": reasons,
                "target_tab": "teg",
                "target_search": target_search,
                "sidebar_group": "teg",
            }
            label = product_code or vehicle or "제품 미상"
            title = f"[TEG_CHECK] {label} · {filename} Mapfile 이상"

            for recipient in recipients:
                key = _delivery_key(filename, signature, recipient)
                if key in deliveries:
                    duplicates += 1
                    continue
                try:
                    sent = bool(
                        emitter(
                            EVENT_TYPE,
                            actor="system",
                            target_user=recipient,
                            title=title,
                            body=body,
                            payload=payload,
                            allow_self=True,
                            notification_id=_notification_id(filename, signature, recipient),
                        )
                    )
                except Exception as exc:
                    failed += 1
                    failures.append(f"{filename}:{recipient}: {exc}")
                    logger.warning("TEG mapfile alert publish failed for %s/%s: %s", filename, recipient, exc)
                    continue

                outcome = "published" if sent else "suppressed"
                deliveries[key] = {
                    "filename": filename,
                    "signature": signature,
                    "recipient": recipient,
                    "notification_id": _notification_id(filename, signature, recipient),
                    "outcome": outcome,
                    "recorded_at": timestamp,
                }
                if sent:
                    published += 1
                else:
                    suppressed += 1
                # Persist after each recipient to keep the duplicate window as
                # small as possible if the scheduler process is interrupted.
                save_json(state_path, state, indent=2)

    warning = ""
    retry_required = failed > 0
    if failures:
        warning = f"TEG_CHECK 알림 {failed}건 전송 실패. 다음 검사에서 재시도합니다."
    return {
        "ok": failed == 0,
        "group": GROUP_NAME,
        "group_status": "ready",
        "retry_required": retry_required,
        "warning": warning,
        "abnormal_files": len(abnormal),
        "published": published,
        "duplicates": duplicates,
        "suppressed": suppressed,
        "failed": failed,
        "failures": failures,
    }
