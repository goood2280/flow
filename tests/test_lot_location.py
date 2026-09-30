import csv
import io
import pytest
from fastapi.testclient import TestClient

from app import app
from routers.lot_location import parse_lot_ids, query_lot_locations


from core.auth import issue_token


@pytest.fixture(autouse=True)
def wip_cache(monkeypatch, tmp_path):
    """Exercise real parquet queries without depending on operator data."""
    import polars as pl
    from routers import lot_location

    path = tmp_path / "wip.parquet"
    pl.DataFrame({
        "lot_id": ["A1022A.2", "A1022A.2", "A1022A.2", "A1027C.1"],
        "root_lot_id": ["A1022", "A1022", "A1022", "A1027"],
        "wafer_id": ["10", "2", "1", "1"],
        "step_id": ["S1", "S1", "S1", "S2"],
        "function_step": ["공정 1", "공정 1", "공정 1", "공정 2"],
        "product": ["DEMO"] * 4,
    }).write_parquet(path)
    monkeypatch.setattr(lot_location.lot_progress_cache, "filebrowser_cache_parquet_file", lambda: path)
    monkeypatch.setattr(lot_location.fab_reference, "vehicle_matching_rows", lambda: [])


@pytest.fixture
def client():
    token, _ = issue_token("test_lot_location_user", "admin")
    return TestClient(app, headers={"x-session-token": token})


def test_parse_lot_ids_excel_formats():
    raw_excel_paste = """
    LOT_ID
    A1022A.2
    A1027C.1\tB1008A.1
    A1022A.2,C1000A.1; D2000B.1
    """
    lots = parse_lot_ids([], raw_text=raw_excel_paste)
    assert lots == ["A1022A.2", "A1027C.1", "B1008A.1", "C1000A.1", "D2000B.1"]


def test_parse_lot_ids_preserves_order_and_filters_headers():
    tokens = ["root_lot_id", "A1022", "a1022", "B2000", "A1022"]
    res = parse_lot_ids(tokens)
    assert res == ["A1022", "B2000"]


def test_query_lot_locations_parquet_cache():
    result = query_lot_locations(["A1022A.2", "UNKNOWN_LOT_999"])
    items = result.get("items") or []
    stats = result.get("stats") or {}

    assert stats["requested_count"] == 2
    assert "UNKNOWN_LOT_999" in stats["unmatched_lots"]

    if items:
        # Check wafer rows for A1022A.2
        assert stats["matched_lot_count"] >= 1
        first = items[0]
        assert first["lot_id"] == "A1022A.2"
        assert first["root_lot_id"] == "A1022"
        assert "wafer_id" in first
        assert "current_step_id" in first
        assert "step_desc" in first
        assert len(first["current_step_id"]) > 0
        assert len(first["step_desc"]) > 0

        # Check wafer numbers are in natural numeric ascending order
        wafer_nums = []
        for row in items:
            if row["lot_id"] == "A1022A.2":
                try:
                    wafer_nums.append(int(row["wafer_id"]))
                except ValueError:
                    pass
        if len(wafer_nums) > 1:
            assert wafer_nums == sorted(wafer_nums)


def test_api_lot_location_query(client):
    resp = client.post(
        "/api/lot-location/query",
        json={"lot_ids": ["A1022A.2"], "raw_text": "A1027C.1\nNOT_EXIST_LOT"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "stats" in data
    assert data["stats"]["requested_count"] == 3


def test_api_lot_location_export_csv(client):
    resp = client.post(
        "/api/lot-location/export-csv",
        json={"lot_ids": ["A1022A.2"]},
    )
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert "attachment" in resp.headers.get("content-disposition", "")

    content = resp.content.decode("utf-8")
    assert content.startswith("\ufeff")  # BOM check

    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    headers = reader.fieldnames
    assert "lot_id" in headers
    assert "root_lot_id" in headers
    assert "wafer_id" in headers
    assert "현step_id" in headers
    assert "현 step_desc" in headers

    rows = list(reader)
    if rows:
        assert rows[0]["lot_id"] == "A1022A.2"
        assert rows[0]["root_lot_id"] == "A1022"


def test_query_lot_locations_base_lot_matching():
    # Query without sublot suffix (A1022A instead of A1022A.2)
    result = query_lot_locations(["A1022A"])
    items = result.get("items") or []
    assert len(items) > 0
    assert any(item["lot_id"] == "A1022A.2" for item in items)


def test_api_lot_location_query_raw_body_resilience(client):
    import json
    raw_payload = json.dumps({"lot_ids": ["A1022A.2"]})
    resp = client.post(
        "/api/lot-location/query",
        content=raw_payload,
        headers={"content-type": "text/plain"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["stats"]["matched_lot_count"] >= 1
