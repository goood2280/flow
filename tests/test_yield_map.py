from pathlib import Path
from types import SimpleNamespace

from core import yield_map


def _sample_source(tmp_path: Path) -> tuple[Path, str]:
    db_root = tmp_path / "DB"
    source_id = "1.RAWDATA_DB_EDS/WAFER_BIN"
    product_dir = db_root / source_id / "product=PROD_A"
    product_dir.mkdir(parents=True)
    (product_dir / "part.csv").write_text(
        "chip_x_pos,chip_y_pos,BIN,MSR,lot_id,wafer_id\n"
        "-3,10,1,0.9,L1,1\n"
        "-1,10,2,0.2,L1,1\n"
        "4,20,9,0.1,L2,2\n",
        encoding="utf-8",
    )
    return db_root, source_id


def test_yield_map_discovers_bin_table_and_filters_product_map(tmp_path, monkeypatch):
    db_root, source_id = _sample_source(tmp_path)
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    monkeypatch.setattr(yield_map, "CONFIG_PATH", tmp_path / "yield_map.json")

    sources = yield_map.discover_sources()
    assert [item["id"] for item in sources] == [source_id]
    assert sources[0]["products"] == ["PROD_A"]

    preview = yield_map.preview(source_id, "PROD_A")
    assert preview["detected_fields"]["x"] == "chip_x_pos"
    assert preview["detected_fields"]["y"] == "chip_y_pos"
    assert preview["detected_fields"]["bin"] == "BIN"

    yield_map.save_product_config("PROD_A", {
        "source": source_id,
        "fields": preview["detected_fields"],
        "bin_map": [
            {"bin": "2", "bin_color": "#ff0000"},
            {"bin": "1", "bin_color": "#00ff00"},
            {"bin": "bad", "bin_color": "invalid"},
        ],
    })
    result = yield_map.map_data("PROD_A", lot_id="L1", wafer_id="1")
    assert len(result["rows"]) == 2
    assert result["net_die"] == 2
    assert result["bin_colors"] == {"2": "#FF0000", "1": "#00FF00"}
    assert result["bins"] == [{"bin": "1", "count": 1}, {"bin": "2", "count": 1}]
    saved = yield_map.product_config("PROD_A")
    assert saved["bin_map"] == [
        {"bin": "2", "bin_color": "#FF0000"},
        {"bin": "1", "bin_color": "#00FF00"},
    ]


def test_yield_map_rejects_source_outside_db_root(tmp_path, monkeypatch):
    db_root = tmp_path / "DB"
    db_root.mkdir()
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    try:
        yield_map.resolve_source("../outside_BIN.csv")
    except ValueError as exc:
        assert "벗어납니다" in str(exc)
    else:
        raise AssertionError("path traversal source must be rejected")


def test_yield_map_does_not_fall_back_to_another_product_partition(tmp_path, monkeypatch):
    db_root, source_id = _sample_source(tmp_path)
    monkeypatch.setattr(yield_map, "PATHS", SimpleNamespace(db_root=db_root, data_root=tmp_path))
    assert yield_map.source_files(source_id, "PROD_B") == []
