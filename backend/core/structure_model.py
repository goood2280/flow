"""Small, explicit GAA scene graph with a published nanosheet reference."""
from __future__ import annotations

import json
import re
import sqlite3
import zlib
from contextlib import closing
from datetime import datetime, timezone

from core.paths import PATHS
from core import product_semantics, structure_topology


ROLES = {"substrate": "기판", "source": "소스", "drain": "드레인", "channel": "나노시트 채널",
         "inner_gate": "Inner gate", "gate": "게이트 금속", "highk": "High-k 게이트 절연막", "spacer": "스페이서",
         "contact": "MOL 콘택트", "mol": "MOL M0", "beol": "BEOL M1/M2", "bitline": "비트라인",
         "rx": "RX 활성 영역", "field": "Field 절연 영역", "sdb": "SDB",
         "nwell": "N-Well", "pwell": "P-Well", "well_tap": "Well/Substrate tap",
         "latch_path": "기생 PNPN 경로", "guard_ring": "Guard ring"}
STAGES = {"substrate": "FEOL", "source": "FEOL", "drain": "FEOL", "channel": "FEOL",
          "inner_gate": "FEOL", "gate": "FEOL", "highk": "FEOL", "spacer": "FEOL", "contact": "MOL",
          "mol": "MOL", "beol": "BEOL", "bitline": "BEOL", "rx": "FEOL", "field": "FEOL", "sdb": "FEOL",
          "nwell": "FEOL", "pwell": "FEOL", "well_tap": "FEOL", "latch_path": "FEOL",
          "guard_ring": "FEOL"}
# Only the nanosheet dimensions are calibrated. Other shapes show topology.
NANOSHEET_NM_PER_UNIT = 40.0
NANOSHEET_THICKNESS_NM = 5.0
NANOSHEET_SOURCE = "https://research.ibm.com/publications/stacked-nanosheet-gate-all-around-transistor-to-enable-scaling-beyond-finfet"
PARAM_LIMITS = {"cell_height": (1.0, 4.0), "gate_width": (0.45, 2.2),
                "sheet_width": (0.5, 2.0), "sheet_spacing": (0.18, 0.65),
                "sheet_count": (1, 5)}
DETAIL_LIMITS = {**{f"ns{i}_{field}_nm": (2.0, 80.0 if field == "width" else 15.0)
                     for i in range(1, 6) for field in ("width", "thickness")},
                 # Inner gate i sits under NS i (inner gate 1 = substrate..NS1).
                 # Height sets the vertical gap, so it moves every sheet above it.
                 **{f"inner_gate{i}_{field}_nm": (2.0, 90.0 if field == "width" else 40.0)
                    for i in range(1, 6) for field in ("width", "height")},
                 **{f"gate_{level}_cd_nm": (8.0, 90.0) for level in ("top", "middle", "bottom")},
                 "sd_doping_relative": (0.0, 1.0), "sd_doping_log10_cm3": (17.0, 21.0),
                 "mol_level_count": (1, 4), "gate_mol_level_count": (1, 4),
                 "mol_sd_landing_pad": (0, 1), "contact_epi_recess_nm": (0.0, 40.0),
                 "gate_height_above_ns_nm": (4.0, 80.0),
                 "sdb_width_nm": (2.0, 40.0), "gate_to_sd_gap_nm": (0.0, 30.0),
                 "sd_width_nm": (8.0, 80.0), "sd_protrusion_nm": (0.0, 80.0),
                 "epi_facet_angle_deg": (25.0, 80.0),
                 "mol_height_nm": (8.0, 100.0), "mol_level_pitch_nm": (8.0, 80.0)}
INTEGER_PARAMS = {"sheet_count", "mol_level_count", "gate_mol_level_count", "mol_sd_landing_pad"}
PARAMETER_GUIDE = {
    "sheet_count": "나노시트(NS) 층 수", "sheet_spacing": "NS 중심 간격(40 nm/단위)",
    "sheet_width": "NS 기본 폭(40 nm/단위)", "gate_width": "게이트 기본 폭(40 nm/단위)",
    "ns{i}_width_nm": "NS {i}층 가로 폭", "ns{i}_thickness_nm": "NS {i}층 두께",
    "inner_gate{i}_width_nm": "Inner gate {i} (NS{i} 바로 아래, 1=기판~NS1) 소스-드레인 방향 폭",
    "inner_gate{i}_height_nm": "Inner gate {i} 높이(=NS{i} 아래 빈 공간, 바꾸면 위 시트가 이동)",
    "mol_level_count": "epi(S/D) 위 MOL 단 수 (1~4)",
    "gate_mol_level_count": "게이트 위 MOL 단 수 (없으면 mol_level_count와 같음)",
    "mol_sd_landing_pad": "MOL 단 사이 네모 패드(epi·게이트 공통): 0=바로 연결(기본, 직결 기둥), 1=패드 있음",
    "contact_epi_recess_nm": "epi와 연결된 MOL 콘택트가 epi 상면을 깎고 들어간 깊이",
    "gate_height_above_ns_nm": "NS 최상층 상면에서 게이트 상면까지 높이",
    "mol_height_nm": "S/D 위 MOL M0 높이", "mol_level_pitch_nm": "MOL 단 간격",
    "gate_to_sd_gap_nm": "게이트-S/D 간격", "sd_width_nm": "S/D 가로 폭",
    "sd_protrusion_nm": "NS 최상층 위 S/D 돌출", "epi_facet_angle_deg": "epi 성장면 각도",
    "sdb_width_nm": "SDB 폭", "sd_doping_log10_cm3": "S/D 도핑 log10(cm^-3), 색상만",
}
# Material fill recipes: layers are applied from the outside in, in list order.
MATERIAL_REGIONS = {"gate": "상부 게이트 (NS 위)", "sd_contact": "epi(S/D) MOL 콘택트",
                    "gate_contact": "게이트 콘택트"}
MATERIAL_REGION_ROLES = {"gate": "gate", "sd_contact": "contact", "gate_contact": "contact"}
MATERIAL_MODES = {"bottom": "아래부터 쌓기", "liner": "측벽", "u_liner": "U자", "fill": "나머지 채움"}
MATERIAL_SIZES = {"thin": 0.5, "normal": 1.0, "thick": 2.0}
MATERIAL_PALETTE = ["#d9a441", "#5b8fd9", "#58b37f", "#c9677f", "#8b73d1", "#3fb0b8",
                    "#e07b45", "#8a9a5b", "#b5838d", "#6d7fa3"]
