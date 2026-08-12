"""core/valve_step_advisor.py — 미매칭 step 의 function step(step_desc) 추천.

매칭알람(`core/valve_alerts.py`)으로 새로 들어온 미매칭 step 은 결국 "이 step_id 가
어느 function step 이냐"를 사람이 정해 `Vehicle_matching.csv` 에 넣는 일이다.
그 후보를 자동으로 좁혀 준다.

판단 근거 (사람이 하는 것과 같은 순서):
  1. step_id 는 앞 영문 prefix 가 같으면 같은 계열이고 숫자가 가까울수록 같은
     공정 구간일 확률이 높다 — `AA100002` 는 `AA100000` 쪽이다.
  2. 숫자만으로는 부족하다. **FAB 최근 며칠치**에서 그 step_id 가 실제로 쓴
     `eqp_id · eqp_model · area · ppid` unique 집합을 뽑고, 앞뒤 매칭 step 들의
     집합과 겹치는 정도를 본다.
     · 같은 ppid 를 쓰는 step 이 있으면 그 step 의 function step 일 확률이 가장 높다.
     · 새 ppid 라면 eqp_id / eqp_model / area 가 같은 쪽을 본다.
     근거는 두 갈래다 — ① 로컬 FAB raw DB 스캔(개발 PC 등 raw 가 있는 환경),
     ② **Valve 가 매칭알람에 실어 보낸 `match_hint`**(`signatures_from_alert`).
     flow 서버에 FAB raw 가 없는 것이 정상이므로 실환경 근거는 보통 ②다.
  3. 위 근거를 정리해 사내 LLM(GPT OSS 120B)에게 최종 선택과 사유를 받는다.

**LLM 이 없어도 동작한다.** 연결이 없거나 실패하면 `llm.applied=False` 와 사유를
남기고, 근거 점수(혹은 근거조차 없으면 step_id 숫자 거리)만으로 고른 결과를 그대로
추천한다 — 화면에는 "AI 미적용"으로 표시된다.

검사 대상은 **판정 대기 중인 미매칭 step 뿐**이다 (반영불필요/미확인예정/판정 완료
제외). 한 번 검사한 step 은 `data/flow-data/valve_step_recommendations.json` 에
기록해 같은 알람이 다시 와도 재탐색하지 않는다 (`force=True` 또는 clear 로만 재검사).
"""
from __future__ import annotations

import csv
import json
import logging
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json

logger = logging.getLogger("flow.valve_step_advisor")

STORE_PATH = PATHS.data_root / "valve_step_recommendations.json"
# 추천 규칙이 달라지면 과거 보조 캐시만 다시 계산한다. 사용자 판정/CSV와는
# 무관한 버전이며, 동일 PPID 우선 탐색을 도입한 버전이 2다.
ALGORITHM_VERSION = 2

# FAB raw 후보 루트 — 먼저 존재하는 것을 쓴다 (lot_step / splittable 과 같은 규약).
FAB_ROOTS = ("1.RAWDATA_DB_FAB", "1.RAWDATA_DB", "FAB")
# 시그니처 열 — DB 에 있는 것만 쓴다. 값은 가중치 (사내 DB 에 area/eqp_model 이
# 없으면 그만큼 남은 열로 정규화된다).
SIGNATURE_WEIGHTS = {
    "ppid": 0.45,
    "eqp_id": 0.25,
    "eqp_model": 0.15,
    "area": 0.10,
    "chamber_id": 0.05,
}
# 시그니처 점수 : step_id 숫자 근접도 배분
SIG_WEIGHT, PROX_WEIGHT = 0.8, 0.2

DEFAULT_SETTINGS = {
    "enabled": True,
    "lookback_days": 14,        # FAB raw 를 며칠치까지 훑을지 (데이터 최신일 기준)
    "neighbors": 3,             # 앞/뒤로 볼 매칭 step 개수
    "max_alerts_per_run": 10,   # 자동 실행 1회당 검사 상한
    "use_llm": True,
}

