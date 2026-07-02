# ESG Longitudinal — Circular-economy classification layer

**Date:** 2026-07-02
**Skill:** `esg-longitudinal`
**Status:** Design (awaiting user spec-review)

## Provisional decisions

The user was away during brainstorming; these were chosen from the recommended
defaults and are marked provisional until confirmed at the spec-review gate:

- **Architecture:** add columns to the tidy snapshot schema (not reference-only).
  Rationale: the year-over-year target tracking (achieved / delayed / dropped)
  must be machine-checkable and diffable — narrative prose cannot deliver that.
- **Scope:** `r_strategy` / `enabler_topic` are circular-specific; the
  `item_type` and `target_*` columns are domain-agnostic (a climate net-zero
  target gets `item_type=target`, `target_end_year`, `target_status` too).

## Problem

Today the skill captures ESG values as tidy rows but classifies them only by
`indicator` and `status` (found | not_found | target). For circular economy the
user needs richer, queryable classification:

1. Which of the 10 R-strategies (R0–R9) a commitment advances — and, for
   commitments that are *enablers* rather than R-strategies (training, data
   infrastructure, R&D, …), which enabler.
2. Whether a row is a measured metric/KPI or a forward-looking target.
3. Target completeness: does the target carry a KPI value? an end year? neither,
   one, or both?
4. Year-over-year: once more than one year is in view, whether the prior year's
   target was achieved, delayed, changed, failed, or dropped.

## Design

### New columns (tidy schema 13 → 19)

Appended after `retrieved_at`. Column order is irrelevant to `diff.py` (keyed by
name), so appending is safe for backward compatibility.

| column | values | applies to |
|---|---|---|
| `item_type` | `kpi` \| `target` \| `qualitative` | every disclosed row |
| `r_strategy` | `R0`…`R9`, pipe-separated with primary first (e.g. `R2\|R8`) \| blank | circular rows |
| `enabler_topic` | one of the enabler taxonomy ids \| blank | enabler rows |
| `target_end_year` | four-digit year \| blank | target rows |
| `target_has_kpi` | `yes` \| `no` \| blank | target rows |
| `target_status` | `on_track`\|`achieved`\|`delayed`\|`changed`\|`failed`\|`dropped`\|`too_early` \| blank | target rows |

`r_strategy` and `enabler_topic` are independent: a row may carry one, the other,
both (an enabler that also advances a specific R), or neither (non-circular rows).

Full 19-column header:
```
entity, lei, domain, indicator, value, unit, period, status, source,
source_url, page, quote, retrieved_at, item_type, r_strategy,
enabler_topic, target_end_year, target_has_kpi, target_status
```

### Derived, report-only (not stored)

`target_completeness` = `both` \| `kpi_only` \| `year_only` \| `none`, from
(`target_has_kpi`, `target_end_year`). This is the user's "kpi? end year? none or
both?" dimension. Kept derived to avoid a redundant stored column that could drift
out of sync with its two inputs.

### How the four asks map

| ask | mechanism |
|---|---|
| 10R + enablers | `r_strategy` + `enabler_topic`; enabler taxonomy in `circular-economy-10rs.json` |
| metric vs target | `item_type` (complements existing `status`) |
| target completeness | `target_end_year` + `target_has_kpi` → derived `target_completeness` |
| year-over-year status | `target_status` (agent-recorded from report text) **plus** automated `diff.py` "Target movements" |

Why both for year-over-year: the agent records `target_status` from what the
report *says* ("we achieved our 2020 goal"; "we now target 2030"). `diff.py`
independently compares target rows across snapshots to catch **silent** drops and
changes the report does not admit. The two cross-check each other.

### Enabler taxonomy (added to `circular-economy-10rs.json`)

New top-level `enablers` array. Each entry: `{id, name, description, supports:[R ids]}`.

| id | name | supports |
|---|---|---|
| `ecodesign` | Design for circularity (durability, modularity, recyclability, disassembly) | R2,R3,R4,R5,R8 |
| `rnd` | R&D / innovation in circular materials, processes, technology | R2,R5,R6,R8,R9 |
| `data_infrastructure` | Digital product passports, material traceability, IoT, data systems | R3,R4,R5,R8 |
| `training` | Workforce skills & capability for repair/refurbish/circular ops | R4,R5,R6 |
| `partnerships` | Value-chain collaboration, take-back networks, consortia, customer engagement | R1,R3,R7,R8 |
| `reverse_logistics` | Collection, take-back, sorting infrastructure | R3,R4,R5,R6,R8 |
| `finance` | Circular business-model financing, PaaS/leasing, green capex | R1,R2 |
| `policy` | Policy advocacy, standards & regulatory alignment (EPR, right-to-repair) | R0,R4,R8 |