KNOWN_MATERIAL_COLORS = {"w": "#8e9aaf", "co": "#6c7fa3", "ru": "#9c8fb8", "cu": "#d08a57",
                         "mo": "#7f8ea3", "tin": "#c6a15b", "tan": "#a88b5a", "tial": "#b7b0a0",
                         "al": "#b8c2cc", "ti": "#a3a8b0", "sio2": "#dfe6ee", "sin": "#e7c47f"}
HK_UNITS = 0.012
MOL_LEVEL_COLORS = {1: "#eda951", 2: "#50b7d9", 3: "#ab8ce0", 4: "#e07a9a"}
DOCUMENT_KEYS = {"variants", "products", "dimensions", "shape_profiles", "material_stacks"}
PROFILE_KEYS = {"parameters", "anchors", "dimension_anchors", "shape_profiles", "material_stacks"}
SHAPABLE_ROLES = {"source", "drain", "gate", "spacer", "contact", "mol", "beol", "sdb"}
SHAPE_PRIMITIVES = {"profile_box", "cylinder", "tapered_cylinder", "faceted_epi", "gate_shell"}
SHAPE_CD_LIMITS = (2.0, 120.0)
STRUCTURE_QUERY = re.compile(
    r"gaa|nanosheet|nano.?sheet|ns\s*\d|inner.?gate|gate|source|drain|cell.?height|mol|beol|sdb|latch.?up|well|"
    r"게이트|나노시트|시트|구조|단면|높이|폭|두께|도핑|앵커|inline|step.?id|item.?id|mts|tcd|mcd|bcd",
    re.I)
LANDMARK_LABELS = {"substrate_top": "기판 상면", "ns_stack_bottom": "NS 최하단",
                   "ns_stack_top": "NS 최상단", "gate_top": "게이트 상면",
                   "mol_m0_bottom": "MOL M0 하면", "mol_m0_top": "MOL M0 상면",
                   "beol_m1_bottom": "BEOL M1 하면", "sd_epi_top": "S/D epi 상면",
                   "sd_contact_bottom": "S/D 콘택트 바닥"}
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
            "shape_profiles": {}, "material_stacks": {},
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
    doc.setdefault("material_stacks", {})
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
            profile.setdefault("material_stacks", {})
    return doc


def validate(document):
    if not isinstance(document, dict) or not {"variants", "products"} <= set(document) or set(document) - DOCUMENT_KEYS:
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
    stacks = document.get("material_stacks", {})
    if not isinstance(stacks, dict) or set(stacks) - valid_profiles:
        raise ValueError("공통 재료 채움 프로파일 키가 올바르지 않습니다.")
    for profile_key, regions in stacks.items():
        _material_stacks(regions, profile_key)
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
            if not isinstance(profile, dict) or set(profile) - PROFILE_KEYS or not {"parameters", "anchors"} <= set(profile):
                raise ValueError(f"{name}/{profile_key}: 형상과 앵커가 필요합니다.")
            _params(profile["parameters"], f"{name}/{profile_key}")
            kind, variant = profile_key.split("/")
            _sheet_clearance({**variants[kind][variant], **profile["parameters"]}, f"{name}/{profile_key}")
            _shape_profiles(profile.get("shape_profiles", {}), f"{name}/{profile_key}")
            _material_stacks(profile.get("material_stacks", {}), f"{name}/{profile_key}")
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
        if key in INTEGER_PARAMS and int(value) != value:
            raise ValueError(f"{key}는 정수여야 합니다.")


def _stack_layout(params):
    """Vertical NS stack in nm: inner gate i is the gap directly under NS i."""
    count = int(params["sheet_count"])
    pitch = params["sheet_spacing"] * NANOSHEET_NM_PER_UNIT
    thickness = [params.get(f"ns{i}_thickness_nm", NANOSHEET_THICKNESS_NM) for i in range(1, count + 1)]
    heights, centers, y = [], [], 0.0
    for index in range(count):
        explicit = params.get(f"inner_gate{index + 1}_height_nm")
        if explicit is not None:
            height = explicit
        elif index == 0:
            # First sheet centre stays at 0.43 scene units, as before per-level heights.
            height = 0.43 * NANOSHEET_NM_PER_UNIT - thickness[0] / 2
        else:
            height = pitch - (thickness[index - 1] + thickness[index]) / 2
        heights.append(height)
        y += height
        centers.append(y + thickness[index] / 2)
        y += thickness[index]
    return {"thickness": thickness, "inner_gate_heights": heights, "centers_nm": centers, "top_nm": y}


def _gate_height_nm(params, layout):
    return params.get("gate_height_above_ns_nm", 0.32 * NANOSHEET_NM_PER_UNIT - layout["thickness"][-1] / 2)


def _sheet_clearance(params, label):
    """Keep independently sized sheets and upper contact geometry valid."""
    layout = _stack_layout(params)
    if any(height <= 0 for height in layout["inner_gate_heights"]):
        raise ValueError(f"{label}: NS 층간 간격은 인접 시트 두께의 절반 합보다 커야 합니다.")
    if params.get("sd_protrusion_nm", 22.4) + params.get("mol_height_nm", 24.0) <= _gate_height_nm(params, layout):
        raise ValueError(f"{label}: MOL M0는 게이트 상면보다 높아야 합니다.")
    if params.get("contact_epi_recess_nm", 0.0) >= 0.9 * (layout["top_nm"] + params.get("sd_protrusion_nm", 22.4)):
        raise ValueError(f"{label}: epi 식각 깊이가 epi 높이보다 깊습니다.")


