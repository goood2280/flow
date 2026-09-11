import asyncio
import io
from pathlib import Path

import polars as pl


def _response_bytes(response):
    async def collect():
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(collect())


def test_process_columns_keep_step_id_and_step_desc_separate():
    from routers import splittable

    metas = {
        "knob": {
            "KNOB_A": {
                "groups": [
                    {"rule_order": "R1", "step_desc": "ETCH", "step_ids": ["S10", "S11"]},
                    {"rule_order": "R1", "step_desc": "CLEAN", "step_ids": ["S20"]},
                    {"rule_order": "R2", "step_desc": "SKIP", "step_ids": ["S30"], "operator": "not_null"},
                ]
            }
        },
        "inline": {},
        "vm": {},
    }

    columns = splittable._step_process_columns_for_param("KNOB_A", metas, exclude_not_null=True)

    assert columns == {
        "step_id": "S10\nS11\nS20",
        "step_desc": "ETCH\nCLEAN",
    }


def test_xlsx_process_columns_dedupe_across_knob_rule_orders():
    from routers import splittable

    repeated = {
        "knob": {
            "KNOB_A": {
                "groups": [
                    {"rule_order": "R1", "step_desc": "ETCH", "step_ids": ["S10"]},
                    {"rule_order": "R2", "step_desc": "ETCH", "step_ids": ["S10"]},
                    {"rule_order": "R3", "step_desc": "ETCH", "step_ids": ["S10"]},
                ]
            }
        },
        "inline": {},
        "vm": {},
    }

    columns = splittable._step_process_columns_for_param("KNOB_A", repeated)

    assert columns == {"step_id": "S10", "step_desc": "ETCH"}


def test_step_order_interleaves_mapped_prefixes_and_leaves_mask_unranked(monkeypatch):
    from routers import splittable

    splittable._STEP_ORDER_CTX_CACHE.clear()
    monkeypatch.setattr(splittable, "_s0_sop_catalog", lambda: {})
    monkeypatch.setattr(splittable, "_load_knob_step_matching_rows", lambda *args, **kwargs: [
        {"product": "P1", "step_id": "ST300", "step_desc": "FAB STEP"},
        {"product": "P1", "step_id": "ST100", "step_desc": "KNOB STEP"},
        {"product": "P1", "step_id": "ST200", "step_desc": "INLINE STEP"},
        {"product": "P1", "step_id": "ST500", "step_desc": "MASK STEP"},
        {"product": "P1", "step_id": "ST400", "step_desc": "VM STEP"},
    ])
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "product_col": "product", "step_id_col": "step_id", "step_desc_col": "step_desc",
    } if name == "step_matching" else {})
    monkeypatch.setattr(splittable, "_load_prefixes", lambda: ["KNOB", "FAB", "MASK", "INLINE", "VM"])
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda *args, **kwargs: [
        "KNOB_K", "FAB_F", "MASK_M", "INLINE_I", "VM_V",
    ])
    inferred = {
        "FAB": {"FAB_F": {"groups": [{"step_ids": ["ST300"]}]}},
    }
    monkeypatch.setattr(
        splittable, "_inferred_stage_meta",
        lambda product, prefix: inferred.get(str(prefix).upper(), {}),
    )
    monkeypatch.setattr(splittable, "_build_knob_meta", lambda *args, **kwargs: {
        "K": {"groups": [{"step_ids": ["ST100"]}]},
    })
    monkeypatch.setattr(splittable, "_build_inline_meta", lambda *args, **kwargs: {
        "I": {"groups": [{"step_ids": ["ST200"]}]},
    })
    monkeypatch.setattr(splittable, "_build_vm_meta", lambda *args, **kwargs: {
        "V": {"groups": [{"step_ids": ["ST400"]}]},
    })

    context = splittable._split_step_order_context("P1")
    source = ["VM_V", "MASK_M", "KNOB_K", "FAB_F", "INLINE_I"]
    ordered = sorted(
        source,
        key=lambda column: splittable._step_order_sort_key(
            column, column, context["param_rank"],
        ),
    )

    # f_step이 없을 때는 기존 Vehicle_matching 행 순서를 유지한다.
    assert ordered == ["FAB_F", "KNOB_K", "INLINE_I", "VM_V", "MASK_M"]
    assert context["param_step"]["FAB_F"] == "ST300"
    assert "MASK_M" not in context["param_rank"]
    splittable._STEP_ORDER_CTX_CACHE.clear()


