"""core/domain.py v7.1 — Semiconductor domain registry.

Encodes the physical causality hierarchy used across the team's data lake.

Level hierarchy (L0 → L3, strictly one-directional causality):
    L0 = FAB / VM / MASK / KNOB   (process-configuration + virtual-metrology; source of truth for "what was done")
    L1 = INLINE                    (in-line optical/chemical metrology; early post-process measurement)
    L2 = ET                        (electrical test at shot; functional signal)
    L3 = YLD                       (yield / defect at chip; terminal business outcome, built from EDS)

Rules:
  - Features at level K can cause signals at any level ≥ K.
  - Features at level K CANNOT cause signals at level < K (physically impossible).
  - Within the same level, features are correlated but not directly causal — treat as covariates.
  - KNOB and MASK are engineer-classified views on FAB (via knob_pppid.csv / mask.csv rulebooks),
    but they act as independent split axes for DOE analysis and retain L0 rank.

Granularity:
  - wf      — LOT_WF (ROOT_LOT_ID + WAFER_ID) — FAB, VM, MASK, KNOB, ML_TABLE
  - shot    — ET: WF × (shot_x, shot_y); INLINE raw: WF × subitem_id
  - chip    — WF × shot × chip_coordinate     — YLD

ML analysis for PRODA → PRODB transfer should:
  1. Train on PRODA using L0/L1 features to predict L2/L3 target,
  2. Apply to PRODB and compare shift (distribution + importance),
  3. Prefer small feature-count models with high explanatory power (parsimony).
"""
from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────
# Canonical DB registry
# ─────────────────────────────────────────────────────────────────────
# Keys are the CANONICAL (display) names. `physical` is the folder name
# on disk — may differ (e.g. YLD is derived from legacy CP).
DB_REGISTRY: dict = {
    "FAB": {
        "level": "L0", "granularity": "wf",
        "keys": ["ROOT_LOT_ID", "WAFER_ID"],
        "signature_cols": ["EQP", "CHAMBER", "RETICLE_ID", "PPID", "TKOUT_TIME"],
        "description": "Process history — equipment/chamber/reticle/ppid/tkout_time per wafer step. Normally fully populated; some nullable.",
        "icon": "🏭",
    },
    "VM": {
        "level": "L0", "granularity": "wf",
        "keys": ["ROOT_LOT_ID", "WAFER_ID"],
        "signature_cols": ["ITEM_ID", "VALUE"],
        "description": "Virtual Metrology — FAB parameters treated as measurements. Usually mostly populated.",
        "icon": "🧮",
    },
    "MASK": {
        "level": "L0", "granularity": "wf",
        "keys": ["ROOT_LOT_ID", "WAFER_ID"],
        "signature_cols": ["MASK_VERSION", "PHOTO_STEP"],
        "description": "Mask classification — FAB photo-step reticle_id mapped via MASK.csv rulebook (engineer-defined versions).",
        "icon": "🎭",
        "derived_from": "FAB",
    },
    "KNOB": {
        "level": "L0", "granularity": "wf",
        "keys": ["ROOT_LOT_ID", "WAFER_ID"],
        "signature_cols": ["SPLIT_GROUP", "KNOB_NAME"],
        "description": "Split / experiment classification — FAB ppid mapped via knob_pppid.csv rulebook.",
        "icon": "🎛️",
        "derived_from": "FAB",
    },
    "INLINE": {
        "level": "L1", "granularity": "shot",
        "keys": ["ROOT_LOT_ID", "WAFER_ID", "SUBITEM_ID"],
        "signature_cols": ["ITEM_ID", "VALUE"],  # CD, OCD, THK, ...
        "description": "In-line metrology (CD/OCD/THK — optical/chemical). Raw INLINE distinguishes measured points by subitem_id; coordinate mapping is optional.",
        "icon": "📏",
    },
    "ET": {
        "level": "L2", "granularity": "shot",
        "keys": ["ROOT_LOT_ID", "WAFER_ID", "SHOT_X", "SHOT_Y"],
        "signature_cols": ["ITEM_ID", "VALUE"],
        "description": "Electrical test — functional param measurements per shot. Usually many items populated.",
        "icon": "⚡",
    },
    "YLD": {
        "level": "L3", "granularity": "chip",
        "keys": ["ROOT_LOT_ID", "WAFER_ID", "SHOT_X", "SHOT_Y", "CHIP_X", "CHIP_Y"],
        "signature_cols": ["YIELD", "BIN", "DEFECT_CODE"],
        "description": "Yield + bin + defect codes per chip (EDS source). Terminal business outcome.",
        "icon": "🎯",
    },
    "ML_TABLE": {
        "level": "wide", "granularity": "wf",
        "keys": ["ROOT_LOT_ID", "WAFER_ID"],
        "signature_cols": [],
        "description": "Wide feature table — wafer-level join of KNOB/MASK/FAB/VM/INLINE/ET/YLD. Input for ML.",
        "icon": "🧠",
    },
}

