from pathlib import Path
from types import SimpleNamespace

import polars as pl

from core import data_chat_por, fab_reference


def _request(*, role="user", tabs="flowi,splittable"):
    return SimpleNamespace(state=SimpleNamespace(user={"username": "tester", "role": role, "tabs": tabs}))


def _roots(tmp_path, monkeypatch):
    db = tmp_path / "db"
    base = tmp_path / "base"
    db.mkdir()
    base.mkdir()
    paths = SimpleNamespace(db_root=db, base_root=base, data_root=tmp_path / "data")
    monkeypatch.setattr(data_chat_por, "PATHS", paths)
    monkeypatch.setattr(fab_reference, "PATHS", paths)
    return db, base


def _csv(root: Path, body: str):
    folder = root / "credential"
    folder.mkdir(exist_ok=True)
    (folder / "f_step.csv").write_text(body, encoding="utf-8")


def _rules(root: Path, body: str):
    (root / "ppid_knob.csv").write_text(
        "feature_name,function_step,rule_order,operator,value,category\n" + body,
        encoding="utf-8",
    )


def test_exact_current_por_and_actual_knob_category(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "product,step_id,recipe_id\nPRODA,CA100000,PP_REAL_A\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_REAL_A,BASE\n")

    result = data_chat_por.dispatch("현재 CA100000 전산 POR이 뭐야", {}, _request())

    assert result["ok"] is True
    assert result["tool"]["ppid"] == "PP_REAL_A"
    assert result["tool"]["categories"] == ["BASE"]
    assert "PP_REAL_A" in result["reply"] and "BASE" in result["reply"]
    assert result["context"]["product"] == "PRODA"
    assert "pending_por" not in result["context"]


def test_csv_is_authoritative_over_confidential_parquet(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "product_id,step_id,recipe_id\nPRODA,CA100000,PP_CSV\n")
    confidential = db / "confidential"
    confidential.mkdir()
    pl.DataFrame({"product": ["PRODA"], "step_id": ["CA100000"], "recipe_id": ["PP_PARQUET"]}).write_parquet(
        confidential / "f_step.parquet"
    )
    _rules(db, "WIDTH,ETCH,R1,eq,PP_CSV,CSV_CATEGORY\nWIDTH,ETCH,R2,eq,PP_PARQUET,PARQUET_CATEGORY\n")

    result = data_chat_por.dispatch("PRODA CA100000 현재 POR 알려줘", {}, _request())

    assert result["tool"]["ppid"] == "PP_CSV"
    assert result["tool"]["categories"] == ["CSV_CATEGORY"]


def test_db_root_product_scope_wins_over_same_product_in_base_root(tmp_path, monkeypatch):
    db, base = _roots(tmp_path, monkeypatch)
    _csv(db, "product,step_id,recipe_id\nPRODA,CA100000,PP_DB\n")
    _csv(base, "product,step_id,recipe_id\nPRODA,CA100000,PP_BASE\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_DB,DB_CATEGORY\nWIDTH,ETCH,R2,eq,PP_BASE,BASE_CATEGORY\n")

    result = data_chat_por.dispatch("PRODA CA100000 POR이 뭐야", {}, _request())

    assert result["tool"]["ppid"] == "PP_DB"
    assert result["tool"]["categories"] == ["DB_CATEGORY"]


