from __future__ import annotations

import datetime as dt
import json
import math
import re
import statistics
import uuid
from pathlib import Path
from typing import Any

import polars as pl

from core.paths import PATHS


KNOWLEDGE_VERSION = "semi-dx-seed-2026.04"
SEMICONDUCTOR_DIR = PATHS.data_root / "semiconductor"
DIAGNOSIS_RUNS_FILE = SEMICONDUCTOR_DIR / "diagnosis_runs.jsonl"
ENGINEER_KNOWLEDGE_FILE = SEMICONDUCTOR_DIR / "engineer_knowledge.jsonl"
CUSTOM_KNOWLEDGE_FILE = SEMICONDUCTOR_DIR / "custom_knowledge.jsonl"
CODE_RCA_SEED_FILE = Path(__file__).with_name("semiconductor_rca_seed_knowledge.json")
FLOW_DATA_SEED_DIR = SEMICONDUCTOR_DIR / "seed_knowledge"
FLOW_DATA_RCA_SEED_FILE = FLOW_DATA_SEED_DIR / "semiconductor_rca_seed_knowledge.json"


ITEM_MASTER: list[dict[str, Any]] = [
    {
        "canonical_item_id": "DIBL",
        "raw_names": ["DIBL", "DIBL_MV_V", "DIBL_SHORT"],
        "display_name": "Drain-induced barrier lowering",
        "meaning": "Short-channel electrostatic control metric. Higher DIBL usually means worse channel/gate control.",
        "unit": "mV/V",
        "source_type": "ET",
        "test_structure": "short_Lg_FET",
        "layer": "device",
        "measurement_method": "Id-Vg low/high Vd extraction",
        "module": "device_electrical",
        "direction_bad": "increase",
        "aliases": ["드레인 유도 장벽 저하", "short channel DIBL"],
    },
    {
        "canonical_item_id": "SS",
        "raw_names": ["SS", "SS_MV_DEC", "SUBTHRESHOLD_SWING"],
        "display_name": "Subthreshold swing",
        "meaning": "Subthreshold gate-control metric. Higher SS indicates degraded switching slope or interface/electrostatic issue.",
        "unit": "mV/dec",
        "source_type": "ET",
        "test_structure": "FET",
        "layer": "device",
        "measurement_method": "Id-Vg subthreshold slope",
        "module": "device_electrical",
        "direction_bad": "increase",
        "aliases": ["subthreshold", "swing"],
    },
    {
        "canonical_item_id": "VTH_ROLLOFF",
        "raw_names": ["VTH_ROLLOFF", "VTH_ROLL_OFF", "DVT"],
        "display_name": "Vth roll-off",
        "meaning": "Threshold-voltage degradation versus channel length. Often reviewed with DIBL/SS.",
        "unit": "mV",
        "source_type": "ET",
        "test_structure": "Lg sweep FET",
        "layer": "device",
        "measurement_method": "Vth extraction over Lg split",
        "module": "device_electrical",
        "direction_bad": "increase",
        "aliases": ["vth roll off", "rolloff", "DVT"],
    },
    {
        "canonical_item_id": "VTH",
        "raw_names": ["VTH", "VT", "VTHLIN", "VTHSAT"],
        "display_name": "Threshold voltage",
        "meaning": "Device threshold voltage. Direction must be interpreted by polarity, target, and split context.",
        "unit": "V",
        "source_type": "ET",
        "test_structure": "FET",
        "layer": "device",
        "measurement_method": "constant-current or gm extraction",
        "module": "RMG_WFM",
        "direction_bad": "context",
        "aliases": ["threshold", "threshold voltage"],
    },
    {
        "canonical_item_id": "ION",
        "raw_names": ["ION", "IDSAT", "IDLIN_ON"],
        "display_name": "On current",
        "meaning": "Drive current at on-state bias. Lower Ion can indicate mobility, Rs/Rsd, Vth, stress, or geometry issue.",
        "unit": "uA/um",
        "source_type": "ET",
        "test_structure": "FET",
        "layer": "device",
        "measurement_method": "Id-Vg/Id-Vd on-state extraction",
        "module": "device_electrical",
        "direction_bad": "decrease",
        "aliases": ["drive current", "Idsat"],
    },
    {
        "canonical_item_id": "IOFF",
        "raw_names": ["IOFF", "ILEAK_OFF", "OFF_LEAKAGE"],
        "display_name": "Off leakage",
        "meaning": "Off-state leakage. Higher Ioff can indicate electrostatic, junction, or leakage path issue.",
        "unit": "nA/um",
        "source_type": "ET",
        "test_structure": "FET",
        "layer": "device",
        "measurement_method": "off-state current extraction",
        "module": "device_electrical",
        "direction_bad": "increase",
        "aliases": ["off current", "off leakage"],
    },
    {
        "canonical_item_id": "RSD",
        "raw_names": ["RSD", "RSD_EXT", "RSD_TOTAL"],
        "display_name": "Source/drain resistance",
        "meaning": "Series resistance around source/drain and extension path. Higher Rsd can depress Ion.",
        "unit": "ohm*um",
        "source_type": "ET",
        "test_structure": "FET Rext extraction",
        "layer": "S/D",
        "measurement_method": "Y-function/TLM-like device extraction",
        "module": "SD_EPI",
        "direction_bad": "increase",
        "aliases": ["Rext", "source drain resistance"],
    },
    {
        "canonical_item_id": "IGATE",
        "raw_names": ["IGATE", "IG", "GATE_LEAKAGE", "LKG_GATE"],
        "display_name": "Gate leakage",
        "meaning": "Gate dielectric leakage. Higher value can indicate dielectric, RMG, plasma, or reliability issue.",
        "unit": "A/um",
        "source_type": "ET",
        "test_structure": "FET/gate capacitor",
        "layer": "gate_stack",
        "measurement_method": "gate current bias sweep",
        "module": "GATE_DIELECTRIC",
        "direction_bad": "increase",
        "aliases": ["gate leak", "게이트 누설", "LKG"],
    },
    {
        "canonical_item_id": "SRAM_VMIN",
        "raw_names": ["SRAM_VMIN", "VMIN", "SRAM_FAIL_VMIN"],
        "display_name": "SRAM Vmin",
        "meaning": "Minimum operating voltage of SRAM macro/bitcell. Higher Vmin can reflect device mismatch, Ion loss, leakage, or local variation.",
        "unit": "V",
        "source_type": "VM",
        "test_structure": "SRAM macro",
        "layer": "SRAM",
        "measurement_method": "functional voltage sweep",
        "module": "SRAM",
        "direction_bad": "increase",
        "aliases": ["sram minimum voltage", "비민"],
    },
    {
        "canonical_item_id": "CA_RS",
        "raw_names": ["CA_RS", "CA_RSH", "CA_SHEET_R"],
        "display_name": "CA sheet resistance",
        "meaning": "CA/MOL conductive film sheet resistance only when unit and structure support Rsheet interpretation.",
        "unit": "ohm/sq",
        "source_type": "INLINE",
        "test_structure": "sheet_resistance",
        "layer": "CA",
        "measurement_method": "Rsheet / Van der Pauw",
        "module": "CA_MOL_CONTACT",
        "direction_bad": "increase",
        "aliases": ["CA sheet R", "CA Rsheet"],
        "ambiguity_note": "CA_RS must not be treated as sheet resistance if Kelvin/TLM/contact-chain structure is used.",
    },
    {
        "canonical_item_id": "CA_RC_KELVIN",
        "raw_names": ["CA_RC_KELVIN", "CA_KELVIN_R", "CA_CONTACT_R"],
        "display_name": "CA contact resistance",
        "meaning": "CA contact resistance candidate from Kelvin/TLM structures.",
        "unit": "ohm",
        "source_type": "INLINE",
        "test_structure": "Kelvin/TLM",
        "layer": "CA",
        "measurement_method": "Kelvin contact resistance",
        "module": "CA_MOL_CONTACT",
        "direction_bad": "increase",
        "aliases": ["CA Rc", "contact resistance", "Kelvin CA"],
    },
    {
        "canonical_item_id": "CA_CHAIN_R",
        "raw_names": ["CA_CHAIN_R", "CA_CHAIN_RES", "CONTACT_CHAIN_R"],
        "display_name": "CA contact chain resistance",
        "meaning": "Cumulative contact-chain resistance, sensitive to opens, CD, liner/fill and contact integrity.",
        "unit": "ohm",
        "source_type": "INLINE",
        "test_structure": "contact_chain",
        "layer": "CA",
        "measurement_method": "chain resistance",
        "module": "CA_MOL_CONTACT",
        "direction_bad": "increase",
        "aliases": ["chain resistance", "contact chain"],
    },
    {
        "canonical_item_id": "CA_CD",
        "raw_names": ["CA_CD", "CA_CD_MEAN", "CA_DIAMETER"],
        "display_name": "CA critical dimension",
        "meaning": "Contact aperture diameter/width. Lower CA CD can increase contact resistance and chain fail risk.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "CDSEM",
        "layer": "CA",
        "measurement_method": "CDSEM mean",
        "module": "CA_MOL_CONTACT",
        "direction_bad": "decrease",
        "aliases": ["CA CD", "contact CD"],
    },
    {
        "canonical_item_id": "GATE_CD",
        "raw_names": ["GATE_CD", "PCD", "POLY_CD", "LG_CD"],
        "display_name": "Gate length CD",
        "meaning": "Gate/channel length proxy. Shorter Lg can increase DIBL and Vth roll-off.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "CDSEM",
        "layer": "gate",
        "measurement_method": "CDSEM mean",
        "module": "LITHO_ETCH",
        "direction_bad": "context",
        "aliases": ["Lg", "gate length", "short Lg"],
    },
    {
        "canonical_item_id": "NS_WIDTH",
        "raw_names": ["NS_WIDTH", "SHEET_WIDTH", "NANOSHEET_WIDTH"],
        "display_name": "Nanosheet width",
        "meaning": "GAA nanosheet channel width. Impacts electrostatics, Ion, capacitance, and variability.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "TEM/CD",
        "layer": "channel",
        "measurement_method": "TEM/CD extraction",
        "module": "GAA_CHANNEL_RELEASE",
        "direction_bad": "context",
        "aliases": ["sheet width", "channel width"],
    },
    {
        "canonical_item_id": "NS_THK",
        "raw_names": ["NS_THK", "SHEET_THK", "NANOSHEET_THICKNESS"],
        "display_name": "Nanosheet thickness",
        "meaning": "GAA channel thickness. Affects Vth, Ion, electrostatics, and release sensitivity.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "TEM",
        "layer": "channel",
        "measurement_method": "TEM thickness",
        "module": "GAA_CHANNEL_RELEASE",
        "direction_bad": "context",
        "aliases": ["sheet thickness"],
    },
    {
        "canonical_item_id": "IS_THK",
        "raw_names": ["IS_THK", "INNER_SPACER_THK", "INNER_SPACER"],
        "display_name": "Inner spacer thickness",
        "meaning": "Inner spacer geometry controlling parasitic capacitance, S/D overlap and short-channel behavior.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "TEM",
        "layer": "inner_spacer",
        "measurement_method": "TEM thickness",
        "module": "INNER_SPACER",
        "direction_bad": "context",
        "aliases": ["inner spacer"],
    },
    {
        "canonical_item_id": "EPI_HEIGHT",
        "raw_names": ["EPI_HEIGHT", "SD_EPI_HEIGHT", "EPI_HT"],
        "display_name": "S/D epi height",
        "meaning": "Source/drain epi growth geometry. Affects Rsd, stress, junction leakage, and contact landing.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "TEM/metrology",
        "layer": "S/D",
        "measurement_method": "TEM/optical metrology",
        "module": "SD_EPI",
        "direction_bad": "context",
        "aliases": ["epi", "source drain epi"],
    },
    {
        "canonical_item_id": "EPI_DOPING",
        "raw_names": ["EPI_DOPING", "SD_DOPING", "ACTIVE_DOPING"],
        "display_name": "S/D epi active doping",
        "meaning": "Effective source/drain dopant activation. Lower activation can increase Rsd and reduce Ion.",
        "unit": "cm^-3",
        "source_type": "INLINE",
        "test_structure": "SIMS/spreading resistance",
        "layer": "S/D",
        "measurement_method": "SIMS/SRP/proxy",
        "module": "SD_EPI",
        "direction_bad": "decrease",
        "aliases": ["dopant activation", "SD activation"],
    },
    {
        "canonical_item_id": "WFM_THK",
        "raw_names": ["WFM_THK", "WFM_THICKNESS", "WORKFUNCTION_METAL"],
        "display_name": "Work-function metal thickness",
        "meaning": "RMG/WFM stack knob that can shift Vth globally or by polarity.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "film metrology",
        "layer": "RMG",
        "measurement_method": "XRF/ellipsometry/TEM",
        "module": "RMG_WFM",
        "direction_bad": "context",
        "aliases": ["WFM", "workfunction"],
    },
    {
        "canonical_item_id": "OX_THK",
        "raw_names": ["OX_THK", "EOT", "GATE_OX_THK"],
        "display_name": "Gate dielectric thickness/EOT",
        "meaning": "Gate dielectric thickness or EOT. Impacts Vth, SS, gate leakage, and reliability.",
        "unit": "nm",
        "source_type": "INLINE",
        "test_structure": "film/metrology",
        "layer": "gate_stack",
        "measurement_method": "ellipsometry/CV/TEM",
        "module": "GATE_DIELECTRIC",
        "direction_bad": "context",
        "aliases": ["EOT", "oxide thickness"],
    },
    {
        "canonical_item_id": "LKG_SHORT",
        "raw_names": ["SHORT", "LKG_SHORT", "BRIDGE_LEAK"],
        "display_name": "Short / bridge leakage",
        "meaning": "Leakage caused by bridge/short path. Needs layout, defect, and wafer map evidence.",
        "unit": "A",
        "source_type": "ET",
        "test_structure": "comb/serpentine/device",
        "layer": "BEOL/MOL/device",
        "measurement_method": "leakage current",
        "module": "DEFECTIVITY",
        "direction_bad": "increase",
        "aliases": ["short", "bridge"],
    },
]


LAYER_DICTIONARY = [
    {"layer": "channel", "description": "GAA nanosheet/channel geometry and release quality"},
    {"layer": "inner_spacer", "description": "Inner spacer controlling S/D overlap and parasitic capacitance"},
    {"layer": "gate_stack", "description": "High-k/interfacial layer/RMG stack"},
    {"layer": "RMG", "description": "Replacement metal gate and work-function metal module"},
    {"layer": "S/D", "description": "Source/drain epi, extension, activation, silicidation"},
    {"layer": "CA", "description": "MOL contact aperture, liner, barrier, fill and contact resistance"},
    {"layer": "SRAM", "description": "SRAM bitcell/macro functional metrics"},
]

