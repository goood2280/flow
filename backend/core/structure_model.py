"""Small, explicit GAA scene graph with a published nanosheet reference."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from core.paths import PATHS
from core import product_semantics


ROLES = {"substrate": "기판", "source": "소스", "drain": "드레인", "channel": "나노시트 채널",
         "inner_gate": "Inner gate", "gate": "상부 게이트", "spacer": "스페이서",
         "contact": "MOL 콘택트", "mol": "MOL M0", "beol": "BEOL M1/M2", "bitline": "비트라인",
         "rx": "RX 활성 영역", "field": "Field 절연 영역", "sdb": "SDB",
         "nwell": "N-Well", "pwell": "P-Well", "well_tap": "Well/Substrate tap",
         "latch_path": "기생 PNPN 경로"}
STAGES = {"substrate": "FEOL", "source": "FEOL", "drain": "FEOL", "channel": "FEOL",
          "inner_gate": "FEOL", "gate": "FEOL", "spacer": "FEOL", "contact": "MOL",
          "mol": "MOL", "beol": "BEOL", "bitline": "BEOL", "rx": "FEOL", "field": "FEOL", "sdb": "FEOL",
          "nwell": "FEOL", "pwell": "FEOL", "well_tap": "FEOL", "latch_path": "FEOL"}
# Only the nanosheet dimensions are calibrated. Other shapes show topology.
NANOSHEET_NM_PER_UNIT = 40.0
NANOSHEET_THICKNESS_NM = 5.0
NANOSHEET_SOURCE = "https://research.ibm.com/publications/stacked-nanosheet-gate-all-around-transistor-to-enable-scaling-beyond-finfet"
PARAM_LIMITS = {"cell_height": (1.0, 4.0), "gate_width": (0.45, 2.2),
                "sheet_width": (0.5, 2.0), "sheet_spacing": (0.18, 0.65),
                "sheet_count": (1, 5)}
DETAIL_LIMITS = {**{f"ns{i}_{field}_nm": (2.0, 80.0 if field == "width" else 15.0)
                     for i in range(1, 6) for field in ("width", "thickness")},
                 **{f"gate_{level}_cd_nm": (8.0, 90.0) for level in ("top", "middle", "bottom")},
                 "sd_doping_relative": (0.0, 1.0), "sd_doping_log10_cm3": (17.0, 21.0),
                 "mol_level_count": (1, 3),
                 "sdb_width_nm": (2.0, 40.0), "gate_to_sd_gap_nm": (0.0, 30.0),
                 "sd_width_nm": (8.0, 80.0), "sd_protrusion_nm": (0.0, 80.0),
                 "mol_height_nm": (8.0, 100.0), "mol_level_pitch_nm": (8.0, 80.0)}
SHAPABLE_ROLES = {"source", "drain", "gate", "spacer", "contact", "mol", "beol", "sdb"}
SHAPE_CD_LIMITS = (2.0, 120.0)
STRUCTURE_QUERY = re.compile(
    r"gaa|nanosheet|nano.?sheet|ns\s*\d|inner.?gate|gate|source|drain|cell.?height|mol|beol|sdb|latch.?up|well|"
    r"게이트|나노시트|시트|구조|단면|높이|폭|두께|도핑|앵커|inline|step.?id|item.?id|mts|tcd|mcd|bcd",
    re.I)
LANDMARK_LABELS = {"substrate_top": "기판 상면", "ns_stack_bottom": "NS 최하단",
                   "ns_stack_top": "NS 최상단", "gate_top": "게이트 상면",
                   "mol_m0_bottom": "MOL M0 하면", "mol_m0_top": "MOL M0 상면",
                   "beol_m1_bottom": "BEOL M1 하면"}
STRUCTURE_RELATIONS = [
    {"subject": "channel", "predicate": "vertically_stacked", "object": "substrate", "axis": "y"},
    {"subject": "inner_gate", "predicate": "wraps_above_and_below", "object": "channel"},
    {"subject": "gate", "predicate": "connects_to", "object": "inner_gate"},
    {"subject": "source", "predicate": "opposite_x_side_of_gate", "object": "drain"},
    {"subject": "contact", "predicate": "connects_feol_to", "object": "mol"},
    {"subject": "mol", "predicate": "connects_to", "object": "beol"},
    {"subject": "rx", "predicate": "active_region_next_to", "object": "field"},
    {"subject": "sdb", "predicate": "insulates_boundary_of", "object": "rx"},
]
DEFAULT_DIMENSIONS = {
    "ns_stack_height": {"label": "NS 적층 높이", "from": "ns_stack_bottom", "to": "ns_stack_top"},
    "ns_top_to_m0": {"label": "NS Top → MOL M0 하면", "from": "ns_stack_top", "to": "mol_m0_bottom"},
    "gate_top_to_m0": {"label": "Gate Top → MOL M0 하면", "from": "gate_top", "to": "mol_m0_bottom"},
}
DEFAULTS = {
    "logic": {
        "5T": {"cell_height": 1.25, "gate_width": 0.75, "sheet_width": 0.85, "sheet_spacing": 0.28, "sheet_count": 3},
        "6T": {"cell_height": 1.55, "gate_width": 0.9, "sheet_width": 1.0, "sheet_spacing": 0.375, "sheet_count": 3},
        "7.5T": {"cell_height": 1.9, "gate_width": 1.05, "sheet_width": 1.1, "sheet_spacing": 0.36, "sheet_count": 3},
        "9T": {"cell_height": 2.3, "gate_width": 1.2, "sheet_width": 1.2, "sheet_spacing": 0.4, "sheet_count": 4},
    },
    "sram": {
        "HD": {"cell_height": 1.3, "gate_width": 0.7, "sheet_width": 0.75, "sheet_spacing": 0.27, "sheet_count": 2},
        "HC": {"cell_height": 1.75, "gate_width": 0.95, "sheet_width": 0.95, "sheet_spacing": 0.34, "sheet_count": 3},
    },
}


class Conflict(Exception):
    pass


def _path():
    return PATHS.data_root / "knowledge" / "structure_model.sqlite3"


def _empty():
    return {"version": 0, "variants": json.loads(json.dumps(DEFAULTS)), "products": {},
            "dimensions": json.loads(json.dumps(DEFAULT_DIMENSIONS)),
            "shape_profiles": {},
            "updated_at": "", "updated_by": ""}


def read():
    if not _path().exists():
        return _empty()
    with closing(sqlite3.connect(_path())) as db:
        db.execute("CREATE TABLE IF NOT EXISTS revisions (version INTEGER PRIMARY KEY, document TEXT NOT NULL)")
        row = db.execute("SELECT document FROM revisions ORDER BY version DESC LIMIT 1").fetchone()
    doc = json.loads(row[0]) if row else _empty()
    doc.setdefault("dimensions", json.loads(json.dumps(DEFAULT_DIMENSIONS)))
    doc.setdefault("shape_profiles", {})
    # Early local revisions stored one model per product. Keep them readable
    # while moving to one profile per product/type/variant combination.
    for override in doc.get("products", {}).values():
        if "profiles" not in override and "type" in override:
            key = f"{override['type']}/{override['variant']}"
            old = {k: v for k, v in override.items()}
            override.clear()
            override["profiles"] = {key: {"parameters": old.get("parameters", {}),
                                         "anchors": old.get("anchors", {}), "dimension_anchors": {},
                                         "shape_profiles": {}}}
        for profile in override.get("profiles", {}).values():
            profile.setdefault("dimension_anchors", {})
            profile.setdefault("shape_profiles", {})
    return doc


def validate(document):
    if not isinstance(document, dict) or not {"variants", "products"} <= set(document) or set(document) - {"variants", "products", "dimensions", "shape_profiles"}:
        raise ValueError("모델 설정 키가 올바르지 않습니다.")
    variants, products = document["variants"], document["products"]
    dimensions = document.get("dimensions", DEFAULT_DIMENSIONS)
    if not isinstance(dimensions, dict) or len(dimensions) > 60:
        raise ValueError("측정 구간은 최대 60개입니다.")
    for dimension_id, definition in dimensions.items():
        if (not isinstance(dimension_id, str) or not dimension_id or len(dimension_id) > 60
                or not isinstance(definition, dict) or set(definition) != {"label", "from", "to"}
                or not isinstance(definition["label"], str) or not definition["label"].strip()
                or len(definition["label"]) > 100 or not isinstance(definition["from"], str)
                or not isinstance(definition["to"], str) or definition["from"] not in LANDMARK_LABELS
                or definition["to"] not in LANDMARK_LABELS or definition["from"] == definition["to"]):
            raise ValueError(f"{dimension_id}: 측정 구간 정의가 올바르지 않습니다.")
    valid_profiles = {f"{kind}/{variant}" for kind, presets in DEFAULTS.items() for variant in presets}
    shapes = document.get("shape_profiles", {})
    if not isinstance(shapes, dict) or set(shapes) - valid_profiles:
        raise ValueError("공통 구조 프로파일 키가 올바르지 않습니다.")
    for profile_key, role_profiles in shapes.items():
        _shape_profiles(role_profiles, profile_key)
    if not isinstance(variants, dict) or set(variants) != set(DEFAULTS):
        raise ValueError("logic과 sram 타입이 필요합니다.")
    for kind, presets in DEFAULTS.items():
        if not isinstance(variants[kind], dict) or set(variants[kind]) != set(presets):
            raise ValueError(f"{kind} 프리셋 목록이 올바르지 않습니다.")
        for name, params in variants[kind].items():
            _params(params, f"{kind}/{name}", complete=True)
            _sheet_clearance(params, f"{kind}/{name}")
    if not isinstance(products, dict) or len(products) > 500:
        raise ValueError("제품 모델은 최대 500개입니다.")
    for name, override in products.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or not isinstance(override, dict):
            raise ValueError("제품 모델 이름이 올바르지 않습니다.")
        if set(override) != {"profiles"} or not isinstance(override["profiles"], dict) or set(override["profiles"]) - valid_profiles:
            raise ValueError(f"{name}: 제품별 타입/변형 목록이 올바르지 않습니다.")
        for profile_key, profile in override["profiles"].items():
            if not isinstance(profile, dict) or set(profile) - {"parameters", "anchors", "dimension_anchors", "shape_profiles"} or not {"parameters", "anchors"} <= set(profile):
                raise ValueError(f"{name}/{profile_key}: 형상과 앵커가 필요합니다.")
            _params(profile["parameters"], f"{name}/{profile_key}")
            kind, variant = profile_key.split("/")
            _sheet_clearance({**variants[kind][variant], **profile["parameters"]}, f"{name}/{profile_key}")
            _shape_profiles(profile.get("shape_profiles", {}), f"{name}/{profile_key}")
            anchors = profile["anchors"]
            if not isinstance(anchors, dict) or set(anchors) - ROLES.keys():
                raise ValueError(f"{name}/{profile_key}: 구조물 앵커가 올바르지 않습니다.")
            dimension_anchors = profile.get("dimension_anchors", {})
            if not isinstance(dimension_anchors, dict) or set(dimension_anchors) - dimensions.keys():
                raise ValueError(f"{name}/{profile_key}: 측정 구간 앵커가 올바르지 않습니다.")
            for anchor_id, anchor in [*anchors.items(), *dimension_anchors.items()]:
                _anchor(anchor, f"{name}/{profile_key}/{anchor_id}")
    if len(json.dumps(document, ensure_ascii=False)) > 150_000:
        raise ValueError("모델 설정은 150KB 이내여야 합니다.")
    return document


def _params(params, label, complete=False):
    limits = {**PARAM_LIMITS, **DETAIL_LIMITS}
    if not isinstance(params, dict) or (complete and not set(PARAM_LIMITS) <= set(params)) or set(params) - limits.keys():
        raise ValueError(f"{label}: 치수 키가 올바르지 않습니다.")
    for key, value in params.items():
        low, high = limits[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"{label}/{key}: {low}~{high} 범위가 필요합니다.")
        if key in {"sheet_count", "mol_level_count"} and int(value) != value:
            raise ValueError(f"{key}는 정수여야 합니다.")


def _sheet_clearance(params, label):
    """Keep independently sized sheets and upper contact geometry valid."""
    pitch = params["sheet_spacing"] * NANOSHEET_NM_PER_UNIT
    thicknesses = [params.get(f"ns{i}_thickness_nm", NANOSHEET_THICKNESS_NM)
                   for i in range(1, int(params["sheet_count"]) + 1)]
    if any((left + right) / 2 >= pitch for left, right in zip(thicknesses, thicknesses[1:])):
        raise ValueError(f"{label}: NS 층간 간격은 인접 시트 두께의 절반 합보다 커야 합니다.")
    if params.get("sd_protrusion_nm", 22.4) + params.get("mol_height_nm", 24.0) <= 12.8:
        raise ValueError(f"{label}: MOL M0는 게이트 상면보다 높아야 합니다.")


def _shape_profiles(profiles, label):
    if not isinstance(profiles, dict) or set(profiles) - SHAPABLE_ROLES:
        raise ValueError(f"{label}: 구조 프로파일 역할이 올바르지 않습니다.")
    for role, cds in profiles.items():
        if not isinstance(cds, dict) or set(cds) != {"tcd_nm", "mcd_nm", "bcd_nm"}:
            raise ValueError(f"{label}/{role}: TCD/MCD/BCD가 필요합니다.")
        for key, value in cds.items():
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not SHAPE_CD_LIMITS[0] <= value <= SHAPE_CD_LIMITS[1]):
                raise ValueError(f"{label}/{role}/{key}: 2~120 nm 범위가 필요합니다.")


def _anchor(anchor, label):
    if not isinstance(anchor, dict) or set(anchor) != {"module", "step_id", "item_id"}:
        raise ValueError(f"{label}: module, step_id, item_id가 필요합니다.")
    if (any(not isinstance(anchor[k], str) or not anchor[k].strip() or len(anchor[k]) > 100
            for k in ("step_id", "item_id")) or not isinstance(anchor["module"], str)
            or len(anchor["module"]) > 100):
        raise ValueError(f"{label}: 앵커 식별자가 올바르지 않습니다.")


def save(document, base_version, actor):
    validate(document)
    document = {**document, "dimensions": document.get("dimensions", DEFAULT_DIMENSIONS),
                "shape_profiles": document.get("shape_profiles", {})}
    document = json.loads(json.dumps(document))
    for product in document["products"].values():
        for profile in product["profiles"].values():
            profile.setdefault("dimension_anchors", {})
            profile.setdefault("shape_profiles", {})
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS revisions (version INTEGER PRIMARY KEY, document TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT COALESCE(MAX(version), 0) FROM revisions").fetchone()[0]
        if current != base_version:
            raise Conflict("다른 관리자가 3D 모델을 변경했습니다. 최신 모델을 다시 불러오세요.")
        result = {**document, "version": current + 1, "updated_at": datetime.now(timezone.utc).isoformat(),
                  "updated_by": actor}
        db.execute("INSERT INTO revisions(version,document) VALUES(?,?)", (current + 1, json.dumps(result, ensure_ascii=False)))
    return result


def anchor_candidates(product):
    if not product:
        return []
    rows = product_semantics.load_inline_matching_rows(product)
    result, seen = [], set()
    for row in rows:
        key = (row.get("module", ""), row["step_id"], row["item_id"])
        if key in seen:
            continue
        seen.add(key)
        result.append({"module": key[0], "step_id": key[1], "item_id": key[2],
                       "step_desc": row.get("step_desc", ""), "item_desc": row.get("item_desc", "")})
    return sorted(result, key=lambda row: (row["module"], row["step_id"], row["item_id"]))


def prompt_context(product="", text="", *, max_chars=3000):
    """Small, factual scene summary for text-only LLMs; no mesh or other products."""
    if not STRUCTURE_QUERY.search(str(text)):
        return {}
    doc = read()
    key = "logic/6T"
    profiles = {}
    if product:
        profiles = doc["products"].get(product, {}).get("profiles", {})
        explicit = next((candidate for candidate in ("logic/5T", "logic/6T", "logic/7.5T", "logic/9T",
                                                   "sram/HD", "sram/HC")
                         if re.search(rf"(?<![\w.]){re.escape(candidate.split('/')[1])}(?![\w.])", str(text), re.I)), None)
        if explicit:
            key = explicit
        elif len(profiles) == 1:
            key = next(iter(profiles))
        elif len(profiles) > 1:
            return {"source": "관리자 GAA 구조 모델", "scope": product,
                    "available_profiles": sorted(profiles),
                    "selection_required": "제품에 여러 Logic/SRAM 변형이 있습니다. 타입과 변형을 확인해야 치수를 특정할 수 있습니다."}
    kind, variant = key.split("/")
    scene = build_scene(doc, product, kind, variant)
    summary = {
        "source": "관리자 GAA 구조 모델 + 선택 제품 오버라이드" if key in profiles else "관리자 GAA 공통 구조 모델",
        "scope": "공통" if not product else product,
        "type": kind, "variant": variant,
        "product_override_present": key in profiles,
        "axes": scene["axes"],
        "topology": ["수평 NS 채널은 아래에서 위로 1..N층이다.",
                     "Inner gate는 각 NS의 위아래를 감싸고 상부 gate와 연결된다.",
                     "S/D는 게이트의 X 양쪽, contact와 MOL은 그 위, BEOL M1/M2는 MOL 위다.",
                     "RX는 활성 영역, Field는 절연 영역, SDB는 인접 구조의 절연 경계다.",
                     "Latch-up 보기는 PMOS N-Well과 NMOS P-Well/기판 및 Well tap의 기생 PNPN 경로다."],
        "sheet_dimensions_nm": scene["nanosheet_dimensions_nm"],
        "parameters": {key: value for key, value in scene["parameters"].items()
                       if key in {"mol_level_count", "mol_height_nm", "gate_to_sd_gap_nm", "sd_width_nm",
                                  "sd_protrusion_nm", "sdb_width_nm", "sd_doping_log10_cm3"}},
        "shape_profiles_nm": scene["shape_profiles"],
        "measurements_nm": {key: {"label": value["label"], "from": value["from_label"],
                                   "to": value["to_label"], "model_height_nm": value["height_nm"],
                                   "inline_anchor": value["anchor"]}
                            for key, value in scene["measurements"].items()},
        "role_anchors": scene["anchors"],
        "limits": "모델 계산값은 실측 MTS가 아니다. 도핑은 색상 표현만 하며 ET/전기 특성 예측을 하지 않는다.",
    }
    encoded = json.dumps(summary, ensure_ascii=False)
    if len(encoded) <= max_chars:
        return summary
    summary.pop("role_anchors")
    summary.pop("shape_profiles_nm")
    if len(json.dumps(summary, ensure_ascii=False)) > max_chars:
        summary["measurements_nm"] = dict(list(summary["measurements_nm"].items())[:3])
        summary["sheet_dimensions_nm"] = {key: summary["sheet_dimensions_nm"][key]
                                          for key in ("count_per_stack", "active_stack_height", "center_pitch")}
    if len(json.dumps(summary, ensure_ascii=False)) > max_chars:
        summary.pop("measurements_nm")
        summary.pop("parameters")
    return summary


def apply_numeric_edits(document, product, kind, variant, edits):
    """Validate an LLM's small numeric patch before any UI preview or save."""
    if not isinstance(edits, list) or not 1 <= len(edits) <= 12:
        raise ValueError("한 번에 1~12개의 치수 변경만 제안할 수 있습니다.")
    candidate = json.loads(json.dumps({key: document[key] for key in
        ("variants", "products", "dimensions", "shape_profiles")}))
    key = f"{kind}/{variant}"
    if kind not in DEFAULTS or variant not in DEFAULTS[kind]:
        raise ValueError("지원하지 않는 타입/변형입니다.")
    if product:
        if not isinstance(product, str) or not product.strip() or len(product) > 200:
            raise ValueError("제품명이 올바르지 않습니다.")
        profiles = candidate["products"].setdefault(product, {"profiles": {}})["profiles"]
        profile = profiles.setdefault(key, {"parameters": {}, "anchors": {},
                                            "dimension_anchors": {}, "shape_profiles": {}})
        params = profile["parameters"]
        shapes = profile["shape_profiles"]
    else:
        params = candidate["variants"][kind][variant]
        shapes = candidate["shape_profiles"].setdefault(key, {})
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"kind", "name", "role", "value"}:
            raise ValueError("LLM 변경안 형식이 올바르지 않습니다.")
        value = edit["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("치수 변경은 숫자여야 합니다.")
        if edit["kind"] == "parameter" and edit["role"] == "" and edit["name"] in {**PARAM_LIMITS, **DETAIL_LIMITS}:
            params[edit["name"]] = value
        elif edit["kind"] == "shape" and edit["role"] in SHAPABLE_ROLES and edit["name"] in {"tcd_nm", "mcd_nm", "bcd_nm"}:
            role = edit["role"]
            base = shapes.get(role) or candidate["shape_profiles"].get(key, {}).get(role) or {
                field: 24.0 for field in ("tcd_nm", "mcd_nm", "bcd_nm")}
            shapes[role] = {**base, edit["name"]: value}
        else:
            raise ValueError("지원하지 않는 구조 치수 변경입니다.")
    validate(candidate)
    build_scene(candidate, product, kind, variant)
    return candidate


def build_scene(document, product="", kind="", variant="", *, include_candidates=False, view="gaa"):
    """Return the exact geometric primitives sent to Three.js and text-only LLMs."""
    config = validate({"variants": document["variants"], "products": document["products"],
                       "dimensions": document.get("dimensions", DEFAULT_DIMENSIONS),
                       "shape_profiles": document.get("shape_profiles", {})})
    kind = kind or "logic"
    if kind not in DEFAULTS:
        raise ValueError("지원하지 않는 모델 타입입니다.")
    variant = variant or ("6T" if kind == "logic" else "HD")
    if variant not in DEFAULTS[kind]:
        raise ValueError("지원하지 않는 모델 변형입니다.")
    if view not in {"gaa", "latchup"}:
        raise ValueError("지원하지 않는 구조 보기입니다.")
    profile = config["products"].get(product, {}).get("profiles", {}).get(f"{kind}/{variant}", {}) if product else {}
    params = {**config["variants"][kind][variant], **profile.get("parameters", {})}
    shape_profiles = {**config["shape_profiles"].get(f"{kind}/{variant}", {}),
                      **profile.get("shape_profiles", {})}
    anchors = profile.get("anchors", {})
    candidates = anchor_candidates(product)
    valid = {(r["module"], r["step_id"], r["item_id"]) for r in candidates}
    checked = {role: {**value, "status": "matched" if (value["module"], value["step_id"], value["item_id"]) in valid else "missing"}
               for role, value in anchors.items()}
    dimension_anchors = {key: {**value, "status": "matched" if (value["module"], value["step_id"], value["item_id"]) in valid else "missing"}
                         for key, value in profile.get("dimension_anchors", {}).items()}
    parts = []

    def box(role, name, pos, size, color, *, opacity=1, shape="box", profile_widths=None, metadata=None):
        role_profile = shape_profiles.get(role)
        if role_profile:
            cds = [role_profile[key] for key in ("bcd_nm", "mcd_nm", "tcd_nm")]
            shape = "profile_box"
            profile_widths = [round(cd / max(cds), 4) for cd in cds]
            size = [max(cds) / NANOSHEET_NM_PER_UNIT, size[1], size[2]]
            metadata = {**(metadata or {}), **role_profile}
        parts.append({"id": f"{role}-{len(parts)}", "role": role, "stage": STAGES[role],
                      "name": name, "shape": shape,
                      "center": [round(float(x), 3) for x in pos], "size": [round(float(x), 3) for x in size],
                      "color": color, "opacity": opacity, "anchor": checked.get(role),
                      **({"profile_widths": profile_widths} if profile_widths else {}),
                      **({"metadata": metadata} if metadata else {})})

    h, gate, width, space, count = (params[k] for k in ("cell_height", "gate_width", "sheet_width", "sheet_spacing", "sheet_count"))
    count = int(count)
    gate_cds = {level: params.get(f"gate_{level}_cd_nm", gate * NANOSHEET_NM_PER_UNIT)
                for level in ("top", "middle", "bottom")}
    if "gate" in shape_profiles:
        gate_cds = {level: shape_profiles["gate"][field] for level, field in
                    (("top", "tcd_nm"), ("middle", "mcd_nm"), ("bottom", "bcd_nm"))}
    gate_max = max(gate_cds.values()) / NANOSHEET_NM_PER_UNIT
    gate_profile = [round(gate_cds[level] / max(gate_cds.values()), 4)
                    for level in ("bottom", "middle", "top")]
    doping_log = params.get("sd_doping_log10_cm3")
    doping = (doping_log - 17.0) / 4.0 if doping_log is not None else params.get("sd_doping_relative")
    sd_width = params.get("sd_width_nm", 26.0) / NANOSHEET_NM_PER_UNIT
    sd_extent = max([sd_width, *(max(shape_profiles[role].values()) / NANOSHEET_NM_PER_UNIT
                                 for role in ("source", "drain") if role in shape_profiles)])
    sd_x = gate_max / 2 + sd_extent / 2 + params.get("gate_to_sd_gap_nm", 14.2) / NANOSHEET_NM_PER_UNIT
    shifts = [-1.55, 1.55] if kind == "sram" else [0]
    for cell_index, shift in enumerate(shifts):
        suffix = f" {cell_index + 1}" if kind == "sram" else ""
        box("substrate", "기판" + suffix, [shift, -0.16, 0], [2.9, 0.32, h], "#43536b")
        box("rx", "RX 활성 영역" + suffix, [shift, 0.015, 0], [2.55, 0.035, min(h, 1.3)], "#478b7a", opacity=0.45)
        for z in (-(h + 0.24) / 2, (h + 0.24) / 2):
            box("field", "Field 절연" + suffix, [shift, -0.035, z],
                [2.9, 0.15, 0.24], "#c9d0d8", opacity=0.45)
        box("sdb", "SDB 절연 경계" + suffix, [shift + 1.47, 0.13, 0],
            [params.get("sdb_width_nm", 5.2) / NANOSHEET_NM_PER_UNIT, 0.4, h],
            "#d6b8a2", opacity=0.8)
        top = 0.43 + (count - 1) * space
        source_top = top + params.get("sd_protrusion_nm", 22.4) / NANOSHEET_NM_PER_UNIT
        for side, x in (("소스", -sd_x), ("드레인", sd_x)):
            role = "source" if x < 0 else "drain"
            box(role, side + suffix, [shift + x, source_top / 2, 0],
                [sd_width, source_top, width * 0.8],
                "#4b80bd" if doping is not None and doping >= 0.65 else "#3e90b4",
                opacity=0.88, shape="profile_box", profile_widths=[0.72, 1.0, 0.82],
                metadata={"sd_doping_log10_cm3": doping_log, "sd_color_scale": doping,
                          "meaning": "color-only display; no calibrated electrical inference"} if doping is not None else None)
            box("contact", side + " 콘택트" + suffix, [shift + x, source_top + 0.22, 0],
                [0.32, 0.44, 0.32], "#e2ae61")
        for index in range(count):
            y = 0.43 + index * space
            sheet_width_nm = params.get(f"ns{index + 1}_width_nm", width * NANOSHEET_NM_PER_UNIT)
            sheet_thickness_nm = params.get(f"ns{index + 1}_thickness_nm", NANOSHEET_THICKNESS_NM)
            box("channel", f"나노시트 {index + 1}" + suffix, [shift, y, 0],
                [2.02, sheet_thickness_nm / NANOSHEET_NM_PER_UNIT,
                 sheet_width_nm / NANOSHEET_NM_PER_UNIT], "#65c4dd",
                metadata={"sheet_index": index + 1, "width_nm": sheet_width_nm, "thickness_nm": sheet_thickness_nm})
            for side, offset in (("상", 0.12), ("하", -0.12)):
                box("inner_gate", f"Inner gate {index + 1} {side}" + suffix,
                    [shift, y + offset, 0], [gate, 0.06, width + 0.26], "#aa80d5", opacity=0.82)
        box("gate", "상부 게이트" + suffix, [shift, top + 0.19, 0],
            [gate_max, 0.26, width + 0.42], "#8f63c7", opacity=0.9,
            shape="profile_box", profile_widths=gate_profile,
            metadata={"TCD_nm": gate_cds["top"], "MCD_nm": gate_cds["middle"],
                      "BCD_nm": gate_cds["bottom"]})
        for z in (-(width + 0.25) / 2, (width + 0.25) / 2):
            box("gate", "게이트 측벽" + suffix, [shift, (top + 0.28) / 2, z],
                [gate, top + 0.28, 0.16], "#8f63c7", opacity=0.75)
        for x in (-(gate_max + 0.16) / 2, (gate_max + 0.16) / 2):
            box("spacer", "스페이서" + suffix, [shift + x, (top + 0.3) / 2, 0], [0.12, top + 0.3, width + 0.35], "#e7c47f", opacity=0.75)
        mol_y = source_top + params.get("mol_height_nm", 24.0) / NANOSHEET_NM_PER_UNIT
        gate_top = top + 0.32
        box("contact", "게이트 콘택트" + suffix, [shift, (gate_top + mol_y) / 2, 0],
            [0.3, mol_y - gate_top, 0.3], "#e2ae61")
        for x, name in ((-sd_x, "소스"), (0, "게이트"), (sd_x, "드레인")):
            box("mol", f"MOL M0 {name}" + suffix, [shift + x, mol_y, 0],
                [0.55, 0.14, 0.72], "#e9ae63")
        mol_levels = int(params.get("mol_level_count", 2))
        mol_pitch = params.get("mol_level_pitch_nm", 12.8) / NANOSHEET_NM_PER_UNIT
        for level in range(2, mol_levels + 1):
            local_y = mol_y + (level - 1) * mol_pitch
            for x, name in ((-sd_x, "소스"), (0, "게이트"), (sd_x, "드레인")):
                box("mol", f"MOL V{level - 1} {name}" + suffix,
                    [shift + x, local_y - mol_pitch / 2, 0],
                    [0.16, mol_pitch, 0.16], "#d7b77f")
                box("mol", f"MOL M0-{level} {name}" + suffix,
                    [shift + x, local_y, 0], [0.45, 0.12, 0.64], "#d9a662")
        mol_top = mol_y + (mol_levels - 1) * mol_pitch
        m1_y = mol_top + 0.52
        m2_y = m1_y + 0.55
        for x, name in ((-sd_x, "소스"), (0, "게이트"), (sd_x, "드레인")):
            box("beol", f"BEOL V0 {name}" + suffix, [shift + x, (mol_top + m1_y) / 2, 0],
                [0.18, m1_y - mol_top, 0.18], "#9bc5d9")
            box("beol", f"BEOL M1 {name}" + suffix, [shift + x, m1_y, 0],
                [0.2, 0.16, 2.0], "#8bc4e0")
        box("beol", "BEOL V1" + suffix, [shift - sd_x, (m1_y + m2_y) / 2, 0.75],
            [0.18, m2_y - m1_y, 0.18], "#d4e3e9")
        box("beol", "BEOL M2" + suffix, [shift, m2_y, 0.75],
            [2.8, 0.16, 0.22], "#d4e3e9")
    if kind == "sram":
        box("bitline", "SRAM 비트라인", [0, mol_y + 0.25, -0.75], [4.4, 0.12, 0.18], "#e2ae61")
    if view == "latchup":
        parts.clear()
        box("substrate", "P형 기판", [0, -0.47, 0], [5.8, 0.5, 2.2], "#53647a")
        box("nwell", "N-Well · PMOS 영역", [-1.35, -0.13, 0],
            [2.5, 0.35, 1.8], "#6885bc", opacity=0.68)
        box("pwell", "P-Well · NMOS 영역", [1.35, -0.13, 0],
            [2.5, 0.35, 1.8], "#b8809a", opacity=0.68)
        for x, name, color in ((-1.95, "P+ PMOS S/D", "#e3a2ad"),
                               (-0.75, "P+ PMOS S/D", "#e3a2ad"),
                               (0.75, "N+ NMOS S/D", "#72c5d9"),
                               (1.95, "N+ NMOS S/D", "#72c5d9")):
            box("source" if x < 0 else "drain", name, [x, 0.18, 0],
                [0.42, 0.28, 0.9], color)
        box("well_tap", "N-Well tap → VDD", [-2.48, 0.22, 0.55],
            [0.24, 0.35, 0.24], "#dcb572")
        box("well_tap", "P-Well tap → VSS", [2.48, 0.22, 0.55],
            [0.24, 0.35, 0.24], "#dcb572")
        box("gate", "PMOS gate", [-1.35, 0.36, 0], [0.2, 0.34, 1.0], "#a281d1")
        box("gate", "NMOS gate", [1.35, 0.36, 0], [0.2, 0.34, 1.0], "#a281d1")
        box("latch_path", "기생 PNP/NPN 결합 경로", [0, -0.15, -0.62],
            [3.8, 0.06, 0.09], "#ec764e")
    sheet_pitch_nm = round(space * NANOSHEET_NM_PER_UNIT, 2)
    sheet_width_nm = round(width * NANOSHEET_NM_PER_UNIT, 2)
    thicknesses = [params.get(f"ns{i}_thickness_nm", NANOSHEET_THICKNESS_NM) for i in range(1, count + 1)]
    widths = [params.get(f"ns{i}_width_nm", sheet_width_nm) for i in range(1, count + 1)]
    sheet_stack_height_nm = round((count - 1) * sheet_pitch_nm + (thicknesses[0] + thicknesses[-1]) / 2, 2)
    pair_gaps = [round(sheet_pitch_nm - (left + right) / 2, 2)
                 for left, right in zip(thicknesses, thicknesses[1:])]
    ns_bottom = 0.43 - thicknesses[0] / (2 * NANOSHEET_NM_PER_UNIT)
    ns_top = top + thicknesses[-1] / (2 * NANOSHEET_NM_PER_UNIT)
    landmarks = {"substrate_top": 0.0, "ns_stack_bottom": ns_bottom, "ns_stack_top": ns_top,
                 "gate_top": gate_top, "mol_m0_bottom": mol_y - 0.07,
                 "mol_m0_top": mol_y + 0.07, "beol_m1_bottom": m1_y - 0.08}
    measurements = {key: {**definition, "from_label": LANDMARK_LABELS[definition["from"]],
                          "to_label": LANDMARK_LABELS[definition["to"]],
                          "height_nm": round(abs(landmarks[definition["to"]] - landmarks[definition["from"]]) * NANOSHEET_NM_PER_UNIT, 2),
                          "anchor": dimension_anchors.get(key)}
                    for key, definition in config["dimensions"].items()}
    return {"schema": "flow.gaa.scene.v1", "view": view,
            "units": "illustrative scene units; nanosheet dimensions calibrated to nm only in GAA view", "product": product,
            "type": kind, "variant": variant, "parameters": params, "axes": {"x": "source to drain", "y": "vertical", "z": "cell height direction"},
            "relations": STRUCTURE_RELATIONS if view == "gaa" else [
                {"subject": "nwell", "predicate": "hosts", "object": "PMOS"},
                {"subject": "pwell", "predicate": "hosts", "object": "NMOS"},
                {"subject": "latch_path", "predicate": "conceptual_parasitic_path_between", "object": "nwell/pwell"}],
            "nanosheet_dimensions_nm": {"count_per_stack": count, "thickness": NANOSHEET_THICKNESS_NM,
                                         "vertical_gap": pair_gaps[0] if pair_gaps else None,
                                         "pair_gaps": pair_gaps,
                                         "center_pitch": sheet_pitch_nm, "width": sheet_width_nm,
                                         "active_stack_height": sheet_stack_height_nm,
                                         "sheets": [{"index": i, "width": widths[i - 1], "thickness": thicknesses[i - 1]}
                                                    for i in range(1, count + 1)]},
            "gate_profile_nm": gate_cds, "shape_profiles": shape_profiles,
            "measurements": measurements if view == "gaa" else {},
            "landmarks": {key: round(value, 4) for key, value in landmarks.items()} if view == "gaa" else {},
            "landmark_labels": LANDMARK_LABELS,
            "mol_level_count": mol_levels,
            "spacing_nm": {"gate_to_sd": round((sd_x - sd_extent / 2 - gate_max / 2) * NANOSHEET_NM_PER_UNIT, 2),
                           "sdb_width": round(params.get("sdb_width_nm", 5.2), 2)},
            "dimension_reference": {"url": NANOSHEET_SOURCE,
                                    "note": "IBM/Samsung/GF 2017 3-sheet example: 5 nm thickness, 10 nm vertical gap, 15–45 nm width; default width 40 nm is a representative choice."},
            "layer_order": ["FEOL: nanosheet and inner gate", "FEOL: upper gate",
                            "MOL: contact and M0", "BEOL: V0, M1, V1, M2"],
            "roles": ROLES, "parts": parts, "anchors": checked,
            "dimension_anchors": dimension_anchors,
            "latchup_path": {"sequence": ["P+ PMOS", "N-Well", "P-Well/P형 기판", "N+ NMOS"],
                             "meaning": "기생 PNP와 NPN이 결합하는 개념 경로; trigger/holding 수치 예측이 아님"}
                             if view == "latchup" else None,
            "anchor_candidates": candidates[:3000] if include_candidates else [], "anchor_candidate_count": len(candidates),
            "warnings": [f"{role}: 해당 제품의 INLINE 매칭에서 앵커를 찾지 못했습니다." for role, value in checked.items() if value["status"] == "missing"]
                        + [f"{key}: 해당 제품의 INLINE 매칭에서 측정 구간 앵커를 찾지 못했습니다." for key, value in dimension_anchors.items() if value["status"] == "missing"],
            "model_source": "관리자 정의 개념 모델; 실측 치수/공정 순서를 의미하지 않음"}