_STEP_RE = re.compile(r"^([A-Za-z]*?)(\d+)([A-Za-z]*)$")
_DATE_RE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})")

_store_lock = threading.RLock()


# ────────────────────────────────────────── 설정
def settings() -> dict:
    """valve_alerts.json 의 `recommend` 블록 (없으면 기본값)."""
    from core import valve_alerts as _va
    raw = (_va.load_cfg().get("recommend") or {})
    out = dict(DEFAULT_SETTINGS)
    for k, v in raw.items():
        if k in out:
            out[k] = v
    out["enabled"] = bool(out["enabled"])
    out["use_llm"] = bool(out["use_llm"])
    out["lookback_days"] = max(1, min(365, int(out["lookback_days"] or 14)))
    out["neighbors"] = max(1, min(20, int(out["neighbors"] or 3)))
    out["max_alerts_per_run"] = max(1, min(200, int(out["max_alerts_per_run"] or 10)))
    return out


# ────────────────────────────────────────── 기록 (1회 검사 후 재탐색 금지)
def _key(vehicle: str, step_id: str) -> str:
    return f"{vehicle}|{step_id}"


def load_records() -> dict[str, dict]:
    data = load_json(STORE_PATH, {})
    recs = data.get("records") if isinstance(data, dict) else None
    return recs if isinstance(recs, dict) else {}


