from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MY_INFORM = ROOT / "frontend" / "src" / "pages" / "My_Inform.jsx"


def test_inform_wizard_five_step_backend_contract_order():
    src = MY_INFORM.read_text(encoding="utf-8")

    for token in [
        'export const WIZARD_STEPS = ["lot", "module", "splittable", "mail_preview", "review"]',
        '"/api/informs/config"',
        '"/api/splittable/lot-candidates"',
        "prefix=${encodeURIComponent(rawLot)}",
        "lotCandidateRootScope(rawLot)",
        "root_lot_id=${encodeURIComponent(rootScope)}",
        "LOT_CANDIDATE_LIMIT = 20000",
        '"/api/informs/splittable-snapshot"',
        '"/api/informs/recipients"',
        '"/api/informs/mail-groups"',
        "Promise.all(requests)",
        "fab_lot_id_at_save = targetLot",
        "buildEmbedForLot",
        "lot_id: targetLot",
        "custom_cols: customCols",
        "LOT_ID 검색 (입력 즉시 필터)",
        "체크된 LOT_ID",
        'const lotIdFilterText = String(fabSearch || "").trim().toLowerCase();',
        "const needle = rawLot.toLowerCase();",
        'filter(o => !String(o.value || "").trim().toLowerCase().includes(needle))',
        'return { ...f, lot_id: nextFabs[0] || "", fab_lot_ids: nextFabs };',
        "여러 LOT_ID 중 가장 위에 선택된 LOT_ID만 미리보기로 표시합니다.",
        'gridTemplateRows: "auto 150px"',
        "height: 150",
        '"POST /api/informs"',
    ]:
        assert token in src
    assert ".slice(0, 500)" not in src
    assert "fabSearch || form.lot_id" not in src

    ordered = [
        '"/api/informs/config"',
        '"/api/splittable/lot-candidates"',
        '"/api/informs/splittable-snapshot"',
        '"/api/informs/recipients"',
        '"/api/informs/mail-groups"',
        '"POST /api/informs"',
    ]
    positions = [src.index(token) for token in ordered]
    assert positions == sorted(positions)


def test_inform_wizard_mail_note_is_plain_top_block():
    src = MY_INFORM.read_text(encoding="utf-8")

    assert 'fontSize: "12pt"' in src
    assert 'background: "#fffbeb"' not in src
    assert 'borderLeft: "4px solid #f59e0b"' not in src
