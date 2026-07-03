# ESG SMART+ Target-Quality Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `esg-longitudinal` snapshot schema from 19 to 29 columns with a v2 SMART+ target-quality assessment block, 3 new circular-economy enablers, and a diff "Quality reassessed"/"Newly assessed" subsection.

**Architecture:** Additive, backward-compatible schema extension. Ten new optional columns validated only when non-empty (so 13-col and 19-col snapshots still pass). One cross-field rule (D1: any judgment column set ⇒ `assessment_notes` required). Diff gains a materiality-drift subsection that never spuriously churns against a pre-v2 baseline. All work is in Python stdlib (csv, json, re) + pytest; no new dependencies.

**Tech Stack:** Python 3 (stdlib only), pytest, JSON/YAML/CSV reference data, markdown docs.

## Global Constraints

- **Schema order is load-bearing.** `COLS` in `scripts/snapshot.py` is the source of truth; the verbatim header string in `evals/evals.json`, the CSV `DictWriter`, and the header test must all match it exactly. Final 29-col order: `entity, lei, domain, indicator, value, unit, period, status, source, source_url, page, quote, retrieved_at, item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status, smart_specific, smart_achievable, smart_relevant, substance, planetary_alignment, impact_scope, priority_internal, importance_external, linked_targets, assessment_notes`.
- **Every new column is optional** and validated ONLY when `str(row.get(col,"")).strip()` is non-empty. Never add a column to `REQUIRED`.
- **Enum values are exact-match, case-sensitive** (consistent with v1). Error messages must name the column and list allowed values with casing.
- **Empty test everywhere is `str(row.get(k,"")).strip()`** — including the D1 rule (whitespace-only `assessment_notes` counts as empty).
- **Enabler id set lives in THREE files that must change in one commit:** `circular-economy-10rs.json` `enablers`, `snapshot.py` `VALID_ENABLER`, `tests/test_reference_data.py` `EXPECTED_ENABLERS`.
- **Provenance rule unchanged:** a `found`/`target` row still requires `value` + `source_url` + `quote`.
- Run tests with `python -m pytest esg-longitudinal/tests/<file> -v` from the repo root (`C:\Users\kmorg\claude-skills`). The `tests/conftest.py::_load` helper loads the scripts by path; `from conftest import _load` already works under pytest's default import mode.
- Commit on the current branch `feat/esg-smart-plus-target-quality-design` (a stacked branch off `esg-circular-classification`).

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `esg-longitudinal/scripts/snapshot.py` | Schema (`COLS`), enum constants, `validate()`, D1 rule | 1, 2 |
| `esg-longitudinal/scripts/diff.py` | Materiality reassessment subsection | 3 |
| `esg-longitudinal/circular-economy-10rs.json` | Enabler taxonomy (3 new + narrow `data_infrastructure`) | 2 |
| `esg-longitudinal/SKILL.md` | Human docs: schema block, SMART+ section, report scorecard | 4 |
| `esg-longitudinal/evals/evals.json` | Eval assertions (29-col header, eval 4 extend, eval 5) | 5 |
| `esg-longitudinal/tests/test_snapshot.py` | Validator tests | 1 |
| `esg-longitudinal/tests/test_diff.py` | Diff tests | 3 |
| `esg-longitudinal/tests/test_reference_data.py` | Enabler + eval + SKILL.md doc tests | 2, 4, 5 |

Task order: **1 (schema/validator) → 2 (enablers) → 3 (diff) → 4 (docs) → 5 (evals)**. Task 1 is first because extending `COLS` breaks the existing header test, which Task 1 rewrites in the same commit.

---

### Task 1: Schema extension + SMART+ validators + D1 rule

**Files:**
- Modify: `esg-longitudinal/scripts/snapshot.py` (COLS `:30-33`, module docstring `:8-16`, enum constants `:39-46`, `validate()` `:61-100`)
- Test: `esg-longitudinal/tests/test_snapshot.py`

**Interfaces:**
- Produces: `snapshot.COLS` (29-element list), `snapshot.VALID_SUBSTANCE`, `snapshot.VALID_PLANETARY`, `snapshot.VALID_IMPACT_SCOPE`, `snapshot.VALID_LEVEL` (sets), `snapshot.JUDGMENT_COLS` (list of 8 column names), and `snapshot.validate(row) -> list[str]` extended with the v2 checks. Later tasks rely on `COLS` being 29-long and ordered as in Global Constraints.

