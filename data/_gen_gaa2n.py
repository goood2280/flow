#!/usr/bin/env python3
"""Generate 2nm GAA Nanosheet dummy data package for FabCanvas demo.

Pure stdlib for the raw generation phase (random, csv, json, datetime, math, os).
The post-processing phase (`_restructure.py`) requires polars + pyarrow.

Re-runnable: overwrites outputs under data/DB/ and data/Base/ .

Two-phase pipeline:
  Phase A (this file)  — emit flat CSVs in legacy paths
                          (data/DB/<table>.csv, data/DB/matching/*.csv)
  Phase B (_restructure.py) — split by product into Hive-flat layout under
                          data/DB/<MODULE>/<table>/product=<P>/part-0.csv,
                          move matching/ → data/Base/, build dvc_rulebook.csv
                          and ML wide-form parquets, write _uniques.json,
                          delete the legacy flat files.

Final layout (after both phases):
  data/DB/FAB/fab_history/product=<P>/part-0.csv
  data/DB/INLINE/inline_meas/product=<P>/part-0.csv
  data/DB/ET/et_wafer/product=<P>/part-0.csv
  data/DB/EDS/eds_die/product=<P>/part-0.csv
  data/DB/LOTS/lots/part-0.csv
  data/DB/wafer_maps/*.json
  data/Base/<matching CSVs>
  data/Base/dvc_rulebook.csv
  data/Base/_uniques.json

Schema highlights (v2):
  inline_meas  : lot_id, root_lot_id, wafer_id, step_id, tkin_time, tkout_time,
                 item_id, subitem_id, value
                 → shot_x/shot_y 는 Base/inline_subitem_pos.csv 매칭 테이블에서 조회
  et_wafer     : lot_id, root_lot_id, wafer_id, pgm, eqp, chamber,
                 shot_x, shot_y, item_id, value   (long/column 형식)
  eds_die      : lot_id, wafer_id, die_x, die_y, bin_id, value  (0/1)
  inline_subitem_pos : item_id, subitem_id, shot_x, shot_y      (map_id 폐기)
  inline_item_map    : product, step_id, item_id, canonical_item (map_id 폐기)

Disclaimer: Synthetic values only. NOT actual process data. Academic-reference shaped.
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20260419
random.seed(SEED)

ROOT = Path(__file__).resolve().parent  # data/
DB = ROOT / "DB"
MATCHING = DB / "matching"
WMAPS = DB / "wafer_maps"
for d in (DB, MATCHING, WMAPS):
    d.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────
# Process-flow definition (academic-reference shaped, 2nm GAA Nanosheet)
# ─────────────────────────────────────────────────────────────────────
MAIN_STEPS = [
    # STI
    ("1.0",  "STI_PAD_OX_GROW",       "STI"),
    ("1.1",  "STI_PAD_NITRIDE_DEP",   "STI"),
    ("1.2",  "STI_ACTIVE_PHOTO",      "STI"),
    ("1.3",  "STI_TRENCH_ETCH",       "STI"),
    ("1.4",  "STI_LINER_OX",          "STI"),
    ("1.5",  "STI_OX_FILL",           "STI"),
    ("1.6",  "STI_CMP",               "STI"),
    # Well / VT
    ("2.0",  "WELL_NWELL_PHOTO",      "Well/VT"),
    ("2.1",  "WELL_NWELL_IMPLANT",    "Well/VT"),
    ("2.2",  "WELL_PWELL_PHOTO",      "Well/VT"),
    ("2.3",  "WELL_PWELL_IMPLANT",    "Well/VT"),
    ("2.4",  "VTH_N_IMPLANT",         "Well/VT"),
    ("2.5",  "VTH_P_IMPLANT",         "Well/VT"),
    ("2.6",  "WELL_IMPLANT_ANNEAL",   "Well/VT"),
    # Nanosheet epi + fin/nanosheet patterning (PC module)
    ("3.0",  "PC_NS_EPI_SIGE_SI_STACK","PC"),
    ("3.1",  "PC_FIN_HARDMASK_DEP",   "PC"),
    ("3.2",  "PC_FIN_PHOTO",          "PC"),
    ("3.3",  "PC_NANOSHEET_PATTERN_ETCH","PC"),
    # Dummy gate
    ("4.0",  "DUMMY_GATE_POLY_DEP",   "PC"),
    ("4.1",  "DUMMY_GATE_HARDMASK",   "PC"),
    ("4.2",  "DUMMY_GATE_PHOTO",      "PC"),
    ("4.3",  "DUMMY_GATE_ETCH",       "PC"),
    # Spacer
    ("5.0",  "SPACER_LOWK_DEP",       "Spacer"),
    ("5.1",  "SPACER_ANISO_ETCH",     "Spacer"),
    ("5.2",  "INNER_SPACER_SIGE_ETCH","Spacer"),
    ("5.3",  "INNER_SPACER_FILL",     "Spacer"),
    # S/D Epi
    ("6.0",  "SD_EPI_RECESS_ETCH",    "S/D Epi"),
    ("6.1",  "SD_EPI_NMOS_SIP",       "S/D Epi"),
    ("6.2",  "SD_EPI_PMOS_SIGEB",     "S/D Epi"),
    ("6.3",  "SD_EPI_ANNEAL",         "S/D Epi"),
    # ILD + replacement-gate + HKMG
    ("7.0",  "MOL_ILD0_DEP",          "MOL"),
    ("7.1",  "MOL_ILD0_CMP",          "MOL"),
    ("7.2",  "GATE_DUMMY_PULL",       "Gate"),
    ("7.3",  "GATE_CHANNEL_RELEASE_SIGE","Gate"),
    ("7.4",  "HKMG_HFO2_ALD",         "Gate"),
    ("7.5",  "HKMG_WF_METAL_DEP",     "Gate"),
    ("7.6",  "HKMG_W_FILL",           "Gate"),
    ("7.7",  "HKMG_CMP",              "Gate"),
    # MOL
    ("8.0",  "CT_PHOTO",              "MOL"),
    ("8.1",  "CT_ETCH",               "MOL"),
    ("8.2",  "CT_SILICIDE_TI_NI",     "MOL"),
    ("8.3",  "CT_W_FILL",             "MOL"),
    ("8.4",  "CT_CMP",                "MOL"),
    ("8.5",  "MOL_V0_PHOTO",          "MOL"),
    ("8.6",  "MOL_V0_ETCH_FILL",      "MOL"),
    # BEOL M1..M6
    ("9.1",  "M1_LOWK_ILD_DEP",       "BEOL-M1"),
    ("9.2",  "M1_DUAL_DAMASCENE_ETCH","BEOL-M1"),
    ("9.3",  "M1_CU_FILL_CMP",        "BEOL-M1"),
    ("10.1", "M2_LOWK_ILD_DEP",       "BEOL-M2"),
    ("10.2", "M2_DUAL_DAMASCENE_ETCH","BEOL-M2"),
    ("10.3", "M2_CU_FILL_CMP",        "BEOL-M2"),
    ("11.1", "M3_LOWK_ILD_DEP",       "BEOL-M3"),
    ("11.2", "M3_DUAL_DAMASCENE_ETCH","BEOL-M3"),
    ("11.3", "M3_CU_FILL_CMP",        "BEOL-M3"),
    ("12.1", "M4_CU_DAMASCENE",       "BEOL-M4"),
    ("13.1", "M5_CU_DAMASCENE",       "BEOL-M5"),
    ("14.1", "M6_CU_DAMASCENE",       "BEOL-M6"),
]

# INLINE meas steps
MEAS_STEPS = [
    # (meas_number, func_name, area, item_id, measurement_kind)
    ("1.6M",  "STI_CMP_THK_MEAS",         "STI",     "THK_STI_OX",    "THK"),
    ("3.0M",  "PC_NS_EPI_THK_MEAS",       "PC",      "THK_NS_STACK",  "THK"),
    ("3.3M",  "PC_FIN_CD_MEAS",           "PC",      "CD_FIN",        "CD"),
    ("4.3M",  "PC_DUMMY_GATE_CD_OCD",     "PC",      "CD_DGATE",      "OCD"),
    ("5.1M",  "SPACER_THK_MEAS",          "Spacer",  "THK_SPACER",    "THK"),
    ("5.3M",  "INNER_SPACER_OCD",         "Spacer",  "OCD_INSPCR",    "OCD"),
    ("6.2M",  "SD_EPI_OCD",               "S/D Epi", "OCD_SDEPI",     "OCD"),
    ("7.3M",  "GATE_CHANNEL_RELEASE_OCD", "Gate",    "OCD_NS_REL",    "OCD"),
    ("7.7M",  "HKMG_CMP_THK",             "Gate",    "THK_HKMG",      "THK"),
    ("8.1M",  "CT_CD_OCD",                "MOL",     "CD_CT",         "OCD"),
    ("8.1M2", "CT_OVL_GATE",              "MOL",     "OVL_CT_GATE",   "OVL"),
    ("8.4M",  "CT_RS_MEAS",               "MOL",     "RS_CT",         "RS"),
    ("9.2M",  "M1_CD_OCD",                "BEOL-M1", "CD_M1",         "OCD"),
    ("9.3M",  "M1_THK_MEAS",              "BEOL-M1", "THK_M1",        "THK"),
    ("10.2M", "M2_OVL_M1",                "BEOL-M2", "OVL_M2_M1",     "OVL"),
    ("11.2M", "M3_CD_OCD",                "BEOL-M3", "CD_M3",         "OCD"),
    ("12.1M", "M4_OVL_M3",                "BEOL-M4", "OVL_M4_M3",     "OVL"),
    ("13.1M", "M5_THK_MEAS",              "BEOL-M5", "THK_M5",        "THK"),
]

PRODUCTS = ["PRODUCT_A0", "PRODUCT_A1", "PRODUCT_B"]
PROD_PREFIX = {"PRODUCT_A0": "AA", "PRODUCT_A1": "AC", "PRODUCT_B": "AB"}

PHOTO_CANONICAL_STEPS = [
    "1.2 ACTIVE_PHOTO", "2.0 NWELL_PHOTO", "2.2 PWELL_PHOTO", "3.2 FIN_PHOTO",
    "4.2 DUMMY_GATE_PHOTO", "8.0 CT_PHOTO", "8.5 V0_PHOTO",
    "9.2 M1_DUAL_DAMASCENE_ETCH", "10.2 M2_DUAL_DAMASCENE_ETCH",
    "11.2 M3_DUAL_DAMASCENE_ETCH", "12.1 M4_CU_DAMASCENE",
    "13.1 M5_CU_DAMASCENE", "14.1 M6_CU_DAMASCENE",
]

# ET test sites: 5 standard positions per wafer (center + 4 edges)
ET_SITES = [(0, 0), (0, 3), (0, -3), (-3, 0), (3, 0)]

# ET parameters
DVC_PARAMS = ["Rc", "Rch", "ACint", "AChw", "Vth_n", "Vth_p",
              "Ion_n", "Ion_p", "Ioff_n", "Ioff_p", "lkg"]

# EDS bins: BIN_01=PASS, BIN_02=Vth_fail, BIN_03=Ion_fail, BIN_04=Edge_fail
EDS_BINS = ["BIN_01", "BIN_02", "BIN_03", "BIN_04"]

# ─────────────────────────────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────────────────────────────
def raw_step_id(product: str, seq: int) -> str:
    return f"{PROD_PREFIX[product]}{100000 + seq * 10:06d}"

def canonical_str(num: str, name: str) -> str:
    return f"{num} {name}"

def write_csv(path: Path, header: list, rows: list):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        w.writerows(rows)

def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S")

def split_eqp_chamber(eqp_chamber: str):
    """'E12_CH1' → ('E12', 'CH1')"""
    parts = eqp_chamber.split("_", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (eqp_chamber, "CH1")

# ─────────────────────────────────────────────────────────────────────
# (A) matching/ tables
# ─────────────────────────────────────────────────────────────────────
def gen_matching_step():
    rows = []
    for prod in PRODUCTS:
        seq = 1
        meas_by_host = {}
        for meas in MEAS_STEPS:
            h = meas[0].replace("M2", "").rstrip("M")
            meas_by_host.setdefault(h, []).append(meas)

        for m_num, m_name, m_area in MAIN_STEPS:
            rsid = raw_step_id(prod, seq); seq += 1
            rows.append([prod, rsid, canonical_str(m_num, m_name), "main", m_area])
            for ms in meas_by_host.get(m_num, []):
                rsid_m = raw_step_id(prod, seq); seq += 1
                rows.append([prod, rsid_m, canonical_str(ms[0], ms[1]), "meas", ms[2]])
    write_csv(MATCHING / "matching_step.csv",
              ["product","raw_step_id","canonical_step","step_type","area"], rows)
    return rows

def gen_knob_ppid():
    knob_defs = [
        ("GATE_PROFILE",          ["STD", "TILT_2DEG", "STD", "STD", "STD", "STD"]),
        ("SD_EPI_BORON",          ["NOM", "NOM", "HIGH_1E20", "NOM", "LOW_8E19", "NOM"]),
        ("CHANNEL_RELEASE_TIME",  ["60s", "60s", "60s", "72s", "60s", "54s"]),
        ("HKMG_WF_METAL",         ["TiN_3nm", "TiN_3nm", "TiN_3nm", "TiN_3nm", "TiAlC_2nm", "TiN_3.5nm"]),
        ("SPACER_LOWK_K",         ["k3.9", "k3.9", "k3.9", "k3.9", "k3.9", "k3.5"]),
        ("M1_CU_RECIPE",          ["R1", "R1", "R1", "R1", "R1", "R2"]),
    ]
    rows = []
    for prod in PRODUCTS:
        for i in range(6):
            ppid = f"PP_{prod}_{i+1:02d}"
            for k, vals in knob_defs:
                rows.append([prod, ppid, k, vals[i]])
    write_csv(MATCHING / "knob_ppid.csv",
              ["product","ppid","knob_name","knob_value"], rows)
    return rows

def gen_mask():
    vendors = ["MaskVendorX", "MaskVendorY", "MaskVendorZ"]
    rows = []
    for prod in PRODUCTS:
        for i, pstep in enumerate(PHOTO_CANONICAL_STEPS, start=1):
            reticle = f"R{PROD_PREFIX[prod]}{i:03d}"
            rows.append([prod, reticle, f"v{1 + i%3}.{i%5}",
                         vendors[i % 3], pstep])
    write_csv(MATCHING / "mask.csv",
              ["product","reticle_id","mask_version","mask_vendor","photo_step"], rows)
    return rows

def gen_inline_step_match(matching_rows):
    rows = []
    for r in matching_rows:
        prod, rsid, canonical, stype, area = r
        if stype == "meas":
            rows.append([prod, rsid, canonical])
    write_csv(MATCHING / "inline_step_match.csv",
              ["product","raw_step_id","canonical_step"], rows)
    return rows

def gen_inline_item_map(matching_rows):
    """step_id → item_id 매핑. map_id 폐기."""
    meas_meta = {ms[0]: ms for ms in MEAS_STEPS}
    rows = []
    for r in matching_rows:
        prod, rsid, canonical, stype, area = r
        if stype != "meas":
            continue
        num = canonical.split(" ", 1)[0]
        ms = meas_meta.get(num)
        if not ms:
            continue
        item_id = ms[3]
        rows.append([prod, rsid, item_id, item_id])
    write_csv(MATCHING / "inline_item_map.csv",
              ["product", "step_id", "item_id", "canonical_item"], rows)
    return rows

def gen_inline_subitem_pos(item_rows):
    """item_id × subitem_id → shot_x, shot_y 매핑 테이블.
    map_id 폐기, item_id 기준으로 shot 위치 정의.
    CD/OCD critical 항목은 5×5, 나머지는 3×3.
    """
    big_kinds = ("CD_DGATE","CD_CT","OCD_NS_REL","OCD_SDEPI","CD_M1","CD_M3")
    seen = set()
    rows = []
    for r in item_rows:
        prod, step_id, item_id, canon = r
        if item_id in seen:
            continue
        seen.add(item_id)
        grid = 5 if item_id in big_kinds else 3
        half = grid // 2
        sid = 1
        for yy in range(-half, half+1):
            for xx in range(-half, half+1):
                rows.append([item_id, f"S{sid:02d}", xx, yy])
                sid += 1
    write_csv(MATCHING / "inline_subitem_pos.csv",
              ["item_id", "subitem_id", "shot_x", "shot_y"], rows)
    return rows

def gen_yld_shot_agg():
    rows = []
    for prod in PRODUCTS:
        rows.append([prod, "subitem_id", "majority_bin"])
    write_csv(MATCHING / "yld_shot_agg.csv",
              ["product","shot_group_cols","agg_method"], rows)
    return rows

# ─────────────────────────────────────────────────────────────────────
# (B) source data
# ─────────────────────────────────────────────────────────────────────
START_DATE = datetime(2026, 1, 1, 0, 0, 0)
END_DATE   = datetime(2026, 4, 15, 23, 59, 59)

def gen_lots():
    """30 lots, split across 3 products.
    일부 lot은 동일 root_lot_id를 공유 (split lot 시나리오).
    """
    rows = []
    lot_specs = []
    span_days = (END_DATE - START_DATE).days
    ppids_by_prod = {p: [f"PP_{p}_{i+1:02d}" for i in range(6)] for p in PRODUCTS}
    a_variants = [PRODUCTS[0], PRODUCTS[1]]
    prod_b = PRODUCTS[2]
    lot_ids = []

    for i in range(30):
        if i % 2 == 0:
            prod = a_variants[(i // 2) % 2]
        else:
            prod = prod_b
        ppid = ppids_by_prod[prod][i // 2 % 6]
        lot_id = f"LOT{i+1:03d}{PROD_PREFIX[prod]}"
        lot_ids.append(lot_id)
        start = START_DATE + timedelta(days=(i * span_days / 30),
                                       hours=random.randint(0, 23))
        end = start + timedelta(hours=random.randint(55, 75))
        knob = f"KNOB_{ppid[-2:]}"
        lot_specs.append({"lot_id": lot_id, "product": prod, "ppid": ppid,
                          "start": start, "end": end, "knob": knob})

    # root_lot_id: 매 6번째 인덱스 쌍을 split lot으로 연결 (5쌍)
    root_map = {lid: lid for lid in lot_ids}
    for i in range(0, 30, 6):
        if i + 1 < 30:
            root_map[lot_ids[i + 1]] = lot_ids[i]

    for spec in lot_specs:
        spec["root_lot_id"] = root_map[spec["lot_id"]]
        rows.append([spec["lot_id"], spec["root_lot_id"], spec["product"],
                     spec["ppid"], iso(spec["start"]), iso(spec["end"]), spec["knob"]])

    write_csv(DB / "lots.csv",
              ["lot_id","root_lot_id","product","ppid","start_ts","end_ts","knob"],
              rows)
    return lot_specs

# Equipment/chamber pool per area
EQP_POOL = {
    "STI":      ["E01_CH1","E01_CH2","E02_CH1"],
    "Well/VT":  ["E03_CH1","E03_CH2"],
    "PC":       ["E04_CH1","E04_CH2","E05_CH1"],
    "Gate":     ["E11_CH1","E11_CH2","E12_CH1","E12_CH2"],
    "Spacer":   ["E06_CH1","E06_CH2"],
    "S/D Epi":  ["E07_CH1","E07_CH2","E08_CH1"],
    "MOL":      ["E21_CH1","E21_CH2","E22_CH1"],
    "BEOL-M1":  ["E31_CH1","E31_CH2"],
    "BEOL-M2":  ["E32_CH1","E32_CH2"],
    "BEOL-M3":  ["E33_CH1","E33_CH2"],
    "BEOL-M4":  ["E34_CH1"],
    "BEOL-M5":  ["E35_CH1"],
    "BEOL-M6":  ["E36_CH1"],
}

def gen_fab_history(lot_specs, matching_rows):
    per_prod_main = {p: [] for p in PRODUCTS}
    for r in matching_rows:
        prod, rsid, canonical, stype, area = r
        if stype == "main":
            per_prod_main[prod].append((rsid, canonical, area))

    sampled_lots = lot_specs[:10]
    rows = []
    for spec in sampled_lots:
        prod = spec["product"]
        main = per_prod_main[prod]
        dur_per_step = (spec["end"] - spec["start"]) / max(len(main), 1)
        for wf in range(1, 26):
            for i, (rsid, canonical, area) in enumerate(main):
                in_ts = spec["start"] + dur_per_step * i + timedelta(
                    minutes=random.randint(0, 4))
                out_ts = in_ts + timedelta(minutes=random.randint(15, 90))
                eqp = random.choice(EQP_POOL.get(area, ["E99_CH1"]))
                rows.append([spec["lot_id"], f"W{wf:02d}", rsid, eqp,
                             iso(in_ts), iso(out_ts), spec["ppid"]])
    write_csv(DB / "fab_history.csv",
              ["lot_id","wafer_id","raw_step_id","eqp_chamber",
               "in_ts","out_ts","ppid"],
              rows)
    return rows

def gen_inline_meas(lot_specs, matching_rows, item_rows, pos_rows):
    """INLINE 측정 데이터.

    Schema: lot_id, root_lot_id, wafer_id, step_id, tkin_time, tkout_time,
            item_id, subitem_id, value
    - shot_x/shot_y 는 Base/inline_subitem_pos.csv 에서 (item_id, subitem_id) 키로 조회
    - tkin_time/tkout_time : 측정 시작/종료 (FAB history와 lot 시간 기준 정렬)
    """
    # meas step index per product
    meas_by_prod = {p: [] for p in PRODUCTS}
    meas_meta = {ms[0]: ms for ms in MEAS_STEPS}
    # step 순서 인덱스 (시간 비례 배분용)
    meas_seq_by_prod = {p: [] for p in PRODUCTS}
    main_count = {p: sum(1 for r in matching_rows if r[0] == p and r[3] == "main")
                  for p in PRODUCTS}
    step_idx = {p: 0 for p in PRODUCTS}

    for r in matching_rows:
        prod, rsid, canonical, stype, area = r
        if stype == "main":
            step_idx[prod] += 1
        elif stype == "meas":
            num = canonical.split(" ", 1)[0]
            ms = meas_meta.get(num)
            if ms:
                meas_by_prod[prod].append((rsid, canonical, ms[2], ms[3], ms[4]))
                meas_seq_by_prod[prod].append(step_idx[prod])  # 직전 main step index

    # subitem_id 목록 per item_id
    subs_by_item = {}
    for r in pos_rows:
        item_id, sub_id, sx, sy = r
        subs_by_item.setdefault(item_id, []).append(sub_id)

    # item_rows: (product, step_id, item_id, canonical_item)
    item_index = {}
    for r in item_rows:
        prod, step_id, item_id, canon = r
        item_index[(prod, step_id)] = item_id

    sampled_lots = lot_specs[:10]
    rows = []
    target_by_kind = {
        "CD":  (30.0, 1.2),
        "OCD": (14.0, 0.8),
        "THK": (50.0, 2.5),
        "OVL": (0.0, 1.8),
        "RS":  (250.0, 15.0),
    }
    for spec in sampled_lots:
        prod = spec["product"]
        meas_list = meas_by_prod[prod]
        n_main = max(main_count[prod], 1)
        lot_dur = spec["end"] - spec["start"]

        for wf in range(1, 26, 6):  # wafers 1,7,13,19,25
            for idx, (rsid, canonical, area, item_id, kind) in enumerate(meas_list):
                seq_frac = meas_seq_by_prod[prod][idx] / n_main
                # 측정 시작 = lot_start + 공정 진행 비율 × lot 기간 + 소량 랜덤
                tkin = spec["start"] + timedelta(
                    seconds=int(lot_dur.total_seconds() * seq_frac)
                    + random.randint(0, 900))
                tkout = tkin + timedelta(minutes=random.randint(20, 90))

                subs = subs_by_item.get(item_id, ["S01"])
                mean, sd = target_by_kind.get(kind, (100.0, 5.0))

                for sub in subs:
                    # subitem 위치에 따른 edge drift는 매칭 테이블에서 계산
                    v = random.gauss(mean, sd)
                    if random.random() < 0.01:
                        v += random.choice([-1, 1]) * 4 * sd
                    rows.append([
                        spec["lot_id"], spec["root_lot_id"],
                        f"W{wf:02d}", rsid,
                        iso(tkin), iso(tkout),
                        item_id, sub, round(v, 4)
                    ])

    write_csv(DB / "inline_meas.csv",
              ["lot_id","root_lot_id","wafer_id","step_id",
               "tkin_time","tkout_time","item_id","subitem_id","value"],
              rows)
    return rows

def gen_et_wafer(lot_specs):
    """ET 측정 데이터 — long/column 형식.

    Schema: lot_id, root_lot_id, wafer_id, pgm, eqp, chamber,
            shot_x, shot_y, item_id, value
    - 5 test sites per wafer × 11 DVC params = 55 rows per wafer
    - eqp_chamber ('E12_CH1') → eqp='E12', chamber='CH1' 로 분리
    """
    base = {
        "Rc":    150.0,
        "Rch":   200.0,
        "ACint": 80.0,
        "AChw":  1.0,
        "Vth_n": 0.30,
        "Vth_p": -0.30,
        "Ion_n": 900.0,
        "Ion_p": 800.0,
        "Ioff_n": 5e-9,
        "Ioff_p": 5e-9,
        "lkg":    1e-8,
    }

    def knob_shift(ppid: str, param: str) -> float:
        tag = ppid[-2:]
        m = {
            "01": {},
            "02": {"Vth_n": -0.02, "Vth_p":  0.02, "Ion_n":  25},
            "03": {"Ion_p": 40, "Ioff_p": 1e-9, "Rc": 2.0},
            "04": {"Rc":   4.0, "Rch": 5.0, "Ion_n": -15},
            "05": {"Vth_p": -0.04, "Ion_p": -30, "lkg": 2e-9},
            "06": {"ACint": -6.0, "AChw": -0.05},
        }
        return m.get(tag, {}).get(param, 0.0)

    rows = []
    for spec in lot_specs:
        prod = spec["product"]
        ppid = spec["ppid"]
        eqp_chamber_str = random.choice(EQP_POOL["Gate"])
        eqp, chamber = split_eqp_chamber(eqp_chamber_str)

        for wf in range(1, 26):
            for sx, sy in ET_SITES:
                # site-level spatial variation (edge drift for some params)
                r2 = sx * sx + sy * sy
                for param, b in base.items():
                    # knob shift
                    shift = knob_shift(ppid, param)
                    # spatial variation: edge sites drift slightly
                    if param in ("Rc", "Rch", "ACint"):
                        site_drift = 0.01 * r2 * b
                    elif param in ("Vth_n", "Vth_p"):
                        site_drift = 0.002 * r2
                    else:
                        site_drift = 0.0

                    if param.startswith("Ioff") or param == "lkg":
                        noise = random.lognormvariate(0, 0.15) - 1
                        v = b * (1 + noise) + shift
                    elif param in ("Vth_n", "Vth_p", "AChw"):
                        v = b + random.gauss(0, 0.012) + shift + site_drift
                    else:
                        v = b + random.gauss(0, b * 0.03) + shift + site_drift

                    if random.random() < 0.03:
                        v += random.choice([-1, 1]) * b * 0.15

                    # 정밀도 포맷
                    if param.startswith("Ioff") or param == "lkg":
                        val_str = f"{v:.3e}"
                    elif param in ("Vth_n", "Vth_p", "AChw"):
                        val_str = str(round(v, 4))
                    else:
                        val_str = str(round(v, 3))

                    rows.append([
                        spec["lot_id"], spec["root_lot_id"],
                        f"W{wf:02d}", ppid, eqp, chamber,
                        sx, sy, param, val_str
                    ])

    write_csv(DB / "et_wafer.csv",
              ["lot_id","root_lot_id","wafer_id","pgm","eqp","chamber",
               "shot_x","shot_y","item_id","value"],
              rows)
    return rows

def gen_eds_die(lot_specs):
    """Die-level EDS — bin 기반 long 형식.

    Schema: lot_id, wafer_id, die_x, die_y, bin_id, value
    - 4 bins: BIN_01=PASS, BIN_02=Vth_fail, BIN_03=Ion_fail, BIN_04=Edge_fail
    - 각 die는 4개 row, 해당 bin만 value=1 나머지 0
    """
    rows = []
    sampled = lot_specs[:5]
    for spec in sampled:
        for wf in range(1, 26):
            for dx in range(-5, 5):
                for dy in range(-5, 5):
                    r2 = dx * dx + dy * dy
                    # 실패 확률 패턴
                    p_vth  = 0.02 + 0.005 * r2 / 25.0
                    p_ion  = 0.015 + 0.004 * r2 / 25.0
                    p_edge = 0.01 + 0.020 * r2 / 25.0  # 엣지 집중

                    if random.random() < p_edge:
                        assigned = "BIN_04"
                    elif random.random() < p_ion:
                        assigned = "BIN_03"
                    elif random.random() < p_vth:
                        assigned = "BIN_02"
                    else:
                        assigned = "BIN_01"

                    for bin_id in EDS_BINS:
                        rows.append([
                            spec["lot_id"], f"W{wf:02d}",
                            dx, dy, bin_id,
                            1 if bin_id == assigned else 0
                        ])

    write_csv(DB / "eds_die.csv",
              ["lot_id","wafer_id","die_x","die_y","bin_id","value"],
              rows)
    return rows

def gen_wafer_maps(lot_specs):
    """Representative patterns as JSON."""
    patterns = ["center_spot","edge_ring","left_half","right_half",
                "scratch","cluster","random","none"]
    picks = []
    for i, pat in enumerate(patterns):
        spec = lot_specs[i % len(lot_specs)]
        picks.append((spec["lot_id"], f"W{(i % 25) + 1:02d}", pat, spec["product"]))

    def make_grid(pattern: str):
        g = []
        for y in range(-6, 7):
            for x in range(-6, 7):
                r2 = x*x + y*y
                if r2 > 36:
                    continue
                b = 1
                if pattern == "center_spot":
                    b = 0 if r2 <= 4 else (1 if random.random() > 0.02 else 0)
                elif pattern == "edge_ring":
                    b = 0 if 25 <= r2 <= 36 else (1 if random.random() > 0.02 else 0)
                elif pattern == "left_half":
                    b = 0 if x < 0 and random.random() > 0.2 else 1
                elif pattern == "right_half":
                    b = 0 if x > 0 and random.random() > 0.2 else 1
                elif pattern == "scratch":
                    b = 0 if abs(y - 0.5*x) < 0.8 else (1 if random.random() > 0.02 else 0)
                elif pattern == "cluster":
                    cx, cy = 3, -2
                    b = 0 if (x-cx)**2 + (y-cy)**2 <= 4 else (1 if random.random() > 0.02 else 0)
                elif pattern == "random":
                    b = 0 if random.random() < 0.18 else 1
                elif pattern == "none":
                    b = 0 if random.random() < 0.03 else 1
                g.append([x, y, b])
        return g

    files = []
    for lot_id, wf_id, pat, prod in picks:
        obj = {
            "lot_id": lot_id, "wafer_id": wf_id,
            "product": prod, "pattern": pat,
            "grid_radius": 6, "notation": "WM-811K-like binary",
            "die_grid": make_grid(pat),
        }
        p = WMAPS / f"{lot_id}_{wf_id}_{pat}.json"
        with p.open("w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
        files.append(p)
    return files

# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────
def byte_size(p: Path) -> int:
    return p.stat().st_size if p.exists() else 0

def main():
    print("── Generating 2nm GAA Nanosheet dummy data package (v2) ──")
    ms_rows = gen_matching_step()
    knob_rows = gen_knob_ppid()
    mask_rows = gen_mask()
    ism_rows = gen_inline_step_match(ms_rows)
    item_rows = gen_inline_item_map(ms_rows)
    pos_rows = gen_inline_subitem_pos(item_rows)
    yld_rows = gen_yld_shot_agg()

    lot_specs = gen_lots()
    fh_rows = gen_fab_history(lot_specs, ms_rows)
    im_rows = gen_inline_meas(lot_specs, ms_rows, item_rows, pos_rows)
    et_rows = gen_et_wafer(lot_specs)
    eds_rows = gen_eds_die(lot_specs)
    wm_files = gen_wafer_maps(lot_specs)

    paths = {
        "matching/matching_step.csv":     (len(ms_rows),    MATCHING/"matching_step.csv"),
        "matching/knob_ppid.csv":         (len(knob_rows),  MATCHING/"knob_ppid.csv"),
        "matching/mask.csv":              (len(mask_rows),  MATCHING/"mask.csv"),
        "matching/inline_step_match.csv": (len(ism_rows),   MATCHING/"inline_step_match.csv"),
        "matching/inline_item_map.csv":   (len(item_rows),  MATCHING/"inline_item_map.csv"),
        "matching/inline_subitem_pos.csv":(len(pos_rows),   MATCHING/"inline_subitem_pos.csv"),
        "matching/yld_shot_agg.csv":      (len(yld_rows),   MATCHING/"yld_shot_agg.csv"),
        "lots.csv":                       (len(lot_specs),  DB/"lots.csv"),
        "fab_history.csv":                (len(fh_rows),    DB/"fab_history.csv"),
        "inline_meas.csv":                (len(im_rows),    DB/"inline_meas.csv"),
        "et_wafer.csv":                   (len(et_rows),    DB/"et_wafer.csv"),
        "eds_die.csv":                    (len(eds_rows),   DB/"eds_die.csv"),
    }
    print(f"{'file':44s}  {'rows':>8s}  {'bytes':>10s}")
    total = 0
    for name, (rc, p) in paths.items():
        sz = byte_size(p); total += sz
        print(f"{name:44s}  {rc:>8d}  {sz:>10d}")
    for p in wm_files:
        sz = byte_size(p); total += sz
        print(f"wafer_maps/{p.name:<40s}  {'-':>8s}  {sz:>10d}")
    print(f"{'TOTAL':44s}  {'':>8s}  {total:>10d}")


def restructure():
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("_restructure", ROOT / "_restructure.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_restructure"] = mod
    spec.loader.exec_module(mod)
    mod.main()


if __name__ == "__main__":
    main()
    restructure()
