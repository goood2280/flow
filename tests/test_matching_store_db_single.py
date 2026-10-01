"""Vehicle and Inline matching use the operator's one DB-root CSV each."""
from __future__ import annotations

import csv

from core import matching_store


def _write(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_vehicle_read_ignores_flow_data_copy_and_does_not_seed(tmp_path):
    db, data = tmp_path / "db", tmp_path / "data"
    source = db / "Vehicle_matching.csv"
    _write(source, ["step_desc"], [{"step_desc": "DB_VALUE"}])
    stale = data / "matching" / "Vehicle_matching.csv"
    _write(stale, ["step_desc"], [{"step_desc": "STALE"}])

    rows, path = matching_store.read_csv_rows("Vehicle_matching.csv", db_root=db, data_root=data)

    assert path == source
    assert rows == [{"step_desc": "DB_VALUE"}]
    assert "STALE" in stale.read_text(encoding="utf-8-sig")


def test_inline_save_targets_db_file(tmp_path):
    db, data = tmp_path / "db", tmp_path / "data"
    source = db / "Inline_matching.csv"
    _write(source, ["product", "step_id"], [{"product": "P", "step_id": "S1"}])

    saved = matching_store.save_csv_rows(
        "Inline_matching.csv", [{"product": "P", "step_id": "S2"}],
        ["product", "step_id"], db_root=db, data_root=data,
    )

    assert saved == source
    rows, path = matching_store.read_csv_rows("Inline_matching.csv", db_root=db, data_root=data)
    assert path == source and rows == [{"product": "P", "step_id": "S2"}]
    assert not (data / "matching" / "Inline_matching.csv").exists()


def test_inline_does_not_treat_nested_legacy_copy_as_the_db_single_file(tmp_path):
    db, data = tmp_path / "db", tmp_path / "data"
    nested = db / "matching" / "Inline_matching.csv"
    _write(nested, ["step_id"], [{"step_id": "OLD"}])

    rows, source = matching_store.read_csv_rows("Inline_matching.csv", db_root=db, data_root=data)

    assert rows == []
    assert source == db / "Inline_matching.csv"


def test_lowercase_inline_name_still_resolves_db_single_file(tmp_path):
    db, data = tmp_path / "db", tmp_path / "data"
    source = db / "Inline_matching.csv"
    _write(source, ["step_id"], [{"step_id": "S1"}])

    rows, path = matching_store.read_csv_rows("inline_matching.csv", db_root=db, data_root=data)

    assert path == source
    assert rows == [{"step_id": "S1"}]
    assert not (data / "matching").exists()