def _material_stacks(regions, label):
    if not isinstance(regions, dict) or set(regions) - MATERIAL_REGIONS.keys():
        raise ValueError(f"{label}: 재료 채움 영역은 {', '.join(MATERIAL_REGIONS)} 중 하나여야 합니다.")
    for region, layers in regions.items():
        if not isinstance(layers, list) or len(layers) > 10:
            raise ValueError(f"{label}/{region}: 재료 층은 최대 10개입니다.")
        for index, layer in enumerate(layers):
            if (not isinstance(layer, dict) or not {"material", "mode"} <= set(layer)
                    or set(layer) - {"material", "mode", "size", "thickness_nm"}):
                raise ValueError(f"{label}/{region}: 재료 층은 material, mode가 필요합니다.")
            material = layer["material"]
            if not isinstance(material, str) or not material.strip() or len(material) > 40:
                raise ValueError(f"{label}/{region}: 재료 이름이 올바르지 않습니다.")
            if layer["mode"] not in MATERIAL_MODES:
                raise ValueError(f"{label}/{region}/{material}: 채움 방식은 {', '.join(MATERIAL_MODES)} 중 하나입니다.")
            if "size" in layer and layer["size"] not in MATERIAL_SIZES:
                raise ValueError(f"{label}/{region}/{material}: 두께 정도는 thin, normal, thick 중 하나입니다.")
            if "thickness_nm" in layer:
                value = layer["thickness_nm"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.2 <= value <= 40:
                    raise ValueError(f"{label}/{region}/{material}: 두께는 0.2~40 nm입니다.")
            if layer["mode"] == "fill" and index != len(layers) - 1:
                raise ValueError(f"{label}/{region}: 나머지 채움(fill) 층은 마지막 하나만 둘 수 있습니다.")


def normalize_layers(layers):
    """Put the single remainder fill last so natural-language order still validates."""
    cleaned = [{key: value for key, value in layer.items() if value is not None}
               for layer in layers if isinstance(layer, dict)]
    fills = [layer for layer in cleaned if layer.get("mode") == "fill"]
    rest = [layer for layer in cleaned if layer.get("mode") != "fill"]
    return rest + fills[-1:]


def material_color(name):
    key = re.sub(r"[^a-z0-9]", "", str(name).casefold())
    if key in KNOWN_MATERIAL_COLORS:
        return KNOWN_MATERIAL_COLORS[key]
    return MATERIAL_PALETTE[zlib.crc32(str(name).strip().encode("utf-8")) % len(MATERIAL_PALETTE)]


def _shape_profiles(profiles, label):
    if not isinstance(profiles, dict) or set(profiles) - SHAPABLE_ROLES:
        raise ValueError(f"{label}: 구조 프로파일 역할이 올바르지 않습니다.")
    for role, cds in profiles.items():
        if (not isinstance(cds, dict) or not {"tcd_nm", "mcd_nm", "bcd_nm"} <= set(cds)
                or set(cds) - {"tcd_nm", "mcd_nm", "bcd_nm", "primitive"}):
            raise ValueError(f"{label}/{role}: TCD/MCD/BCD가 필요합니다.")
        if "primitive" in cds and cds["primitive"] not in SHAPE_PRIMITIVES:
            raise ValueError(f"{label}/{role}: 지원하지 않는 기본 형상입니다.")
        if cds.get("primitive") == "cylinder" and len({cds[key] for key in ("tcd_nm", "mcd_nm", "bcd_nm")}) != 1:
            raise ValueError(f"{label}/{role}: 원기둥은 TCD/MCD/BCD가 같아야 합니다.")
        for key in ("tcd_nm", "mcd_nm", "bcd_nm"):
            value = cds[key]
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
                "shape_profiles": document.get("shape_profiles", {}),
                "material_stacks": document.get("material_stacks", {})}
    document = json.loads(json.dumps(document))
    for product in document["products"].values():
        for profile in product["profiles"].values():
            profile.setdefault("dimension_anchors", {})
            profile.setdefault("shape_profiles", {})
            profile.setdefault("material_stacks", {})
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
                     "Inner gate i는 NS i 바로 아래 공간(1=기판~NS1)을 채우고 상부 gate와 연결된다.",
                     "S/D는 게이트의 X 양쪽, contact와 MOL은 그 위, BEOL M1/M2는 MOL 위다.",
                     "RX는 활성 영역, Field는 절연 영역, SDB는 인접 구조의 절연 경계다.",
                     "Latch-up 보기는 PMOS N-Well과 NMOS P-Well/기판 및 Well tap의 기생 PNPN 경로다."],
        "sheet_dimensions_nm": scene["nanosheet_dimensions_nm"],
        "parameters": {key: value for key, value in scene["parameters"].items()
                       if key in {"mol_level_count", "gate_mol_level_count", "mol_sd_landing_pad",
                                  "contact_epi_recess_nm", "mol_height_nm", "gate_to_sd_gap_nm", "sd_width_nm",
                                  "sd_protrusion_nm", "sdb_width_nm", "sd_doping_log10_cm3"}},
        "material_layers_nm": scene["material_layers"],
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
    summary.pop("material_layers_nm")
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
    return apply_edits(document, product, kind, variant, edits)


def apply_edits(document, product, kind, variant, edits, material_stacks=None):
    """Validate a numeric patch plus optional material fill recipes; nothing is saved."""
    material_stacks = material_stacks or {}
    if (not isinstance(edits, list) or len(edits) > 12 or not isinstance(material_stacks, dict)
            or not (edits or material_stacks)):
        raise ValueError("한 번에 1~12개의 치수 변경 또는 재료 채움 변경만 제안할 수 있습니다.")
    candidate = json.loads(json.dumps({key: document.get(key, {}) for key in
        ("variants", "products", "dimensions", "shape_profiles", "material_stacks")}))
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
        shapes = profile.setdefault("shape_profiles", {})
        stacks = profile.setdefault("material_stacks", {})
    else:
        params = candidate["variants"][kind][variant]
        shapes = candidate["shape_profiles"].setdefault(key, {})
        stacks = candidate["material_stacks"].setdefault(key, {})
    for region, layers in material_stacks.items():
        if region not in MATERIAL_REGIONS or not isinstance(layers, list):
            raise ValueError("지원하지 않는 재료 채움 영역입니다.")
        if layers:
            stacks[region] = normalize_layers(layers)
        else:
            stacks.pop(region, None)
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


def _dedupe(points):
    result = []
    for point in points:
        if not result or abs(point[0] - result[-1][0]) > 1e-6 or abs(point[1] - result[-1][1]) > 1e-6:
            result.append(point)
    if len(result) > 1 and abs(result[0][0] - result[-1][0]) < 1e-6 and abs(result[0][1] - result[-1][1]) < 1e-6:
        result.pop()
    return result


