"""Circuit-level scene topology assembled from one generated GAA device."""
from __future__ import annotations

from copy import deepcopy


SRAM_REFERENCE = "https://cornell-ece5745.github.io/ece5745-tut8-sram/"
LATCHUP_REFERENCES = [
    "https://www.ti.com/lit/an/scla007a/scla007a.pdf",
    "https://www.ti.com/content/dam/videos/external-videos/en-us/3/3816841626001/6018870011001.mp4/subassets/spaceseriespart4_1.pdf",
]
GAA_REFERENCES = [
    {"title": "IBM 2 nm nanosheet technology",
     "url": "https://research.ibm.com/blog/2-nm-chip",
     "note": "Published stacked nanosheet GAA cross-section and technology context."},
    {"title": "Raised source/drain epitaxy patent example",
     "url": "https://patents.google.com/patent/US20200098879A1/en",
     "note": "Illustrative faceted raised epitaxy; the 54.7° {111}/{001} angle is not universal."},
    {"title": "Growth-faceting TEM crystallography",
     "url": "https://www.beilstein-journals.org/bjnano/articles/5/246",
     "note": "Ge-island facet crystallography reference only; it is not a GAA source/drain shape calibration."},
]

_NET_COLORS = {
    "VDD": "#efb85a", "VSS": "#9aa7b8", "WL": "#b888e0",
    "BL": "#4db7d5", "BLB": "#7acde1", "Q": "#ec7f68", "QB": "#77bf83",
}


def gaa_annotations(parts):
    """Return world-space labels for the single generated GAA device."""
    source_part = next((part for part in parts if part["role"] == "source"), None)
    source = list(source_part["center"]) if source_part else [0, 0, 0]
    gate_part = next((part for part in parts if part["role"] == "gate"), None)
    gate = list(gate_part["center"]) if gate_part else [0, 0, 0]
    channel_part = next((part for part in parts if part["role"] == "channel"), None)
    channel = list(channel_part["center"]) if channel_part else [0, 0, 0]
    contact_part = next((part for part in parts
                         if part["role"] == "contact" and "소스" in part.get("name", "")), None)
    contact = list(contact_part["center"]) if contact_part else [0, 0, 0]
    source_metadata = source_part.get("metadata", {}) if source_part else {}
    material = source_metadata.get("material", "S/D").split(" (")[0]
    facet_angle = source_metadata.get("facet_angle_deg")
    if source_part and source_part.get("shape") == "faceted_epi" and facet_angle is not None:
        facet = "{111} " if abs(float(facet_angle) - 54.7356) < 0.1 else ""
        source_label = f"{material} epi · {facet}{float(facet_angle):.2f}°"
    else:
        source_label = f"{material} epi · {source_part.get('shape', 'custom shape') if source_part else 'custom shape'}"
    gate_top = gate[1] + (gate_part["size"][1] / 2 if gate_part else 0) + 0.35
    channel_front = channel[2] + (channel_part["size"][2] / 2 if channel_part else 0) + 0.65
    return [
        {"text": source_label, "position": [source[0] - 0.9, source[1], source[2]],
         "anchor": source, "role": "source"},
        {"text": "gate wraps sheets" if gate_part and gate_part["shape"] == "gate_shell" else "gate profile", "position": [gate[0], gate_top, gate[2]],
         "anchor": gate, "role": "gate"},
        {"text": "stacked nanosheets", "position": [channel[0], channel[1], channel_front],
         "anchor": channel, "role": "channel"},
        {"text": "contact etch taper" if contact_part and contact_part["shape"] == "tapered_cylinder" else "contact profile", "position": [contact[0] - 0.8, contact[1] + 0.35, contact[2]],
         "anchor": contact, "role": "contact"},
    ]


def _rounded(values):
    return [round(float(value), 3) for value in values]


def _raw_part(parts, role, name, center, size, color, *, stage="BEOL", opacity=1,
              shape="box", metadata=None):
    """Add topology geometry without applying editable per-role CD overrides."""
    if any(float(value) <= 0 for value in size):
        raise ValueError(f"{name}: topology dimensions must be positive")
    part = {
        "id": f"topology-{len(parts)}", "role": role, "stage": stage,
        "name": name, "shape": shape, "center": _rounded(center),
        "size": _rounded(size), "color": color, "opacity": opacity,
        "anchor": None,
    }
    if metadata:
        part["metadata"] = metadata
    parts.append(part)
    return part


