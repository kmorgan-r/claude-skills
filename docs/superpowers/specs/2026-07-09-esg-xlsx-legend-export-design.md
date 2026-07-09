# ESG Longitudinal — Excel workbook export with a Legend tab

**Date:** 2026-07-09
**Skill:** `esg-longitudinal`
**Status:** Approved design, ready for implementation plan

## Problem

The skill's durable output is a tidy long-format CSV snapshot (29 columns). It diffs
cleanly and is provenance-checked, but a non-author opening it cannot tell what the
columns or the coded values mean: `impact_scope = D`, `planetary_alignment =
pb_aligned`, `status = not_found`, and the many blank SMART+ cells all read as noise.
The user asked for a **tab on the Excel sheet that clearly explains what everything
means**, made user-friendly and colored.

CSV cannot carry multiple tabs or color. So this adds a **presentation layer**: a
script that renders a snapshot CSV into a formatted `.xlsx` workbook with a Data
sheet and a Legend (data-dictionary) sheet. The CSV snapshot remains the canonical,
diff-able source of truth (design principle #1); the workbook is generated *from* it
and is the shareable artifact.

## Goals

- One command turns a snapshot CSV into a formatted `.xlsx`.
- A **Legend** sheet explains, in plain English, every column and every coded value,
  plus a color key — readable by someone who has never seen the schema.
- The **Data** sheet is friendly to read: frozen/colored header, autofilter, and
  color-coded `status` and SMART cells.
- The CSV snapshot is never modified or replaced. No change to `snapshot.py` or
  `diff.py`.
- Works on any snapshot the skill can produce (13-, 19-, or 29-column) without edits.

## Non-goals (YAGNI)

- No per-domain row coloring.
- No numeric-delta / conditional formatting or sparklines.
- No charts or pivot tables.
- No change to the canonical CSV schema or the diff logic.
- Not wired into `snapshot.py` — export is a separate, explicit step so the snapshot
  stays a pure, dependency-light CSV writer.

## Architecture

### New component: `scripts/export_xlsx.py`

```bash
python scripts/export_xlsx.py --snapshot data/snapshots/2026-07-03.csv \
                              --out reports/2026-07-03.xlsx
```

- **Input:** a snapshot CSV (the canonical file).
- **Output:** an `.xlsx` in `reports/` (a presentation artifact — kept out of
  `data/snapshots/`, which stays pure CSV for diffing). `--out` defaults to
  `reports/<snapshot-basename>.xlsx` when omitted.
- **Library:** `openpyxl` (multi-sheet, cell fills, freeze panes, autofilter, column
  widths). If `openpyxl` is not importable, the script prints a one-line install hint
  (`pip install openpyxl`) and exits non-zero — an explicit failure, never a silent
  no-op. This mirrors how `find_reports.py` handles a missing `ddgs`.

### Header-driven, schema-tolerant

The script reads the CSV header and renders exactly the columns present, in file
order. It does **not** assume 29 columns. A known-columns table drives labeling and
coloring; unknown columns are still rendered (plain) so nothing is dropped. This keeps
the script working across the skill's schema versions, consistent with the skill's
"old snapshots still validate" philosophy.

### Workbook structure (two sheets)

**Sheet 1 — `Data`**
- All snapshot rows, columns in CSV order.
- Header row: dark fill, white bold text, **frozen** (`freeze_panes` at `A2`),
  **autofilter** across the used range.
- `status` cell fill: `found` → green, `target` → blue, `not_found` → grey.
- SMART judgment cells (`smart_specific`, `smart_achievable`, `smart_relevant`,
  `substance`): `yes`/`substantive` → green, `no`/`symbolic` → red, blank → no fill.
- Column widths: sensible defaults, capped; `quote` and `assessment_notes` widened
  with wrap-text so long text is readable without exploding the grid.

**Sheet 2 — `Legend`**
- Styled, colored, static content (authored from `SKILL.md`'s schema section,
  `references/indicators.yaml`, and `circular-economy-10rs.json`), so it is
  reproducible and independent of run data.
- **Column dictionary:** one row per canonical column → plain-English meaning +
  allowed/example values. Covers all known columns even if a given snapshot omits some
  (so the legend is complete regardless of schema version).
- **Code tables:** `status` (found / target / not_found), `item_type` (kpi / target /
  qualitative), `impact_scope` (A own operations / B direct value chain / C broader
  system / D handprint), `planetary_alignment` (insufficient / pb_aligned / unknown),
  and a pointer to the 10 R-strategies in `circular-economy-10rs.json`.
- **Color key:** a small block mapping each fill color to its meaning (green =
  found / yes / substantive; blue = target; grey = not_found; red = no / symbolic),
  matching the fills used on the Data sheet exactly.

### Data flow

```
data/snapshots/2026-07-03.csv   (canonical, unchanged)
        │  read header + rows
        ▼
scripts/export_xlsx.py  ── openpyxl ──▶  reports/2026-07-03.xlsx
        │                                   ├── Data   (colored, filtered, frozen)
        │  static schema knowledge          └── Legend (dictionary + codes + color key)
        └── from SKILL.md / indicators.yaml / circular-economy-10rs.json
```

### Skill wiring (`SKILL.md`)

- Add **Step 10 — Export workbook** after Step 9 (Report): run `export_xlsx.py` on the
  snapshot just written; the `.xlsx` (with its Legend tab) is the shareable deliverable,
  the CSV snapshot the canonical one.
- Add the workbook to the Output section and `export_xlsx.py` to Bundled resources.
- Keep the edit small and additive; it must not alter the snapshot-first framing.

## Error handling

- Missing `openpyxl` → clear install hint, non-zero exit.
- Missing / unreadable `--snapshot` file → clear error, non-zero exit.
- Empty CSV (header only) → still writes a valid workbook (header + Legend), logs that
  no data rows were found. A gap is not a crash.
- Unknown columns present → rendered plain, not an error (forward-compatible).

## Testing

Two layers, both test-first (RED before GREEN).

### 1. Script — deterministic pytest (`tests/test_export_xlsx.py`)

Write the test first and watch it fail (no script yet), then build the script to pass:

- Feed a small fixture CSV (a few `found` / `target` / `not_found` rows across
  columns) → run the export → reopen the `.xlsx` with `openpyxl` and assert:
  - sheets are exactly `Data` and `Legend`;
  - the Data header row carries every input column, in order, and is frozen;
  - a `status = found` cell has the green fill; `not_found` has grey; a `smart_* = no`
    cell has red; a blank SMART cell has no fill;
  - the Legend sheet contains each canonical column name and the code values
    (`pb_aligned`, `handprint`/`D`, `not_found`, …);
  - the color-key block is present.
- Edge test: header-only CSV → workbook still valid with both sheets.
- Skip the module cleanly (pytest `importorskip`) if `openpyxl` is unavailable, so the
  existing suite stays green in a bare environment.

### 2. Skill behavior — RED/GREEN with subagents

Per the writing-skills Iron Law (an edit needs a failing test first):

- **RED:** give subagents a written snapshot + the current skill; confirm they finish
  at the CSV/markdown report and do **not** produce a colored `.xlsx` with a Legend
  tab.
- **GREEN:** with Step 10 added, confirm they run `export_xlsx.py` and produce the
  workbook. Report the split (e.g. 0/5 → 5/5) in the PR.

## Deliverables

- `scripts/export_xlsx.py` (new).
- `tests/test_export_xlsx.py` (new).
- `SKILL.md` — Step 10, Output, and Bundled resources updates (additive).
- Isolated on branch `feat/esg-xlsx-legend-export` off `main`; its own PR.