# Physical-folder → canonical mapping. If no entry, folder name IS canonical.
# (CP was an early alias for YLD; with v7.1 we generate YLD natively, so CP is hidden.)
PHYSICAL_TO_CANONICAL: dict = {}

# Whitelist — only these canonical DBs appear in File Browser / Dashboard sources.
VISIBLE_CANONICAL = {"FAB", "VM", "MASK", "KNOB", "INLINE", "ET", "YLD", "ML_TABLE"}

# Root-level files to also whitelist (matching rulebook etc.).
VISIBLE_ROOT_FILES = {
    # Rulebooks kept so engineers can audit classifications
    "knob_pppid.csv", "mask.csv",
    # Top-level wide tables (e.g., ML_TABLE_PRODA.parquet)
}
# Regex-style whitelist prefixes for root-level files
VISIBLE_ROOT_PREFIXES = ("ML_TABLE_",)


LEVEL_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "wide": 0}


# ─────────────────────────────────────────────────────────────────────
# v7.3: Matching-table registry (Spotfire calculated-column dependencies)
# ─────────────────────────────────────────────────────────────────────
# Cross-product / cross-DB joins depend on engineer-maintained CSV rulebooks.
# The registry makes them discoverable + version-controlled.
MATCHING_TABLES = {
    "matching_step": {
        "file": "matching_step.csv",
        "description": "Product-specific FAB STEP_ID → canonical functional step (e.g. OX_M1, ETCH_POLY) + process area (GAA Nanosheet module).",
        "applies_to": ["FAB", "VM"],
        "keys": ["product", "raw_step_id"],
        # v8.2.1: area 컬럼 추가 (nullable). STI / Well/VT / PC / Gate / Spacer / S/D Epi / MOL / BEOL-M1~M6.
        "outputs": ["canonical_step", "step_type", "area"],  # step_type ∈ {main, meas}
        "required_cols": ["product", "raw_step_id", "canonical_step", "step_type"],
        "optional_cols": ["area"],
    },
    "knob_ppid": {
        "file": "knob_ppid.csv",
        "description": "FAB PPID → KNOB split classification (experiment label per engineer).",
        "applies_to": ["FAB"],
        "keys": ["product", "ppid"],
        "outputs": ["knob_name", "knob_value"],
        "required_cols": ["product", "ppid", "knob_name", "knob_value"],
    },
    "mask_reticle": {
        "file": "mask.csv",
        "description": "FAB RETICLE_ID → MASK version / vendor.",
        "applies_to": ["FAB"],
        "keys": ["product", "reticle_id"],
        "outputs": ["mask_version", "mask_vendor", "photo_step"],
        "required_cols": ["product", "reticle_id", "mask_version"],
    },
    "inline_step_match": {
        "file": "inline_step_match.csv",
        "description": "INLINE raw STEP_ID → canonical meas step.",
        "applies_to": ["INLINE"],
        "keys": ["product", "raw_step_id"],
        "outputs": ["canonical_step"],
        "required_cols": ["product", "raw_step_id", "canonical_step"],
    },
    "inline_item_map": {
        "file": "inline_item_map.csv",
        "description": "INLINE item_id → canonical item name + item-specific coordinate map_id.",
        "applies_to": ["INLINE"],
        "keys": ["product", "item_id"],
        "outputs": ["canonical_item", "map_id"],
        "required_cols": ["product", "item_id", "canonical_item", "map_id"],
        "optional_cols": ["process_id", "step_id"],
    },
    "inline_subitem_pos": {
        "file": "inline_subitem_pos.csv",
        "description": "INLINE (map_id, subitem_id) → (shot_x, shot_y) in ET coordinate. Lets INLINE join ET.",
        "applies_to": ["INLINE"],
        "keys": ["map_id", "subitem_id"],
        "outputs": ["shot_x", "shot_y"],
        "required_cols": ["map_id", "subitem_id", "shot_x", "shot_y"],
    },
    "yld_shot_agg": {
        "file": "yld_shot_agg.csv",
        "description": "Aggregation policy per product: which chip cols collapse to shot-level, which ET items to match.",
        "applies_to": ["YLD"],
        "keys": ["product"],
        "outputs": ["shot_group_cols", "agg_method"],
        "required_cols": ["product", "shot_group_cols", "agg_method"],
    },
}


