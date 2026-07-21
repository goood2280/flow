"""v8.8.6 — step_matching.csv / knob_ppid.csv 에 product 컬럼 추가 (PRODA/PRODB).

요구사항: 해당 제품에 맞는 rule/step 만 SplitTable parameter 표시에 붙어야 함.
공용(공통) 행은 product="" 로 유지 → _build_knob_meta 가 빈 product 는 필터 통과.

이 스크립트는 기존 CSV 를 읽어 product 컬럼을 추가해 같은 파일에 저장.
이미 product 컬럼이 있으면 idempotent (변경 없음).
"""
from __future__ import annotations
from pathlib import Path
import csv

HERE = Path(__file__).resolve().parent
FAB = HERE / "Fab"
PRODUCTS = ["PRODA", "PRODB"]


def _ensure_product_col(fp: Path, key_rule=None):
    """fp 를 읽어 product 컬럼이 없으면 각 행을 PRODA/PRODB 로 복제해 저장.
    key_rule: dict {row_key_tuple → [products]} — 특정 행을 특정 제품으로만 매핑할 때.
    없으면 모든 행을 전 제품에 복제.
    """
    if not fp.is_file():
        print(f"[skip] {fp} not found")
        return
    with fp.open("r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        cols = list(rdr.fieldnames or [])
        rows = list(rdr)
    if "product" in cols:
        print(f"[skip] {fp.name}: already has product column")
        return
    new_cols = cols + ["product"]
    new_rows = []
    for r in rows:
        assigned = None
        if key_rule:
            assigned = key_rule(r)
        if assigned is None:
            assigned = PRODUCTS  # 전 제품 복제
        for prod in assigned:
            nr = {**r, "product": prod}
            new_rows.append(nr)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=new_cols)
        w.writeheader()
        w.writerows(new_rows)
    print(f"[ok] {fp.name}: {len(rows)} → {len(new_rows)} rows (product 컬럼 추가)")


def step_key_rule(r):
    # PRODA 전용 step 예시: AB100010/AB100020 (BEOL_M1_*)
    sid = (r.get("step_id") or "").strip()
    if sid in ("AB100010", "AB100020"):
        return ["PRODA"]
    return None  # 전 제품


def knob_key_rule(r):
    # PRODB 는 LITHO 가 다른 recipe 를 쓴다고 가정 — PRODB 전용 rule 은 별도 행으로 추가하면 됨.
    # 여기서는 기본적으로 공용으로 복제만.
    return None


def main():
    _ensure_product_col(FAB / "step_matching.csv", step_key_rule)
    _ensure_product_col(FAB / "knob_ppid.csv", knob_key_rule)
    print("\n── done. 샘플 행 (PRODA) ──")
    with (FAB / "step_matching.csv").open("r", encoding="utf-8-sig") as f:
        lines = f.readlines()
    print("".join(lines[:5]))


if __name__ == "__main__":
    main()