def _local_outline(points, center_y):
    return [[round(z, 4), round(y - center_y, 4)] for z, y in _dedupe(points)]


def _spread_column(labels, gap):
    """Keep one label column at least `gap` apart, centred on the anchors it serves."""
    ordered = sorted(labels, key=lambda label: label["position"][1])
    if not ordered:
        return labels
    wanted = sum(label["position"][1] for label in ordered) / len(ordered)
    for previous, current in zip(ordered, ordered[1:]):
        if current["position"][1] - previous["position"][1] < gap:
            current["position"][1] = previous["position"][1] + gap
    spread = sum(label["position"][1] for label in ordered) / len(ordered)
    shift = max(wanted - spread, -ordered[0]["position"][1])
    for label in ordered:
        label["position"][1] = round(label["position"][1] + shift, 3)
    return labels


def build_scene(document, product="", kind="", variant="", *, include_candidates=False, view="gaa"):
    """Return the exact geometric primitives sent to Three.js and text-only LLMs."""
    config = validate({"variants": document["variants"], "products": document["products"],
                       "dimensions": document.get("dimensions", DEFAULT_DIMENSIONS),
                       "shape_profiles": document.get("shape_profiles", {}),
                       "material_stacks": document.get("material_stacks", {})})
    kind = kind or "logic"
    if kind not in DEFAULTS:
        raise ValueError("지원하지 않는 모델 타입입니다.")
    variant = variant or ("6T" if kind == "logic" else "HD")
    if variant not in DEFAULTS[kind]:
        raise ValueError("지원하지 않는 모델 변형입니다.")
    if view not in {"gaa", "latchup"}:
        raise ValueError("지원하지 않는 구조 보기입니다.")
    profile_key = f"{kind}/{variant}"
    profile = config["products"].get(product, {}).get("profiles", {}).get(profile_key, {}) if product else {}
    params = {**config["variants"][kind][variant], **profile.get("parameters", {})}
    shape_profiles = {**config["shape_profiles"].get(profile_key, {}),
                      **profile.get("shape_profiles", {})}
    material_stacks = {region: layers for region, layers in
                       {**config.get("material_stacks", {}).get(profile_key, {}),
                        **profile.get("material_stacks", {})}.items() if layers}
    anchors = profile.get("anchors", {})
    candidates = anchor_candidates(product)
    valid = {(r["module"], r["step_id"], r["item_id"]) for r in candidates}
    checked = {role: {**value, "status": "matched" if (value["module"], value["step_id"], value["item_id"]) in valid else "missing"}
               for role, value in anchors.items()}
    dimension_anchors = {key: {**value, "status": "matched" if (value["module"], value["step_id"], value["item_id"]) in valid else "missing"}
                         for key, value in profile.get("dimension_anchors", {}).items()}
    parts = []
    nm = NANOSHEET_NM_PER_UNIT

    def box(role, name, pos, size, color, *, opacity=1, shape="box", profile_widths=None, metadata=None,
            use_profile=True, sub_label=None):
        role_profile = shape_profiles.get(role) if use_profile else None
        if role_profile:
            cds = [role_profile[key] for key in ("bcd_nm", "mcd_nm", "tcd_nm")]
            shape = role_profile.get("primitive", shape if shape in SHAPE_PRIMITIVES else "profile_box")
            profile_widths = [round(cd / max(cds), 4) for cd in cds]
            size = [max(cds) / nm, size[1], size[2]]
            metadata = {**(metadata or {}), **role_profile}
        parts.append({"id": f"{role}-{len(parts)}", "role": role, "stage": STAGES[role],
                      "name": name, "shape": shape,
                      "center": [round(float(x), 3) for x in pos], "size": [round(float(x), 3) for x in size],
                      "color": color, "opacity": opacity, "anchor": checked.get(role),
                      **({"profile_widths": profile_widths} if profile_widths else {}),
                      **({"metadata": metadata} if metadata else {}),
                      **({"sub_label": sub_label} if sub_label else {})})

    h, gate, width, space, count = (params[k] for k in ("cell_height", "gate_width", "sheet_width", "sheet_spacing", "sheet_count"))
    count = int(count)
    gate_cds = {level: params.get(f"gate_{level}_cd_nm", gate * nm)
                for level in ("top", "middle", "bottom")}
    if "gate" in shape_profiles:
        gate_cds = {level: shape_profiles["gate"][field] for level, field in
                    (("top", "tcd_nm"), ("middle", "mcd_nm"), ("bottom", "bcd_nm"))}
    gate_max = max(gate_cds.values()) / nm
    gate_profile = [round(gate_cds[level] / max(gate_cds.values()), 4)
                    for level in ("bottom", "middle", "top")]
    doping_log = params.get("sd_doping_log10_cm3")
    doping = (doping_log - 17.0) / 4.0 if doping_log is not None else params.get("sd_doping_relative")
    sd_width = params.get("sd_width_nm", 26.0) / nm
    sd_extent = max([sd_width, *(max(shape_profiles[role][key] for key in ("tcd_nm", "mcd_nm", "bcd_nm")) / nm
                                 for role in ("source", "drain") if role in shape_profiles)])
    sd_x = gate_max / 2 + sd_extent / 2 + params.get("gate_to_sd_gap_nm", 14.2) / nm
    layout = _stack_layout(params)
    thick = [value / nm for value in layout["thickness"]]
    widths = [params.get(f"ns{i}_width_nm", width * nm) for i in range(1, count + 1)]
    sheet_w = [value / nm for value in widths]
    ys = [value / nm for value in layout["centers_nm"]]
    ns_top_y = layout["top_nm"] / nm
    ns_bottom_y = ys[0] - thick[0] / 2
    gate_top = ns_top_y + _gate_height_nm(params, layout) / nm
    source_top = ns_top_y + params.get("sd_protrusion_nm", 22.4) / nm
    recess = min(params.get("contact_epi_recess_nm", 0.0) / nm, source_top * 0.9)
    contact_bottom = source_top - recess
    epi_depth = max(sheet_w) + 0.28
    gate_depth = epi_depth + 0.14
    mol_y = source_top + params.get("mol_height_nm", 24.0) / nm
    sd_levels = int(params.get("mol_level_count", 2))
    gate_levels = int(params.get("gate_mol_level_count", sd_levels))
    mol_pitch = params.get("mol_level_pitch_nm", 12.8) / nm
    # Default MOL is one continuous column per terminal; wide square pads are opt-in (=1).
    landing_pad = int(params.get("mol_sd_landing_pad", 0)) == 1
    sheet_holes = [{"y": ys[i], "thickness": thick[i], "width": sheet_w[i]} for i in range(count)]
    material_layers = {}

    def gate_cd_at(y):
        values = [gate_cds["bottom"], gate_cds["middle"], gate_cds["top"]]
        t = min(max(y / gate_top, 0.0), 1.0) * 2
        low = 0 if t < 1 else 1
        return (values[low] + (values[low + 1] - values[low]) * (t - low)) / nm

    def slab_widths(y0, y1):
        cds = [gate_cd_at(y0), gate_cd_at((y0 + y1) / 2), gate_cd_at(y1)]
        return max(cds), [round(cd / max(cds), 4) for cd in cds]

    def fill_region(region, layers, x0, x1, y0, y1, z0, z1, *, walls_z, role, base_name, remainder_color,
                    label_side=True):
        """Lay recipe layers into a box region from the outside in; returns resolved thicknesses."""
        height, span = y1 - y0, (min(x1 - x0, z1 - z0) if walls_z else x1 - x0)
        if height <= 0.01 or span <= 0.01:
            raise ValueError(f"{MATERIAL_REGIONS[region]}: 재료를 채울 공간이 없습니다.")
        plan = []
        for layer in layers:
            mode = layer["mode"]
            base = height * 0.14 if mode == "bottom" else min(span, height) * 0.1
            explicit = layer.get("thickness_nm")
            thickness = 0.0 if mode == "fill" else (
                explicit / nm if explicit is not None else base * MATERIAL_SIZES[layer.get("size", "normal")])
            plan.append([layer, thickness, explicit is not None])

        def sums(explicit_flag):
            vertical = sum(t for layer, t, e in plan if e == explicit_flag and layer["mode"] in {"bottom", "u_liner"})
            lateral = sum(2 * t for layer, t, e in plan if e == explicit_flag and layer["mode"] in {"liner", "u_liner"})
            return vertical, lateral
        fixed_y, fixed_x = sums(True)
        auto_y, auto_x = sums(False)
        if fixed_y > 0.9 * height or fixed_x > 0.9 * span:
            raise ValueError(f"{MATERIAL_REGIONS[region]}: 지정한 재료 두께 합이 영역(높이 {height * nm:.1f} nm, "
                             f"폭 {span * nm:.1f} nm)을 넘습니다.")
        scale = 1.0
        if auto_y:
            scale = min(scale, max(0.0, 0.85 * height - fixed_y) / auto_y)
        if auto_x:
            scale = min(scale, max(0.0, 0.85 * span - fixed_x) / auto_x)
        scale = max(scale, 0.05)
        for item in plan:
            if not item[2]:
                item[1] *= scale
        cavity = {"x0": x0, "x1": x1, "y0": y0, "z0": z0, "z1": z1}
        resolved = []

        def piece(layer, index, labelled, low, high):
            size = [high[axis] - low[axis] for axis in range(3)]
            if min(size) <= 1e-4:
                return
            mode_label = MATERIAL_MODES[layer["mode"]]
            box(role, f"{base_name} · {layer['material']} ({mode_label})",
                [(low[axis] + high[axis]) / 2 for axis in range(3)], size, material_color(layer["material"]),
                use_profile=False,
                metadata={"material": layer["material"], "material_mode": layer["mode"],
                          "material_region": region, "layer_index": index + 1},
                sub_label=f"{base_name} {layer['material']} · {mode_label}" if labelled and label_side else None)

        def walls(layer, index, t, bottom, labelled):
            c = cavity
            piece(layer, index, labelled, (c["x0"], bottom, c["z0"]), (c["x0"] + t, y1, c["z1"]))
            piece(layer, index, False, (c["x1"] - t, bottom, c["z0"]), (c["x1"], y1, c["z1"]))
            if walls_z:
                piece(layer, index, False, (c["x0"] + t, bottom, c["z0"]), (c["x1"] - t, y1, c["z0"] + t))
                piece(layer, index, False, (c["x0"] + t, bottom, c["z1"] - t), (c["x1"] - t, y1, c["z1"]))
            c["x0"] += t
            c["x1"] -= t
            if walls_z:
                c["z0"] += t
                c["z1"] -= t

        filled = False
        for index, (layer, t, _) in enumerate(plan):
            c = cavity
            mode = layer["mode"]
            if mode == "bottom":
                piece(layer, index, True, (c["x0"], c["y0"], c["z0"]), (c["x1"], c["y0"] + t, c["z1"]))
                c["y0"] += t
            elif mode == "liner":
                walls(layer, index, t, c["y0"], True)
            elif mode == "u_liner":
                piece(layer, index, True, (c["x0"], c["y0"], c["z0"]), (c["x1"], c["y0"] + t, c["z1"]))
                c["y0"] += t
                walls(layer, index, t, c["y0"], False)
            else:
                piece(layer, index, True, (c["x0"], c["y0"], c["z0"]), (c["x1"], y1, c["z1"]))
                filled = True
            resolved.append({"material": layer["material"], "mode": layer["mode"],
                             "thickness_nm": None if mode == "fill" else round(t * nm, 2)})
        if not filled:
            c = cavity
            size = [c["x1"] - c["x0"], y1 - c["y0"], c["z1"] - c["z0"]]
            if min(size) > 1e-4:
                box(role, f"{base_name} · 기존 재료", [(c["x0"] + c["x1"]) / 2, (c["y0"] + y1) / 2, (c["z0"] + c["z1"]) / 2],
                    size, remainder_color, use_profile=False,
                    metadata={"material_region": region, "material_mode": "remainder"})
        return resolved

    box("substrate", "기판", [0, -0.16, 0], [2.9, 0.32, h], "#43536b")
    box("rx", "RX 활성 영역", [0, 0.015, 0], [2.55, 0.035, min(h, 1.3)], "#478b7a", opacity=0.45)
    for z in (-(h + 0.24) / 2, (h + 0.24) / 2):
        box("field", "Field 절연", [0, -0.035, z], [2.9, 0.15, 0.24], "#c9d0d8", opacity=0.45)
    box("sdb", "SDB 절연 경계", [1.47, 0.13, 0],
        [params.get("sdb_width_nm", 5.2) / nm, 0.4, h], "#d6b8a2", opacity=0.8)
    for side, x in (("소스", -sd_x), ("드레인", sd_x)):
        role = "source" if x < 0 else "drain"
        box(role, side, [x, source_top / 2, 0], [sd_width, source_top, epi_depth],
            "#bd839f" if doping is None else "#4b80bd" if doping >= 0.65 else "#3e90b4",
            shape="faceted_epi",
            metadata={"sd_doping_log10_cm3": doping_log, "sd_color_scale": doping,
                      "material": "SiGe:B (pFET example)", "facet_angle_deg": params.get("epi_facet_angle_deg", 54.7356),
                      "cap_height": min(max(0, source_top - ns_top_y), epi_depth * 0.32),
                      "contact_recess_nm": round(recess * nm, 2),
                      "meaning": "{111}/{001} illustrative facet section; process-dependent, not a TEM reconstruction"},
            sub_label=f"{side} epi")
        contact_top = mol_y if landing_pad else mol_y - 0.07
        if "sd_contact" in material_stacks:
            half = (shape_profiles["contact"]["mcd_nm"] / nm if "contact" in shape_profiles else 0.32) / 2
            # The recipe fills the epi contact; direct MOL levels continue above it.
            material_layers["sd_contact"] = fill_region(
                "sd_contact", material_stacks["sd_contact"], x - half, x + half, contact_bottom, mol_y - 0.07,
                -half, half, walls_z=True, role="contact", base_name=f"{side} 콘택트", remainder_color="#e2ae61",
                label_side=x < 0)
        else:
            box("contact", side + " 콘택트", [x, (contact_bottom + contact_top) / 2, 0],
                [0.32, contact_top - contact_bottom, 0.32], "#e2ae61", shape="tapered_cylinder",
                profile_widths=[0.65, 0.82, 1.0],
                metadata={"process": "positive etch taper; wider opening, narrower bottom",
                          "epi_recess_nm": round(recess * nm, 2)},
                sub_label=f"{side} 콘택트" if x < 0 else None)
    notch, previous, inner_widths = [], 0.0, []
    for index in range(count):
        y, t, w = ys[index], thick[index], sheet_w[index]
        box("channel", f"나노시트 {index + 1}", [0, y, 0], [2 * sd_x - sd_width + 0.06, t, w], "#65c4dd",
            metadata={"sheet_index": index + 1, "width_nm": widths[index], "thickness_nm": layout["thickness"][index]},
            sub_label=f"NS{index + 1}")
        dielectric_height = t + 2 * HK_UNITS
        box("highk", f"High-k {index + 1}", [0, y, 0], [gate_max, dielectric_height, w + 2 * HK_UNITS],
            "#e8efe6", opacity=0.7, shape="gate_shell",
            metadata={"sheet_holes": [sheet_holes[index]], "hole_clearance": 0,
                      "base_y": y - dielectric_height / 2})
        # Inner gate i fills the gap directly under NS i (i=1: substrate..NS1).
        low, high = previous, y - t / 2 - HK_UNITS
        middle = (low + high) / 2
        local_cd = gate_cd_at(middle)
        explicit_width = params.get(f"inner_gate{index + 1}_width_nm")
        inner_width = min(explicit_width / nm, local_cd + 0.28) if explicit_width is not None else local_cd
        inner_depth = w + 2 * HK_UNITS
        inner_widths.append(round(inner_width * nm, 2))
        box("inner_gate", f"Inner gate {index + 1}", [0, middle, 0], [inner_width, high - low, inner_depth],
            "#aa80d5", opacity=0.82,
            metadata={"level": index + 1, "width_nm": round(inner_width * nm, 2),
                      "height_nm": round(layout["inner_gate_heights"][index], 2),
                      "position": "기판~NS1" if index == 0 else f"NS{index}~NS{index + 1}"},
            sub_label=f"IG{index + 1}")
        if inner_width < local_cd - 0.005:
            side_width = (local_cd - inner_width) / 2
            for sign in (-1, 1):
                box("spacer", f"Inner spacer {index + 1}", [sign * (inner_width / 2 + side_width / 2), middle, 0],
                    [side_width, high - low, inner_depth], "#e7c47f", opacity=0.75, use_profile=False,
                    metadata={"level": index + 1, "kind": "inner_spacer"})
        level_top = y + t / 2 + HK_UNITS
        notch.append((low, level_top, w / 2 + HK_UNITS))
        previous = level_top
    notch_top = previous
    half_depth = gate_depth / 2
    left = [point for low, high, half in notch for point in ((-half, low), (-half, high))]
    right = [point for low, high, half in notch for point in ((half, low), (half, high))]
    gate_metadata = {"TCD_nm": gate_cds["top"], "MCD_nm": gate_cds["middle"], "BCD_nm": gate_cds["bottom"],
                     "process": "replacement metal gate wrapping each nanosheet; inner gates fill the gaps"}
    if "gate" in material_stacks:
        wrap_width, wrap_profile = slab_widths(0.0, notch_top)
        box("gate", "게이트 측면 wrap", [0, notch_top / 2, 0], [wrap_width, notch_top, gate_depth], "#8f63c7",
            opacity=0.72, shape="gate_shell", profile_widths=wrap_profile, use_profile=False,
            metadata={**gate_metadata, "outlines": [
                _local_outline([(-half_depth, 0.0), *left, (-half_depth, notch_top)], notch_top / 2),
                _local_outline([*reversed(right), (half_depth, 0.0), (half_depth, notch_top)], notch_top / 2)]})
        fill_cd = gate_cd_at((notch_top + gate_top) / 2)
        material_layers["gate"] = fill_region(
            "gate", material_stacks["gate"], -fill_cd / 2, fill_cd / 2, notch_top, gate_top,
            -half_depth, half_depth, walls_z=False, role="gate", base_name="상부 게이트", remainder_color="#8f63c7")
    else:
        box("gate", "상부 게이트", [0, gate_top / 2, 0], [gate_max, gate_top, gate_depth], "#8f63c7", opacity=0.72,
            shape="gate_shell", profile_widths=gate_profile,
            metadata={**gate_metadata, "base_y": 0, "outlines": [_local_outline(
                [(-half_depth, 0.0), *left, *reversed(right), (half_depth, 0.0),
                 (half_depth, gate_top), (-half_depth, gate_top)], gate_top / 2)]},
            sub_label="Gate")
    for x in (-(gate_max + 0.16) / 2, (gate_max + 0.16) / 2):
        box("spacer", "Outer / inner spacer", [x, (gate_top - 0.02) / 2, 0],
            [0.12, gate_top - 0.02, epi_depth + 0.07], "#e7c47f", opacity=0.65,
            shape="gate_shell", metadata={"sheet_holes": sheet_holes, "base_y": 0})
    if "gate_contact" in material_stacks:
        material_layers["gate_contact"] = fill_region(
            "gate_contact", material_stacks["gate_contact"], -0.15, 0.15, gate_top, mol_y - 0.07, -0.15, 0.15,
            walls_z=True, role="contact", base_name="게이트 콘택트", remainder_color="#e2ae61")
    else:
        box("contact", "게이트 콘택트", [0, (gate_top + mol_y) / 2, 0],
            [0.3, mol_y - gate_top, 0.3], "#e2ae61", shape="tapered_cylinder", profile_widths=[0.68, 0.84, 1.0],
            sub_label="G 콘택트")
    columns = [("소스", -sd_x, sd_levels, not landing_pad), ("게이트", 0.0, gate_levels, not landing_pad),
               ("드레인", sd_x, sd_levels, not landing_pad)]
    column_tops = {}
    for name, x, levels, direct in columns:
        column_tops[name] = mol_y + (levels - 1) * mol_pitch
        if direct:
            bottom = mol_y - 0.07
            for level in range(1, levels + 1):
                upper = mol_y + (level - 1) * mol_pitch + (0.07 if level == 1 else 0.06)
                label = f"M{level - 1}"
                box("mol", f"MOL {label} {name} (직결)", [x, (bottom + upper) / 2, 0], [0.32, upper - bottom, 0.32],
                    MOL_LEVEL_COLORS[level], shape="cylinder",
                    metadata={"mol_level": level, "mol_kind": "line", "connection": "direct"},
                    sub_label=f"MOL {level}단" if x < 0 else None)
                bottom = upper
            continue
        box("mol", f"MOL M0 {name}", [x, mol_y, 0], [0.55, 0.14, 0.72], MOL_LEVEL_COLORS[1],
            metadata={"mol_level": 1, "mol_kind": "line"}, sub_label="MOL 1단" if x > 0 else None)
        for level in range(2, levels + 1):
            local_y = mol_y + (level - 1) * mol_pitch
            box("mol", f"MOL V{level - 1} {name}", [x, local_y - mol_pitch / 2, 0],
                [0.16, mol_pitch, 0.16], MOL_LEVEL_COLORS[level], shape="cylinder",
                metadata={"mol_level": level, "mol_kind": "via"})
            box("mol", f"MOL M{level - 1} {name}", [x, local_y, 0], [0.45, 0.12, 0.64], MOL_LEVEL_COLORS[level],
                metadata={"mol_level": level, "mol_kind": "line"}, sub_label=f"MOL {level}단" if x > 0 else None)
    mol_top = max(column_tops.values())
    m1_y = mol_top + 0.52
    m2_y = m1_y + 0.55
    for name, x, _, _ in columns:
        column_top = column_tops[name]
        box("beol", f"BEOL V0 {name}", [x, (column_top + m1_y) / 2, 0],
            [0.18, m1_y - column_top, 0.18], "#9bc5d9", shape="cylinder")
        box("beol", f"BEOL M1 {name}", [x, m1_y, 0], [0.2, 0.16, 2.0], "#8bc4e0")
    box("beol", "BEOL V1", [-sd_x, (m1_y + m2_y) / 2, 0.75], [0.18, m2_y - m1_y, 0.18], "#d4e3e9", shape="cylinder")
    box("beol", "BEOL M2", [0, m2_y, 0.75], [2.8, 0.16, 0.22], "#d4e3e9")
    landmarks = {"substrate_top": 0.0, "ns_stack_bottom": ns_bottom_y, "ns_stack_top": ns_top_y,
                 "gate_top": gate_top, "mol_m0_bottom": mol_y - 0.07,
                 "mol_m0_top": mol_y + 0.07, "beol_m1_bottom": m1_y - 0.08,
                 "sd_epi_top": source_top, "sd_contact_bottom": contact_bottom}
    measurements = {key: {**definition, "from_label": LANDMARK_LABELS[definition["from"]],
                          "to_label": LANDMARK_LABELS[definition["to"]],
                          "height_nm": round(abs(landmarks[definition["to"]] - landmarks[definition["from"]]) * nm, 2),
                          "anchor": dimension_anchors.get(key)}
                    for key, definition in config["dimensions"].items()}
    sub_annotations = []
    label_column = max(part["center"][0] + part["size"][0] / 2 for part in parts) + 0.45
    if view == "gaa" and kind == "logic":
        # One leader-lined column right of the device keeps opt-in names from piling up.
        for part in parts:
            if not part.get("sub_label"):
                continue
            front = [part["center"][0], part["center"][1], round(part["center"][2] + part["size"][2] / 2, 3)]
            sub_annotations.append({"text": part["sub_label"], "role": part["role"], "anchor": front,
                                    "position": [round(label_column, 3), front[1], front[2]], "align": "left",
                                    "color": "#e9f2ff"})
        _spread_column(sub_annotations, 0.24)
    topology = {"kind": "single GAA transistor"}
    netlist = []
    annotations = structure_topology.gaa_annotations(parts)
    references = list(structure_topology.GAA_REFERENCES)
    latchup_path = None
    if view == "latchup":
        assembled = structure_topology.build_latchup(parts)
        parts = assembled["parts"]
        topology, netlist = assembled["topology"], assembled["netlist"]
        annotations = assembled["annotations"]
        references.extend(assembled["references"])
        latchup_path = assembled["latchup_path"]
    elif kind == "sram":
        assembled = structure_topology.build_sram_6t(parts)
        parts = assembled["parts"]
        topology, netlist = assembled["topology"], assembled["netlist"]
        annotations = assembled["annotations"]
        references.extend(assembled["references"])
    inline_markers = []
    status_text = {"matched": "연결 확인", "missing": "매칭 없음"}
    if view == "gaa" and kind == "logic":
        x_base = -(sd_x + sd_extent / 2) - 0.4
        for index, (key, row) in enumerate(measurements.items()):
            x = round(x_base - index * 0.22, 3)
            low, high = landmarks[row["from"]], landmarks[row["to"]]
            anchor = row["anchor"]
            text = f"{row['label']} {row['height_nm']:g} nm · " + (
                f"{anchor['step_id']} / {anchor['item_id']} ({status_text[anchor['status']]})" if anchor else "미연결")
            inline_markers.append({"kind": "dimension", "id": key, "text": text,
                                   "status": anchor["status"] if anchor else "unlinked",
                                   "from": [x, round(low, 4), 0], "to": [x, round(high, 4), 0],
                                   "position": [round(x - 0.08, 3), round((low + high) / 2, 4), 0], "align": "right",
                                   "anchor": anchor})
        # Right-aligned labels extend leftward over neighbouring lines, so spread them as one column.
        _spread_column(inline_markers, 0.26)
    right_edge = label_column if view == "gaa" and kind == "logic" else (
        max((part["center"][0] + part["size"][0] / 2 for part in parts), default=1.5) + 0.35)
    role_markers = []
    for role, anchor in checked.items():
        part = next((item for item in parts if item["role"] == role), None)
        if not part:
            continue
        point = [part["center"][0], round(part["center"][1] + part["size"][1] / 2, 4), part["center"][2]]
        role_markers.append({"kind": "role", "id": role, "role": role, "status": anchor["status"],
                             "text": f"{ROLES[role]} · {anchor['step_id']} / {anchor['item_id']} ({status_text[anchor['status']]})",
                             "point": point, "position": [round(right_edge, 3), point[1], point[2]], "align": "left",
                             "anchor": anchor})
    inline_markers.extend(_spread_column(role_markers, 0.2))
    inner_gates = [{"index": i + 1, "position": "기판~NS1" if i == 0 else f"NS{i}~NS{i + 1}",
                    "height_nm": round(layout["inner_gate_heights"][i], 2),
                    "width_nm": inner_widths[i]}
                   for i in range(count)]
    centers = layout["centers_nm"]
    return {"schema": "flow.gaa.scene.v1", "view": view,
            "units": "illustrative scene units; nanosheet dimensions calibrated to nm only in GAA view", "product": product,
            "type": kind, "variant": variant, "parameters": params, "axes": {"x": "source to drain", "y": "vertical", "z": "cell height direction"},
            "relations": STRUCTURE_RELATIONS if view == "gaa" else [
                {"subject": "nwell", "predicate": "hosts", "object": "PMOS"},
                {"subject": "pwell", "predicate": "hosts", "object": "NMOS"},
                {"subject": "latch_path", "predicate": "conceptual_parasitic_path_between", "object": "nwell/pwell"}],
            "nanosheet_dimensions_nm": {"count_per_stack": count, "thickness": NANOSHEET_THICKNESS_NM,
                                         "vertical_gap": round(layout["inner_gate_heights"][1], 2) if count > 1 else None,
                                         "pair_gaps": [round(value, 2) for value in layout["inner_gate_heights"][1:]],
                                         "center_pitch": round(centers[1] - centers[0], 2) if count > 1 else round(space * nm, 2),
                                         "width": round(width * nm, 2),
                                         "active_stack_height": round(layout["top_nm"] - layout["inner_gate_heights"][0], 2),
                                         "sheets": [{"index": i, "width": widths[i - 1], "thickness": layout["thickness"][i - 1]}
                                                    for i in range(1, count + 1)],
                                         "inner_gates": inner_gates},
            "gate_profile_nm": gate_cds, "shape_profiles": shape_profiles,
            "material_stacks": material_stacks, "material_layers": material_layers,
            "material_regions": MATERIAL_REGIONS, "material_modes": MATERIAL_MODES,
            "measurements": measurements if view == "gaa" else {},
            "landmarks": {key: round(value, 4) for key, value in landmarks.items()} if view == "gaa" else {},
            "landmark_labels": LANDMARK_LABELS,
            "mol_level_count": sd_levels, "gate_mol_level_count": gate_levels,
            "mol_connection": "landing_pad" if landing_pad else "direct",
            "contact_epi_recess_nm": round(recess * nm, 2),
            "spacing_nm": {"gate_to_sd": round((sd_x - sd_extent / 2 - gate_max / 2) * nm, 2),
                           "sdb_width": round(params.get("sdb_width_nm", 5.2), 2)},
            "dimension_reference": {"url": NANOSHEET_SOURCE,
                                    "note": "IBM/Samsung/GF 2017 3-sheet example: 5 nm thickness, 10 nm vertical gap, 15–45 nm width; default width 40 nm is a representative choice."},
            "layer_order": ["FEOL: nanosheet, inner gate (under each sheet)", "FEOL: upper gate",
                            "MOL: contact (optionally recessed into epi) and M0..M3", "BEOL: V0, M1, V1, M2"],
            "roles": ROLES, "parts": parts, "anchors": checked,
            "topology": topology, "netlist": netlist,
            "annotations": annotations, "references": references,
            "sub_annotations": sub_annotations, "inline_markers": inline_markers,
            "dimension_anchors": dimension_anchors,
            "latchup_path": latchup_path,
            "anchor_candidates": candidates[:3000] if include_candidates else [], "anchor_candidate_count": len(candidates),
            "warnings": [f"{role}: 해당 제품의 INLINE 매칭에서 앵커를 찾지 못했습니다." for role, value in checked.items() if value["status"] == "missing"]
                        + [f"{key}: 해당 제품의 INLINE 매칭에서 측정 구간 앵커를 찾지 못했습니다." for key, value in dimension_anchors.items() if value["status"] == "missing"],
            "model_source": "관리자 정의 개념 모델; 실측 치수/공정 순서를 의미하지 않음"}