- [ ] **Step 1: Rewrite the header test + add helpers/imports (test)**

In `esg-longitudinal/tests/test_snapshot.py`, add `import pytest` at the top. Replace the existing `NEW_COLS` constant (lines 5-6) and `test_header_has_19_columns_in_order` (lines 18-23) with:

```python
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


def test_header_has_29_columns_in_order():
    assert snapshot.COLS[:13] == [
        "entity", "lei", "domain", "indicator", "value", "unit", "period",
        "status", "source", "source_url", "page", "quote", "retrieved_at"]
    assert snapshot.COLS[13:19] == V1_COLS
    assert snapshot.COLS[19:29] == V2_COLS
    assert len(snapshot.COLS) == 29
```

- [ ] **Step 2: Run the header test to verify it fails**

Run: `python -m pytest esg-longitudinal/tests/test_snapshot.py::test_header_has_29_columns_in_order -v`
Expected: FAIL (`snapshot.COLS` is currently 19 long; `COLS[19:29]` is empty).

- [ ] **Step 3: Extend COLS + module docstring (implementation)**

In `esg-longitudinal/scripts/snapshot.py`, replace the `COLS` definition (lines 30-33) with:

```python
COLS = ["entity", "lei", "domain", "indicator", "value", "unit", "period", "status",
        "source", "source_url", "page", "quote", "retrieved_at",
        "item_type", "r_strategy", "enabler_topic",
        "target_end_year", "target_has_kpi", "target_status",
        "smart_specific", "smart_achievable", "smart_relevant", "substance",
        "planetary_alignment", "impact_scope", "priority_internal", "importance_external",
        "linked_targets", "assessment_notes"]
```

In the module docstring, replace the schema block (lines 9-11, the `Schema ... entity, ... retrieved_at` lines) with the full 29-column list so the file's own docs match `COLS`:

```
Schema (one row per company-indicator-period):
    entity, lei, domain, indicator, value, unit, period, status,
    source, source_url, page, quote, retrieved_at,
    item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status,
    smart_specific, smart_achievable, smart_relevant, substance,
    planetary_alignment, impact_scope, priority_internal, importance_external,
    linked_targets, assessment_notes
```

- [ ] **Step 4: Run the header test to verify it passes**

Run: `python -m pytest esg-longitudinal/tests/test_snapshot.py::test_header_has_29_columns_in_order -v`
Expected: PASS.

- [ ] **Step 5: Add the v2 validator tests (test)**

Append to `esg-longitudinal/tests/test_snapshot.py`:

```python
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
    # validate() must accumulate, not short-circuit
    errs = snapshot.validate(_target(substance="maybe"))
    assert any("substance" in e for e in errs)
    assert any("assessment_notes" in e for e in errs)


def test_notes_without_judgment_is_valid():
    assert snapshot.validate(_target(assessment_notes="context only, no judgment set")) == []
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `python -m pytest esg-longitudinal/tests/test_snapshot.py -k "v2 or judgment or linked_targets or whitespace_notes or notes_without" -v`
Expected: FAIL (the v2 enum constants and D1 rule do not exist yet; e.g. rows with unknown enum values currently validate clean, so the "rejected" assertions fail).

- [ ] **Step 7: Add enum constants + D1 rule (implementation)**

In `esg-longitudinal/scripts/snapshot.py`, after the `VALID_TARGET_STATUS` definition (line 45-46), add:

```python
# v2 SMART+ enums — validated ONLY when the field is non-empty (backward compat).
VALID_SUBSTANCE = {"symbolic", "substantive"}
VALID_PLANETARY = {"insufficient", "pb_aligned", "unknown"}
VALID_IMPACT_SCOPE = {"A", "B", "C", "D"}
VALID_LEVEL = {"high", "low"}  # named for its values: shared by priority_internal AND importance_external
# VALID_HAS_KPI {"yes","no"} is reused for the three smart_* columns (shared yes/no set).

# The 8 judgment columns that require a rationale in assessment_notes (D1).
JUDGMENT_COLS = ["smart_specific", "smart_achievable", "smart_relevant", "substance",
                 "planetary_alignment", "impact_scope", "priority_internal",
                 "importance_external"]
