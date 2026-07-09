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

- **Input:** a snapshot CSV (the canonical file). It is read with
  `encoding="utf-8-sig"` to match how `snapshot.py` writes it (with a BOM) and how
  `diff.py` reads it — otherwise the first header cell arrives as `﻿entity` and
  every name-keyed lookup on the first column silently breaks.
- **Output:** an `.xlsx` in `reports/` (a presentation artifact — kept out of
  `data/snapshots/`, which stays pure CSV for diffing). `--out` defaults to
  `reports/<snapshot-basename>.xlsx` when omitted (basename derived from the snapshot
  filename, so `2026-07-03-Philips.csv` → `reports/2026-07-03-Philips.xlsx`). The
  output directory is created before saving with
  `os.makedirs(os.path.dirname(out) or ".", exist_ok=True)` — the same idiom
  `diff.py` uses — because `reports/` does not exist on a fresh checkout and
  `openpyxl`'s `save()` raises `FileNotFoundError` on a missing parent dir.
- **Library:** `openpyxl` (multi-sheet, cell fills, freeze panes, autofilter, column
  widths). `openpyxl` is imported **lazily** inside the functions that need it so the
  missing-dependency path is reachable and testable (see Entry points). If it is not
  importable, the script prints a one-line install hint (`pip install openpyxl`) and
  returns a non-zero exit code — an explicit failure, never a silent no-op.

  (Note: this is deliberately *stricter* than `find_reports.py`, which degrades to a
  graceful exit-0 JSON payload when `ddgs` is absent. Here a missing library means no
  workbook can be produced, so the correct behavior is a non-zero exit — do not copy
  `find_reports.py`'s exit-0 fallback.)

### Entry points (testability seams)

The module exposes three seams so each behavior can be tested at the right level:

- `read_snapshot(path) -> (columns: list[str], rows: list[dict])` — opens the CSV
  with `utf-8-sig`, returns the header (in file order) and the row dicts.
- `build_workbook(columns, rows) -> openpyxl.Workbook` — the pure builder. Given the
  columns (file order) and rows, returns an in-memory `Workbook` with the `Data` and
  `Legend` sheets fully styled. Fill/sheet/freeze/autofilter assertions test this
  directly, with no file I/O.
- `main(argv=None) -> int` — argparse front door. Resolves the default `--out`,
  catches missing `openpyxl` (→ hint + non-zero return) and a missing/unreadable
  snapshot (→ clear error + non-zero return), calls `read_snapshot` → `build_workbook`
  → `makedirs` → `save`, and returns `0` on success. Exit-code and default-path
  behaviors test this.

`if __name__ == "__main__": sys.exit(main())`.

### Header-driven, schema-tolerant

The script reads the CSV header and renders exactly the columns present, in file
order. It does **not** assume 29 columns. A known-columns table drives labeling and
coloring; unknown columns are still rendered (plain, no fill) so nothing is dropped.
This keeps the script working across the skill's schema versions (13-, 19-, and
29-column snapshots), consistent with the skill's "old snapshots still validate"
philosophy.

### Color constants (shared, single source)

All fills are module-level named constants (ARGB hex strings), referenced by both the
Data sheet, the Legend color-key block, and the tests, so the "color key matches the
Data fills exactly" promise is enforced by shared identity rather than by two
hand-copied palettes:

- `FILL_FOUND` (green), `FILL_TARGET` (blue), `FILL_NOT_FOUND` (grey)
- `FILL_YES` (green, = `FILL_FOUND`), `FILL_NO` (red)

### Workbook structure (two sheets)

**Sheet 1 — `Data`**
- All snapshot rows, columns in CSV order.
- Header row: dark fill, white bold text, **frozen** (`freeze_panes` at `A2`),
  **autofilter** across the used range (`ws.auto_filter.ref`).
- `status` cell fill: `found` → `FILL_FOUND`, `target` → `FILL_TARGET`,
  `not_found` → `FILL_NOT_FOUND`.
