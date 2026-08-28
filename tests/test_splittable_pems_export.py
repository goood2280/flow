def test_pems_export_uses_fixed_25_wafers_and_assigns_blanks_to_s0():
    from routers import splittable

    rows, param_keys = splittable._build_pems_export_rows(
        ["KNOB_CONTACT"],
        {
            "KNOB_CONTACT": (
                {0: "PPID_A", 2: "PPID_B"},
                {},
            ),
        },
        {"KNOB_CONTACT": "KNOB_10.0 CONTACT"},
    )

    assert param_keys == ["KNOB_CONTACT", "KNOB_CONTACT"]
    assert len(rows) == 2
    assert all(len(row) == 28 for row in rows)  # 항목/값/Split + wafer 1..25
    assert rows[0][:3] == ["10.0 CONTACT", "PPID_A", "S0"]
    assert rows[0][3] == "S0"   # wafer 1: first actual group
    assert rows[0][4] == "S0"   # wafer 2: missing/blank is forced into S0
    assert rows[0][5] == ""     # wafer 3 belongs to S1
    assert rows[0][27] == "S0"  # wafer 25 is still present and in S0
    assert rows[1][:3] == ["10.0 CONTACT", "PPID_B", "S1"]
    assert rows[1][5] == "S1"


def test_pems_export_emits_an_s0_row_when_parameter_has_no_values():
    from routers import splittable

    rows, param_keys = splittable._build_pems_export_rows(
        ["KNOB_EMPTY"],
        {"KNOB_EMPTY": ({}, {})},
    )

    assert param_keys == ["KNOB_EMPTY"]
    assert rows[0][:3] == ["EMPTY", "", "S0"]
    assert rows[0][3:] == ["S0"] * 25