```

In `validate()`, immediately before `return errs` (line 100), add:

```python
    for col in ("smart_specific", "smart_achievable", "smart_relevant"):
        v = str(row.get(col, "")).strip()
        if v and v not in VALID_HAS_KPI:
            errs.append(f"invalid {col} '{v}' (use yes|no)")

    sub = str(row.get("substance", "")).strip()
    if sub and sub not in VALID_SUBSTANCE:
        errs.append(f"invalid substance '{sub}' (use symbolic|substantive)")

    pa = str(row.get("planetary_alignment", "")).strip()
    if pa and pa not in VALID_PLANETARY:
        errs.append(f"invalid planetary_alignment '{pa}' "
                    "(use insufficient|pb_aligned|unknown)")

    isc = str(row.get("impact_scope", "")).strip()
    if isc and isc not in VALID_IMPACT_SCOPE:
        errs.append(f"invalid impact_scope '{isc}' (use A|B|C|D, uppercase)")

    for col in ("priority_internal", "importance_external"):
        v = str(row.get(col, "")).strip()
        if v and v not in VALID_LEVEL:
            errs.append(f"invalid {col} '{v}' (use high|low)")

    set_judgments = [c for c in JUDGMENT_COLS if str(row.get(c, "")).strip()]
    if set_judgments and not str(row.get("assessment_notes", "")).strip():
        errs.append(f"judgment field(s) {sorted(set_judgments)} set but "
                    "assessment_notes empty (add rationale)")
```

- [ ] **Step 8: Run the full snapshot test file to verify it passes**

Run: `python -m pytest esg-longitudinal/tests/test_snapshot.py -v`
Expected: PASS (all existing v1 tests + the new v2 tests). Existing `test_backward_compatible_13col_row_passes` and `test_valid_classification_row_passes` are the 13-col and 19-col backward-compat coverage and must still pass unchanged.

- [ ] **Step 9: Commit**

```bash
git add esg-longitudinal/scripts/snapshot.py esg-longitudinal/tests/test_snapshot.py
git commit -m "feat: add SMART+ v2 columns, enums, and D1 rationale rule to snapshot schema"
```

---

### Task 2: Enabler taxonomy — 3 new ids + narrow `data_infrastructure`

**Files:**
- Modify: `esg-longitudinal/circular-economy-10rs.json` (`enablers` array `:81-106`)
- Modify: `esg-longitudinal/scripts/snapshot.py` (`VALID_ENABLER` `:42-43`)
- Test: `esg-longitudinal/tests/test_reference_data.py` (`EXPECTED_ENABLERS` `:9-10`)

**Interfaces:**
- Produces: `VALID_ENABLER` (11-id set) == JSON `enablers` ids == `EXPECTED_ENABLERS`. The two equality tests `test_enablers_present_and_well_formed` and `test_enabler_ids_match_snapshot_validator` enforce this three-way equality.

- [ ] **Step 1: Update `EXPECTED_ENABLERS` to 11 ids (test)**

In `esg-longitudinal/tests/test_reference_data.py`, replace `EXPECTED_ENABLERS` (lines 9-10) with:

```python
EXPECTED_ENABLERS = {"ecodesign", "rnd", "data_infrastructure", "measurement",
                     "traceability", "procurement", "training", "partnerships",
                     "reverse_logistics", "finance", "policy"}
```

- [ ] **Step 2: Run enabler tests to verify they fail**

Run: `python -m pytest esg-longitudinal/tests/test_reference_data.py -k enabler -v`
Expected: FAIL (`test_enablers_present_and_well_formed`: JSON has 8 ids, expected 11; `test_enabler_ids_match_snapshot_validator`: `VALID_ENABLER` has 8).

- [ ] **Step 3: Add 3 enablers + narrow `data_infrastructure` (implementation)**

In `esg-longitudinal/circular-economy-10rs.json`, change the `data_infrastructure` object's `description` (line 89) to:

```json
     "description": "Broad digital and data systems — ERP, IoT, analytics platforms, and enterprise data infrastructure — that underpin circular operations at scale.",
```

(Leave its `id` and `supports` unchanged.) Then add these three objects at the end of the `enablers` array (after the `policy` object at line 105, adding a comma after the `policy` object's closing `}`):

```json
    {"id": "measurement", "name": "Measurement & impact accounting",
     "description": "Metering, KPI systems, and material/impact accounting that quantify circular flows across the ladder.",
     "supports": ["R2", "R4", "R6", "R8"]},
    {"id": "traceability", "name": "Traceability & product passports",
     "description": "Digital/material passports and chain-of-custody tracking that make specific loops auditable.",
     "supports": ["R3", "R5", "R8"]},
    {"id": "procurement", "name": "Circular procurement",
     "description": "Purchasing standards and supplier requirements that pull circular inputs (reused, refurbished, remanufactured, recycled) through the value chain.",
     "supports": ["R2", "R3", "R5", "R6", "R8"]}
