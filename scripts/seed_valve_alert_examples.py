# -*- coding: utf-8 -*-
"""scripts/seed_valve_alert_examples.py — 매칭알람 예시 알람을 DB 폴더에 만든다.

Valve 가 발행한 알람을 flow DB 폴더로 받아온 상태를 재현한다. 실제 Valve
(`backend/core/alert_store.py` publish)가 쓰는 payload 스키마 그대로 쓰고,
행수·lot 수·예시 lot/wafer·eqp/ppid 는 **DB 의 FAB raw 를 실제로 읽어서** 채우므로
추천 엔진(core/valve_step_advisor.py)이 같은 데이터를 보고 동작한다.

    python scripts/seed_valve_alert_examples.py            # 미리보기 (파일 안 씀)
    python scripts/seed_valve_alert_examples.py --write
    python scripts/seed_valve_alert_examples.py --write --force   # 기존 파일 덮어쓰기

출력 위치는 `{db_root}/{alerts_prefix}/pipeline/` — data/flow-data/valve_alerts.json
의 local_root(`{db_root}` 토큰 해석) 와 alerts_prefix 를 그대로 따른다.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from core import valve_alerts as va          # noqa: E402
from core.paths import PATHS                 # noqa: E402

FAB_ROOTS = ("1.RAWDATA_DB_FAB", "1.RAWDATA_DB", "FAB")
DAY = 86400.0

# 예시 알람 정의 — step_id 는 FAB raw 에 실제로 있고 Vehicle_matching.csv 에는
# 없는 것으로 고른다 (그게 곧 "미매칭 step" 의 정의다).
UNMATCHED = [
    # (vehicle, product, step_id, step_desc, area, ack_status, first_seen_days_ago)
    ("VH_PRODA", "PRODA", "AA100510", "", "BEOL", "", 3),
    ("VH_PRODA", "PRODA", "AA100590", "", "BEOL", "", 1),
    ("VH_PRODA", "PRODA", "AA100230", "", "FEOL", "반영불필요", 21),
    ("VH_PRODB", "PRODB", "AB100120", "", "FEOL", "", 2),
]
# Valve 가 step_desc 를 raw 에서 얻지 못한 건은 빈 값으로 온다 — 판정 화면에서
# 엔지니어가 채우는 자리이고, 추천 엔진이 채워 넣는 자리이기도 하다.
LEGACY_UNMATCHED = [
    # DB 파티션에 없는(이미 지나간) step — 근거 없음 경로 확인용
    ("VH_PRODA", "PRODA", "XX777700", "IMP_WELL", "FEOL", "반영불필요", 30, "IMP_01", "I-2000"),
    ("VH_PRODB", "PRODB", "XX777700", "IMP_WELL", "FEOL", "", 30, "IMP_01", "I-2000"),
]
RO_PPID = [
    # (vehicle, product, step_id, step_desc, ppid, ack_status, first_seen_days_ago)
    ("VH_PRODA", "PRODA", "CC942300", "GATE_ETCH", "PP_GE_A7", "", 4),
    ("VH_PRODA", "PRODA", "CC970200", "CONTACT_ETCH", "PP_X9_0200", "반영완료", 14),
    ("VH_PRODB", "PRODB", "CC980500", "METAL_ETCH", "PP_ME_C9", "", 2),
    ("VH_PRODB", "PRODB", "CC980500", "METAL_ETCH", "PP_X9_0500", "반영완료", 16),
]
ACK_NOTES = {
    "ro|VH_PRODA|CC970200|PP_X9_0200": ("반영완료", "10.0 CONTACT R2 → 테스트1"),
    "ro|VH_PRODB|CC980500|PP_X9_0500": ("반영완료", "METAL_ETCH R2 → KNOB_M2"),
    "um|VH_PRODA|AA100230": ("반영불필요", "PM 검증 스텝 — 매칭 대상 아님"),
    "um|VH_PRODA|XX777700": ("반영불필요", ""),
}
ALERT_COLS = ["eqp_id", "eqp_model", "area", "ppid"]
EQP_MODEL = {"PRODA": "ETCH-8100", "PRODB": "ETCH-8100"}


def _fab_dir(db_root: Path, product: str) -> Path | None:
    for root in FAB_ROOTS:
        p = db_root / root / product
        if p.is_dir():
            return p
    return None


def _fab_facts(db_root: Path, product: str, step_ids: set[str]) -> dict[str, dict]:
    """step_id 별 실제 (rows, n_lots, eqp_id, ppid, examples) — 없으면 빈 dict."""
    d = _fab_dir(db_root, product)
    if d is None or not step_ids:
        return {}
    try:
        import polars as pl
    except ImportError:
        print("  ! polars 없음 — FAB 실측 없이 기본값으로 채웁니다")
        return {}
    files = sorted(p for p in d.rglob("*.parquet") if "_backups" not in str(p))
    if not files:
        return {}
    out: dict[str, dict] = {}
    lf = pl.scan_parquet(files).filter(pl.col("step_id").is_in(sorted(step_ids)))
    have = set(lf.collect_schema().names())
    aggs = [pl.len().alias("rows"),
            pl.col("lot_id").n_unique().alias("n_lots") if "lot_id" in have
            else pl.lit(0).alias("n_lots")]
    for col in ("eqp_id", "ppid"):
        aggs.append(pl.col(col).unique().sort().alias(col) if col in have
                    else pl.lit([]).alias(col))
    if {"root_lot_id", "wafer_id"} <= have:
        aggs.append(pl.struct(["root_lot_id", "wafer_id"]).unique(maintain_order=True)
                    .head(3).alias("examples"))
    for row in lf.group_by("step_id").agg(aggs).collect().iter_rows(named=True):
        out[row["step_id"]] = row
    return out


def _match_hint(vehicle: str, product: str, step_id: str, db_root: Path,
                matched: list[dict]) -> dict:
    """Valve alert_store 가 미매칭 step 에 실어 보내는 이웃 step 컨텍스트(v2).

    사내에서는 flow 서버에 FAB raw 가 없으므로 추천의 근거는 이것뿐이다
    (Valve feature_pipeline._step_match_hints). 값은 여기서도 FAB raw 를 실제로
    읽어 채운다 — 지어내지 않는다.
    """
    prefix, num, width = _parse_step(step_id)
    if num is None:
        return {}
    near = []
    for m in matched:
        if m["vehicle"] != vehicle:
            continue
        p, n, w = _parse_step(m["step_id"])
        if n is None or p != prefix or w != width or n == num:
            continue
        near.append((abs(n - num), n, m))
    prev = sorted([c for c in near if c[1] < num], key=lambda c: c[0])[:2]
    nxt = sorted([c for c in near if c[1] > num], key=lambda c: c[0])[:2]
    picked = sorted(prev + nxt, key=lambda c: c[1])
    facts = _fab_facts(db_root, product, {step_id} | {m["step_id"] for _g, _n, m in picked})
    me = facts.get(step_id) or {}

    def node(sid: str, extra: dict) -> dict:
        f = facts.get(sid) or {}
        return {**extra, "rows": int(f.get("rows") or 0),
                "n_lots": int(f.get("n_lots") or 0),
                "values": {c: list(f.get(c) or [])[:20] for c in ("ppid", "eqp_id")}}

    return {
        "step_id": step_id, "prefix": prefix, "number": num,
        "days": 14, "window": {"from": "", "to": "", "dates": 0,
                               "time_col": "tkout_time"},
        "cols": ["ppid", "eqp_id"],
        **node(step_id, {}),
        "neighbors": [node(m["step_id"], {
            "step_id": m["step_id"], "step_desc": m["step_desc"],
            "direction": "prev" if n < num else "next", "gap": gap})
            for gap, n, m in picked],
    }


def _parse_step(step_id: str) -> tuple[str, int | None, int]:
    s = str(step_id or "").strip()
    i = 0
    while i < len(s) and s[i].isalpha():
        i += 1
    digits = ""
    j = i
    while j < len(s) and s[j].isdigit():
        digits += s[j]
        j += 1
    if not digits:
        return s, None, 0
    return s[:i].upper(), int(digits), len(digits)


def _matched_rows(db_root: Path) -> list[dict]:
    fp = db_root / va.VEHICLE_MATCHING_FILE
    if not fp.exists():
        return []
    with open(fp, "r", encoding="utf-8-sig", newline="") as f:
        return [{"vehicle": str(r.get("vehicle") or "").strip(),
                 "step_id": str(r.get("step_id") or "").strip(),
                 "step_desc": str(r.get("step_desc") or "").strip()}
                for r in csv.DictReader(f)]


def _matched_steps(db_root: Path) -> set[tuple[str, str]]:
    fp = db_root / va.VEHICLE_MATCHING_FILE
    if not fp.exists():
        return set()
    with open(fp, "r", encoding="utf-8-sig", newline="") as f:
        return {(str(r.get("vehicle") or "").strip(), str(r.get("step_id") or "").strip())
                for r in csv.DictReader(f)}


def build_payloads(db_root: Path, now: float) -> dict[str, dict]:
    matched = _matched_steps(db_root)
    matched_rows = _matched_rows(db_root)
    by_product: dict[str, set[str]] = {}
    for _v, product, step_id, *_rest in UNMATCHED:
        by_product.setdefault(product, set()).add(step_id)
    facts = {p: _fab_facts(db_root, p, s) for p, s in by_product.items()}

    alerts: dict[str, list[dict]] = {}
    for vehicle, product, step_id, step_desc, area, ack, days in UNMATCHED:
        if (vehicle, step_id) in matched:
            print(f"  ! {vehicle} {step_id} 는 이미 Vehicle_matching.csv 에 있음 — 건너뜀")
            continue
        f = (facts.get(product) or {}).get(step_id) or {}
        eqp = list(f.get("eqp_id") or [])
        ppids = list(f.get("ppid") or [])
        alerts.setdefault(vehicle, []).append({
            "id": f"um|{vehicle}|{step_id}", "type": "unmatched_step",
            "vehicle": vehicle, "product": product,
            "step_id": step_id, "step_desc": step_desc,
            "ppid": ", ".join(ppids[:3]), "split": "",
            "eqp_id": ", ".join(eqp[:3]), "eqp_model": EQP_MODEL.get(product, ""),
            "area": area,
            "rows": int(f.get("rows") or 0), "n_lots": int(f.get("n_lots") or 0),
            "examples": [{"root_lot_id": e["root_lot_id"], "wafer_id": e["wafer_id"]}
                         for e in (f.get("examples") or [])],
            "match_hint": _match_hint(vehicle, product, step_id, db_root, matched_rows),
            "status": ack or "active", "ack_note": "",
            "first_seen_ts": now - days * DAY, "last_seen_ts": now,
        })
    for vehicle, product, step_id, step_desc, area, ack, days, eqp, model in LEGACY_UNMATCHED:
        if (vehicle, step_id) in matched:
            continue
        alerts.setdefault(vehicle, []).append({
            "id": f"um|{vehicle}|{step_id}", "type": "unmatched_step",
            "vehicle": vehicle, "product": product,
            "step_id": step_id, "step_desc": step_desc, "ppid": "", "split": "",
            "eqp_id": eqp, "eqp_model": model, "area": area,
            "rows": 579, "n_lots": 128,
            "examples": [{"root_lot_id": "R176", "wafer_id": "1"},
                         {"root_lot_id": "R018", "wafer_id": "1"}],
            "status": ack or "active", "ack_note": "",
            "first_seen_ts": now - days * DAY, "last_seen_ts": now,
        })
    for vehicle, product, step_id, step_desc, ppid, ack, days in RO_PPID:
        alerts.setdefault(vehicle, []).append({
            "id": f"ro|{vehicle}|{step_id}|{ppid}", "type": "ro_ppid",
            "vehicle": vehicle, "product": product,
            "step_id": step_id, "step_desc": step_desc, "ppid": ppid,
            "split": "2026-07-08~2026-07-09, 2026-07-09~2026-07-10",
            "eqp_id": "", "eqp_model": "", "area": "",
            "rows": 35, "n_lots": 12,
            "status": ack or "active", "ack_note": "",
            "first_seen_ts": now - days * DAY, "last_seen_ts": now,
        })

    payloads = {}
    for vehicle, rows in alerts.items():
        for a in rows:
            note = ACK_NOTES.get(a["id"])
            if note:
                a["ack_note"] = note[1]
        active = [a for a in rows if a["status"] not in ("미확인예정", "반영불필요")]
        payloads[vehicle] = {
            "vehicle": vehicle, "ts": now, "count": len(active),
            "suppressed": len(rows) - len(active),
            "alert_cols": ALERT_COLS,
            "fp": f"example-{vehicle.lower()}",
            "delta": {"new": sorted(a["id"] for a in active
                                    if now - a["first_seen_ts"] < 5 * DAY),
                      "resolved": []},
            "alerts": rows,
        }
    return payloads


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="실제로 파일을 쓴다")
    ap.add_argument("--force", action="store_true", help="기존 파일도 덮어쓴다")
    args = ap.parse_args()

    cfg = va.load_cfg()
    root = va.resolve_root(cfg.get("local_root") or "")
    if not root:
        print("local_root 가 비어 있습니다 (S3 모드). data/flow-data/valve_alerts.json "
              "의 local_root 를 `{db_root}` 로 설정한 뒤 다시 실행하세요.")
        return 2
    out_dir = Path(root) / cfg["alerts_prefix"].strip("/") / "pipeline"
    db_root = Path(PATHS.db_root)
    print(f"DB root      : {db_root}")
    print(f"알람 폴더    : {out_dir}")

    payloads = build_payloads(db_root, time.time())
    ack = {aid: {"status": st, "note": note, "by": "hol", "ts": time.time() - 3 * DAY}
           for aid, (st, note) in ACK_NOTES.items()}

    files = {f"{v}.json": p for v, p in payloads.items()}
    files["ack.json"] = ack
    for name, obj in files.items():
        fp = out_dir / name
        n = len(obj.get("alerts", [])) if name != "ack.json" else len(obj)
        mark = "덮어씀" if fp.exists() else "새로 씀"
        if fp.exists() and not args.force:
            print(f"  = {name:16s} 이미 있음 — 건너뜀 (--force 로 덮어쓰기), {n}건")
            continue
        if not args.write:
            print(f"  · {name:16s} {n}건 ({mark} 예정)")
            continue
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  + {name:16s} {n}건 {mark}")
    if not args.write:
        print("\n미리보기입니다. 실제로 만들려면 --write 를 붙이세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
