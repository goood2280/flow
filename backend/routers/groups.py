"""routers/groups.py v8.8.3 — User groups for Dashboard/Tracker visibility + LOT watch + module 담당.

스키마 ({data_root}/groups/groups.json):
  [{id, name, description, owner, departments:[부서명], members:[username], watched_lots:[lot_id],
    modules:[module_name], created, updated}]

부서 기준 멤버십 (2026-09-29):
  - 그룹 소속은 사람이 아니라 부서(users.csv `department`, SSO 가 채움)로 정한다.
    `departments` 에 적힌 부서의 승인된 사용자가 곧 멤버다 — 입·퇴사·부서 이동이
    그룹 편집 없이 반영된다.
  - 저장 파일의 `members` 는 예전 방식의 개별 멤버(하위 호환)다. `_load()` 는 이것을
    `manual_members` 로 옮기고 `department_members` 와 합친 실제 소속을 `members` 로
    돌려준다. 다른 라우터(인폼·회의·알람·가시성)는 계속 `members` 만 읽으면 된다.
  - `_save()` 는 파생 필드를 버리고 개별 멤버만 `members` 로 되쓴다.

v8.8.3 변경:
  - description(optional str) 필드 추가: 그룹 목적 자유 텍스트.
  - 기존 레코드는 description 없어도 옵셔널 처리.

v8.7.0 추가:
  - modules: 이 그룹이 담당하는 공정 모듈 (GATE/STI/PC/MOL/BEOL/ET/EDS/...).
  - user_modules(username, role): 해당 유저가 담당하는 모듈 set. 인폼 모듈별 필터용.

v8.8.1 정책 변경:
  - 일반 유저도 그룹 생성·편집 가능 (admin 전용 기능 아님).
  - 생성자가 반드시 members 에 포함될 필요 없음 (owner 만 기록).
  - admin 계정 및 "test" 가 포함된 username 은 members 대상에서 자동 제외
    (admin 은 사내 이메일이 없어 메일 발송 대상이 될 수 없고, test 계정은 가상).

규약:
  - admin 은 모든 그룹 조회/수정 가능.
  - 일반 유저는 자기가 owner 이거나 member 인 그룹만 조회·LOT watch 편집 가능.
  - 생성·삭제·멤버 편집은 owner 또는 admin.
  - 감사 로그: groups_audit.jsonl (actor, action, group_id, timestamp, detail).

가시성 필터 헬퍼 (다른 라우터가 import):
  filter_by_visibility(items, username, role, key="group_ids")
    - admin 은 모두 통과.
    - item 에 group_ids 가 비어있으면 public → 통과.
    - group_ids 가 있으면 유저가 최소 1개 그룹의 member 여야 통과.
"""
import datetime
import re
import threading
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Query
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json, jsonl_append
from core.auth import current_user, require_admin

router = APIRouter(prefix="/api/groups", tags=["groups"])


# v8.8.1/v8.8.5: admin 필터 해제 — admin 도 그룹 멤버로 추가 가능 (사내 이메일 있는 경우 多).
#   v8.8.1 에선 "admin 은 사내 이메일이 없어 메일 발송 대상 아님" 가정으로 제외했으나,
#   실사내 admin 계정은 정상 이메일 보유 → 배제하면 안 됨. test substring 만 block.
def _is_blocked_member(username: str, users_by_name: dict | None = None) -> bool:
    un = (username or "").strip()
    if not un:
        return True
    if "test" in un.lower():
        return True
    return False


def _load_users_by_name() -> dict:
    try:
        from routers.auth import read_users
        return {u.get("username", ""): u for u in read_users() if u.get("username")}
    except Exception:
        return {}


def _sanitize_members(raw, users_by_name: dict | None = None) -> list:
    if users_by_name is None:
        users_by_name = _load_users_by_name()
    out = []
    seen = set()
    for m in (raw or []):
        s = str(m).strip()
        if not s or s in seen:
            continue
        if _is_blocked_member(s, users_by_name):
            continue
        seen.add(s)
        out.append(s)
    return out