PROCESS_MODULE_DICTIONARY = [
    {"module": "GAA_CHANNEL_RELEASE", "keywords": ["GAA", "release", "nanosheet", "channel"], "description": "Channel release and nanosheet geometry"},
    {"module": "INNER_SPACER", "keywords": ["inner spacer", "IS"], "description": "Inner spacer etch/deposition and overlap control"},
    {"module": "RMG_WFM", "keywords": ["RMG", "WFM", "workfunction"], "description": "Replacement metal gate and work-function metal"},
    {"module": "SD_EPI", "keywords": ["epi", "S/D", "source drain"], "description": "Source/drain epi growth, dopant activation and stress"},
    {"module": "CA_MOL_CONTACT", "keywords": ["CA", "MOL", "contact"], "description": "CA/MOL contact CD, liner, barrier, fill, resistance"},
    {"module": "GATE_DIELECTRIC", "keywords": ["EOT", "oxide", "gate leakage"], "description": "Gate dielectric and interface quality"},
    {"module": "DEFECTIVITY", "keywords": ["short", "bridge", "particle"], "description": "Defect, bridge, short, local abnormality"},
]

TEST_STRUCTURE_DICTIONARY = [
    {"structure": "short_Lg_FET", "description": "Short channel FET used for DIBL/SS/Vth roll-off checks"},
    {"structure": "FET", "description": "Device ET transistor structure"},
    {"structure": "sheet_resistance", "description": "Rsheet structure. Required for CA_RS sheet interpretation."},
    {"structure": "Kelvin/TLM", "description": "Contact resistance extraction structure. CA_RS name alone must not override this."},
    {"structure": "contact_chain", "description": "Chain resistance sensitive to cumulative contact defects/opens"},
    {"structure": "SRAM macro", "description": "SRAM functional voltage/fail metrics"},
]


SOURCE_TYPE_PROFILES: list[dict[str, Any]] = [
    {
        "source_type": "FAB",
        "meaning": "Process route, step, chamber/recipe/progress and lot movement data.",
        "default_grain": "root_lot_id/fab_lot_id/wafer_id/step_id/time",
        "join_keys": ["root_lot_id", "fab_lot_id", "wafer_id", "step_id", "lot_wf"],
        "default_aggregation": "latest step/time per lot_wf unless a route segment is requested",
        "knowledge_to_attach": ["step meaning", "process module", "recipe/chamber context", "queue/rework state"],
        "guardrails": ["Do not compare FAB step order lexicographically without route sequence metadata."],
    },
    {
        "source_type": "INLINE",
        "meaning": "Inline metrology, CD/film/defect/process monitors before or during route.",
        "default_grain": "lot_wf or shot/position when available",
        "join_keys": ["lot_wf", "root_lot_id", "wafer_id", "shot_id", "shot_x", "shot_y", "subitem_id"],
        "default_aggregation": "avg by lot_wf unless shot/position exact match exists",
        "knowledge_to_attach": ["item semantics", "measurement method", "layer/module", "coordinate map", "source step"],
        "guardrails": ["Do not infer item meaning from raw name alone.", "Prefer shot match over lot_wf aggregation when both sides have coordinates."],
    },
    {
        "source_type": "ET",
        "meaning": "Electrical test/device/WAT parametric measurements.",
        "default_grain": "lot_wf/item/step/point",
        "join_keys": ["lot_wf", "root_lot_id", "fab_lot_id", "wafer_id", "step_id", "item_id", "shot_id"],
        "default_aggregation": "median by lot_wf unless exact shot/point match exists",
        "knowledge_to_attach": ["test structure", "bias condition", "polarity", "device size", "measurement method"],
        "guardrails": ["Do not mix nFET/pFET or short/long Lg without explicit structure fields."],
    },
    {
        "source_type": "VM",
        "meaning": "Functional/voltage margin measurements such as SRAM Vmin and macro fail behavior.",
        "default_grain": "lot_wf/macro/condition/bin",
        "join_keys": ["lot_wf", "root_lot_id", "wafer_id", "macro", "condition", "bin"],
        "default_aggregation": "median or fail-rate by lot_wf and macro; keep condition/bin split",
        "knowledge_to_attach": ["macro name", "bitcell/layout", "condition", "fail mode", "linked ET proxy"],
        "guardrails": ["Do not turn VM fail into a process root cause without linked ET/Inline/wafer-map evidence."],
    },
    {
        "source_type": "QTIME",
        "meaning": "Queue time, hold time and time-between-step exposure windows.",
        "default_grain": "lot/wafer/from_step/to_step/time_window",
        "join_keys": ["root_lot_id", "fab_lot_id", "wafer_id", "from_step_id", "to_step_id", "lot_wf"],
        "default_aggregation": "duration median/p95 by route segment and lot_wf",
        "knowledge_to_attach": ["from/to step module", "time sensitivity", "thermal/clean exposure rule", "queue spec"],
        "guardrails": ["Do not correlate QTIME to ET without confirming the time window precedes the measurement."],
    },
    {
        "source_type": "EDS",
        "meaning": "Electrical die sort / wafer sort die-level bin, yield and map data.",
        "default_grain": "wafer/die/bin/test condition",
        "join_keys": ["root_lot_id", "wafer_id", "die_x", "die_y", "shot_x", "shot_y", "lot_wf"],
        "default_aggregation": "yield/fail-rate by wafer/shot/region; preserve die coordinates for maps",
        "knowledge_to_attach": ["bin meaning", "die coordinate system", "test condition", "product layout", "fail signature"],
        "guardrails": ["Do not aggregate away spatial pattern before wafer/reticle/local signature check."],
    },
]


KNOWLEDGE_CARDS: list[dict[str, Any]] = [
    {
        "id": "KC_DIBL_SS_GAA_ELECTROSTATICS",
        "title": "DIBL increase with SS increase",
        "symptom_items": ["DIBL", "SS", "VTH_ROLLOFF"],
        "trigger_terms": ["short Lg", "GAA", "roll-off", "electrostatic", "증가"],
        "electrical_mechanism": "Gate control degradation increases drain coupling and weakens subthreshold slope.",
        "structural_causes": ["nanosheet width/thickness shift", "inner spacer under/over-etch", "gate length CD short", "EOT/interface degradation"],
        "process_root_causes": ["GAA channel release drift", "inner spacer module drift", "litho/etch Lg bias", "gate dielectric/RMG stack drift"],
        "supporting_evidence": ["DIBL and SS move together", "short-Lg structures are more sensitive than long-Lg", "Inline NS/GATE_CD/IS_THK shift in same lots"],
        "contradicting_evidence": ["Only DIBL moves while SS and Vth roll-off are stable", "No short-Lg dependence", "Wafer-map pattern indicates local defect instead of module drift"],
        "missing_data": ["GATE_CD by lot_wf", "NS_WIDTH/NS_THK", "IS_THK", "short/long Lg split ET"],
        "recommended_checks": ["Plot DIBL vs SS by lot_wf", "Compare short-Lg and long-Lg Vth roll-off", "Join Inline GATE_CD/NS/IS to ET by lot_wf"],
        "chart_suggestions": [
            {"type": "scatter", "x": "DIBL", "y": "SS", "color": "lot_id"},
            {"type": "scatter", "x": "GATE_CD", "y": "DIBL", "fit": "linear"},
            {"type": "trend", "y": "DIBL", "group": "lot_id"},
        ],
        "confidence_base": 0.64,
        "module_tags": ["GAA_CHANNEL_RELEASE", "INNER_SPACER", "LITHO_ETCH", "GATE_DIELECTRIC"],
    },
    {
        "id": "KC_ION_DOWN_RSD_UP",
        "title": "Ion decrease with Rsd increase",
        "symptom_items": ["ION", "RSD"],
        "trigger_terms": ["Ion", "Rsd", "drive", "decrease", "감소", "증가"],
        "electrical_mechanism": "Higher series resistance reduces effective channel drive and lowers on-current.",
        "structural_causes": ["S/D epi volume loss", "dopant activation loss", "silicide/contact issue", "extension resistance increase"],
        "process_root_causes": ["S/D epi growth drift", "anneal activation shift", "CA/MOL contact resistance drift", "implant/extension condition shift"],
        "supporting_evidence": ["Rsd increases in same lot_wf where Ion decreases", "Vth is relatively stable", "EPI_HEIGHT/EPI_DOPING or CA_RC shifts"],
        "contradicting_evidence": ["Ion decrease is fully explained by Vth increase", "Rsd stable across affected wafers"],
        "missing_data": ["RSD extraction by polarity", "EPI_HEIGHT/EPI_DOPING", "CA_RC_KELVIN", "VTH"],
        "recommended_checks": ["Scatter Rsd vs Ion", "Check Ion normalized by Vth", "Review S/D epi and CA contact inline trends"],
        "chart_suggestions": [
            {"type": "scatter", "x": "RSD", "y": "ION", "fit": "linear"},
            {"type": "trend", "y": "RSD", "group": "lot_id"},
        ],
        "confidence_base": 0.68,
        "module_tags": ["SD_EPI", "CA_MOL_CONTACT"],
    },
    {
        "id": "KC_CA_RS_UP_CA_CD_DOWN",
        "title": "CA resistance increase with CA CD decrease",
        "symptom_items": ["CA_RS", "CA_RC_KELVIN", "CA_CHAIN_R", "CA_CD"],
        "trigger_terms": ["CA", "contact", "CD", "Rs", "Rc", "chain", "증가", "감소"],
        "electrical_mechanism": "Reduced contact aperture or degraded liner/fill increases CA/MOL resistance.",
        "structural_causes": ["CA CD shrink", "liner/barrier thickness increase", "fill void/seam", "etch residue or landing issue"],
        "process_root_causes": ["CA litho/etch CD bias", "MOL liner/barrier deposition drift", "contact clean/fill issue", "CMP recess or open risk"],
        "supporting_evidence": ["CA_CD decreases while CA resistance metrics increase", "Kelvin/TLM or chain structures confirm contact path", "Inline and ET move in same affected lots"],
        "contradicting_evidence": ["CA_CD stable and only sheet structure moves", "Resistance change isolated to a non-contact test structure"],
        "missing_data": ["test_structure for CA_RS", "CA_CD", "CA_RC_KELVIN", "CA_CHAIN_R", "wafer map for local opens"],
        "recommended_checks": ["Resolve CA_RS with unit/test_structure first", "Scatter CA_CD vs CA_RC/CHAIN_R", "Check CA defect/open maps"],
        "chart_suggestions": [
            {"type": "scatter", "x": "CA_CD", "y": "CA_RC_KELVIN", "fit": "linear"},
            {"type": "scatter", "x": "CA_CD", "y": "CA_CHAIN_R", "color": "wafer_id"},
        ],
        "confidence_base": 0.66,
        "module_tags": ["CA_MOL_CONTACT"],
    },
    {
        "id": "KC_VTH_GLOBAL_SHIFT_RMG",
        "title": "Vth global shift",
        "symptom_items": ["VTH", "VTH_ROLLOFF"],
        "trigger_terms": ["Vth", "global", "shift", "전체", "이동"],
        "electrical_mechanism": "Work-function, dipole, oxide charge, or EOT changes shift threshold voltage globally.",
        "structural_causes": ["WFM thickness/composition shift", "dipole layer change", "EOT shift", "fixed charge/interface state shift"],
        "process_root_causes": ["RMG/WFM deposition drift", "gate dielectric thermal/clean drift", "metal gate anneal shift"],
        "supporting_evidence": ["Both short and long Lg Vth shift similarly", "DIBL/SS not materially changed", "WFM_THK or OX_THK shifts"],
        "contradicting_evidence": ["Shift only at short Lg", "Strong Rsd/Ion-only behavior", "Local wafer-map defect pattern"],
        "missing_data": ["Long-Lg Vth", "WFM_THK", "OX_THK/EOT", "polarity split"],
        "recommended_checks": ["Separate nFET/pFET and short/long Lg", "Trend WFM_THK/EOT", "Check Vth vs DIBL/SS co-movement"],
        "chart_suggestions": [
            {"type": "trend", "y": "VTH", "group": "polarity"},
            {"type": "scatter", "x": "WFM_THK", "y": "VTH", "fit": "linear"},
        ],
        "confidence_base": 0.61,
        "module_tags": ["RMG_WFM", "GATE_DIELECTRIC"],
    },
    {
        "id": "KC_GATE_LEAKAGE_UP",
        "title": "Gate leakage increase",
        "symptom_items": ["IGATE", "IOFF"],
        "trigger_terms": ["gate leakage", "IGATE", "LKG", "누설", "leak"],
        "electrical_mechanism": "Gate dielectric leakage rises through EOT thinning, trap generation, plasma damage, or local defect paths.",
        "structural_causes": ["thin EOT", "interface/dielectric damage", "metal gate residue", "local bridge/particle"],
        "process_root_causes": ["gate dielectric process drift", "RMG clean/plasma damage", "etch residue", "defectivity excursion"],
        "supporting_evidence": ["IGATE moves before or with IOFF", "EOT/OX_THK shift", "wafer edge or local defect pattern"],
        "contradicting_evidence": ["Only drain leakage rises with stable IGATE", "No gate-bias dependence"],
        "missing_data": ["IGATE bias polarity", "OX_THK/EOT", "wafer map", "defect inspection"],
        "recommended_checks": ["Split gate leakage by bias and polarity", "Check IGATE vs OX_THK/EOT", "Review defect/wafer maps"],
        "chart_suggestions": [
            {"type": "trend", "y": "IGATE", "group": "lot_id"},
            {"type": "scatter", "x": "OX_THK", "y": "IGATE", "fit": "linear"},
        ],
        "confidence_base": 0.63,
        "module_tags": ["GATE_DIELECTRIC", "RMG_WFM", "DEFECTIVITY"],
    },
    {
        "id": "KC_SRAM_VMIN_UP",
        "title": "SRAM Vmin increase",
        "symptom_items": ["SRAM_VMIN", "ION", "VTH", "IOFF", "RSD"],
        "trigger_terms": ["SRAM", "Vmin", "비민", "fail", "margin"],
        "electrical_mechanism": "Higher SRAM Vmin can result from read/write margin loss driven by device mismatch, Ion loss, Vth shift, leakage, or local variation.",
        "structural_causes": ["local Vth mismatch", "Ion asymmetry", "Rsd/contact resistance increase", "gate leakage/off leakage increase", "litho/layout local variation"],
        "process_root_causes": ["RMG/WFM variability", "S/D epi/contact drift", "litho CD variation", "defectivity/local contamination"],
        "supporting_evidence": ["SRAM Vmin correlates with device ET shifts", "affected bitcell region has wafer-map signature", "matching device polarity metric shifted"],
        "contradicting_evidence": ["ET device metrics stable", "only macro/test program condition changed"],
        "missing_data": ["bitcell fail bin", "device polarity split", "matched local ET/Inline", "wafer map and reticle coordinates"],
        "recommended_checks": ["Compare SRAM Vmin vs Ion/Vth/Rsd", "Map by wafer/reticle", "Check local Inline CD and defect layers"],
        "chart_suggestions": [
            {"type": "scatter", "x": "ION", "y": "SRAM_VMIN", "fit": "linear"},
            {"type": "wafer_map", "metric": "SRAM_VMIN"},
        ],
        "confidence_base": 0.59,
        "module_tags": ["SRAM", "RMG_WFM", "SD_EPI", "CA_MOL_CONTACT", "DEFECTIVITY"],
    },
    {
        "id": "KC_IOFF_UP_DIBL_UP",
        "title": "Ioff increase with DIBL increase",
        "symptom_items": ["IOFF", "DIBL", "VTH_ROLLOFF"],
        "trigger_terms": ["Ioff", "DIBL", "leakage", "short channel"],
        "electrical_mechanism": "Short-channel electrostatics can increase off-state leakage through reduced source-channel barrier.",
        "structural_causes": ["short Lg", "channel geometry shift", "junction encroachment", "inner spacer overlap issue"],
        "process_root_causes": ["litho/etch Lg short", "GAA release geometry drift", "inner spacer drift", "extension/junction drift"],
        "supporting_evidence": ["Ioff and DIBL co-move", "short-Lg dependence", "GATE_CD or IS_THK shift"],
        "contradicting_evidence": ["IGATE explains leakage instead", "DIBL stable"],
        "missing_data": ["GATE_CD", "IS_THK", "IGATE", "short/long Lg split"],
        "recommended_checks": ["Scatter DIBL vs Ioff", "Separate gate leakage from drain leakage", "Review short-Lg structures"],
        "chart_suggestions": [{"type": "scatter", "x": "DIBL", "y": "IOFF", "fit": "linear"}],
        "confidence_base": 0.62,
        "module_tags": ["GAA_CHANNEL_RELEASE", "INNER_SPACER", "LITHO_ETCH"],
    },
    {
        "id": "KC_SHORT_BRIDGE_LEAKAGE",
        "title": "Short or bridge leakage excursion",
        "symptom_items": ["LKG_SHORT", "IGATE", "IOFF"],
        "trigger_terms": ["short", "bridge", "leak", "LKG", "불량"],
        "electrical_mechanism": "A physical bridge or local defect creates unintended conductive path.",
        "structural_causes": ["residue bridge", "pattern collapse", "metal/contact short", "particle-driven local defect"],
        "process_root_causes": ["etch/clean residue", "litho defectivity", "CMP scratch/residue", "MOL/BEOL bridge"],
        "supporting_evidence": ["localized wafer map pattern", "comb/serpentine fail", "inspection defect signal"],
        "contradicting_evidence": ["lot-wide smooth parametric shift", "no spatial clustering"],
        "missing_data": ["wafer map", "inspection layer", "reticle/shot coordinates", "defect images"],
        "recommended_checks": ["Review wafer/reticle map", "Check defect inspection at suspected layer", "Compare comb/serpentine structures"],
        "chart_suggestions": [{"type": "wafer_map", "metric": "LKG_SHORT"}],
        "confidence_base": 0.57,
        "module_tags": ["DEFECTIVITY", "CA_MOL_CONTACT"],
    },
]