def test_f_step_route_tracks_unmatched_current_step_and_excludes_unmapped_rows(monkeypatch):
    from routers import splittable

    splittable._STEP_ORDER_CTX_CACHE.clear()
    monkeypatch.setattr(splittable, "_s0_sop_catalog", lambda: {
        "p1": {
            "product": "P1",
            "step_order": ["ST100", "ST200", "ST300"],
            "rows": {
                "st100": {"step_id": "ST100", "ppid": "PP1"},
                "st200": {"step_id": "ST200", "ppid": "PP2"},
                "st300": {"step_id": "ST300", "ppid": "PP3"},
            },
        }
    })
    monkeypatch.setattr(splittable, "_load_knob_step_matching_rows", lambda *args, **kwargs: [
        {"product": "P1", "step_id": "ST100", "step_desc": "UPPER"},
        {"product": "P1", "step_id": "ST300", "step_desc": "LOWER"},
        {"product": "P1", "step_id": "ST999", "step_desc": "NOT_IN_ROUTE"},
    ])
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "product_col": "product", "step_id_col": "step_id", "step_desc_col": "step_desc",
    } if name == "step_matching" else {})
    monkeypatch.setattr(splittable, "_load_prefixes", lambda: ["KNOB"])
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda *args, **kwargs: [
        "KNOB_UPPER", "KNOB_LOWER", "KNOB_NOT_IN_ROUTE", "KNOB_NO_STEP",
    ])
    monkeypatch.setattr(splittable, "_inferred_stage_meta", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_build_knob_meta", lambda *args, **kwargs: {
        "UPPER": {"groups": [{"step_ids": ["ST100"]}]},
        "LOWER": {"groups": [{"step_ids": ["ST300"]}]},
        "NOT_IN_ROUTE": {"groups": [{"step_ids": ["ST999"]}]},
        "NO_STEP": {"groups": []},
    })
    monkeypatch.setattr(splittable, "_build_inline_meta", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_build_vm_meta", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_root_latest_step_state", lambda *args, **kwargs: {
        "step_id": "ST200",
        "by_wafer": {"1": "ST200"},
    })

    progress = splittable._split_step_progress(
        "P1", "ROOT1",
        ["KNOB_UPPER", "KNOB_LOWER", "KNOB_NOT_IN_ROUTE", "KNOB_NO_STEP"],
        [1],
    )

    assert progress["tracked"] == ["KNOB_UPPER", "KNOB_LOWER"]
    assert progress["not_reached"] == ["KNOB_LOWER"]
    assert progress["by_wafer"]["1"]["not_reached"] == ["KNOB_LOWER"]
    context = splittable._split_step_order_context("P1")
    assert context["seq_rank"]["ST200"] == 1
    assert "KNOB_NOT_IN_ROUTE" not in context["progress_param_rank"]
    splittable._STEP_ORDER_CTX_CACHE.clear()


def test_fab_missing_greys_only_f_step_tracked_parameters(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(splittable, "_split_step_order_context", lambda product: {
        "progress_param_rank": {"KNOB_MAPPED": 1},
    })

    progress = splittable._split_step_progress(
        "P1", "ROOT1", ["KNOB_MAPPED", "KNOB_NO_STEP"], [1], fab_present=False,
    )

    assert progress["tracked"] == ["KNOB_MAPPED"]
    assert progress["not_reached"] == ["KNOB_MAPPED"]
    assert progress["by_wafer"]["1"]["not_reached"] == ["KNOB_MAPPED"]


def test_split_table_unmatched_steps_do_not_move_grey_boundary():
    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "features"
        / "splittable"
        / "My_SplitTable.jsx"
    ).read_text(encoding="utf-8")

    assert "trackedProgressParams" in source
    assert "if(rowTracksStepProgress[ri])lastFilledRowByCol[ci]=ri" in source
    assert "if(!rowTracksStepProgress[ri])return false" in source


def test_display_settings_save_normalizes_shared_column_widths(tmp_path, monkeypatch):
    from routers import splittable

    settings_file = tmp_path / "display_settings.json"
    monkeypatch.setattr(splittable, "DISPLAY_SETTINGS_CFG", settings_file)

    saved = splittable.save_display_settings(
        splittable.DisplaySettingsReq(column_widths={
            "module": 120,
            "step_id": 220,
            "step_desc": 9999,
            "item": 360,
            "value": 40,
        }),
        _perm={"role": "admin"},
    )

    assert saved["column_widths"] == {
        "module": 120,
        "step_id": 220,
        "step_desc": 640,
        "item": 360,
        "value": 48,
        "split": 80,
        "wafer": 115,
    }
    assert splittable.get_display_settings() == {
        "column_widths": saved["column_widths"]
    }