def _terminal_from_part(part):
    name = part.get("name", "").lower()
    if part.get("role") == "source" or "소스" in name or "source" in name:
        return "S"
    if part.get("role") == "drain" or "드레인" in name or "drain" in name:
        return "D"
    if part.get("role") in {"gate", "inner_gate"} or "게이트" in name or "gate" in name:
        return "G"
    return None


def _scaled_metadata(metadata, scale):
    copied = deepcopy(metadata or {})
    sx, sy, sz = scale
    if "cap_height" in copied:
        copied["cap_height"] = round(float(copied["cap_height"]) * sy, 4)
    if "base_y" in copied:
        copied["base_y"] = round(float(copied["base_y"]) * sy, 4)
    if isinstance(copied.get("sheet_holes"), list):
        copied["sheet_holes"] = [
            {**hole,
             "y": round(float(hole["y"]) * sy, 4),
             "thickness": round(float(hole["thickness"]) * sy, 4),
             "width": round(float(hole["width"]) * sz, 4)}
            for hole in copied["sheet_holes"]
        ]
    if isinstance(copied.get("outlines"), list):
        # Gate outlines are [z, y] points local to the part centre.
        copied["outlines"] = [[[round(float(z) * sz, 4), round(float(y) * sy, 4)] for z, y in outline]
                              for outline in copied["outlines"]]
    return copied


def _clone_device(template, specification, *, scale=(1.0, 1.0, 1.0),
                  include_roles=None):
    """Clone one device, retaining anchors and dimension-aware shell metadata."""
    sx, sy, sz = scale
    ox, oy, oz = specification["position"]
    parts = []
    terminal_points = {}
    mol_y = None
    for original in template:
        if include_roles is not None and original["role"] not in include_roles:
            continue
        part = deepcopy(original)
        part["id"] = f"{specification['id']}-{len(parts)}"
        part["name"] = f"{specification['id']} · {original['name']}"
        part["center"] = _rounded([
            ox + original["center"][0] * sx,
            oy + original["center"][1] * sy,
            oz + original["center"][2] * sz,
        ])
        part["size"] = _rounded([
            original["size"][0] * sx,
            original["size"][1] * sy,
            original["size"][2] * sz,
        ])
        terminal = _terminal_from_part(original)
        metadata = _scaled_metadata(original.get("metadata"), scale)
        metadata.update({
            "device_id": specification["id"],
            "device_type": specification["type"],
            "device_role": specification["role"],
        })
        if terminal:
            metadata["terminal"] = terminal
            metadata["net"] = specification["terminals"][terminal]
        part["metadata"] = metadata
        if specification["type"] == "pmos" and original["role"] in {"source", "drain"}:
            part["color"] = "#e3a2ad"
            part["metadata"]["dopant"] = "P+"
            part["metadata"]["material"] = "SiGe:B"
        elif specification["type"] == "nmos" and original["role"] in {"source", "drain"}:
            part["color"] = "#72c5d9"
            part["metadata"]["dopant"] = "N+"
            part["metadata"]["material"] = "Si:P"
        parts.append(part)
        if original["role"] == "mol" and original.get("metadata", {}).get("mol_kind") == "line" and terminal:
            level = original.get("metadata", {}).get("mol_level", 1)
            if mol_y is None or part["center"][1] > mol_y:
                mol_y = part["center"][1]
            current = terminal_points.get(terminal)
            if current is None or level >= current[0]:
                terminal_points[terminal] = (level, list(part["center"]))
    return parts, {terminal: point for terminal, (_, point) in terminal_points.items()}, mol_y


