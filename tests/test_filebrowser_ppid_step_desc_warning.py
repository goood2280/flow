import pytest
from fastapi import HTTPException

from core import fab_reference
from routers import filebrowser


def test_missing_ppid_knob_step_desc_reports_unique_values_and_csv_rows():
    missing = filebrowser._missing_ppid_knob_step_desc(
        ["feature_name", "step_desc", "value"],
        [
            ["KNOB_A", "ETCH", "PP_A"],
            ["KNOB_B", "UNKNOWN", "PP_B"],
            ["KNOB_C", "UNKNOWN", "PP_C"],
            ["KNOB_D", "", "PP_D"],
        ],
        [{"step_desc": "ETCH"}, {"step_desc": "CLEAN"}],
    )

    assert missing == [{"value": "UNKNOWN", "rows": [3, 4]}]


def test_ppid_knob_save_requires_confirmation_for_unknown_step_desc(tmp_path, monkeypatch):
    target = tmp_path / "ppid_knob.csv"
    target.write_text("feature_name,step_desc,value\nOLD,ETCH,PP_OLD\n", encoding="utf-8")
    monkeypatch.setattr(
        filebrowser,
        "_require_base_file_access",
        lambda request, file, access_scope, manage: ({"username": "engineer"}, target),
    )
    monkeypatch.setattr(filebrowser, "_resolve_base_file_for_edit", lambda file: target)
    monkeypatch.setattr(
        fab_reference,
        "_read_rows",
        lambda filename: [{"step_id": "AA100000", "step_desc": "ETCH"}],
    )

    req = filebrowser.BaseFileSaveReq(
        file="ppid_knob.csv",
        csv_text="feature_name,step_desc,value\nKNOB_A,UNKNOWN,PP_A\n",
    )
    with pytest.raises(HTTPException) as exc_info:
        filebrowser._save_base_file(req, object())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["error_code"] == "ppid_knob_step_desc_not_found"
    assert exc_info.value.detail["missing_step_desc"] == [
        {"value": "UNKNOWN", "rows": [2]},
    ]
    assert target.read_text(encoding="utf-8") == "feature_name,step_desc,value\nOLD,ETCH,PP_OLD\n"


def test_ppid_knob_step_id_is_converted_before_missing_step_check(tmp_path, monkeypatch):
    target = tmp_path / "ppid_knob.csv"
    target.write_text("feature_name,step_desc,value\nOLD,ETCH,PP_OLD\n", encoding="utf-8")
    monkeypatch.setattr(
        filebrowser,
        "_require_base_file_access",
        lambda request, file, access_scope, manage: ({"username": "engineer"}, target),
    )
    monkeypatch.setattr(filebrowser, "_resolve_base_file_for_edit", lambda file: target)
    monkeypatch.setattr(
        fab_reference,
        "_read_rows",
        lambda filename: [{"step_id": "AA100000", "step_desc": "ETCH"}],
    )
    monkeypatch.setattr(
        filebrowser,
        "_validate_and_sort_csv_rows",
        lambda file, header, rows: (rows, {
            "ok": False,
            "errors": [{"message": "stop after warning check"}],
            "error_count": 1,
        }),
    )

    req = filebrowser.BaseFileSaveReq(
        file="ppid_knob.csv",
        csv_text="feature_name,step_desc,value\nKNOB_A,AA100000,PP_A\n",
    )
    with pytest.raises(HTTPException) as exc_info:
        filebrowser._save_base_file(req, object())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["message"] == "CSV validation failed"


def test_confirmed_unknown_step_desc_continues_to_normal_csv_validation(tmp_path, monkeypatch):
    target = tmp_path / "ppid_knob.csv"
    target.write_text("feature_name,step_desc,value\nOLD,ETCH,PP_OLD\n", encoding="utf-8")
    monkeypatch.setattr(
        filebrowser,
        "_require_base_file_access",
        lambda request, file, access_scope, manage: ({"username": "engineer"}, target),
    )
    monkeypatch.setattr(filebrowser, "_resolve_base_file_for_edit", lambda file: target)
    monkeypatch.setattr(
        fab_reference,
        "_read_rows",
        lambda filename: [{"step_id": "AA100000", "step_desc": "ETCH"}],
    )
    monkeypatch.setattr(
        filebrowser,
        "_validate_and_sort_csv_rows",
        lambda file, header, rows: (rows, {
            "ok": False,
            "errors": [{"message": "normal validation reached"}],
            "error_count": 1,
        }),
    )

    req = filebrowser.BaseFileSaveReq(
        file="ppid_knob.csv",
        csv_text="feature_name,step_desc,value\nKNOB_A,UNKNOWN,PP_A\n",
        confirm_missing_step_desc=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        filebrowser._save_base_file(req, object())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["errors"] == [{"message": "normal validation reached"}]