def test_default_view_merges_context_labels_and_uses_configurable_widths():
    root = Path(__file__).parents[1]
    page = (root / "frontend/src/features/splittable/My_SplitTable.jsx").read_text(encoding="utf-8")
    snapshot = (root / "frontend/src/components/SplitTableSnapshotView.jsx").read_text(encoding="utf-8")

    assert "const leftPrefixColumnCount=1+(showModuleCol?1:0)+(showParamMeta?2:0)" in page
    assert "colSpan={leftPrefixColumnCount} title={lotContextTitle}" in page
    assert "colSpan={leftPrefixColumnCount} style={{boxSizing:\"border-box\",height:purposeHeaderHeight" in page
    assert "columnWidths={columnWidths}" in page
    assert 'sf(API+"/display-settings")' in page
    assert 'sf(API+"/display-settings/save"' in page
    for label in ("module", "step_id", "step_desc", "항목", "값", "Split", "wafer"):
        assert f'\"{label}\"' in page
    assert "normalizeSplitTableColumnWidths" in snapshot
    assert "effectiveColumnWidths.value" in snapshot


def test_merged_view_keeps_live_and_snapshot_context_headers_visually_unified():
    root = Path(__file__).parents[1]
    page = (root / "frontend/src/features/splittable/My_SplitTable.jsx").read_text(encoding="utf-8")
    snapshot = (root / "frontend/src/components/SplitTableSnapshotView.jsx").read_text(encoding="utf-8")

    assert "const mergedContextLeftStyle=mergedViewActive?" in page
    assert page.count('stm-context-left--merged') >= 3
    assert "maxWidth:leftPrefixWidth" in page
    assert "const mergedContextLeftStyle = mergedMode ?" in snapshot
    assert snapshot.count('stm-context-left--merged') >= 3
    assert "maxWidth: prefixTotalWidth" in snapshot


def test_stage_inference_keeps_vehicle_steps_without_numeric_step_desc(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(splittable, "_load_knob_step_matching_rows", lambda *args, **kwargs: [
        {"product": "P1", "step_id": "CC942300", "step_desc": "GATE_ETCH"},
    ])
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "product_col": "product", "step_id_col": "step_id", "step_desc_col": "step_desc",
    } if name == "step_matching" else {})
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda product, prefix="": [
        "FAB_4.0 GATE_OX",
    ] if str(prefix).upper() == "FAB" else [])

    meta = splittable._inferred_stage_meta("P1", "FAB")

    assert meta["FAB_4.0 GATE_OX"]["step_ids"] == ["CC942300"]


def test_vehicle_step_map_keeps_only_selected_product_rows(monkeypatch):
    from routers import splittable

    monkeypatch.setattr(splittable, "_load_knob_step_matching_rows", lambda *args, **kwargs: [
        {"product": "proda", "step_id": "A100", "step_desc": "SHARED"},
        {"product": "proda", "step_id": "A200", "step_desc": "ONLY_A"},
        {"product": "prodb", "step_id": "B100", "step_desc": "SHARED"},
        {"product": "prodb", "step_id": "B200", "step_desc": "ONLY_B"},
    ])
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "product_col": "product", "step_id_col": "step_id", "step_desc_col": "step_desc",
    } if name == "step_matching" else {})
    # 과거 fallback은 선택 제품 S0에 보이는 타 제품 step까지 다시 합쳤다.
    monkeypatch.setattr(splittable, "_s0_sop_catalog", lambda: {
        "proda": {"product": "PRODA", "rows": {"b100": {"step_id": "B100"}}},
    })

    step_map = splittable._product_step_map_by_desc("ML_TABLE_PRODA")

    assert [item["step_id"] for item in step_map["shared"]] == ["A100"]
    assert [item["step_id"] for item in step_map["only_a"]] == ["A200"]
    assert "only_b" not in step_map
    assert "b100" not in step_map


