# SplitTable Candidate Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SplitTable root lot and common KNOB-oriented candidate lookups use prebuilt cache metadata instead of doing directory scans or raw source scans during search.

**Architecture:** Extend the existing `backend/core/ml_table_lookup.py` lookup cache build to write a small `_candidate_index.json` sidecar beside `_meta.json`. Candidate APIs in `backend/routers/splittable.py` read that sidecar first for `root_lot_id`, exposing cache metadata so the frontend can show the dropdown without triggering extra calculation. The index stores root lots plus column groups, with `KNOB_` columns prepared as the common path.

**Tech Stack:** FastAPI router functions, Polars cache builder, pytest.

---

### Task 1: Candidate Index Tests

**Files:**
- Modify: `tests/test_splittable_lot_candidates.py`

- [ ] **Step 1: Write the failing build/index test**

```python
def test_lookup_cache_build_writes_candidate_index(tmp_path, monkeypatch):
    _reset_product_ram_cache(monkeypatch)
    monkeypatch.setattr(ml_table_lookup, "_cache_root", lambda: tmp_path / "lookup_cache")
    fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1000", "A1001", "A1000"],
        "wafer_id": ["1", "1", "2"],
        "KNOB_GATE": ["R1", "R2", "R1"],
        "MASK_ID": ["M1", "M2", "M1"],
    }).write_parquet(fp)

    result = ml_table_lookup.build_lookup_cache(fp, force=True)
    index = ml_table_lookup.read_candidate_index(fp)

    assert result["meta"]["candidate_index"]["has_index"] is True
    assert index["root_lot_ids"] == ["A1000", "A1001"]
    assert index["columns_by_prefix"]["KNOB"] == ["KNOB_GATE"]
```

- [ ] **Step 2: Write the failing API no-directory-scan test**

```python
def test_root_lot_candidates_use_candidate_index_without_partition_scan(tmp_path, monkeypatch):
    _reset_product_ram_cache(monkeypatch)
    monkeypatch.setattr(ml_table_lookup, "_cache_root", lambda: tmp_path / "lookup_cache")
    fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1000", "AX2000", "B1000"],
        "wafer_id": ["1", "1", "1"],
        "KNOB_GATE": ["R1", "R2", "R3"],
    }).write_parquet(fp)
    ml_table_lookup.build_lookup_cache(fp, force=True)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_fab_history_root_candidates", lambda *args, **kwargs: {"candidates": [], "source": ""})
    monkeypatch.setattr(ml_table_lookup, "_partition_files", lambda *args, **kwargs: [])

    result = splittable.get_lot_candidates(
        product="ML_TABLE_PRODA",
        col="root_lot_id",
        prefix="A",
        limit=10,
        source="auto",
        root_lot_id="",
    )

    assert result["match_mode"] == "lookup_cache_roots"
    assert result["lookup_cache"]["candidate_index"] is True
    assert result["candidates"] == ["A1000", "AX2000"]
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```bash
python -m pytest tests/test_splittable_lot_candidates.py::test_lookup_cache_build_writes_candidate_index tests/test_splittable_lot_candidates.py::test_root_lot_candidates_use_candidate_index_without_partition_scan -q
```

Expected: FAIL because `read_candidate_index` and `candidate_index` metadata do not exist yet.

### Task 2: Candidate Index Implementation

**Files:**
- Modify: `backend/core/ml_table_lookup.py`
- Modify: `backend/routers/splittable.py`

- [ ] **Step 1: Add sidecar helpers in `ml_table_lookup.py`**

Implement `CANDIDATE_INDEX_FILE`, `candidate_index_path_for(fp)`, `read_candidate_index(fp)`, `_write_candidate_index(fp, index)`, `_build_candidate_index_from_cache(fp, cache_dir, final_cols)`, and `_root_lot_candidates_from_index(index, prefix, limit)`.

- [ ] **Step 2: Write index at cache build time**

After `_lookup_cache_written_stats(cdir)`, build the sidecar from written partitions and schema columns. Store `root_lot_ids`, `columns_by_prefix`, `default_prefix="KNOB"`, source signature, and version. Put a small `candidate_index` summary into `_meta.json`.

- [ ] **Step 3: Read index first for root lot candidates**

Update `root_lot_candidates_from_lookup_cache()` so a fresh cache reads `_candidate_index.json` before iterating partition directories.

- [ ] **Step 4: Expose public metadata**

Update router lookup cache public metadata so `/lot-candidates` returns `lookup_cache.candidate_index=true` when the index served the list.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
python -m pytest tests/test_splittable_lot_candidates.py::test_lookup_cache_build_writes_candidate_index tests/test_splittable_lot_candidates.py::test_root_lot_candidates_use_candidate_index_without_partition_scan -q
```

Expected: PASS.

### Task 3: Regression Verification

**Files:**
- Modify if behavior docs change: `docs/features/splittable.md`
- Modify if bundle regeneration is required by project workflow: `setup.py`

- [ ] **Step 1: Run focused SplitTable candidate tests**

Run:

```bash
python -m pytest tests/test_splittable_lot_candidates.py -q
```

Expected: PASS.

- [ ] **Step 2: Run diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 3: Commit only scoped files**

Run:

```bash
git add backend/core/ml_table_lookup.py backend/routers/splittable.py tests/test_splittable_lot_candidates.py docs/features/splittable.md docs/superpowers/plans/2026-06-15-splittable-candidate-index.md
git commit -m "Speed up SplitTable candidate lookup"
```

Expected: commit succeeds while unrelated dirty files remain unstaged.
