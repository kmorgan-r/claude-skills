from conftest import _load

snapshot = _load("snapshot")

NEW_COLS = ["item_type", "r_strategy", "enabler_topic",
            "target_end_year", "target_has_kpi", "target_status"]


def _base(**over):
    row = {"entity": "Royal Philips", "domain": "circular",
           "indicator": "circular_revenue_pct", "period": "2022",
           "status": "found", "retrieved_at": "2026-07-02",
           "value": "18", "source_url": "http://x", "quote": "18% circular"}
    row.update(over)
    return row


def test_header_has_19_columns_in_order():
    assert snapshot.COLS[:13] == [
        "entity", "lei", "domain", "indicator", "value", "unit", "period",
        "status", "source", "source_url", "page", "quote", "retrieved_at"]
    assert snapshot.COLS[13:] == NEW_COLS
    assert len(snapshot.COLS) == 19


def test_backward_compatible_13col_row_passes():
    # no new fields at all -> still valid
    assert snapshot.validate(_base()) == []


def test_valid_classification_row_passes():
    row = _base(status="target", value="25", item_type="target",
                r_strategy="R1|R2", target_end_year="2025",
                target_has_kpi="yes", target_status="on_track")
    assert snapshot.validate(row) == []


def test_invalid_item_type_rejected():
    errs = snapshot.validate(_base(item_type="metric"))
    assert any("item_type" in e for e in errs)


def test_invalid_r_strategy_token_rejected():
    errs = snapshot.validate(_base(r_strategy="R1|R99"))
    assert any("r_strategy" in e for e in errs)


def test_pipe_separated_r_strategy_ok():
    assert snapshot.validate(_base(r_strategy="R2|R8")) == []


def test_invalid_enabler_rejected():
    errs = snapshot.validate(_base(enabler_topic="marketing"))
    assert any("enabler_topic" in e for e in errs)


def test_invalid_target_end_year_rejected():
    errs = snapshot.validate(_base(status="target", value="25",
                                   source_url="http://x", quote="q",
                                   target_end_year="25"))
    assert any("target_end_year" in e for e in errs)


def test_invalid_target_status_rejected():
    errs = snapshot.validate(_base(target_status="pending"))
    assert any("target_status" in e for e in errs)


def test_item_type_autofill_from_status():
    r_found = _base()  # status=found, no item_type
    snapshot._autofill_item_type(r_found)
    assert r_found["item_type"] == "kpi"
    r_target = _base(status="target", value="25", source_url="http://x", quote="q")
    snapshot._autofill_item_type(r_target)
    assert r_target["item_type"] == "target"
    r_nf = _base(status="not_found", value="", source_url="", quote="")
    snapshot._autofill_item_type(r_nf)
    assert r_nf.get("item_type", "") == ""