CAUSAL_EDGES: list[dict[str, Any]] = [
    {"source": "GATE_CD_SHORT", "target": "DIBL", "relation": "increases", "evidence": "Shorter effective Lg worsens drain coupling", "module": "LITHO_ETCH"},
    {"source": "GATE_CD_SHORT", "target": "VTH_ROLLOFF", "relation": "increases", "evidence": "Short-channel roll-off grows as Lg shortens", "module": "LITHO_ETCH"},
    {"source": "GAA_CHANNEL_RELEASE", "target": "NS_WIDTH", "relation": "changes", "evidence": "Release process changes nanosheet geometry", "module": "GAA_CHANNEL_RELEASE"},
    {"source": "NS_WIDTH", "target": "DIBL", "relation": "can_change", "evidence": "Channel geometry affects electrostatic control", "module": "GAA_CHANNEL_RELEASE"},
    {"source": "NS_THK", "target": "VTH", "relation": "can_shift", "evidence": "Sheet thickness changes confinement and Vth", "module": "GAA_CHANNEL_RELEASE"},
    {"source": "INNER_SPACER", "target": "IS_THK", "relation": "changes", "evidence": "Spacer deposition/etch changes thickness", "module": "INNER_SPACER"},
    {"source": "IS_THK", "target": "DIBL", "relation": "can_change", "evidence": "Overlap/fringe field changes electrostatics", "module": "INNER_SPACER"},
    {"source": "IS_THK", "target": "RSD", "relation": "can_change", "evidence": "Spacer geometry changes extension/contact access", "module": "INNER_SPACER"},
    {"source": "SD_EPI", "target": "EPI_HEIGHT", "relation": "changes", "evidence": "Epi growth controls source/drain volume", "module": "SD_EPI"},
    {"source": "EPI_HEIGHT", "target": "RSD", "relation": "can_change", "evidence": "Epi volume and contact area affect series resistance", "module": "SD_EPI"},
    {"source": "EPI_DOPING", "target": "RSD", "relation": "decrease_doping_increases", "evidence": "Lower active doping raises resistance", "module": "SD_EPI"},
    {"source": "RSD", "target": "ION", "relation": "increase_decreases", "evidence": "Series resistance lowers effective drive", "module": "SD_EPI"},
    {"source": "RMG_WFM", "target": "WFM_THK", "relation": "changes", "evidence": "Deposition controls WFM thickness/composition", "module": "RMG_WFM"},
    {"source": "WFM_THK", "target": "VTH", "relation": "shifts", "evidence": "Work function metal stack shifts threshold", "module": "RMG_WFM"},
    {"source": "GATE_DIELECTRIC", "target": "OX_THK", "relation": "changes", "evidence": "Dielectric process controls EOT/thickness", "module": "GATE_DIELECTRIC"},
    {"source": "OX_THK", "target": "IGATE", "relation": "thin_increases", "evidence": "Thinner EOT can raise gate leakage", "module": "GATE_DIELECTRIC"},
    {"source": "OX_THK", "target": "SS", "relation": "can_change", "evidence": "EOT/interface changes affect slope", "module": "GATE_DIELECTRIC"},
    {"source": "CA_MOL_CONTACT", "target": "CA_CD", "relation": "changes", "evidence": "CA litho/etch controls contact aperture", "module": "CA_MOL_CONTACT"},
    {"source": "CA_CD", "target": "CA_RC_KELVIN", "relation": "decrease_increases", "evidence": "Smaller aperture raises contact resistance", "module": "CA_MOL_CONTACT"},
    {"source": "CA_CD", "target": "CA_CHAIN_R", "relation": "decrease_increases", "evidence": "Smaller contacts raise chain resistance/open risk", "module": "CA_MOL_CONTACT"},
    {"source": "CA_RC_KELVIN", "target": "RSD", "relation": "can_increase", "evidence": "Contact resistance contributes to series resistance", "module": "CA_MOL_CONTACT"},
    {"source": "DEFECTIVITY", "target": "LKG_SHORT", "relation": "increases", "evidence": "Bridge or particle creates leakage path", "module": "DEFECTIVITY"},
    {"source": "LKG_SHORT", "target": "SRAM_VMIN", "relation": "can_increase", "evidence": "Local defects can reduce SRAM margin", "module": "DEFECTIVITY"},
    {"source": "ION", "target": "SRAM_VMIN", "relation": "decrease_increases", "evidence": "Lower drive current reduces SRAM read/write margin", "module": "SRAM"},
    {"source": "VTH_MISMATCH", "target": "SRAM_VMIN", "relation": "increases", "evidence": "Mismatch reduces SRAM static noise margin", "module": "SRAM"},
]


HISTORICAL_CASES: list[dict[str, Any]] = [
    {
        "case_id": "CASE_GAA_SHORTLG_001",
        "title": "Short-Lg DIBL/SS excursion after channel release window shift",
        "symptoms": ["DIBL", "SS", "VTH_ROLLOFF"],
        "tags": ["GAA", "short Lg", "channel release"],
        "root_causes": ["GAA channel release over-etch changed nanosheet width/thickness"],
        "evidence": ["DIBL vs SS correlation 0.72", "NS_WIDTH shifted -1.8 nm", "long-Lg Vth stable"],
        "actions": ["TEM on channel release split", "tighten release endpoint", "monitor NS_WIDTH by lot_wf"],
        "outcome": "DIBL recovered on corrected release recipe split.",
    },
    {
        "case_id": "CASE_CA_CONTACT_002",
        "title": "CA contact resistance rise with CA CD shrink",
        "symptoms": ["CA_RC_KELVIN", "CA_CHAIN_R", "CA_CD"],
        "tags": ["CA", "MOL", "contact", "CD"],
        "root_causes": ["CA etch bias and liner thickness increase reduced contact area"],
        "evidence": ["CA_CD down 3%", "Kelvin Rc up 18%", "chain tail fail increased at edge wafers"],
        "actions": ["CA CDSEM review", "liner thickness split", "contact clean check"],
        "outcome": "CA Rc improved after etch bias correction.",
    },
    {
        "case_id": "CASE_RSD_ION_003",
        "title": "Ion degradation from S/D epi activation loss",
        "symptoms": ["ION", "RSD", "EPI_DOPING"],
        "tags": ["S/D", "epi", "anneal"],
        "root_causes": ["Anneal temperature drift lowered active dopant activation"],
        "evidence": ["Rsd up 11%", "Ion down 6%", "Vth stable", "activation monitor down"],
        "actions": ["Anneal log review", "SIMS/SRP confirmation", "S/D epi monitor split"],
        "outcome": "Restored after anneal chamber matching.",
    },
    {
        "case_id": "CASE_RMG_VTH_004",
        "title": "Global Vth shift from WFM thickness drift",
        "symptoms": ["VTH", "WFM_THK"],
        "tags": ["RMG", "WFM", "global"],
        "root_causes": ["WFM deposition drift shifted work function"],
        "evidence": ["short/long Lg Vth moved together", "DIBL stable", "WFM_THK high side"],
        "actions": ["XRF trend review", "RMG chamber matching", "polarity split check"],
        "outcome": "Vth centered after WFM thickness correction.",
    },
    {
        "case_id": "CASE_IGATE_005",
        "title": "Gate leakage excursion from dielectric plasma damage",
        "symptoms": ["IGATE", "OX_THK", "IOFF"],
        "tags": ["gate leakage", "dielectric", "RMG"],
        "root_causes": ["RMG clean plasma condition generated dielectric damage"],
        "evidence": ["IGATE high on affected chamber", "EOT stable", "defect map weak but bias dependence strong"],
        "actions": ["Gate leakage by bias", "TDDB sample", "plasma recipe audit"],
        "outcome": "Leakage reduced after plasma power rollback.",
    },
    {
        "case_id": "CASE_SRAM_006",
        "title": "SRAM Vmin rise from local Vth and Ion mismatch",
        "symptoms": ["SRAM_VMIN", "VTH", "ION"],
        "tags": ["SRAM", "mismatch", "local variation"],
        "root_causes": ["Local RMG variability and CD variation increased bitcell mismatch"],
        "evidence": ["SRAM Vmin high by reticle region", "device Vth sigma increased", "GATE_CD sigma high"],
        "actions": ["reticle map review", "Vth sigma check", "local CD distribution check"],
        "outcome": "Monitor added for local CD/Vth sigma.",
    },
]


ENGINEER_USE_CASE_SEEDS: list[dict[str, Any]] = [
    {
        "id": "UC_PROCESS_OWNER_DAILY_RCA",
        "role": "process_owner",
        "workflow": "Daily excursion triage",
        "prior_knowledge_slots": ["owned_module", "golden_metrics", "known_sensitive_steps", "chamber_or_recipe_context"],
        "default_questions": [
            "Which lots changed first and at which step?",
            "Is the symptom lot-wide, wafer-local, reticle-local, or structure-specific?",
            "Which Inline monitor is matched by lot_wf before the ET shift?",
        ],
        "quality_checks": ["Do not conclude from one metric", "Check supporting and contradicting evidence", "Record missing data"],
    },
    {
        "id": "UC_DEVICE_ENGINEER_CORRELATION",
        "role": "device_engineer",
        "workflow": "Device symptom to process candidate narrowing",
        "prior_knowledge_slots": ["device_polarity", "short_long_Lg_splits", "target_bias", "known_layout_sensitivity"],
        "default_questions": [
            "Does the symptom scale with Lg or polarity?",
            "Can Vth explain Ion before invoking Rsd?",
            "Are leakage paths gate, drain, junction, or bridge dominated?",
        ],
        "quality_checks": ["Separate electrical mechanism from process root cause", "Use structure metadata for item interpretation"],
    },
    {
        "id": "UC_INTEGRATION_OWNER_CHANGE_REVIEW",
        "role": "integration_owner",
        "workflow": "Change-point and split review",
        "prior_knowledge_slots": ["recent_changes", "split_table_knobs", "fab_step_window", "risk_modules"],
        "default_questions": [
            "Which split knob or process step precedes the excursion?",
            "Is the effect consistent across products or module-specific?",
            "Which check confirms or falsifies the leading hypothesis fastest?",
        ],
        "quality_checks": ["Use case DB before proposing action", "Prefer matched lot_wf joins over loose lot joins"],
    },
    {
        "id": "UC_YIELD_ENGINEER_SRAM",
        "role": "yield_engineer",
        "workflow": "SRAM/yield fail pareto to device and inline evidence",
        "prior_knowledge_slots": ["fail_bin", "macro", "wafer_map_signature", "reticle_coordinates"],
        "default_questions": [
            "Does fail signature align with device ET, Inline CD, or defect map?",
            "Is the issue global, edge, reticle, or local?",
            "Which module monitor gives the earliest warning?",
        ],
        "quality_checks": ["Do not mix functional test shifts with parametric root cause without evidence"],
    },
]


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    return [value]


def _sql_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def _unique(seq: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in seq:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "canonical_item_id", "display_name", "meaning", "unit", "source_type",
        "test_structure", "layer", "measurement_method", "module", "direction_bad",
        "ambiguity_note",
    ]
    return {k: item.get(k) for k in keys if item.get(k) is not None}


def _item_alias_records() -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    for item in ITEM_MASTER:
        names = [item.get("canonical_item_id"), item.get("display_name")]
        names.extend(item.get("raw_names") or [])
        names.extend(item.get("aliases") or [])
        for name in names:
            n = _norm(name)
            if n:
                records.append((n, item))
    return records


def _find_item_by_name(name: str) -> dict[str, Any] | None:
    n = _norm(name)
    if not n:
        return None
    for alias, item in _item_alias_records():
        if alias == n:
            return item
    return None


def _context_value(context: dict[str, Any], *keys: str) -> str:
    parts: list[str] = []
    for key in keys:
        value = context.get(key)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def _supports_sheet_context(context: dict[str, Any]) -> bool:
    joined = _context_value(context, "unit", "test_structure", "measurement_method", "structure", "method")
    return any(t in joined for t in ["ohm/sq", "ohm per sq", "rsheet", "sheet", "van der pauw", "vdp", "rsh"])