def _department_key(value) -> str:
    """부서명 비교 키 — 대소문자·연속 공백 무시 (admin 권한 그룹과 같은 규칙)."""
    return " ".join(str(value or "").strip().split()).casefold()


def _clean_departments(values) -> list:
    if isinstance(values, str):
        values = values.replace("\r", "\n").replace(",", "\n").split("\n")
    out: list = []
    seen: set = set()
    for value in values or []:
        label = " ".join(str(value or "").strip().split())
        key = _department_key(label)
        if label and key not in seen:
            seen.add(key)
            out.append(label[:200])
    return out


_INACTIVE_USER_STATUSES = {"pending", "rejected", "disabled", "deleted", "inactive"}
_DEPT_INDEX_LOCK = threading.Lock()
_DEPT_INDEX: dict = {"sig": None, "members": {}, "labels": {}, "names": {}}


def _department_index() -> tuple:
    """(부서키 → username 목록, 부서키 → 표시명, username → 이름).

    users.csv 가 바뀔 때만 다시 만든다 — 가시성 필터가 요청마다 `_load()` 를 부르므로
    매번 전체 사용자를 훑지 않게 한다. 승인 대기·거절 계정과 test 계정은 뺀다."""
    try:
        from routers import auth as _auth_router
        sig = _auth_router._users_csv_sig()
    except Exception:
        sig = None
    with _DEPT_INDEX_LOCK:
        if sig is not None and _DEPT_INDEX["sig"] == sig:
            return _DEPT_INDEX["members"], _DEPT_INDEX["labels"], _DEPT_INDEX["names"]
    members: dict = {}
    labels: dict = {}
    names: dict = {}
    for username, user in _load_users_by_name().items():
        if not isinstance(user, dict) or _is_blocked_member(username):
            continue
        if str(user.get("status") or "").strip().lower() in _INACTIVE_USER_STATUSES:
            continue
        names[username] = str(user.get("name") or "").strip()
        label = " ".join(str(user.get("department") or "").strip().split())
        key = _department_key(label)
        if not key:
            continue
        labels.setdefault(key, label)
        members.setdefault(key, []).append(username)
    for key in members:
        members[key].sort(key=str.casefold)
    with _DEPT_INDEX_LOCK:
        _DEPT_INDEX.update({"sig": sig, "members": members, "labels": labels, "names": names})
    return members, labels, names


def _resolve_members(g: dict, dept_members: dict | None = None) -> dict:
    """저장된 개별 멤버 + 부서 소속 사용자 → 실제 멤버(`members`)."""
    if dept_members is None:
        dept_members = _department_index()[0]
    manual = [str(m).strip() for m in (g.get("manual_members", g.get("members")) or []) if str(m).strip()]
    departments = _clean_departments(g.get("departments") or [])
    from_depts: list = []
    seen: set = set()
    for dept in departments:
        for username in dept_members.get(_department_key(dept), []):
            if username not in seen:
                seen.add(username)
                from_depts.append(username)
    g["departments"] = departments
    g["manual_members"] = sorted(set(manual), key=str.casefold)
    g["department_members"] = sorted(from_depts, key=str.casefold)
    g["members"] = sorted(set(manual) | seen, key=str.casefold)
    return g


def _manual_members(g: dict) -> list:
    """개별 멤버 — `_load()` 가 풀어 둔 값, 없으면(해석 전 레코드) 저장된 members."""
    return list(g.get("manual_members", g.get("members")) or [])


def _group_name_key(value: str) -> str:
    """Group names are compared without case so product-name groups are stable."""
    return str(value or "").strip().casefold()


def _bulk_member_ids(raw: str) -> list[str]:
    """Extract login IDs from semicolon/comma/whitespace-separated IDs or emails."""
    out: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[;,\s]+", str(raw or "")):
        clean = token.strip().strip("<>")
        if not clean:
            continue
        username = clean.split("@", 1)[0].strip()
        key = username.casefold()
        if username and key not in seen:
            seen.add(key)
            out.append(username)
    return out

