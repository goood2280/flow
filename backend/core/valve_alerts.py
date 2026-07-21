"""core/valve_alerts.py — Valve 파이프라인 알람 소비 + 엔지니어 판정 반영.

순환 구조 (Valve backend/core/alert_store.py 와 짝):
  1. Valve 파이프라인 실행 → 알람 발행 → S3 `valve-alerts/pipeline/{vehicle}.json`
     · unmatched_step : vehicle_matching 에 없는 step (um|{vehicle}|{step_id})
     · ro_ppid        : knob 매핑에 없어 raw ppid 로 남은 건 (ro|{vehicle}|{step_id}|{ppid})
  2. Flow(여기) 가 알람을 읽어 엔지니어 판정 화면에 표시:
     · ro_ppid → 카테고리 기입 → ppid_knob.csv 에 해당 feature 의 다음 Rule 넘버(R{n+1})로 추가
     · unmatched_step → vehicle/step_id/step_desc 확인 → Vehicle_matching.csv 에 행 추가
     반영 시: 원자적 저장 + 파일 버전 스냅샷(file_versions) + matching cache 갱신
     + S3 업로드(flow/artifacts/matching/…) + 판정 이력(jsonl) 기록 + ack 상태 갱신
  3. Valve csv_sync 가 내려받아 재실행 → 알람 자연 소멸.
     반영 불필요/보류 건은 `valve-alerts/pipeline/ack.json` 으로 억제
     (Valve SUPPRESS_STATUSES = 미확인예정/반영불필요. "반영완료" 는 정보 표시용 —
      재실행으로 해소될 때까지 알람은 active 유지가 정상).

전송 계층: 실환경은 boto3 S3, 로컬 데모는 `local_root`(Valve fake_local 버킷 폴더)
직접 읽기/쓰기. 설정: data/flow-data/valve_alerts.json
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json

logger = logging.getLogger("flow.valve_alerts")

CFG_PATH = PATHS.data_root / "valve_alerts.json"
STATE_PATH = PATHS.log_dir / "valve_alerts_state.json"
DECISIONS_PATH = PATHS.data_root / "valve_alert_decisions.jsonl"

DEFAULT_CFG = {
    "enabled": True,
    "poll_seconds": 300,
    # 알람/ack 이 놓이는 prefix (Valve settings.alerts.s3_prefix 와 동일하게)
    "alerts_prefix": "valve-alerts",
    # 판정 반영된 matching csv 를 올릴 prefix (Valve csv_sync.yaml s3_prefix 와 동일하게)
    "artifacts_prefix": "flow/artifacts",
    # 로컬 데모: Valve fake_local 버킷 루트 (예: D:/semi all/Valve/s3_local/flow-datalake)
    # 비어있으면 S3 모드
    "local_root": "",
    # 알람 파일 전용 S3 설정 — bucket 이 비어있으면 s3_sync.json 의 bucket 설정을 재사용.
    # 자격증명은 s3_sync 와 동일하게 data/flow-data/s3_ingest/aws/ profile 사용.
    "s3": {"bucket": "", "region": "", "profile": "", "endpoint_url": ""},
}

# 판정 반영 대상 파일 (db_root 기준)
PPID_KNOB_FILE = "ppid_knob.csv"
VEHICLE_MATCHING_FILE = "Vehicle_matching.csv"

_thread: threading.Thread | None = None
_started = False
_lock = threading.Lock()
_write_lock = threading.Lock()


# ────────────────────────────────────────── 설정/상태
def load_cfg() -> dict:
    cfg = load_json(CFG_PATH, DEFAULT_CFG)
    if not isinstance(cfg, dict):
        cfg = {}
    out = dict(DEFAULT_CFG)
    out.update({k: v for k, v in cfg.items() if k in DEFAULT_CFG and k != "s3"})
    s3 = dict(DEFAULT_CFG["s3"])
    if isinstance(cfg.get("s3"), dict):
        s3.update({k: str(v or "") for k, v in cfg["s3"].items() if k in s3})
    out["s3"] = s3
    return out


def save_cfg(patch: dict) -> dict:
    """설정 부분 갱신 후 저장. 알 수 없는 키는 무시."""
    cur = load_cfg()
    patch = patch if isinstance(patch, dict) else {}
    if "enabled" in patch:
        cur["enabled"] = bool(patch["enabled"])
    if "poll_seconds" in patch:
        cur["poll_seconds"] = max(30, int(patch["poll_seconds"] or 300))
    for key in ("alerts_prefix", "artifacts_prefix", "local_root"):
        if key in patch:
            cur[key] = str(patch[key] or "").strip()
    cur["alerts_prefix"] = cur["alerts_prefix"].strip("/") or "valve-alerts"
    cur["artifacts_prefix"] = cur["artifacts_prefix"].strip("/") or "flow/artifacts"
    if isinstance(patch.get("s3"), dict):
        for k in cur["s3"]:
            if k in patch["s3"]:
                cur["s3"][k] = str(patch["s3"][k] or "").strip()
    CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(CFG_PATH, cur)
    return cur


def _save_cfg_if_missing() -> None:
    if not CFG_PATH.exists():
        CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_json(CFG_PATH, DEFAULT_CFG)


# ────────────────────────────────────────── 전송 계층 (local 폴더 ↔ S3)
class _Store:
    """알람 버킷 접근 추상화 — local_root 가 있으면 로컬 폴더, 아니면 boto3 S3.

    S3 접속 정보 우선순위: valve_alerts.json 의 s3.* → (bucket 비어있으면)
    s3_sync.json 의 bucket/region/profile/endpoint_url 재사용.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.local_root = str(cfg.get("local_root") or "").strip()

    def _s3_cfg(self) -> dict:
        own = self.cfg.get("s3") or {}
        if str(own.get("bucket") or "").strip():
            return {k: str(own.get(k) or "").strip()
                    for k in ("bucket", "region", "profile", "endpoint_url")}
        from core import s3_sync as _s3
        sync = _s3.load_config(PATHS.data_root)
        return {"bucket": str(sync.get("bucket") or "").strip(),
                "region": str(sync.get("region") or "").strip(),
                "profile": str(sync.get("profile") or "").strip(),
                "endpoint_url": str(sync.get("endpoint_url") or "").strip()}

    def available(self) -> tuple[bool, str]:
        if self.local_root:
            p = Path(self.local_root)
            return (p.is_dir(), f"local_root={self.local_root}")
        try:
            s3cfg = self._s3_cfg()
            if not s3cfg["bucket"]:
                return (False, "valve_alerts.json s3.bucket / s3_sync.json bucket / local_root 모두 미설정")
            import boto3  # noqa: F401
            return (True, f"s3://{s3cfg['bucket']}")
        except Exception as e:
            return (False, f"boto3/s3 설정 불가: {e}")

    def _client_bucket(self):
        import boto3
        from core.aws_credentials import boto3_session_kwargs
        s3cfg = self._s3_cfg()
        if not s3cfg["bucket"]:
            raise RuntimeError("S3 bucket 미설정 (valve_alerts.json 또는 s3_sync.json)")
        session_kwargs, profile_conf = boto3_session_kwargs(
            PATHS.data_root, s3cfg.get("profile") or None)
        session = boto3.Session(**session_kwargs)
        client_kwargs: dict[str, Any] = {}
        region = s3cfg.get("region") or profile_conf.get("region")
        endpoint_url = s3cfg.get("endpoint_url") or profile_conf.get("endpoint_url")
        if region:
            client_kwargs["region_name"] = region
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        return session.client("s3", **client_kwargs), s3cfg["bucket"]

    def get_text(self, key: str) -> str | None:
        if self.local_root:
            fp = Path(self.local_root) / key
            if not fp.exists():
                return None
            return fp.read_text(encoding="utf-8")
        try:
            client, bucket = self._client_bucket()
            obj = client.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read().decode("utf-8")
        except Exception as e:
            if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e):
                return None
            raise

    def put_text(self, key: str, text: str) -> bool:
        if self.local_root:
            fp = Path(self.local_root) / key
            fp.parent.mkdir(parents=True, exist_ok=True)
            tmp = fp.with_suffix(fp.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(fp)
            return True
        client, bucket = self._client_bucket()
        ctype = ("application/json" if key.endswith(".json")
                 else "text/csv" if key.endswith(".csv") else "text/plain")
        client.put_object(Bucket=bucket, Key=key,
                          Body=text.encode("utf-8"), ContentType=ctype)
        return True

    def list_keys(self, prefix: str) -> list[str]:
        if self.local_root:
            root = Path(self.local_root) / prefix
            if not root.is_dir():
                return []
            return [f"{prefix}/{p.name}" for p in sorted(root.iterdir()) if p.is_file()]
        client, bucket = self._client_bucket()
        keys: list[str] = []
        token = None
        while True:
            kw = {"Bucket": bucket, "Prefix": prefix.rstrip("/") + "/"}
            if token:
                kw["ContinuationToken"] = token
            resp = client.list_objects_v2(**kw)
            keys.extend(o["Key"] for o in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return keys


def _store() -> _Store:
    return _Store(load_cfg())


def store_push_artifact(fp: Path, key: str) -> str | None:
    """룰북/매칭 아티팩트를 알람 저장소로 즉시 업로드 (s3_sync 보조 경로).

    s3_sync 가 업로드하지 못하는 환경(비활성/버킷 미설정/boto3 없음/실패)에서
    core.s3_sync 가 호출 — 변경점이 생기면 어떤 경로로 저장되든 바로 올라가도록.
    key 는 'matching/<파일명>' 형태, artifacts_prefix 아래로 올라간다.
    """
    cfg = load_cfg()
    store = _Store(cfg)
    if not store.available()[0]:
        return None
    full = f"{cfg['artifacts_prefix'].strip('/')}/{key.lstrip('/')}"
    store.put_text(full, Path(fp).read_text(encoding="utf-8"))
    return full


# ────────────────────────────────────────── 알람 조회 / ack
def _ack_key(cfg: dict) -> str:
    return f"{cfg['alerts_prefix'].strip('/')}/pipeline/ack.json"


def load_ack(store: _Store | None = None) -> dict:
    store = store or _store()
    try:
        text = store.get_text(_ack_key(store.cfg))
        return json.loads(text) if text else {}
    except Exception as e:
        logger.warning("[valve_alerts] ack.json 읽기 실패: %s", e)
        return {}


def set_ack(alert_id: str, status: str, note: str = "", by: str = "flow") -> dict:
    """Valve alert_store 와 동일 포맷. status 가 'active'/빈값이면 해제."""
    store = _store()
    ack = load_ack(store)
    if status and status != "active":
        ack[alert_id] = {"status": status, "note": note, "by": by, "ts": time.time()}
    else:
        ack.pop(alert_id, None)
    store.put_text(_ack_key(store.cfg), json.dumps(ack, ensure_ascii=False, indent=2))
    return ack


def list_alerts() -> dict:
    """S3(또는 로컬) 의 pipeline/*.json 을 모두 읽어 ack/판정 이력과 병합."""
    cfg = load_cfg()
    store = _Store(cfg)
    ok, where = store.available()
    if not ok:
        return {"ok": False, "error": f"알람 저장소 접근 불가 ({where})",
                "alerts": [], "active": 0, "vehicles": []}
    prefix = f"{cfg['alerts_prefix'].strip('/')}/pipeline"
    ack = load_ack(store)
    decided = _decided_ids()
    alerts: list[dict] = []
    vehicles: list[dict] = []
    for key in store.list_keys(prefix):
        name = key.rsplit("/", 1)[-1]
        if not name.endswith(".json") or name == "ack.json":
            continue
        try:
            payload = json.loads(store.get_text(key) or "{}")
        except Exception as e:
            logger.warning("[valve_alerts] %s 파싱 실패: %s", key, e)
            continue
        vehicles.append({"vehicle": payload.get("vehicle") or name[:-5],
                         "published_ts": payload.get("ts"),
                         "count": payload.get("count"),
                         "delta": payload.get("delta") or {}})
        alerts.extend(payload.get("alerts") or [])
    for a in alerts:
        info = ack.get(a.get("id")) or {}
        a["status"] = info.get("status") or "active"
        a["ack_note"] = info.get("note") or ""
        a["decision"] = decided.get(a.get("id"))

    def _sort_group(a: dict) -> int:
        """판정 대기 → 반영완료 → 보류/반영불필요(맨 아래). 그룹 내에서는 최신이 위."""
        if a.get("status") in ("미확인예정", "반영불필요"):
            return 2
        if a.get("decision") or a.get("status") == "반영완료":
            return 1
        return 0

    alerts.sort(key=lambda a: (_sort_group(a), -float(a.get("first_seen_ts") or 0)))
    active = sum(1 for a in alerts if a["status"] == "active" and not a.get("decision"))
    return {"ok": True, "store": where, "alerts": alerts, "active": active,
            "vehicles": vehicles, "ack_key": _ack_key(cfg)}


def _find_alert(alert_id: str) -> dict | None:
    for a in list_alerts().get("alerts", []):
        if a.get("id") == alert_id:
            return a
    return None


# ────────────────────────────────────────── 판정 이력
def _append_decision(rec: dict) -> None:
    rec = {"ts": time.time(), **rec}
    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def list_decisions(limit: int = 200) -> list[dict]:
    if not DECISIONS_PATH.exists():
        return []
    out: list[dict] = []
    try:
        for line in DECISIONS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return out[-max(1, int(limit)):][::-1]


def _decided_ids() -> dict[str, dict]:
    """alert_id → 마지막 반영 판정(classify/match) 요약 (화면 표시용).

    ack(보류/불필요/해제) 이력은 판정으로 치지 않는다 — 해제하면 다시 판정 대기.
    """
    out: dict[str, dict] = {}
    for d in reversed(list_decisions(limit=1000)):
        aid = d.get("alert_id")
        if aid and d.get("action") in ("classify", "match"):
            out[aid] = {"action": d.get("action"), "by": d.get("by"),
                        "ts": d.get("ts"), "detail": d.get("detail")}
    return out


# ────────────────────────────────────────── CSV 반영 공통
def _read_csv_preserving(fp: Path) -> tuple[list[str], list[dict]]:
    """헤더 순서를 보존해 (columns, rows) 반환. BOM 허용."""
    with open(fp, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return cols, rows


def _write_csv_atomic(fp: Path, cols: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: (r.get(c) if r.get(c) is not None else "") for c in cols})
    text = buf.getvalue()
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(fp)
    return text


def _snapshot_version(fp: Path, actor: str, note: str) -> None:
    """filebrowser 의 파일 버전 스냅샷(file_versions/) 재사용 — 실패해도 반영은 유지."""
    try:
        from routers.filebrowser import _snapshot_base_file_version
        _snapshot_base_file_version(fp, fp.name, actor=actor or "valve-alerts",
                                    action="valve-alert", note=note)
    except Exception as e:
        logger.warning("[valve_alerts] 버전 스냅샷 실패(%s): %s", fp.name, e)


def _after_write(fp: Path, actor: str, note: str) -> dict:
    """버전 스냅샷 + matching cache 갱신 + S3 업로드(+로컬 데모 push)."""
    _snapshot_version(fp, actor, note)
    cache_result: dict[str, Any] = {}
    try:
        from core import matching_cache
        cache_result = matching_cache.refresh_matching_csv(fp)
    except Exception as e:
        cache_result = {"ok": False, "error": str(e)}
    # 즉시 업로드 — s3_sync 가 올리고, 올리지 못하는 환경이면 s3_sync 내부에서
    # 알람 저장소(store_push_artifact)로 폴백한다. 결과에 store_push 로 표시됨.
    sync_result: dict[str, Any] = {}
    try:
        from core import s3_sync as _s3
        sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, fp) or {}
    except Exception as e:
        sync_result = {"status": "error", "error": str(e)}
    return {"version_snapshot": True, "cache": cache_result,
            "s3_sync": sync_result, "store_push": sync_result.get("store_push")}


_RULE_NUM = re.compile(r"[Rr](\d+)$")


# ────────────────────────────────────────── 판정 ① RO ppid → knob 분류
def classify_ro_ppid(alert_id: str, category: str, feature_name: str = "",
                     note: str = "", username: str = "") -> dict:
    """ro_ppid 알람 판정: ppid_knob.csv 의 해당 feature 그룹에 다음 Rule 넘버로 추가.

    - feature 그룹: function_step(=step_desc) 이 알람의 step_desc 와 같은 행들.
      없으면 새 그룹 생성 (feature_name 기본값 = step_desc).
    - rule_order: 그룹 내 R{n} 최대값 + 1. RO(rest-of) 행은 번호에서 제외하고
      새 행은 RO 행 바로 앞에 삽입 (없으면 그룹 끝).
    """
    category = str(category or "").strip()
    if not category:
        raise ValueError("category(분류값)가 비어있습니다")
    alert = _find_alert(alert_id)
    if not alert:
        raise LookupError(f"알람을 찾을 수 없음: {alert_id}")
    if alert.get("type") != "ro_ppid":
        raise ValueError(f"ro_ppid 알람이 아님: {alert_id} ({alert.get('type')})")

    step_desc = str(alert.get("step_desc") or "").strip()
    ppid = str(alert.get("ppid") or "").strip()
    if not ppid:
        raise ValueError("알람에 ppid 정보가 없습니다")

    fp = Path(PATHS.db_root) / PPID_KNOB_FILE
    with _write_lock:
        if fp.exists():
            cols, rows = _read_csv_preserving(fp)
        else:
            cols, rows = ["feature_name", "function_step", "rule_order",
                          "operator", "value", "category"], []
        # function_step / step_desc 컬럼명 겸용 (사내 포맷 호환)
        fs_col = "function_step" if "function_step" in cols else (
            "step_desc" if "step_desc" in cols else "function_step")
        if fs_col not in cols:
            cols.append(fs_col)
        for need in ("feature_name", "rule_order", "operator", "value", "category"):
            if need not in cols:
                cols.append(need)

        group_idx = [i for i, r in enumerate(rows)
                     if str(r.get(fs_col) or "").strip() == step_desc]
        feat = str(feature_name or "").strip()
        if not feat:
            feat = (str(rows[group_idx[0]].get("feature_name") or "").strip()
                    if group_idx else step_desc)

        feat_idx = [i for i, r in enumerate(rows)
                    if str(r.get("feature_name") or "").strip() == feat]
        # 중복 룰 방지: 같은 feature 에 같은 ppid(eq) 룰이 이미 있으면 거부
        for i in feat_idx:
            r = rows[i]
            if (str(r.get("operator") or "").strip() == "eq"
                    and str(r.get("value") or "").strip() == ppid
                    and str(r.get("rule_order") or "").strip().upper() != "RO"):
                raise ValueError(
                    f"이미 등록된 룰: {feat} {r.get('rule_order')} eq {ppid} "
                    f"→ {r.get('category')}")

        max_num = 0
        ro_pos = None  # feature 그룹의 RO 행 위치 (새 룰은 그 앞에)
        for i in feat_idx:
            order = str(rows[i].get("rule_order") or "").strip()
            if order.upper() == "RO":
                ro_pos = i if ro_pos is None else min(ro_pos, i)
                continue
            m = _RULE_NUM.search(order)
            if m:
                max_num = max(max_num, int(m.group(1)))
        rule_order = f"R{max_num + 1}"

        new_row = {c: "" for c in cols}
        new_row.update({"feature_name": feat, fs_col: step_desc,
                        "rule_order": rule_order, "operator": "eq",
                        "value": ppid, "category": category})
        if "use" in cols:
            new_row["use"] = "Y"

        if ro_pos is not None:
            rows.insert(ro_pos, new_row)
        elif feat_idx:
            rows.insert(feat_idx[-1] + 1, new_row)
        else:
            rows.append(new_row)

        actor = username or "flow"
        note_full = (f"[Valve 알람 판정] {alert_id}: {ppid} → {category} "
                     f"({feat} {rule_order})" + (f" | {note}" if note else ""))
        _write_csv_atomic(fp, cols, rows)
        post = _after_write(fp, actor, note_full)

    try:
        set_ack(alert_id, "반영완료", note=f"{feat} {rule_order} → {category}", by=actor)
    except Exception as e:
        post["ack_error"] = str(e)
    _append_decision({
        "alert_id": alert_id, "type": "ro_ppid", "action": "classify",
        "vehicle": alert.get("vehicle"), "step_id": alert.get("step_id"),
        "step_desc": step_desc, "ppid": ppid,
        "feature_name": feat, "rule_order": rule_order, "category": category,
        "file": fp.name, "by": actor, "detail": note or f"{ppid} → {category}",
    })
    return {"ok": True, "file": fp.name, "feature_name": feat,
            "rule_order": rule_order, "category": category, **post}


# ────────────────────────────────────────── 판정 ② 미매칭 step → vehicle matching
def match_step(alert_id: str, step_desc: str = "", note: str = "",
               username: str = "") -> dict:
    """unmatched_step 알람 판정: Vehicle_matching.csv 에 vehicle/step_id/step_desc 추가."""
    alert = _find_alert(alert_id)
    if not alert:
        raise LookupError(f"알람을 찾을 수 없음: {alert_id}")
    if alert.get("type") != "unmatched_step":
        raise ValueError(f"unmatched_step 알람이 아님: {alert_id} ({alert.get('type')})")

    vehicle = str(alert.get("vehicle") or "").strip()
    product = str(alert.get("product") or "").strip()
    step_id = str(alert.get("step_id") or "").strip()
    step_desc = str(step_desc or alert.get("step_desc") or "").strip()
    if not (vehicle and step_id):
        raise ValueError("알람에 vehicle/step_id 정보가 없습니다")
    if not step_desc:
        raise ValueError("step_desc 가 비어있습니다")

    fp = Path(PATHS.db_root) / VEHICLE_MATCHING_FILE
    with _write_lock:
        if fp.exists():
            cols, rows = _read_csv_preserving(fp)
        else:
            cols, rows = ["vehicle", "product", "step_id", "step_desc"], []
        for need in ("vehicle", "step_id", "step_desc"):
            if need not in cols:
                cols.append(need)

        for r in rows:
            if (str(r.get("vehicle") or "").strip() == vehicle
                    and str(r.get("step_id") or "").strip() == step_id):
                raise ValueError(
                    f"이미 등록된 매칭: {vehicle} {step_id} → {r.get('step_desc')}")

        new_row = {c: "" for c in cols}
        new_row.update({"vehicle": vehicle, "step_id": step_id, "step_desc": step_desc})
        if "product" in cols:
            new_row["product"] = product
        rows.append(new_row)

        actor = username or "flow"
        note_full = (f"[Valve 알람 판정] {alert_id}: {vehicle} {step_id} → {step_desc}"
                     + (f" | {note}" if note else ""))
        _write_csv_atomic(fp, cols, rows)
        post = _after_write(fp, actor, note_full)

    try:
        set_ack(alert_id, "반영완료", note=f"{step_id} → {step_desc}", by=actor)
    except Exception as e:
        post["ack_error"] = str(e)
    _append_decision({
        "alert_id": alert_id, "type": "unmatched_step", "action": "match",
        "vehicle": vehicle, "product": product, "step_id": step_id,
        "step_desc": step_desc, "file": fp.name, "by": actor,
        "detail": note or f"{step_id} → {step_desc}",
    })
    return {"ok": True, "file": fp.name, "vehicle": vehicle,
            "step_id": step_id, "step_desc": step_desc, **post}


# ────────────────────────────────────────── 판정 ③ 보류/반영불필요 (ack 만)
def hold_alert(alert_id: str, status: str, note: str = "", username: str = "") -> dict:
    """Valve 억제 상태 기록. status: 반영불필요 | active(해제).

    관리 단순화를 위해 억제 상태는 반영불필요 하나만 쓴다 (해제로 언제든 복귀).
    과거 데이터의 미확인예정 은 표시/정렬에서만 허용."""
    allowed = ("반영불필요", "active", "")
    if status not in allowed:
        raise ValueError(f"허용되지 않는 상태: {status} (허용: {allowed})")
    actor = username or "flow"
    set_ack(alert_id, status, note=note, by=actor)
    _append_decision({
        "alert_id": alert_id, "type": "ack", "action": status or "active",
        "by": actor, "detail": note,
    })
    return {"ok": True, "alert_id": alert_id, "status": status or "active"}


# ────────────────────────────────────────── 신규 알람 폴러 (admin 벨 알림)
def poll_once() -> dict:
    cfg = load_cfg()
    if not cfg.get("enabled", True):
        return {"ok": True, "enabled": False}
    data = list_alerts()
    if not data.get("ok"):
        return {"ok": False, "error": data.get("error")}
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    known = set(state.get("known_ids") or [])
    current = {a["id"] for a in data["alerts"] if a.get("id")}
    fresh = [a for a in data["alerts"]
             if a.get("id") and a["id"] not in known and a.get("status") == "active"]
    if fresh and known:  # 최초 1회(콜드스타트)는 알림 생략하고 상태만 기록
        try:
            from core.notify import send_to_admins
            um = sum(1 for a in fresh if a.get("type") == "unmatched_step")
            ro = len(fresh) - um
            body = " | ".join(
                f"{a.get('vehicle')} {a.get('step_id')} "
                f"{a.get('ppid') or a.get('step_desc') or ''}".strip()
                for a in fresh[:5])
            send_to_admins(
                f"[Valve] 신규 파이프라인 알람 {len(fresh)}건 (미매칭 {um} / RO ppid {ro})",
                body + (" …" if len(fresh) > 5 else ""), "warn")
        except Exception as e:
            logger.warning("[valve_alerts] 알림 발송 실패: %s", e)
    state["known_ids"] = sorted(current)
    state["last_poll_ts"] = time.time()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(STATE_PATH, state)
    return {"ok": True, "alerts": len(data["alerts"]), "new": len(fresh)}


def _loop():
    logger.info("[valve_alerts] background poller started")
    while True:
        try:
            poll_once()
        except Exception as e:
            logger.warning("[valve_alerts] poll failed: %s", e)
        cfg = load_cfg()
        time.sleep(max(30, int(cfg.get("poll_seconds", 300) or 300)))


def start_scheduler() -> None:
    global _thread, _started
    with _lock:
        if _started:
            return
        _save_cfg_if_missing()
        _thread = threading.Thread(target=_loop, name="valve-alerts", daemon=True)
        _thread.start()
        _started = True
        logger.info("[valve_alerts] scheduler started")
