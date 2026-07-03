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
| Scope A/B/C/D (D = handprint) | **new** | `impact_scope` |
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
planetary_alignment, impact_scope, priority_internal, importance_external,
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
| `impact_scope` | `A` \| `B` \| `C` \| `D` | Impact scope (Lukas's A–D scoping). **A** = footprint, own operations; **B** = footprint, direct value chain (suppliers + use phase); **C** = footprint, broader/enabled system; **D** = **handprint** (positive contribution / avoided impact elsewhere). |
| `priority_internal` | `high` \| `low` | Strategic priority inside the company. [RQ1a.3] |
| `importance_external` | `high` \| `low` | External signaling importance to stakeholders/markets. [RQ1a.3] |
| `linked_targets` | free text | Which other (ESG) targets this connects to, and how. E.g. `netzero_target_year — shares Scope-3 boundary`. |
| `assessment_notes` | free text | Rationale for the judgment calls. **Required when any judgment column is set** (see D1). |

**Renamed from `sbt_scope` → `impact_scope`** (review finding): `sbt_scope` implied GHG-Protocol/SBTi Scope 1/2/3, but the A–D values are Lukas's own footprint/handprint scoping, NOT the GHG scopes. `impact_scope` drops the false SBTi implication while keeping the A/B/C/D encoding. Documenting the crosswalk (A≈own-ops footprint, B≈value-chain footprint, C≈system footprint, D=handprint) makes the call reproducible; blank `impact_scope` = **"not assessed"** (fine for non-climate circular targets).

`smart_measurable` and `smart_time_bound` are intentionally NOT added — `target_has_kpi` and `target_end_year` already carry them (the M and T of SMART). These are **factual** determinations (a KPI/deadline is present or not), so they are correctly excluded from the judgment set below and require no rationale.

**Uncertainty:** only `planetary_alignment` carries an explicit `unknown`, because whether a target is science-based is itself a research finding [RQ1a.2] worth recording distinctly from "not assessed." The binary columns (`substance`, `priority_internal`, `importance_external`, `smart_*`) have no `unknown` value by design — an analyst who cannot make the call leaves the field **blank** ("not assessed"). Blank ≠ a graded judgment; it simply carries no D1 rationale obligation.

## D1 — Provenance for judgment columns (Assessment + required rationale)

The v1 rule stands: a `found`/`target` row still needs `value` + `source_url` + `quote` for the target itself. The SMART+ judgment columns are an **assessment layer on top of** that already-sourced target — they do not each get their own source_url/quote.

Instead, a cross-field rule grounds them:

> **If any judgment column is non-empty, `assessment_notes` must be non-empty.**

Judgment columns (the set that triggers the rule):
```
smart_specific, smart_achievable, smart_relevant, substance,
planetary_alignment, impact_scope, priority_internal, importance_external
```
`linked_targets` is a factual cross-reference, not a judgment, so it does not trigger the rule.

**Empty semantics.** "Non-empty" and "empty" use the SAME `str(row.get(k, "")).strip()` test as every other check in `validate()`. A whitespace-only `assessment_notes` (`"  "`) counts as empty and still triggers the error, so the rationale requirement cannot be bypassed with blank space.

**Non–short-circuit.** `validate()` accumulates all errors (never returns early), so a row with an *invalid* judgment value AND empty notes surfaces BOTH errors (invalid-enum + missing-rationale), not one masked by the other.

**Actionable message.** The D1 error names the offending column(s), e.g.
`judgment field(s) ['substance', 'impact_scope'] set but assessment_notes empty (add rationale)` — matching v1's pattern of naming the exact field, so an analyst on a 29-column row is not left hunting.

This keeps opinion permitted but never ungrounded, and makes an assessment reproducible/auditable across analysts. `assessment_notes` is where weak/strong-sustainability reasoning and Global Circularity Protocol context live, since those were pruned as standalone columns. (Notes with no judgment column set is allowed and harmless — the rule is one-directional.)

## D2 — Architecture: fold into the snapshot (not a separate file)

The 10 columns are appended to the existing snapshot CSV. They are **intended for `status=target` rows** and are normally blank elsewhere — exactly how `target_end_year` / `target_has_kpi` / `target_status` already behave in v1. Enforcement is by convention, **consistent with v1**: like the v1 target-only columns, each v2 column is validated (enum + D1 rule) whenever it is present, regardless of `status`, and is not hard-guarded to `status=target`. (v1 already permits `target_status` on a `found` row — see `test_snapshot.py`; v2 stays consistent rather than adding a status guard for only the new columns.) This:

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
| `measurement` | Measurement & impact accounting | Metering, KPI systems, and material/impact accounting that quantify circular flows across the ladder. | R2, R4, R6, R8 |
| `traceability` | Traceability & product passports | Digital/material passports and chain-of-custody tracking that make specific loops auditable. | R3, R5, R8 |
| `procurement` | Circular procurement | Purchasing standards and supplier requirements that pull circular inputs (reused, refurbished, remanufactured, recycled) through the value chain. | R2, R3, R5, R6, R8 |

The three support sets are deliberately **distinct** so the ids are not interchangeable.

**Disambiguate `data_infrastructure` (description edit, backward-compatible).** The kept `data_infrastructure` enabler currently reads "Digital product passports, material traceability, IoT tracking, and the data systems that make loops auditable" — which now collides almost entirely with the new `traceability`. Narrow its description to broad digital systems only:

> `data_infrastructure` → **"Broad digital and data systems — ERP, IoT, analytics platforms, and enterprise data infrastructure — that underpin circular operations at scale."** (supports unchanged: R3, R4, R5, R8)

Editing only the *description* (not the id) does not break backward compat: existing rows keep the `data_infrastructure` id and still validate. SKILL.md guidance: *prefer `traceability` for passports / chain-of-custody, `measurement` for metering / impact accounting, and `data_infrastructure` only for broad digital-systems commitments.*

**Atomic edit.** The enabler id set lives in THREE places that must change together in one commit, or the suite reds:
1. `circular-economy-10rs.json` `enablers` array (source of truth),
2. `snapshot.py` `VALID_ENABLER`,
3. `tests/test_reference_data.py` `EXPECTED_ENABLERS`.
`test_enabler_ids_match_snapshot_validator` (JSON == `VALID_ENABLER`) and `test_enablers_present_and_well_formed` (JSON == `EXPECTED_ENABLERS`) enforce the three-way equality. The **SKILL.md disambiguation guidance is load-bearing, not optional** — distinct `supports` sets alone do not make `traceability`/`measurement`/`data_infrastructure` non-interchangeable (their descriptions still sit close); the "prefer traceability for passports, measurement for metering, data_infrastructure only for broad systems" tie-breaker must ship in the same commit as the enabler change.

## Component changes

### `scripts/snapshot.py`
- Extend `COLS` to the 29-column list above (append the 10 v2 columns in the documented order).
- Update the **module docstring** schema list (currently stale at the original 13 columns) to the full 29-column schema, so the file's own documentation matches `COLS`.
- Add enum constants: `VALID_SUBSTANCE` (`symbolic`/`substantive`), `VALID_PLANETARY` (`insufficient`/`pb_aligned`/`unknown`), `VALID_IMPACT_SCOPE` (`A`/`B`/`C`/`D`), `VALID_LEVEL` (`high`/`low`, shared by `priority_internal` AND `importance_external`). Reuse `VALID_HAS_KPI` (`yes`/`no`) for the three `smart_*` columns (add a one-line comment noting the set is intentionally shared across the `smart_*` and `target_has_kpi` columns). `VALID_LEVEL` is named for its values, not one field, since it validates two differently-named columns.
- Add per-column validation (fires only when non-empty — backward compat). Each new enum error **names its own column and lists the allowed values with exact casing**, following v1's `(use yes|no)` precedent — e.g. `invalid impact_scope 'a' (use A|B|C|D, uppercase)`, `invalid smart_specific 'Yes' (use yes|no)`, `invalid priority_internal 'High' (use high|low)`. (Enums stay case-sensitive, consistent with v1; the message must therefore state the casing, since `impact_scope` uppercase single letters are easy to get wrong.)
- Add the D1 cross-field rule: define `JUDGMENT_COLS` (the 8 judgment columns as a set — consistent with the existing non-`VALID_` collection names `SOURCED_STATUSES`/`REQUIRED`); using the same `.strip()` test as elsewhere, if any is non-empty and `assessment_notes` is empty, append the column-naming error above.
- No change to `_autofill_item_type` or the existing provenance rule.

### `scripts/diff.py`
- Add a **"Quality reassessed"** subsection inside the existing Target movements block. For target keys present in both snapshots, compare the **materiality fields** — `substance`, `planetary_alignment`, `priority_internal`, `importance_external` (a deliberate 4-field subset of D1's 8-column judgment set; the SMART/`impact_scope` letters are intentionally out of the diff for now — noted, not an oversight). List any that changed as `field: old -> new`.
- **Both-non-empty guard (backward-compat, prevents spurious churn).** Report `field: old -> new` ONLY when BOTH old and new are non-empty and differ. A first-time assessment against a pre-v2 baseline is NOT a reassessment; normalize absent/`None`/`""` as equal so a 29-col snapshot diffed against a 13/19-col baseline shows no spurious "reassessed" rows. **Normalize before truthiness**, not via `str()`: a genuine pre-v2 CSV read by `csv.DictReader` yields the v2 keys as **absent → `.get()` returns `None`**, and `str(None)` is the truthy `"None"` — so the guard must compare `(old or "").strip()` / `(new or "").strip()`, never `str(old).strip()`, or every first-time assessment against a real old snapshot leaks in as churn.
- **First-time assessments are surfaced, deliberately.** A target present in both snapshots whose materiality field went blank→value is a genuine event worth reporting; render it under a separate **"Newly assessed"** label, **never** under "reassessed." This is intended (it is not the spurious churn the guard above prevents — that churn was the *mislabeling* of a first assessment as a re-assessment). Add `newly_assessed` to the block render guard alongside `reassessed`. Out of scope (YAGNI): a value→blank *de-assessment* (analyst removes a prior judgment) is intentionally not surfaced — noted so its silence is a known choice, not a bug.
- **Render-guard fix.** The enclosing Target-movements block currently renders only under `if new_targets or changed_targets or dropped_targets or pairs:`. A target whose ONLY change is a materiality field (same `value`/`target_end_year`, no paired actual) satisfies none of those, so the block — and the new subsection — would never render. Compute the `reassessed` (and any `newly_assessed`) key set and ADD it to the block's render condition, so quality-only changes still surface.
- Small, additive; existing sections unchanged. Diff key unchanged.

### `circular-economy-10rs.json`
- Add the three enabler objects above to the `enablers` array (now 11).
- Narrow the `data_infrastructure` description per D3 (id and `supports` unchanged).

### `references/indicators.yaml`
- No change. Existing `r_hint`s stand. (The three new enablers are *enablers*, not indicators — they carry no `r_hint`, and `test_circular_indicators_have_valid_r_hint`'s `>= 8` check reads only the `circular:` indicator block and is unaffected.)

### `SKILL.md`
- Update the schema **code block** to 29 columns.
- Update the **prose immediately after** the block (currently "the last 6 are the classification layer … old 13-column snapshots still validate") to describe the new layout: "first 13 core / next 6 v1 classification / last 10 v2 SMART+."
- Add a **"Target quality (SMART+)"** section documenting each new column, the M/T reuse note, the `impact_scope` A–D crosswalk, the enabler reconciliation guidance, and the D1 rationale rule.
- Extend the report template with a **SMART+ scorecard** table (see below).

### `evals/evals.json`
- Update **every** `19-column` / `19 column` reference to `29`, not only the verbatim header:
  - eval 1: the verbatim header assertion ("exactly the 19-column tidy schema: entity,lei,…target_status.") → the full 29-column header ending `…importance_external,linked_targets,assessment_notes`, and the "19-column" descriptor → "29-column";
  - eval 2: "identical 19-column schema as eval 1" → "29-column";
  - eval 3: "The new snapshot uses the 19-column schema" → "29-column" (the key columns / no-spurious-churn wording stays).
- Extend eval 4 (`philips-target-status-2yr`): **also extend its prompt** so the SMART+ assertions are satisfiable — the current prompt only asks for R-strategy/KPI/deadline/target_status, giving the agent no reason to populate the SMART+ columns. Add a clause asking the analyst to also judge, per target, whether it reads symbolic vs substantive, whether it is science-based / planetary-aligned, and its internal/external priority (with rationale). Then the SMART+ assertions: at least one target row carries `substance` + `planetary_alignment` + a priority/importance value; the snapshot passes validation (so `assessment_notes` is present per the D1 rule).
- Add eval 5 `philips-smart-plus-scorecard` with **`"files": []`** (a fresh classification task like eval 4 — no fixture needed): prompt asks to classify Philips circular targets on the SMART+ dimensions; assert 29-column schema, judgment values drawn from the enums, the D1 rationale rule is satisfied, and the report contains a SMART+ scorecard section.

### `tests/`
- `test_snapshot.py`:
  - **Rewrite the existing `test_header_has_19_columns_in_order`** (it hard-asserts `len(COLS)==19` and `COLS[13:]==NEW_COLS`): rename to `test_header_has_29_columns_in_order`, assert `len(COLS)==29`, `COLS[13:19]` equals the 6 v1 columns, and `COLS[19:29]` equals the exact ordered v2 list (`smart_specific,…,assessment_notes`). Column order is load-bearing (verbatim eval header, CSV headers, DictWriter), so the v2 block order is asserted explicitly.
  - **One negative enum-rejection test per new column** (not per enum): invalid value rejected for each of `smart_specific`, `smart_achievable`, `smart_relevant`, `substance`, `planetary_alignment`, `impact_scope`, `priority_internal`, `importance_external` — proving each column is individually wired into `validate()` (shared constants `VALID_HAS_KPI`/`VALID_LEVEL` otherwise hide a forgotten column).
  - **D1 rule, parametrized over all 8 judgment columns**: each column individually, set with empty `assessment_notes`, must produce the rationale error; with notes, passes. Plus a **negative**: `linked_targets` set alone (empty notes) must validate clean. Plus a whitespace-only `assessment_notes` case that still triggers. Build all D1 tests on a known-valid `status=target` row (like `_base()` with target provenance) varying only the fields under test, so provenance errors never mask the D1 result.
  - **Full v2 happy-path**: a `status=target` row with all 10 v2 columns set to valid enum values + `assessment_notes` → `validate() == []` (co-validation of the whole SMART+ block).
  - **Non–short-circuit accumulation**: a row with an INVALID judgment value (e.g. `substance="maybe"`) AND empty `assessment_notes` returns BOTH errors (invalid-enum AND D1 rationale), pinning that `validate()` never returns early.
  - **One-directional D1 (harmless case)**: `assessment_notes` set while all 8 judgment columns are blank → `validate() == []` (rationale with nothing to justify is allowed).
  - **Backward-compat**: a bare v1 (19-col) and core (13-col) row still validate (`== []`).
- `test_diff.py`:
  - A test that a changed materiality field renders under "Quality reassessed" — covering **all four** fields (`substance`, `planetary_alignment`, `priority_internal`, `importance_external`) independently, not just two.
  - A **quality-only-change** test: two 29-col snapshots where a target's only change is a materiality field (value/end-year unchanged) — assert the Target-movements block AND the "Quality reassessed" subsection still render (guards the `reassessed` render-guard fix).
  - A **"Newly assessed" test** (guards the `newly_assessed` render branch, which is otherwise untested): a target present in BOTH snapshots whose materiality field went blank→value (no value/end-year change, no paired actual) — assert it renders under "Newly assessed" AND is absent from "Quality reassessed." Without this, the whole `newly_assessed` branch could ship unexercised.
  - A **backward-compat no-churn** test that exercises the real absent-key path: `test_diff.py::_write` currently hardcodes `fieldnames=snapshot.COLS`, which becomes 29 cols after migration and so can only emit blank v2 *cells* (`""`), not a genuine pre-v2 header (absent keys → `None`). Give `_write` an optional `cols=` parameter (or hand-write a raw 19-column-header CSV) so the baseline physically omits the v2 columns; then assert no spurious "Quality reassessed" rows. This is the case that catches a `str(None)`-based guard bug (see the diff.py note above).
- `test_reference_data.py`:
  - Update `EXPECTED_ENABLERS` to the 11-id set; keep the JSON↔`VALID_ENABLER` equality test.
  - Update the verbatim header constant to 29 columns; **rename** `test_evals_reference_19_column_schema` → `…_29_column_schema`; extend its negative guard to also assert `"19-column"`/`"19 column"` are absent (mirroring the existing `"13-column"` guard — this is the only automated net that catches stale eval prose); assert `5 in ids` (eval 5 added).
  - Extend `test_skill_documents_new_columns`: add the **10 new column names** to the column-presence loop and the new section name `"Target quality (SMART+)"` to the sections list.

## Report template addition

```markdown
## Target quality (SMART+)
| indicator | S | A | R | substance | planetary | impact_scope | int.pri | ext.imp | notes |
|---|---|---|---|---|---|---|---|---|---|
(one row per status=target; S/A/R from smart_*; impact_scope from impact_scope; notes = assessment_notes)
```

Placed after the existing "Target scorecard" section. **M and T of SMART are not repeated here** — they are shown by the Target scorecard's `has KPI` and `end year` columns; add a one-line note under the SMART+ table pointing readers there for the M and T letters.

## Backward compatibility

- All 10 columns optional; validation fires only when a field is non-empty → existing 13-column and 19-column snapshots validate unchanged (exit 0). Old rows carry no judgment columns, so D1 never fires on them.
- Diff key `(entity, indicator, period)` unchanged → a 29-column snapshot diffs cleanly against a 13/19-column baseline with no spurious churn: the value/target-movement sections key on shared columns, and the new "Quality reassessed" subsection reports only when BOTH old and new materiality fields are non-empty (so first-time assessment against a pre-v2 baseline is not miscounted as a reassessment).
- Enabler set is a superset → prior `data_infrastructure` rows remain valid (only its description text changes).

## Out of scope (YAGNI)

- Standalone columns for weak/strong sustainability and Global Circularity Protocol (→ `assessment_notes` / `linked_targets`).
- Auto-scoring of overall SMART completeness (report-derived if wanted later, not stored).
- Any scorecard UI/HTML rendering.
- Retiring `data_infrastructure` (kept for backward compat; only its description is narrowed).
- `smart_measurable` / `smart_time_bound` columns (reuse `target_has_kpi` / `target_end_year`).
- A hard `status=target` guard on the v2 columns (consistent with v1's unguarded target-only columns; enforcement stays by convention).
- Diffing the SMART/`impact_scope` fields (the "Quality reassessed" subsection covers the 4 materiality fields only for now).
