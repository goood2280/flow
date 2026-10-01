from __future__ import annotations

import csv
import json

from core import file_check


def _write(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_report_uses_splittable_db_vehicle_and_reports_source_rows_without_writes(tmp_path):
    db = tmp_path / "db"
    data = tmp_path / "data"
    _write(db / "Vehicle_matching.csv", ["step_desc"], [{"step_desc": "LEGACY_ONLY"}])
    canonical = data / "matching" / "Vehicle_matching.csv"
    _write(canonical, ["product", "step_desc"], [{"product": "P1", "step_desc": "ETCH"}])
    _write(db / "ppid_knob.csv", ["feature_name", "step_desc"], [
        {"feature_name": "A", "step_desc": "ETCH"},
        {"feature_name": "B", "step_desc": "CLEAN"},
        {"feature_name": "C", "step_desc": "CLEAN"},
        {"feature_name": "D", "step_desc": ""},
    ])
    _write(db / "vm_matching.csv", ["step_desc", "item_id"], [
        {"step_desc": "LEGACY_ONLY", "item_id": "I1"},
    ])
    before = {p: p.read_bytes() for p in (canonical, db / "Vehicle_matching.csv", db / "ppid_knob.csv", db / "vm_matching.csv")}

    report = file_check.build_report(db_root=db, data_root=data)

    assert report["read_only"] is True
    assert report["reference"]["location"] == "SplitTable DB rulebook"
    assert report["reference"]["alternatives"][0]["location"] == "flow-data 기존 사본 (미사용)"
    assert report["summary"]["total_missing"] == 2
    ppid, vm = report["checks"]
    assert ppid["missing"] == [
        {"step_desc": "ETCH", "source_rows": [2]},
        {"step_desc": "CLEAN", "source_rows": [3, 4]},
    ]
    assert ppid["source"]["blank_step_desc_rows"] == [5]
    assert ppid["status"] == "warning"
    assert vm["missing"] == []
    assert {p: p.read_bytes() for p in before} == before


def test_report_falls_back_to_legacy_vehicle_without_seed_copy(tmp_path):
    db = tmp_path / "db"
    data = tmp_path / "data"
    _write(db / "step_matching.csv", ["step_desc"], [{"step_desc": "ETCH"}])
    _write(db / "ppid_knob.csv", ["step_desc"], [{"step_desc": "ETCH"}])
    _write(db / "vm_matching.csv", ["step_desc"], [{"step_desc": "ETCH"}])

    report = file_check.build_report(db_root=db, data_root=data)

    assert report["summary"]["status"] == "warning"
    assert report["reference"]["location"] == "SplitTable DB legacy rulebook"
    assert report["reference"]["diagnostics"][0]["code"] == "requested_file_missing_legacy_used"
    assert not (data / "matching" / "Vehicle_matching.csv").exists()


def test_report_warns_when_ppid_knob_is_missing_but_legacy_file_exists(tmp_path):
    db = tmp_path / "db"
    data = tmp_path / "data"
    _write(db / "Vehicle_matching.csv", ["step_desc"], [{"step_desc": "ETCH"}])
    _write(db / "knob_ppid.csv", ["step_desc"], [{"step_desc": "ETCH"}])
    _write(db / "vm_matching.csv", ["step_desc"], [{"step_desc": "ETCH"}])

    report = file_check.build_report(db_root=db, data_root=data)

    assert report["summary"]["status"] == "warning"
    assert report["checks"][0]["source"]["file"] == "knob_ppid.csv"
    assert report["checks"][0]["diagnostics"][0]["code"] == "requested_file_missing_legacy_used"


def test_report_explains_missing_column_and_unavailable_reference(tmp_path):
    db = tmp_path / "db"
    data = tmp_path / "data"
    _write(db / "ppid_knob.csv", ["feature_name"], [{"feature_name": "A"}])

    report = file_check.build_report(db_root=db, data_root=data)

    assert report["summary"]["status"] == "error"
    assert report["checks"][0]["compared"] is False
    codes = {item["code"] for item in report["checks"][0]["source"]["diagnostics"]}
    assert "missing_column" in codes
    assert any(item["code"] == "reference_unavailable" for item in report["checks"][0]["diagnostics"])
    assert report["reference"]["diagnostics"][0]["code"] == "missing_file"


def test_report_matches_active_schema_resolution_but_keeps_ppid_fixed_file(tmp_path):
    db = tmp_path / "db"
    data = tmp_path / "data"
    _write(db / "Vehicle_custom.csv", ["step_desc"], [{"step_desc": "ACTIVE"}])
    _write(db / "ppid_knob.csv", ["step_desc"], [{"step_desc": "ACTIVE"}])
    _write(db / "ppid_custom.csv", ["step_desc"], [{"step_desc": "IGNORED"}])
    _write(db / "vm_custom.csv", ["step_desc"], [{"step_desc": "ACTIVE"}])
    schema_path = data / "splittable" / "rulebook_schema.json"
    schema_path.parent.mkdir(parents=True)
    schema_path.write_text(json.dumps({
        "step_matching": {"file_name": "Vehicle_custom.csv"},
        "knob_ppid": {"file_name": "ppid_custom.csv"},
        "vm_matching": {"file_name": "vm_custom.csv"},
    }), encoding="utf-8")

    report = file_check.build_report(db_root=db, data_root=data)

    assert report["reference"]["file"] == "Vehicle_custom.csv"
    assert report["checks"][0]["source"]["file"] == "ppid_knob.csv"
    assert report["checks"][1]["source"]["file"] == "vm_custom.csv"
    assert report["summary"]["status"] == "ok"


def test_report_reads_step_desc_header_with_outer_spaces(tmp_path):
    db = tmp_path / "db"
    data = tmp_path / "data"
    _write(db / "Vehicle_matching.csv", [" step_desc "], [{" step_desc ": "ETCH"}])
    _write(db / "ppid_knob.csv", ["step_desc"], [{"step_desc": "ETCH"}])
    _write(db / "vm_matching.csv", ["step_desc"], [{"step_desc": "MISSING"}])

    report = file_check.build_report(db_root=db, data_root=data)

    assert report["checks"][0]["missing"] == []
    assert report["checks"][1]["missing"] == [{"step_desc": "MISSING", "source_rows": [2]}]
