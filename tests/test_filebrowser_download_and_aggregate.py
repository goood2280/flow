import polars as pl

from routers import filebrowser


def test_latest_aggregate_groups_root_lot_and_wafer():
    df = pl.DataFrame(
        {
            "root_lot_id": ["A", "A", "A", "B"],
            "wafer_id": [1, 1, 2, 1],
            "tkout_time": [
                "2026-09-01 08:00:00",
                "2026-09-01 10:00:00",
                "2026-09-01 09:00:00",
                "2026-09-01 07:00:00",
            ],
        }
    )

    spec = filebrowser._normalize_ai_sql_aggregate(
        {
            "function": "latest",
            "column": "tkout_time",
            "group_by": ["root_lot_id", "wafer_id"],
        },
        df.columns,
        [],
    )
    out = filebrowser._apply_aggregate_df(df, spec).sort(["root_lot_id", "wafer_id"])
    active_sort, _ = filebrowser._resolve_view_sort_spec(
        {"column": "tkout_time", "direction": "desc", "nulls": "last"},
        df.columns + [spec["alias"]],
    )
    active_sort = filebrowser._aggregate_sort_alias(active_sort, spec, out.columns)

    assert spec["alias"] == "latest_tkout_time"
    assert active_sort["column"] == "latest_tkout_time"
    assert out.to_dicts() == [
        {"root_lot_id": "A", "wafer_id": 1, "latest_tkout_time": "2026-09-01 10:00:00"},
        {"root_lot_id": "A", "wafer_id": 2, "latest_tkout_time": "2026-09-01 09:00:00"},
        {"root_lot_id": "B", "wafer_id": 1, "latest_tkout_time": "2026-09-01 07:00:00"},
    ]


def test_lazy_csv_download_truncates_after_sort_instead_of_rejecting():
    lf = pl.DataFrame(
        {
            "wafer_id": [1, 2, 3, 4],
            "value": [10, 40, 30, 20],
        }
    ).lazy()

    df, csv_bytes = filebrowser._download_lazy_csv(
        lf,
        "",
        "",
        2,
        1_000_000,
        source_size=0,
        settings={},
        sort_spec={"column": "value", "direction": "desc", "nulls": "last"},
    )

    assert df.height == 2
    assert df["value"].to_list() == [40, 30]
    assert csv_bytes.decode("utf-8").splitlines() == ["wafer_id,value", "2,40", "3,30"]


def test_admin_csv_row_setting_can_exceed_legacy_500k_limit():
    settings = filebrowser._normalize_filebrowser_settings({"csv_download_max_rows": 2_000_000})
    clamped = filebrowser._normalize_filebrowser_settings(
        {"csv_download_max_rows": filebrowser.MAX_CSV_DOWNLOAD_MAX_ROWS + 1}
    )

    assert settings["csv_download_max_rows"] == 2_000_000
    assert clamped["csv_download_max_rows"] == filebrowser.MAX_CSV_DOWNLOAD_MAX_ROWS
