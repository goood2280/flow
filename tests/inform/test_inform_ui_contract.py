from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MY_INFORM = ROOT / "frontend" / "src" / "pages" / "My_Inform.jsx"


def test_inform_wizard_five_step_backend_contract_order():
    src = MY_INFORM.read_text(encoding="utf-8")

    for token in [
        'export const WIZARD_STEPS = ["lot", "module_reason", "splittable", "mail_preview", "review"]',
        '"/api/informs/config"',
        '"/api/splittable/lot-candidates"',
        '"/api/informs/splittable-snapshot"',
        '"/api/informs/modules/recipients"',
        '"/api/informs/modules/knob-map"',
        '"POST /api/informs"',
    ]:
        assert token in src

    ordered = [
        '"/api/informs/config"',
        '"/api/splittable/lot-candidates"',
        '"/api/informs/splittable-snapshot"',
        '"/api/informs/modules/recipients"',
        '"/api/informs/modules/knob-map"',
        '"POST /api/informs"',
    ]
    positions = [src.index(token) for token in ordered]
    assert positions == sorted(positions)