def _save_records(recs: dict[str, dict]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_json(STORE_PATH, {"version": 1, "records": recs})


def get_record(vehicle: str, step_id: str) -> dict | None:
    return load_records().get(_key(vehicle, step_id))


def put_record(rec: dict) -> None:
    with _store_lock:
        recs = load_records()
        recs[_key(rec.get("vehicle", ""), rec.get("step_id", ""))] = rec
        _save_records(recs)


def _stale(rec: dict, vehicle: str) -> bool:
    """추천이 나온 기록은 절대 다시 안 본다. 단 "같은 계열 후보가 없어서" 못 낸
    기록은 매칭 테이블이 바뀌면 다시 본다 — 그때는 후보가 생겼을 수 있다."""
    try:
        cached_version = int(rec.get("algorithm_version") or 0)
    except (TypeError, ValueError):
        cached_version = 0
    if cached_version != ALGORITHM_VERSION:
        return True
    if rec.get("method") != "none":
        return False
    return rec.get("matched_fp") != matched_fingerprint(vehicle)


def clear_record(vehicle: str, step_id: str) -> bool:
    with _store_lock:
        recs = load_records()
        if recs.pop(_key(vehicle, step_id), None) is None:
            return False
        _save_records(recs)
        return True


# ────────────────────────────────────────── step_id 파싱 / 매칭 테이블
def split_step_id(step_id: str) -> tuple[str, int | None]:
    """`AA100002` → ("AA", 100002). 숫자가 없으면 (원문, None)."""
    s = str(step_id or "").strip()
    m = _STEP_RE.match(s)
    if not m:
        return s, None
    return m.group(1).upper(), int(m.group(2))


def matched_steps(vehicle: str) -> list[dict]:
    """Vehicle_matching.csv 에서 해당 vehicle 의 (step_id, step_desc) 목록."""
    fp = Path(PATHS.db_root) / "Vehicle_matching.csv"
    if not fp.exists():
        return []
    out: list[dict] = []
    try:
        with open(fp, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("vehicle") or "").strip() != vehicle:
                    continue
                step_id = str(r.get("step_id") or "").strip()
                desc = str(r.get("step_desc") or "").strip()
                if step_id and desc:
                    out.append({"step_id": step_id, "step_desc": desc,
                                "product": str(r.get("product") or "").strip()})
    except OSError as e:
        logger.warning("[step_advisor] Vehicle_matching.csv 읽기 실패: %s", e)
    return out


def matched_fingerprint(vehicle: str) -> str:
    """해당 vehicle 의 매칭 step 목록 지문 — "후보 없음" 기록의 재검사 조건."""
    return ",".join(sorted(m["step_id"] for m in matched_steps(vehicle)))


def neighbor_candidates(vehicle: str, step_id: str, count: int) -> list[dict]:
    """같은 영문 prefix 를 가진 매칭 step 중 숫자가 가장 가까운 앞/뒤 count 개씩."""
    prefix, num = split_step_id(step_id)
    if num is None:
        return []
    same: list[dict] = []
    for m in matched_steps(vehicle):
        p, n = split_step_id(m["step_id"])
        if n is None or p != prefix:
            continue
        same.append({**m, "num": n, "delta": n - num})
    before = sorted([m for m in same if m["delta"] < 0], key=lambda m: -m["delta"])[:count]
    after = sorted([m for m in same if m["delta"] > 0], key=lambda m: m["delta"])[:count]
    exact = [m for m in same if m["delta"] == 0]
    return sorted(exact + before + after, key=lambda m: m["num"])


def matched_candidate_pool(vehicle: str, step_id: str, product: str = "") -> list[dict]:
    """동일 PPID 후보를 찾기 위한 매칭 완료 step 전체 목록.

    PPID가 같으면 step_id 영문 계열이 달라도 같은 function step일 수 있으므로
    ``neighbor_candidates``보다 넓게 본다. 제품이 명시된 경우 다른 제품에만
    속한 행은 제외하되, product가 비어 있는 기존 매칭 행은 계속 후보로 둔다.
    """
    target_prefix, target_num = split_step_id(step_id)
    rows = matched_steps(vehicle)
    if product:
        product_key = product.casefold()
        scoped = [m for m in rows if not str(m.get("product") or "").strip()
                  or str(m.get("product") or "").strip().casefold() == product_key]
        if scoped:
            rows = scoped

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in rows:
        candidate_id = str(item.get("step_id") or "").strip()
        step_desc = str(item.get("step_desc") or "").strip()
        if not candidate_id or not step_desc or candidate_id == step_id:
            continue
        key = (candidate_id, step_desc)
        if key in seen:
            continue
        seen.add(key)
        prefix, number = split_step_id(candidate_id)
        # 숫자를 읽지 못하는 후보도 동일 PPID 검색에서는 버리지 않는다. 숫자
        # 근접도는 동률 정렬에만 쓰이므로 해석 불가 후보에는 큰 거리를 준다.
        delta = number - target_num if number is not None and target_num is not None else 10**12
        out.append({**item, "num": number, "delta": delta,
                    "same_prefix": prefix == target_prefix})
    return out


def _alert_signature_values(alert: dict, column: str) -> list[str]:
    """알람의 단수/복수 시그니처 필드를 하나의 unique 목록으로 정규화한다."""
    plural = {"ppid": "ppids", "eqp_id": "eqp_ids", "eqp_model": "eqp_models",
              "area": "areas"}.get(column, f"{column}s")
    values: list[str] = []
    for raw in (alert.get(column), alert.get(plural)):
        items = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
        for value in items:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
    return values


# ────────────────────────────────────────── FAB raw 시그니처
def _fab_dir(product: str) -> Path | None:
    db_root = Path(PATHS.db_root)
    for root in FAB_ROOTS:
        p = db_root / root / product
        if p.is_dir():
            return p
    return None


def _partition_date(path: Path) -> date | None:
    for part in reversed(path.parts):
        if not part.startswith("date="):
            continue
        m = _DATE_RE.search(part)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    return None


def _recent_files(fab_dir: Path, lookback_days: int) -> tuple[list[Path], str]:
    """최근 lookback_days 치 파티션 파일. 기준은 **데이터의 최신 날짜**다 —
    실서버는 오늘이 최신이고, 시드 DB 처럼 과거 데이터만 있어도 그 마지막 며칠을
    본다 (윈도우는 근거에 그대로 남긴다)."""
    files = [p for p in fab_dir.rglob("*.parquet") if "_backups" not in p.parts]
    if not files:
        return [], ""
    dated = [(p, _partition_date(p)) for p in files]
    known = [d for _p, d in dated if d]
    if not known:
        return sorted(files), "전체"
    newest = max(known)
    start = newest - timedelta(days=max(0, lookback_days - 1))
    picked = [p for p, d in dated if d and start <= d <= newest]
    return sorted(picked or files), f"{start.isoformat()}~{newest.isoformat()}"


def fab_signatures(product: str, step_ids: list[str],
                   lookback_days: int) -> tuple[dict[str, dict], dict]:
    """step_id → {열: [unique 값…], "rows": n} + 스캔 정보."""
    info: dict[str, Any] = {"product": product, "files": 0, "window": "",
                            "cols": [], "source": "", "error": ""}
    fab = _fab_dir(product)
    if fab is None:
        info["error"] = f"FAB raw 폴더 없음 ({'/'.join(FAB_ROOTS)} 아래 {product})"
        return {}, info
    info["source"] = str(fab.relative_to(Path(PATHS.db_root))).replace("\\", "/")
    files, window = _recent_files(fab, lookback_days)
    info["files"], info["window"] = len(files), window
    if not files or not step_ids:
        info["error"] = info["error"] or "스캔할 파티션 없음"
        return {}, info
    try:
        import polars as pl
    except ImportError:
        info["error"] = "polars 미설치 — FAB 근거 없이 진행"
        return {}, info
    try:
        lf = pl.scan_parquet(files).filter(pl.col("step_id").is_in(list(step_ids)))
        have = set(lf.collect_schema().names())
        cols = [c for c in SIGNATURE_WEIGHTS if c in have]
        info["cols"] = cols
        aggs = [pl.len().alias("rows")]
        aggs += [pl.col(c).cast(pl.Utf8).unique().sort().alias(c) for c in cols]
        df = lf.group_by("step_id").agg(aggs).collect()
    except Exception as e:                      # 스키마 차이/손상 파티션
        info["error"] = f"FAB 스캔 실패: {e}"
        return {}, info
    out: dict[str, dict] = {}
    for row in df.iter_rows(named=True):
        sid = str(row.pop("step_id"))
        out[sid] = {k: ([str(x) for x in v if x is not None] if isinstance(v, list) else v)
                    for k, v in row.items()}
    return out, info


def signatures_from_alert(alert: dict) -> tuple[dict[str, dict], dict]:
    """Valve 알람이 실어 보낸 이웃 step 컨텍스트(`match_hint`)를 시그니처로 변환.

    **flow 서버에 FAB raw DB 가 없는 것이 정상이다** — raw 는 Valve 가 들고 있고
    flow 로는 매칭/룰북 CSV 와 ML_TABLE 만 온다. 그래서 실환경에서는 이게 유일한
    근거다: Valve `feature_pipeline._step_match_hints` 가 신규 step 과 같은
    prefix·자릿수의 앞뒤 매칭 step 들을 골라, 최근 며칠치 FAB raw 에서 각각의
    ppid·eqp_id·eqp_model·area unique 를 뽑아 알람에 실어 보낸다.
    로컬 FAB 스캔이 되는 환경(개발 PC)에서는 그쪽이 우선이고 여기서는 빈 자리만 채운다.
    """
    hint = alert.get("match_hint")
    if not isinstance(hint, dict):
        return {}, {}

    def conv(node: dict) -> dict:
        vals = node.get("values") or {}
        out: dict[str, Any] = {c: [str(v) for v in (vals.get(c) or []) if str(v or "")]
                               for c in SIGNATURE_WEIGHTS if vals.get(c)}
        out["rows"] = node.get("rows") or 0
        return out

    sigs: dict[str, dict] = {}
    sid = str(hint.get("step_id") or alert.get("step_id") or "").strip()
    if sid:
        sigs[sid] = conv(hint)
    for n in (hint.get("neighbors") or []):
        nid = str(n.get("step_id") or "").strip()
        if nid:
            sigs[nid] = conv(n)
    w = hint.get("window") or {}
    window = f"{w.get('from', '')}~{w.get('to', '')}".strip("~")
    return sigs, {"days": hint.get("days"), "window": window,
                  "steps": len(sigs), "cols": list(hint.get("cols") or [])}


def _similarity(a: list[str], b: list[str]) -> float:
    """두 unique 집합의 닮은 정도 (0~1).

    포함도(overlap coefficient)를 주로 쓰고 자카드를 보조로 섞는다. 파티션마다
    표본 수가 달라 뒤쪽 step 은 값 종류가 덜 잡히는데, 자카드만 쓰면 그걸 "다른
    step"으로 깎아내린다 — 실제로는 "같은 설비/ppid 를 쓰는데 표본이 적을 뿐"이다.
    """
    sa, sb = set(a or []), set(b or [])
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    containment = inter / min(len(sa), len(sb))
    jaccard = inter / len(sa | sb)
    return 0.7 * containment + 0.3 * jaccard


def score_candidates(target_sig: dict, candidates: list[dict],
                     sigs: dict[str, dict]) -> list[dict]:
    """후보별 (시그니처 유사도 + 숫자 근접도) 점수. 큰 값이 먼저."""
    gaps = [abs(c["delta"]) for c in candidates if c.get("delta")]
    unit = min(gaps) if gaps else 1
    scored: list[dict] = []
    for c in candidates:
        sig = sigs.get(c["step_id"]) or {}
        sims: dict[str, float] = {}
        wsum = 0.0
        acc = 0.0
        for col, w in SIGNATURE_WEIGHTS.items():
            if col not in target_sig or col not in sig:
                continue
            s = _similarity(target_sig.get(col) or [], sig.get(col) or [])
            sims[col] = round(s, 4)
            acc += w * s
            wsum += w
        sig_score = (acc / wsum) if wsum else 0.0
        prox = 1.0 / (1.0 + abs(c["delta"]) / max(1, unit))
        total = (SIG_WEIGHT * sig_score + PROX_WEIGHT * prox) if wsum else prox
        shared_ppid = sorted(set(target_sig.get("ppid") or [])
                             & set(sig.get("ppid") or []))
        scored.append({
            "step_id": c["step_id"], "step_desc": c["step_desc"],
            "delta": c["delta"], "rows": sig.get("rows", 0),
            "similarity": sims, "sig_score": round(sig_score, 4),
            "proximity": round(prox, 4), "score": round(total, 4),
            "shared_ppid": shared_ppid[:5],
            "has_evidence": bool(wsum),
        })
    scored.sort(key=lambda x: (-x["score"], abs(x["delta"])))
    return scored


# ────────────────────────────────────────── LLM (있으면 최종 선택, 없어도 동작)
_LLM_SYSTEM = (
    "너는 반도체 공정 데이터 엔지니어다. 새로 발견된 미매칭 step_id 가 어떤 "
    "function step(step_desc)인지 후보 중에서 고른다. 판단 우선순위: "
    "(1) 같은 ppid 를 쓰는 후보, (2) eqp_id·eqp_model·area 가 겹치는 후보, "
    "(3) step_id 숫자가 가까운 후보. 후보 목록에 없는 이름은 절대 만들지 마라."
)
_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "step_desc": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["step_desc", "confidence", "reason"],
}