```

In `esg-longitudinal/scripts/snapshot.py`, replace `VALID_ENABLER` (lines 42-43) with:

```python
VALID_ENABLER = {"ecodesign", "rnd", "data_infrastructure", "measurement",
                 "traceability", "procurement", "training", "partnerships",
                 "reverse_logistics", "finance", "policy"}
```

- [ ] **Step 4: Run enabler tests to verify they pass**

Run: `python -m pytest esg-longitudinal/tests/test_reference_data.py -k enabler -v`
Expected: PASS (both equality tests green; each new enabler has non-empty `name`/`description` and valid `R` tokens in `supports`).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/circular-economy-10rs.json esg-longitudinal/scripts/snapshot.py esg-longitudinal/tests/test_reference_data.py
git commit -m "feat: add measurement/traceability/procurement enablers; narrow data_infrastructure"
```

---

### Task 3: Diff — "Quality reassessed" / "Newly assessed" subsection

**Files:**
- Modify: `esg-longitudinal/scripts/diff.py` (add `MATERIALITY` near `TKEY` `:34`; extend `main()` render block `:108-150`)
- Test: `esg-longitudinal/tests/test_diff.py` (`_write` `:14-19`)

**Interfaces:**
- Consumes: `snapshot.COLS` (29-col, from Task 1) via the test `_write` helper.
- Produces: diff output containing `**Quality reassessed**` (both-non-empty materiality change) and `**Newly assessed**` (blank→value) subsections inside the existing Target movements block.

- [ ] **Step 1: Add `cols` param to `_write`, a 19-col constant, and 4 diff tests (test)**

In `esg-longitudinal/tests/test_diff.py`, replace `_write` (lines 14-19) with a version that accepts an explicit column list (so a test can physically emit a pre-v2 19-column header, exercising the absent-key path):

```python
V1_COLS_19 = ["entity", "lei", "domain", "indicator", "value", "unit", "period", "status",
              "source", "source_url", "page", "quote", "retrieved_at",
              "item_type", "r_strategy", "enabler_topic",
              "target_end_year", "target_has_kpi", "target_status"]


def _write(path, rows, cols=None):
    cols = cols or snapshot.COLS
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
```

Append these tests:

```python
def _run_diff(old, new):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "diff.py"), "--old", str(old), "--new", str(new)],
        capture_output=True, text=True)


def test_quality_reassessed_all_four_fields(tmp_path):
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025", substance="symbolic", planetary_alignment="insufficient",
                    priority_internal="low", importance_external="low", assessment_notes="n")])
    _write(new, [_t(period="2025", substance="substantive", planetary_alignment="pb_aligned",
                    priority_internal="high", importance_external="high", assessment_notes="n")])
    out = _run_diff(old, new)
    assert out.returncode == 0, out.stderr
    b = out.stdout
    assert "Quality reassessed" in b
    for f in ("substance", "planetary_alignment", "priority_internal", "importance_external"):
        assert f in b


def test_quality_only_change_still_renders(tmp_path):
    # value + end year identical; only a materiality field changes; no found actual
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025", substance="symbolic", assessment_notes="n")])
    _write(new, [_t(period="2025", substance="substantive", assessment_notes="n")])
    b = _run_diff(old, new).stdout
    assert "Target movements" in b
    assert "Quality reassessed" in b
    assert "substance: symbolic -> substantive" in b


def test_newly_assessed_not_reassessed(tmp_path):
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025")])  # no materiality fields set
    _write(new, [_t(period="2025", substance="substantive", assessment_notes="n")])
    b = _run_diff(old, new).stdout
    assert "Newly assessed" in b
    assert "Quality reassessed" not in b  # blank->value is NOT a reassessment


def test_no_churn_against_19col_baseline(tmp_path):
    # old physically omits v2 columns -> DictReader yields absent keys (None on .get)
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025")], cols=V1_COLS_19)
    _write(new, [_t(period="2025", substance="substantive", assessment_notes="n")])
    b = _run_diff(old, new).stdout
    assert "Quality reassessed" not in b  # first-time assessment, not a reassessment
```

- [ ] **Step 2: Run the diff tests to verify they fail**

