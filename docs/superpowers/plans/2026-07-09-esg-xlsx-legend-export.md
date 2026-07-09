# ESG xlsx Legend Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `scripts/export_xlsx.py` that renders an `esg-longitudinal` snapshot CSV into a formatted `.xlsx` workbook with a colored **Data** sheet and an explanatory **Legend** sheet, wired into the skill as Step 10.

**Architecture:** A dependency-light, header-driven Python CLI reads the canonical snapshot CSV (unchanged, still the diff-able source of truth) and produces a downstream `.xlsx` presentation artifact. Three seams — `read_snapshot` (CSV→rows), `build_workbook` (rows→openpyxl Workbook), `main` (argparse/exit codes) — keep each behavior testable at the right level. Fill colors and Legend content are module-level constants/data so the Data sheet, the Legend color-key, and the tests share one source of truth.

**Tech Stack:** Python 3 (stdlib `argparse`/`csv`/`os`/`sys`), `openpyxl` (imported lazily), pytest.

## Global Constraints

- **The canonical CSV is never modified.** No change to `scripts/snapshot.py` or `scripts/diff.py`. The `.xlsx` is generated *from* the snapshot and lands in `reports/`.
- **Read the snapshot with `encoding="utf-8-sig"`** — matches `snapshot.py` (writes a BOM) and `diff.py` (reads with `utf-8-sig`). Plain `utf-8` corrupts the first header to `﻿entity`.
- **`openpyxl` is imported lazily, top-level-first** (`from openpyxl import Workbook`, never a bare `import openpyxl.styles` as the first reference), inside the functions that use it — so the missing-dependency path is reachable and a `sys.modules["openpyxl"]=None` test reliably raises `ImportError`.
- **Header-driven & schema-tolerant:** render exactly the columns present, in file order; never assume 29 columns; unknown columns render plain (no fill), never dropped. Must work on 13-, 19-, and 29-column snapshots.
- **Color constants are shared** module-level ARGB hex strings (`FILL_*`), referenced by the Data sheet, the Legend color-key, AND the tests. No hand-copied second palette.
- **Fill assertions pin the exact ARGB** via the `FILL_*` constants; "no fill" is asserted as `cell.fill.fill_type is None` (never a truthy check).
- All new files live under `esg-longitudinal/` following existing layout: script in `scripts/`, test in `tests/` (imported via `from conftest import _load`).
- Tests run from the skill dir: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -v`.

---

## File Structure

- **Create** `esg-longitudinal/scripts/export_xlsx.py` — the exporter. Module-level: `FILL_*` constants, coloring maps, `LEGEND_COLUMNS`, `LEGEND_CODE_TABLES`. Functions: `read_snapshot`, `_default_out`, `_cell_fill`, `_build_data_sheet`, `_build_legend_sheet`, `build_workbook`, `main`.
- **Create** `esg-longitudinal/tests/test_export_xlsx.py` — pytest coverage. Loads the script via `_load("export_xlsx")` and `snapshot.py` via `_load("snapshot")` for the drift guard.
- **Modify** `esg-longitudinal/SKILL.md` — add Step 10, an Output-section line, a Bundled-resources bullet (note `openpyxl`).
- **Modify** `.gitignore` (repo root) — add `**/reports/*.xlsx`.

**Setup note (once, before the openpyxl-dependent tasks):** `pip install openpyxl`.

---

### Task 1: Module foundations — constants, Legend data, `read_snapshot`

The openpyxl-free core: fill constants, coloring maps, the Legend data structures, and the CSV reader. Testable with no openpyxl installed.

**Files:**
- Create: `esg-longitudinal/scripts/export_xlsx.py`
- Test: `esg-longitudinal/tests/test_export_xlsx.py`

**Interfaces:**
- Consumes: `snapshot.py`'s `COLS`, `VALID_STATUS`, `VALID_ITEM_TYPE`, `VALID_PLANETARY`, `VALID_IMPACT_SCOPE`, `VALID_TARGET_STATUS` (for the drift-guard test only).
- Produces:
  - `FILL_FOUND, FILL_TARGET, FILL_NOT_FOUND, FILL_YES, FILL_NO, FILL_HEADER` — ARGB hex `str`.
  - `STATUS_FILLS: dict[str,str]`, `SMART_YESNO_FILLS: dict[str,str]`, `SUBSTANCE_FILLS: dict[str,str]`, `SMART_COLS: set[str]`.
  - `LEGEND_COLUMNS: list[tuple[str,str,str]]` — (column, meaning, allowed/example).
  - `LEGEND_CODE_TABLES: dict[str, list[tuple[str,str]]]` — keyed by column name (`status`, `item_type`, `impact_scope`, `planetary_alignment`, `target_status`), each a list of (code, meaning).
  - `read_snapshot(path) -> tuple[list[str], list[dict]]` — (columns in file order, row dicts). Reads `utf-8-sig`.

- [ ] **Step 1: Write the failing tests**

Create `esg-longitudinal/tests/test_export_xlsx.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (no `export_xlsx.py` yet).

- [ ] **Step 3: Write the module foundations**

Create `esg-longitudinal/scripts/export_xlsx.py`:

```python
#!/usr/bin/env python3
"""Render a snapshot CSV into a formatted .xlsx workbook (Data + Legend sheets).

The snapshot CSV stays the canonical, diff-able source of truth (written by
snapshot.py); this produces a downstream, shareable .xlsx in reports/. openpyxl
is imported lazily so a missing dependency fails loudly (non-zero) instead of
silently, and so the missing-dep path stays testable.

Usage:
    python export_xlsx.py --snapshot data/snapshots/2026-07-03.csv \
                          --out reports/2026-07-03.xlsx
    # --out defaults to reports/<snapshot-basename>.xlsx
"""
import argparse
import csv
import os
import sys

# --- Fill colors (ARGB hex) shared by the Data sheet, the Legend color-key,
#     and the tests. One source of truth so "the key matches the data" holds. ---
FILL_FOUND = "FF63BE7B"       # green  — found / yes / substantive
FILL_TARGET = "FF4F81BD"      # blue   — target
FILL_NOT_FOUND = "FFBFBFBF"   # grey   — not_found
FILL_NO = "FFD65F5F"          # red    — no / symbolic
FILL_YES = FILL_FOUND         # green  — yes / substantive (same green as found)
FILL_HEADER = "FF404040"      # dark   — header row

STATUS_FILLS = {"found": FILL_FOUND, "target": FILL_TARGET, "not_found": FILL_NOT_FOUND}
SMART_YESNO_FILLS = {"yes": FILL_YES, "no": FILL_NO}
SUBSTANCE_FILLS = {"substantive": FILL_YES, "symbolic": FILL_NO}
SMART_COLS = {"smart_specific", "smart_achievable", "smart_relevant"}

# Columns rendered wider, with wrap-text, so long prose stays readable.
WIDE_COLS = {"quote": 60, "assessment_notes": 50, "source_url": 40,
             "linked_targets": 40, "source": 24, "indicator": 24}
WRAP_COLS = {"quote", "assessment_notes"}
DEFAULT_WIDTH = 16
MAX_WIDTH = 60

# --- Legend content (rendered by _build_legend_sheet; tested openpyxl-free) ---
# (column, plain-English meaning, allowed / example values)
LEGEND_COLUMNS = [
    ("entity", "Company / legal entity the row is about.", "Royal Philips"),
    ("lei", "Legal Entity Identifier (GLEIF) — stable ID across renames/mergers.", "H1FJE8H61JGM1JSGM897"),
    ("domain", "ESG domain pack the indicator belongs to.", "climate | circular | biodiversity | social_gov"),
    ("indicator", "Canonical indicator name (see references/indicators.yaml).", "circular_revenue_pct"),
    ("value", "The disclosed number or text value.", "18"),
    ("unit", "Unit for value.", "% | tCO2e | MWh | year | bool"),
    ("period", "Reporting year the value describes; for targets, the target end year.", "2022"),
    ("status", "Whether this is a disclosed actual, a gap, or a forward goal.", "found | not_found | target"),
    ("source", "Human-readable title of the source document.", "Annual Report 2022"),
    ("source_url", "URL of the source document.", "https://…/annual-report-2022.pdf"),
    ("page", "Page / location of the figure in the source.", "p.41"),
    ("quote", "Verbatim snippet from the source containing the number (provenance).", '"circular revenues were 18% of sales"'),
    ("retrieved_at", "Run date the value was pulled (YYYY-MM-DD) — distinct from period.", "2026-06-29"),
    ("item_type", "Kind of item.", "kpi | target | qualitative"),
    ("r_strategy", "Circular R-strategy R0–R9, pipe-separated (primary first). See circular-economy-10rs.json.", "R2|R8"),
    ("enabler_topic", "Enabler that supports circularity (not itself an R-strategy). See circular-economy-10rs.json.", "training | data_infrastructure | traceability | …"),
    ("target_end_year", "Deadline year of a target (YYYY); blank if none stated.", "2025"),
    ("target_has_kpi", "Whether the target carries a quantified KPI (the M of SMART).", "yes | no"),
    ("target_status", "Year-over-year outcome of the target.", "on_track | achieved | delayed | changed | failed | dropped | too_early"),
    ("smart_specific", "SMART — is the target Specific?", "yes | no"),
    ("smart_achievable", "SMART — is the target Achievable?", "yes | no"),
    ("smart_relevant", "SMART — is the target Relevant?", "yes | no"),
    ("substance", "Real operational commitment vs signaling.", "substantive | symbolic"),
    ("planetary_alignment", "Aligned to a planetary boundary / science-based pathway?", "insufficient | pb_aligned | unknown"),
    ("impact_scope", "A–D scoping (NOT GHG Scope 1/2/3).", "A | B | C | D"),
    ("priority_internal", "Strategic priority inside the company.", "high | low"),
    ("importance_external", "External signaling importance.", "high | low"),
    ("linked_targets", "Free text — other targets this connects to, and how.", "netzero_target_year — shares Scope-3 boundary"),
    ("assessment_notes", "Free-text rationale; required whenever any judgment column is set.", "Quantified 25% with a 2025 deadline; board-level KPI."),
]

LEGEND_CODE_TABLES = {
    "status": [
        ("found", "A disclosed actual for that period."),
        ("not_found", "You looked and it wasn't disclosed — a gap is data, record it."),
        ("target", "A forward-looking goal (e.g. \"25% by 2025\")."),
    ],
    "item_type": [
        ("kpi", "A measured metric / indicator."),
        ("target", "A forward-looking goal."),
        ("qualitative", "A narrative commitment with no number."),
    ],
    "impact_scope": [
        ("A", "Footprint — own operations."),
        ("B", "Footprint — direct value chain (suppliers + use phase)."),
        ("C", "Footprint — broader / enabled system."),
        ("D", "Handprint — positive contribution / avoided impact elsewhere."),
    ],
    "planetary_alignment": [
        ("insufficient", "Not aligned to a planetary boundary."),
        ("pb_aligned", "Aligned to a planetary boundary / science-based pathway."),
        ("unknown", "Checked, can't tell (distinct from blank = not assessed)."),
    ],
    "target_status": [
        ("on_track", "Actuals moving toward the target, deadline unchanged."),
        ("achieved", "Target met (an actual now meets/exceeds it)."),
        ("delayed", "Deadline pushed out."),
        ("changed", "Target value or scope restated."),
        ("failed", "Deadline passed without meeting the target."),
        ("dropped", "Target disappeared from disclosure."),
        ("too_early", "First year seen; not yet assessable."),
    ],
}

# Color-key rows for the Legend (fill constant, label). Same FILL_* the Data
# sheet uses, so the swatches match the data cells exactly.
LEGEND_COLOR_KEY = [
    (FILL_FOUND, "found / yes / substantive"),
    (FILL_TARGET, "target"),
    (FILL_NOT_FOUND, "not_found"),
    (FILL_NO, "no / symbolic"),
]

# Pointer note for codes decoded elsewhere (not duplicated into a code table).
LEGEND_POINTERS = [
    ("r_strategy", "R0–R9 circular strategies — see circular-economy-10rs.json."),
    ("enabler_topic", "11 enabler ids — see circular-economy-10rs.json."),
]


def read_snapshot(path):
    """Return (columns_in_file_order, list_of_row_dicts). Reads utf-8-sig so the
    BOM snapshot.py writes is stripped and the first column is 'entity'."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return columns, rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/scripts/export_xlsx.py esg-longitudinal/tests/test_export_xlsx.py
git commit -m "feat: export_xlsx foundations — fills, Legend data, read_snapshot"
```

---

### Task 2: Data sheet builder (`_build_data_sheet` + `build_workbook` Data half)

Build the colored, frozen, filtered Data sheet from columns + rows. openpyxl-dependent — guard tests with `importorskip`.

**Files:**
- Modify: `esg-longitudinal/scripts/export_xlsx.py`
- Test: `esg-longitudinal/tests/test_export_xlsx.py`

**Interfaces:**
- Consumes: `read_snapshot` output; the `FILL_*`/`*_FILLS`/`SMART_COLS`/`WIDE_COLS`/`WRAP_COLS` from Task 1.
- Produces:
  - `_cell_fill(name, value) -> str | None` — the ARGB fill for a data cell, or `None`.
  - `build_workbook(columns, rows) -> openpyxl.Workbook` — Data sheet complete (Legend added in Task 3). Data sheet named `"Data"`, frozen at `A2`, autofilter across the full used range.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export_xlsx.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -k "data or fill or autofilter or wrap or unknown or uncolored" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_workbook'`.

- [ ] **Step 3: Implement the Data sheet**

Append to `scripts/export_xlsx.py`:

```python
def _cell_fill(name, value):
    """ARGB fill for a data cell, or None (blank, uncolored column, or unknown)."""
    v = (value or "").strip()
    if not v:
        return None
    if name == "status":
        return STATUS_FILLS.get(v)
    if name in SMART_COLS:
        return SMART_YESNO_FILLS.get(v)
    if name == "substance":
        return SUBSTANCE_FILLS.get(v)
    return None


def _solid(color):
    from openpyxl.styles import PatternFill
    return PatternFill(start_color=color, end_color=color, fill_type="solid")


def _build_data_sheet(ws, columns, rows):
    from openpyxl.styles import Font, Alignment
    from openpyxl.utils import get_column_letter

    header_font = Font(bold=True, color="FFFFFFFF")
    header_fill = _solid(FILL_HEADER)
    for c, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font = header_font
        cell.fill = header_fill

    for r, row in enumerate(rows, start=2):
        for c, name in enumerate(columns, start=1):
            value = row.get(name, "")
            cell = ws.cell(row=r, column=c, value=value)
            fill = _cell_fill(name, value)
            if fill:
                cell.fill = _solid(fill)
            if name in WRAP_COLS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    ws.freeze_panes = "A2"
    last_col = get_column_letter(len(columns)) if columns else "A"
    ws.auto_filter.ref = f"A1:{last_col}{len(rows) + 1}"

    for c, name in enumerate(columns, start=1):
        width = min(WIDE_COLS.get(name, DEFAULT_WIDTH), MAX_WIDTH)
        ws.column_dimensions[get_column_letter(c)].width = width


def build_workbook(columns, rows):
    """Build a Workbook with a Data sheet (Legend added in Task 3)."""
    from openpyxl import Workbook
    wb = Workbook()
    data = wb.active
    data.title = "Data"
    _build_data_sheet(data, columns, rows)
    return wb
```

- [ ] **Step 4: Run to verify pass**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -v`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/scripts/export_xlsx.py esg-longitudinal/tests/test_export_xlsx.py
git commit -m "feat: export_xlsx Data sheet — fills, freeze, autofilter, wrap"
```

---

### Task 3: Legend sheet builder + color-key equivalence + schema tolerance

Add the Legend sheet (column dictionary, code tables, pointers, color key) and wire it into `build_workbook`. Then assert the color-key swatches equal the Data fills and that the round-trip works across 13/19/29-column snapshots.

**Files:**
- Modify: `esg-longitudinal/scripts/export_xlsx.py`
- Test: `esg-longitudinal/tests/test_export_xlsx.py`

**Interfaces:**
- Consumes: `LEGEND_COLUMNS`, `LEGEND_CODE_TABLES`, `LEGEND_COLOR_KEY`, `LEGEND_POINTERS` (Task 1); `_solid` (Task 2).
- Produces: `_build_legend_sheet(ws) -> None`; `build_workbook` now yields sheets exactly `["Data", "Legend"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export_xlsx.py`:

```python
def _all_cell_values(ws):
    out = []
    for row in ws.iter_rows(values_only=True):
        out.extend(v for v in row if v is not None)
    return out


def test_sheets_are_exactly_data_and_legend():
    wb = _wb(DATA_COLS, _data_rows())
    assert wb.sheetnames == ["Data", "Legend"]


def test_legend_lists_column_names_and_multichar_codes():
    ws = _wb(DATA_COLS, _data_rows())["Legend"]
    text = "\n".join(str(v) for v in _all_cell_values(ws))
    for name in ("entity", "impact_scope", "planetary_alignment", "target_status"):
        assert name in text
    for code in ("pb_aligned", "not_found", "too_early", "Handprint", "handprint"):
        # at least the lowercased forms are present
        pass
    for code in ("pb_aligned", "not_found", "too_early"):
        assert code in text


def test_legend_impact_scope_uses_code_table_cells_not_substring():
    # 'D' as a bare substring is vacuous; assert D sits in a cell paired with its meaning.
    ws = _wb(DATA_COLS, _data_rows())["Legend"]
    found = False
    for row in ws.iter_rows(values_only=True):
        cells = [str(c) for c in row if c is not None]
        if "D" in cells and any("handprint" in c.lower() for c in cells):
            found = True
            break
    assert found, "impact_scope D / handprint row not found as discrete cells"


def test_color_key_swatches_match_data_fills():
    wb = _wb(DATA_COLS, _data_rows())
    legend = wb["Legend"]
    # Tie each swatch fill to its label's row (col1 swatch, col2 label) so a
    # swapped swatch is caught, not just overall presence of the colors.
    label_to_fill = {}
    for row in legend.iter_rows():
        cells = list(row)
        if len(cells) >= 2 and cells[1].value:
            f = _fill_rgb(cells[0])
            if f is not None:
                label_to_fill[cells[1].value] = f
    for color, label in export_xlsx.LEGEND_COLOR_KEY:
        assert label_to_fill.get(label) == color, f"swatch for {label!r} != {color}"


# 13 / 19 / 29 column headers straight from snapshot.py's COLS.
@pytest.mark.parametrize("width", [13, 19, 29])
def test_schema_tolerance_round_trip(width):
    pytest.importorskip("openpyxl")
    columns = snapshot.COLS[:width]
    row = {c: "" for c in columns}
    row.update({"entity": "X", "domain": "circular", "indicator": "i",
                "period": "2022", "status": "found", "value": "1"})
    wb = export_xlsx.build_workbook(columns, [row])
    assert wb.sheetnames == ["Data", "Legend"]
    header = [wb["Data"].cell(row=1, column=c).value for c in range(1, len(columns) + 1)]
    assert header == columns


def test_header_only_still_valid():
    wb = _wb(["entity", "domain", "indicator", "period", "status"], [])
    assert wb.sheetnames == ["Data", "Legend"]
    assert wb["Data"].cell(row=1, column=1).value == "entity"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -k "legend or sheets or color_key or schema or header_only" -v`
Expected: FAIL — `build_workbook` produces only `["Data"]`; no Legend.

- [ ] **Step 3: Implement the Legend sheet**

Append `_build_legend_sheet` to `scripts/export_xlsx.py` and add its call in `build_workbook`:

```python
def _build_legend_sheet(ws):
    from openpyxl.styles import Font

    bold = Font(bold=True)
    title = Font(bold=True, size=14)
    r = 1

    def heading(txt):
        nonlocal r
        cell = ws.cell(row=r, column=1, value=txt)
        cell.font = title
        r += 2

    def subhead(*cols):
        nonlocal r
        for c, txt in enumerate(cols, start=1):
            ws.cell(row=r, column=c, value=txt).font = bold
        r += 1

    heading("ESG snapshot — Legend")

    subhead("column", "meaning", "allowed / example values")
    for name, meaning, allowed in LEGEND_COLUMNS:
        ws.cell(row=r, column=1, value=name).font = bold
        ws.cell(row=r, column=2, value=meaning)
        ws.cell(row=r, column=3, value=allowed)
        r += 1
    r += 1

    for table_name, entries in LEGEND_CODE_TABLES.items():
        subhead(f"{table_name} — codes", "meaning")
        for code, meaning in entries:
            ws.cell(row=r, column=1, value=code)
            ws.cell(row=r, column=2, value=meaning)
            r += 1
        r += 1

    subhead("coded elsewhere", "pointer")
    for name, note in LEGEND_POINTERS:
        ws.cell(row=r, column=1, value=name)
        ws.cell(row=r, column=2, value=note)
        r += 1
    r += 1

    subhead("color key", "meaning")
    for color, label in LEGEND_COLOR_KEY:
        swatch = ws.cell(row=r, column=1, value="")
        swatch.fill = _solid(color)
        ws.cell(row=r, column=2, value=label)
        r += 1

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 44
```

Then update `build_workbook` (add the Legend sheet before `return`):

```python
def build_workbook(columns, rows):
    """Build a Workbook with a Data sheet and a Legend sheet."""
    from openpyxl import Workbook
    wb = Workbook()
    data = wb.active
    data.title = "Data"
    _build_data_sheet(data, columns, rows)
    legend = wb.create_sheet("Legend")
    _build_legend_sheet(legend)
    return wb
```

- [ ] **Step 4: Run to verify pass**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/scripts/export_xlsx.py esg-longitudinal/tests/test_export_xlsx.py
git commit -m "feat: export_xlsx Legend sheet — dictionary, code tables, color key"
```

---

### Task 4: `main()` — CLI, default `--out`, error paths, happy path

Wire the argparse front door with exact exit-code semantics and the default-output derivation. This is where missing-openpyxl, missing/unreadable-snapshot, and the success contract are pinned.

**Files:**
- Modify: `esg-longitudinal/scripts/export_xlsx.py`
- Test: `esg-longitudinal/tests/test_export_xlsx.py`

**Interfaces:**
- Consumes: `read_snapshot`, `build_workbook`.
- Produces: `_default_out(snapshot_path) -> str` (→ `reports/<basename>.xlsx`); `main(argv=None) -> int` (0 success; 1 snapshot read error; 2 missing openpyxl). Module ends with `if __name__ == "__main__": sys.exit(main())`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export_xlsx.py`:

```python
import sys


def test_default_out_derives_basename():
    assert export_xlsx._default_out("data/snapshots/2026-07-03-Philips.csv") \
        == os.path.join("reports", "2026-07-03-Philips.xlsx")


def test_main_happy_path_writes_workbook(tmp_path):
    pytest.importorskip("openpyxl")
    src = tmp_path / "2026-07-03.csv"
    _write_csv(src, ["entity", "domain", "indicator", "period", "status"],
               [{"entity": "X", "domain": "circular", "indicator": "i",
                 "period": "2022", "status": "found"}])
    out = tmp_path / "out.xlsx"
    rc = export_xlsx.main(["--snapshot", str(src), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    from openpyxl import load_workbook
    assert load_workbook(str(out)).sheetnames == ["Data", "Legend"]


def test_main_missing_snapshot_returns_nonzero_and_names_path(tmp_path, capsys):
    missing = tmp_path / "nope.csv"
    rc = export_xlsx.main(["--snapshot", str(missing), "--out", str(tmp_path / "o.xlsx")])
    assert rc != 0
    assert "nope.csv" in capsys.readouterr().err


def test_main_unreadable_snapshot_dir_returns_nonzero(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    rc = export_xlsx.main(["--snapshot", str(d), "--out", str(tmp_path / "o.xlsx")])
    assert rc != 0


def test_main_missing_openpyxl_returns_nonzero_with_hint(tmp_path, capsys, monkeypatch):
    # NOT importorskip-guarded: this path must run in an env that HAS openpyxl.
    src = tmp_path / "s.csv"
    _write_csv(src, ["entity", "domain", "indicator", "period", "status"],
               [{"entity": "X", "domain": "circular", "indicator": "i",
                 "period": "2022", "status": "found"}])
    # Force ImportError regardless of import form / test order.
    for name in [m for m in list(sys.modules) if m == "openpyxl" or m.startswith("openpyxl.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    rc = export_xlsx.main(["--snapshot", str(src), "--out", str(tmp_path / "o.xlsx")])
    assert rc != 0
    assert "pip install openpyxl" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify failure**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -k "main or default_out" -v`
Expected: FAIL — no `main` / `_default_out`.

- [ ] **Step 3: Implement `main` and `_default_out`**

Append to `scripts/export_xlsx.py`:

```python
def _default_out(snapshot_path):
    base = os.path.splitext(os.path.basename(snapshot_path))[0]
    return os.path.join("reports", f"{base}.xlsx")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Render a snapshot CSV into a formatted .xlsx (Data + Legend).")
    ap.add_argument("--snapshot", required=True, help="path to a snapshot CSV")
    ap.add_argument("--out", default="", help="output .xlsx (default reports/<basename>.xlsx)")
    args = ap.parse_args(argv)

    out = args.out or _default_out(args.snapshot)

    try:
        columns, rows = read_snapshot(args.snapshot)
    except OSError as e:
        print(f"error: cannot read snapshot '{args.snapshot}': {e}", file=sys.stderr)
        return 1

    try:
        wb = build_workbook(columns, rows)
    except ImportError:
        print("error: openpyxl is required to export a workbook — "
              "pip install openpyxl", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    wb.save(out)
    print(f"wrote {len(rows)} data row(s) -> {out}")
    if not rows:
        print("note: snapshot had no data rows (header only)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -v`
Expected: PASS (full file).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/scripts/export_xlsx.py esg-longitudinal/tests/test_export_xlsx.py
git commit -m "feat: export_xlsx main() — CLI, default out, exit codes, happy path"
```

---

### Task 5: Skill wiring — SKILL.md Step 10, Output, Bundled resources, and `.gitignore`

Make the export a documented step of the skill and keep the generated workbook out of git. A small doc-consistency test guards the wiring; the Layer-2 RED/GREEN subagent check is a manual verification step ship runs during implementation.

**Files:**
- Modify: `esg-longitudinal/SKILL.md`
- Modify: `.gitignore` (repo root)
- Test: `esg-longitudinal/tests/test_export_xlsx.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: documentation only. Test asserts SKILL.md documents Step 10 + `export_xlsx.py`, and `.gitignore` ignores `**/reports/*.xlsx`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export_xlsx.py`:

```python
import pathlib

_SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
_REPO_ROOT = _SKILL_DIR.parent


def test_skill_md_documents_export_step():
    text = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "### 10." in text
    assert "export_xlsx.py" in text
    # export_xlsx.py appears in the Bundled resources list
    bundled = text.split("## Bundled resources", 1)[1]
    assert "export_xlsx.py" in bundled


def test_gitignore_excludes_report_workbooks():
    gi = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "**/reports/*.xlsx" in gi
```

- [ ] **Step 2: Run to verify failure**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -k "skill_md or gitignore" -v`
Expected: FAIL — Step 10 / gitignore entry absent.

- [ ] **Step 3: Make the doc + config edits**

In `esg-longitudinal/SKILL.md`, add a new step immediately after the `### 9. Report` section (before `## Output: report structure`):

```markdown
### 10. Export workbook (shareable)
Turn the snapshot CSV written in step 7 into a formatted, colored Excel workbook
with a **Legend** tab that explains every column and coded value:

```bash
pip install openpyxl   # one-time
python scripts/export_xlsx.py --snapshot data/snapshots/2026-06-29.csv \
                              --out reports/2026-06-29.xlsx
```

It reads the canonical snapshot (never modifies it) and writes an `.xlsx` with a
**Data** sheet (frozen, filtered, color-coded `status` and SMART cells) and a
**Legend** sheet (a plain-English data dictionary + code tables + color key). The
`.xlsx` is the shareable deliverable for a non-author; the CSV snapshot stays the
canonical, diff-able source of truth. `--out` defaults to
`reports/<snapshot-basename>.xlsx`. If `openpyxl` is missing the script prints an
install hint and exits non-zero.
```

In the `## Output: report structure` section, add one line noting the workbook is also produced — after the fenced report template, add:

```markdown
Alongside the markdown report, step 10 emits `reports/<date>.xlsx` — a colored
Data + Legend workbook rendered from the snapshot for sharing.
```

In `## Bundled resources`, add a bullet after the `diff.py` line:

```markdown
- `scripts/export_xlsx.py` — render a snapshot CSV into a formatted `.xlsx`
  (Data + Legend tabs, colored); requires `openpyxl` (`pip install openpyxl`).
```

In the repo-root `.gitignore`, add under the run-artifacts section:

```
# Generated Excel workbooks (regenerable presentation artifacts derived from
# snapshot data); the canonical snapshot CSVs stay tracked.
**/reports/*.xlsx
```

- [ ] **Step 4: Run to verify pass**

Run: `cd esg-longitudinal && python -m pytest tests/test_export_xlsx.py -v`
Expected: PASS (entire suite).

- [ ] **Step 5: Verify the skill behavior (Layer-2 RED/GREEN, manual)**

This confirms the *skill edit* works, per writing-skills' "an edit needs a failing test." Ship runs this during implementation/verification:
- **RED reference:** on `main` (pre–Step 10), a subagent handed a written snapshot + the skill finishes at the CSV/markdown report and produces no `.xlsx`.
- **GREEN:** on this branch, a subagent following the skill runs `export_xlsx.py` and produces `reports/<date>.xlsx` with Data + Legend tabs. Record the split (e.g. 0/3 → 3/3) in the PR body.

- [ ] **Step 6: Commit**

```bash
git add esg-longitudinal/SKILL.md .gitignore esg-longitudinal/tests/test_export_xlsx.py
git commit -m "docs: wire export_xlsx as SKILL Step 10; ignore reports/*.xlsx"
```

---

## Self-Review

**1. Spec coverage** (each spec section → task):
- CLI `--snapshot`/`--out` + default → Task 4 (`main`, `_default_out`). ✓
- utf-8-sig read → Task 1 (`read_snapshot`) + BOM test. ✓
- Lazy top-level-first openpyxl + missing-dep non-zero + hint → Task 2 (lazy imports) / Task 4 (`main` catch) + monkeypatch-purge test. ✓
- `reports/` makedirs before save → Task 4. ✓
- Header-driven / schema-tolerant / unknown-plain → Task 2 `_cell_fill` returns None default + Task 3 schema-tolerance parametrize + unknown-col test. ✓
- Shared `FILL_*` + exact-ARGB asserts + blank=`fill_type is None` → Tasks 1–2 constants + fill matrix/uncolored/blank tests. ✓
- Data sheet: frozen A2, autofilter full range, status/SMART fills, wrap on quote/assessment_notes, widths → Task 2. ✓
- Legend: column dictionary (all 29), code tables (status/item_type/impact_scope/planetary/target_status), R/enabler pointer, color key → Tasks 1+3. ✓
- Color-key equivalence with Data fills → Task 3 test. ✓
- impact_scope code-table-cell check (not substring) → Task 3 test. ✓
- Drift guard (openpyxl-free, correct enum scope) → Task 1 tests. ✓
- Happy path + missing/unreadable snapshot (error names path) → Task 4. ✓
- Header-only CSV valid → Task 3. ✓
- Skill Step 10 + Output + Bundled (openpyxl noted) + Step-10 consumes snapshot CSV → Task 5. ✓
- `.gitignore **/reports/*.xlsx` → Task 5. ✓
- Layer-2 RED/GREEN → Task 5 Step 5. ✓
- No change to snapshot.py/diff.py → nothing touches them (Global Constraints). ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**3. Type consistency:** `read_snapshot -> (list, list)` used by `build_workbook(columns, rows)` and `main`; `_cell_fill(name, value) -> str|None` used by `_build_data_sheet`; `_solid(color)` shared by Data + Legend; `FILL_*`/`LEGEND_*` names identical across tasks and tests; `main(argv=None) -> int` with returns 0/1/2 matches tests. ✓