def _llm_prompt(alert: dict, target_sig: dict, scored: list[dict], info: dict) -> str:
    def fmt(sig: dict) -> str:
        parts = [f"{c}={','.join((sig.get(c) or [])[:8])}"
                 for c in SIGNATURE_WEIGHTS if sig.get(c)]
        return " | ".join(parts) or "(FAB 근거 없음)"

    lines = [
        f"vehicle={alert.get('vehicle')} product={alert.get('product')} "
        f"신규 step_id={alert.get('step_id')}",
        f"알람이 함께 보낸 값: eqp_id={alert.get('eqp_id') or '-'} "
        f"eqp_model={alert.get('eqp_model') or '-'} area={alert.get('area') or '-'} "
        f"ppid={alert.get('ppid') or '-'}",
        f"근거 출처: {info.get('source') or '-'} / 기간 {info.get('window') or '-'}"
        + (f" / 파티션 {info.get('files')}개" if info.get("files") else ""),
        f"신규 step 의 FAB unique: {fmt(target_sig)}",
        "",
        "후보 (step_id · function step · 숫자거리 · 계산점수 · FAB unique):",
    ]
    for c in scored:
        sig = {k: v for k, v in (c.get("_sig") or {}).items()}
        lines.append(
            f"- {c['step_id']} · {c['step_desc']} · Δ{c['delta']:+d} · "
            f"score={c['score']} · 공통ppid={','.join(c['shared_ppid']) or '없음'} · {fmt(sig)}")
    lines += [
        "",
        "위 후보 중 하나의 step_desc 를 골라 JSON 으로만 답하라: "
        '{"step_desc": "...", "confidence": 0~1 숫자, "reason": "한국어 한두 문장"}',
    ]
    return "\n".join(lines)