Run: `python -m pytest esg-longitudinal/tests/test_diff.py -k "reassessed or quality_only or newly or churn" -v`
Expected: FAIL (`diff.py` emits no "Quality reassessed"/"Newly assessed" text yet).

- [ ] **Step 3: Implement the materiality subsection (implementation)**

In `esg-longitudinal/scripts/diff.py`, after the `TKEY = ("entity", "indicator")` line (line 34), add:

```python
MATERIALITY = ("substance", "planetary_alignment", "priority_internal", "importance_external")
```

In `main()`, after the line `pairs = [k for k in new_t if k in found_new]` (line 109), add:

```python
    reassessed, newly_assessed = {}, {}
    for k in (x for x in new_t if x in old_t):
        for f in MATERIALITY:
            ov = (old_t[k].get(f) or "").strip()
            nv = (new_t[k].get(f) or "").strip()
            if ov and nv and ov != nv:
                reassessed.setdefault(k, []).append(f"{f}: {ov} -> {nv}")
            elif not ov and nv:
                newly_assessed.setdefault(k, []).append(f"{f}: -> {nv}")
```

Change the Target-movements render guard (line 111) from:

```python
    if new_targets or changed_targets or dropped_targets or pairs:
```

to:

```python
    if new_targets or changed_targets or dropped_targets or pairs or reassessed or newly_assessed:
```

Inside that block, after the `if pairs:` sub-block ends (after line 150, the `L.append("")` that closes the pairs table), add:

```python
        if reassessed:
            L.append("**Quality reassessed**\n")
            for k in sorted(reassessed):
                L.append(f"- {k[0]} / {k[1]}: " + "; ".join(reassessed[k]))
            L.append("")
        if newly_assessed:
            L.append("**Newly assessed**\n")
            for k in sorted(newly_assessed):
                L.append(f"- {k[0]} / {k[1]}: " + "; ".join(newly_assessed[k]))
            L.append("")
```

Note: `(old_t[k].get(f) or "")` normalizes an absent key (`None`, from a pre-v2 CSV) and an empty string identically — this is the both-non-empty guard that prevents `str(None)` churn.

- [ ] **Step 4: Run the full diff test file to verify it passes**

Run: `python -m pytest esg-longitudinal/tests/test_diff.py -v`
Expected: PASS (the 4 new tests + the 3 existing tests, which auto-adapt to 29-col via `snapshot.COLS`).

- [ ] **Step 5: Commit**

```bash
git add esg-longitudinal/scripts/diff.py esg-longitudinal/tests/test_diff.py
git commit -m "feat: add Quality reassessed / Newly assessed materiality diff subsection"
```

---

### Task 4: SKILL.md — 29-col schema block, SMART+ section, report scorecard

**Files:**
- Modify: `esg-longitudinal/SKILL.md` (schema block `:45-49`, prose `:51-53`, new section after `:143`, report template `:234-237`, enabler prose `:109-112`)
- Test: `esg-longitudinal/tests/test_reference_data.py` (`test_skill_documents_new_columns` `:39-46`)

**Interfaces:**
- Consumes: nothing new.
- Produces: SKILL.md text containing all 10 v2 column names and the section header `Target quality (SMART+)`.

- [ ] **Step 1: Extend the SKILL.md documentation test (test)**

In `esg-longitudinal/tests/test_reference_data.py`, replace `test_skill_documents_new_columns` (lines 39-46) with:

```python
def test_skill_documents_new_columns():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    v1_cols = ["item_type", "r_strategy", "enabler_topic",
               "target_end_year", "target_has_kpi", "target_status"]
    v2_cols = ["smart_specific", "smart_achievable", "smart_relevant", "substance",
               "planetary_alignment", "impact_scope", "priority_internal",
               "importance_external", "linked_targets", "assessment_notes"]
    for col in v1_cols + v2_cols:
        assert col in text, f"SKILL.md missing {col}"
    for section in ["Classification layer", "Target anatomy",
                    "Year-over-year target status", "Target quality (SMART+)"]:
        assert section in text, f"SKILL.md missing section: {section}"
```

- [ ] **Step 2: Run the doc test to verify it fails**

Run: `python -m pytest esg-longitudinal/tests/test_reference_data.py::test_skill_documents_new_columns -v`
Expected: FAIL (SKILL.md has none of the v2 column names nor the SMART+ section).

- [ ] **Step 3: Update the schema block + prose (implementation)**

In `esg-longitudinal/SKILL.md`, replace the schema code block (lines 45-49) with:

