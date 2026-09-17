from core import data_chat, data_product_catalog


def test_catalog_uses_physical_tables_and_product_directories(tmp_path):
    (tmp_path / "ML_TABLE_REAL_ALPHA.parquet").write_bytes(b"")
    fab = tmp_path / "1.RAWDATA_DB_FAB"
    (fab / "RealBeta" / "date=20260916").mkdir(parents=True)
    inline = tmp_path / "1.RAWDATA_DB_INLINE"
    (inline / "measurements" / "product=RealGamma").mkdir(parents=True)
    (tmp_path / "mapfile" / "VH_FAKE").mkdir(parents=True)

    rows = data_product_catalog.discover_product_catalog(tmp_path)
    by_product = {row["product"]: row for row in rows}

    assert set(by_product) == {"REAL_ALPHA", "RealBeta", "RealGamma"}
    assert by_product["REAL_ALPHA"]["split_table"] == "ML_TABLE_REAL_ALPHA"
    assert by_product["REAL_ALPHA"]["tables"] == ["ML_TABLE_REAL_ALPHA"]
    assert by_product["RealBeta"]["source_roots"] == ["1.RAWDATA_DB_FAB"]
    assert by_product["RealGamma"]["tables"] == ["measurements"]


def test_catalog_does_not_invent_product_from_vehicle_or_empty_root(tmp_path):
    (tmp_path / "teg_location" / "VH_PRODA").mkdir(parents=True)

    assert data_product_catalog.product_names(tmp_path) == []
    assert data_product_catalog.product_record("PRODA", tmp_path) is None


def test_chat_resolves_vehicle_alias_to_real_db_product_without_inventing_table(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(data_product_catalog, "discover_product_catalog", lambda: [{
        "product": "REAL_ALPHA",
        "tables": ["ML_TABLE_REAL_ALPHA"],
        "source_roots": ["DB"],
        "split_table": "ML_TABLE_REAL_ALPHA",
    }])
    monkeypatch.setattr(splittable, "list_products", lambda: {
        "products": [{"name": "ML_TABLE_REAL_ALPHA"}],
    })

    assert data_chat.available_product_names() == ["REAL_ALPHA"]
    assert data_chat.product_candidates("VH_REAL_ALPHA TEG 위치", ["REAL_ALPHA"]) == ["REAL_ALPHA"]
    assert data_chat.split_table_product("REAL_ALPHA") == "ML_TABLE_REAL_ALPHA"


def test_execution_trace_without_product_has_no_demo_product_or_table(monkeypatch):
    monkeypatch.setattr(data_chat, "available_product_catalog", list)

    trace = data_chat.extract_execution_trace("스플릿테이블 보여줘", {
        "context": {"last_feature": "splittable"},
        "tool": {"feature": "splittable"},
    })

    serialized = str(trace)
    assert "PRODA" not in serialized
    assert "PRODB" not in serialized
    assert "ML_TABLE_PRODA" not in serialized
    assert "실제 DB 테이블 확인 필요" in serialized