def test_vehicle_file_excludes_legacy_steps_and_unassigned_products(tmp_path, monkeypatch):
    from routers import splittable

    (tmp_path / "Vehicle_matching.csv").write_text(
        'product,step_id,step_desc\nproda,A100,ETCH\nproda,A100,ETCH\n'
        'prodb,B100,ETCH\n"proda,prodb",SHARED,CLEAN\n,UNASSIGNED,ETCH\n',
        encoding="utf-8",
    )
    (tmp_path / "step_matching.csv").write_text(
        'product,step_id,function_step\nproda,LEGACY,OLD\n', encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_sch", lambda kind: {})
    for product, expected in (("ML_TABLE_PRODA", {"A100", "SHARED"}),
                              ("ML_TABLE_PRODB", {"B100", "SHARED"}),
                              ("ML_TABLE_UNKNOWN", set())):
        mapping = splittable._product_step_map_by_desc(product, tmp_path)
        assert {row["step_id"] for rows in mapping.values() for row in rows} == expected


def test_vm_process_info_uses_unique_vehicle_steps_for_selected_product(monkeypatch, tmp_path):
    from routers import splittable

    (tmp_path / "vm_matching.csv").write_text(
        "step_desc,item_id\nSHARED,ITEM_1\nSHARED,ITEM_1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_load_knob_step_matching_rows", lambda *args, **kwargs: [
        {"product": "proda", "step_id": "A100", "step_desc": "SHARED"},
        {"product": "proda", "step_id": "A100", "step_desc": "SHARED"},
        {"product": "prodb", "step_id": "B100", "step_desc": "SHARED"},
    ])
    original_sch = splittable._sch
    monkeypatch.setattr(splittable, "_sch", lambda name: {
        "product_col": "product", "step_id_col": "step_id", "step_desc_col": "step_desc",
    } if name == "step_matching" else original_sch(name))

    meta = splittable._build_vm_meta("ML_TABLE_PRODA")

    assert meta["SHARED_ITEM_1"]["step_ids"] == ["A100"]
    assert meta["SHARED_ITEM_1"]["groups"][0]["step_ids"] == ["A100"]


def test_inline_process_info_keeps_steps_without_vehicle_description(monkeypatch, tmp_path):
    from routers import splittable

    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc\nproda,A100,ETCH\nprodb,B100,ETCH\n", encoding="utf-8",
    )
    (tmp_path / "inline_matching.csv").write_text(
        "product,step_id,item_id,item_desc\nproda,A100,ITEM,CD\nproda,A200,ITEM2,CD\n"
        "proda,A300,ITEM3,NO_DESC\nprodb,B100,ITEM,CD\n", encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_sch", lambda kind: {})
    meta = splittable._build_inline_meta("ML_TABLE_PRODA")
    assert meta["ITEM"]["step_ids"] == ["A100"]
    assert meta["CD"]["step_ids"] == ["A100", "A200"]
    assert meta["CD"]["groups"][1]["step_desc"] == ""
    assert meta["NO_DESC"]["step_ids"] == ["A300"]
    assert splittable._step_process_columns_for_param("INLINE_NO_DESC", {"inline": meta}) == {
        "step_id": "A300", "step_desc": "",
    }
    assert splittable._step_label_lines_for_param("INLINE_NO_DESC", {"inline": meta}) == (
        "inline_matching", ["A300 | ITEM3"],
    )
    assert splittable._step_process_columns_for_param("INLINE_CD", {"inline": meta}) == {
        "step_id": "A100\nA200", "step_desc": "ETCH",
    }


def test_vm_process_info_preserves_underscores_and_vehicle_module(monkeypatch, tmp_path):
    from routers import splittable

    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc,module\nproda,A100,GATE_ETCH,GATE\n"
        "prodb,B100,GATE_ETCH,OTHER\n", encoding="utf-8",
    )
    (tmp_path / "vm_matching.csv").write_text(
        "step_desc,item_id\nGATE_ETCH,ITEM_1\n", encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_sch", lambda kind: {})
    meta = splittable._build_vm_meta("ML_TABLE_PRODA")
    assert meta["GATE_ETCH_ITEM_1"]["module"] == "GATE"
    assert meta["GATE_ETCH_ITEM_1"]["item_id"] == "ITEM_1"
    assert splittable._step_process_columns_for_param("VM_GATE_ETCH_ITEM_1", {"vm": meta}) == {
        "step_id": "A100", "step_desc": "GATE_ETCH",
    }


def test_fab_process_info_uses_fab_name_and_selected_product_vehicle_step(monkeypatch, tmp_path):
    from routers import splittable

    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc,module\n"
        "proda,A100,GATE_ETCH,GATE\n"
        "prodb,B100,GATE_ETCH,OTHER\n",
        encoding="utf-8",
    )
    (tmp_path / "fab.csv").write_text(
        "step_desc,feature_name\nGATE_ETCH,CHAMBER_ID\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_sch", lambda kind: {})

    meta = splittable._build_fab_meta("ML_TABLE_PRODA")

    assert meta["GATE_ETCH_CHAMBER_ID"]["step_ids"] == ["A100"]
    assert meta["FAB_GATE_ETCH_CHAMBER_ID"] is meta["GATE_ETCH_CHAMBER_ID"]
    assert splittable._step_process_columns_for_param(
        "FAB_GATE_ETCH_CHAMBER_ID", {"fab": meta}
    ) == {"step_id": "A100", "step_desc": "GATE_ETCH"}