```
entity, lei, domain, indicator, value, unit, period, status,
source, source_url, page, quote, retrieved_at,
item_type, r_strategy, enabler_topic, target_end_year, target_has_kpi, target_status,
smart_specific, smart_achievable, smart_relevant, substance,
planetary_alignment, impact_scope, priority_internal, importance_external,
linked_targets, assessment_notes
```

Replace the prose paragraph after it (lines 51-53) with:

```markdown
The first 13 columns are the original core; the next 6 are the v1 **classification
layer** and the last 10 are the v2 **SMART+ target-quality** block (see sections
below). All 16 non-core columns are optional — old 13-column and 19-column snapshots
still validate, and `diff.py` keys on `(entity, indicator, period)` regardless.
```

- [ ] **Step 4: Add the enabler tie-breaker guidance + SMART+ section (implementation)**

In `esg-longitudinal/SKILL.md`, in the "Classification layer" section, replace the **Enablers** bullet (lines 109-112) with:

```markdown
- **Enablers** (`enabler_topic`): some commitments are not an R-strategy but make
  them possible — one of the 11 enabler ids in `circular-economy-10rs.json`. Prefer
  `traceability` for product/material passports and chain-of-custody, `measurement`
  for metering / KPI / impact accounting, and `data_infrastructure` only for broad
  digital-systems commitments; the others are `ecodesign`, `rnd`, `procurement`,
  `training`, `partnerships`, `reverse_logistics`, `finance`, `policy`. A row is
  usually an R-strategy item *or* an enabler item; occasionally both.
```

Then, immediately after the "Year-over-year target status" section (after line 143, before `## Workflow`), insert:

```markdown
## Target quality (SMART+)

For `status=target` rows you may add a per-target quality/materiality assessment.
These 10 columns are all optional and populate on target rows; each is validated
only when present.

- **S / A / R** (`smart_specific`, `smart_achievable`, `smart_relevant`) — `yes|no`.
  The **M** and **T** of SMART are *not* separate columns: `target_has_kpi` is M and
  `target_end_year` is T (both factual — see the Target scorecard for them).
- `substance` — `symbolic|substantive`: a real operational commitment vs signaling.
- `planetary_alignment` — `insufficient|pb_aligned|unknown`: aligned to a planetary
  boundary / science-based pathway. `unknown` is a real finding (we checked, can't
  tell) and is distinct from leaving the field blank ("not assessed").
- `impact_scope` — `A|B|C|D` (Lukas's A–D scoping, **not** GHG Scope 1/2/3):
  **A** = footprint, own operations; **B** = footprint, direct value chain (suppliers
  + use phase); **C** = footprint, broader/enabled system; **D** = **handprint**
  (positive contribution / avoided impact elsewhere). Blank = not assessed.
- `priority_internal` / `importance_external` — `high|low`: strategic priority inside
  the company vs external signaling importance.
- `linked_targets` — free text: which other (ESG) targets this connects to, and how.
- `assessment_notes` — free text rationale. **Required** whenever any of the eight
  judgment columns (`smart_specific`, `smart_achievable`, `smart_relevant`,
  `substance`, `planetary_alignment`, `impact_scope`, `priority_internal`,
  `importance_external`) is set — opinion is permitted but never ungrounded. A
  judgment value with empty `assessment_notes` is rejected by `snapshot.py`.
```

- [ ] **Step 5: Add the SMART+ scorecard to the report template (implementation)**

In `esg-longitudinal/SKILL.md`, in the "Output: report structure" block, after the "Target scorecard" table (after line 237, before the `## Enablers` line inside the fenced template), insert:

```markdown
## Target quality (SMART+)
| indicator | S | A | R | substance | planetary | impact_scope | int.pri | ext.imp | notes |
|---|---|---|---|---|---|---|---|---|---|
(one row per status=target; S/A/R from smart_*; notes = assessment_notes.
M and T are in the Target scorecard above — has KPI and end year.)
```

- [ ] **Step 6: Run the doc test to verify it passes**

Run: `python -m pytest esg-longitudinal/tests/test_reference_data.py::test_skill_documents_new_columns -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add esg-longitudinal/SKILL.md esg-longitudinal/tests/test_reference_data.py
git commit -m "docs: document SMART+ columns, impact_scope crosswalk, enabler tie-breaker in SKILL.md"
```

---

### Task 5: Evals — 29-col header, eval 4 extension, eval 5, stale-descriptor guard