- SMART judgment cells (`smart_specific`, `smart_achievable`, `smart_relevant`,
  `substance`): `yes`/`substantive` → `FILL_YES` (green), `no`/`symbolic` →
  `FILL_NO` (red), blank → no fill (`fill_type is None`). Note `substance` uses the
  `symbolic|substantive` vocabulary, not `yes|no`.
- Column widths: sensible defaults, capped; `quote` and `assessment_notes` widened
  with wrap-text (`alignment.wrap_text = True`) so long text is readable without
  exploding the grid.

**Sheet 2 — `Legend`**
- Styled, colored content. Plain-English descriptions are authored statically (from
  `SKILL.md`'s schema section); the **coded value lists** for the code tables are the
  literal enum members, kept in sync with `snapshot.py` by a test (see Testing → drift
  guard) rather than drifting silently.
- **Column dictionary:** one row per canonical column → plain-English meaning +
  allowed/example values. Covers all known columns even if a given snapshot omits some
  (so the legend is complete regardless of schema version).
- **Code tables:** `status` (found / target / not_found), `item_type` (kpi / target /
  qualitative), `impact_scope` (A own operations / B direct value chain / C broader
  system / D handprint), `planetary_alignment` (insufficient / pb_aligned / unknown),
  `target_status` (on_track / achieved / delayed / changed / failed / dropped /
  too_early), and a pointer to the 10 R-strategies + enabler ids in
  `circular-economy-10rs.json`.
- **Color key:** a small block mapping each fill (the same `FILL_*` constants) to its
  meaning (green = found / yes / substantive; blue = target; grey = not_found; red =
  no / symbolic), matching the fills used on the Data sheet exactly.

### Data flow

```
data/snapshots/2026-07-03.csv   (canonical, unchanged)
        │  read_snapshot()  (utf-8-sig → header + rows)
        ▼
scripts/export_xlsx.py  ── openpyxl ──▶  reports/2026-07-03.xlsx
        │  build_workbook()                 ├── Data   (colored, filtered, frozen)
        │  static schema knowledge          └── Legend (dictionary + codes + color key)
        └── from SKILL.md / indicators.yaml / circular-economy-10rs.json
```

### Skill wiring (`SKILL.md`)

- Add **Step 10 — Export workbook** after Step 9 (Report): run `export_xlsx.py` on the
  snapshot just written; the `.xlsx` (with its Legend tab) is the shareable
  deliverable, the CSV snapshot the canonical one. Note the one-time
  `pip install openpyxl` prerequisite here so an agent installs it up front rather
  than hitting the failure path mid-workflow.
- Add the workbook to the Output section and `export_xlsx.py` to Bundled resources
  (noting the `openpyxl` dependency).
- Keep the edit small and additive; it must not alter the snapshot-first framing.

### Data hygiene (`.gitignore`)

`reports/*.xlsx` is added to `.gitignore`. The workbook is a regenerable binary
artifact derived from snapshot data; ignoring it is consistent with the repo's
existing `**/outputs/*` posture (exports are not committed) while leaving the small,
diff-friendly `reports/*.md` change reports tracked as they are today. The canonical
`data/snapshots/*.csv` remains committed — it is the durable baseline, so this does
not touch it.

## Error handling

- Missing `openpyxl` → clear install hint, non-zero return from `main()`.
- Missing / unreadable `--snapshot` file → clear error, non-zero return from `main()`.
- Missing `reports/` directory → created via `makedirs` before save (never a crash).
- Empty CSV (header only) → still writes a valid workbook (header + Legend), logs that
  no data rows were found. A gap is not a crash.
- Unknown columns present → rendered plain (no fill), not an error (forward-compatible).

## Testing

Two layers, both test-first (RED before GREEN).

### 1. Script — deterministic pytest (`tests/test_export_xlsx.py`)

Follows the existing test convention: `from conftest import _load; export_xlsx =
_load("export_xlsx")`. Output is written under `tmp_path` with an explicit `--out`
(never the repo `reports/` default) so tests don't pollute the tree or collide in
parallel. Fixtures are written with `encoding="utf-8-sig"` so they exercise the real
BOM path.

Guard the openpyxl-dependent tests with `pytest.importorskip("openpyxl")` **inside a
fixture / those test bodies**, NOT at module top — module-top `importorskip` would
also skip the missing-dependency test below, which must run in an env that has
openpyxl.

Write the tests first and watch them fail (no script yet), then build the script to
pass. Coverage:

- **Sheets & header:** feed a fixture CSV (found / target / not_found rows) → build →
  assert sheets are exactly `Data` and `Legend`; the Data header row carries every
  input column, in order (first column `entity`, BOM stripped); `freeze_panes ==
  "A2"`; `ws.auto_filter.ref` is set.
- **Full fill matrix** (assert exact ARGB via the module's `FILL_*` constants, not a
  literal or a truthy check): `status` found→`FILL_FOUND`, target→`FILL_TARGET`,
  not_found→`FILL_NOT_FOUND`; `smart_*` yes→`FILL_YES`, no→`FILL_NO`; `substance`
  substantive→`FILL_YES`, symbolic→`FILL_NO`; a blank SMART cell → `fill_type is
  None`.
- **Unknown column:** include a non-canonical column in the fixture; assert its data
  cells have `fill_type is None` and it still appears in the header.
- **Wrap:** a `quote` / `assessment_notes` data cell has `alignment.wrap_text` true.
- **Legend content:** the Legend sheet contains each canonical column name and the
  code values (`pb_aligned`, `handprint`/`D`, `not_found`, `too_early`, …).
- **Color-key equivalence:** each Legend color-key swatch fill equals the
  corresponding Data-cell fill (same `FILL_*` constant) — the "matches exactly"
  promise, tested.
- **Schema tolerance:** parametrize the round-trip over representative 13-, 19-, and
  29-column headers; assert header order preserved and both sheets present on each.
- **Header-only CSV:** workbook still valid with both sheets, no data rows.
- **Missing snapshot:** call `main()` with a nonexistent `--snapshot`; assert non-zero
  return.
- **Missing openpyxl** (NOT importorskip-guarded): force the import to fail
  (`monkeypatch.setitem(sys.modules, "openpyxl", None)`), call `main()` with a valid
  snapshot; assert non-zero return and that the install hint was printed. (This is why
  openpyxl is imported lazily inside the functions.)
- **Drift guard:** load `snapshot.py` via `_load("snapshot")`; assert every member of
  its `VALID_STATUS`, `VALID_ITEM_TYPE`, `VALID_PLANETARY`, `VALID_IMPACT_SCOPE`, and
  `VALID_TARGET_STATUS` sets, and every name in `COLS`, appears in the rendered Legend
  text — so a future enum/column change that isn't reflected in the Legend fails CI.

### 2. Skill behavior — RED/GREEN with subagents

Per the writing-skills Iron Law (an edit needs a failing test first):

- **RED:** give subagents a written snapshot + the current skill; confirm they finish
  at the CSV/markdown report and do **not** produce a colored `.xlsx` with a Legend
  tab.
- **GREEN:** with Step 10 added, confirm they run `export_xlsx.py` and produce the
  workbook. Report the split (e.g. 0/5 → 5/5) in the PR.

## Deliverables

- `scripts/export_xlsx.py` (new) — `read_snapshot` / `build_workbook` / `main` seams,
  lazy `openpyxl` import, shared `FILL_*` constants.
- `tests/test_export_xlsx.py` (new) — the coverage above.
- `SKILL.md` — Step 10, Output, and Bundled resources updates (additive), openpyxl
  prerequisite noted.
- `.gitignore` — add `reports/*.xlsx`.
- Isolated on branch `feat/esg-xlsx-legend-export` off `main`; its own PR.