def matching_table_path(base: "Path", name: str) -> "Path":
    """Return filesystem path for a matching table. Stored under <db_root>/matching/."""
    from pathlib import Path as _P
    meta = MATCHING_TABLES.get(name)
    if not meta:
        raise ValueError(f"Unknown matching table: {name}")
    return _P(base) / "matching" / meta["file"]


def canonical_name(folder: str) -> str:
    """Map a physical folder name to its canonical DB name."""
    return PHYSICAL_TO_CANONICAL.get(folder, folder)


# v8.4.3: 사이드바 노출 DB 엄격 화이트리스트. 사용자 요구 2026-04-20.
SIDEBAR_VISIBLE = {"FAB", "INLINE", "ET", "QTIME", "VM", "EDS"}


def is_visible_root(folder: str) -> bool:
    """True if this physical folder should appear in File Browser sidebar.

    v8.4.3: 엄격 whitelist. SIDEBAR_VISIBLE 에 있는 것만 노출. LOTS/wafer_maps/
    MASK/KNOB/YLD 등은 backend 로직에 여전히 필요하지만 UI 에는 숨김.
    """
    return folder in SIDEBAR_VISIBLE


def is_visible_file(name: str) -> bool:
    """True if this root-level file should appear. v8.0.1: 모든 parquet/csv 허용."""
    if name.startswith(".") or name.startswith("_"):
        return False
    return True


def _is_visible_file_legacy(name: str) -> bool:
    """True if a root-level data file should appear in File Browser."""
    if name in VISIBLE_ROOT_FILES:
        return True
    return any(name.startswith(p) for p in VISIBLE_ROOT_PREFIXES)


def db_level(canonical: str) -> int:
    """Return integer level 0-3, or -1 if unknown. 'wide' (ML_TABLE) reports 0."""
    meta = DB_REGISTRY.get(canonical)
    if not meta:
        return -1
    return LEVEL_ORDER.get(meta["level"], -1)


def can_cause(upstream_level: int, downstream_level: int) -> bool:
    """L_K can cause L_J iff K <= J (upstream features precede downstream signals)."""
    if upstream_level < 0 or downstream_level < 0:
        return True  # unknown → don't block
    return upstream_level <= downstream_level


# Column-name prefix → canonical DB. Used by ML wide-table parser.
COL_PREFIX_TO_DB = {
    "FAB": "FAB",
    "VM": "VM",
    "MASK": "MASK",
    "KNOB": "KNOB",
    "INLINE": "INLINE",
    "ET": "ET",
    "YLD": "YLD", "YIELD": "YLD", "BIN": "YLD",
    "QTIME": "FAB",  # queue-time is a FAB-level signal
}


