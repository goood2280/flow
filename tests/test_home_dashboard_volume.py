from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import HTTPException

from routers import dashboard


def test_dashboard_selected_knob_is_remembered_even_with_previous_dashboard(monkeypatch):
    from core import data_chat_dashboard as home
    remembered = []
    monkeypatch.setattr(home.auth, 'current_user', lambda request: {'role': 'admin', 'username': 'qa'})
    monkeypatch.setattr(home.auth, 'effective_permissions', lambda user: {'tabs': '*'})
    monkeypatch.setattr(dashboard, '_wip_split_catalog', lambda path: ([], ['PRODA']))
    monkeypatch.setattr(dashboard, '_wip_split_column_options', lambda product: ([{'col': 'KNOB_AAAC'}], None))
    monkeypatch.setattr(home.knob_resolution, 'remember', lambda *args: remembered.append(args))
    monkeypatch.setattr(dashboard, 'wip_split_summary', lambda **kw: {'split_col': kw['split_col'], 'bins': [], 'total_wafers': 1})
    monkeypatch.setattr(home, '_reply', lambda message, state, query, **tool: {'state': state, 'query': query, **tool})
    result = home.dispatch('KNOB_AAAC', {
        'dashboard_query': {'product': 'PRODA', 'mode': 'wip'},
        'pending_dashboard': {'kind': 'split_col', 'query': {'product': 'PRODA', 'mode': 'wip', 'alias': 'AAA'},
                              'options': [{'label': 'AAAC', 'value': 'KNOB_AAAC'}]}})
    assert remembered == [('qa', 'PRODA', 'AAA', 'KNOB_AAAC', ['KNOB_AAAC'])]
    assert result['query']['split_col'] == 'KNOB_AAAC'
    assert 'pending_dashboard' not in result['state']


def test_dashboard_product_confirmation_resumes_original_split_request(monkeypatch):
    from core import data_chat_dashboard as home
    resolved = []
    monkeypatch.setattr(home.auth, 'current_user', lambda request: {'role': 'admin', 'username': 'qa'})
    monkeypatch.setattr(home.auth, 'effective_permissions', lambda user: {'tabs': '*'})
    monkeypatch.setattr(dashboard, '_wip_split_catalog', lambda path: ([], ['PRODA']))
    monkeypatch.setattr(dashboard, '_wip_split_column_options', lambda product: ([{'col': 'KNOB_AAAC'}], None))
    def resolve(product, text, cols, user):
        resolved.append(text)
        return {'alias': 'AAA', 'exact': '', 'options': [{'label': 'AAAC', 'value': 'KNOB_AAAC'}]}
    monkeypatch.setattr(home.knob_resolution, 'resolve', resolve)
    monkeypatch.setattr(home, '_reply', lambda message, state, query, **tool: {'state': state, 'query': query, **tool})
    result = home.dispatch('PRODA', {'pending_dashboard': {
        'kind': 'product', 'query': {'mode': 'wip', 'product': '', 'recolor_prompt': 'AAA Split으로 나눠줘'},
        'options': [{'label': 'PRODA', 'value': 'PRODA'}]}})
    assert resolved == ['AAA Split으로 나눠줘']
    assert result['state']['pending_dashboard']['kind'] == 'split_col'


def _request():
    return SimpleNamespace(state=SimpleNamespace(user={"username": "volume-qa", "role": "admin", "tabs": "*"}))


def _install_cache(monkeypatch, frame, selected="ALL", products=None):
    calls = []

    def load(product):
        calls.append(product)
        return frame.clone(), selected, products or ["PRODA", "PRODB"], SimpleNamespace(name="canonical.parquet")

    monkeypatch.setattr(dashboard, "_wip_split_latest_cache", load)
    monkeypatch.setattr(dashboard, "_require_dashboard_section", lambda request, section: request.state.user)
    return calls


def test_product_share_folds_aliases_and_keeps_same_wafer_in_other_product(monkeypatch):
    frame = pl.DataFrame({
        "product": ["PRODA", "ML_TABLE_PRODA", "PRODA", "PRODB"],
        "root_lot_id": [" R1 ", "r1", "R2", "R1"],
        "wafer_id": ["01", "W1", "02", "1"],
        "update_time": ["2026-09-01", "2026-09-02", "2026-09-01", "2026-09-01"],
        "lot_type": ["BUILD", "BUILD", "ENG", "BUILD"],
    })
    calls = _install_cache(monkeypatch, frame)

    result = dashboard.volume_distribution(_request(), product="ALL", group_by="product")

    assert calls == ["ALL"]
    assert result["unit"] == "wafer"
    assert result["total_wafers"] == 3
    assert result["total_lots"] == 3
    assert result["rows"] == [
        {"label": "PRODA", "wafer_count": 2, "lot_count": 2, "share_pct": pytest.approx(200 / 3)},
        {"label": "PRODB", "wafer_count": 1, "lot_count": 1, "share_pct": pytest.approx(100 / 3)},
    ]


def test_lot_type_share_uses_latest_row_and_keeps_missing_in_denominator(monkeypatch):
    frame = pl.DataFrame({
        "product": ["PRODA", "PRODA", "PRODA", "PRODA"],
        "root_lot_id": ["R1", "R1", "R2", "Z-DUMMY"],
        "wafer_id": ["WF01", "1", "02", "3"],
        "update_time": ["2026-09-01", "2026-09-03", "2026-09-02", "2026-09-02"],
        "lot_type": [" build ", " eng ", None, "BUILD"],
    })
    _install_cache(monkeypatch, frame, selected="PRODA", products=["PRODA"])

    result = dashboard.volume_distribution(
        _request(), product="PRODA", group_by="lot_type", exclude_root_prefix="Z"
    )

    assert result["product"] == "PRODA"
    assert result["exclude_root_prefix"] == "Z"
    assert result["total_wafers"] == 2
    assert result["total_lots"] == 2
    assert result["rows"] == [
        {"label": "(미지정)", "wafer_count": 1, "lot_count": 1, "share_pct": 50.0},
        {"label": "ENG", "wafer_count": 1, "lot_count": 1, "share_pct": 50.0},
    ]


@pytest.mark.parametrize("product,group_by", [("ALL", "lot_type"), ("", "lot_type"), ("PRODA", "eqp")])
def test_distribution_rejects_unsupported_or_unscoped_requests(monkeypatch, product, group_by):
    frame = pl.DataFrame({"product": ["PRODA"], "root_lot_id": ["R1"], "wafer_id": ["1"]})
    calls = _install_cache(monkeypatch, frame)

    with pytest.raises(HTTPException) as exc:
        dashboard.volume_distribution(_request(), product=product, group_by=group_by)

    assert exc.value.status_code == 400
    assert calls == []


def test_distribution_fails_closed_when_canonical_keys_or_lot_type_are_missing(monkeypatch):
    missing_key = pl.DataFrame({"product": ["PRODA"], "root_lot_id": ["R1"]})
    _install_cache(monkeypatch, missing_key)
    with pytest.raises(HTTPException) as exc:
        dashboard.volume_distribution(_request(), product="ALL", group_by="product")
    assert exc.value.status_code == 409

    missing_type = pl.DataFrame({"product": ["PRODA"], "root_lot_id": ["R1"], "wafer_id": ["1"]})
    _install_cache(monkeypatch, missing_type, selected="PRODA", products=["PRODA"])
    with pytest.raises(HTTPException) as exc:
        dashboard.volume_distribution(_request(), product="PRODA", group_by="lot_type")
    assert exc.value.status_code == 409
