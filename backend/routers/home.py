import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.auth import current_user
from core.paths import PATHS
from core import home_dismissed_alerts as _hda

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
                "cta": "ET 추적으로 이동",
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


@router.get("/alerts")
def home_alerts(request: Request, limit: int = 50):
    """홈 화면 인사말 하단용 알람 및 진행이상 목록 집계.

    SplitTable KNOB Plan/Actual 불일치, 관심랏 변동(스플릿/노트/랏관리) 알람,
    사용자의 중요 미확인 알림(warning/Lot 관련)을 카테고리별로 취합하여 제공합니다.
    """
    me = current_user(request)
    username = me.get("username", "")
    alerts = []
    seen_keys = set()
    live_mismatches = {}
    limit = max(1, min(500, int(limit)))

    # 1. SplitTable KNOB Plan/Actual anomalies
    try:
        from core import fab_matching_alerts
        data = fab_matching_alerts.list_plan_knob_anomalies(limit=100)
        for item in (data.get("items") or []):
            prod = str(item.get("product") or "").strip()
            prod_key = str(item.get("product_key") or "").strip()
            if not prod_key:
                prod_key = prod.replace("ML_TABLE_", "") if prod.startswith("ML_TABLE_") else prod
            feature = str(item.get("feature_name") or item.get("column") or "KNOB").strip()
            plan = str(item.get("plan") or "").strip()
            actual = str(item.get("actual_ppid") or "").strip()
            user = str(item.get("plan_user") or "").strip()
            occ = item.get("occurrences") or 0
            locs = item.get("locations") or []
            root_lots = []
            for loc in locs:
                rl = str(loc.get("root_lot_id") or "").strip()
                if rl and rl not in root_lots:
                    root_lots.append(rl)

            # Critical vs Warning 분류: 기존 split 간 오투입은 Critical, RO 및 미매칭(빈값)은 Warning
            sev, badge_text, _ = fab_matching_alerts.classify_plan_mismatch(
                plan, actual, item.get("current_categories"), item.get("ready"), item.get("reason")
            )
            tone_val = "danger" if sev == "critical" else "warn"

            for r_lot in (root_lots or [""]):
                dedup = (prod, r_lot, feature)
                if dedup in seen_keys:
                    continue
                seen_keys.add(dedup)
                aid = f"plan-{item.get('id')}-{r_lot}"
                if _hda.is_alert_dismissed(username, aid):
                    continue

                title = f"[{prod_key}] Lot {r_lot} · {feature} {badge_text}" if r_lot else f"[{prod_key}] {feature} {badge_text}"
                occ_str = f" ({occ}매)" if occ else ""
                user_str = f" · 작성: {user}" if user else ""
                detail = f"Plan: {plan} → Actual: {actual}{occ_str}{user_str}"
                target_search = "?" + urlencode({"product": prod, **({"root": r_lot} if r_lot else {})})
                alerts.append({
                    "id": aid,
                    "type": "splittable_plan_mismatch",
                    "category": "스플릿테이블",
                    "priority_group": sev,
                    "badge": badge_text,
                    "tone": tone_val,
                    "product": prod,
                    "product_key": prod_key,
                    "root_lot_id": r_lot,
                    "title": title,
                    "detail": detail,
                    "target_tab": "splittable",
                    "target_search": target_search,
                    "action_label": "스플릿테이블에서 확인",
                    "timestamp": item.get("plan_updated") or "",
                    "source_notification_ids": [],
                })
                live_mismatches[(prod_key.upper(), r_lot.upper(), str(item.get("column") or feature), plan, actual)] = alerts[-1]
    except Exception:
        pass

    # 2. User notifications (관심랏 변동 알람 Info + Mismatch / Tracker 알림)
    try:
        from core import notify
        from core import fab_matching_alerts
        user_notifs = notify.get_notifications(username, unread_only=True)
        for n in reversed(user_notifs):
            ntype = str(n.get("type") or "info").lower()
            event = str(n.get("event") or "")
            payload = n.get("payload") or {}

            is_watched_lot = event.startswith("watched_lot_") or payload.get("category") == "관심랏"
            prod = str(payload.get("product") or "").strip()
            root_lot = str(payload.get("root_lot_id") or payload.get("lot_id") or "").strip()
            if event == "my_plan_actual_mismatch":
                key = (prod.removeprefix("ML_TABLE_").upper(), root_lot.upper(),
                       str(payload.get("column") or ""), str(payload.get("plan") or ""), str(payload.get("actual") or ""))
                existing = live_mismatches.get(key)
                if existing is not None:
                    existing["source_notification_ids"].append(str(n.get("id") or ""))
                    continue
            dedup = (prod, root_lot, n.get("id"), n.get("title"))
            if dedup in seen_keys:
                continue
            seen_keys.add(dedup)

            if is_watched_lot:
                category = "관심랏"
                priority_group = "info"
                # Info 계층: Plan 추가, Plan 변경, 코멘트 변경, 용도 변경, 노트 등록, 태그 갱신
                if event == "watched_lot_split_changed":
                    badge = payload.get("badge") or "Plan 변경"
                elif event == "watched_lot_note_registered":
                    badge = payload.get("badge") or ("태그 갱신" if "태그" in (n.get("title") or "") else "노트 등록")
                elif event == "watched_lot_management_updated":
                    badge = payload.get("badge") or "랏관리 갱신"
                else:
                    badge = "관심랏"

                tone = "info"
                target_tab = payload.get("target_tab") or ("splittable" if (prod or root_lot) else "home")
                if target_tab == "lot_management":
                    target_search = f"?product={prod}" if prod else ""
                    action_label = "랏관리에서 확인"
                elif target_tab == "splittable":
                    if prod and root_lot:
                        target_search = f"?product={prod}&root={root_lot}"
                    elif root_lot:
                        target_search = f"?root={root_lot}"
                    elif prod:
                        target_search = f"?product={prod}"
                    else:
                        target_search = ""
                    action_label = "스플릿테이블에서 확인"
                else:
                    target_search = ""
                    action_label = "확인하기"
            elif event in ("tracker_step_reached", "lot_step_threshold_reached"):
                category = "랏관리" if event == "lot_step_threshold_reached" else "알림/이상"
                priority_group = "notice"
                badge = "기준 Step 도달" if event == "lot_step_threshold_reached" else "위치 도달"
                tone = "neutral"
                target_tab = "lot_management" if event == "lot_step_threshold_reached" else ("tracker" if payload.get("issue_id") else "home")
                target_search = f"?product={prod}&lot={root_lot}" if prod and root_lot else ""
                action_label = "랏관리에서 확인" if target_tab == "lot_management" else "확인하기"
            elif event == "my_plan_actual_mismatch":
                category = "스플릿테이블"
                plan_val = payload.get("plan") or ""
                actual_val = payload.get("actual") or ""
                sev, badge_text, _ = fab_matching_alerts.classify_plan_mismatch(plan_val, actual_val)
                priority_group = sev
                badge = badge_text
                tone = "danger" if sev == "critical" else "warn"
                target_tab = "splittable"
                target_search = f"?product={prod}&root={root_lot}" if prod and root_lot else ""
                action_label = "스플릿테이블에서 확인"
            else:
                category = "알림/이상"
                priority_group = {"critical": "critical", "danger": "critical", "error": "critical",
                                  "warning": "warning", "warn": "warning", "approval": "warning",
                                  "admin_notice": "notice", "notice": "notice"}.get(ntype, "info")
                badge = {"critical": "긴급", "warning": "경고", "info": "알림", "notice": "공지"}[priority_group]
                tone = {"critical": "danger", "warning": "warn", "info": "info", "notice": "neutral"}[priority_group]
                target_tab = payload.get("target_tab") or ("splittable" if (prod or root_lot) else "admin")
                target_search = ""
                if target_tab == "splittable":
                    if prod and root_lot:
                        target_search = f"?product={prod}&root={root_lot}"
                    elif root_lot:
                        target_search = f"?root={root_lot}"
                    elif prod:
                        target_search = f"?product={prod}"
                action_label = "스플릿테이블에서 확인" if target_tab == "splittable" and root_lot else "확인하기"

            alerts.append({
                "id": f"notif-{n.get('id')}",
                "type": "notification",
                "event": event,
                "category": category,
                "priority_group": priority_group,
                "badge": badge,
                "tone": tone,
                "product": prod,
                "product_key": prod.replace("ML_TABLE_", "") if prod.startswith("ML_TABLE_") else prod,
                "root_lot_id": root_lot,
                "title": n.get("title") or "알림",
                "detail": n.get("body") or "",
                "target_tab": target_tab,
                "target_search": target_search,
                "action_label": action_label,
                "timestamp": n.get("timestamp") or "",
            })
    except Exception:
        pass

    # 3. 랏관리 특정 step_id 초과/도달 알람 검출 (Notice 계층)
    try:
        from routers import lot_management as lm
        from core import watchlist as _wl
        table_dir = lm.TABLE_DIR
        if table_dir.is_dir():
            import json
            for p_path in table_dir.glob("*.json"):
                try:
                    doc = json.loads(p_path.read_text(encoding="utf-8"))
                    if not isinstance(doc, dict):
                        continue
                    prod_name = str(doc.get("product") or p_path.stem).strip()
                    clean_prod = prod_name.replace("ML_TABLE_", "") if prod_name.startswith("ML_TABLE_") else prod_name
                    product_group_members = {
                        str(member).strip().casefold()
                        for member in lm._product_group_members(prod_name)
                        if str(member).strip()
                    }
                    # 캐시로부터 실시간 현재 step 오버레이
                    hydrated = lm._with_latest_cache_fields(doc)
                    for row in (hydrated.get("rows") or []):
                        vals = row.get("values") if isinstance(row.get("values"), dict) else {}
                        lid = str(vals.get("lot_id") or "").strip().upper()
                        alert_step = str(vals.get("alert_step_id") or "").strip()
                        curr_step = str(vals.get("current_step_id") or "").strip()
                        step_desc = str(vals.get("step_desc") or "").strip()
                        if not (lid and alert_step and curr_step):
                            continue
                        if (str(username or "").strip().casefold() not in product_group_members
                                and not _wl.is_lot_watched(username, lid)):
                            continue
                        if lm._is_step_reached_or_exceeded(curr_step, alert_step):
                            dedup = ("lot_step_threshold", prod_name, lid, alert_step)
                            if dedup in seen_keys:
                                continue
                            seen_keys.add(dedup)
                            aid = f"lotstep-{prod_name}-{lid}-{alert_step}"
                            if _hda.is_alert_dismissed(username, aid):
                                continue

                            alerts.append({
                                "id": aid,
                                "type": "lot_step_threshold",
                                "category": "랏관리",
                                "priority_group": "notice",
                                "badge": "기준 Step 도달",
                                "tone": "neutral",
                                "product": prod_name,
                                "product_key": clean_prod,
                                "root_lot_id": lid,
                                "title": f"[{clean_prod}] Lot {lid} · 기준 Step 도달/초과 ({curr_step} >= {alert_step})",
                                "detail": f"현재 공정: {curr_step} ({step_desc}) · 기준 공정: {alert_step}",
                                "target_tab": "lot_management",
                                "target_search": f"?product={prod_name}",
                                "action_label": "랏관리에서 확인",
                                "timestamp": doc.get("updated_at") or "",
                            })
                except Exception:
                    continue
    except Exception:
        pass

    # 위험도 우선(critical -> warning -> info -> notice) 후 최신 timestamp 순 정렬
    priority_order = {"critical": 0, "warning": 1, "info": 2, "notice": 3}
    # Stable second sort retains newest-first ordering inside each importance.
    alerts.sort(key=lambda a: a.get("timestamp") or "", reverse=True)
    alerts.sort(key=lambda a: priority_order.get(a.get("priority_group", "info"), 4))

    return {
        "ok": True,
        "total": len(alerts),
        "counts": {group: sum(a["priority_group"] == group for a in alerts) for group in priority_order},
        "truncated": len(alerts) > limit,
        "alerts": alerts[:limit],
    }


