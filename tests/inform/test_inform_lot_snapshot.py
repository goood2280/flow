from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def test_create_inform_preserves_split_table_root_and_fab_lots(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)

    req = informs.InformCreate(**{
        "lot_id": "LOT029AA",
        "product": "PRODA",
        "module": "ET",
        "reason": "PEMS",
        "text": "check split lot",
        "embed_table": {
            "source": "SplitTable/PRODA @ LOT029AA",
            "columns": ["parameter"],
            "rows": [],
            "st_view": {
                "root_lot_id": "LOT029AA",
                "header_groups": [
                    {"label": "LOT029AA.1", "span": 2},
                    {"label": "LOT029AA.2", "span": 1},
                ],
                "wafer_fab_list": ["LOT029AA.1", "LOT029AA.2"],
            },
            "st_scope": {},
        },
    })
    resp = informs.create_inform(req, object())

    created = resp["inform"]
    assert created["root_lot_id"] == "LOT029AA"
    assert created["fab_lot_id_at_save"] == "LOT029AA.1, LOT029AA.2"

    by_lot = informs.by_lot(object(), lot_id="LOT029AA")
    assert by_lot["count"] == 1
