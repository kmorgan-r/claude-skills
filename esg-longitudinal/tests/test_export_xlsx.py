import os
from conftest import _load

export_xlsx = _load("export_xlsx")
snapshot = _load("snapshot")


def _write_csv(path, columns, rows):
    """Write a snapshot-style CSV with a BOM, exactly like snapshot.py does."""
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_read_snapshot_strips_bom_and_preserves_order(tmp_path):
    p = tmp_path / "s.csv"
    cols = ["entity", "domain", "indicator", "value", "period", "status"]
    _write_csv(p, cols, [{"entity": "Royal Philips", "domain": "circular",
                          "indicator": "circular_revenue_pct", "value": "18",
                          "period": "2022", "status": "found"}])
    columns, rows = export_xlsx.read_snapshot(str(p))
    assert columns == cols               # first col is 'entity', not '﻿entity'
    assert columns[0] == "entity"
    assert rows[0]["value"] == "18"


def test_read_snapshot_handles_embedded_comma_and_newline(tmp_path):
    p = tmp_path / "s.csv"
    cols = ["entity", "domain", "indicator", "period", "status", "quote"]
    tricky = 'He said, "25% by 2025",\nacross two lines'
    _write_csv(p, cols, [{"entity": "X", "domain": "circular",
                          "indicator": "i", "period": "2025",
                          "status": "target", "quote": tricky}])
    _, rows = export_xlsx.read_snapshot(str(p))
    assert rows[0]["quote"] == tricky    # one cell, comma+newline intact


def test_read_snapshot_header_only(tmp_path):
    p = tmp_path / "s.csv"
    cols = ["entity", "domain", "indicator", "period", "status"]
    _write_csv(p, cols, [])
    columns, rows = export_xlsx.read_snapshot(str(p))
    assert columns == cols
    assert rows == []


def test_legend_covers_every_canonical_column():
    documented = [c for c, _, _ in export_xlsx.LEGEND_COLUMNS]
    # set-equality (not subset): catches misspellings, omissions, AND extras/dupes.
    assert set(documented) == set(snapshot.COLS)
    assert len(documented) == len(snapshot.COLS)


def test_legend_code_tables_cover_enums():
    def codes(key):
        return {code for code, _ in export_xlsx.LEGEND_CODE_TABLES[key]}
    assert snapshot.VALID_STATUS <= codes("status")
    assert snapshot.VALID_ITEM_TYPE <= codes("item_type")
    assert snapshot.VALID_PLANETARY <= codes("planetary_alignment")
    assert snapshot.VALID_IMPACT_SCOPE <= codes("impact_scope")
    assert snapshot.VALID_TARGET_STATUS <= codes("target_status")


def test_fill_constants_shared_identity():
    assert export_xlsx.FILL_YES == export_xlsx.FILL_FOUND
    assert export_xlsx.STATUS_FILLS["found"] == export_xlsx.FILL_FOUND
    assert export_xlsx.STATUS_FILLS["target"] == export_xlsx.FILL_TARGET
    assert export_xlsx.STATUS_FILLS["not_found"] == export_xlsx.FILL_NOT_FOUND
    assert export_xlsx.SUBSTANCE_FILLS["symbolic"] == export_xlsx.FILL_NO
