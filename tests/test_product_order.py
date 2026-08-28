from core import product_order


def test_clean_product_order_normalizes_prefix_and_duplicates():
    assert product_order.clean_product_order([
        "ML_TABLE_PRODB", "proda", "PRODB", "", None, "ML_TABLE_PRODC",
    ]) == ["PRODB", "proda", "PRODC"]


def test_order_products_appends_unlisted_names_alphabetically():
    rows = [
        {"name": "ML_TABLE_PRODC"},
        {"name": "ML_TABLE_PRODA"},
        {"name": "ML_TABLE_PRODB"},
    ]
    ordered = product_order.order_products(
        rows,
        name=lambda row: row["name"],
        product_order=["PRODB"],
    )
    assert [row["name"] for row in ordered] == [
        "ML_TABLE_PRODB", "ML_TABLE_PRODA", "ML_TABLE_PRODC",
    ]


def test_product_order_round_trip(tmp_path, monkeypatch):
    settings_file = tmp_path / "product_order.json"
    monkeypatch.setattr(product_order, "_settings_file", lambda: settings_file)

    assert product_order.load_product_order() == []
    assert product_order.save_product_order(["PRODC", "ML_TABLE_PRODA", "prodc"]) == ["PRODC", "PRODA"]
    assert product_order.load_product_order() == ["PRODC", "PRODA"]