def _route_network(parts, terminals, base_y):
    """Route each net on its own illustrative metal height."""
    net_order = ["VSS", "Q", "QB", "WL", "BL", "BLB", "VDD"]
    levels = {net: round(base_y + 0.42 + index * 0.31, 3)
              for index, net in enumerate(net_order)}
    # Source terminals occupy z=-2/0/+2 for VDD/VSS/bitlines. Route each
    # outward from its own row so a vertical via never pierces another net's
    # horizontal branch. Drain/gate routes have distinct x tracks.
    lane_z = {"VSS": -0.75, "Q": -1.2, "QB": -1.2, "WL": 3.2,
              "BL": 3.65, "BLB": 3.65, "VDD": -3.2}
    terminal_escape = {"S": -0.42, "G": 0.42, "D": 0.42}
    net_parts = {net: [] for net in net_order}
    for net in net_order:
        points = terminals[net]
        level = levels[net]
        lane = lane_z[net]
        role = "bitline" if net in {"BL", "BLB"} else "beol"
        color = _NET_COLORS[net]
        escaped_points = []
        for device_id, terminal, point in points:
            via_x = point[0] + terminal_escape[terminal]
            escape_length = abs(via_x - point[0])
            net_parts[net].append(_raw_part(
                parts, role, f"{net} escape · {device_id}.{terminal}",
                [(point[0] + via_x) / 2, point[1], point[2]],
                [escape_length + 0.12, 0.1, 0.11], color,
                metadata={"net": net, "routing_level": point[1], "kind": "escape",
                          "device_id": device_id, "terminal": terminal}))
            via_height = level - point[1]
            net_parts[net].append(_raw_part(
                parts, role, f"{net} via · {device_id}.{terminal}",
                [via_x, point[1] + via_height / 2, point[2]], [0.12, via_height, 0.12], color,
                shape="cylinder", metadata={"net": net, "routing_level": level, "kind": "via",
                                             "device_id": device_id, "terminal": terminal}))
            branch_length = abs(lane - point[2])
            if branch_length:
                net_parts[net].append(_raw_part(
                    parts, role, f"{net} branch · {device_id}.{terminal}",
                    [via_x, level, (point[2] + lane) / 2], [0.11, 0.1, branch_length], color,
                    metadata={"net": net, "routing_level": level, "kind": "branch"}))
            escaped_points.append(via_x)
        xs = escaped_points
        left, right = min(xs) - 0.42, max(xs) + 0.42
        net_parts[net].append(_raw_part(
            parts, role, f"{net} trunk", [(left + right) / 2, level, lane],
            [right - left, 0.1, 0.12], color,
            metadata={"net": net, "routing_level": level, "kind": "trunk"}))
    return levels, net_parts, lane_z


def build_sram_6t(template_parts):
    """Build a true 6T bitcell from one GAA transistor scene template."""
    device_roles = {"substrate", "rx", "source", "drain", "channel", "inner_gate",
                    "gate", "highk", "spacer", "contact", "mol"}
    specifications = [
        {"id": "PU_Q", "type": "pmos", "role": "pull_up", "position": [-2.25, 0, -2.0],
         "terminals": {"S": "VDD", "D": "Q", "G": "QB", "B": "VDD"}},
        {"id": "PD_Q", "type": "nmos", "role": "pull_down", "position": [-2.25, 0, 0],
         "terminals": {"S": "VSS", "D": "Q", "G": "QB", "B": "VSS"}},
        {"id": "PG_Q", "type": "nmos", "role": "access", "position": [-2.25, 0, 2.0],
         "terminals": {"S": "BL", "D": "Q", "G": "WL", "B": "VSS"}},
        {"id": "PU_QB", "type": "pmos", "role": "pull_up", "position": [2.25, 0, -2.0],
         "terminals": {"S": "VDD", "D": "QB", "G": "Q", "B": "VDD"}},
        {"id": "PD_QB", "type": "nmos", "role": "pull_down", "position": [2.25, 0, 0],
         "terminals": {"S": "VSS", "D": "QB", "G": "Q", "B": "VSS"}},
        {"id": "PG_QB", "type": "nmos", "role": "access", "position": [2.25, 0, 2.0],
         "terminals": {"S": "BLB", "D": "QB", "G": "WL", "B": "VSS"}},
    ]
    parts = []
    terminals = {net: [] for net in _NET_COLORS}
    base_y = 0.0
    for specification in specifications:
        cloned, terminal_points, mol_y = _clone_device(
            template_parts, specification, include_roles=device_roles)
        parts.extend(cloned)
        base_y = max(base_y, mol_y or 0.0)
        for terminal, net in specification["terminals"].items():
            if terminal in terminal_points:
                terminals[net].append((specification["id"], terminal, terminal_points[terminal]))
    routing_levels, net_parts, lane_z = _route_network(parts, terminals, base_y)
    netlist = [{key: deepcopy(specification[key]) for key in ("id", "type", "role", "terminals")}
               for specification in specifications]
    annotations = [
        {"text": net, "position": [net_parts[net][-1]["center"][0], routing_levels[net] + 0.18, lane_z[net]],
         "color": _NET_COLORS[net], "role": "bitline" if net in {"BL", "BLB"} else "beol"}
        for net in ("VDD", "VSS", "WL", "BL", "BLB", "Q", "QB")
    ]
    annotations.extend(
        {"text": specification["id"],
         "position": [specification["position"][0], base_y + 0.2, specification["position"][2]],
         "anchor": [specification["position"][0], 0.55, specification["position"][2]],
         "color": "#e3a2ad" if specification["type"] == "pmos" else "#72c5d9",
         "role": "channel"}
        for specification in specifications)
    topology = {
        "kind": "6T SRAM bitcell",
        "transistors": netlist,
        "nets": {net: {
            "routing_level": routing_levels[net],
            "terminals": [{"device": device, "terminal": terminal}
                          for device, terminal, _ in terminals[net]],
            "part_ids": [part["id"] for part in net_parts[net]],
        } for net in routing_levels},
        "cross_coupled_nodes": {"Q": "drives PU_QB/PD_QB gates", "QB": "drives PU_Q/PD_Q gates"},
    }
    return {"parts": parts, "topology": topology, "netlist": netlist,
            "annotations": annotations,
            "references": [{"title": "Cornell ECE 5745 SRAM tutorial", "url": SRAM_REFERENCE,
                            "note": "6T cell netlist: cross-coupled inverters, two access NMOS, WL, BL and BLB."}]}