def test_inline_virtual_columns_use_item_desc_once(monkeypatch):
    from routers import splittable

    by_item_id = {"item_id": "ITEM_1", "item_desc": "CD", "feature_name": "CD"}
    by_item_desc = {"item_id": "ITEM_1", "item_desc": "CD", "feature_name": "CD"}
    monkeypatch.setattr(
        splittable, "_build_inline_meta", lambda *args, **kwargs: {
            "ITEM_1": by_item_id,
            "CD": by_item_desc,
        },
    )

    assert splittable._virtual_columns_for_prefix("PRODA", "INLINE") == ["INLINE_CD"]
    assert splittable._virtual_columns_for_prefix(
        "PRODA", "INLINE", existing_columns=["INLINE_ITEM_1"]
    ) == []


def test_inline_and_vm_process_info_accept_case_variant_headers_and_configured_files(monkeypatch, tmp_path):
    from routers import splittable

    (tmp_path / "inline_custom.csv").write_text(
        " PRODUCT , STEP_ID , ITEM_ID , ITEM_DESC \n"
        " proda , IN200 , CD_ITEM , CD VALUE \n",
        encoding="utf-8",
    )
    (tmp_path / "vm_custom.csv").write_text(
        " STEP_DESC , ITEM_ID \n gate   etch , VM_ITEM \n",
        encoding="utf-8",
    )
    (tmp_path / "Vehicle_matching.csv").write_text(
        " PRODUCT , STEP_ID , STEP_DESC \n ML_TABLE_PRODA , VM300 , GATE ETCH \n",
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    defaults = splittable.rulebook_repo.get_default_schema()
    monkeypatch.setattr(splittable, "_sch", lambda kind: {
        **defaults.get(kind, {}),
        **({"file_name": "inline_custom.csv"} if kind == "inline_matching" else {}),
        **({"file_name": "vm_custom.csv"} if kind == "vm_matching" else {}),
    })

    inline = splittable._build_inline_meta("ML_TABLE_PRODA")
    vm = splittable._build_vm_meta("PRODA")

    assert inline["CD_ITEM"]["step_ids"] == ["IN200"]
    assert vm["gate   etch_VM_ITEM"]["step_ids"] == ["VM300"]
    assert splittable._step_process_columns_for_param(
        "vm_GATE   ETCH_vm_item", {"vm": vm}
    )["step_id"] == "VM300"


def test_matching_csv_cache_refreshes_after_same_sized_rewrite(monkeypatch, tmp_path):
    from routers import splittable

    import os
    stamp = 1_700_000_000_000_000_000
    path = tmp_path / "inline_matching.csv"
    path.write_text("step_id,item_id\nA100,ITEM\n", encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))
    monkeypatch.setattr(splittable, "_CSV_ROWS_CACHE", {})
    first = splittable._load_csv_rows(path)

    path.write_text("step_id,item_id\nB200,ITEM\n", encoding="utf-8")
    os.utime(path, ns=(stamp + 100, stamp + 100))
    second = splittable._load_csv_rows(path)

    assert first[0]["step_id"] == "A100"
    assert second[0]["step_id"] == "B200"


def test_knob_virtual_columns_emit_one_row_per_rulebook_feature(monkeypatch):
    from routers import splittable

    pc = {"feature_name": "5.0 PC", "groups": []}
    ldd = {"feature_name": "6.0 LDD", "groups": []}
    monkeypatch.setattr(splittable, "_build_knob_meta", lambda *args, **kwargs: {
        "5.0 PC": pc,
        "KNOB_5.0 PC": pc,
        "5.0_PC_Split": pc,
        "KNOB_5.0_PC_Split": pc,
        "6.0 LDD": ldd,
        "KNOB_6.0_LDD_Split": ldd,
    })

    virtual = splittable._virtual_columns_for_prefix("P1", "KNOB")

    assert virtual == ["KNOB_5.0 PC", "KNOB_6.0 LDD"]


