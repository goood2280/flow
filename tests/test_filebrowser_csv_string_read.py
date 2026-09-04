import copy

import polars as pl

from core import teg_map
from routers import filebrowser


def test_editable_csv_scan_keeps_decimal_after_integer_sample(tmp_path):
    path = tmp_path / "Teg_location.csv"
    path.write_text(
        "vehicle,teg,teg_h\n"
        + "".join(f"V,T{index},30\n" for index in range(5001))
        + "V,T_DECIMAL,30.12\n",
        encoding="utf-8",
    )

    frame = filebrowser._scan_editable_csv_as_strings(path).collect()

    assert frame.schema["teg_h"] == pl.String
    assert frame[-1, "teg_h"] == "30.12"


def test_base_file_view_returns_reference_csv_cells_as_strings(tmp_path, monkeypatch):
    path = tmp_path / "Teg_location.csv"
    path.write_text("vehicle,teg,teg_h\nV,T_INT,30\nV,T_DECIMAL,30.12\n", encoding="utf-8")
    settings = copy.deepcopy(filebrowser.DEFAULT_FILEBROWSER_SETTINGS)
    monkeypatch.setattr(filebrowser, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(filebrowser, "_load_filebrowser_settings", lambda: settings)
    monkeypatch.setattr(filebrowser, "_require_base_file_access", lambda *args, **kwargs: ({"role": "admin"}, None))
    monkeypatch.setattr(filebrowser._fbcache, "is_enabled", lambda settings: False)

    response = filebrowser.base_file_view(
        file=path.name,
        sql="",
        rows=100,
        cols=10,
        select_cols="",
        sort_column="",
        sort_direction="asc",
        sort_nulls="last",
        agg_func="",
        agg_column="",
        agg_group_by="",
        engine="auto",
        meta_only=False,
        page=0,
        page_size=100,
        access_scope="",
        reuse_history_id="",
        request=object(),
    )

    assert response["dtypes"]["teg_h"] == "String"
    assert [row["teg_h"] for row in response["data"]] == ["30", "30.12"]


def test_teg_reference_reader_preserves_csv_cells_before_numeric_conversion(tmp_path):
    path = tmp_path / "Teg_location.csv"
    path.write_text("vehicle,teg,ebeam_x,ebeam_y,teg_h\nV,T1,10,20,30.12\n", encoding="utf-8")

    frame = teg_map._read_table(path)

    assert frame.loc[0, "teg_h"] == "30.12"
    assert frame.loc[0, "ebeam_x"] == "10"