## File changes

### `circular-economy-10rs.json`
Add the `enablers` array above. Existing `categories`/`strategies` untouched.

### `scripts/snapshot.py`
- Extend `COLS` with the six new columns (appended).
- Add enum validation, applied **only when the field is non-empty**:
  - `item_type` ∈ {kpi, target, qualitative}
  - each pipe-token of `r_strategy` ∈ {R0…R9}
  - `enabler_topic` ∈ enabler ids
  - `target_has_kpi` ∈ {yes, no}
  - `target_status` ∈ {on_track, achieved, delayed, changed, failed, dropped, too_early}
  - `target_end_year` matches `^\d{4}$`
- Auto-fill convenience: if `item_type` empty, derive from `status`
  (`found`→`kpi`, `target`→`target`, `not_found`→leave blank).
- **No new required fields.** Old 13-column rows and existing evals still pass.
  Keep the existing `found|target ⇒ value+source_url+quote` rule unchanged.

### `scripts/diff.py`
- Keep `KEY = (entity, indicator, period)` and the existing New/Changed/Dropped
  sections unchanged (eval-3 depends on them).
- Add a **"Target movements"** section:
  - Build old/new target maps keyed `(entity, indicator)` from `status=target`
    rows (latest `period` wins if several).
  - **New targets:** key in new, absent in old.
  - **Changed targets:** same key, `target_end_year` or `value` differs
    (e.g. "moved 2025 → 2030", "value 25 → 30").
  - **Dropped targets:** key in old, absent in new (flag: check achieved vs abandoned).
  - **Target vs actual** table: pair each active target with the latest `found`
    actual for the same `(entity, indicator)`, show both values and numeric delta;
    surface the row's `target_status` when present. Do **not** auto-label
    achieved/failed (higher-better vs lower-better is indicator-dependent) — the
    agent's recorded `target_status` is the source of truth; the diff shows the raw
    comparison.
- Tolerate snapshots lacking the new columns (`.get(col, "")`).

### `SKILL.md`
- Update the tidy-schema block to the 19 columns; explain each new field.
- Add a **"Classification layer"** section: R-strategy assignment (reference
  `circular-economy-10rs.json`), the enabler taxonomy, and that a row is an
  R-strategy item or an enabler item (or both).
- Add a **"Target anatomy"** section: `item_type`, `target_end_year`,
  `target_has_kpi`, derived `target_completeness`.
- Add a **"Year-over-year target status"** section: the `target_status` values,
  how the agent picks them from report text, and how `diff.py` cross-checks.
- Update the report template: add R-strategy to the time-series table; add a
  **Target scorecard** table (indicator | target value | end_year | has_kpi |
  completeness | status); add an **Enablers** list; add **Target movements since
  last snapshot** fed by `diff.py`.

### `references/indicators.yaml`
- Add an `r_hint` to each circular indicator to guide assignment:
  `circular_revenue_pct`→R1\|R2, `recycled_input_pct`→R8, `waste_recycled_pct`→R8,
  `waste_to_landfill_pct`→R8\|R9, `total_waste`→R2, `product_takeback_scope`→R3\|R6,
  `closed_loop_target_year`→R8, `material_circularity_rate`→R2\|R8.
- Header note pointing to the new classification columns.

### `evals/evals.json`
- Rewrite the three "exactly 13-column / no added columns" assertions
  (eval 1, 2, 3) to the 19-column header.
- Add classification assertions: target rows carry `item_type=target` and a
  `target_end_year` where the report states a deadline; circular found/target
  rows carry an `r_strategy` or `enabler_topic`.
- Optional new **eval-4**: two-year circular run that exercises `target_status`
  (a prior target shown as achieved/changed/dropped in the later year).

## Backward compatibility

- Old 13-column snapshots load fine; missing columns default blank.
- `diff.py` KEY unchanged → eval-3's re-run diff against the 2025-06-29 13-column
  baseline still aligns; no spurious churn.
- New columns are additive and optional; no existing row becomes invalid.

## Testing

- `snapshot.py`: rows with valid/invalid enum values (invalid rejected only when
  non-empty); old 13-col rows still pass; `item_type` auto-fill from `status`.
- `diff.py`: two snapshots with target changes/drops → "Target movements"
  renders; 13-col old vs 19-col new still diffs on shared KEY.
- Evals 1–3 pass against the new header; new eval-4 (if added) exercises
  `target_status` across two years.

## Out of scope (YAGNI)

- No stored `target_completeness` column (derived in report).
- No auto achieved/failed labelling in `diff.py` (direction-dependent).
- No R-strategy/enabler columns for non-circular domains beyond leaving them blank.
- No changes to find/fetch/extract scripts.
