# ESG SMART+ Target-Quality Extension — Design Spec

**Date:** 2026-07-02
**Skill:** `esg-longitudinal`
**Builds on:** v1 circular-economy classification layer (PR #14 — 19-column schema, 6 classification columns).
**Origin:** Colleague Lukas's per-target SMART+ rubric (Specific / Measurable / Achievable / Relevant / Time-bound + symbolic-vs-substantive, science-based/planetary-aligned, internal priority, external importance, linkage). Tagged in his table with research questions [RQ1a.2], [RQ1a.3].

## Goal

Enrich `status=target` rows with a SMART+ quality/materiality assessment so targets can be compared on *how good the commitment is*, not only its value — while preserving the tidy longitudinal schema and the provenance discipline.

## Scope stance

**Adapt with YAGNI.** Lukas's table is a menu, not a contract. Attributes already covered by v1 are reused (not duplicated); genuinely new research variables become columns; theoretical/niche attributes fold into a free-text rationale rather than their own column.

## Attribute crosswalk (Lukas → this skill)

| Lukas attribute | Decision | Column |
|---|---|---|
| Target (text) | reuse | the row itself (`status=target`, `value`) |
| Measurable (numeric target) | reuse | `target_has_kpi` |
| Time-bound (deadline) | reuse | `target_end_year` |
| On track (Y/N, how) [RQ1a.3] | reuse | `target_status` + `assessment_notes` |
| Re-defined over time (Y/N, how) | reuse | `target_status=changed` + `assessment_notes` |
| R-ladder (R0–9) | reuse | `r_strategy` |
| System enablers | reconcile | `enabler_topic` (enabler set extended — see below) |
| Specific (Y/N) | **new** | `smart_specific` |
| Achievable (Y/N) | **new** | `smart_achievable` |
| Relevant (Y/N) | **new** | `smart_relevant` |
| Symbolic or substantive | **new** | `substance` |
| Science-based / planetary-aligned [RQ1a.2] | **new** | `planetary_alignment` |
| Scope A/B/C/D (D = handprint) | **new** | `sbt_scope` |
| Internal priority (strategic) [RQ1a.3] | **new** | `priority_internal` |
| External importance (signaling) [RQ1a.3] | **new** | `importance_external` |
| Link to other ESG targets (which & how) | **new** | `linked_targets` |
| (rationale behind every judgment) | **new** | `assessment_notes` |
| Weak/strong sustainability | fold → notes | captured in `assessment_notes` |
| Global Circularity Protocol | fold → notes/link | captured in `assessment_notes` / `linked_targets` |

Result: **10 new columns**, schema **19 → 29**.

## Schema (29 columns)

```
entity, lei, domain, indicator, value, unit, period, status,
source, source_url, page, quote, retrieved_at,
item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status,
smart_specific, smart_achievable, smart_relevant, substance,
planetary_alignment, sbt_scope, priority_internal, importance_external,
linked_targets, assessment_notes
```

First 13 = original core. Next 6 = v1 classification. Last 10 = v2 SMART+ block. **Every v2 column is optional** and validated only when non-empty, so 13-column and 19-column snapshots still pass validation unchanged. The diff key `(entity, indicator, period)` is untouched.

### New column definitions & enums

| Column | Type / enum | Meaning |
|---|---|---|
| `smart_specific` | `yes` \| `no` | Target scope/boundary clearly defined (what, where, whose). |
| `smart_achievable` | `yes` \| `no` | Plausible given trajectory, resources, sector — analyst assessment. |
| `smart_relevant` | `yes` \| `no` | Material to the business and to the impact it claims to address. |
| `substance` | `symbolic` \| `substantive` | Real operational commitment vs signaling/PR. |
| `planetary_alignment` | `insufficient` \| `pb_aligned` \| `unknown` | Whether the target is aligned to a planetary boundary / science-based pathway. [RQ1a.2] |
| `sbt_scope` | `A` \| `B` \| `C` \| `D` | Impact scope. A/B/C = footprint (own ops → value chain), **D = handprint** (positive contribution / avoided impact elsewhere). |
| `priority_internal` | `high` \| `low` | Strategic priority inside the company. [RQ1a.3] |
| `importance_external` | `high` \| `low` | External signaling importance to stakeholders/markets. [RQ1a.3] |
| `linked_targets` | free text | Which other (ESG) targets this connects to, and how. E.g. `netzero_target_year — shares Scope-3 boundary`. |
| `assessment_notes` | free text | Rationale for the judgment calls. **Required when any judgment column is set** (see D1). |

`smart_measurable` and `smart_time_bound` are intentionally NOT added — `target_has_kpi` and `target_end_year` already carry them; documented as the M and T of SMART.

## D1 — Provenance for judgment columns (Assessment + required rationale)

The v1 rule stands: a `found`/`target` row still needs `value` + `source_url` + `quote` for the target itself. The SMART+ judgment columns are an **assessment layer on top of** that already-sourced target — they do not each get their own source_url/quote.

Instead, a cross-field rule grounds them:

> **If any judgment column is non-empty, `assessment_notes` must be non-empty.**

Judgment columns (the set that triggers the rule):
```
smart_specific, smart_achievable, smart_relevant, substance,
planetary_alignment, sbt_scope, priority_internal, importance_external
```
`linked_targets` is a factual cross-reference, not a judgment, so it does not trigger the rule.

This keeps opinion permitted but never ungrounded, and makes an assessment reproducible/auditable across analysts. `assessment_notes` is where weak/strong-sustainability reasoning and Global Circularity Protocol context live, since those were pruned as standalone columns.

## D2 — Architecture: fold into the snapshot (not a separate file)

The 10 columns are appended to the existing snapshot CSV. They populate only on `status=target` rows (blank elsewhere) — exactly how `target_end_year` / `target_has_kpi` / `target_status` already behave in v1. This:

- preserves the "one tidy schema, many domains" design principle;
- reuses `snapshot.py` validation, `diff.py`, and the Target-movements section with no new file/join;
- keeps a target represented in exactly one place.

Accepted cost: the schema is wider (29 columns), mostly blank on `found`/`not_found` rows. That sparsity is already an accepted property of the target-only v1 columns.

## D3 — Enabler reconciliation (non-breaking superset, 11 ids)

Lukas's enabler set = `finance, procurement, measurement, traceability, training`. v1 has 8. Union them, keeping every existing id valid so no existing data breaks:

```
ecodesign, rnd, data_infrastructure, measurement, traceability,
procurement, training, partnerships, reverse_logistics, finance, policy
```

Three new ids added to `circular-economy-10rs.json` `enablers`:

| id | name | description | supports |
|---|---|---|---|
| `measurement` | Measurement & impact accounting | Metering, KPI systems, and material/impact accounting that quantify circularity flows. | R2, R8 |
| `traceability` | Traceability & product passports | Digital/material passports and chain-of-custody tracking that make loops auditable. | R3, R5, R8 |
| `procurement` | Circular procurement | Purchasing standards and supplier requirements that pull circular inputs through the value chain. | R2, R8 |

`data_infrastructure` stays valid; SKILL.md guidance: *prefer `measurement` / `traceability` when the commitment is specifically about metering or chain-of-custody; use `data_infrastructure` for broader digital-systems commitments.* Backward-compatible: any v1 row using `data_infrastructure` still validates.

## Component changes

### `scripts/snapshot.py`
- Extend `COLS` to the 29-column list above.
- Add enum constants: `VALID_SUBSTANCE`, `VALID_PLANETARY`, `VALID_SBT_SCOPE`, `VALID_PRIORITY` (used by both `priority_internal` and `importance_external`); reuse `VALID_HAS_KPI` (`yes`/`no`) for the three `smart_*` columns.
- Extend `VALID_ENABLER` to the 11-id set.
- Add per-column validation (fires only when non-empty — backward compat).
- Add the D1 cross-field rule: define `JUDGMENT_COLS`; if any is non-empty and `assessment_notes` is empty, append error `"judgment fields set but assessment_notes empty (rationale required)"`.
- No change to `_autofill_item_type` or the existing provenance rule.

### `scripts/diff.py`
- Add a **"Quality reassessed"** subsection inside the existing Target movements block. For target keys present in both snapshots, compare the judgment fields (`substance`, `planetary_alignment`, `priority_internal`, `importance_external`); list any that changed as `field: old -> new`. Small, additive; existing sections unchanged. Diff key unchanged.

### `circular-economy-10rs.json`
- Add the three enabler objects above to the `enablers` array (now 11).

### `references/indicators.yaml`
- No change. Existing `r_hint`s stand.

### `SKILL.md`
- Update the schema block to 29 columns.
- Add a **"Target quality (SMART+)"** section documenting each new column, the M/T reuse note, the enabler reconciliation guidance, and the D1 rationale rule.
- Extend the report template with a **SMART+ scorecard** table (see below).

### `evals/evals.json`
- Update the verbatim schema-header assertion(s) to the 29-column header.
- Extend eval 4 (`philips-target-status-2yr`) with SMART+ assertions: at least one target row carries `substance` + `planetary_alignment` + a priority/importance value; the snapshot passes validation (so `assessment_notes` is present per the D1 rule).
- Add eval 5 `philips-smart-plus-scorecard`: prompt asks to classify Philips circular targets on the SMART+ dimensions; assert 29-column schema, judgment values drawn from the enums, the D1 rationale rule is satisfied, and the report contains a SMART+ scorecard section.

### `tests/`
- `test_snapshot.py`: enum-validation tests for the new columns; a test that a judgment value with empty `assessment_notes` fails and with notes passes; a test that a bare v1 (19-col) / core (13-col) row still validates.
- `test_diff.py`: a test that a changed `substance`/`priority_internal` across two snapshots appears under "Quality reassessed".
- `test_reference_data.py`: update `EXPECTED_ENABLERS` to the 11-id set; keep the JSON↔`VALID_ENABLER` equality test; update the evals header assertion to 29 columns; extend the SKILL.md-sections test with the new section name.

## Report template addition

```markdown
## Target quality (SMART+)
| indicator | S | A | R | substance | planetary | scope | int.pri | ext.imp | notes |
|---|---|---|---|---|---|---|---|---|---|
(one row per status=target; S/A/R from smart_*; scope = sbt_scope; notes = assessment_notes)
```

Placed after the existing "Target scorecard" section. (M and T of SMART are already shown by the Target scorecard's `has KPI` and `end year` columns.)

## Backward compatibility

- All 10 columns optional; validation fires only when a field is non-empty → existing 13-column and 19-column snapshots validate unchanged (exit 0).
- Diff key `(entity, indicator, period)` unchanged → a 29-column snapshot diffs cleanly against a 13/19-column baseline with no spurious churn.
- Enabler set is a superset → prior `data_infrastructure` rows remain valid.

## Out of scope (YAGNI)

- Standalone columns for weak/strong sustainability and Global Circularity Protocol (→ `assessment_notes` / `linked_targets`).
- Auto-scoring of overall SMART completeness (report-derived if wanted later, not stored).
- Any scorecard UI/HTML rendering.
- Retiring `data_infrastructure` (kept for backward compat).
- `smart_measurable` / `smart_time_bound` columns (reuse `target_has_kpi` / `target_end_year`).
```
