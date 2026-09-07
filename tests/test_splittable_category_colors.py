import pytest
from fastapi import HTTPException


def test_category_colors_are_saved_per_product_case_insensitively(tmp_path, monkeypatch):
    from routers import splittable

    target = tmp_path / "category_colors.json"
    monkeypatch.setattr(splittable, "CATEGORY_COLORS_CFG", target)

    first = splittable.save_category_color(
        splittable.CategoryColorReq(product="ML_TABLE_PRODA", category="KNOB_STD", color="#AaBbCc"),
        _perm=None,
    )
    second = splittable.save_category_color(
        splittable.CategoryColorReq(product="prodb", category="KNOB_STD", color="#112233"),
        _perm=None,
    )

    assert first["colors"] == {"KNOB_STD": "#aabbcc"}
    assert second["colors"] == {"KNOB_STD": "#112233"}
    assert splittable.get_category_colors("PRODA")["colors"] == {"KNOB_STD": "#aabbcc"}
    assert splittable.get_category_colors("ML_TABLE_PRODB")["colors"] == {"KNOB_STD": "#112233"}


def test_category_color_rejects_non_hex_value(tmp_path, monkeypatch):
    from routers import splittable

    monkeypatch.setattr(splittable, "CATEGORY_COLORS_CFG", tmp_path / "category_colors.json")

    with pytest.raises(HTTPException, match="color must be"):
        splittable.save_category_color(
            splittable.CategoryColorReq(product="PRODA", category="STD", color="red"),
            _perm=None,
        )


def test_knob_meta_accepts_recipe_id_as_ppid_value(tmp_path, monkeypatch):
    from routers import splittable

    (tmp_path / "ppid_knob.csv").write_text(
        "feature_name,step_desc,step_id,recipe_id,category\n"
        "KNOB_A,ETCH,AA100,PP_CURRENT,CAT_A\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_product_step_map_by_desc", lambda *args, **kwargs: {
        "etch": [{"step_id": "AA100", "step_desc": "ETCH", "module": "ETCH"}],
        "aa100": [{"step_id": "AA100", "step_desc": "ETCH", "module": "ETCH"}],
    })
    monkeypatch.setattr(splittable, "_inferred_stage_meta", lambda *args, **kwargs: {})

    group = splittable._build_knob_meta("PRODA")["KNOB_A"]["groups"][0]

    assert group["value"] == "PP_CURRENT"
    assert group["category"] == "CAT_A"
