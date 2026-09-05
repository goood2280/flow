from routers import informs


def test_module_colors_round_trip_and_invalid_values_are_dropped(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(informs, "CONFIG_FILE", config_file)
    monkeypatch.setattr(informs, "_merged_catalog_products", lambda *args, **kwargs: [])

    response = informs.save_config_endpoint(
        informs.ConfigReq(
            modules=["GATE", "NEW_MODULE"],
            module_colors={
                "GATE": "#AABBCC",
                "NEW_MODULE": "#123456",
                "BAD": "red",
                "": "#ffffff",
            },
        ),
        _admin={"username": "tester"},
    )

    assert response["config"]["module_colors"] == {
        "GATE": "#aabbcc",
        "NEW_MODULE": "#123456",
    }
    assert informs.get_config()["module_colors"] == response["config"]["module_colors"]


def test_missing_module_colors_defaults_to_empty_mapping(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text('{"modules":["GATE"]}', encoding="utf-8")
    monkeypatch.setattr(informs, "CONFIG_FILE", config_file)

    assert informs._load_config()["module_colors"] == {}