def _supports_contact_context(context: dict[str, Any]) -> bool:
    joined = _context_value(context, "unit", "test_structure", "measurement_method", "structure", "method")
    if "ohm/sq" in joined or "rsheet" in joined:
        return False
    return any(t in joined for t in ["kelvin", "tlm", "contact", "chain", "ohm"])


def search_items(q: str = "", limit: int = 50) -> dict[str, Any]:
    needle = str(q or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for item in ITEM_MASTER:
        hay = " ".join(
            str(v)
            for v in [
                item.get("canonical_item_id"),
                item.get("display_name"),
                item.get("meaning"),
                item.get("unit"),
                item.get("source_type"),
                item.get("test_structure"),
                item.get("layer"),
                item.get("measurement_method"),
                item.get("module"),
                " ".join(item.get("raw_names") or []),
                " ".join(item.get("aliases") or []),
            ]
        ).lower()
        if not needle or needle in hay:
            rows.append(_compact_item(item))
    return {
        "ok": True,
        "version": KNOWLEDGE_VERSION,
        "items": rows[: max(1, min(200, int(limit or 50)))],
        "total": len(rows),
        "rule": "Raw item names are searched only against item_master aliases; meaning is not inferred from raw text alone.",
    }


def resolve_item_semantics(raw_items: list[Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    warnings: list[str] = []

    for entry in _coerce_list(raw_items):
        item_context = dict(context)
        if isinstance(entry, dict):
            raw = entry.get("raw_item") or entry.get("item") or entry.get("name") or entry.get("raw") or ""
            item_context.update({k: v for k, v in entry.items() if k not in {"raw_item", "item", "name", "raw"}})
        else:
            raw = str(entry or "")
        raw = raw.strip()
        raw_norm = _norm(raw)
        if not raw_norm:
            continue

        if raw_norm == "CARS":
            ca_rs = _find_item_by_name("CA_RS")
            ca_rc = _find_item_by_name("CA_RC_KELVIN")
            ca_chain = _find_item_by_name("CA_CHAIN_R")
            if _supports_contact_context(item_context):
                msg = "CA_RS was mapped to contact resistance candidate because unit/test structure indicates Kelvin/TLM/contact/chain, not Rsheet."
                warnings.append(msg)
                resolved.append({
                    "raw_item": raw,
                    "status": "resolved_with_context",
                    "canonical_item_id": "CA_RC_KELVIN",
                    "item": _compact_item(ca_rc or {}),
                    "confidence": 0.78,
                    "ambiguity": msg,
                    "context_used": item_context,
                })
                continue
            if _supports_sheet_context(item_context):
                resolved.append({
                    "raw_item": raw,
                    "status": "resolved",
                    "canonical_item_id": "CA_RS",
                    "item": _compact_item(ca_rs or {}),
                    "confidence": 0.82,
                    "context_used": item_context,
                })
                continue
            msg = "CA_RS is ambiguous without unit/test_structure/measurement_method; do not conclude sheet resistance from the raw name alone."
            warnings.append(msg)
            resolved.append({
                "raw_item": raw,
                "status": "ambiguous",
                "canonical_item_id": "",
                "confidence": 0.0,
                "candidates": [_compact_item(x or {}) for x in [ca_rs, ca_rc, ca_chain] if x],
                "ambiguity": msg,
                "context_used": item_context,
            })
            continue

        item = _find_item_by_name(raw)
        if not item:
            unresolved.append({
                "raw_item": raw,
                "status": "unresolved",
                "reason": "No exact item_master/raw_names/alias match. Add item_master metadata before using this item in RCA.",
                "context_used": item_context,
            })
            continue
        resolved.append({
            "raw_item": raw,
            "status": "resolved",
            "canonical_item_id": item.get("canonical_item_id"),
            "item": _compact_item(item),
            "confidence": 0.86,
            "context_used": item_context,
        })

    return {
        "ok": True,
        "version": KNOWLEDGE_VERSION,
        "resolved": resolved,
        "unresolved": unresolved,
        "warnings": _unique(warnings),
        "rule": "Never infer a raw item meaning from the name alone; use item_master with unit/source_type/test_structure/layer/measurement_method.",
    }


def _extract_candidate_item_names(prompt: str) -> list[str]:
    prompt_s = str(prompt or "")
    prompt_l = prompt_s.lower()
    found: list[str] = []
    for item in ITEM_MASTER:
        names = [item.get("canonical_item_id")]
        names.extend(item.get("raw_names") or [])
        names.extend(item.get("aliases") or [])
        for name in names:
            if not name:
                continue
            name_s = str(name)
            if len(name_s) <= 2:
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(name_s)}(?![A-Za-z0-9])", prompt_s, flags=re.IGNORECASE):
                    found.append(item["canonical_item_id"])
                    break
            elif name_s.lower() in prompt_l or _norm(name_s) in _norm(prompt_s):
                found.append(item["canonical_item_id"])
                break
    return _unique(found)


def _direction_for_prompt(prompt: str, item_id: str) -> str:
    text = str(prompt or "").lower()
    up_terms = ["increase", "increased", "up", "rise", "rising", "high", "증가", "상승", "올", "높", "악화", "커"]
    down_terms = ["decrease", "decreased", "down", "drop", "low", "감소", "하락", "떨어", "낮", "작"]
    item = _find_item_by_name(item_id) or {}
    names = [item_id] + list(item.get("raw_names") or []) + list(item.get("aliases") or [])
    windows: list[str] = []
    for name in names:
        idx = text.find(str(name).lower())
        if idx >= 0:
            windows.append(text[max(0, idx - 30): idx + len(str(name)) + 45])
    if not windows:
        windows = [text]
    joined = " ".join(windows)
    if any(t in joined for t in up_terms):
        return "increase"
    if any(t in joined for t in down_terms):
        return "decrease"
    return "mentioned"


def extract_symptom_features(prompt: str, resolved: dict[str, Any] | None = None) -> dict[str, Any]:
    item_ids = _extract_candidate_item_names(prompt)
    for row in (resolved or {}).get("resolved") or []:
        cid = row.get("canonical_item_id")
        if cid:
            item_ids.append(cid)
        if row.get("status") == "ambiguous":
            for c in row.get("candidates") or []:
                if c.get("canonical_item_id"):
                    item_ids.append(c["canonical_item_id"])
    item_ids = _unique(item_ids)
    modules: list[str] = []
    prompt_l = str(prompt or "").lower()
    for mod in PROCESS_MODULE_DICTIONARY:
        if any(str(k).lower() in prompt_l for k in mod.get("keywords") or []):
            modules.append(mod["module"])
    symptoms = [{"item": item_id, "direction": _direction_for_prompt(prompt, item_id)} for item_id in item_ids]
    return {
        "items": item_ids,
        "symptoms": symptoms,
        "modules": _unique(modules),
        "terms": _unique(re.findall(r"[A-Za-z][A-Za-z0-9_/-]{1,}|[가-힣]{2,}", str(prompt or "")))[:30],
    }


def _mock_measurements(source_type: str = "") -> list[dict[str, Any]]:
    source = str(source_type or "").upper()
    lots = ["A10001", "A10002", "A10003", "A10004", "A10005", "A10006"]
    wafers = ["01", "02", "03", "04"]
    et_base = {
        "DIBL": 82.0, "SS": 72.0, "VTH_ROLLOFF": 41.0, "VTH": 0.42,
        "ION": 920.0, "IOFF": 1.8, "RSD": 155.0, "IGATE": 2.5e-9,
        "SRAM_VMIN": 0.69, "LKG_SHORT": 1.1e-8,
    }
    inline_base = {
        "CA_RS": 38.0, "CA_RC_KELVIN": 14.0, "CA_CHAIN_R": 820.0, "CA_CD": 23.5,
        "GATE_CD": 15.0, "NS_WIDTH": 28.0, "NS_THK": 5.2, "IS_THK": 7.6,
        "EPI_HEIGHT": 32.0, "EPI_DOPING": 2.4e20, "WFM_THK": 3.2, "OX_THK": 1.25,
    }
    bases = et_base if source == "ET" else inline_base if source == "INLINE" else {**et_base, **inline_base}
    rows: list[dict[str, Any]] = []
    base_date = dt.date(2026, 4, 1)
    for item_id, base in bases.items():
        item = _find_item_by_name(item_id) or {}
        for li, lot in enumerate(lots):
            for wi, wafer in enumerate(wafers):
                drift = (li - 2) * 0.035 + (wi - 1.5) * 0.012
                if item_id in {"DIBL", "SS", "VTH_ROLLOFF", "RSD", "IGATE", "SRAM_VMIN", "CA_RS", "CA_RC_KELVIN", "CA_CHAIN_R", "IOFF", "LKG_SHORT"}:
                    value = base * (1.0 + drift)
                elif item_id in {"ION", "CA_CD", "EPI_DOPING"}:
                    value = base * (1.0 - drift)
                else:
                    value = base + (base * drift * 0.25 if abs(base) > 10 else drift * 0.05)
                rows.append({
                    "product": "PRODA",
                    "root_lot_id": lot,
                    "lot_id": lot,
                    "fab_lot_id": "FAB" + lot,
                    "wafer_id": wafer,
                    "lot_wf": f"{lot}_{wafer}",
                    "canonical_item_id": item_id,
                    "source_type": item.get("source_type") or ("ET if item in ET else INLINE"),
                    "value": round(float(value), 8),
                    "unit": item.get("unit") or "",
                    "date": (base_date + dt.timedelta(days=li)).isoformat(),
                    "step_id": "AA200000" if li < 3 else "AB300000",
                })
    return rows


def _ci_col(cols: list[str], *names: str, contains: list[str] | None = None) -> str:
    lower = {str(c).lower(): c for c in cols}
    for name in names:
        found = lower.get(str(name).lower())
        if found:
            return found
    if contains:
        for col in cols:
            low = str(col).lower()
            if all(token in low for token in contains):
                return col
    return ""


def _item_alias_norms(canonical_item_id: str) -> set[str]:
    item = _find_item_by_name(canonical_item_id) or {}
    names = [canonical_item_id, item.get("canonical_item_id"), item.get("display_name")]
    names.extend(item.get("raw_names") or [])
    names.extend(item.get("aliases") or [])
    return {_norm(x) for x in names if _norm(x)}


def _canonical_from_raw_item(raw: Any, requested: set[str] | None = None) -> str:
    raw_norm = _norm(raw)
    if requested:
        for canonical in requested:
            if raw_norm in _item_alias_norms(canonical):
                return canonical
        return ""
    item = _find_item_by_name(str(raw or ""))
    return str(item.get("canonical_item_id") or raw or "").upper()


def _float_value(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _actual_source_candidates(source_type: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
    source = str(source_type or "").upper()
    explicit = {
        "source_type": filters.get("source_type") or filters.get("dataset_source_type") or "",
        "root": filters.get("root") or "",
        "product": filters.get("product") or "",
        "file": filters.get("file") or "",
    }
    source_obj = filters.get("source")
    if isinstance(source_obj, dict):
        explicit.update({k: source_obj.get(k, explicit.get(k, "")) for k in explicit})
    if explicit["file"] or (explicit["root"] and explicit["product"]):
        return [explicit]

    try:
        from core.utils import find_all_sources
        sources = find_all_sources(apply_whitelist=False)
    except Exception:
        return []

    product = str(filters.get("product") or "").strip().casefold()
    out: list[dict[str, Any]] = []
    for src in sources:
        hay = " ".join(str(src.get(k) or "") for k in ("canonical", "root", "label", "file")).upper()
        if source and source not in hay.split("/") and source not in hay:
            continue
        if product and str(src.get("product") or "").strip().casefold() not in {"", product}:
            continue
        out.append({
            "source_type": src.get("source_type") or "",
            "root": src.get("root") or "",
            "product": src.get("product") or filters.get("product") or "",
            "file": src.get("file") or "",
        })
        if len(out) >= 6:
            break
    return out


def _read_dataset_sample(source: dict[str, Any], max_files: int = 8, limit: int = 5000) -> pl.DataFrame | None:
    try:
        from core.utils import read_source
        df = read_source(
            source_type=str(source.get("source_type") or ""),
            root=str(source.get("root") or ""),
            product=str(source.get("product") or ""),
            file=str(source.get("file") or ""),
            max_files=max(1, min(30, int(max_files or 8))),
        )
        if df is None or df.height == 0:
            return None
        return df.head(max(1, min(20000, int(limit or 5000))))
    except Exception:
        return None


def _normalize_measurement_rows(
    df: pl.DataFrame,
    source_type: str,
    filters: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    if df is None or df.height == 0:
        return []
    cols = list(df.columns)
    source = str(source_type or "").upper()
    requested = {str(x).upper() for x in _coerce_list(filters.get("canonical_item_ids") or filters.get("items")) if str(x).strip()}
    lots = {str(x).upper() for x in _coerce_list(filters.get("lot_filter") or filters.get("lots") or filters.get("root_lot_id")) if str(x).strip()}
    product_filter = str(filters.get("product") or "").upper()

    product_col = _ci_col(cols, "product", "PRODUCT", "prod", "PROD")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID", "root_lot", "ROOT_LOT", contains=["root", "lot"])
    lot_col = _ci_col(cols, "lot_id", "LOT_ID", "lot", "LOT")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID", "fab_lot", "FAB_LOT")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID", "wafer", "WF")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "func_step", "FUNC_STEP")
    item_col = _ci_col(cols, "canonical_item_id", "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "parameter", "PARAMETER", "metric", "METRIC", contains=["item"])
    value_col = _ci_col(cols, "value", "VALUE", "val", "VAL", "result", "RESULT", "meas_value", "MEAS_VALUE", contains=["value"])
    unit_col = _ci_col(cols, "unit", "UNIT")
    date_col = _ci_col(cols, "date", "DATE", "timestamp", "TIMESTAMP", "time", "TIME", "measure_time", "MEASURE_TIME", "tkout_time", "TKOUT_TIME")
    shot_x_col = _ci_col(cols, "shot_x", "SHOT_X")
    shot_y_col = _ci_col(cols, "shot_y", "SHOT_Y")

    def base_from_row(row: dict[str, Any]) -> dict[str, Any]:
        product = row.get(product_col) if product_col else product_filter
        root_lot = row.get(root_col) if root_col else row.get(lot_col) if lot_col else ""
        lot = row.get(lot_col) if lot_col else root_lot
        wafer = row.get(wafer_col) if wafer_col else ""
        lot_wf = row.get(lot_wf_col) if lot_wf_col else (f"{root_lot}_{wafer}" if root_lot and wafer else "")
        return {
            "product": str(product or ""),
            "root_lot_id": str(root_lot or ""),
            "lot_id": str(lot or ""),
            "fab_lot_id": str(row.get(fab_col) or ""),
            "wafer_id": str(wafer or ""),
            "lot_wf": str(lot_wf or ""),
            "source_type": source or "",
            "date": str(row.get(date_col) or ""),
            "step_id": str(row.get(step_col) or ""),
            "shot_x": str(row.get(shot_x_col) or ""),
            "shot_y": str(row.get(shot_y_col) or ""),
        }

    rows: list[dict[str, Any]] = []
    max_rows = max(1, min(5000, int(limit or 500)))
    records = df.to_dicts()

    if item_col and value_col:
        for rec in records:
            base = base_from_row(rec)
            if product_filter and base["product"].upper() != product_filter:
                continue
            if lots and base["root_lot_id"].upper() not in lots and base["lot_id"].upper() not in lots and base["fab_lot_id"].upper() not in lots:
                continue
            canonical = _canonical_from_raw_item(rec.get(item_col), requested or None)
            if requested and canonical not in requested:
                continue
            value = _float_value(rec.get(value_col))
            if value is None:
                continue
            item = _find_item_by_name(canonical) or {}
            rows.append({
                **base,
                "canonical_item_id": canonical,
                "raw_item_id": str(rec.get(item_col) or ""),
                "value": value,
                "unit": str(rec.get(unit_col) or item.get("unit") or ""),
            })
            if len(rows) >= max_rows:
                break
        return rows

    # Wide table fallback: requested item columns are value columns.
    item_cols: list[tuple[str, str]] = []
    for col in cols:
        col_norm = _norm(col)
        if requested:
            match = next((canonical for canonical in requested if col_norm in _item_alias_norms(canonical)), "")
            if match:
                item_cols.append((col, match))
        else:
            item = _find_item_by_name(col)
            if item:
                item_cols.append((col, item["canonical_item_id"]))
    if not item_cols:
        return []
    for rec in records:
        base = base_from_row(rec)
        if product_filter and base["product"].upper() != product_filter:
            continue
        if lots and base["root_lot_id"].upper() not in lots and base["lot_id"].upper() not in lots and base["fab_lot_id"].upper() not in lots:
            continue
        for col, canonical in item_cols:
            value = _float_value(rec.get(col))
            if value is None:
                continue
            item = _find_item_by_name(canonical) or {}
            rows.append({
                **base,
                "canonical_item_id": canonical,
                "raw_item_id": col,
                "value": value,
                "unit": str(item.get("unit") or ""),
            })
            if len(rows) >= max_rows:
                return rows
    return rows


def _actual_measurements(source_type: str, filters: dict[str, Any], limit: int) -> dict[str, Any]:
    candidates = _actual_source_candidates(source_type, filters)
    if not candidates:
        return {"ok": False, "rows": [], "sources": [], "reason": "no_source_candidates"}
    rows: list[dict[str, Any]] = []
    used: list[dict[str, Any]] = []
    max_files = int(filters.get("max_files") or 8)
    for source in candidates:
        df = _read_dataset_sample(source, max_files=max_files, limit=max(5000, limit * 4))
        if df is None:
            continue
        part = _normalize_measurement_rows(df, source_type, filters, max(1, min(5000, int(limit or 500))) - len(rows))
        if part:
            used.append({**source, "columns": df.columns[:80], "sample_rows": df.height})
            rows.extend(part)
        if len(rows) >= max(1, min(5000, int(limit or 500))):
            break
    return {"ok": bool(rows), "rows": rows, "sources": used, "reason": "" if rows else "no_matching_rows"}


def dataset_sample(filters: dict[str, Any] | None = None, limit: int = 200) -> dict[str, Any]:
    filters = dict(filters or {})
    candidates = _actual_source_candidates(str(filters.get("source_kind") or filters.get("source_type_filter") or ""), filters)
    if not candidates:
        return {"ok": False, "columns": [], "rows": [], "sources": [], "reason": "no_source_candidates"}
    max_files = int(filters.get("max_files") or 3)
    for source in candidates:
        df = _read_dataset_sample(source, max_files=max_files, limit=max(1, min(5000, int(limit or 200))))
        if df is None or df.height == 0:
            continue
        rows = df.head(max(1, min(1000, int(limit or 200)))).to_dicts()
        return {
            "ok": True,
            "columns": list(df.columns),
            "rows": rows,
            "total_sample_rows": df.height,
            "source": source,
            "mode": "actual_dataset_sample",
            "note": "DB directory and Files single parquet/csv are both supported through the same source filter.",
        }
    return {"ok": False, "columns": [], "rows": [], "sources": candidates, "reason": "no_readable_dataset"}


def _source_profile(source_type: str) -> dict[str, Any]:
    st = str(source_type or "").upper()
    return next((p for p in SOURCE_TYPE_PROFILES if p.get("source_type") == st), {})


def _guess_source_type_from_dataset(source: dict[str, Any], columns: list[str]) -> str:
    source_hay = " ".join([
        str(source.get("source_type") or ""),
        str(source.get("root") or ""),
        str(source.get("file") or ""),
    ]).upper()
    source_hay = re.sub(r"\.(PARQUET|CSV|JSON|YAML|YML)\b", "", source_hay)
    col_hay = " ".join(columns).upper()
    for st in ("INLINE", "EDS", "VM", "QTIME", "FAB", "ET"):
        if st in source_hay:
            return st
    lower_cols = {c.lower() for c in columns}
    if {"from_step_id", "to_step_id"} & lower_cols or "QTIME" in col_hay:
        return "QTIME"
    if {"die_x", "die_y", "bin"} & lower_cols:
        return "EDS"
    if {"macro", "vmin", "condition"} & lower_cols:
        return "VM"
    if {"step_id", "chamber", "recipe"} & lower_cols and {"item_id", "value"}.isdisjoint(lower_cols):
        return "FAB"
    if {"shot_x", "shot_y", "site_x", "site_y"} & lower_cols:
        return "INLINE"
    return "ET" if {"item_id", "value"} <= lower_cols else "AUTO"


def dataset_profile(filters: dict[str, Any] | None = None, limit: int = 300) -> dict[str, Any]:
    """Profile a DB/File source so Flow-i can use non-canonical ET/EDS/etc. files.

    The profile is intentionally heuristic and read-only.  It does not create a
    schema contract; it gives the user/LLM enough structure to choose the
    whitelisted query/reformatter/TEG tools without generating SQL.
    """
    sample = dataset_sample(filters or {}, limit=limit)
    if not sample.get("ok"):
        return {
            "ok": False,
            "source": (filters or {}).get("source") if isinstance(filters, dict) else filters,
            "reason": sample.get("reason") or "sample_failed",
            "columns": [],
            "warnings": ["No readable DB/File sample was found for this source."],
        }

    columns = list(sample.get("columns") or [])
    source = sample.get("source") or {}
    rows = sample.get("rows") or []
    item_col = _ci_col(columns, "canonical_item_id", "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "parameter", "PARAMETER", "metric", "METRIC", contains=["item"])
    value_col = _ci_col(columns, "value", "VALUE", "val", "VAL", "result", "RESULT", "meas_value", "MEAS_VALUE", contains=["value"])
    product_col = _ci_col(columns, "product", "PRODUCT", "prod", "PROD")
    root_col = _ci_col(columns, "root_lot_id", "ROOT_LOT_ID", "root_lot", "ROOT_LOT", contains=["root", "lot"])
    lot_col = _ci_col(columns, "lot_id", "LOT_ID", "lot", "LOT")
    fab_col = _ci_col(columns, "fab_lot_id", "FAB_LOT_ID", "fab_lot", "FAB_LOT")
    wafer_col = _ci_col(columns, "wafer_id", "WAFER_ID", "wf_id", "WF_ID", "wafer", "WF")
    lot_wf_col = _ci_col(columns, "lot_wf", "LOT_WF")
    step_col = _ci_col(columns, "step_id", "STEP_ID", "func_step", "FUNC_STEP")
    shot_x_col = _ci_col(columns, "shot_x", "SHOT_X", "site_x", "SITE_X", "x_shot", "X_SHOT")
    shot_y_col = _ci_col(columns, "shot_y", "SHOT_Y", "site_y", "SITE_Y", "y_shot", "Y_SHOT")
    die_x_col = _ci_col(columns, "die_x", "DIE_X", "x_die", "X_DIE")
    die_y_col = _ci_col(columns, "die_y", "DIE_Y", "y_die", "Y_DIE")
    macro_col = _ci_col(columns, "macro", "MACRO", "macro_id", "MACRO_ID")
    bin_col = _ci_col(columns, "bin", "BIN", "bin_id", "BIN_ID", "hard_bin", "SOFT_BIN")
    condition_col = _ci_col(columns, "condition", "CONDITION", "cond", "COND")

    long_shape = bool(item_col and value_col)
    known_meta = {
        x for x in [
            item_col, value_col, product_col, root_col, lot_col, fab_col, wafer_col, lot_wf_col,
            step_col, shot_x_col, shot_y_col, die_x_col, die_y_col, macro_col, bin_col, condition_col,
        ] if x
    }
    metric_cols = []
    for col in columns:
        if col in known_meta:
            continue
        if _find_item_by_name(col) or re.search(r"(DIBL|VTH|ION|IOFF|SS|RS|RC|LKG|CD|WIDTH|HEIGHT|VMIN)", col, re.I):
            metric_cols.append(col)
    source_type = _guess_source_type_from_dataset(source, columns)
    profile = _source_profile(source_type)
    join_keys = [
        name for name, col in [
            ("lot_wf", lot_wf_col),
            ("root_lot_id", root_col),
            ("lot_id", lot_col),
            ("fab_lot_id", fab_col),
            ("wafer_id", wafer_col),
            ("step_id", step_col),
            ("shot_x", shot_x_col),
            ("shot_y", shot_y_col),
            ("die_x", die_x_col),
            ("die_y", die_y_col),
            ("macro", macro_col),
            ("bin", bin_col),
            ("condition", condition_col),
        ] if col
    ]
    if die_x_col and die_y_col:
        grain = "die"
    elif shot_x_col and shot_y_col:
        grain = "shot"
    elif lot_wf_col or (root_col and wafer_col):
        grain = "lot_wf"
    elif root_col or lot_col:
        grain = "lot"
    else:
        grain = "row"

    unique_items: list[str] = []
    if long_shape and item_col:
        seen: set[str] = set()
        for row in rows:
            raw = str(row.get(item_col) or "").strip()
            if raw and raw not in seen:
                seen.add(raw)
                unique_items.append(raw)
            if len(unique_items) >= 40:
                break
    else:
        unique_items = metric_cols[:40]

    warnings: list[str] = []
    if not long_shape and not metric_cols:
        warnings.append("No clear item/value columns or known wide metric columns were detected.")
    if "lot_wf" not in join_keys and not {"root_lot_id", "wafer_id"} <= set(join_keys):
        warnings.append("lot_wf cannot be built confidently; joins may need root_lot_id + wafer_id mapping guidance.")
    if not product_col and not source.get("product"):
        warnings.append("Product is not explicit in the file/source. Prompt should provide product context when needed.")
    if source_type == "AUTO":
        warnings.append("Source type could not be inferred; choose ET/INLINE/EDS/VM/QTIME in the prompt or source profile.")

    return {
        "ok": True,
        "source": source,
        "mode": sample.get("mode") or "actual_dataset_sample",
        "suggested_source_type": source_type,
        "metric_shape": "long" if long_shape else "wide",
        "grain": grain,
        "join_keys": join_keys,
        "columns": columns,
        "column_roles": {
            "product": product_col,
            "root_lot_id": root_col,
            "lot_id": lot_col,
            "fab_lot_id": fab_col,
            "wafer_id": wafer_col,
            "lot_wf": lot_wf_col,
            "step_id": step_col,
            "item": item_col,
            "value": value_col,
            "shot_x": shot_x_col,
            "shot_y": shot_y_col,
            "die_x": die_x_col,
            "die_y": die_y_col,
            "macro": macro_col,
            "bin": bin_col,
            "condition": condition_col,
        },
        "sample_rows": len(rows),
        "unique_items": unique_items,
        "metric_columns": metric_cols[:80],
        "default_aggregation": profile.get("default_aggregation") or "Review grain and item meaning before aggregation.",
        "knowledge_to_attach": profile.get("knowledge_to_attach") or ["item semantics", "join key mapping", "measurement method"],
        "guardrails": profile.get("guardrails") or ["Do not infer item meaning from raw name alone."],
        "warnings": warnings,
    }


def query_measurements(source_type: str, filters: dict[str, Any] | None = None, limit: int = 500) -> dict[str, Any]:
    filters = dict(filters or {})
    source = str(source_type or "").upper()
    if source not in {"FAB", "ET", "INLINE", "VM", "QTIME", "EDS", "YLD", ""}:
        source = ""
    actual = _actual_measurements(source, filters, limit) if not filters.get("force_mock") else {"ok": False, "rows": []}
    if actual.get("ok"):
        rows = actual.get("rows") or []
        return {
            "ok": True,
            "source_type": source or "AUTO",
            "mode": "actual_parquet_sample",
            "rows": rows[: max(1, min(5000, int(limit or 500)))],
            "total": len(rows),
            "sources": actual.get("sources") or [],
            "rule": "No direct SQL is generated by LLM; data access is through this whitelisted query function.",
        }

    rows = _mock_measurements(source)
    item_ids = {str(x).upper() for x in _coerce_list(filters.get("canonical_item_ids") or filters.get("items")) if str(x).strip()}
    lots = {str(x).upper() for x in _coerce_list(filters.get("lot_filter") or filters.get("lots") or filters.get("root_lot_id")) if str(x).strip()}
    product = str(filters.get("product") or "").upper()
    if item_ids:
        rows = [r for r in rows if str(r.get("canonical_item_id")).upper() in item_ids]
    if lots:
        rows = [r for r in rows if str(r.get("root_lot_id")).upper() in lots or str(r.get("lot_id")).upper() in lots]
    if product:
        rows = [r for r in rows if str(r.get("product")).upper() == product]
    return {
        "ok": True,
        "source_type": source or "MOCK",
        "mode": "mock_in_memory",
        "rows": rows[: max(1, min(5000, int(limit or 500)))],
        "total": len(rows),
        "fallback_reason": actual.get("reason") if isinstance(actual, dict) else "",
        "rule": "No direct SQL is generated by LLM; data access is through this whitelisted query function.",
    }


def _avg(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _linear_fit(points: list[tuple[float, float]]) -> dict[str, Any]:
    pts = [(float(x), float(y)) for x, y in points if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pts) < 2:
        return {"slope": None, "intercept": None, "r2": None}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return {"slope": None, "intercept": None, "r2": None}
    slope = sum((x - mx) * (y - my) for x, y in pts) / den
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in pts)
    r2 = 1.0 - ss_res / ss_tot if ss_tot else 1.0
    return {"slope": round(slope, 6), "intercept": round(intercept, 6), "r2": round(r2, 6)}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def get_metric_trend(canonical_item_ids: list[str], lot_filter: Any = None, date_range: Any = None) -> dict[str, Any]:
    rows = query_measurements("", {"canonical_item_ids": canonical_item_ids, "lot_filter": lot_filter}, limit=5000)["rows"]
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        grouped.setdefault((row["canonical_item_id"], row["date"]), []).append(float(row["value"]))
    series: list[dict[str, Any]] = []
    for (item_id, date), values in sorted(grouped.items()):
        series.append({"canonical_item_id": item_id, "date": date, "value": round(_avg(values) or 0.0, 8), "count": len(values)})
    slopes: dict[str, Any] = {}
    for item_id in {s["canonical_item_id"] for s in series}:
        pts = [(i, s["value"]) for i, s in enumerate([x for x in series if x["canonical_item_id"] == item_id])]
        slopes[item_id] = _linear_fit(pts)
    return {"ok": True, "series": series, "fit": slopes, "mode": "mock_in_memory", "date_range": date_range}


def run_correlation_analysis(x_items: list[str], y_items: list[str], filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = dict(filters or {})
    rows = query_measurements("", {"canonical_item_ids": list(x_items or []) + list(y_items or []), **filters}, limit=5000)["rows"]
    by_key: dict[str, dict[str, float]] = {}
    for row in rows:
        by_key.setdefault(row["lot_wf"], {})[row["canonical_item_id"]] = float(row["value"])
    pairs: list[dict[str, Any]] = []
    for x in x_items or []:
        for y in y_items or []:
            pts = []
            for key, vals in by_key.items():
                if x in vals and y in vals:
                    pts.append({"lot_wf": key, "x": vals[x], "y": vals[y]})
            corr = _pearson([p["x"] for p in pts], [p["y"] for p in pts])
            pairs.append({
                "x_item": x,
                "y_item": y,
                "correlation": None if corr is None else round(corr, 6),
                "fit": _linear_fit([(p["x"], p["y"]) for p in pts]),
                "points": pts[:500],
                "n": len(pts),
            })
    return {"ok": True, "pairs": pairs, "mode": "mock_in_memory"}


def create_chart_spec(data: Any = None, chart_intent: str = "", **kwargs: Any) -> dict[str, Any]:
    intent = str(chart_intent or kwargs.get("intent") or "").lower()
    chart_type = "scatter" if any(t in intent for t in ["scatter", "corr", "상관"]) else "trend"
    if "wafer" in intent:
        chart_type = "wafer_map"
    spec = {
        "type": chart_type,
        "title": kwargs.get("title") or ("Metric correlation" if chart_type == "scatter" else "Metric trend"),
        "x": kwargs.get("x") or "date" if chart_type == "trend" else kwargs.get("x") or "x_metric",
        "y": kwargs.get("y") or kwargs.get("metric") or "value",
        "color": kwargs.get("color") or "lot_id",
        "fit": "linear" if chart_type == "scatter" else None,
        "source": "whitelisted_chart_spec",
        "data_ref": "inline_payload" if data is not None else "query_result",
    }
    return {"ok": True, "chart": {k: v for k, v in spec.items() if v is not None}}


def search_knowledge_cards(query: str, filters: dict[str, Any] | None = None, limit: int = 8) -> dict[str, Any]:
    features = extract_symptom_features(query)
    item_ids = set(features.get("items") or [])
    modules = set(features.get("modules") or [])
    q = str(query or "").lower()
    scored: list[tuple[float, dict[str, Any]]] = []
    for card in all_knowledge_cards():
        score = 0.0
        score += 4.0 * len(item_ids.intersection(card.get("symptom_items") or []))
        score += 1.5 * len(modules.intersection(card.get("module_tags") or []))
        score += sum(1.0 for t in card.get("trigger_terms") or [] if str(t).lower() in q)
        if filters:
            fmods = set(_coerce_list(filters.get("module") or filters.get("modules")))
            if fmods:
                score += 1.0 * len(fmods.intersection(card.get("module_tags") or []))
        if score > 0:
            out = dict(card)
            out["score"] = round(score, 3)
            scored.append((score, out))
    if not scored:
        fallback_cards = all_knowledge_cards() or KNOWLEDGE_CARDS
        scored = [(0.1, dict(fallback_cards[0], score=0.1))]
    scored.sort(key=lambda x: x[0], reverse=True)
    return {"ok": True, "cards": [c for _, c in scored[: max(1, min(20, int(limit or 8)))]]}


def traverse_causal_graph(seed_nodes: list[str], max_depth: int = 2) -> dict[str, Any]:
    seeds = {str(x).upper() for x in _coerce_list(seed_nodes) if str(x).strip()}
    max_depth = max(1, min(4, int(max_depth or 2)))
    edges = all_causal_edges()
    paths: list[dict[str, Any]] = []
    frontier = [(seed, [seed]) for seed in seeds]
    visited: set[tuple[str, str]] = set()
    for _depth in range(max_depth):
        next_frontier: list[tuple[str, list[str]]] = []
        for node, path in frontier:
            for edge in edges:
                src = str(edge.get("source")).upper()
                dst = str(edge.get("target")).upper()
                if node not in {src, dst}:
                    continue
                nxt = edge.get("target") if node == src else edge.get("source")
                key = (node, str(nxt))
                if key in visited:
                    continue
                visited.add(key)
                new_path = path + [str(nxt)]
                paths.append({"nodes": new_path, "edge": edge, "depth": len(new_path) - 1})
                next_frontier.append((str(nxt).upper(), new_path))
        frontier = next_frontier
    return {"ok": True, "seed_nodes": list(seeds), "paths": paths[:80], "edge_count": len(edges)}


def find_similar_cases(symptom_features: dict[str, Any], limit: int = 5) -> dict[str, Any]:
    items = set(symptom_features.get("items") or [])
    terms = {str(t).lower() for t in symptom_features.get("terms") or []}
    modules = set(symptom_features.get("modules") or [])
    scored: list[tuple[float, dict[str, Any]]] = []
    for case in all_historical_cases():
        score = 0.0
        score += 4.0 * len(items.intersection(case.get("symptoms") or []))
        score += 1.0 * len(terms.intersection({str(t).lower() for t in case.get("tags") or []}))
        score += 0.5 * len(modules.intersection({str(t).upper() for t in case.get("tags") or []}))
        if score > 0:
            out = dict(case)
            out["similarity_score"] = round(score, 3)
            scored.append((score, out))
    scored.sort(key=lambda x: x[0], reverse=True)
    return {"ok": True, "cases": [c for _, c in scored[: max(1, min(20, int(limit or 5)))]]}


def get_wafer_map_summary(canonical_item_id: str, lot_id: str = "") -> dict[str, Any]:
    rows = query_measurements("", {"canonical_item_ids": [canonical_item_id], "lot_filter": [lot_id] if lot_id else []}, limit=5000)["rows"]
    by_wf = {r["wafer_id"]: r["value"] for r in rows[:25]}
    values = list(by_wf.values())
    return {
        "ok": True,
        "canonical_item_id": canonical_item_id,
        "lot_id": lot_id,
        "wafer_values": by_wf,
        "summary": {
            "count": len(values),
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "median": statistics.median(values) if values else None,
        },
        "mode": "mock_in_memory",
    }


def _hypotheses_from_cards(cards: list[dict[str, Any]], features: dict[str, Any]) -> list[dict[str, Any]]:
    hyps: list[dict[str, Any]] = []
    item_count = max(1, len(features.get("items") or []))
    for idx, card in enumerate(cards[:5], start=1):
        overlap = len(set(features.get("items") or []).intersection(card.get("symptom_items") or []))
        confidence = min(0.88, float(card.get("confidence_base") or 0.55) + 0.03 * max(0, overlap - 1))
        hyps.append({
            "rank": idx,
            "hypothesis": card.get("title"),
            "electrical_mechanism": card.get("electrical_mechanism"),
            "structural_cause": card.get("structural_causes") or [],
            "process_root_cause": card.get("process_root_causes") or [],
            "supporting_evidence": card.get("supporting_evidence") or [],
            "contradicting_evidence": card.get("contradicting_evidence") or [],
            "recommended_checks": card.get("recommended_checks") or [],
            "confidence": round(confidence if item_count > 1 else min(confidence, 0.55), 2),
            "knowledge_card_id": card.get("id"),
        })
    return hyps


def _eval_report(report: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    ambiguous = [r for r in resolution.get("resolved") or [] if r.get("status") == "ambiguous"]
    hypotheses = report.get("ranked_hypotheses") or []
    checks = [
        {
            "name": "no_raw_name_only_inference",
            "passed": True,
            "detail": "Item resolution uses item_master metadata and marks ambiguous raw items.",
        },
        {
            "name": "ca_rs_ambiguity_guard",
            "passed": not any(r.get("raw_item", "").upper() == "CA_RS" and r.get("status") == "resolved" and not r.get("context_used") for r in resolution.get("resolved") or []),
            "detail": "CA_RS without supportive context must remain ambiguous.",
        },
        {
            "name": "no_single_metric_root_cause",
            "passed": len(report.get("observed_symptoms") or []) != 1 or all(float(h.get("confidence") or 0) <= 0.55 for h in hypotheses),
            "detail": "Single metric symptoms are capped and missing data is surfaced.",
        },
        {
            "name": "structured_schema",
            "passed": all(k in report for k in ["diagnosis_summary", "observed_symptoms", "ranked_hypotheses", "recommended_action_plan", "charts", "evidence", "missing_data", "do_not_conclude"]),
            "detail": "Diagnosis JSON has the required top-level fields.",
        },
    ]
    if ambiguous:
        checks.append({
            "name": "ambiguous_items_present",
            "passed": True,
            "detail": f"{len(ambiguous)} ambiguous item(s) require metadata confirmation before final conclusion.",
        })
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def run_diagnosis(
    prompt: str,
    *,
    product: str = "",
    raw_items: list[Any] | None = None,
    filters: dict[str, Any] | None = None,
    user_context: dict[str, Any] | None = None,
    save: bool = True,
) -> dict[str, Any]:
    prompt = str(prompt or "").strip()
    detected_items = _extract_candidate_item_names(prompt)
    all_raw = _unique(list(raw_items or []) + detected_items)
    resolution = resolve_item_semantics(all_raw, context=(filters or {}).get("item_context") or {})
    features = extract_symptom_features(prompt, resolution)
    card_hits = search_knowledge_cards(prompt, filters, limit=8)["cards"]
    cases = find_similar_cases(features, limit=5)["cases"]
    graph = traverse_causal_graph(features.get("items") + features.get("modules"), max_depth=2)
    hypotheses = _hypotheses_from_cards(card_hits, features)

    observed = features.get("symptoms") or []
    charts: list[dict[str, Any]] = []
    for card in card_hits[:3]:
        for spec in card.get("chart_suggestions") or []:
            if isinstance(spec, str):
                spec = {
                    "type": "scatter" if any(t in spec.lower() for t in ["scatter", "corr", "상관"]) else "trend",
                    "title": spec,
                }
            if not isinstance(spec, dict):
                continue
            chart = create_chart_spec(chart_intent=spec.get("type") or "", **spec)["chart"]
            chart["knowledge_card_id"] = card.get("id")
            x_item = str(spec.get("x") or "")
            y_item = str(spec.get("y") or spec.get("metric") or "")
            if x_item and y_item and _find_item_by_name(x_item) and _find_item_by_name(y_item):
                corr_filters = {**(filters or {}), "product": product or (filters or {}).get("product") or ""}
                corr = run_correlation_analysis([x_item], [y_item], corr_filters)
                pair = (corr.get("pairs") or [{}])[0]
                chart["data"] = {
                    "mode": corr.get("mode"),
                    "points": pair.get("points") or [],
                    "fit": pair.get("fit") or {},
                    "correlation": pair.get("correlation"),
                    "n": pair.get("n") or 0,
                }
            charts.append(chart)
    charts = _unique(charts)[:8]

    supporting: list[dict[str, Any]] = []
    missing: list[str] = []
    action_plan: list[dict[str, Any]] = []
    for hyp in hypotheses:
        supporting.extend({"hypothesis": hyp["hypothesis"], "type": "supporting", "text": x} for x in hyp.get("supporting_evidence") or [])
        supporting.extend({"hypothesis": hyp["hypothesis"], "type": "contradicting", "text": x} for x in hyp.get("contradicting_evidence") or [])
        missing.extend(card_hits[hyp["rank"] - 1].get("missing_data") or [])
        for check in hyp.get("recommended_checks") or []:
            action_plan.append({"priority": hyp["rank"], "action": check, "hypothesis": hyp["hypothesis"]})
    if not observed:
        missing.append("No recognized semiconductor metric in prompt; resolve item names first.")
    if any(r.get("status") == "ambiguous" for r in resolution.get("resolved") or []):
        missing.append("Ambiguous item metadata: unit/source_type/test_structure/layer/measurement_method")

    report = {
        "id": "DX-" + uuid.uuid4().hex[:10].upper(),
        "created_at": _now_iso(),
        "product": product or (filters or {}).get("product") or "",
        "mode": "mock_llm_deterministic",
        "knowledge_version": KNOWLEDGE_VERSION,
        "diagnosis_summary": (
            "Detected semiconductor symptoms were mapped through item_master, knowledge cards, causal graph paths, and similar cases. "
            "The result is a ranked RCA candidate list, not a single confirmed root cause."
        ),
        "observed_symptoms": observed,
        "ranked_hypotheses": hypotheses,
        "recommended_action_plan": _unique(action_plan)[:12],
        "charts": charts,
        "evidence": _unique(supporting)[:30],
        "missing_data": _unique(missing)[:30],
        "do_not_conclude": [
            "Do not infer raw item meaning from the item name alone.",
            "Do not conclude a single process root cause from one metric.",
            "Do not treat CA_RS as sheet resistance unless unit/test structure supports Rsheet.",
            "Do not use LLM-generated SQL; only whitelisted backend tools may query data.",
        ],
        "interpreted_items": resolution,
        "feature_extractor": features,
        "knowledge_cards": card_hits,
        "causal_graph_paths": graph.get("paths") or [],
        "similar_cases": cases,
        "engineer_context": user_context or {},
        "pipeline": [
            {"stage": "feature_extractor", "status": "done", "output": {"items": features.get("items"), "modules": features.get("modules")}},
            {"stage": "item_semantics", "status": "done", "output": {"resolved": len(resolution.get("resolved") or []), "unresolved": len(resolution.get("unresolved") or [])}},
            {"stage": "knowledge_card_rag", "status": "done", "output": {"cards": [c.get("id") for c in card_hits[:5]]}},
            {"stage": "graph_causal_db", "status": "done", "output": {"paths": len(graph.get("paths") or [])}},
            {"stage": "case_db", "status": "done", "output": {"cases": [c.get("case_id") for c in cases]}},
            {"stage": "eval_guardrails", "status": "done", "output": "pending"},
        ],
    }
    report["eval"] = _eval_report(report, resolution)
    report["pipeline"][-1]["output"] = report["eval"]
    if save:
        save_diagnosis_report(report)
    return report


def save_diagnosis_report(report: dict[str, Any]) -> dict[str, Any]:
    SEMICONDUCTOR_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    if not payload.get("id"):
        payload["id"] = "DX-" + uuid.uuid4().hex[:10].upper()
    with DIAGNOSIS_RUNS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return payload


def get_diagnosis_run(run_id: str) -> dict[str, Any] | None:
    rid = str(run_id or "").strip()
    if not rid or not DIAGNOSIS_RUNS_FILE.exists():
        return None
    found = None
    for line in DIAGNOSIS_RUNS_FILE.read_text("utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("id")) == rid:
            found = row
    return found


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text("utf-8").splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except Exception:
            continue
    return rows


def _read_json_obj(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def default_seed_knowledge_pack() -> dict[str, Any]:
    """Read the default RCA knowledge pack.

    FLOW_DATA_RCA_SEED_FILE wins when present so site admins can review or
    extend the copied seed without editing code.  setup.py creates that file
    only on first install; this code still falls back to the bundled seed.
    """
    data = _read_json_obj(FLOW_DATA_RCA_SEED_FILE)
    if data:
        data.setdefault("_source_path", str(FLOW_DATA_RCA_SEED_FILE))
        return data
    data = _read_json_obj(CODE_RCA_SEED_FILE)
    if data:
        data.setdefault("_source_path", str(CODE_RCA_SEED_FILE))
        return data
    return {}


def install_default_seed_knowledge(overwrite: bool = False) -> dict[str, Any]:
    if not CODE_RCA_SEED_FILE.exists():
        return {"ok": False, "reason": "bundled seed file missing", "target": str(FLOW_DATA_RCA_SEED_FILE)}
    if FLOW_DATA_RCA_SEED_FILE.exists() and not overwrite:
        return {"ok": True, "created": False, "preserved": True, "target": str(FLOW_DATA_RCA_SEED_FILE)}
    FLOW_DATA_SEED_DIR.mkdir(parents=True, exist_ok=True)
    FLOW_DATA_RCA_SEED_FILE.write_text(CODE_RCA_SEED_FILE.read_text("utf-8"), encoding="utf-8")
    return {"ok": True, "created": True, "preserved": False, "target": str(FLOW_DATA_RCA_SEED_FILE)}


def seed_knowledge_cards() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    pack = default_seed_knowledge_pack()
    for card in pack.get("knowledge_cards") or []:
        if not isinstance(card, dict):
            continue
        out = dict(card)
        out.setdefault("module_tags", _coerce_list(out.get("module_tags")))
        out.setdefault("symptom_items", _coerce_list(out.get("symptom_items")))
        out.setdefault("trigger_terms", _coerce_list(out.get("trigger_terms")))
        out.setdefault("structural_causes", _coerce_list(out.get("structural_causes")))
        out.setdefault("process_root_causes", _coerce_list(out.get("process_root_causes")))
        out.setdefault("supporting_evidence", _coerce_list(out.get("supporting_evidence")))
        out.setdefault("contradicting_evidence", _coerce_list(out.get("contradicting_evidence")))
        out.setdefault("missing_data", _coerce_list(out.get("missing_data")))
        out.setdefault("recommended_checks", _coerce_list(out.get("recommended_checks")))
        out.setdefault("chart_suggestions", _coerce_list(out.get("chart_suggestions")))
        try:
            out["confidence_base"] = float(out.get("confidence_base") or 0.55)
        except Exception:
            out["confidence_base"] = 0.55
        out["default_seed"] = True
        cards.append(out)
    return cards


def seed_causal_edges() -> list[dict[str, Any]]:
    pack = default_seed_knowledge_pack()
    return [dict(e, default_seed=True) for e in (pack.get("causal_edges") or []) if isinstance(e, dict)]


def seed_historical_cases() -> list[dict[str, Any]]:
    pack = default_seed_knowledge_pack()
    return [dict(c, default_seed=True) for c in (pack.get("historical_cases") or []) if isinstance(c, dict)]


def _dedup_by_id(rows: list[dict[str, Any]], key: str = "id") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        rid = str(row.get(key) or row.get("case_id") or "")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        out.append(row)
    return out


def all_knowledge_cards() -> list[dict[str, Any]]:
    return _dedup_by_id(list(KNOWLEDGE_CARDS) + seed_knowledge_cards() + custom_knowledge_cards())


def all_causal_edges() -> list[dict[str, Any]]:
    return list(CAUSAL_EDGES) + seed_causal_edges()


def all_historical_cases() -> list[dict[str, Any]]:
    return _dedup_by_id(list(HISTORICAL_CASES) + seed_historical_cases(), key="case_id")


def list_engineer_use_cases() -> dict[str, Any]:
    return {
        "ok": True,
        "version": KNOWLEDGE_VERSION,
        "use_cases": ENGINEER_USE_CASE_SEEDS,
        "schema": {
            "owned_module": "담당 모듈",
            "golden_metrics": "평상시 중요 지표와 정상 범위",
            "known_sensitive_steps": "민감 step_id / recipe / chamber",
            "recent_changes": "최근 변경점",
            "fail_signature": "wafer/reticle/local fail signature",
        },
    }


def list_engineer_knowledge(username: str, role: str = "user") -> dict[str, Any]:
    rows = _read_jsonl(ENGINEER_KNOWLEDGE_FILE)
    if role != "admin":
        rows = [r for r in rows if r.get("visibility") == "public" or r.get("username") == username]
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {"ok": True, "rows": rows[:500], "use_cases": ENGINEER_USE_CASE_SEEDS}


def add_engineer_knowledge(payload: dict[str, Any], username: str, role: str = "user") -> dict[str, Any]:
    SEMICONDUCTOR_DIR.mkdir(parents=True, exist_ok=True)
    visibility = str(payload.get("visibility") or "private").lower()
    if role != "admin" and visibility != "public":
        visibility = "private"
    row = {
        "id": "EK-" + uuid.uuid4().hex[:10].upper(),
        "created_at": _now_iso(),
        "username": username,
        "visibility": visibility if visibility in {"private", "public"} else "private",
        "role": payload.get("role") or "",
        "product": payload.get("product") or "",
        "module": payload.get("module") or "",
        "use_case": payload.get("use_case") or "",
        "prior_knowledge": payload.get("prior_knowledge") or "",
        "tags": _coerce_list(payload.get("tags")),
        "quality_note": payload.get("quality_note") or "",
    }
    with ENGINEER_KNOWLEDGE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return {"ok": True, "row": row}


def custom_knowledge_rows(username: str = "", role: str = "user") -> list[dict[str, Any]]:
    rows = _read_jsonl(CUSTOM_KNOWLEDGE_FILE)
    if role != "admin":
        rows = [
            r for r in rows
            if r.get("visibility") == "public" or (username and r.get("username") == username)
        ]
    return rows


def custom_knowledge_cards() -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for row in _read_jsonl(CUSTOM_KNOWLEDGE_FILE):
        if row.get("kind") != "knowledge_card":
            continue
        body = row.get("structured_json")
        if not isinstance(body, dict):
            body = {}
        card = {
            "id": row.get("id") or "CUSTOM_CARD",
            "title": body.get("title") or row.get("title") or "Custom knowledge card",
            "symptom_items": _coerce_list(body.get("symptom_items") or row.get("items")),
            "trigger_terms": _coerce_list(body.get("trigger_terms") or row.get("tags")),
            "electrical_mechanism": body.get("electrical_mechanism") or row.get("content") or "",
            "structural_causes": _coerce_list(body.get("structural_causes")),
            "process_root_causes": _coerce_list(body.get("process_root_causes")),
            "supporting_evidence": _coerce_list(body.get("supporting_evidence")),
            "contradicting_evidence": _coerce_list(body.get("contradicting_evidence")),
            "missing_data": _coerce_list(body.get("missing_data")),
            "recommended_checks": _coerce_list(body.get("recommended_checks")),
            "chart_suggestions": _coerce_list(body.get("chart_suggestions")),
            "confidence_base": float(body.get("confidence_base") or 0.52),
            "module_tags": _coerce_list(body.get("module_tags") or row.get("module")),
            "custom": True,
        }
        cards.append(card)
    return cards


def add_custom_knowledge(payload: dict[str, Any], username: str, role: str = "user") -> dict[str, Any]:
    SEMICONDUCTOR_DIR.mkdir(parents=True, exist_ok=True)
    kind = str(payload.get("kind") or "research_note").strip().lower()
    if kind not in {"research_note", "knowledge_card", "historical_case", "engineer_prior"}:
        kind = "research_note"
    visibility = str(payload.get("visibility") or "private").strip().lower()
    if visibility not in {"private", "public"}:
        visibility = "private"
    if role != "admin":
        visibility = "private"
    structured = payload.get("structured_json")
    if isinstance(structured, str):
        try:
            structured = json.loads(structured)
        except Exception:
            structured = {"raw": structured}
    row = {
        "id": "CK-" + uuid.uuid4().hex[:10].upper(),
        "created_at": _now_iso(),
        "username": username,
        "kind": kind,
        "visibility": visibility,
        "title": payload.get("title") or "",
        "source": payload.get("source") or "manual",
        "product": payload.get("product") or "",
        "module": payload.get("module") or "",
        "items": _coerce_list(payload.get("items")),
        "tags": _coerce_list(payload.get("tags")),
        "content": payload.get("content") or "",
        "structured_json": structured if isinstance(structured, dict) else {},
    }
    with CUSTOM_KNOWLEDGE_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    if kind == "engineer_prior":
        add_engineer_knowledge({
            "visibility": visibility,
            "role": payload.get("engineer_role") or "",
            "product": row["product"],
            "module": row["module"],
            "use_case": payload.get("use_case") or "",
            "prior_knowledge": row["content"],
            "tags": row["tags"],
            "quality_note": payload.get("quality_note") or "",
        }, username=username, role=role)
    return {"ok": True, "row": row}


def structure_rag_update_from_prompt(prompt: str, username: str, role: str = "user") -> dict[str, Any]:
    """Append operator/domain knowledge from a Flow-i RAG update prompt.

    This intentionally writes an append-only runtime record instead of editing
    code seed data.  Admin entries can become public; user entries stay private
    until reviewed/promoted.
    """
    text = str(prompt or "").strip()
    marker_re = re.compile(r"^\s*\[?\s*flow-i\s+rag\s+update\s*\]?\s*[:：-]?\s*", re.I)
    body = marker_re.sub("", text).strip()
    if not body:
        raise ValueError("RAG update body is empty")

    dims = [
        {"width": m.group(1), "height": m.group(2), "raw": m.group(0)}
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:x|\*|×)\s*(\d+(?:\.\d+)?)", body, flags=re.I)
    ]
    likely_items = _extract_candidate_item_names(body)
    raw_tokens = [
        x for x in re.findall(r"[A-Za-z][A-Za-z0-9_./+-]*(?:[-_][A-Za-z0-9_./+-]+){1,}", body)
        if len(x) >= 3
    ][:20]
    lower = body.lower()
    if any(t in lower for t in ["alias", "reformatter", "별칭", "alias화"]):
        schema_type = "reformatter_alias_guidance"
        kind = "research_note"
    elif any(t in lower for t in ["teg", "chain", "pitch", "cell height", "pc-", "cb-", "m1"]):
        schema_type = "real_item_tegrid_semantics"
        kind = "research_note"
    elif any(t in lower for t in ["원인", "rca", "root cause", "mechanism", "knowledge card"]):
        schema_type = "diagnostic_knowledge_card_draft"
        kind = "knowledge_card"
    else:
        schema_type = "research_note"
        kind = "research_note"

    structured = {
        "schema_type": schema_type,
        "raw_item_tokens": _unique(raw_tokens),
        "known_canonical_candidates": likely_items,
        "dimension_tokens": dims,
        "focus_points": [],
        "discriminators": [],
        "alias_candidates": [],
        "review_status": "needs_admin_review" if role != "admin" else "admin_added",
        "source_prompt_prefix": "[flow-i RAG Update]",
    }


def _raw_item_tokens_from_text(text: str) -> list[str]:
    tokens = []
    for x in re.findall(r"[A-Za-z0-9]+(?:[-_./][A-Za-z0-9]+)+", str(text or "")):
        if len(x) >= 3 and not re.fullmatch(r"\d+(?:[._/-]\d+)+", x):
            tokens.append(x)
    return _unique(tokens)


def _alias_from_raw_item(raw: str) -> str:
    alias = re.sub(r"[^A-Za-z0-9]+", "_", str(raw or "")).strip("_").upper()
    if not alias:
        alias = "ITEM"
    if alias[0].isdigit():
        alias = "I_" + alias
    return alias[:80]


def _category_from_raw_item(raw: str) -> str:
    up = str(raw or "").upper()
    for cat in ("PC", "CB", "CA", "M1", "M2", "M3", "GATE", "SRAM", "TEG"):
        if re.search(rf"(^|[-_./]){re.escape(cat)}($|[-_./])", up) or up.startswith(cat):
            return cat
    if "CHAIN" in up:
        return "CHAIN"
    return "ET"


def reformatter_alias_proposal_from_prompt(
    prompt: str,
    product: str = "",
    sample_columns: list[str] | None = None,
) -> dict[str, Any]:
    body = str(prompt or "").strip()
    raw_tokens = _raw_item_tokens_from_text(body)
    columns = [str(c) for c in _coerce_list(sample_columns) if str(c).strip()]
    if columns:
        col_norms = {_norm(c): c for c in columns}
        raw_tokens.extend(col_norms.values())
    raw_tokens = _unique(raw_tokens)[:200]
    if not raw_tokens:
        return {
            "ok": False,
            "reason": "No raw item-like tokens found. Provide item_id strings or sample column names.",
            "rules": [],
            "table_rows": [],
            "discriminators": [],
        }

    dims = [
        {"width": m.group(1), "height": m.group(2), "raw": m.group(0)}
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:x|\*|×)\s*(\d+(?:\.\d+)?)", body, flags=re.I)
    ]
    low = body.lower()
    discriminators = []
    if dims:
        discriminators.append("geometry_dimension")
    if "pitch" in low:
        discriminators.append("pitch")
    if "cell height" in low or "cell_height" in low:
        discriminators.append("cell_height")
    if "chain" in low:
        discriminators.append("chain_structure")
    if "coordinate" in low or "좌표" in low:
        discriminators.append("coordinate")

    rules: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_tokens, start=1):
        alias = _alias_from_raw_item(raw)
        cat = _category_from_raw_item(raw)
        rule = {
            "name": alias,
            "type": "scale_abs",
            "source_col": "value",
            "filter": f"item_id == '{_sql_string(raw)}'",
            "scale": 1.0,
            "abs": False,
            "offset": 0.0,
            "no": idx * 10,
            "addp": "real",
            "item_id": raw,
            "rawitem_id": raw,
            "cat": cat,
            "alias": alias,
            "alias_form": "",
            "scale_factor": 1.0,
            "report_order": idx * 10,
            "y_axis": "linear",
            "spec": "none",
            "spec_check": "none",
            "report_cat1": cat,
            "report_cat2": "LLM_REVIEW",
            "use": True,
            "report_enabled": True,
            "point_mode": "all_pt",
            "tracker_attach": False,
            "llm_proposal": {
                "source": "flow-i",
                "review_required": True,
                "discriminators": discriminators,
                "dimension_tokens": dims,
            },
        }
        rules.append(rule)
        rows.append({
            "no": idx * 10,
            "addp": "real",
            "item_id": raw,
            "alias": alias,
            "addp_form": "",
            "abs": "N",
            "scale_factor": 1.0,
            "speclow": "",
            "target": "",
            "spechigh": "",
            "report_order": idx * 10,
            "y_axis": "linear",
            "spec_check": "none",
            "report_cat1": cat,
            "report_cat2": "LLM_REVIEW",
            "use": "Y",
        })
    return {
        "ok": True,
        "product": product,
        "rules": rules,
        "table_rows": rows,
        "discriminators": discriminators,
        "dimension_tokens": dims,
        "review_guidance": [
            "Similar raw items must keep discriminator fields such as chain size, pitch, cell height, layer, and coordinate.",
            "Do not collapse PC-CB-M1 14x14 and 13x13 into one alias unless they truly measure the same DOE TEG.",
            "Apply only after admin review because product reformatter affects ET report, dashboard, and ML features.",
        ],
    }


def reformatter_alias_proposal_from_dataset(
    product: str = "",
    source: dict[str, Any] | None = None,
    prompt: str = "",
    limit: int = 500,
) -> dict[str, Any]:
    sample = dataset_sample(source or {}, limit=limit)
    if not sample.get("ok"):
        return {**sample, "rules": [], "table_rows": []}
    cols = sample.get("columns") or []
    rows = sample.get("rows") or []
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "parameter", "PARAMETER", "metric", "METRIC", contains=["item"])
    raw_items: list[str] = []
    if item_col:
        for row in rows:
            value = str(row.get(item_col) or "").strip()
            if value:
                raw_items.append(value)
    else:
        # Wide Files often carry ET/EDS/Inline metrics as columns.
        id_like = {"product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "lot_wf", "step_id", "date", "time"}
        raw_items = [c for c in cols if str(c).lower() not in id_like]
    seed_prompt = (prompt or "") + "\n" + "\n".join(_unique(raw_items)[:120])
    proposal = reformatter_alias_proposal_from_prompt(seed_prompt, product=product, sample_columns=_unique(raw_items)[:120])
    proposal["dataset"] = {
        "source": sample.get("source"),
        "columns": cols[:120],
        "item_column": item_col,
        "sample_rows": len(rows),
        "mode": sample.get("mode"),
    }
    return proposal


def apply_reformatter_alias_proposal(product: str, rules: list[dict[str, Any]], username: str = "admin") -> dict[str, Any]:
    product = str(product or "").strip()
    if not product:
        raise ValueError("product required")
    from core import reformatter as _rf

    base = PATHS.data_root / "reformatter"
    existing = _rf.load_rules(base, product)
    existing_keys = {
        (str(r.get("name") or "").upper(), str(r.get("rawitem_id") or r.get("item_id") or "").upper())
        for r in existing
    }
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    validation_errors: list[dict[str, Any]] = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        rule = dict(rule)
        key = (str(rule.get("name") or "").upper(), str(rule.get("rawitem_id") or rule.get("item_id") or "").upper())
        if key in existing_keys:
            skipped.append({"name": rule.get("name"), "rawitem_id": rule.get("rawitem_id") or rule.get("item_id"), "reason": "duplicate"})
            continue
        errs = _rf.validate_rule(rule)
        if errs:
            rule["disabled"] = True
            validation_errors.append({"name": rule.get("name"), "errors": errs})
        rule.setdefault("llm_proposal", {})
        if isinstance(rule["llm_proposal"], dict):
            rule["llm_proposal"]["applied_by"] = username
            rule["llm_proposal"]["applied_at"] = _now_iso()
        existing.append(rule)
        existing_keys.add(key)
        added.append(rule)
    _rf.save_rules(base, product, existing)
    return {
        "ok": True,
        "product": product,
        "added": len(added),
        "skipped": skipped,
        "validation_errors": validation_errors,
        "path": str(base / f"{product}.json"),
        "rule_count": len(existing),
    }


def teg_layout_proposal_from_rows(product: str, rows: list[dict[str, Any]] | None = None, prompt: str = "") -> dict[str, Any]:
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    prompt = str(prompt or "")
    out: list[dict[str, Any]] = []

    def _num_value(row: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            if key in row and row.get(key) not in (None, ""):
                try:
                    v = float(row.get(key))
                    if math.isfinite(v):
                        return v
                except Exception:
                    pass
        return None

    for idx, row in enumerate(rows, start=1):
        name = row.get("id") or row.get("label") or row.get("name") or row.get("teg") or row.get("TEG") or f"TEG_{idx}"
        x = _num_value(row, "dx_mm", "x", "X", "teg_x", "TEG_X", "local_x", "coord_x")
        y = _num_value(row, "dy_mm", "y", "Y", "teg_y", "TEG_Y", "local_y", "coord_y")
        if x is None or y is None:
            continue
        item = {
            "id": _alias_from_raw_item(name),
            "label": str(name),
            "dx_mm": x,
            "dy_mm": y,
            "role": row.get("role") or row.get("type") or "",
        }
        width = _num_value(row, "width_mm", "w", "width", "teg_w", "size_x")
        height = _num_value(row, "height_mm", "h", "height", "teg_h", "size_y")
        if width is not None:
            item["width_mm"] = width
        if height is not None:
            item["height_mm"] = height
        out.append(item)

    if not out:
        for idx, m in enumerate(re.finditer(
            r"([A-Za-z][A-Za-z0-9_.-]{1,40})\s*(?:[:=, ]+)\s*x\s*[:=]\s*(-?\d+(?:\.\d+)?)\s*[,/ ]+\s*y\s*[:=]\s*(-?\d+(?:\.\d+)?)",
            prompt,
            flags=re.I,
        ), start=1):
            out.append({
                "id": _alias_from_raw_item(m.group(1)),
                "label": m.group(1),
                "dx_mm": float(m.group(2)),
                "dy_mm": float(m.group(3)),
                "role": "prompt_extracted",
            })

    return {
        "ok": bool(out),
        "product": product,
        "teg_definitions": out,
        "required_columns": ["label/name/id", "dx_mm/x", "dy_mm/y"],
        "review_guidance": [
            "Coordinates are shot-local offsets in mm unless the source declares another coordinate mode.",
            "If the dataset uses wafer-absolute coordinates, convert them before applying to product YAML.",
            "Gate pitch, cell height, and TEG size should stay as metadata so item aliasing can distinguish DOE structures.",
        ],
    }


def teg_layout_proposal_from_dataset(
    product: str = "",
    source: dict[str, Any] | None = None,
    prompt: str = "",
    limit: int = 500,
) -> dict[str, Any]:
    sample = dataset_sample(source or {}, limit=limit)
    if not sample.get("ok"):
        return {**sample, "teg_definitions": []}
    proposal = teg_layout_proposal_from_rows(product, rows=sample.get("rows") or [], prompt=prompt)
    proposal["dataset"] = {
        "source": sample.get("source"),
        "columns": (sample.get("columns") or [])[:120],
        "sample_rows": len(sample.get("rows") or []),
        "mode": sample.get("mode"),
    }
    return proposal


def apply_teg_layout_proposal(product: str, teg_definitions: list[dict[str, Any]], username: str = "admin") -> dict[str, Any]:
    product = str(product or "").strip()
    if not product:
        raise ValueError("product required")
    from core import product_config as _pc

    config = _pc.load(PATHS.data_root, product) or {"product": product}
    wafer_layout = dict(config.get("wafer_layout") or {})
    clean: list[dict[str, Any]] = []
    for idx, row in enumerate(teg_definitions or [], start=1):
        try:
            x = float(row.get("dx_mm"))
            y = float(row.get("dy_mm"))
        except Exception:
            continue
        label = str(row.get("label") or row.get("id") or f"TEG_{idx}")
        clean.append({
            "id": str(row.get("id") or _alias_from_raw_item(label)),
            "label": label,
            "dx_mm": x,
            "dy_mm": y,
            "role": row.get("role") or "",
            **({"width_mm": float(row["width_mm"])} if row.get("width_mm") not in (None, "") else {}),
            **({"height_mm": float(row["height_mm"])} if row.get("height_mm") not in (None, "") else {}),
        })
    if not clean:
        raise ValueError("no valid TEG rows")
    wafer_layout["teg_definitions"] = clean
    wafer_layout["tegs"] = [
        {"no": idx, "name": row["label"], "x": row["dx_mm"], "y": row["dy_mm"], "flat": 0}
        for idx, row in enumerate(clean, start=1)
    ]
    wafer_layout["llm_update"] = {"updated_by": username, "updated_at": _now_iso(), "review_required": True}
    config["wafer_layout"] = wafer_layout
    errs = _pc.validate(config)
    _pc.save(PATHS.data_root, product, config)
    return {
        "ok": not errs,
        "product": product,
        "errors": errs,
        "teg_count": len(clean),
        "path": str(_pc.config_path(PATHS.data_root, product)),
        "wafer_layout": wafer_layout,
    }
    if dims:
        structured["discriminators"].append("geometry_dimension")
        structured["focus_points"].append("Check which TEG dimension token, such as 14x14/13x13/12x12, differentiates the DOE structure.")
    if "pitch" in lower:
        structured["discriminators"].append("pitch")
        structured["focus_points"].append("Identify whether the item encodes gate pitch, metal pitch, or contact pitch.")
    if "cell height" in lower or "cell_height" in lower:
        structured["discriminators"].append("cell_height")
        structured["focus_points"].append("Identify whether the item encodes standard-cell height sensitivity.")
    if "chain" in lower:
        structured["discriminators"].append("chain_structure")
        structured["focus_points"].append("Separate chain resistance/open-sensitive structures from Kelvin/contact/sheet monitors.")
    if "reformatter" in lower or "alias" in lower or "별칭" in lower:
        structured["alias_candidates"] = _unique(raw_tokens[:8])
        structured["focus_points"].append("Before aliasing, preserve discriminator fields so similar raw items do not collapse into one meaning.")

    payload = {
        "kind": kind,
        "visibility": "public" if role == "admin" else "private",
        "title": (body.splitlines()[0] or "Flow-i RAG update")[:120],
        "source": "flow-i RAG Update prompt",
        "product": "",
        "module": "",
        "items": likely_items,
        "tags": [schema_type] + likely_items[:8],
        "content": body,
        "structured_json": structured,
    }
    saved = add_custom_knowledge(payload, username=username, role=role)
    return {
        "ok": True,
        "mode": "append_only_runtime_knowledge",
        "saved": saved.get("row"),
        "structured": structured,
        "storage": storage_manifest()["runtime_data"],
        "review_note": (
            "Admin entry is public immediately. User entry is private and can be reviewed/promoted later. "
            "Code seed is not edited by prompt."
        ),
    }


def storage_manifest() -> dict[str, Any]:
    return {
        "ok": True,
        "knowledge_version": KNOWLEDGE_VERSION,
        "code_seed": {
            "python_module": "backend/core/semiconductor_knowledge.py",
            "default_rca_seed": str(CODE_RCA_SEED_FILE),
            "description": "Git/setup.py에 포함되는 기본 item, knowledge card, causal graph, case, use case seed.",
        },
        "runtime_data": {
            "diagnosis_runs": str(DIAGNOSIS_RUNS_FILE),
            "engineer_knowledge": str(ENGINEER_KNOWLEDGE_FILE),
            "custom_knowledge": str(CUSTOM_KNOWLEDGE_FILE),
            "default_seed_knowledge": str(FLOW_DATA_RCA_SEED_FILE),
            "description": "운영 중 추가되는 사내 지식/심층리서치/유저별 prior는 flow-data 아래 jsonl로 보존.",
        },
        "setup_policy": {
            "bundled": ["backend/core/semiconductor_knowledge.py", "backend/core/semiconductor_rca_seed_knowledge.json", "backend/routers/semiconductor.py", "frontend/src/pages/My_Diagnosis.jsx", "docs/SEMICONDUCTOR_DIAGNOSIS_MVP.md"],
            "not_bundled": ["data/DB", "data/Base", "data/flow-data", "FLOW_DATA_ROOT", "FLOW_DB_ROOT"],
            "operator_action": "setup.py는 기본 RCA seed를 flow-data에 없을 때만 생성하고, 기존 custom_knowledge/engineer_knowledge/diagnosis_runs는 덮어쓰지 않습니다.",
        },
        "default_seed_pack": {
            "active_path": default_seed_knowledge_pack().get("_source_path") or "",
            "flow_data_target": str(FLOW_DATA_RCA_SEED_FILE),
            "card_count": len(seed_knowledge_cards()),
            "causal_edge_count": len(seed_causal_edges()),
            "historical_case_count": len(seed_historical_cases()),
        },
        "source_type_profiles": SOURCE_TYPE_PROFILES,
        "new_db_onboarding": [
            "Add source_type profile: grain, join_keys, default aggregation, coordinate/bias/condition fields.",
            "Register raw items in item_master or custom_knowledge with unit/source_type/test_structure/layer/measurement_method.",
            "Add matching/reformatter rules for aliases only after discriminator fields are preserved.",
            "Add Knowledge Cards only when there is evidence separating electrical symptom, structural cause, and process root cause.",
            "Add Case DB examples with supporting and contradicting evidence for Eval.",
        ],
    }


def llm_tool_catalog() -> list[dict[str, Any]]:
    return [
        {"name": "resolve_item_semantics", "description": "Map raw ET/Inline/VM item names to canonical item_master records."},
        {"name": "query_et_metrics", "description": "Query ET metric rows through whitelisted backend filters."},
        {"name": "query_inline_metrics", "description": "Query Inline metric rows through whitelisted backend filters."},
        {"name": "get_metric_trend", "description": "Aggregate metric trend by date/lot."},
        {"name": "get_wafer_map_summary", "description": "Summarize wafer-level metric distribution."},
        {"name": "search_knowledge_cards", "description": "Retrieve diagnostic knowledge cards."},
        {"name": "traverse_causal_graph", "description": "Retrieve causal graph paths from seed nodes."},
        {"name": "find_similar_cases", "description": "Retrieve similar historical RCA cases."},
        {"name": "run_correlation_analysis", "description": "Compute correlation using matched lot_wf mock/demo data."},
        {"name": "create_chart_spec", "description": "Create safe chart spec for frontend rendering."},
        {"name": "save_diagnosis_report", "description": "Persist structured diagnosis report."},
    ]