**Files:**
- Modify: `esg-longitudinal/evals/evals.json` (eval 1 assertion `:12`, eval 2 `:30`, eval 3 `:50`, eval 4 prompt `:58` + assertions `:61-67`, add eval 5)
- Test: `esg-longitudinal/tests/test_reference_data.py` (`test_evals_reference_19_column_schema` `:49-61`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `evals.json` whose only schema descriptor is "29-column"; the verbatim 29-col header string present; evals 4 and 5 exercising SMART+.

- [ ] **Step 1: Rewrite the evals schema test → 29 + stale-guard (test)**

In `esg-longitudinal/tests/test_reference_data.py`, replace `test_evals_reference_19_column_schema` (lines 49-61) with:

```python
def test_evals_reference_29_column_schema():
    data = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
    blob = json.dumps(data)
    # no stale schema descriptors from prior migrations
    assert "13-column" not in blob and "13 column" not in blob
    assert "19-column" not in blob and "19 column" not in blob
    # the full 29-column header appears verbatim in at least one assertion
    header = ("entity,lei,domain,indicator,value,unit,period,status,source,"
              "source_url,page,quote,retrieved_at,item_type,r_strategy,"
              "enabler_topic,target_end_year,target_has_kpi,target_status,"
              "smart_specific,smart_achievable,smart_relevant,substance,"
              "planetary_alignment,impact_scope,priority_internal,"
              "importance_external,linked_targets,assessment_notes")
    assert header in blob
    # classification + SMART+ assertions exist
    assert "r_strategy" in blob and "target_status" in blob
    assert "substance" in blob and "planetary_alignment" in blob
    ids = [e["id"] for e in data["evals"]]
    assert 4 in ids and 5 in ids  # target-status eval + new SMART+ scorecard eval
```

- [ ] **Step 2: Run the evals test to verify it fails**

Run: `python -m pytest esg-longitudinal/tests/test_reference_data.py::test_evals_reference_29_column_schema -v`
Expected: FAIL (evals.json still contains "19-column", lacks the 29-col header, has no eval id 5).

- [ ] **Step 3: Update evals 1–3 schema descriptors + header (implementation)**

In `esg-longitudinal/evals/evals.json`:

- Eval 1, the header assertion (line 12) — replace with:

```json
        "The snapshot CSV header is exactly the 29-column tidy schema: entity,lei,domain,indicator,value,unit,period,status,source,source_url,page,quote,retrieved_at,item_type,r_strategy,enabler_topic,target_end_year,target_has_kpi,target_status,smart_specific,smart_achievable,smart_relevant,substance,planetary_alignment,impact_scope,priority_internal,importance_external,linked_targets,assessment_notes.",
```

- Eval 2 (line 30): change `"identical 19-column schema as eval 1"` → `"identical 29-column schema as eval 1"`.
- Eval 3 (line 50): change `"The new snapshot uses the 19-column schema and matching key fields"` → `"The new snapshot uses the 29-column schema and matching key fields"` (leave the rest of the sentence, including "which predates the classification columns", intact).

- [ ] **Step 4: Extend eval 4 (prompt + assertions) (implementation)**

In `esg-longitudinal/evals/evals.json`, eval 4:

- Append to the `prompt` (line 58), before the closing quote, this sentence:

```
 Also judge each target's quality: whether it reads symbolic or substantive, whether it is science-based / planetary-aligned, and its internal (strategic) vs external (signaling) priority — with a short rationale for each call.
```

- Add these two assertion strings to eval 4's `assertions` array (after the last existing assertion, line 66):

```json
        "At least one status=target row carries SMART+ fields: a substance (symbolic|substantive), a planetary_alignment (insufficient|pb_aligned|unknown), and a priority_internal or importance_external (high|low).",
        "The snapshot still passes scripts/snapshot.py validation (exit 0), so every target row carrying judgment fields also carries a non-empty assessment_notes (the D1 rationale rule)."
```

- [ ] **Step 5: Add eval 5 (implementation)**

In `esg-longitudinal/evals/evals.json`, add this object to the `evals` array after eval 4 (add a comma after eval 4's closing `}`):

```json
    {
      "id": 5,
      "name": "philips-smart-plus-scorecard",
      "prompt": "For Royal Philips' circular economy targets, build a SMART+ quality scorecard. For each target, assess whether it is Specific, Achievable, and Relevant (yes/no), whether it is symbolic or substantive, whether it is science-based / planetary-aligned, its impact scope (A/B/C footprint or D handprint), and its internal strategic priority vs external signaling importance. Give a short rationale for each target and note any links to other ESG targets. Save a snapshot and produce a report with a SMART+ scorecard.",
      "files": [],
      "assertions": [
        "A timestamped snapshot CSV is written under data/snapshots/ using the 29-column schema (header ends ...importance_external,linked_targets,assessment_notes).",
        "At least one status=target row carries SMART+ judgment values drawn from the documented enums (smart_specific/achievable/relevant in yes|no, substance in symbolic|substantive, planetary_alignment in insufficient|pb_aligned|unknown, impact_scope in A|B|C|D, priority_internal/importance_external in high|low).",
        "The snapshot passes scripts/snapshot.py validation (exit 0): every target row with any judgment field set also has a non-empty assessment_notes (D1 rationale rule).",
        "The markdown report contains a SMART+ scorecard section (Target quality (SMART+)) with per-target S/A/R plus substance and impact_scope.",
        "Values the analyst could not determine are left blank or recorded as planetary_alignment=unknown rather than guessed."
      ]
    }
```

- [ ] **Step 6: Run the evals test to verify it passes**

Run: `python -m pytest esg-longitudinal/tests/test_reference_data.py::test_evals_reference_29_column_schema -v`
Expected: PASS.

- [ ] **Step 7: Run the FULL test suite to verify nothing regressed**

Run: `python -m pytest esg-longitudinal/tests/ -v`
Expected: PASS (all of `test_snapshot.py`, `test_diff.py`, `test_reference_data.py`). This confirms the 29-col migration is internally consistent across schema, validators, diff, docs, enablers, and evals.

- [ ] **Step 8: Commit**

```bash
git add esg-longitudinal/evals/evals.json esg-longitudinal/tests/test_reference_data.py
git commit -m "test: migrate evals to 29-column schema, extend eval 4, add SMART+ scorecard eval 5"
```

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Schema 19→29 + docstring → Task 1 (Steps 3, 8).
- 10 new columns + enums + `VALID_LEVEL`/`VALID_IMPACT_SCOPE`/`VALID_SUBSTANCE`/`VALID_PLANETARY` + reuse `VALID_HAS_KPI` → Task 1 (Step 7).
- D1 rule (`.strip()` semantics, non-short-circuit, names offending columns, whitespace-only notes) → Task 1 (Steps 5, 7; tests `test_whitespace_notes_counts_as_empty`, `test_invalid_enum_and_missing_notes_both_reported`).
- `sbt_scope`→`impact_scope` rename → applied throughout (Task 1 COLS/enum, Task 4 docs, Task 5 evals header).
- D2 by-convention (no status guard) → honored: no status guard added anywhere.
- D3 enablers (3 new, distinct supports, narrow `data_infrastructure`, atomic 3-file edit, SKILL.md tie-breaker) → Task 2 + Task 4 (Step 4).
- diff both-non-empty guard + `newly_assessed` + render-guard fix → Task 3.
- SKILL.md 29-col block + prose + SMART+ section + scorecard + M/T note → Task 4.
- evals: 29-col header, evals 1/2/3 descriptors, eval 4 prompt+assertions, eval 5 `files:[]` → Task 5.
- tests: rewrite header test, per-column enum negatives, D1 parametrized + linked_targets negative + whitespace + happy-path + non-short-circuit + notes-only, diff all-4-fields + quality-only + newly-assessed + 19-col no-churn, EXPECTED_ENABLERS 11, rename+29-guard+assert5, skill-doc 10 cols → Tasks 1/2/3/4/5.

**2. Placeholder scan** — no TBD/TODO/"add validation"/"similar to Task N"; every code step shows complete code.

**3. Type consistency** — `_target()` (test_snapshot), `_t()`/`_write(cols=)`/`V1_COLS_19` (test_diff), `MATERIALITY`/`reassessed`/`newly_assessed` (diff.py), `JUDGMENT_COLS`/`VALID_LEVEL`/`VALID_IMPACT_SCOPE`/`VALID_SUBSTANCE`/`VALID_PLANETARY` (snapshot.py), `V1_COLS`/`V2_COLS` (test_snapshot) are each defined before use, and `impact_scope` (not `sbt_scope`) is used uniformly. Column order in Task 1 `COLS`, Task 4 SKILL.md block, and Task 5 eval header string are byte-identical.
