from core.ppid_knob_order import normalize_ppid_knob_rule_order


def test_exact_same_ppid_group_moves_before_tkout_threshold_group():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "gte~~tkout_time", "value": "PP_1", "category": "OLD", "step_desc": "ETCH"},
        # A second condition in R1 must move with the whole AND group.
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "eq", "value": "AUX", "category": "", "step_desc": "CLEAN"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "eq", "value": "PP_2", "category": "OTHER", "step_desc": "ETCH"},
        {"feature_name": "KNOB_A", "rule_order": "R3", "operator": "eq", "value": "PP_1", "category": "NEW", "step_desc": "ETCH"},
        # A second condition in R3 must also remain in that AND group.
        {"feature_name": "KNOB_A", "rule_order": "R3", "operator": "contains", "value": "SIDE", "category": "", "step_desc": "CLEAN"},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert [row["rule_order"] for row in result["rows"]] == ["R3", "R3", "R2", "R1", "R1"]
    assert {(change["from_rule_order"], change["to_rule_order"]) for change in result["changes"]} == {
        ("R1", "R3"), ("R3", "R1"),
    }
    assert result["warnings"] == []
    # The caller's preview rows are not mutated in place.
    assert [row["rule_order"] for row in rows] == ["R1", "R1", "R2", "R3", "R3"]


def test_value_suffix_marker_and_comparison_alias_are_case_insensitive():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "greater_than_or_equal", "value": "PP_1~~TKOUT_TIME", "category": "OLD"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "equals", "value": "PP_1", "category": "NEW"},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert [row["rule_order"] for row in result["rows"]] == ["R2", "R1"]
    assert len(result["changes"]) == 2


def test_unrelated_or_already_ordered_rules_remain_unchanged():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "eq", "value": "PP_1", "category": "NEW"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": ">=~~tkout_time", "value": "PP_1", "category": "OLD"},
        {"feature_name": "KNOB_A", "rule_order": "R3", "operator": "gt~~tkout_time", "value": "PP_2", "category": "OTHER"},
        {"feature_name": "KNOB_B", "rule_order": "RO", "operator": "gte~~tkout_time", "value": "PP_1", "category": "OLD"},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert result["rows"] == rows
    assert result["changes"] == []
    assert result["warnings"] == []


def test_same_and_group_is_reported_without_splitting_rows():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "eq", "value": "PP_1", "category": "NEW"},
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "gte~~tkout_time", "value": "PP_1", "category": "OLD"},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert result["rows"] == rows
    assert result["changes"] == []
    assert result["warnings"][0]["rule_order"] == "R1"


def test_different_category_is_detected_pairwise_when_categories_overlap():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "gte~~tkout_time", "value": "PP_1", "category": "A"},
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "gte~~tkout_time", "value": "PP_1", "category": "B"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "eq", "value": "PP_1", "category": "A"},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert [row["rule_order"] for row in result["rows"]] == ["R2", "R2", "R1"]


def test_multiple_ppids_use_one_collision_free_group_permutation():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "gte~~tkout_time", "value": "PP_1", "category": "OLD_1"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "eq", "value": "PP_1", "category": "NEW_1"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "gte~~tkout_time", "value": "PP_2", "category": "OLD_2"},
        {"feature_name": "KNOB_A", "rule_order": "R3", "operator": "eq", "value": "PP_2", "category": "NEW_2"},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert [row["rule_order"] for row in result["rows"]] == ["R3", "R2", "R2", "R1"]
    assert sorted(row["rule_order"] for row in result["rows"]) == ["R1", "R2", "R2", "R3"]


def test_conflicting_priority_cycle_is_reported_and_left_unchanged():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "gte~~tkout_time", "value": "PP_1", "category": "OLD_1"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "eq", "value": "PP_1", "category": "NEW_1"},
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "eq", "value": "PP_2", "category": "NEW_2"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "gte~~tkout_time", "value": "PP_2", "category": "OLD_2"},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert result["rows"] == rows
    assert result["changes"] == []
    assert result["warnings"][0]["reason"].endswith("form a cycle")


def test_numeric_and_r_prefixed_labels_share_groups_and_keep_row_style():
    rows = [
        {"feature_name": "KNOB_A", "rule_order": "1", "operator": "gte~~tkout_time", "value": "PP_1", "category": "OLD"},
        # R1 is the same AND group as numeric 1 and must move together.
        {"feature_name": "KNOB_A", "rule_order": "R1", "operator": "eq", "value": "AUX", "category": ""},
        {"feature_name": "KNOB_A", "rule_order": "2", "operator": "eq", "value": "PP_1", "category": "NEW"},
        {"feature_name": "KNOB_A", "rule_order": "R2", "operator": "contains", "value": "SIDE", "category": ""},
    ]

    result = normalize_ppid_knob_rule_order(rows)

    assert [row["rule_order"] for row in result["rows"]] == ["2", "R2", "1", "R1"]
    assert {(change["from_rule_order"], change["to_rule_order"]) for change in result["changes"]} == {
        ("R1", "R2"), ("R2", "R1"),
    }
    assert result["warnings"] == []