# Semantic target-name overrides. Column-name prefix says "family", but some columns
# are actually business-outcome metrics and should target a different level.
# e.g. FAB_YIELD is stored under FAB but is an L3 (YLD) outcome semantically.
TARGET_SEMANTIC_LEVEL = {
    # Yield metrics → L3
    "FAB_YIELD": 3, "YIELD": 3, "YLD_YIELD": 3, "BIN1_RATE": 3, "FAB_BIN1_RATE": 3,
    "DEFECT_DENSITY": 3, "FAB_DEFECT_DENSITY": 3,
    "RESULT": 3, "PASS_RATE": 3, "GOOD_RATE": 3,
}

# Suffix heuristics for target level (scanned after literal match)
TARGET_SUFFIX_LEVEL = [
    ("_YIELD", 3), ("_BIN1", 3), ("_BIN1_RATE", 3), ("_PASS_RATE", 3),
    ("_DEFECT_CNT", 3), ("_DEFECT_DENSITY", 3),
]


def target_level(col: str) -> int:
    """Infer semantic level of a target column (0-3). Used for causality mask.

    Rule: explicit override > suffix heuristic > column prefix level > default L3.
    """
    if col in TARGET_SEMANTIC_LEVEL:
        return TARGET_SEMANTIC_LEVEL[col]
    for suf, lvl in TARGET_SUFFIX_LEVEL:
        if col.endswith(suf):
            return lvl
    c = classify_column(col)
    if c["level"] >= 0:
        return c["level"]
    return 3  # unknown → treat as terminal outcome


def classify_column(col: str) -> dict:
    """Parse ML_TABLE column → {db, level, family, step_major, step_minor}.

    Examples:
      KNOB_RECIPE_1     → db=KNOB   level=0  step_major=-1
      FAB_1.0_THK       → db=FAB    level=0  step_major=1   step_minor=0
      INLINE_MEAS_3     → db=INLINE level=1  step_major=0
      ET_VTH            → db=ET     level=2  step_major=0
      YLD_BIN1          → db=YLD    level=3  step_major=0
      FAB_YIELD         → db=FAB    level=0  (but treated as endpoint target)
    """
    import re
    for prefix, db in COL_PREFIX_TO_DB.items():
        if col.startswith(prefix + "_") or col == prefix:
            # Try to parse numeric step
            m = re.match(rf"^{prefix}_(\d+)(?:[._](\d+))?", col)
            major = int(m.group(1)) if m else 0
            minor = int(m.group(2)) if m and m.group(2) else 0
            # KNOB/MASK are pre-process — sit below step 0
            if db in ("KNOB", "MASK") and not m:
                major = -1
            return {
                "db": db,
                "level": db_level(db),
                "family": db,
                "step_major": major,
                "step_minor": minor,
                "col": col,
            }
    # Unknown — treat as OTHER / endpoint-ish
    return {"db": "OTHER", "level": -1, "family": "OTHER",
            "step_major": 500, "step_minor": 0, "col": col}


# ─────────────────────────────────────────────────────────────────────
# v8.2.1: Process Area tagging — 2nm GAA Nanosheet module taxonomy
# ─────────────────────────────────────────────────────────────────────
# Canonical ordered area list. Kept in sync with
# frontend/src/constants/processAreas.js. `/api/match/area-rollup`
# validates against this set.
PROCESS_AREAS = [
    "STI",
    "Well/VT",
    "PC",
    "Gate",
    "Spacer",
    "S/D Epi",
    "MOL",
    "BEOL-M1", "BEOL-M2", "BEOL-M3", "BEOL-M4", "BEOL-M5", "BEOL-M6",
]


# Ordered (regex, area) pairs — first match wins. Patterns are case-insensitive.
# Token boundary is `(?<![A-Z0-9])` / `(?![A-Z0-9])` (case-insensitive friendly) —
# underscores separate tokens but Python's \b treats `_` as word char, so we
# use an explicit alnum-boundary assertion instead.
_B  = r"(?<![A-Z0-9])"   # left token boundary (start or non-alnum, underscore-friendly)
_BE = r"(?![A-Z0-9])"    # right token boundary
def _tok(p: str) -> str:
    """Wrap a literal token so underscores / start / end delimit it."""
    return _B + p + _BE