def test_knob_virtual_columns_do_not_duplicate_a_physical_alias(monkeypatch):
    from routers import splittable

    pc = {"feature_name": "5.0 PC", "groups": []}
    ldd = {"feature_name": "6.0 LDD", "groups": []}
    monkeypatch.setattr(splittable, "_build_knob_meta", lambda *args, **kwargs: {
        "5.0 PC": pc,
        "KNOB_5.0_PC_Split": pc,
        "6.0 LDD": ldd,
        "KNOB_6.0_LDD_Split": ldd,
    })

    virtual = splittable._virtual_columns_for_prefix(
        "P1", "KNOB", existing_columns=["KNOB_5.0_PC_Split"],
    )

    assert virtual == ["KNOB_6.0 LDD"]


def _patch_export_source(monkeypatch):
    from routers import splittable

    frame = pl.DataFrame({
        "root_lot_id": ["L1"],
        "fab_lot_id": ["L1.1"],
        "wafer_id": [1],
        "KNOB_A": ["PP_A"],
    })
    monkeypatch.setattr(splittable, "_product_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(splittable, "_scan_product", lambda *args, **kwargs: frame.lazy())
    monkeypatch.setattr(splittable, "_build_step_process_columns", lambda *args, **kwargs: {
        "KNOB_A": {"step_id": "S10", "step_desc": "ETCH"},
    })
    monkeypatch.setattr(splittable, "_load_plan_data", lambda *args, **kwargs: {"plans": {}})
    monkeypatch.setattr(splittable, "_custom_tag_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_custom_tag_colors_for_root", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_management_row_label_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(splittable, "_split_step_order_context", lambda *args, **kwargs: {"param_rank": {}})
    monkeypatch.setattr(splittable, "_log_split_table_download", lambda *args, **kwargs: None)


def test_csv_process_columns_precede_preserved_parameter(monkeypatch):
    from routers import splittable

    _patch_export_source(monkeypatch)
    response = splittable.download_csv(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", transposed="true", username="u", custom_cols="",
        step_labels="1", exclude_not_null="1",
    )
    text = _response_bytes(response).decode("utf-8-sig")

    assert "step_id,step_desc,Parameter,#1" in text
    assert "S10,ETCH,A,PP_A" in text


def test_csv_download_header_uses_authenticated_user_and_split_context(monkeypatch):
    from routers import splittable

    _patch_export_source(monkeypatch)
    response = splittable.download_csv(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB,FAB",
        custom_name="", transposed="true", username="spoofed", custom_cols="",
        step_labels="0", exclude_not_null="1",
        user={"username": "auth-owner", "role": "user"},
    )

    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "_L1_auth-owner_P1_KNOB_FAB.csv" in disposition


def test_xlsx_process_columns_precede_preserved_parameter(monkeypatch):
    from openpyxl import load_workbook
    from routers import splittable

    _patch_export_source(monkeypatch)
    monkeypatch.setattr(splittable, "_split_step_progress", lambda *args, **kwargs: {})
    response = splittable.download_xlsx(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", username="u", custom_cols="", display_mode="",
        step_labels="1", exclude_not_null="1",
    )

    workbook = load_workbook(io.BytesIO(_response_bytes(response)))
    sheet = workbook.active

    assert sheet.cell(4, 1).value == "purpose"
    assert sheet.cell(5, 1).value == "fab_lot_id"
    assert [sheet.cell(6, col).value for col in range(1, 5)] == ["step_id", "step_desc", "Parameter", "#1"]
    assert [sheet.cell(7, col).value for col in range(1, 5)] == ["S10", "ETCH", "A", "PP_A"]


def test_xlsx_applied_process_columns_match_web_unique_values(monkeypatch):
    from openpyxl import load_workbook
    from routers import splittable

    _patch_export_source(monkeypatch)
    repeated = {
        "knob": {
            "KNOB_A": {
                "groups": [
                    {"rule_order": "R1", "step_desc": "ETCH", "step_ids": ["S10"]},
                    {"rule_order": "R2", "step_desc": "ETCH", "step_ids": ["S10"]},
                ]
            }
        },
        "inline": {},
        "vm": {},
    }
    monkeypatch.setattr(splittable, "_step_label_metas", lambda *args, **kwargs: repeated)
    monkeypatch.setattr(splittable, "_split_step_progress", lambda *args, **kwargs: {})

    response = splittable.download_xlsx(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", username="u", custom_cols="", display_mode="",
        step_labels="1", exclude_not_null="1",
    )
    sheet = load_workbook(io.BytesIO(_response_bytes(response))).active

    assert sheet.cell(7, 1).value == "S10"
    assert sheet.cell(7, 2).value == "ETCH"


def test_split_check_xlsx_uses_the_same_process_prefix(monkeypatch):
    from openpyxl import load_workbook
    from routers import splittable

    _patch_export_source(monkeypatch)
    monkeypatch.setattr(splittable, "_split_step_progress", lambda *args, **kwargs: {})
    response = splittable.download_xlsx(
        product="P1", root_lot_id="L1", wafer_ids="", prefix="KNOB",
        custom_name="", username="u", custom_cols="", display_mode="split_check",
        step_labels="1", exclude_not_null="1",
    )

    sheet = load_workbook(io.BytesIO(_response_bytes(response))).active

    assert [sheet.cell(5, col).value for col in range(1, 7)] == [
        "step_id", "step_desc", "항목", "값", "Split", "#1",
    ]
    assert [sheet.cell(6, col).value for col in range(1, 7)] == [
        "S10", "ETCH", "A", "PP_A", "S0", "S0",
    ]


def test_inform_snapshot_preserves_parameter_beside_process_columns():
    from routers import informs

    embed = {
        "st_view": {
            "headers": ["#1"],
            "root_lot_id": "L1",
            "step_labels": True,
            "rows": [{
                "_param": "KNOB_A",
                "_display": "KNOB_A",
                "_process_columns": {"step_id": "S10", "step_desc": "ETCH"},
                "_cells": {"0": {"actual": "PP_A", "plan": ""}},
            }],
        },
        "step_labels": True,
    }

    result = informs._apply_step_labels_to_embed(embed, "P1")
    html = informs._render_embed_table_html(result)

    assert result["columns"][:3] == ["step_id", "step_desc", "parameter"]
    assert result["rows"][0][:4] == ["S10", "ETCH", "KNOB_A", "PP_A"]
    assert all(value in html for value in ["step_id", "step_desc", "S10", "ETCH"])
    assert ">A</td>" in html


def test_inform_split_check_keeps_process_prefix_columns():
    from routers import informs

    embed = {
        "st_view": {
            "headers": ["#1"],
            "root_lot_id": "L1",
            "step_labels": True,
            "rows": [{
                "_param": "KNOB_A",
                "_display": "KNOB_A",
                "_process_columns": {"step_id": "S10", "step_desc": "ETCH"},
                "_cells": {"0": {"actual": "PP_A", "plan": ""}},
            }],
        },
        "step_labels": True,
    }

    result = informs._convert_splittable_embed_to_split_check(embed)

    assert result["st_view"]["prefix_columns"] == ["step_id", "step_desc", "항목", "값", "Split"]
    assert result["st_view"]["parameter_prefix_index"] == 2
    assert result["rows"][0][:6] == ["S10", "ETCH", "A", "PP_A", "S0", "S0"]


def test_inform_split_check_only_expands_knob_and_keeps_mask_wafer_values():
    from routers import informs

    embed = {
        "st_view": {
            "headers": ["#1", "#2"],
            "root_lot_id": "L1",
            "rows": [
                {
                    "_param": "KNOB_A", "_display": "KNOB_A",
                    "_cells": {"0": {"actual": "P0"}, "1": {"actual": "P1"}},
                },
                {
                    "_param": "MASK_GATE", "_display": "MASK_GATE",
                    "_cells": {"0": {"actual": "AAAA_PC_B"}, "1": {"actual": "PC_B"}},
                },
                {
                    "_param": "INLINE_CD", "_display": "INLINE_CD",
                    "_cells": {"0": {"actual": "10.1"}, "1": {"actual": "10.2"}},
                },
            ],
        },
    }

    result = informs._convert_splittable_embed_to_split_check(embed)

    assert len(result["st_view"]["rows"]) == 4
    assert result["rows"] == [
        ["A", "P0", "S0", "S0", ""],
        ["A", "P1", "S1", "", "S1"],
        ["GATE", "", "", "PC_B", "PC_B"],
        ["CD", "", "", "10.1", "10.2"],
    ]
    html = informs._render_embed_table_html(result)
    assert "AAAA_PC_B" not in html
    assert "PC_B" in html
    inline_row = result["st_view"]["rows"][-1]
    inline_row["_cells"]["0"] = {"actual": "10.1", "plan": "11.2"}
    inline_row["_cells"]["1"] = {"actual": "", "plan": "12.3"}
    html = informs._render_embed_table_html(result)
    assert "≠11.2" in html
    assert "📌 12.3" in html


def test_merged_mode_is_knob_only():
    from routers import informs, splittable

    assert informs._merge_view_allowed_param("KNOB_A")
    assert splittable._merge_view_allowed_param("KNOB_A")
    for param in ("MASK_A", "FAB_A", "INLINE_A", "VM_A", "TAG_A"):
        assert not informs._merge_view_allowed_param(param)
        assert not splittable._merge_view_allowed_param(param)


def test_mask_process_columns_resolves_step_id_and_module_from_vehicle_matching(tmp_path, monkeypatch):
    from routers import splittable

    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc,module\n"
        "proda,PH210300,GATE_PHOTO,PHOTO\n"
        "proda,CC942300,1.0 STI,ETCH\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_sch", lambda kind: {})
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda *args, **kwargs: [
        "MASK_GATE_PHOTO", "MASK_1.0 STI",
    ])

    meta = splittable._build_mask_meta("ML_TABLE_PRODA")

    # MASK_GATE_PHOTO resolves step_id, step_desc, and module
    gate_meta = meta["MASK_GATE_PHOTO"]
    assert gate_meta["step_id"] == "PH210300"
    assert gate_meta["step_desc"] == "GATE_PHOTO"
    assert gate_meta["module"] == "PHOTO"
    assert gate_meta["modules"] == ["PHOTO"]

    # MASK_1.0 STI resolves step_id, step_desc, and module
    sti_meta = meta["MASK_1.0 STI"]
    assert sti_meta["step_id"] == "CC942300"
    assert sti_meta["step_desc"] == "1.0 STI"
    assert sti_meta["module"] == "ETCH"

    metas = splittable._step_label_metas("ML_TABLE_PRODA")
    assert "mask" in metas
    assert metas["mask"]["MASK_GATE_PHOTO"]["step_id"] == "PH210300"

    cols = splittable._step_process_columns_for_param("MASK_GATE_PHOTO", metas)
    assert cols == {"step_id": "PH210300", "step_desc": "GATE_PHOTO"}