def test_confidential_parquet_is_fallback_and_blank_product_rows_are_skipped(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    confidential = db / "confidential"
    confidential.mkdir()
    pl.DataFrame({
        "product": ["", "PRODA"],
        "step_id": ["CA100000", "CA100000"],
        "recipe_id": ["PP_BLANK", "PP_PARQUET"],
    }).write_parquet(confidential / "f_step.parquet")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_PARQUET,FALLBACK\n")

    result = data_chat_por.dispatch("PRODA CA100000 POR이 뭐야", {}, _request())

    assert result["tool"]["ppid"] == "PP_PARQUET"
    assert result["tool"]["categories"] == ["FALLBACK"]


def test_duplicate_step_uses_last_nonempty_recipe_without_moving_scope(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "product,step_id,recipe_id\nPRODA,CA100000,PP_OLD\nPRODA,CA100000,\nPRODA,CA100000,PP_NEW\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_NEW,CURRENT\n")

    result = data_chat_por.dispatch("PRODA CA100000 현재 POR 조회", {}, _request())

    assert result["tool"]["ppid"] == "PP_NEW"


def test_multiple_product_mappings_require_validated_hitl_choice(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "product,step_id,recipe_id\nPRODA,CA100000,PP_A\nPRODB,CA100000,PP_B\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_A,A_CAT\nWIDTH,ETCH,R2,eq,PP_B,B_CAT\n")

    first = data_chat_por.dispatch("현재 CA100000 전산 POR이 뭐야", {}, _request())
    assert first["ok"] is False
    assert len(first["context"]["pending_por"]["options"]) == 2
    assert first["tool"]["needs_input"] is True

    invalid = data_chat_por.dispatch("PRODC", first["context"], _request())
    assert invalid["ok"] is False
    assert "pending_por" in invalid["context"]

    selected = data_chat_por.dispatch("PRODB", invalid["context"], _request())
    assert selected["tool"]["ppid"] == "PP_B"
    assert selected["tool"]["categories"] == ["B_CAT"]
    assert selected["context"]["product"] == "PRODB"


def test_explicit_product_and_context_product_apply_exact_scope(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "product,step_id,recipe_id\nPRODA,CA100000,PP_A\nPRODB,CA100000,PP_B\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_A,A_CAT\nWIDTH,ETCH,R2,eq,PP_B,B_CAT\n")

    explicit = data_chat_por.dispatch("PRODA CA100000 현재 POR 알려줘", {}, _request())
    inherited = data_chat_por.dispatch("CA100000 POR이 뭐야", {"confirmed_product": "PRODB"}, _request())

    assert explicit["tool"]["ppid"] == "PP_A"
    assert inherited["tool"]["ppid"] == "PP_B"


def test_unknown_mapping_stays_pending_until_exact_product_resolves(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "product,step_id,recipe_id\nPRODA,CA100000,PP_A\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_A,A_CAT\n")

    first = data_chat_por.dispatch("ZZ100000 현재 POR이 뭐야", {}, _request())
    assert first["ok"] is False
    assert first["context"]["pending_por"]["step_id"] == "ZZ100000"
    assert first["context"]["pending_por"]["options"] == []

    second = data_chat_por.dispatch("아무거나", first["context"], _request())
    assert second["ok"] is False
    assert "pending_por" in second["context"]


def test_productless_f_step_is_a_global_exact_scope(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "step_id,recipe_id\nCA100000,PP_GLOBAL\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_GLOBAL,GLOBAL\n")

    result = data_chat_por.dispatch("현재 CA100000 POR 알려줘", {}, _request())

    assert result["tool"]["ppid"] == "PP_GLOBAL"
    assert result["context"]["product"] == ""


def test_permission_matches_splittable_visibility_and_admin_override(tmp_path, monkeypatch):
    db, _ = _roots(tmp_path, monkeypatch)
    _csv(db, "product,step_id,recipe_id\nPRODA,CA100000,PP_A\n")
    _rules(db, "WIDTH,ETCH,R1,eq,PP_A,A_CAT\n")

    denied = data_chat_por.dispatch("현재 CA100000 POR이 뭐야", {}, _request(tabs="flowi"))
    admin = data_chat_por.dispatch("현재 CA100000 POR이 뭐야", {}, _request(role="admin", tabs=""))

    assert denied["tool"]["error"] == "permission_denied"
    assert denied["tool"]["blocked"] is True
    assert admin["tool"]["ppid"] == "PP_A"


def test_unrelated_text_is_not_claimed_and_cancel_clears_pending():
    assert data_chat_por.dispatch("CA100000 위치가 어디야", {}, _request()) is None
    context = {"pending_por": {"step_id": "CA100000", "options": []}}
    result = data_chat_por.dispatch("취소", context, _request())
    assert "pending_por" not in result["context"]
