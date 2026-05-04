from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def test_attached_sets_roundtrip_into_detail_and_mail_html(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")

    attached = [{
        "id": "custom:set_a",
        "name": "Set A",
        "source": "custom",
        "columns": ["parameter", "#1", "#2"],
        "rows": [["KNOB_A", "10", "11"]],
        "columns_count": 3,
        "wafer_count": 1,
        "owner": "tester",
    }]
    created = informs.create_inform(
        informs.InformCreate(
            lot_id="R1000A.1",
            product="PRODA",
            module="GATE",
            reason="",
            text="body",
            fab_lot_id_at_save="R1000A.1",
            attached_sets=attached,
        ),
        object(),
    )["inform"]

    detail = informs.by_lot(object(), lot_id="R1000A.1")["informs"][0]
    html = informs._build_html_body(
        created,
        "",
        "",
        sender_username="tester",
        product_contacts=[{"name": "Owner A", "email": "owner@example.com"}],
        embed_table=created.get("embed_table"),
    )

    assert created["attachments"][0]["id"] == "custom:set_a"
    assert detail["attachments"][0]["name"] == "Set A"
    assert html.index("body") < html.index("제품 담당자")
    assert html.index("제품 담당자") < html.index("작성자")
    assert "Set A" not in html
    assert "KNOB_A" not in html
    assert "안녕하세요" not in html
    assert "background:#fffbeb" not in html
    assert "font-size:12pt" in html