def _ask_llm(alert: dict, target_sig: dict, scored: list[dict],
             info: dict, sigs: dict) -> dict:
    """{applied, step_desc, confidence, reason, error} — 실패해도 예외를 던지지 않는다."""
    out = {"applied": False, "step_desc": "", "confidence": 0.0, "reason": "", "error": ""}
    try:
        from core import llm_adapter
    except Exception as e:                       # import 실패 = 환경 문제
        out["error"] = f"llm_adapter 로드 실패: {e}"
        return out
    try:
        if not llm_adapter.is_available():
            out["error"] = "LLM 미연결 (관리자 설정에서 사내 LLM 프로필 확인)"
            return out
        if not llm_adapter.should_attempt_llm():
            out["error"] = "LLM 헬스 차단 상태 (직전 호출 실패)"
            return out
        enriched = [{**c, "_sig": sigs.get(c["step_id"]) or {}} for c in scored]
        res = llm_adapter.complete_json(
            _llm_prompt(alert, target_sig, enriched, info),
            system=_LLM_SYSTEM, schema=_LLM_SCHEMA, timeout=45)
    except Exception as e:
        out["error"] = f"LLM 호출 예외: {e}"
        return out
    if not res.get("ok"):
        out["error"] = str(res.get("error") or "LLM 응답 없음")
        return out
    obj = res.get("obj") or {}
    picked = str(obj.get("step_desc") or "").strip()
    allowed = {c["step_desc"] for c in scored}
    if picked not in allowed:
        out["error"] = f"후보에 없는 답: {picked or '(빈값)'}"
        return out
    try:
        conf = float(obj.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    out.update({"applied": True, "step_desc": picked,
                "confidence": max(0.0, min(1.0, conf)),
                "reason": str(obj.get("reason") or "").strip()})
    return out


# ────────────────────────────────────────── 추천 1건
def recommend(alert: dict, *, force: bool = False, use_llm: bool | None = None) -> dict:
    """미매칭 step 알람 1건에 대한 function step 추천.

    캐시가 있으면 그대로 돌려준다(재탐색 금지). 결과는 항상 dict 이며
    `step_desc` 가 비어 있으면 추천할 근거가 없었다는 뜻이다.
    """
    vehicle = str(alert.get("vehicle") or "").strip()
    step_id = str(alert.get("step_id") or "").strip()
    if not (vehicle and step_id):
        return {"ok": False, "error": "vehicle/step_id 없음"}
    if not force:
        cached = get_record(vehicle, step_id)
        if cached and not _stale(cached, vehicle):
            return {**cached, "cached": True}

    cfg = settings()
    if use_llm is None:
        use_llm = cfg["use_llm"]
    product = str(alert.get("product") or "").strip()
    if not product:
        product = next((m["product"] for m in matched_steps(vehicle) if m["product"]), "")

    neighbors = neighbor_candidates(vehicle, step_id, cfg["neighbors"])
    # 기본 추천은 step 번호 계열보다 PPID를 먼저 본다. 같은 vehicle/product의
    # 매칭 완료 step 전체를 스캔한 뒤, target과 PPID가 겹치는 step만 후보로 좁힌다.
    pool = matched_candidate_pool(vehicle, step_id, product)
    rec: dict[str, Any] = {
        "ok": True, "vehicle": vehicle, "step_id": step_id, "product": product,
        "algorithm_version": ALGORITHM_VERSION,
        "alert_id": alert.get("id") or f"um|{vehicle}|{step_id}",
        "step_desc": "", "confidence": 0.0, "method": "none", "reason": "",
        "candidates": [], "evidence": {}, "llm": {"applied": False, "error": ""},
        "ts": time.time(), "cached": False,
    }
    if not pool and not neighbors:
        have = sorted({split_step_id(m["step_id"])[0] for m in matched_steps(vehicle)})
        rec["reason"] = (
            f"Vehicle_matching.csv 에 {vehicle} 의 같은 계열"
            f"({split_step_id(step_id)[0]}…) 매칭 step 이 없어 비교 대상이 없습니다"
            + (f" — 등록된 계열: {', '.join(have)}" if have else ""))
        rec["llm"] = {"applied": False, "error": "후보가 없어 AI 를 호출하지 않았습니다"}
        # 기록은 남기되 매칭 테이블 지문을 같이 박아 둔다 — 같은 계열 step 이
        # 등록되면 그때 다시 본다 (_stale). 그 전까지는 재탐색하지 않는다.
        rec["matched_fp"] = matched_fingerprint(vehicle)
        put_record(rec)
        return rec

    signature_ids = list(dict.fromkeys([step_id] + [c["step_id"] for c in pool or neighbors]))
    sigs, info = fab_signatures(product, signature_ids,
                                cfg["lookback_days"])
    # Valve 가 알람에 실어 보낸 이웃 step 컨텍스트로 빈 자리를 채운다 —
    # flow 서버에는 FAB raw 가 없으므로 실환경에서는 이게 근거의 전부다.
    hint_sigs, hint_info = signatures_from_alert(alert)
    for sid, sig in hint_sigs.items():
        if not sigs.get(sid):
            sigs[sid] = sig
    if hint_sigs:
        info["valve_hint"] = hint_info
        info["window"] = info.get("window") or hint_info.get("window") or ""
        info["source"] = info.get("source") or "Valve 알람 컨텍스트(match_hint)"
        info["error"] = ""      # 근거를 알람으로 받았으므로 로컬 스캔 실패는 문제가 아니다
    target_sig = sigs.get(step_id) or {}
    # 알람이 실어 온 값도 근거로 쓴다 — FAB 스캔이 비었을 때의 유일한 시그니처.
    for col in ("eqp_id", "eqp_model", "area", "ppid"):
        combined = list(target_sig.get(col) or [])
        for value in _alert_signature_values(alert, col):
            if value not in combined:
                combined.append(value)
        if combined:
            target_sig[col] = combined

    target_ppids = set(target_sig.get("ppid") or [])
    ppid_candidates = [
        c for c in pool
        if target_ppids & set((sigs.get(c["step_id"]) or {}).get("ppid") or [])
    ]
    candidates = ppid_candidates or neighbors
    if not candidates:
        have = sorted({split_step_id(m["step_id"])[0] for m in matched_steps(vehicle)})
        rec["reason"] = (
            f"{step_id}와 같은 PPID를 쓰는 매칭 완료 step이 없고, "
            f"같은 계열({split_step_id(step_id)[0]}…) 후보도 없습니다"
            + (f" — 등록된 계열: {', '.join(have)}" if have else ""))
        rec["evidence"] = {**info, "target": {k: v for k, v in target_sig.items()
                                                if k in SIGNATURE_WEIGHTS}}
        rec["llm"] = {"applied": False, "error": "후보가 없어 AI를 호출하지 않았습니다"}
        rec["matched_fp"] = matched_fingerprint(vehicle)
        put_record(rec)
        return rec

    scored = score_candidates(target_sig, candidates, sigs)
    rec["candidates"] = scored
    rec["evidence"] = {**info, "target": {k: v for k, v in target_sig.items()
                                          if k in SIGNATURE_WEIGHTS}}
    has_evidence = any(c["has_evidence"] for c in scored)
    top = scored[0]
    has_ppid_match = bool(ppid_candidates)
    rec.update({"step_desc": top["step_desc"], "confidence": round(top["score"], 3),
                "method": "ppid" if has_ppid_match else
                          ("signature" if has_evidence else "distance")})
    nearest = min(scored, key=lambda c: abs(c["delta"]))
    rec["reason"] = (
        f"{top['step_id']}의 매칭된 function step을 추천합니다"
        f"(동일 PPID {', '.join(top['shared_ppid'])})"
        if has_ppid_match else
        f"{top['step_id']} 와 FAB 시그니처가 가장 비슷합니다"
        f"({'공통 ppid ' + ', '.join(top['shared_ppid']) if top['shared_ppid'] else 'ppid 겹침 없음'}"
        f", 유사도 {top['sig_score']})"
        if has_evidence else
        f"FAB 근거를 찾지 못해 step_id 숫자가 가장 가까운 {nearest['step_id']} 로 추천합니다"
        f"({info.get('error') or '해당 step 의 최근 데이터 없음'})")

    if use_llm and not has_ppid_match:
        llm = _ask_llm(alert, target_sig, scored, info, sigs)
        rec["llm"] = llm
        if llm["applied"]:
            picked = next((c for c in scored if c["step_desc"] == llm["step_desc"]), None)
            rec.update({"step_desc": llm["step_desc"], "method": "ai",
                        "confidence": round(llm["confidence"], 3),
                        "reason": llm["reason"] or rec["reason"]})
            if picked:
                rec["picked_step_id"] = picked["step_id"]
    else:
        rec["llm"] = {"applied": False, "error": (
            "동일 PPID 매칭을 우선 적용해 AI를 호출하지 않았습니다"
            if has_ppid_match else "AI 사용 안 함 설정")}
    if rec["method"] != "ai":
        rec["picked_step_id"] = top["step_id"] if has_evidence else nearest["step_id"]
        if not has_evidence:
            rec["step_desc"] = nearest["step_desc"]
            rec["confidence"] = round(nearest["proximity"], 3)
    put_record(rec)
    return rec


# ────────────────────────────────────────── 일괄 (판정 대기 건만)
SKIP_STATUSES = ("반영불필요", "미확인예정")


def needs_recommendation(alert: dict, records: dict[str, dict] | None = None) -> bool:
    if alert.get("type") != "unmatched_step":
        return False
    if alert.get("status") in SKIP_STATUSES or alert.get("decision"):
        return False
    recs = load_records() if records is None else records
    vehicle = str(alert.get("vehicle") or "")
    rec = recs.get(_key(vehicle, str(alert.get("step_id") or "")))
    return rec is None or _stale(rec, vehicle)


def recommend_pending(alerts: list[dict] | None = None, *, force: bool = False,
                      limit: int | None = None) -> dict:
    """판정 대기 중이고 아직 검사한 적 없는 미매칭 step 을 일괄 검사."""
    cfg = settings()
    if not cfg["enabled"] and not force:
        return {"ok": True, "enabled": False, "checked": 0, "records": []}
    if alerts is None:
        from core import valve_alerts as _va
        data = _va.list_alerts()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("error"), "checked": 0, "records": []}
        alerts = data.get("alerts") or []
    records = load_records()
    todo = [a for a in alerts
            if a.get("type") == "unmatched_step"
            and a.get("status") not in SKIP_STATUSES and not a.get("decision")
            and (force or needs_recommendation(a, records))]
    cap = cfg["max_alerts_per_run"] if limit is None else max(1, int(limit))
    out: list[dict] = []
    for alert in todo[:cap]:
        try:
            out.append(recommend(alert, force=force))
        except Exception as e:                   # 한 건 실패가 배치를 죽이지 않게
            logger.warning("[step_advisor] %s 추천 실패: %s", alert.get("id"), e)
            out.append({"ok": False, "vehicle": alert.get("vehicle"),
                        "step_id": alert.get("step_id"), "error": str(e)})
    return {"ok": True, "enabled": cfg["enabled"], "pending": len(todo),
            "checked": len(out), "skipped": max(0, len(todo) - cap), "records": out}