class HomeAlertMarkReadReq(BaseModel):
    ids: list[str] = Field(default_factory=list)
    mark_all: bool = False


@router.post("/alerts/mark-read")
def mark_home_alerts_read(req: HomeAlertMarkReadReq, request: Request):
    """홈 화면 알람 읽음(확인) 처리.

    - notif-* 알림: notify.mark_read_by_ids 로 unread 해제하여 우상단 종 뱃지 즉시 차감
    - plan-* 알람: home_dismissed_alerts 에 기록하여 홈 화면 목록에서 영구 제외
    - mark_all=True 시: 현재 사용자의 모든 알람 일괄 확인 처리
    """
    me = current_user(request)
    username = me.get("username", "")
    if not username:
        raise HTTPException(401, "login required")

    from core import notify

    selected = list(dict.fromkeys(str(aid or "").strip() for aid in req.ids if str(aid or "").strip()))
    current = None
    if req.mark_all or any(aid.startswith("plan-") for aid in selected):
        current = home_alerts(request, limit=500)["alerts"]
    if req.mark_all:
        selected = [a["id"] for a in current]
    by_id = {a["id"]: a for a in current or []}
    notif_ids, derived_ids = [], []
    for aid in selected:
        if aid.startswith("notif-"):
            notif_ids.append(aid[len("notif-"):])
        elif aid.startswith(("plan-", "lotstep-")):
            derived_ids.append(aid)
            # Exact source IDs only. Parsing a hyphenated lot out of the ID
            # used to dismiss unrelated notes/alerts for the same lot.
            notif_ids.extend(by_id.get(aid, {}).get("source_notification_ids") or [])
        else:
            notif_ids.append(aid)  # Compatibility with raw notification IDs.
    if notif_ids:
        notify.mark_read_by_ids(username, list(dict.fromkeys(notif_ids)))
    if derived_ids:
        _hda.dismiss_alerts(username, derived_ids)
    return {"ok": True, "ids": selected, "marked_all": bool(req.mark_all)}
