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


def test_pems_export_keeps_non_knob_rows_as_wafer_values_and_formats_mask():
    from routers import splittable

    rows, param_keys = splittable._build_pems_export_rows(
        ["KNOB_CONTACT", "INLINE_CD", "MASK_GATE"],
        {
            "KNOB_CONTACT": ({0: "PP_A"}, {}),
            "INLINE_CD": ({0: "10.1", 1: "10.2"}, {}),
            "MASK_GATE": ({0: "AAAA_PC_B", 1: "PC_B"}, {}),
        },
    )

    assert [row[:3] for row in rows] == [
        ["CONTACT", "PP_A", "S0"],
        ["CD", "", ""],
        ["GATE", "", ""],
    ]
    assert rows[1][3:5] == ["10.1", "10.2"]
    assert rows[2][3:5] == ["PC_B", "PC_B"]
    assert param_keys == ["KNOB_CONTACT", "INLINE_CD", "MASK_GATE"]


def test_split_check_export_keeps_mixed_non_knob_rows_unexpanded():
    from routers import splittable

    value_maps = {
        "KNOB_A": ({0: "P0", 1: "P1"}, {}),
        "VM_TEMP": ({0: "1.2", 1: "1.3"}, {}),
        "FAB_STEP": ({0: "F1", 1: "F1"}, {}),
    }
    rows = splittable._build_split_check_export_rows(
        list(value_maps), 2, value_maps,
    )
    keys = splittable._split_check_export_param_keys(list(value_maps), 2, value_maps)

    assert [row[:3] for row in rows] == [
        ["A", "P0", "S0"],
        ["A", "P1", "S1"],
        ["TEMP", "", ""],
        ["STEP", "", ""],
    ]
    assert rows[2][3:] == ["1.2", "1.3"]
    assert rows[3][3:] == ["F1", "F1"]
    assert keys == ["KNOB_A", "KNOB_A", "VM_TEMP", "FAB_STEP"]
