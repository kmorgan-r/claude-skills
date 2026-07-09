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


import pytest
# NOTE: do NOT import any openpyxl symbol at module scope — that would run at
# pytest collection time and error the whole module (defeating the openpyxl-free
# tests and the importorskip fallback) when openpyxl is absent. Import openpyxl
# names lazily, inside tests that already went through _wb()'s importorskip.


def _wb(columns, rows):
    pytest.importorskip("openpyxl")
    return export_xlsx.build_workbook(columns, rows)


DATA_COLS = ["entity", "domain", "indicator", "value", "period", "status",
             "item_type", "target_status", "smart_specific", "substance",
             "planetary_alignment", "impact_scope", "quote", "assessment_notes",
             "made_up_col"]


def _data_rows():
    return [
        {"entity": "X", "domain": "circular", "indicator": "i1", "value": "18",
         "period": "2022", "status": "found", "quote": "q", "made_up_col": "z"},
        {"entity": "X", "domain": "circular", "indicator": "i2", "value": "25",
         "period": "2025", "status": "target", "item_type": "target",
         "target_status": "on_track", "smart_specific": "yes", "substance": "substantive",
         "planetary_alignment": "pb_aligned", "impact_scope": "D",
         "quote": "25% by 2025", "assessment_notes": "board KPI"},
        {"entity": "X", "domain": "circular", "indicator": "i3", "value": "",
         "period": "2021", "status": "not_found", "smart_specific": "no",
         "substance": "symbolic"},
    ]


def _fill_rgb(cell):
    return cell.fill.fgColor.rgb if cell.fill and cell.fill.fill_type else None


def _col_idx(ws, name):
    for c in range(1, ws.max_column + 1):
        if ws.cell(row=1, column=c).value == name:
            return c
    raise AssertionError(f"column {name} not in header")


def test_data_sheet_header_order_and_freeze():
    ws = _wb(DATA_COLS, _data_rows())["Data"]
    header = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    assert header == DATA_COLS
    assert ws.freeze_panes == "A2"


def test_autofilter_covers_full_range():
    rows = _data_rows()
    ws = _wb(DATA_COLS, rows)["Data"]
    from openpyxl.utils import get_column_letter   # lazy: after _wb's importorskip
    last_col = get_column_letter(len(DATA_COLS))
    assert ws.auto_filter.ref == f"A1:{last_col}{len(rows) + 1}"


@pytest.mark.parametrize("col,value,expected", [
    ("status", "found", "FILL_FOUND"),
    ("status", "target", "FILL_TARGET"),
    ("status", "not_found", "FILL_NOT_FOUND"),
    ("smart_specific", "yes", "FILL_YES"),
    ("smart_specific", "no", "FILL_NO"),
    ("substance", "substantive", "FILL_YES"),
    ("substance", "symbolic", "FILL_NO"),
])
def test_cell_fill_matrix(col, value, expected):
    pytest.importorskip("openpyxl")
    assert export_xlsx._cell_fill(col, value) == getattr(export_xlsx, expected)


def test_status_cells_get_expected_fill_on_sheet():
    rows = _data_rows()
    ws = _wb(DATA_COLS, rows)["Data"]
    sc = _col_idx(ws, "status")
    assert _fill_rgb(ws.cell(row=2, column=sc)) == export_xlsx.FILL_FOUND
    assert _fill_rgb(ws.cell(row=3, column=sc)) == export_xlsx.FILL_TARGET
    assert _fill_rgb(ws.cell(row=4, column=sc)) == export_xlsx.FILL_NOT_FOUND


def test_uncolored_coded_columns_have_no_fill():
    ws = _wb(DATA_COLS, _data_rows())["Data"]
    for name in ("item_type", "target_status", "planetary_alignment", "impact_scope"):
        c = _col_idx(ws, name)
        assert _fill_rgb(ws.cell(row=3, column=c)) is None, f"{name} should be uncolored"


def test_unknown_column_has_no_fill_but_appears():
    ws = _wb(DATA_COLS, _data_rows())["Data"]
    c = _col_idx(ws, "made_up_col")
    assert _fill_rgb(ws.cell(row=2, column=c)) is None


def test_blank_smart_cell_has_no_fill():
    ws = _wb(DATA_COLS, _data_rows())["Data"]
    c = _col_idx(ws, "smart_specific")
    assert _fill_rgb(ws.cell(row=2, column=c)) is None   # row 2 has no smart_specific


def test_wrap_text_on_long_columns():
    ws = _wb(DATA_COLS, _data_rows())["Data"]
    c = _col_idx(ws, "quote")
    assert ws.cell(row=2, column=c).alignment.wrap_text is True