def build_latchup(template_parts, *, include_guard_ring=True):
    """Build complementary GAA devices over adjacent wells and their parasitic SCR path."""
    device_roles = {"source", "drain", "channel", "inner_gate", "gate", "highk", "spacer", "contact", "mol"}
    devices = [
        {"id": "PMOS", "type": "pmos", "role": "complementary_device", "position": [-2.0, 0, 0],
         "terminals": {"S": "VDD", "D": "OUT", "G": "IN", "B": "VDD"}},
        {"id": "NMOS", "type": "nmos", "role": "complementary_device", "position": [2.0, 0, 0],
         "terminals": {"S": "VSS", "D": "OUT", "G": "IN", "B": "VSS"}},
    ]
    parts = []
    _raw_part(parts, "substrate", "P형 기판", [0, -1.15, 0], [9.0, 1.3, 4.2], "#53647a",
              stage="FEOL", metadata={"material": "P-type substrate"})
    _raw_part(parts, "nwell", "N-Well · PMOS 영역", [-2.0, -0.43, 0], [3.8, 0.85, 3.4],
              "#6885bc", stage="FEOL", opacity=0.68, metadata={"hosts": "PMOS", "body_net": "VDD"})
    _raw_part(parts, "pwell", "P-Well · NMOS 영역", [2.0, -0.43, 0], [3.8, 0.85, 3.4],
              "#b8809a", stage="FEOL", opacity=0.68, metadata={"hosts": "NMOS", "body_net": "VSS"})
    _raw_part(parts, "field", "중앙 STI", [0, 0.06, 0], [0.24, 0.5, 3.5], "#d5dce3",
              stage="FEOL", opacity=0.75, metadata={"isolation": "STI", "separates": ["N-Well", "P-Well"]})
    terminal_points = {}
    mol_y = 0.0
    for device in devices:
        cloned, points, local_mol_y = _clone_device(
            template_parts, device, scale=(0.86, 0.86, 0.86), include_roles=device_roles)
        parts.extend(cloned)
        terminal_points[device["id"]] = points
        mol_y = max(mol_y, local_mol_y or 0.0)

    rail_y = mol_y + 0.75
    rail_levels = {"VDD": rail_y, "VSS": rail_y + 0.32}
    rail_lanes = {"VDD": -1.62, "VSS": 1.62}
    tap_specs = [
        (-3.65, "N+ N-Well tap → VDD", "N+", "VDD", "#72c5d9"),
        (3.65, "P+ P-Well tap → VSS", "P+", "VSS", "#e3a2ad"),
    ]
    for x, name, dopant, net, color in tap_specs:
        level, lane = rail_levels[net], rail_lanes[net]
        tap_top = 0.1
        _raw_part(parts, "well_tap", name, [x, -0.05, 0], [0.4, 0.3, 0.72], color,
                  stage="FEOL", shape="faceted_epi", metadata={"dopant": dopant, "net": net})
        _raw_part(parts, "contact", f"{name} contact", [x, (tap_top + level) / 2, 0],
                  [0.22, level - tap_top, 0.22], "#e2ae61", stage="MOL", shape="cylinder",
                  metadata={"net": net, "connects": "well tap to rail"})
        _raw_part(parts, "beol", f"{name} rail branch", [x, level, lane / 2],
                  [0.12, 0.1, abs(lane)], _NET_COLORS[net],
                  metadata={"net": net, "kind": "tap_branch"})
    _raw_part(parts, "beol", "VDD rail", [-2.35, rail_y, -1.62], [4.0, 0.14, 0.2], _NET_COLORS["VDD"],
              metadata={"net": "VDD", "kind": "power_rail"})
    _raw_part(parts, "beol", "VSS rail", [2.35, rail_y + 0.32, 1.62], [4.0, 0.14, 0.2], _NET_COLORS["VSS"],
              metadata={"net": "VSS", "kind": "power_rail"})
    for device_id, terminal, net, level, lane in (
            ("PMOS", "S", "VDD", rail_y, -1.62), ("NMOS", "S", "VSS", rail_y + 0.32, 1.62)):
        point = terminal_points[device_id][terminal]
        _raw_part(parts, "beol", f"{device_id} source → {net}", [point[0], level, (point[2] + lane) / 2],
                  [0.12, 0.1, abs(lane - point[2]) or 0.12], _NET_COLORS[net],
                  metadata={"net": net, "device_id": device_id, "terminal": terminal, "kind": "branch"})
        _raw_part(parts, "beol", f"{device_id} source via", [point[0], (point[1] + level) / 2, point[2]],
                  [0.12, level - point[1], 0.12], _NET_COLORS[net], shape="cylinder",
                  metadata={"net": net, "device_id": device_id, "terminal": terminal, "kind": "via"})

    if include_guard_ring:
        ring_specs = [(-2.0, "VDD", "N+"), (2.0, "VSS", "P+")]
        for center_x, net, dopant in ring_specs:
            for side, x, z, size in (
                    ("left", center_x - 1.7, 0, [0.18, 0.2, 2.95]),
                    ("right", center_x + 1.7, 0, [0.18, 0.2, 2.95]),
                    ("front", center_x, -1.42, [3.58, 0.2, 0.18]),
                    ("back", center_x, 1.42, [3.58, 0.2, 0.18])):
                _raw_part(parts, "guard_ring", f"{dopant} guard ring {side} · {net}",
                          [x, 0.0, z], size, _NET_COLORS[net], stage="FEOL", opacity=0.85,
                          metadata={"net": net, "dopant": dopant, "side": side,
                                    "function": "collect injected charge"})
            contact_x = center_x - 1.7 if net == "VDD" else center_x + 1.7
            contact_z = -1.42 if net == "VDD" else 1.42
            level, lane = rail_levels[net], rail_lanes[net]
            _raw_part(parts, "guard_ring", f"{dopant} guard-ring contact · {net}",
                      [contact_x, (0.1 + level) / 2, contact_z], [0.18, level - 0.1, 0.18],
                      "#e2ae61", stage="MOL", shape="cylinder",
                      metadata={"net": net, "dopant": dopant, "kind": "contact"})
            _raw_part(parts, "guard_ring", f"{dopant} guard-ring rail branch · {net}",
                      [contact_x, level, (contact_z + lane) / 2],
                      [0.11, 0.1, abs(lane - contact_z)], _NET_COLORS[net],
                      metadata={"net": net, "dopant": dopant, "kind": "rail_branch"})

    p_diffusion_x = terminal_points["PMOS"]["D"][0]
    n_diffusion_x = terminal_points["NMOS"]["S"][0]
    path_z = terminal_points["PMOS"]["D"][2]
    path_nodes = [
        [p_diffusion_x, -0.18, path_z], [-0.45, -0.18, path_z],
        [-0.45, -0.5, path_z], [0.45, -0.5, path_z],
        [0.45, -0.18, path_z], [n_diffusion_x, -0.18, path_z],
    ]
    for index, (start, end) in enumerate(zip(path_nodes, path_nodes[1:]), 1):
        parasitic = "PNP" if index <= 3 else "NPN"
        direction = ("P+ emitter → N-Well base → P-substrate collector" if parasitic == "PNP"
                     else "N-Well collector → P-Well base → N+ emitter")
        delta_x, delta_y = abs(end[0] - start[0]), abs(end[1] - start[1])
        size = [delta_x or 0.08, delta_y or 0.08, 0.1]
        center = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2, path_z]
        _raw_part(parts, "latch_path", f"{parasitic} path {index}", center, size,
                  "#ec764e", stage="FEOL",
                  metadata={"sequence": index, "parasitic": parasitic, "direction": direction,
                            "from": _rounded(start), "to": _rounded(end),
                            "path": "P+ → N-Well → P-Well/P-substrate → N+"})

    annotations = [
        {"text": "P+", "position": [p_diffusion_x - 0.55, 0.55, 0],
         "anchor": [p_diffusion_x, 0.25, 0], "color": "#e3a2ad", "role": "drain"},
        {"text": "N+", "position": [n_diffusion_x + 0.55, 0.55, 0],
         "anchor": [n_diffusion_x, 0.25, 0], "color": "#72c5d9", "role": "source"},
        {"text": "N-Well", "position": [-2.0, -0.35, 1.45], "color": "#8fa8d6", "role": "nwell"},
        {"text": "P-Well", "position": [2.0, -0.35, 1.45], "color": "#d39ab2", "role": "pwell"},
        {"text": "PNP", "position": [-0.8, -0.18, path_z], "color": "#ec764e", "role": "latch_path"},
        {"text": "NPN", "position": [0.8, -0.58, path_z], "color": "#ec764e", "role": "latch_path"},
        {"text": "VDD", "position": [-2.35, rail_y + 0.2, -1.62],
         "color": _NET_COLORS["VDD"], "role": "beol"},
        {"text": "VSS", "position": [2.35, rail_y + 0.52, 1.62],
         "color": _NET_COLORS["VSS"], "role": "beol"},
    ]
    latchup_path = {
        "sequence": ["P+ PMOS", "N-Well", "P-Well/P형 기판", "N+ NMOS"],
        "meaning": "기생 PNP와 NPN이 결합하는 개념 경로; trigger/holding 수치 예측이 아님",
        "hotspots": [
            {"id": "injection", "label": "P+ / N-Well 주입 접합", "roles": ["source", "nwell"],
             "reason": "전류 주입이 기생 PNP 경로를 시작할 수 있음"},
            {"id": "well_boundary", "label": "N-Well / P-Well 경계", "roles": ["nwell", "pwell", "latch_path"],
             "reason": "well·기판 저항을 통한 전압 강하가 기생 NPN/PNP 결합에 관여"},
            {"id": "tap", "label": "Well / substrate tap", "roles": ["well_tap", "guard_ring"],
             "reason": "탭과 guard ring이 주입 전류의 저저항 귀환 경로를 제공"}],
        "reference": LATCHUP_REFERENCES[0],
        "references": LATCHUP_REFERENCES,
        "model": "qualitative topology only; no calibrated trigger current, holding current, or failure probability",
    }
    topology = {
        "kind": "complementary GAA latch-up cross-section",
        "devices": [{key: deepcopy(device[key]) for key in ("id", "type", "role", "terminals")}
                    for device in devices],
        "parasitics": [
            {"id": "PNP", "emitter": "P+ PMOS diffusion", "base": "N-Well", "collector": "P-Well/P-substrate"},
            {"id": "NPN", "emitter": "N+ NMOS diffusion", "base": "P-Well/P-substrate", "collector": "N-Well"},
        ],
        "guard_ring": {"enabled": include_guard_ring, "role": "guard_ring"},
    }
    return {"parts": parts, "topology": topology, "netlist": topology["devices"],
            "annotations": annotations, "latchup_path": latchup_path,
            "references": [
                {"title": "TI CMOS latch-up application report", "url": LATCHUP_REFERENCES[0],
                 "note": "Figures 16–17 motivate the parasitic PNP/NPN and well/tap cross-section."},
                {"title": "TI radiation effects presentation", "url": LATCHUP_REFERENCES[1],
                 "note": "Latch-up and charge-collection context for complementary CMOS structures."},
            ]}