_AREA_RULES = [
    # STI — isolation (check before anything else)
    (_tok("STI") + r"|SHALLOW.?TRENCH|ISOL",                       "STI"),
    # Well / VT implant
    (_tok("WELL") + r"|" + _tok("VT") + r"|" + _tok("VTH") + r"|IMPLANT|IMP\b|CHN_IMP",  "Well/VT"),
    # S/D Epi (check before generic EPI matches, before Gate so "SD_EPI" wins)
    (r"(?:S[_/]?D|SOURCE.?DRAIN|SDEPI|RSD)[_ ]?EPI|" + _tok("SDEPI") + r"|" + _tok("RSD"), "S/D Epi"),
    # MOL — contact / via-to-gate (CT / CA / CB / CONTACT / MOL / VIA0)
    (_tok("MOL") + r"|CONTACT|" + _tok("CT") + r"|" + _tok("CA") + r"|" + _tok("CB") + r"|VIA0", "MOL"),
    # BEOL M<n>  (must come before generic PHOTO/ETCH).
    # Accept M1 / METAL_1 / METAL1 / BEOL-M1 / BEOL_M1
    (r"(?:" + _B + r"M(\d+)" + _BE + r"|METAL[_ ]?(\d+)|BEOL[_\- ]?M(\d+))", "__BEOL__"),
    # PC-specific patterns that should beat the Gate rule (dummy gate is a PC-module step)
    (r"DUMMY.?GATE|NS.?RELEASE|NANOSHEET.?REL",                    "PC"),
    # Gate — HKMG (high-K metal gate)
    (_tok("HKMG") + r"|" + _tok("HK") + r"|METAL.?GATE|POLY[_ ]?GATE|" + _tok("GATE"), "Gate"),
    # Spacer
    (_tok("SPACER") + r"|" + _tok("SPCR"),                         "Spacer"),
    # PC — poly / photo-defined patterning / resist / develop
    (_tok("PC") + r"|RESIST|DEVELOP|PHOTO",                        "PC"),
]


def classify_process_area(func_step: str) -> str | None:
    """Heuristic mapping from a canonical/func_step string → process area.

    Returns one of PROCESS_AREAS, or None when no rule matches (caller should
    leave the cell NULL so a human can edit it later via the UI).

    Examples:
        NW_PHOTO            → PC
        GATE_ETCH           → Gate
        SPACER_DEP          → Spacer
        SD_EPI              → S/D Epi
        CT_FILL             → MOL
        METAL_3_CMP         → BEOL-M3
        M1_ETCH             → BEOL-M1
        STI_CMP             → STI
    """
    import re
    if not func_step:
        return None
    s = str(func_step).strip()
    if not s:
        return None
    for pat, area in _AREA_RULES:
        m = re.search(pat, s, flags=re.IGNORECASE)
        if not m:
            continue
        if area == "__BEOL__":
            # Extract metal index from whichever group matched
            idx = next((g for g in m.groups() if g), None)
            try:
                n = int(idx) if idx is not None else None
            except (TypeError, ValueError):
                n = None
            if n is None or n < 1 or n > 6:
                # out-of-range metal — fall through to next rule
                continue
            return f"BEOL-M{n}"
        return area
    return None


def seed_area_rows(rows: list) -> list:
    """Given a list of matching_step rows (dicts), fill `area` when blank using
    classify_process_area(canonical_step or raw_step_id). Returns a new list."""
    out = []
    for r in rows or []:
        r2 = dict(r)
        if not r2.get("area"):
            src = r2.get("canonical_step") or r2.get("raw_step_id") or ""
            a = classify_process_area(src)
            if a:
                r2["area"] = a
        out.append(r2)
    return out