def test_mask_process_columns_fallback_when_not_in_vehicle_matching(tmp_path, monkeypatch):
    from routers import splittable

    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc,module\n"
        "proda,PH210300,OTHER_STEP,PHOTO\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_sch", lambda kind: {})
    monkeypatch.setattr(splittable, "_mltable_schema_columns", lambda *args, **kwargs: [
        "MASK_CUSTOM_UNMATCHED",
    ])

    meta = splittable._build_mask_meta("ML_TABLE_PRODA")
    assert meta["MASK_CUSTOM_UNMATCHED"]["step_desc"] == "CUSTOM_UNMATCHED"
    assert meta["MASK_CUSTOM_UNMATCHED"]["step_id"] == ""
    assert meta["MASK_CUSTOM_UNMATCHED"]["module"] == ""

    metas = splittable._step_label_metas("ML_TABLE_PRODA")
    # Even if parameter wasn't in schema, fallback extracts MASK_ tail as step_desc
    cols = splittable._step_process_columns_for_param("MASK_SOME_DYNAMIC_STEP", metas)
    assert cols == {"step_id": "", "step_desc": "SOME_DYNAMIC_STEP"}


def test_mask_meta_does_not_turn_vehicle_steps_into_virtual_rows(tmp_path, monkeypatch):
    from routers import splittable

    (tmp_path / "Vehicle_matching.csv").write_text(
        "product,step_id,step_desc,module\n"
        "proda,A100,GATE_ETCH,GATE\n"
        "proda,A200,CONTACT_ETCH,CONTACT\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_sch", lambda kind: {})
    monkeypatch.setattr(
        splittable, "_mltable_schema_columns", lambda *args, **kwargs: ["MASK_GATE_ETCH"]
    )

    meta = splittable._build_mask_meta("ML_TABLE_PRODA")

    assert "MASK_CONTACT_ETCH" not in meta
    assert splittable._virtual_columns_for_prefix(
        "ML_TABLE_PRODA", "MASK", existing_columns=["MASK_GATE_ETCH"]
    ) == []
