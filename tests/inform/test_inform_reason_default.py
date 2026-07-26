from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402


def test_create_inform_defaults_empty_reason_to_pems(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")

    res = informs.create_inform(
        informs.InformCreate(lot_id="R1000", product="PRODA", module="GATE", reason="", text="body"),
        object(),
    )

    assert res["inform"]["reason"] == "PEMS"
