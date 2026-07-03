from conftest import _load

import pytest

snapshot = _load("snapshot")

V1_COLS = ["item_type", "r_strategy", "enabler_topic",
           "target_end_year", "target_has_kpi", "target_status"]
V2_COLS = ["smart_specific", "smart_achievable", "smart_relevant", "substance",
           "planetary_alignment", "impact_scope", "priority_internal",
           "importance_external", "linked_targets", "assessment_notes"]


def _target(**over):
    """A valid status=target row (carries provenance) for exercising v2 fields."""
    row = {"entity": "Royal Philips", "domain": "circular",
           "indicator": "circular_revenue_pct", "period": "2025",
           "status": "target", "retrieved_at": "2026-07-02",
           "value": "25", "source_url": "http://x", "quote": "25% by 2025"}
    row.update(over)
    return row


def _base(**over):
    row = {"entity": "Royal Philips", "domain": "circular",
           "indicator": "circular_revenue_pct", "period": "2022",
           "status": "found", "retrieved_at": "2026-07-02",
           "value": "18", "source_url": "http://x", "quote": "18% circular"}
    row.update(over)
    return row


def test_header_has_29_columns_in_order():
    assert snapshot.COLS[:13] == [
        "entity", "lei", "domain", "indicator", "value", "unit", "period",
        "status", "source", "source_url", "page", "quote", "retrieved_at"]
    assert snapshot.COLS[13:19] == V1_COLS
    assert snapshot.COLS[19:29] == V2_COLS
    assert len(snapshot.COLS) == 29


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


@pytest.mark.parametrize("col,bad", [
    ("smart_specific", "Yes"),
    ("smart_achievable", "maybe"),
    ("smart_relevant", "y"),
    ("substance", "sym"),
    ("planetary_alignment", "aligned"),
    ("impact_scope", "a"),
    ("priority_internal", "High"),
    ("importance_external", "medium"),
])
def test_invalid_v2_enum_rejected(col, bad):
    # notes present so ONLY the enum error can fire
    errs = snapshot.validate(_target(**{col: bad, "assessment_notes": "n"}))
    assert any(col in e for e in errs)


@pytest.mark.parametrize("col,good", [
    ("smart_specific", "yes"),
    ("smart_achievable", "no"),
    ("smart_relevant", "yes"),
    ("substance", "substantive"),
    ("planetary_alignment", "pb_aligned"),
    ("impact_scope", "D"),
    ("priority_internal", "high"),
    ("importance_external", "low"),
])
def test_judgment_column_requires_notes(col, good):
    # judgment set, no notes -> D1 error
    errs = snapshot.validate(_target(**{col: good}))
    assert any("assessment_notes" in e for e in errs)
    # same, with notes -> clean
    assert snapshot.validate(_target(**{col: good, "assessment_notes": "because"})) == []


def test_linked_targets_alone_does_not_require_notes():
    assert snapshot.validate(
        _target(linked_targets="netzero_target_year — shares Scope-3 boundary")) == []


def test_whitespace_notes_counts_as_empty():
    errs = snapshot.validate(_target(substance="substantive", assessment_notes="   "))
    assert any("assessment_notes" in e for e in errs)


def test_full_v2_row_valid():
    row = _target(item_type="target", r_strategy="R1|R2",
                  target_end_year="2025", target_has_kpi="yes", target_status="on_track",
                  smart_specific="yes", smart_achievable="yes", smart_relevant="yes",
                  substance="substantive", planetary_alignment="pb_aligned",
                  impact_scope="D", priority_internal="high", importance_external="high",
                  linked_targets="netzero_target_year — circular design lowers Scope 3",
                  assessment_notes="Quantified 25% with a 2025 deadline; board-level KPI.")
    assert snapshot.validate(row) == []


def test_invalid_enum_and_missing_notes_both_reported():
    # validate() must accumulate, not short-circuit. The D1 message ALSO contains
    # "substance" (in the column list), so pin the distinct enum message explicitly
    # and assert exactly two errors — otherwise a short-circuit that dropped the enum
    # error would still satisfy a bare `any("substance" ...)`.
    errs = snapshot.validate(_target(substance="maybe"))
    assert any(e.startswith("invalid substance") for e in errs)  # the enum error
    assert any("assessment_notes" in e for e in errs)            # the D1 error
    assert len(errs) == 2


def test_notes_without_judgment_is_valid():
    assert snapshot.validate(_target(assessment_notes="context only, no judgment set")) == []