GROUPS_DIR = PATHS.data_root / "groups"
GROUPS_DIR.mkdir(parents=True, exist_ok=True)
GROUPS_FILE = GROUPS_DIR / "groups.json"
AUDIT_FILE = GROUPS_DIR / "groups_audit.jsonl"


def _clean_emails_for_group(raw) -> list:
    """v8.8.23: 메일 그룹 통합 — email 정규화 헬퍼 (mail_groups.py 에서 가져옴)."""
    if not isinstance(raw, (list, tuple, set)):
        return []
    out = []
    seen = set()
    for e in raw:
        s = str(e).strip()
        if s and "@" in s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


_MIGRATION_DONE = False


def _migrate_legacy_mail_groups(groups: list) -> list:
    """v8.8.23: 레거시 `mail_groups.json` + admin_settings.json:recipient_groups 를
    groups.json 으로 일회성 병합. 이름 기준 match — 기존 그룹이 있으면 extra_emails 만
    보강, 없으면 새 그룹으로 추가 (owner="system").
    마이그레이션 완료 파일은 `.migrated` suffix 로 이름 변경해 두번 돌지 않게.
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return groups
    _MIGRATION_DONE = True
    changed = False
    # 1) mail_groups.json
    legacy_fp = PATHS.data_root / "mail_groups.json"
    if legacy_fp.exists():
        try:
            legacy = load_json(legacy_fp, [])
            if isinstance(legacy, list):
                by_name = {(g.get("name") or "").strip().lower(): g for g in groups}
                for mg in legacy:
                    if not isinstance(mg, dict):
                        continue
                    nm = (mg.get("name") or "").strip()
                    if not nm:
                        continue
                    key = nm.lower()
                    mg_members = _sanitize_members(mg.get("members") or [])
                    mg_extras = _clean_emails_for_group(mg.get("extra_emails") or [])
                    mg_note = (mg.get("note") or "").strip() or None
                    if key in by_name:
                        tgt = by_name[key]
                        # extra_emails 병합 (dedupe)
                        cur_ext = _clean_emails_for_group(tgt.get("extra_emails") or [])
                        for e in mg_extras:
                            if e not in cur_ext:
                                cur_ext.append(e)
                        if cur_ext != (tgt.get("extra_emails") or []):
                            tgt["extra_emails"] = cur_ext
                            changed = True
                        # members 병합
                        cur_m = set(tgt.get("members") or [])
                        for m in mg_members:
                            if m not in cur_m:
                                cur_m.add(m)
                                changed = True
                        tgt["members"] = sorted(cur_m)
                    else:
                        gid = mg.get("id") or f"grp_mig_{uuid.uuid4().hex[:8]}"
                        groups.append({
                            "id": gid,
                            "name": nm,
                            "description": mg_note,
                            "owner": mg.get("created_by") or "system",
                            "members": sorted(set(mg_members)),
                            "watched_lots": [],
                            "modules": [],
                            "extra_emails": mg_extras,
                            "created": mg.get("created") or datetime.datetime.now().isoformat(timespec="seconds"),
                            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                        })
                        by_name[key] = groups[-1]
                        changed = True
            # 이름 변경 — 두번 안 돌게.
            try:
                legacy_fp.rename(legacy_fp.with_suffix(".json.migrated"))
            except Exception:
                pass
        except Exception:
            pass
    # 2) admin_settings.json:recipient_groups (dict: name → [usernames])
    try:
        adm_fp = PATHS.data_root / "admin_settings.json"
        if adm_fp.exists():
            adm = load_json(adm_fp, {})
            rg = (adm.get("mail") or {}).get("recipient_groups") or adm.get("recipient_groups") or {}
            if isinstance(rg, dict) and rg:
                by_name = {(g.get("name") or "").strip().lower(): g for g in groups}
                for nm, ulist in rg.items():
                    nm = (nm or "").strip()
                    if not nm:
                        continue
                    key = nm.lower()
                    members = _sanitize_members([u for u in (ulist or []) if isinstance(u, str)])
                    if key in by_name:
                        tgt = by_name[key]
                        cur_m = set(tgt.get("members") or [])
                        for m in members:
                            if m not in cur_m:
                                cur_m.add(m); changed = True
                        tgt["members"] = sorted(cur_m)
                    else:
                        gid = f"grp_mig_{uuid.uuid4().hex[:8]}"
                        groups.append({
                            "id": gid,
                            "name": nm,
                            "description": None,
                            "owner": "system",
                            "members": sorted(set(members)),
                            "watched_lots": [],
                            "modules": [],
                            "extra_emails": [],
                            "created": datetime.datetime.now().isoformat(timespec="seconds"),
                            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
                        })
                        by_name[key] = groups[-1]
                        changed = True
    except Exception:
        pass
    return groups if changed else groups  # caller will save if needed


def _load() -> list:
    data = load_json(GROUPS_FILE, [])
    if not isinstance(data, list):
        return []
    # v8.8.23: 정규화 — extra_emails 필드 보장.
    for g in data:
        if isinstance(g, dict):
            g.setdefault("extra_emails", [])
    # 최초 로드 시 레거시 병합 시도.
    global _MIGRATION_DONE
    if not _MIGRATION_DONE:
        before = [dict(x) for x in data]
        migrated = _migrate_legacy_mail_groups(data)
        # 변경 여부 단순 검사 — 리스트 길이 or 대표 필드 비교.
        if len(migrated) != len(before) or any(
            (a.get("extra_emails") or []) != (b.get("extra_emails") or []) or
            (a.get("members") or []) != (b.get("members") or [])
            for a, b in zip(migrated, before)
        ):
            try:
                save_json(GROUPS_FILE, migrated, indent=2)
            except Exception:
                pass
        data = migrated
    dept_members = _department_index()[0]
    for g in data:
        if isinstance(g, dict):
            _resolve_members(g, dept_members)
    return data


_DERIVED_KEYS = ("manual_members", "department_members")


def _save(groups: list) -> None:
    """파생 필드를 버리고 개별 멤버만 `members` 로 저장한다.

    `mail_groups.py` 처럼 `members` 를 직접 고치는 예전 호출부도 있다. 그때는
    `members` 가 (개별 ∪ 부서) 와 달라지므로, 부서 소속을 뺀 나머지를 개별 멤버로 본다."""
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        row = {k: v for k, v in g.items() if k not in _DERIVED_KEYS}
        if "manual_members" in g:
            members = [str(m).strip() for m in (g.get("members") or []) if str(m).strip()]
            manual = [str(m).strip() for m in (_manual_members(g)) if str(m).strip()]
            dept = {str(m).strip() for m in (g.get("department_members") or [])}
            if set(members) != set(manual) | dept:
                manual = [m for m in members if m not in dept]
            row["members"] = sorted(set(manual), key=str.casefold)
        row["departments"] = _clean_departments(g.get("departments") or [])
        out.append(row)
    save_json(GROUPS_FILE, out, indent=2)


def _audit(actor: str, action: str, group_id: str, detail: str = "") -> None:
    jsonl_append(
        AUDIT_FILE,
        {
            "actor": actor,
            "action": action,
            "group_id": group_id,
            "detail": detail,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        },
    )


def _find(groups: list, gid: str) -> Optional[dict]:
    return next((g for g in groups if g.get("id") == gid), None)


def _can_view(g: dict, username: str, role: str) -> bool:
    if role == "admin":
        return True
    if g.get("owner") == username:
        return True
    return username in (g.get("members") or [])


def _can_edit(g: dict, username: str, role: str) -> bool:
    if role == "admin":
        return True
    return g.get("owner") == username


# ── Visibility filter (다른 라우터가 import) ─────────────────────────
def user_group_ids(username: str, role: str) -> set:
    """해당 유저가 속한 group id set. admin 은 *모두 포함* 간주 → 전역 통과."""
    if role == "admin":
        return {"__admin__"}
    groups = _load()
    return {
        g.get("id", "")
        for g in groups
        if g.get("owner") == username or username in (g.get("members") or [])
    }


def user_modules(username: str, role: str) -> set:
    """해당 유저가 담당하는 공정 모듈 set. admin 은 sentinel '__all__' 반환 (전체 담당)."""
    if role == "admin":
        return {"__all__"}
    groups = _load()
    mods: set = set()
    for g in groups:
        if g.get("owner") == username or username in (g.get("members") or []):
            for m in (g.get("modules") or []):
                if m:
                    mods.add(m)
    return mods


def filter_by_visibility(items: list, username: str, role: str, key: str = "group_ids") -> list:
    """item.group_ids 가 비어있으면 public (통과). 값이 있으면 유저 그룹과 교집합 필요.
    admin 은 항상 전부 통과."""
    if role == "admin":
        return items
    my = user_group_ids(username, role)
    out = []
    for it in items:
        gids = it.get(key) or []
        if not gids:
            out.append(it)
            continue
        if any(g in my for g in gids):
            out.append(it)
    return out


# ── Pydantic ────────────────────────────────────────────────────────
class GroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    departments: List[str] = []
    members: List[str] = []
    watched_lots: List[str] = []
    modules: List[str] = []
    # v8.8.23: 메일 그룹 통합 — 외부 고정 수신자(email) 리스트를 그룹 레코드에 보관.
    extra_emails: List[str] = []


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    departments: Optional[List[str]] = None
    members: Optional[List[str]] = None
    watched_lots: Optional[List[str]] = None
    modules: Optional[List[str]] = None
    extra_emails: Optional[List[str]] = None  # v8.8.23


class ModulesReq(BaseModel):
    modules: List[str]


class MemberReq(BaseModel):
    username: str


class MembersBulkReq(BaseModel):
    entries: str


class LotReq(BaseModel):
    lot_id: str


# ── Endpoints ───────────────────────────────────────────────────────
@router.get("/list")
def list_groups(request: Request):
    """내가 속한(또는 owner) 그룹 목록. admin 은 전체."""
    me = current_user(request)
    groups = _load()
    if me.get("role") == "admin":
        return {"groups": groups}
    vis = [g for g in groups if _can_view(g, me["username"], me.get("role", "user"))]
    return {"groups": vis}


@router.get("/mine")
def my_group_ids(request: Request):
    """내가 속한 그룹 id 배열. Dashboard/Tracker visibility UI 용."""
    me = current_user(request)
    if me.get("role") == "admin":
        # admin 은 모든 그룹을 선택 가능
        return {"group_ids": [g.get("id") for g in _load()], "admin": True}
    groups = _load()
    mine = [g for g in groups if _can_view(g, me["username"], me.get("role", "user"))]
    return {"group_ids": [g.get("id") for g in mine], "admin": False}


@router.post("/create")
def create_group(req: GroupCreate, request: Request):
    me = current_user(request)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    groups = _load()
    if any(_group_name_key(g.get("name")) == _group_name_key(name) for g in groups):
        raise HTTPException(409, "group name already exists")
    gid = f"grp_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    # v8.8.1: 생성자 자동 포함 X — 요청된 멤버만 수용. admin/test 필터.
    members = sorted(set(_sanitize_members(req.members or [])))
    g = {
        "id": gid,
        "name": name,
        "description": (req.description or "").strip() or None,
        "owner": me["username"],
        "departments": _clean_departments(req.departments or []),
        "members": members,
        "watched_lots": sorted(set(req.watched_lots or [])),
        "modules": sorted(set(req.modules or [])),
        # v8.8.23: 메일 통합 — extra_emails (외부 고정 수신자).
        "extra_emails": _clean_emails_for_group(req.extra_emails or []),
        "created": now,
        "updated": now,
    }
    _resolve_members(g)
    groups.append(g)
    _save(groups)
    _audit(me["username"], "create", gid, f"{name} departments={','.join(g['departments'])}")
    return {"ok": True, "group": g}


@router.post("/update")
def update_group(req: GroupUpdate, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403, "Only owner or admin can edit")
    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "name empty")
        if any(_group_name_key(x.get("name")) == _group_name_key(name) and x.get("id") != id for x in groups):
            raise HTTPException(409, "group name already exists")
        g["name"] = name
    if req.description is not None:
        g["description"] = req.description.strip() or None
    if req.departments is not None:
        g["departments"] = _clean_departments(req.departments)
    if req.members is not None:
        # v8.8.1: owner 자동 포함 X. admin/test 필터. 부서 기준 전환 뒤에는 개별 멤버만 뜻한다.
        g["manual_members"] = sorted(set(_sanitize_members(req.members)))
    _resolve_members(g)
    if req.watched_lots is not None:
        g["watched_lots"] = sorted(set(req.watched_lots))
    if req.modules is not None:
        g["modules"] = sorted({m.strip() for m in req.modules if m and m.strip()})
    # v8.8.23: extra_emails 편집 지원.
    if req.extra_emails is not None:
        g["extra_emails"] = _clean_emails_for_group(req.extra_emails)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "update", id, g.get("name", ""))
    return {"ok": True, "group": g}


@router.post("/modules/set")
def set_modules(req: ModulesReq, request: Request, id: str = Query(...)):
    """그룹 담당 모듈 일괄 설정 (owner/admin)."""
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    g["modules"] = sorted({m.strip() for m in (req.modules or []) if m and m.strip()})
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "modules_set", id, ",".join(g["modules"]))
    return {"ok": True, "modules": g["modules"]}


@router.get("/my-modules")
def get_my_modules(request: Request):
    """현재 유저가 담당하는 모듈 list. admin 은 '__all__' sentinel."""
    me = current_user(request)
    mods = user_modules(me["username"], me.get("role", "user"))
    all_rounder = "__all__" in mods or me.get("role") == "admin"
    return {
        "modules": [] if all_rounder else sorted(mods),
        "all_rounder": all_rounder,
    }


@router.post("/delete")
def delete_group(request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403, "Only owner or admin can delete")
    groups = [x for x in groups if x.get("id") != id]
    _save(groups)
    _audit(me["username"], "delete", id, g.get("name", ""))
    return {"ok": True}


@router.post("/members/add")
def add_member(req: MemberReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    users_by_name = _load_users_by_name()
    if _is_blocked_member(req.username, users_by_name):
        raise HTTPException(400, "admin/test 계정은 멤버로 추가할 수 없습니다.")
    members = set(_manual_members(g))
    members.add(req.username)
    g["manual_members"] = sorted(_sanitize_members(members, users_by_name))
    _resolve_members(g)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "member_add", id, req.username)
    return {"ok": True, "members": g["members"]}


@router.post("/members/add-bulk")
def add_members_bulk(req: MembersBulkReq, request: Request, id: str = Query(...)):
    """Add registered users from `id@domain;id2@domain` style input in one write."""
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    if len(str(req.entries or "")) > 100_000:
        raise HTTPException(400, "bulk member input too long")

    users_by_name = _load_users_by_name()
    canonical_users = {
        str(username).strip().casefold(): str(username).strip()
        for username in users_by_name
        if str(username).strip()
    }
    existing = [str(member).strip() for member in (_manual_members(g)) if str(member).strip()]
    existing_keys = {member.casefold() for member in existing}
    added: list[str] = []
    already_members: list[str] = []
    not_found: list[str] = []
    rejected: list[str] = []

    for requested in _bulk_member_ids(req.entries)[:2000]:
        actual = canonical_users.get(requested.casefold())
        if not actual:
            not_found.append(requested)
            continue
        if _is_blocked_member(actual, users_by_name):
            rejected.append(actual)
            continue
        key = actual.casefold()
        if key in existing_keys:
            already_members.append(actual)
            continue
        existing_keys.add(key)
        existing.append(actual)
        added.append(actual)

    if added:
        g["manual_members"] = sorted(existing, key=str.casefold)
        _resolve_members(g)
        g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        _save(groups)
        _audit(me["username"], "member_add_bulk", id, ",".join(added))

    return {
        "ok": True,
        "members": g.get("members") or [],
        "added": added,
        "already_members": already_members,
        "not_found": not_found,
        "rejected": rejected,
    }


@router.post("/members/remove")
def remove_member(req: MemberReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_edit(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    # v8.8.1: owner 자동 포함 정책 제거. 개별 멤버만 뺄 수 있다 — 부서 소속은 부서에서 뺀다.
    g["manual_members"] = sorted([m for m in (_manual_members(g)) if m != req.username])
    _resolve_members(g)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "member_remove", id, req.username)
    return {"ok": True, "members": g["members"]}


@router.post("/lots/add")
def add_lot(req: LotReq, request: Request, id: str = Query(...)):
    """그룹의 관심 LOT_WF 목록에 추가 (member 도 가능 — 공유 와치리스트)."""
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_view(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    lots = set(g.get("watched_lots") or [])
    lots.add(req.lot_id)
    g["watched_lots"] = sorted(lots)
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "lot_add", id, req.lot_id)
    return {"ok": True, "watched_lots": g["watched_lots"]}


@router.post("/lots/remove")
def remove_lot(req: LotReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    groups = _load()
    g = _find(groups, id)
    if not g:
        raise HTTPException(404)
    if not _can_view(g, me["username"], me.get("role", "user")):
        raise HTTPException(403)
    g["watched_lots"] = sorted([x for x in (g.get("watched_lots") or []) if x != req.lot_id])
    g["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save(groups)
    _audit(me["username"], "lot_remove", id, req.lot_id)
    return {"ok": True, "watched_lots": g["watched_lots"]}


@router.get("/eligible-users")
def eligible_users(request: Request):
    """v8.8.1: 그룹 멤버로 추가 가능한 username 목록.
    admin 계정과 "test" 가 포함된 계정은 제외 (메일 발송 대상 아님).
    로그인 유저 누구나 조회 가능 (GroupsPanel FE 용)."""
    _me = current_user(request)
    users_by_name = _load_users_by_name()
    out = []
    for un, u in users_by_name.items():
        if _is_blocked_member(un, users_by_name):
            continue
        out.append({
            "username": un,
            "email": u.get("email", "") if isinstance(u, dict) else "",
            "role": u.get("role", "user") if isinstance(u, dict) else "user",
            # v8.8.27: 이름(실명) 라벨. FE 가 `{name} ({username})` 로 표시.
            "name": (u.get("name", "") if isinstance(u, dict) else "").strip(),
            "department": (u.get("department", "") if isinstance(u, dict) else "").strip(),
        })
    out.sort(key=lambda x: ((x.get("name") or "").lower(), x["username"].lower()))
    return {"users": out}


@router.get("/departments")
def list_departments(request: Request):
    """그룹에 넣을 수 있는 부서 목록 — users.csv 의 부서별 승인 사용자 수.

    부서명은 SSO 로그인 때 채워진다. 아직 아무도 로그인하지 않은 부서도 그룹에
    적어 둘 수 있다(그 부서 사람이 처음 로그인하는 순간 멤버가 된다)."""
    current_user(request)
    members, labels, names = _department_index()
    rows = [
        {
            "department": labels.get(key, key),
            "count": len(users),
            "users": [{"username": u, "name": names.get(u, "")} for u in users[:200]],
        }
        for key, users in members.items()
    ]
    rows.sort(key=lambda r: r["department"].casefold())
    return {"departments": rows}


@router.get("/audit")
def audit_log(limit: int = 200, _admin=Depends(require_admin)):
    from core.utils import jsonl_read
    return {"entries": jsonl_read(AUDIT_FILE, limit)}
